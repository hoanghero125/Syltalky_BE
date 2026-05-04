import uuid
from datetime import datetime, timezone
from app.services.minio_client import get_public_url

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

try:
    import bleach  # type: ignore
    _BLEACH_OK = True
except Exception:
    _BLEACH_OK = False

_ALLOWED_TAGS = {
    "p", "br", "strong", "em", "s", "u", "code", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "a", "hr",
}
_ALLOWED_ATTRS = {"a": ["href", "title", "target", "rel"]}

from app.database import get_db
from app.core.deps import get_current_user
from app.core.meeting_auth import (
    get_meeting_or_404, require_host_or_cohost, require_participant,
)
from app.models.user import User
from app.models.meeting import Meeting
from app.models.meeting_extras import PinnedMessage, Poll, PollVote, Note
from app.schemas.meeting_extras import (
    PinIn, PinOut, PollIn, PollOut, PollVoteIn, PollClosedOut,
    NoteCreateIn, NoteRenameIn, NoteOut,
    MeetingStateOut, CoHostsIn,
)


router = APIRouter(prefix="/meetings", tags=["meeting-extras"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pin_to_out(p: PinnedMessage) -> PinOut:
    return PinOut(
        id=str(p.id),
        sender_id=str(p.sender_id),
        sender_name=p.sender_name,
        text=p.text,
        original_ts_ms=p.original_ts_ms or 0,
        pinned_by=str(p.pinned_by),
        pinned_at=p.pinned_at,
    )


async def _build_poll_out(
    poll: Poll,
    votes: list[PollVote],
    user_lookup: dict[str, User],
    requester_id: uuid.UUID,
) -> PollOut:
    tallies: dict[str, int] = {}
    voters: dict[str, list[dict]] = {}
    my_for_poll: list[str] = []

    for v in votes:
        tallies[v.option_id] = tallies.get(v.option_id, 0) + 1
        if v.user_id == requester_id:
            my_for_poll.append(v.option_id)
        if not poll.anonymous:
            u = user_lookup.get(str(v.user_id))
            avatar_url = get_public_url(u.avatar_path) if (u and u.avatar_path) else ""
            voters.setdefault(v.option_id, []).append({
                "user_id": str(v.user_id),
                "display_name": (u.display_name if u else "") or "",
                "avatar_url": avatar_url or "",
            })

    return PollOut(
        id=str(poll.id),
        question=poll.question,
        options=poll.options or [],
        anonymous=poll.anonymous,
        multi_choice=poll.multi_choice,
        max_selections=poll.max_selections,
        closed=poll.closed,
        created_by=str(poll.created_by),
        created_at=poll.created_at,
        closed_at=poll.closed_at,
        tallies=tallies,
        voters=voters,
        my_votes={str(poll.id): my_for_poll},
    )


def _note_to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=str(n.id),
        title=n.title or "",
        created_by=str(n.created_by),
        created_at=n.created_at,
        updated_at=n.updated_at,
        plain_text=n.plain_text or "",
        plain_html=n.plain_html or "",
    )


# ── State (late-joiner snapshot) ─────────────────────────────────────────────

@router.get("/{meeting_id}/state", response_model=MeetingStateOut)
async def get_state(
    meeting_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_participant(meeting_id, user, db)

    pins_res = await db.execute(
        select(PinnedMessage).where(PinnedMessage.meeting_id == meeting.id).order_by(PinnedMessage.pinned_at)
    )
    pins = [_pin_to_out(p) for p in pins_res.scalars().all()]

    polls_res = await db.execute(
        select(Poll).where(Poll.meeting_id == meeting.id).order_by(Poll.created_at)
    )
    poll_list = polls_res.scalars().all()
    poll_ids = [p.id for p in poll_list]

    votes_by_poll: dict[uuid.UUID, list[PollVote]] = {pid: [] for pid in poll_ids}
    voter_user_ids: set[uuid.UUID] = set()
    if poll_ids:
        votes_res = await db.execute(select(PollVote).where(PollVote.poll_id.in_(poll_ids)))
        for v in votes_res.scalars().all():
            votes_by_poll.setdefault(v.poll_id, []).append(v)
            voter_user_ids.add(v.user_id)

    user_lookup: dict[str, User] = {}
    if voter_user_ids:
        users_res = await db.execute(select(User).where(User.id.in_(voter_user_ids)))
        for u in users_res.scalars().all():
            user_lookup[str(u.id)] = u

    polls_out = [
        await _build_poll_out(p, votes_by_poll.get(p.id, []), user_lookup, user.id)
        for p in poll_list
    ]

    notes_res = await db.execute(
        select(Note).where(Note.meeting_id == meeting.id, Note.deleted_at.is_(None)).order_by(Note.created_at)
    )
    notes = [_note_to_out(n) for n in notes_res.scalars().all()]

    return MeetingStateOut(pins=pins, polls=polls_out, notes=notes)


# ── Co-hosts (server-side authoritative list) ────────────────────────────────

@router.post("/{meeting_id}/co-hosts")
async def set_co_hosts(
    meeting_id: str,
    body: CoHostsIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await get_meeting_or_404(meeting_id, db)
    if meeting.host_id != user.id:
        raise HTTPException(status_code=403, detail="Only host can set co-hosts")
    # Sanitize: keep only valid UUID strings
    cleaned: list[str] = []
    for cid in body.co_hosts:
        try:
            cleaned.append(str(uuid.UUID(cid)))
        except (ValueError, TypeError):
            continue
    meeting.co_hosts = cleaned
    await db.commit()
    return {"co_hosts": cleaned}


# ── Pinned messages ──────────────────────────────────────────────────────────

@router.post("/{meeting_id}/pins", response_model=PinOut, status_code=201)
async def pin_message(
    meeting_id: str,
    body: PinIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    count_res = await db.execute(
        select(func.count(PinnedMessage.id)).where(PinnedMessage.meeting_id == meeting.id)
    )
    if (count_res.scalar() or 0) >= 5:
        raise HTTPException(status_code=409, detail="Đã đạt giới hạn 5 tin nhắn ghim")

    try:
        sender_uuid = uuid.UUID(body.sender_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid sender_id")

    pin = PinnedMessage(
        meeting_id=meeting.id,
        pinned_by=user.id,
        sender_id=sender_uuid,
        sender_name=body.sender_name,
        text=body.text,
        original_ts_ms=body.original_ts_ms or 0,
    )
    db.add(pin)
    await db.commit()
    await db.refresh(pin)
    return _pin_to_out(pin)


@router.delete("/{meeting_id}/pins/{pin_id}", status_code=204)
async def unpin_message(
    meeting_id: str,
    pin_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    try:
        pid = uuid.UUID(pin_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Pin not found")
    res = await db.execute(
        select(PinnedMessage).where(PinnedMessage.id == pid, PinnedMessage.meeting_id == meeting.id)
    )
    pin = res.scalar_one_or_none()
    if not pin:
        raise HTTPException(status_code=404, detail="Pin not found")
    await db.delete(pin)
    await db.commit()
    return None


# ── Polls ────────────────────────────────────────────────────────────────────

@router.post("/{meeting_id}/polls", response_model=PollOut, status_code=201)
async def create_poll(
    meeting_id: str,
    body: PollIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Câu hỏi không được trống")
    if len(body.options) < 2:
        raise HTTPException(status_code=400, detail="Cần ít nhất 2 lựa chọn")
    if len(body.options) > 10:
        raise HTTPException(status_code=400, detail="Tối đa 10 lựa chọn")

    max_sel = body.max_selections if body.multi_choice and body.max_selections and body.max_selections > 0 else None
    poll = Poll(
        meeting_id=meeting.id,
        created_by=user.id,
        question=body.question.strip(),
        options=[{"id": o.id, "text": o.text} for o in body.options],
        anonymous=body.anonymous,
        multi_choice=body.multi_choice,
        max_selections=max_sel,
    )
    db.add(poll)
    await db.commit()
    await db.refresh(poll)
    return await _build_poll_out(poll, [], {}, user.id)


@router.post("/{meeting_id}/polls/{poll_id}/vote", response_model=PollOut)
async def vote_poll(
    meeting_id: str,
    poll_id: str,
    body: PollVoteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_participant(meeting_id, user, db)
    try:
        pid = uuid.UUID(poll_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll = (await db.execute(
        select(Poll).where(Poll.id == pid, Poll.meeting_id == meeting.id)
    )).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    if poll.closed:
        raise HTTPException(status_code=409, detail="Bình chọn đã đóng")
    if not poll.multi_choice and len(body.option_ids) > 1:
        raise HTTPException(status_code=400, detail="Bình chọn này chỉ cho chọn 1")
    if poll.multi_choice and poll.max_selections and len(body.option_ids) > poll.max_selections:
        raise HTTPException(status_code=400, detail=f"Tối đa {poll.max_selections} lựa chọn")

    valid_ids = {o["id"] for o in (poll.options or [])}
    for oid in body.option_ids:
        if oid not in valid_ids:
            raise HTTPException(status_code=400, detail=f"Lựa chọn không hợp lệ: {oid}")

    # Replace this user's existing votes for this poll
    await db.execute(
        delete(PollVote).where(PollVote.poll_id == poll.id, PollVote.user_id == user.id)
    )
    for oid in body.option_ids:
        db.add(PollVote(poll_id=poll.id, user_id=user.id, option_id=oid))
    await db.commit()

    # Return refreshed poll state
    votes = (await db.execute(select(PollVote).where(PollVote.poll_id == poll.id))).scalars().all()
    voter_ids = {v.user_id for v in votes}
    user_lookup: dict[str, User] = {}
    if voter_ids:
        users = (await db.execute(select(User).where(User.id.in_(voter_ids)))).scalars().all()
        user_lookup = {str(u.id): u for u in users}
    return await _build_poll_out(poll, votes, user_lookup, user.id)


@router.post("/{meeting_id}/polls/{poll_id}/close", response_model=PollClosedOut)
async def close_poll(
    meeting_id: str,
    poll_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    try:
        pid = uuid.UUID(poll_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll = (await db.execute(
        select(Poll).where(Poll.id == pid, Poll.meeting_id == meeting.id)
    )).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll.closed = True
    poll.closed_at = datetime.now(timezone.utc)
    await db.commit()
    return PollClosedOut(closed=True, closed_at=poll.closed_at)


@router.delete("/{meeting_id}/polls/{poll_id}", status_code=204)
async def delete_poll(
    meeting_id: str,
    poll_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    try:
        pid = uuid.UUID(poll_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Poll not found")
    poll = (await db.execute(
        select(Poll).where(Poll.id == pid, Poll.meeting_id == meeting.id)
    )).scalar_one_or_none()
    if not poll:
        raise HTTPException(status_code=404, detail="Poll not found")
    await db.delete(poll)
    await db.commit()
    return None


# ── Notes (metadata; content syncs via Yjs WebSocket) ────────────────────────

@router.post("/{meeting_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(
    meeting_id: str,
    body: NoteCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    title = (body.title or "").strip() or "Ghi chú"
    note = Note(
        meeting_id=meeting.id,
        created_by=user.id,
        title=title,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _note_to_out(note)


@router.patch("/{meeting_id}/notes/{note_id}", response_model=NoteOut)
async def rename_note(
    meeting_id: str,
    note_id: str,
    body: NoteRenameIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    note = (await db.execute(
        select(Note).where(Note.id == nid, Note.meeting_id == meeting.id, Note.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    title = (body.title or "").strip()
    if title:
        note.title = title
    await db.commit()
    await db.refresh(note)
    return _note_to_out(note)


class NoteSnapshotIn(BaseModel):
    plain_text: str = ""
    plain_html: str = ""


@router.post("/{meeting_id}/notes/{note_id}/snapshot")
async def note_snapshot(
    meeting_id: str,
    note_id: str,
    body: NoteSnapshotIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_participant(meeting_id, user, db)
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    note = (await db.execute(
        select(Note).where(Note.id == nid, Note.meeting_id == meeting.id, Note.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    note.plain_text = body.plain_text or ""
    if _BLEACH_OK and body.plain_html:
        note.plain_html = bleach.clean(body.plain_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
    else:
        note.plain_html = body.plain_html or ""
    note.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.delete("/{meeting_id}/notes/{note_id}", status_code=204)
async def delete_note(
    meeting_id: str,
    note_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    meeting = await require_host_or_cohost(meeting_id, user, db)
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    note = (await db.execute(
        select(Note).where(Note.id == nid, Note.meeting_id == meeting.id, Note.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    note.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return None
