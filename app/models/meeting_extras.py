import uuid
from sqlalchemy import String, DateTime, ForeignKey, Text, Boolean, BigInteger, LargeBinary, Index, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class PinnedMessage(Base):
    __tablename__ = "pinned_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    pinned_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sender_name: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pinned_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # [{id, text}]
    anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    multi_choice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    max_selections: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PollVote(Base):
    __tablename__ = "poll_votes"

    poll_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("polls.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    option_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    voted_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ydoc_state: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    plain_html: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
