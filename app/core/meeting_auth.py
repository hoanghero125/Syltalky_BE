import uuid
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.meeting import Meeting, MeetingParticipant
from app.models.user import User


async def get_meeting_or_404(meeting_id: uuid.UUID | str, db: AsyncSession) -> Meeting:
    if isinstance(meeting_id, str):
        try:
            meeting_id = uuid.UUID(meeting_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Meeting not found")
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting


def _is_host_or_cohost(meeting: Meeting, user_id: uuid.UUID) -> bool:
    if meeting.host_id == user_id:
        return True
    co_hosts = meeting.co_hosts or []
    return str(user_id) in [str(c) for c in co_hosts]


async def require_host_or_cohost(meeting_id, user: User, db: AsyncSession) -> Meeting:
    meeting = await get_meeting_or_404(meeting_id, db)
    if not _is_host_or_cohost(meeting, user.id):
        raise HTTPException(status_code=403, detail="Host or co-host required")
    return meeting


async def require_participant(meeting_id, user: User, db: AsyncSession) -> Meeting:
    meeting = await get_meeting_or_404(meeting_id, db)
    if meeting.host_id == user.id:
        return meeting
    co_hosts = meeting.co_hosts or []
    if str(user.id) in [str(c) for c in co_hosts]:
        return meeting
    # Otherwise must be a registered participant of the meeting
    result = await db.execute(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingParticipant.user_id == user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a participant of this meeting")
    return meeting
