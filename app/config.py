from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"
    MINIO_ROOT_USER: str = "syltalky"
    MINIO_ROOT_PASSWORD: str = "syltalky123"
    MINIO_BUCKET: str = "syltalky"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_SECURE: bool = True

    RESEND_API_KEY: str
    RESEND_FROM: str = "noreply@syltalky.app"

    LIVEKIT_URL: str = "ws://localhost:7880"
    LIVEKIT_PUBLIC_URL: str = "ws://localhost:7880"
    LIVEKIT_API_KEY: str = "devkey"
    LIVEKIT_API_SECRET: str = "devsecret"

    AI_API_URL: str = "http://localhost:8000"

    LLM_BASE_URL: str
    LLM_API_KEY: str

    GOOGLE_CLIENT_ID: str = ""

    FRONTEND_URL: str = "http://localhost:5173"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
