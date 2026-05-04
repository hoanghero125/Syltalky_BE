import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User
from app.models.voice_profile import UserVoiceConfig
from app.core.deps import get_current_user
from app.services.minio_client import upload_bytes, get_public_url, delete_object
from app.schemas.user import UserOut, UpdateVoiceConfigRequest, VoiceConfigOut

router = APIRouter(prefix="/users", tags=["users"])


def _avatar_url(user: User) -> str | None:
    if not user.avatar_path:
        return None
    return get_public_url(user.avatar_path)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        gender=user.gender,
        avatar_url=_avatar_url(user),
        is_verified=user.is_verified,
    )


@router.get("/by-ids")
async def get_users_by_ids(
    ids: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch display_name + avatar_url for a comma-separated list of user IDs."""
    try:
        parsed = [uuid.UUID(i.strip()) for i in ids.split(",") if i.strip()]
    except ValueError:
        return []
    result = await db.execute(select(User).where(User.id.in_(parsed)))
    users = result.scalars().all()
    return [{"id": str(u.id), "display_name": u.display_name, "avatar_url": _avatar_url(u)} for u in users]


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return _user_out(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    display_name: str | None = Form(None),
    remove_avatar: bool = Form(False),
    avatar: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if display_name is not None:
        current_user.display_name = display_name.strip() or current_user.display_name

    if remove_avatar and current_user.avatar_path:
        delete_object(current_user.avatar_path)
        current_user.avatar_path = None

    if avatar and avatar.filename:
        data = await avatar.read()
        ext = avatar.filename.rsplit('.', 1)[-1].lower()
        key = f"avatars/{current_user.id}_{uuid.uuid4().hex[:8]}.{ext}"
        if current_user.avatar_path and current_user.avatar_path != key:
            delete_object(current_user.avatar_path)
        upload_bytes(key, data, avatar.content_type or "image/jpeg")
        current_user.avatar_path = key

    await db.commit()
    await db.refresh(current_user)
    return _user_out(current_user)


@router.get("/me/voice-config", response_model=VoiceConfigOut)
async def get_voice_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Voice config not found")
    return VoiceConfigOut(
        mode=cfg.mode,
        design_instruct=cfg.design_instruct,
        active_voice_profile_id=str(cfg.active_voice_profile_id) if cfg.active_voice_profile_id else None,
    )


@router.patch("/me/voice-config", response_model=VoiceConfigOut)
async def update_voice_config(
    body: UpdateVoiceConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Voice config not found")

    if body.mode is not None:
        cfg.mode = body.mode
    if body.design_instruct is not None:
        cfg.design_instruct = body.design_instruct
    if body.active_voice_profile_id is not None:
        cfg.active_voice_profile_id = uuid.UUID(body.active_voice_profile_id)

    await db.commit()
    return VoiceConfigOut(
        mode=cfg.mode,
        design_instruct=cfg.design_instruct,
        active_voice_profile_id=str(cfg.active_voice_profile_id) if cfg.active_voice_profile_id else None,
    )
