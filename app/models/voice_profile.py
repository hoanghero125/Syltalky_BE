import uuid
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    ref_audio_path: Mapped[str] = mapped_column(String, nullable=False)
    ref_text: Mapped[str] = mapped_column(String, nullable=False)
    active_voice_id: Mapped[str | None] = mapped_column(String, nullable=True)  # AI API volatile id
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="voice_profiles")


class UserVoiceConfig(Base):
    __tablename__ = "user_voice_config"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="design")  # 'design' | 'clone'
    design_instruct: Mapped[str | None] = mapped_column(String, nullable=True)
    active_voice_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("voice_profiles.id"), nullable=True)

    user = relationship("User", back_populates="voice_config")
    active_voice_profile = relationship("VoiceProfile")
