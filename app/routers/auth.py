import uuid
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
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserOut,
    RefreshRequest, ForgotPasswordRequest, ResetPasswordRequest, VerifyEmailRequest,
)
from app.services.email import send_verification_email, send_reset_password_email

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

    if not user or not verify_password(body.password, user.hashed_password):
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
