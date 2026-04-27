from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    gender: str
    avatar_url: str | None
    is_verified: bool

    class Config:
        from_attributes = True


class UpdateVoiceConfigRequest(BaseModel):
    mode: str | None = None
    design_instruct: str | None = None
    active_voice_profile_id: str | None = None


class VoiceConfigOut(BaseModel):
    mode: str
    design_instruct: str | None
    active_voice_profile_id: str | None
