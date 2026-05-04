"""
Yjs sync WebSocket for shared meeting notes.

Implements the y-websocket protocol so the FE `WebsocketProvider` from
`y-websocket` can sync multiple Tiptap editors over our backend.

Protocol summary (binary, varuint-encoded):
- byte 0 (varuint): messageType — 0=sync, 1=awareness
- For sync (0):
  - byte 1 (varuint): subType — 0=syncStep1, 1=syncStep2, 2=update
  - then varuint(len) + payload bytes

The server keeps an in-memory Y.Doc per note (loaded from `notes.ydoc_state`
on first connect, persisted with debounce).

Clients can also send TEXT (JSON) frames carrying:
{ "type": "snapshot", "plain_text": "...", "plain_html": "..." }
to provide a render-friendly snapshot for the post-meeting detail page —
the server can't easily walk a ProseMirror Y-fragment from Python, so we
let the FE compute that from its rendered editor.
"""

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select

from app.core.security import decode_token
from app.database import AsyncSessionLocal
from app.models.meeting import Meeting, MeetingParticipant
from app.models.meeting_extras import Note
from app.models.user import User

try:
    import y_py as Y  # type: ignore
    _HAS_YPY = True
except Exception:  # pragma: no cover
    Y = None
    _HAS_YPY = False


router = APIRouter(prefix="/meetings", tags=["notes-sync"])


# ── y-protocol byte helpers ──────────────────────────────────────────────────

