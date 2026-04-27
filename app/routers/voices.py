import uuid
import httpx

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.voice_profile import VoiceProfile, UserVoiceConfig
from app.schemas.voice import VoiceProfileOut, VoiceProfileRename
from app.services.minio_client import upload_bytes, get_public_url, delete_object

router = APIRouter(tags=["voices"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _profile_to_out(p: VoiceProfile) -> VoiceProfileOut:
    return VoiceProfileOut(
        id=p.id,
        name=p.name,
        ref_text=p.ref_text,
        ref_audio_url=get_public_url(p.ref_audio_path),
        is_active=p.is_active,
        created_at=p.created_at,
    )


async def _register_voice(ref_audio_bytes: bytes, ref_text: str, filename: str) -> str:
    """Call AI API /tts/voice → return voice_id."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{settings.AI_API_URL}/tts/voice",
            files={"ref_audio": (filename, ref_audio_bytes, "audio/webm")},
            data={"ref_text": ref_text},
        )
        resp.raise_for_status()
    return resp.json()["voice_id"]


# ── STT transcribe proxy ──────────────────────────────────────────────────────

@router.post("/stt/transcribe")
async def stt_transcribe(
    file: UploadFile = File(...),
    start_sec: float = Form(0.0),
    end_sec: float   = Form(None),
    _: User = Depends(get_current_user),
):
    """Proxy audio file to AI API /stt for transcription."""
    data = await file.read()
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{settings.AI_API_URL}/stt",
                files={"audio": (file.filename, data, file.content_type or "audio/webm")},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"STT error: {e.response.text}")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="AI API unreachable")
    return resp.json()


# ── Voice profile CRUD ────────────────────────────────────────────────────────

@router.get("/voices", response_model=list[VoiceProfileOut])
async def list_voices(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VoiceProfile)
        .where(VoiceProfile.user_id == user.id)
        .order_by(VoiceProfile.created_at.desc())
    )
    return [_profile_to_out(p) for p in result.scalars().all()]


@router.post("/voices", response_model=VoiceProfileOut, status_code=201)
async def create_voice(
    name: str       = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str   = Form(...),
    start_sec: float = Form(0.0),
    end_sec: float   = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    audio_bytes = await ref_audio.read()

    # Register with AI API to get voice_id
    try:
        voice_id = await _register_voice(audio_bytes, ref_text, ref_audio.filename or "audio.webm")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Voice registration failed: {e.response.text}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="AI API unreachable")

    # Store audio in MinIO
    key = f"voices/{user.id}/{uuid.uuid4()}/{ref_audio.filename or 'audio.webm'}"
    upload_bytes(key, audio_bytes, ref_audio.content_type or "audio/webm")

    profile = VoiceProfile(
        user_id=user.id,
        name=name,
        ref_audio_path=key,
        ref_text=ref_text,
        active_voice_id=voice_id,
        is_active=False,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return _profile_to_out(profile)


@router.delete("/voices/{voice_id}", status_code=204)
async def delete_voice(
    voice_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")

    # Clear from voice config if it was active
    cfg_result = await db.execute(
        select(UserVoiceConfig).where(UserVoiceConfig.user_id == user.id)
    )
    cfg = cfg_result.scalar_one_or_none()
    if cfg and cfg.active_voice_profile_id == voice_id:
        cfg.active_voice_profile_id = None

    delete_object(profile.ref_audio_path)
    await db.delete(profile)
    await db.commit()


@router.patch("/voices/{voice_id}", response_model=VoiceProfileOut)
async def rename_voice(
    voice_id: uuid.UUID,
    body: VoiceProfileRename,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    profile.name = body.name
    await db.commit()
    await db.refresh(profile)
    return _profile_to_out(profile)

