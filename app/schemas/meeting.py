import uuid
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class MeetingCreate(BaseModel):
    waiting_room_enabled: bool = True


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
    waiting_room_enabled: bool = True
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    summary: Optional[str] = None
    transcript: Optional[list] = None

    model_config = {"from_attributes": True}


class JoinResponse(BaseModel):
    status: str = "joined"   # "joined" | "waiting"
    # filled when status == "joined"
    token: Optional[str] = None
    livekit_url: Optional[str] = None
    meeting_id: Optional[uuid.UUID] = None
    room_code: Optional[str] = None
    host_id: Optional[uuid.UUID] = None
    waiting_room_enabled: Optional[bool] = None
    # filled when status == "waiting"
    request_id: Optional[uuid.UUID] = None


class WaitingRequestOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    avatar_url: Optional[str] = None
    requested_at: datetime

    model_config = {"from_attributes": True}
