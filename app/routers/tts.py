import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/tts", tags=["tts"])


class DesignDemoRequest(BaseModel):
    text: str
    instruct: str


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
