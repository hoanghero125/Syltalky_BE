import re
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

_SPEAKER_RE = re.compile(r'^\s*[A-Za-z][A-Za-z\s\'.]*:\s*')

from app.config import settings
from app.core.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/sign", tags=["sign"])


@router.post("")
async def translate_sign(
    video: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    content = await video.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty video file")

    filename = "recording.webm" if "webm" in (video.content_type or "") else "recording.mp4"
    content_type = video.content_type or "video/webm"

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{settings.AI_API_URL}/sign",
                files={"video": (filename, content, content_type)},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = e.response.text
            try:
                detail = e.response.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=f"AI API error: {detail}")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="AI API unreachable")

    data = resp.json()
    if "text" in data:
        data["text"] = _SPEAKER_RE.sub('', data["text"]).strip()
    return data
