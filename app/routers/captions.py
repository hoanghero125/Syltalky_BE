import asyncio
import time
import uuid
from collections import defaultdict

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select

from app.config import settings
from app.core.security import decode_token
from app.database import AsyncSessionLocal
from app.models.caption import Caption
from app.models.meeting import Meeting
from app.models.user import User

router = APIRouter(prefix="/meetings", tags=["captions"])

_room_connections: dict[str, set[WebSocket]] = defaultdict(set)


async def _broadcast(meeting_id: str, data: dict):
    dead = set()
    for ws in list(_room_connections[meeting_id]):
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _room_connections[meeting_id].discard(ws)


@router.websocket("/{meeting_id}/captions")
async def captions_ws(
    websocket: WebSocket,
    meeting_id: uuid.UUID,
    token: str = Query(...),
):
    # Auth + verify meeting
    payload = decode_token(token)
    user_id_str = payload.get("sub")
    if not user_id_str or payload.get("type") != "access":
        await websocket.close(code=4001)
        return

    try:
        uid = uuid.UUID(user_id_str)
    except ValueError:
        await websocket.close(code=4001)
        return

    async with AsyncSessionLocal() as db:
        user_row = await db.execute(select(User).where(User.id == uid))
        user = user_row.scalar_one_or_none()
        meeting_row = await db.execute(
            select(Meeting).where(Meeting.id == meeting_id, Meeting.ended_at.is_(None))
        )
        meeting = meeting_row.scalar_one_or_none()

    if not user or not meeting:
        await websocket.close(code=4004)
        return

    await websocket.accept()
    mid = str(meeting_id)
    _room_connections[mid].add(websocket)

    # Send history so late joiners see full context
    async with AsyncSessionLocal() as db_h:
        hist = await db_h.execute(
            select(Caption, User)
            .join(User, Caption.user_id == User.id)
            .where(Caption.meeting_id == meeting_id)
            .order_by(Caption.timestamp_ms.asc())
        )
        for cap, cap_user in hist.all():
            try:
                await websocket.send_json({
                    "speaker_id": str(cap.user_id),
                    "speaker_name": cap_user.display_name,
                    "text": cap.text,
                    "timestamp_ms": cap.timestamp_ms,
                    "is_history": True,
                    "is_tts": cap.is_tts,
                    "audio_url": cap.audio_url,
                })
            except Exception:
                break

    stt_url = settings.AI_API_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws/stt"

    async def _run_with_stt():
        async with websockets.connect(stt_url) as stt_ws:

            async def recv_from_stt():
                async for message in stt_ws:
                    if not isinstance(message, str):
                        continue
                    text = message.strip()
                    if not text:
                        continue
                    ts = int(time.time() * 1000)
                    async with AsyncSessionLocal() as db2:
                        db2.add(Caption(
                            meeting_id=meeting_id,
                            user_id=uid,
                            text=text,
                            timestamp_ms=ts,
                        ))
                        await db2.commit()
                    await _broadcast(mid, {
                        "type": "caption",
                        "speaker_id": user_id_str,
                        "speaker_name": user.display_name,
                        "text": text,
                        "timestamp_ms": ts,
                    })

            stt_task = asyncio.create_task(recv_from_stt())
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        await stt_ws.send(data)
                    except Exception:
                        break
            except WebSocketDisconnect:
                pass
            finally:
                stt_task.cancel()
                try:
                    await stt_task
                except asyncio.CancelledError:
                    pass

    async def _run_receive_only():
        try:
            async for _ in websocket.iter_bytes():
                pass
        except WebSocketDisconnect:
            pass

    try:
        await _run_with_stt()
    except Exception:
        # AI API unavailable — stay connected to receive others' captions
        await _run_receive_only()
    finally:
        _room_connections[mid].discard(websocket)
