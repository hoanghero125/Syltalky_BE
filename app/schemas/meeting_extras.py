from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


# ── Pinned messages ──────────────────────────────────────────────────────────

class PinIn(BaseModel):
    sender_id: str
    sender_name: str
    text: str
    original_ts_ms: int = 0


class PinOut(BaseModel):
    id: str
    sender_id: str
    sender_name: str
    text: str
    original_ts_ms: int
    pinned_by: str
    pinned_at: datetime


# ── Polls ────────────────────────────────────────────────────────────────────

class PollOption(BaseModel):
    id: str
    text: str


class PollIn(BaseModel):
    question: str
    options: List[PollOption]
    anonymous: bool = False
    multi_choice: bool = False
    max_selections: Optional[int] = None  # None / 0 = unlimited; only meaningful when multi_choice=True


class PollVoter(BaseModel):
    user_id: str
    display_name: str = ""
    avatar_url: str = ""


class PollOut(BaseModel):
    id: str
    question: str
    options: List[PollOption]
    anonymous: bool
    multi_choice: bool
    max_selections: Optional[int] = None
    closed: bool
    created_by: str
    created_at: datetime
    closed_at: Optional[datetime] = None
    tallies: Dict[str, int] = Field(default_factory=dict)
    # voters: only populated when not anonymous
    voters: Dict[str, List[PollVoter]] = Field(default_factory=dict)
    # my_votes: option_ids voted for by the requester ({poll_id: [...]})
    my_votes: Dict[str, List[str]] = Field(default_factory=dict)


class PollVoteIn(BaseModel):
    option_ids: List[str]


class PollClosedOut(BaseModel):
    closed: bool = True
    closed_at: datetime


# ── Notes ────────────────────────────────────────────────────────────────────

class NoteCreateIn(BaseModel):
    title: str


class NoteRenameIn(BaseModel):
    title: str


class NoteOut(BaseModel):
    id: str
    title: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    plain_text: str = ""
    plain_html: str = ""


# ── State / co-hosts ─────────────────────────────────────────────────────────

class MeetingStateOut(BaseModel):
    pins: List[PinOut]
    polls: List[PollOut]
    notes: List[NoteOut]


class CoHostsIn(BaseModel):
    co_hosts: List[str]
