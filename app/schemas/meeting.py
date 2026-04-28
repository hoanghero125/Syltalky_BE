import uuid
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class MeetingCreate(BaseModel):
    pass  # no body required — backend generates room code


class MeetingJoin(BaseModel):
    room_code: str


class MeetingEnd(BaseModel):
    pass


class ParticipantOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    joined_at: datetime
    left_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class MeetingOut(BaseModel):
    id: uuid.UUID
    room_code: str
    host_id: uuid.UUID
    host_name: Optional[str] = None
    livekit_room_name: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    summary: Optional[str] = None
    transcript: Optional[list] = None

    model_config = {"from_attributes": True}


class JoinResponse(BaseModel):
    token: str
    livekit_url: str
    meeting_id: uuid.UUID
    room_code: str
    host_id: uuid.UUID
