from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://scenarai:changeme@localhost:5432/scenarai"
    redis_url:    str = "redis://localhost:6379/0"
    jwt_secret:   str = "dev_secret"
    jwt_algorithm: str = "HS256"
    # Short-lived on purpose -- a stolen access token that can't be
    # individually revoked (stateless JWT) should only be dangerous for a
    # bounded window. Session continuity now comes from the separately
    # revocable refresh token below, not from a long-lived access token.
    jwt_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    groq_api_key: str = ""
    cors_origins: str = "http://localhost:3000"

    # Section 4 media pipeline (NEXT_PHASE_PLAN.md) — zero egress, S3-compatible.
    # All empty by default; storage.py.is_configured() gates on these rather
    # than failing at import time, since most of the app runs fine without them.
    r2_account_id:        str = ""
    r2_access_key_id:     str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name:       str = ""
    # Set after enabling R2.dev public access or a custom domain in the
    # Cloudflare dashboard (R2 buckets aren't publicly readable by default,
    # and the R2.dev URL's hash isn't derivable from account_id/bucket_name
    # alone) — e.g. "https://pub-xxxx.r2.dev" or "https://media.yourdomain.com".
    r2_public_base_url:   str = ""

    # Mock billing scaffolding (no real payment processor yet — see
    # models.py's UserSubscription docstring). False by default: real
    # visitors see "coming soon" until this is deliberately flipped on for
    # internal testing. Never gates on this being true to mean "a real
    # charge happened" — is_mock is always True regardless of this flag.
    mock_billing_enabled: bool = False

    # Local image-generation bridge (imagepipe/bridge_server.py) — a native
    # process outside Docker, kept resident for GPU access. See
    # docker-compose.yml's worker service for the extra_hosts entry this
    # depends on.
    image_bridge_url: str = "http://host.docker.internal:8188"
    image_bridge_timeout: float = 120.0

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
