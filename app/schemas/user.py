from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    gender: str
    avatar_path: str | None
    is_verified: bool

    class Config:
        from_attributes = True


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
