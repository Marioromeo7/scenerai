"""
Cloudflare R2 storage — Section 4 of NEXT_PHASE_PLAN.md. Decided over S3
specifically for zero egress fees (assets get streamed repeatedly by
players, not stored once and forgotten) and a permanent 10GB/month free tier.

R2 is S3-compatible, so this is a thin wrapper around aioboto3 pointed at
R2's endpoint — no new client library, matches the S3 API most tooling
already understands. Not wired into any route yet; gated behind
is_configured() so the rest of the app runs fine without R2 credentials set
(matches how prefab/turns already work with zero image/video pipeline).

Requires `aioboto3` — not yet in requirements.txt, add when this is
actually wired into a route (no point installing it before there's a
caller, per the "don't add things you don't need yet" rule).
"""
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        settings.r2_account_id and settings.r2_access_key_id
        and settings.r2_secret_access_key and settings.r2_bucket_name
    )


def _endpoint_url() -> str:
    return f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"


def _client_session():
    """Returns an aioboto3 client as an async context manager:
        async with _client_session() as client:
            await client.put_object(...)
    Imported lazily so importing this module doesn't require aioboto3 to be
    installed until something actually calls into it."""
    import aioboto3
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",  # R2 has no regions; boto3 requires something non-empty
    )


def public_url(key: str) -> Optional[str]:
    """None if no public base URL is configured yet — caller should treat
    that as "uploaded but not yet servable", not an error; R2 buckets
    aren't publicly readable until R2.dev or a custom domain is enabled."""
    if not settings.r2_public_base_url:
        return None
    return f"{settings.r2_public_base_url.rstrip('/')}/{key.lstrip('/')}"


async def upload_bytes(key: str, data: bytes, content_type: str) -> Optional[str]:
    """Uploads and returns the public URL (or None if no public base URL is
    configured — see public_url()). Raises RuntimeError if R2 isn't
    configured at all; callers on the generation path should check
    is_configured() first and skip/queue-for-later rather than let this raise."""
    if not is_configured():
        raise RuntimeError(
            "R2 not configured — set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME"
        )
    async with _client_session() as client:
        await client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    url = public_url(key)
    if url is None:
        logger.warning(f"Uploaded {key} to R2 but no R2_PUBLIC_BASE_URL is set — "
                        f"object exists but isn't servable yet")
    return url


async def delete_object(key: str) -> None:
    if not is_configured():
        raise RuntimeError("R2 not configured")
    async with _client_session() as client:
        await client.delete_object(Bucket=settings.r2_bucket_name, Key=key)
