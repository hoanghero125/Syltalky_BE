import io
import json
from minio import Minio
from minio.error import S3Error

from app.config import settings

_client: Minio | None = None

_PUBLIC_READ_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": ["s3:GetObject"],
        "Resource": [f"arn:aws:s3:::{settings.MINIO_BUCKET}/*"],
    }],
})


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
    client.set_bucket_policy(settings.MINIO_BUCKET, _PUBLIC_READ_POLICY)


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    client = get_minio()
    client.put_object(
        settings.MINIO_BUCKET, key,
        io.BytesIO(data), length=len(data),
        content_type=content_type,
    )
    return key


def get_public_url(key: str) -> str:
    scheme = "https" if settings.MINIO_PUBLIC_SECURE else "http"
    return f"{scheme}://{settings.MINIO_PUBLIC_ENDPOINT}/{settings.MINIO_BUCKET}/{key}"


def delete_object(key: str):
    client = get_minio()
    try:
        client.remove_object(settings.MINIO_BUCKET, key)
    except S3Error:
        pass
