import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.services.minio_client import ensure_bucket, get_public_url
from app.routers import auth, users, tts, voices, meetings


async def _reregister_voices():
    """On startup, re-register all voice profiles with the AI API (voice_ids are volatile)."""
    async for db in get_db():
        from app.models.voice_profile import VoiceProfile
        result = await db.execute(select(VoiceProfile))
        profiles = result.scalars().all()
        if not profiles:
            return

        async with httpx.AsyncClient(timeout=60) as client:
            for profile in profiles:
                try:
                    # Download ref audio from MinIO public URL
                    audio_resp = await client.get(get_public_url(profile.ref_audio_path))
                    audio_resp.raise_for_status()

                    reg_resp = await client.post(
                        f"{settings.AI_API_URL}/tts/voice",
                        files={"ref_audio": ("audio.webm", audio_resp.content, "audio/webm")},
                        data={"ref_text": profile.ref_text},
                    )
                    reg_resp.raise_for_status()
                    profile.active_voice_id = reg_resp.json()["voice_id"]
                    print(f"[startup] Re-registered voice {profile.id} ({profile.name})")
                except Exception as e:
                    print(f"[startup] Failed to re-register voice {profile.id}: {e}")

        await db.commit()
        break  # only need one db session


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_bucket()
    await _reregister_voices()
    yield


app = FastAPI(title="Syltalky Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tts.router)
app.include_router(voices.router)
app.include_router(meetings.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
