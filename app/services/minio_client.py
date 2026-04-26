import io
from minio import Minio
from minio.error import S3Error

from app.config import settings

_client: Minio | None = None


def get_minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ROOT_USER,
            secret_key=settings.MINIO_ROOT_PASSWORD,
            secure=settings.MINIO_SECURE,
        )
    return _client


def ensure_bucket():
    client = get_minio()
    if not client.bucket_exists(settings.MINIO_BUCKET):
        client.make_bucket(settings.MINIO_BUCKET)


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    client = get_minio()
    client.put_object(
        settings.MINIO_BUCKET, key,
        io.BytesIO(data), length=len(data),
        content_type=content_type,
    )
    return key


def get_presigned_url(key: str, expires_hours: int = 24) -> str:
    from datetime import timedelta
    client = get_minio()
    return client.presigned_get_object(
        settings.MINIO_BUCKET, key,
        expires=timedelta(hours=expires_hours),
    )


def delete_object(key: str):
    client = get_minio()
    try:
        client.remove_object(settings.MINIO_BUCKET, key)
    except S3Error:
        pass
