import uuid
from datetime import datetime
from pydantic import BaseModel


class VoiceProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    ref_text: str
    ref_audio_url: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class VoiceProfileRename(BaseModel):
    name: str
