import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.user import User
from app.models.voice_profile import UserVoiceConfig
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    create_email_token, decode_token,
)
from app.core.deps import get_current_user
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserOut,
    RefreshRequest, ForgotPasswordRequest, ResetPasswordRequest, VerifyEmailRequest,
    GoogleAuthRequest, GoogleAuthResponse, CompleteProfileRequest,
)
from app.services.email import send_verification_email, send_reset_password_email
from app.services.minio_client import upload_bytes
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_VOICE = {
    "female": "female, young adult, moderate pitch",
    "male": "male, young adult, moderate pitch",
}


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        gender=user.gender,
        avatar_url=None,
        is_verified=user.is_verified,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        display_name=body.display_name,
        gender=body.gender,
        hashed_password=hash_password(body.password),
        is_verified=False,
    )
    db.add(user)
    await db.flush()

    voice_config = UserVoiceConfig(
        user_id=user.id,
        mode="design",
        design_instruct=DEFAULT_VOICE[body.gender],
    )
    db.add(voice_config)
    await db.commit()

    token = create_email_token(str(user.id), "verify")
    send_verification_email(user.email, user.display_name, token)

    return {"message": "Registration successful. Please check your email to verify your account."}


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.hashed_password:
        raise HTTPException(status_code=401, detail="This account uses Google Sign-In. Please log in with Google.")
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before logging in")

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        user=_user_out(user),
    )


@router.post("/refresh")
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh" or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.token)
    if payload.get("purpose") != "verify" or not payload.get("sub"):
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_verified = True
    await db.commit()
    return {"message": "Email verified successfully"}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user:
        token = create_email_token(str(user.id), "reset")
        send_reset_password_email(user.email, user.display_name, token)
    return {"message": "If that email exists, a reset link has been sent"}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.token)
    if payload.get("purpose") != "reset" or not payload.get("sub"):
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"message": "Password reset successfully"}


@router.post("/google", response_model=GoogleAuthResponse)
async def google_auth(body: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {body.credential}"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    info = resp.json()
    if not info.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google email not verified")

    google_id = info["sub"]
    email = info["email"]
    name = info.get("name", email.split("@")[0])
    picture_url = info.get("picture")

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    needs_profile = False
    if not user:
        user = User(
            email=email,
            display_name=name,
            google_id=google_id,
            is_verified=True,
        )
        db.add(user)
        await db.flush()

        # Download and store Google profile picture
        if picture_url and not user.avatar_path:
            try:
                async with httpx.AsyncClient() as pic_client:
                    pic_resp = await pic_client.get(picture_url)
                if pic_resp.status_code == 200:
                    avatar_key = f"avatars/{user.id}.jpg"
                    upload_bytes(avatar_key, pic_resp.content, "image/jpeg")
                    user.avatar_path = avatar_key
            except Exception:
                pass

        needs_profile = True
    else:
        if not user.google_id:
            user.google_id = google_id
        if not user.gender:
            needs_profile = True

    if not needs_profile and not await db.scalar(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == user.id)
    ):
        db.add(UserVoiceConfig(
            user_id=user.id,
            mode="design",
            design_instruct=DEFAULT_VOICE.get(user.gender, "female, young adult, moderate pitch"),
        ))

    await db.commit()
    await db.refresh(user)

    return GoogleAuthResponse(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
        user=_user_out(user),
        needs_profile=needs_profile,
    )


@router.post("/complete-profile")
async def complete_profile(
    body: CompleteProfileRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user.gender = body.gender
    user.display_name = body.display_name

    existing_config = await db.scalar(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == user.id)
    )
    if not existing_config:
        db.add(UserVoiceConfig(
            user_id=user.id,
            mode="design",
            design_instruct=DEFAULT_VOICE[body.gender],
        ))

    await db.commit()
    await db.refresh(user)
    return _user_out(user)
