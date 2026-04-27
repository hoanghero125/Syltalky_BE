import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.voice_profile import VoiceProfile

router = APIRouter(prefix="/tts", tags=["tts"])


class DesignDemoRequest(BaseModel):
    text: str
    instruct: str


class CloneDemoRequest(BaseModel):
    text: str
    voice_id: str  # DB UUID of the voice profile


@router.post("/demo/design")
async def demo_design(body: DesignDemoRequest, _: User = Depends(get_current_user)):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{settings.AI_API_URL}/tts/design",
                json={"instruct": body.instruct, "text": body.text},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"AI API error: {e.response.text}")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="AI API unreachable")
    return Response(content=resp.content, media_type="audio/wav")


@router.post("/demo/clone")
async def demo_clone(
    body: CloneDemoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    # Look up the AI API voice_id from the profile
    result = await db.execute(
        select(VoiceProfile).where(
            VoiceProfile.id == uuid.UUID(body.voice_id),
            VoiceProfile.user_id == user.id,
        )
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    if not profile.active_voice_id:
        raise HTTPException(status_code=409, detail="Voice not registered with AI API yet")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{settings.AI_API_URL}/tts/synthesize",
                json={"voice_id": profile.active_voice_id, "text": body.text},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"AI API error: {e.response.text}")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="AI API unreachable")
    return Response(content=resp.content, media_type="audio/wav")
