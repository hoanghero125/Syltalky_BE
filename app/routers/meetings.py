import asyncio
import json
import random
import string
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from livekit import api as livekit_api

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.meeting import Meeting, MeetingParticipant, MeetingWaitingRequest
from app.models.user import User
from app.models.voice_profile import UserVoiceConfig
from app.schemas.meeting import MeetingOut, MeetingCreate, MeetingJoin, JoinResponse, WaitingRequestOut
from app.services.minio_client import get_public_url, upload_bytes

router = APIRouter(prefix="/meetings", tags=["meetings"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _generate_room_code() -> str:
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(random.choices(chars, k=4))
    return f"PRO-{suffix}"


async def _create_livekit_room(room_name: str) -> None:
    """Create a LiveKit room (idempotent)."""
    lk = livekit_api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        await lk.room.create_room(livekit_api.CreateRoomRequest(name=room_name))
    finally:
        await lk.aclose()


async def _delete_livekit_room(room_name: str) -> None:
    """Delete a LiveKit room, kicking all participants."""
    lk = livekit_api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
    finally:
        await lk.aclose()


def _generate_token(room_name: str, identity: str, display_name: str, avatar_url: str = "") -> str:
    """Issue a LiveKit access token for a participant (synchronous — no network)."""
    token = livekit_api.AccessToken(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    token.with_identity(identity).with_name(display_name)
    token.with_metadata(json.dumps({"avatar_url": avatar_url or ""}))
    token.with_grants(
        livekit_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_update_own_metadata=True,
        )
    )
    return token.to_jwt()


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=MeetingOut, status_code=201)
async def create_meeting(
    body: MeetingCreate = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body is None:
        body = MeetingCreate()

    for _ in range(10):
        code = _generate_room_code()
        existing = await db.execute(select(Meeting).where(Meeting.room_code == code))
        if not existing.scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique room code")

    room_name = f"syltalky-{code.lower()}"
    try:
        await _create_livekit_room(room_name)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LiveKit unavailable: {e}")

    meeting = Meeting(
        room_code=code,
        host_id=user.id,
        livekit_room_name=room_name,
        waiting_room_enabled=body.waiting_room_enabled,
    )
    db.add(meeting)
    await db.flush()

    db.add(MeetingParticipant(meeting_id=meeting.id, user_id=user.id))
    await db.commit()
    await db.refresh(meeting)
    return meeting


@router.get("/check/{room_code}")
async def check_room(
    room_code: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Check if a room exists and is still active."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.room_code == room_code.upper().replace(" ", ""),
            Meeting.ended_at.is_(None),
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Room not found or already ended")
    return {"room_code": meeting.room_code, "valid": True}


@router.post("/join", response_model=JoinResponse)
async def join_meeting(
    body: MeetingJoin,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Meeting).where(
            Meeting.room_code == body.room_code.upper().replace(" ", ""),
            Meeting.ended_at.is_(None),
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Room not found or already ended")

    # Host always joins directly
    is_host = meeting.host_id == user.id

    if meeting.waiting_room_enabled and not is_host:
        # Returning (non-kicked) participants bypass the waiting room
        existing_part_result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == user.id,
            )
        )
        existing_part = existing_part_result.scalar_one_or_none()

        needs_waiting_room = existing_part is None or existing_part.kicked

        if needs_waiting_room:
            # New participant or previously kicked — goes through waiting room
            existing_req_result = await db.execute(
                select(MeetingWaitingRequest).where(
                    MeetingWaitingRequest.meeting_id == meeting.id,
                    MeetingWaitingRequest.user_id == user.id,
                    MeetingWaitingRequest.status == "pending",
                )
            )
            req = existing_req_result.scalar_one_or_none()
            if not req:
                req = MeetingWaitingRequest(
                    meeting_id=meeting.id,
                    user_id=user.id,
                    display_name=user.display_name,
                    avatar_url=get_public_url(user.avatar_path) if user.avatar_path else None,
                )
                db.add(req)
                await db.commit()
                await db.refresh(req)
                from app.routers.captions import _broadcast
                await _broadcast(str(meeting.id), {
                    "type": "join_request",
                    "request_id": str(req.id),
                    "user_id": str(user.id),
                    "display_name": user.display_name,
                    "avatar_url": req.avatar_url or "",
                })
            else:
                await db.commit()
            return JoinResponse(status="waiting", request_id=req.id, meeting_id=meeting.id)
        # else: returning non-kicked participant — fall through to direct join

    # Direct join — upsert participant
    part_result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == user.id,
        )
    )
    participant = part_result.scalar_one_or_none()
    if participant:
        participant.left_at = None
        participant.kicked = False
    else:
        db.add(MeetingParticipant(meeting_id=meeting.id, user_id=user.id))
    await db.commit()

    token = _generate_token(
        room_name=meeting.livekit_room_name,
        identity=str(user.id),
        display_name=user.display_name,
        avatar_url=get_public_url(user.avatar_path) if user.avatar_path else "",
    )
    return JoinResponse(
        status="joined",
        token=token,
        livekit_url=settings.LIVEKIT_PUBLIC_URL,
        meeting_id=meeting.id,
        room_code=meeting.room_code,
        host_id=meeting.host_id,
        waiting_room_enabled=meeting.waiting_room_enabled,
    )


