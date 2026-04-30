import uuid
from sqlalchemy import String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    livekit_room_name: Mapped[str | None] = mapped_column(String, nullable=True)
    waiting_room_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String, nullable=True)
    transcript: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_history: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    host = relationship("User", foreign_keys=[host_id])
    participants = relationship("MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan")
    captions = relationship("Caption", back_populates="meeting", cascade="all, delete-orphan")
    waiting_requests = relationship("MeetingWaitingRequest", back_populates="meeting", cascade="all, delete-orphan")


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"

    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    joined_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kicked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    meeting = relationship("Meeting", back_populates="participants")
    user = relationship("User")


class MeetingWaitingRequest(Base):
    __tablename__ = "meeting_waiting_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/approved/denied
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("Meeting", back_populates="waiting_requests")
    user = relationship("User")
