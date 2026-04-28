import json
import random
import string
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from livekit import api as livekit_api

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.meeting import Meeting, MeetingParticipant
from app.models.user import User
from app.schemas.meeting import MeetingOut, MeetingJoin, JoinResponse
from app.services.minio_client import get_public_url

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
        )
    )
    return token.to_jwt()


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=MeetingOut, status_code=201)
async def create_meeting(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new meeting room. Returns the meeting with its room code."""
    # Generate unique room code
    for _ in range(10):
        code = _generate_room_code()
        existing = await db.execute(select(Meeting).where(Meeting.room_code == code))
        if not existing.scalar_one_or_none():
            break
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique room code")

    room_name = f"syltalky-{code.lower()}"

    # Create LiveKit room
    try:
        await _create_livekit_room(room_name)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LiveKit unavailable: {e}")

    # Persist meeting
    meeting = Meeting(
        room_code=code,
        host_id=user.id,
        livekit_room_name=room_name,
    )
    db.add(meeting)
    await db.flush()  # get meeting.id

    # Add host as participant
    participant = MeetingParticipant(meeting_id=meeting.id, user_id=user.id)
    db.add(participant)
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
    """Join a meeting by room code. Returns a LiveKit token."""
    result = await db.execute(
        select(Meeting).where(
            Meeting.room_code == body.room_code.upper().replace(" ", ""),
            Meeting.ended_at.is_(None),
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Room not found or already ended")

    # Upsert participant record
    part_result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == user.id,
        )
    )
    participant = part_result.scalar_one_or_none()
    if participant:
        participant.left_at = None  # rejoin
    else:
        participant = MeetingParticipant(meeting_id=meeting.id, user_id=user.id)
        db.add(participant)
    await db.commit()

    # Issue LiveKit token (synchronous, no network required)
    try:
        token = _generate_token(
            room_name=meeting.livekit_room_name,
            identity=str(user.id),
            display_name=user.display_name,
            avatar_url=get_public_url(user.avatar_path) if user.avatar_path else "",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")

    return JoinResponse(
        token=token,
        livekit_url=settings.LIVEKIT_PUBLIC_URL,
        meeting_id=meeting.id,
        room_code=meeting.room_code,
        host_id=meeting.host_id,
    )


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
    await db.commit()

    # Delete LiveKit room (kick all participants)
    try:
        await _delete_livekit_room(meeting.livekit_room_name)
    except Exception:
        pass  # non-fatal — Phase 8 will handle cleanup


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
    return MeetingOut.model_validate(m, from_attributes=True).model_copy(update={"host_name": host_name})


@router.post("/webhook")
async def livekit_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle LiveKit webhook events."""
    body = await request.body()
    auth_header = request.headers.get("Authorization", "")

    verifier = livekit_api.TokenVerifier(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )
    receiver = livekit_api.WebhookReceiver(token_verifier=verifier)
    try:
        event = receiver.receive(body.decode(), auth_header)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event.event == "participant_left":
        participant_identity = event.participant.identity if event.participant else None
        room_name = event.room.name if event.room else None
        if participant_identity and room_name:
            result = await db.execute(
                select(Meeting).where(Meeting.livekit_room_name == room_name)
            )
            meeting = result.scalar_one_or_none()
            if meeting:
                try:
                    part_id = uuid.UUID(participant_identity)
                    part_result = await db.execute(
                        select(MeetingParticipant).where(
                            MeetingParticipant.meeting_id == meeting.id,
                            MeetingParticipant.user_id == part_id,
                        )
                    )
                    part = part_result.scalar_one_or_none()
                    if part:
                        part.left_at = datetime.now(timezone.utc)
                        await db.commit()
                except ValueError:
                    pass  # non-UUID identity (e.g. egress bot)

    elif event.event == "room_finished":
        room_name = event.room.name if event.room else None
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

    return {"ok": True}