# ── Waiting room — connections keyed by request_id ───────────────────────────
_waiting_connections: dict[str, WebSocket] = {}


@router.websocket("/{meeting_id}/waiting-ws")
async def waiting_ws(
    websocket: WebSocket,
    meeting_id: uuid.UUID,
    request_id: str,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.core.security import decode_token
    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    _waiting_connections[request_id] = websocket
    try:
        while True:
            await websocket.receive_text()  # keep alive; server pushes close
    except WebSocketDisconnect:
        pass
    finally:
        _waiting_connections.pop(request_id, None)
        # If still pending, mark cancelled and notify host
        try:
            req_result = await db.execute(
                select(MeetingWaitingRequest).where(
                    MeetingWaitingRequest.id == uuid.UUID(request_id),
                    MeetingWaitingRequest.status == "pending",
                )
            )
            req = req_result.scalar_one_or_none()
            if req:
                req.status = "cancelled"
                await db.commit()
                from app.routers.captions import _broadcast
                await _broadcast(str(meeting_id), {
                    "type": "join_cancelled",
                    "request_id": request_id,
                })
        except Exception:
            pass


@router.get("/{meeting_id}/waiting", response_model=list[WaitingRequestOut])
async def list_waiting(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Not the host")
    reqs = await db.execute(
        select(MeetingWaitingRequest).where(
            MeetingWaitingRequest.meeting_id == meeting_id,
            MeetingWaitingRequest.status == "pending",
        ).order_by(MeetingWaitingRequest.requested_at)
    )
    return reqs.scalars().all()


@router.post("/{meeting_id}/approve/{request_id}", status_code=204)
async def approve_request(
    meeting_id: uuid.UUID,
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Not the host")

    req_result = await db.execute(
        select(MeetingWaitingRequest).where(MeetingWaitingRequest.id == request_id)
    )
    req = req_result.scalar_one_or_none()
    if not req or req.status != "pending":
        raise HTTPException(status_code=404, detail="Request not found")

    # Upsert participant record
    part_result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == req.user_id,
        )
    )
    participant = part_result.scalar_one_or_none()
    if participant:
        participant.left_at = None
        participant.kicked = False
    else:
        db.add(MeetingParticipant(meeting_id=meeting.id, user_id=req.user_id))

    token = _generate_token(
        room_name=meeting.livekit_room_name,
        identity=str(req.user_id),
        display_name=req.display_name,
        avatar_url=req.avatar_url or "",
    )
    req.status = "approved"
    req.token = token
    await db.commit()

    # Push to the waiting participant's WS
    ws = _waiting_connections.get(str(request_id))
    if ws:
        try:
            await ws.send_json({
                "type": "join_approved",
                "token": token,
                "livekit_url": settings.LIVEKIT_PUBLIC_URL,
                "meeting_id": str(meeting.id),
                "room_code": meeting.room_code,
                "host_id": str(meeting.host_id),
            })
        except Exception:
            pass


@router.post("/{meeting_id}/approve-all", status_code=204)
async def approve_all_requests(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Not the host")

    reqs_result = await db.execute(
        select(MeetingWaitingRequest).where(
            MeetingWaitingRequest.meeting_id == meeting_id,
            MeetingWaitingRequest.status == "pending",
        )
    )
    pending = reqs_result.scalars().all()

    for req in pending:
        part_result = await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == req.user_id,
            )
        )
        participant = part_result.scalar_one_or_none()
        if participant:
            participant.left_at = None
            participant.kicked = False
        else:
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=req.user_id))

        token = _generate_token(
            room_name=meeting.livekit_room_name,
            identity=str(req.user_id),
            display_name=req.display_name,
            avatar_url=req.avatar_url or "",
        )
        req.status = "approved"
        req.token = token

    await db.commit()

    # Notify each waiting participant
    for req in pending:
        ws = _waiting_connections.get(str(req.id))
        if ws:
            try:
                await ws.send_json({
                    "type": "join_approved",
                    "token": req.token,
                    "livekit_url": settings.LIVEKIT_PUBLIC_URL,
                    "meeting_id": str(meeting.id),
                    "room_code": meeting.room_code,
                    "host_id": str(meeting.host_id),
                })
            except Exception:
                pass


