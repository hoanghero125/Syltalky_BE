"""
Post-processing triggered after a meeting ends:
1. Build transcript from captions table and store on meeting row
2. Auto-summarize using LLM and store summary on meeting row
"""
import asyncio

import httpx
from datetime import datetime, timezone
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.caption import Caption
from app.models.meeting import Meeting
from app.models.meeting_extras import Poll
from app.models.user import User

CHUNK_SIZE = 12000


async def _llm(messages: list) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            json={"messages": messages, "thinking": False, "temperature": 0.4, "max_tokens": 512},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


_SYSTEM = (
    "Bạn là trợ lý tóm tắt cuộc họp. "
    "Nhiệm vụ: tóm tắt toàn bộ nội dung đã được nói — không bỏ sót bất kỳ thông tin nào.\n"
    "Quy tắc bắt buộc:\n"
    "- Bao phủ ĐẦY ĐỦ mọi nội dung, không được bỏ sót\n"
    "- Mỗi điểm ngắn gọn nhất có thể — chỉ giữ thông tin cốt lõi\n"
    "- Chỉ viết về những gì THỰC SỰ đã được nói — không nhận xét về những gì còn thiếu hay chưa được đề cập\n"
    "- Không có câu mở đầu, câu kết thúc, hay nhận xét về chất lượng bản ghi\n"
    "- Dùng định dạng markdown gạch đầu dòng (- ), tiếng Việt"
)

_SYSTEM_CHUNK = (
    "Bạn là trợ lý tóm tắt cuộc họp. "
    "Trích xuất TẤT CẢ các điểm nội dung từ đoạn bản ghi — không được bỏ sót.\n"
    "Mỗi điểm ngắn gọn nhất có thể, chỉ giữ thông tin cốt lõi. "
    "Không nhận xét về những gì còn thiếu. Không có câu mở đầu hay kết thúc. "
    "Dùng gạch đầu dòng (- ), tiếng Việt."
)

_SYSTEM_MERGE = (
    "Bạn là trợ lý tóm tắt cuộc họp. "
    "Tổng hợp các điểm từ nhiều phần thành một bản tóm tắt cuối cùng bao phủ ĐẦY ĐỦ toàn bộ nội dung.\n"
    "Quy tắc: loại bỏ trùng lặp, mỗi điểm ngắn gọn nhất có thể, không có câu mở đầu hay kết thúc, "
    "không nhận xét về những gì còn thiếu. Dùng gạch đầu dòng (- ), tiếng Việt."
)


async def _summarize(transcript_text: str) -> str:
    if len(transcript_text) <= CHUNK_SIZE:
        return await _llm([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Bản ghi cuộc họp:\n\n{transcript_text}"},
        ])

    chunks = [transcript_text[i:i + CHUNK_SIZE] for i in range(0, len(transcript_text), CHUNK_SIZE)]
    partial_summaries = []
    for i, chunk in enumerate(chunks):
        partial = await _llm([
            {"role": "system", "content": _SYSTEM_CHUNK},
            {"role": "user", "content": f"Phần {i + 1}/{len(chunks)}:\n\n{chunk}"},
        ])
        partial_summaries.append(partial)

    combined = "\n\n".join(f"[Phần {i + 1}]\n{s}" for i, s in enumerate(partial_summaries))
    return await _llm([
        {"role": "system", "content": _SYSTEM_MERGE},
        {"role": "user", "content": f"Tổng hợp:\n\n{combined}"},
    ])


async def _finalize_extras(meeting_id):
    """Close any still-open polls and flush + drop in-memory note rooms."""
    # Close open polls
    async with AsyncSessionLocal() as db:
        polls = (await db.execute(
            select(Poll).where(Poll.meeting_id == meeting_id, Poll.closed.is_(False))
        )).scalars().all()
        now = datetime.now(timezone.utc)
        for p in polls:
            p.closed = True
            p.closed_at = now
        if polls:
            await db.commit()

    # Flush + clear note rooms
    try:
        from app.routers.notes_sync import _rooms, _rooms_lock
        async with _rooms_lock:
            rooms = list(_rooms.values())
        for room in rooms:
            try:
                await room.persist()
            except Exception:
                pass
    except Exception:
        pass


async def run_post_processing(meeting_id, host_id):
    """Run in background after meeting.ended_at is set."""
    await asyncio.sleep(2)  # let DB writes settle

    await _finalize_extras(meeting_id)

    async with AsyncSessionLocal() as db:
        cap_result = await db.execute(
            select(Caption, User.display_name)
            .join(User, User.id == Caption.user_id)
            .where(Caption.meeting_id == meeting_id)
            .order_by(Caption.timestamp_ms)
        )
        rows = cap_result.all()

        meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
        meeting = meeting_result.scalar_one_or_none()
        if not meeting:
            return

        if rows:
            transcript = [
                {
                    "speaker_id": str(cap.user_id),
                    "display_name": display_name,
                    "text": cap.text,
                    "timestamp": cap.timestamp_ms,
                    "is_tts": cap.is_tts,
                }
                for cap, display_name in rows
            ]
            meeting.transcript = transcript
            await db.commit()

            # Auto-summarize — only if enough content
            if len(rows) >= 5:
                try:
                    lines = [
                        f"[TTS] {display_name}: {cap.text}" if cap.is_tts else f"{display_name}: {cap.text}"
                        for cap, display_name in rows
                    ]
                    meeting.summary = await _summarize("\n".join(lines))
                    await db.commit()
                except Exception:
                    pass  # summary is optional — don't fail the whole post-processing