def write_varuint(n: int) -> bytes:
    if n < 0:
        raise ValueError("varuint must be non-negative")
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def read_varuint(buf: bytes, offset: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        if offset >= len(buf):
            raise ValueError("varuint truncated")
        b = buf[offset]
        offset += 1
        n |= (b & 0x7F) << shift
        if not (b & 0x80):
            return n, offset
        shift += 7


def encode_message(message_type: int, sub_type: int | None, payload: bytes) -> bytes:
    out = bytearray()
    out += write_varuint(message_type)
    if sub_type is not None:
        out += write_varuint(sub_type)
    out += write_varuint(len(payload))
    out += payload
    return bytes(out)


# ── Per-note rooms (in-memory) ───────────────────────────────────────────────

class NoteRoom:
    def __init__(self, note_id: str):
        self.note_id = note_id
        self.clients: set[WebSocket] = set()
        self.doc = Y.YDoc() if _HAS_YPY else None
        self.lock = asyncio.Lock()
        self.dirty = False
        self._save_task: asyncio.Task | None = None
        self.plain_text: str | None = None
        self.plain_html: str | None = None

    async def load_from_db(self):
        async with AsyncSessionLocal() as db:
            try:
                nid = uuid.UUID(self.note_id)
            except ValueError:
                return None
            res = await db.execute(select(Note).where(Note.id == nid))
            note = res.scalar_one_or_none()
            if not note:
                return None
            if note.ydoc_state and self.doc is not None:
                try:
                    Y.apply_update(self.doc, note.ydoc_state)
                except Exception:
                    pass
            return note

    def encode_full_state_step2(self) -> bytes:
        # sync (0) / syncStep2 (1) / update bytes (full state encoded with empty vector)
        if self.doc is None:
            return encode_message(0, 1, b"")
        try:
            update = Y.encode_state_as_update(self.doc)
        except Exception:
            update = b""
        return encode_message(0, 1, update)

    def encode_step2_for_remote_vector(self, remote_state_vector: bytes) -> bytes:
        if self.doc is None:
            return encode_message(0, 1, b"")
        try:
            update = Y.encode_state_as_update(self.doc, remote_state_vector)
        except Exception:
            update = Y.encode_state_as_update(self.doc) if self.doc else b""
        return encode_message(0, 1, update)

    async def broadcast(self, sender: WebSocket, data: bytes | str):
        is_text = isinstance(data, str)
        for ws in list(self.clients):
            if ws is sender:
                continue
            try:
                if is_text:
                    await ws.send_text(data)
                else:
                    await ws.send_bytes(data)
            except Exception:
                self.clients.discard(ws)

    def schedule_save(self):
        self.dirty = True
        if self._save_task and not self._save_task.done():
            return
        self._save_task = asyncio.create_task(self._save_after_delay(2.0))

    async def _save_after_delay(self, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.persist()
        except Exception:
            pass

    async def persist(self):
        if not self.dirty:
            return
        self.dirty = False
        async with self.lock:
            try:
                blob = Y.encode_state_as_update(self.doc) if self.doc is not None else None
            except Exception:
                blob = None
            async with AsyncSessionLocal() as db:
                try:
                    nid = uuid.UUID(self.note_id)
                except ValueError:
                    return
                res = await db.execute(select(Note).where(Note.id == nid))
                note = res.scalar_one_or_none()
                if not note:
                    return
                if blob is not None:
                    note.ydoc_state = blob
                if self.plain_text is not None:
                    note.plain_text = self.plain_text
                if self.plain_html is not None:
                    note.plain_html = self.plain_html
                note.updated_at = datetime.now(timezone.utc)
                await db.commit()


_rooms: dict[str, NoteRoom] = {}
_rooms_lock = asyncio.Lock()


async def get_or_create_room(note_id: str) -> NoteRoom:
    async with _rooms_lock:
        room = _rooms.get(note_id)
        if room is None:
            room = NoteRoom(note_id)
            _rooms[note_id] = room
            await room.load_from_db()
        return room


# ── Auth helpers ─────────────────────────────────────────────────────────────

async def _authorize(token: str, meeting_id: uuid.UUID, note_id: uuid.UUID) -> User | None:
    try:
        payload = decode_token(token)
    except Exception:
        return None
    if payload.get("type") != "access":
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        uid = uuid.UUID(sub)
    except ValueError:
        return None

    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if not u:
            return None
        meeting = (await db.execute(select(Meeting).where(Meeting.id == meeting_id))).scalar_one_or_none()
        if not meeting:
            return None
        # Verify note belongs to meeting and isn't deleted
        note = (await db.execute(
            select(Note).where(Note.id == note_id, Note.meeting_id == meeting.id, Note.deleted_at.is_(None))
        )).scalar_one_or_none()
        if not note:
            return None
        # Authorize: host, co-host, or registered participant
        if meeting.host_id == u.id:
            return u
        co_hosts = meeting.co_hosts or []
        if str(u.id) in [str(c) for c in co_hosts]:
            return u
        mp = (await db.execute(
            select(MeetingParticipant).where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == u.id,
            )
        )).scalar_one_or_none()
        if mp:
            return u
    return None


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@router.websocket("/{meeting_id}/notes/{note_id}/sync")
async def notes_sync_ws(
    websocket: WebSocket,
    meeting_id: uuid.UUID,
    note_id: uuid.UUID,
    token: str = Query(...),
):
    user = await _authorize(token, meeting_id, note_id)
    if not user:
        await websocket.close(code=4001)
        return
    if not _HAS_YPY:
        # Without y-py we can't persist merged state. Fall back to relay-only.
        pass

    await websocket.accept()
    room = await get_or_create_room(str(note_id))
    room.clients.add(websocket)

    # Send initial full state so a fresh client gets caught up immediately.
    try:
        await websocket.send_bytes(room.encode_full_state_step2())
    except Exception:
        room.clients.discard(websocket)
        return

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"] is not None:
                data: bytes = msg["bytes"]
                if not data:
                    continue

                # Inspect message type
                try:
                    mtype, off = read_varuint(data, 0)
                except Exception:
                    continue

                if mtype == 0:  # sync
                    try:
                        sub, off2 = read_varuint(data, off)
                        plen, off3 = read_varuint(data, off2)
                        payload = data[off3:off3 + plen]
                    except Exception:
                        continue

                    if sub == 0:
                        # syncStep1: client sends its state vector — reply with step2 diff for it
                        if _HAS_YPY and room.doc is not None:
                            try:
                                resp = room.encode_step2_for_remote_vector(payload)
                                await websocket.send_bytes(resp)
                            except Exception:
                                pass
                    elif sub in (1, 2):
                        # syncStep2 / update: apply to server doc, broadcast
                        if _HAS_YPY and room.doc is not None and payload:
                            try:
                                Y.apply_update(room.doc, payload)
                                room.schedule_save()
                            except Exception:
                                pass
                        await room.broadcast(websocket, data)
                elif mtype == 1:  # awareness
                    await room.broadcast(websocket, data)

            elif "text" in msg and msg["text"] is not None:
                # JSON snapshot side-channel
                try:
                    js = json.loads(msg["text"])
                except Exception:
                    continue
                if js.get("type") == "snapshot":
                    if isinstance(js.get("plain_text"), str):
                        room.plain_text = js["plain_text"]
                    if isinstance(js.get("plain_html"), str):
                        room.plain_html = js["plain_html"]
                    room.schedule_save()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        room.clients.discard(websocket)
        # If empty, persist + drop room
        if not room.clients:
            try:
                await room.persist()
            finally:
                async with _rooms_lock:
                    _rooms.pop(room.note_id, None)