@router.post("/{meeting_id}/deny/{request_id}", status_code=204)
async def deny_request(
    meeting_id: uuid.UUID,
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Not the host")

    req_result = await db.execute(
        select(MeetingWaitingRequest).where(MeetingWaitingRequest.id == request_id)
    )
    req = req_result.scalar_one_or_none()
    if not req or req.status != "pending":
        raise HTTPException(status_code=404, detail="Request not found")

    req.status = "denied"
    await db.commit()

    ws = _waiting_connections.get(str(request_id))
    if ws:
        try:
            await ws.send_json({"type": "join_denied"})
        except Exception:
            pass


@router.patch("/{meeting_id}/waiting-room", status_code=204)
async def toggle_waiting_room(
    meeting_id: uuid.UUID,
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting or meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Not the host")

    enabled = bool(body.get("enabled", True))
    meeting.waiting_room_enabled = enabled
    await db.commit()

    if not enabled:
        # Auto-approve all pending requests so waiting participants can join
        reqs_result = await db.execute(
            select(MeetingWaitingRequest).where(
                MeetingWaitingRequest.meeting_id == meeting_id,
                MeetingWaitingRequest.status == "pending",
            )
        )
        pending = reqs_result.scalars().all()
        from app.routers.captions import _broadcast
        for req in pending:
            # Upsert participant
            part_result = await db.execute(
                select(MeetingParticipant).where(
                    MeetingParticipant.meeting_id == meeting.id,
                    MeetingParticipant.user_id == req.user_id,
                )
            )
            participant = part_result.scalar_one_or_none()
            if participant:
                participant.left_at = None
            else:
                db.add(MeetingParticipant(meeting_id=meeting.id, user_id=req.user_id))

            token = _generate_token(
                room_name=meeting.livekit_room_name,
                identity=str(req.user_id),
                display_name=req.display_name,
                avatar_url=req.avatar_url or "",
            )
            req.status = "approved"
            req.token = token
            await db.commit()

            # Push join_approved to the waiting participant
            ws = _waiting_connections.get(str(req.id))
            if ws:
                try:
                    await ws.send_json({
                        "type": "join_approved",
                        "token": token,
                        "livekit_url": settings.LIVEKIT_PUBLIC_URL,
                        "meeting_id": str(meeting.id),
                        "room_code": meeting.room_code,
                        "host_id": str(meeting.host_id),
                    })
                except Exception:
                    pass

            # Remove from host's waiting list
            await _broadcast(str(meeting_id), {
                "type": "join_cancelled",
                "request_id": str(req.id),
            })


@router.post("/{meeting_id}/kick/{participant_id}", status_code=204)
async def kick_participant(
    meeting_id: uuid.UUID,
    participant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the host can kick participants")
    if meeting.ended_at:
        raise HTTPException(status_code=400, detail="Meeting already ended")

    # Mark participant as kicked before removing from LiveKit
    kicked_part_result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.user_id == participant_id,
        )
    )
    kicked_part = kicked_part_result.scalar_one_or_none()
    if kicked_part:
        kicked_part.kicked = True
        await db.commit()

    lk = livekit_api.LiveKitAPI(
        url=settings.LIVEKIT_URL,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    try:
        await lk.room.remove_participant(
            livekit_api.RoomParticipantIdentity(
                room=meeting.livekit_room_name,
                identity=str(participant_id),
            )
        )
    finally:
        await lk.aclose()


@router.post("/{meeting_id}/end", status_code=204)
async def end_meeting(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Host ends the meeting. Marks ended_at and disconnects the LiveKit room."""
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only the host can end the meeting")
    if meeting.ended_at:
        return  # already ended, idempotent

    meeting.ended_at = datetime.now(timezone.utc)
    host_id = meeting.host_id
    meeting_id_val = meeting.id
    await db.commit()

    # Delete LiveKit room (kick all participants)
    try:
        await _delete_livekit_room(meeting.livekit_room_name)
    except Exception:
        pass

    # Fire-and-forget: save transcript
    from app.services.post_processing import run_post_processing
    asyncio.create_task(run_post_processing(meeting_id_val, host_id))


@router.post("/{meeting_id}/tts")
async def meeting_tts(
    meeting_id: uuid.UUID,
    body: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS audio for a meeting participant using their voice config."""
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Get user's voice config
    vc_result = await db.execute(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == user.id)
    )
    voice_config = vc_result.scalar_one_or_none()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if voice_config and voice_config.mode == "clone" and voice_config.active_voice_profile_id:
                from app.models.voice_profile import VoiceProfile
                vp_result = await db.execute(
                    select(VoiceProfile).where(VoiceProfile.id == voice_config.active_voice_profile_id)
                )
                vp = vp_result.scalar_one_or_none()
                if vp and vp.active_voice_id:
                    resp = await client.post(
                        f"{settings.AI_API_URL}/tts/synthesize",
                        json={"voice_id": vp.active_voice_id, "text": text},
                    )
                    resp.raise_for_status()
                    audio_bytes = resp.content
                else:
                    raise HTTPException(status_code=400, detail="No active voice profile found")
            else:
                instruct = (voice_config.design_instruct if voice_config else None) or "female, adult, moderate pitch"
                resp = await client.post(
                    f"{settings.AI_API_URL}/tts/design",
                    json={"instruct": instruct, "text": text},
                )
                resp.raise_for_status()
                audio_bytes = resp.content
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"TTS failed: {e}")

    # Store audio in MinIO
    key = f"tts/{meeting_id}/{uuid.uuid4()}.wav"
    upload_bytes(key, audio_bytes, "audio/wav")
    audio_url = get_public_url(key)
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Persist TTS as a Caption row so history is restored on reconnect
    from app.models.caption import Caption
    db.add(Caption(
        meeting_id=meeting_id,
        user_id=user.id,
        text=text,
        timestamp_ms=ts,
        is_tts=True,
        audio_url=audio_url,
    ))
    await db.commit()

    # Broadcast to all participants via captions WS
    from app.routers.captions import _broadcast
    await _broadcast(str(meeting_id), {
        "type": "tts",
        "speaker_id": str(user.id),
        "speaker_name": user.display_name,
        "text": text,
        "audio_url": audio_url,
        "timestamp_ms": ts,
    })

    return {"audio_url": audio_url}


@router.post("/{meeting_id}/summarize")
async def summarize_preview(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """In-meeting catch-up: generate a snapshot summary, display only, never saved."""
    from app.models.caption import Caption
    from app.services.post_processing import _summarize

    cap_result = await db.execute(
        select(Caption, User.display_name)
        .join(User, User.id == Caption.user_id)
        .where(Caption.meeting_id == meeting_id)
        .order_by(Caption.timestamp_ms)
    )
    rows = cap_result.all()

    if len(rows) < 5:
        raise HTTPException(status_code=422, detail="Không đủ nội dung để tóm tắt.")

    lines = [
        f"[TTS] {display_name}: {cap.text}" if cap.is_tts else f"{display_name}: {cap.text}"
        for cap, display_name in rows
    ]

    try:
        summary = await _summarize("\n".join(lines))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Tóm tắt thất bại: {e}")

    return {"summary": summary}


class AskRequest(BaseModel):
    question: str
    history: list[dict] = []


@router.post("/{meeting_id}/ask")
async def ask_meeting(
    meeting_id: uuid.UUID,
    body: AskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask a question about the meeting content."""
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Build context from summary + transcript
    context_parts = []
    if meeting.summary:
        context_parts.append(f"[TÓM TẮT - bao phủ toàn bộ cuộc họp]\n{meeting.summary}")
    if meeting.transcript:
        lines = [
            f"[TTS] {e['display_name']}: {e['text']}" if e.get('is_tts') else f"{e['display_name']}: {e['text']}"
            for e in meeting.transcript
        ]
        transcript_text = "\n".join(lines)
        # ~20000 chars fits safely within the context window after accounting for
        # system prompt, history, and 512-token output budget
        LIMIT = 20000
        if len(transcript_text) > LIMIT:
            # Take first half + last half so both opening context and
            # closing decisions are always present
            half = LIMIT // 2
            transcript_text = (
                transcript_text[:half]
                + "\n\n...(nội dung giữa cuộc họp đã lược bỏ)...\n\n"
                + transcript_text[-half:]
            )
        context_parts.append(f"[BẢN GHI CHI TIẾT]\n{transcript_text}")

    if not context_parts:
        raise HTTPException(status_code=422, detail="Cuộc họp chưa có nội dung để hỏi.")

    context = "\n\n".join(context_parts)
    system = (
        "Bạn là trợ lý trả lời câu hỏi về nội dung cuộc họp.\n"
        "Bạn có hai nguồn thông tin: TÓM TẮT (bao phủ toàn bộ cuộc họp) và BẢN GHI CHI TIẾT (nguyên văn).\n"
        "Ưu tiên dùng BẢN GHI CHI TIẾT cho các câu hỏi cụ thể về lời nói, nguyên văn. "
        "Dùng TÓM TẮT cho các câu hỏi tổng quan hoặc khi chi tiết không có trong bản ghi.\n\n"
        f"{context}\n\n"
        "Quy tắc:\n"
        "- Chỉ trả lời dựa trên nội dung cuộc họp được cung cấp\n"
        "- Nếu câu hỏi không có trong nội dung, hãy nói thẳng\n"
        "- Trả lời ngắn gọn, súc tích, đúng trọng tâm\n"
        "- Trả lời bằng tiếng Việt"
    )

    messages = [{"role": "system", "content": system}]
    # Include conversation history (last 10 turns to stay within context)
    for msg in body.history[-10:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": body.question})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                json={"messages": messages, "thinking": False, "temperature": 0.5, "max_tokens": 512},
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=503, detail=f"LLM lỗi {resp.status_code}: {resp.text[:300]}")
        answer = resp.json()["choices"][0]["message"]["content"].strip()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Trả lời thất bại: {e}")

    # Persist the new exchange to DB (per-user, keyed by user_id)
    all_history = meeting.chat_history or {}
    if isinstance(all_history, list):
        all_history = {}  # migrate old shared format
    uid = str(user.id)
    user_history = all_history.get(uid, []) + [
        {"role": "user", "content": body.question},
        {"role": "assistant", "content": answer},
    ]
    meeting.chat_history = {**all_history, uid: user_history}
    await db.commit()

    return {"answer": answer}


@router.get("/{meeting_id}/chat")
async def get_chat_history(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    all_history = meeting.chat_history or {}
    if isinstance(all_history, list):
        all_history = {}
    return {"history": all_history.get(str(user.id), [])}


@router.get("")
async def list_meetings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import text as sa_text
    result = await db.execute(
        sa_text("""
            SELECT m.id, m.room_code, m.host_id, u.display_name AS host_name,
                   m.livekit_room_name, m.started_at, m.ended_at, m.summary, m.transcript
            FROM meetings m
            JOIN users u ON u.id = m.host_id
            JOIN meeting_participants mp ON mp.meeting_id = m.id
            WHERE mp.user_id = :uid
            ORDER BY m.started_at DESC
            LIMIT 50
        """),
        {"uid": str(user.id)}
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(
    meeting_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.meeting_extras import Note, Poll, PollVote
    host_alias = User.__table__.alias("host_user")
    result = await db.execute(
        select(Meeting, host_alias.c.display_name.label("host_display_name"))
        .join(host_alias, host_alias.c.id == Meeting.host_id)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .where(Meeting.id == meeting_id, MeetingParticipant.user_id == user.id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Meeting not found")
    m, host_name = row

    notes_rows = (await db.execute(
        select(Note).where(Note.meeting_id == m.id, Note.deleted_at.is_(None)).order_by(Note.created_at)
    )).scalars().all()
    notes_out = [
        {
            "id": str(n.id),
            "title": n.title or "",
            "plain_text": n.plain_text or "",
            "plain_html": n.plain_html or "",
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        }
        for n in notes_rows
    ]

    polls_rows = (await db.execute(
        select(Poll).where(Poll.meeting_id == m.id).order_by(Poll.created_at)
    )).scalars().all()
    poll_ids = [p.id for p in polls_rows]
    tallies_by_poll: dict = {pid: {} for pid in poll_ids}
    voters_by_poll: dict = {pid: {} for pid in poll_ids}
    if poll_ids:
        votes = (await db.execute(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))).scalars().all()
        voter_ids = list({v.user_id for v in votes})
        voter_map = {}
        if voter_ids:
            users = (await db.execute(select(User).where(User.id.in_(voter_ids)))).scalars().all()
            for u in users:
                from app.services.minio_client import get_public_url
                avatar_url = get_public_url(u.avatar_path) if u.avatar_path else ""
                voter_map[u.id] = {"user_id": str(u.id), "display_name": u.display_name, "avatar_url": avatar_url or ""}
        for v in votes:
            tallies_by_poll[v.poll_id][v.option_id] = tallies_by_poll[v.poll_id].get(v.option_id, 0) + 1
            voters_by_poll[v.poll_id].setdefault(v.option_id, [])
            if v.user_id in voter_map:
                voters_by_poll[v.poll_id][v.option_id].append(voter_map[v.user_id])
    polls_out = [
        {
            "id": str(p.id),
            "question": p.question,
            "options": p.options or [],
            "anonymous": p.anonymous,
            "multi_choice": p.multi_choice,
            "closed": p.closed,
            "tallies": tallies_by_poll.get(p.id, {}),
            "voters": None if p.anonymous else voters_by_poll.get(p.id, {}),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in polls_rows
    ]

    return MeetingOut.model_validate(m, from_attributes=True).model_copy(update={
        "host_name": host_name,
        "notes": notes_out,
        "polls": polls_out,
    })


@router.post("/webhook")
async def livekit_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    auth_header = request.headers.get("Authorization", "")
    try:
        event = livekit_api.WebhookReceiver(
            token_verifier=livekit_api.TokenVerifier(
                api_key=settings.LIVEKIT_API_KEY,
                api_secret=settings.LIVEKIT_API_SECRET,
            )
        ).receive(body.decode(), auth_header)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    room_name = event.room.name if event.room else None

    if event.event == "participant_left":
        identity = event.participant.identity if event.participant else None
        if identity and room_name:
            result = await db.execute(
                select(Meeting).where(Meeting.livekit_room_name == room_name)
            )
            meeting = result.scalar_one_or_none()
            if meeting:
                try:
                    part_result = await db.execute(
                        select(MeetingParticipant).where(
                            MeetingParticipant.meeting_id == meeting.id,
                            MeetingParticipant.user_id == uuid.UUID(identity),
                        )
                    )
                    part = part_result.scalar_one_or_none()
                    if part:
                        part.left_at = datetime.now(timezone.utc)
                        await db.commit()
                except ValueError:
                    pass

    elif event.event == "room_finished":
        if room_name:
            result = await db.execute(
                select(Meeting).where(
                    Meeting.livekit_room_name == room_name,
                    Meeting.ended_at.is_(None),
                )
            )
            meeting = result.scalar_one_or_none()
            if meeting:
                meeting.ended_at = datetime.now(timezone.utc)
                await db.commit()
                print(f"[webhook] {meeting.room_code} ended")

    return {"ok": True}
