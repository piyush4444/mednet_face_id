"""
config.py — Backend configuration via environment variables.

Uses pydantic-settings to load values from environment / .env file.
Import the singleton `settings` object everywhere:

    from app.core.config import settings
    print(settings.APP_NAME)
"""

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the FastAPI backend.

    Values are read from environment variables (case-insensitive).
    A .env file in the project root is loaded automatically if present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────────────
    APP_NAME: str = "Face Recognition API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Server ───────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── CORS ─────────────────────────────────────────────────────────────
    # Accepts either a JSON array (`["https://a","https://b"]`) or a
    # comma-separated string (`"https://a,https://b"`). `NoDecode` stops
    # pydantic-settings from JSON-parsing the raw env value before our
    # validator runs.
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["*"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json
                return json.loads(s)
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    # ── Paths (relative to project root) ─────────────────────────────────
    DATABASE_DIR: str = "database"
    FAISS_INDEX_PATH: str = "database/face_index.bin"
    NAME_MAP_PATH: str = "database/name_map.json"

    # ── Media storage (profile photos) ───────────────────────────────────
    # Photos are stored under MEDIA_DIR and served at ``/media/*`` by
    # this API. When a dedicated media server arrives, point
    # MEDIA_BASE_URL at it (e.g. ``https://media.example.com``) — the DB
    # keeps only relative paths, so no rewrite is needed. MEDIA_DIR is
    # gitignored: profile photos are biometric PII and must never be
    # committed.
    MEDIA_DIR: str = "database/media"
    MEDIA_BASE_URL: str = ""

    # ── Relational database (PostgreSQL) ─────────────────────────────────
    # Set via the DATABASE_URL env var. The default points at a local dev
    # database and intentionally has no real password so credentials are
    # never baked into source. For deployments, supply your own URL via
    # the environment or a .env file (which is .gitignored).
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/facedb"

    # ── First-run bootstrap ──────────────────────────────────────────────
    # Used only by ``python -m backend.scripts.seed_initial``. Keep the
    # password blank in source and provide it through the environment.
    BOOTSTRAP_FACILITY_NAME: str = "Mednet"
    BOOTSTRAP_SUPERADMIN_NAME: str = "Mednet Superadmin"
    BOOTSTRAP_SUPERADMIN_USERNAME: str = "superadmin"
    BOOTSTRAP_SUPERADMIN_PASSWORD: str = ""

    # ── Client HIS integration (B2B partner) ─────────────────────────────
    # Secrets live here (.env, gitignored); singleton-facility integration
    # values (facilityGuid, companyID, queueSetupID) live on ``facility_master``.
    # Empty URL = integration disabled; the punch pusher and pre-reg
    # forwarder no-op until the values are supplied.
    CLIENT_PUNCH_API_URL: str = ""
    CLIENT_PUNCH_API_KEY: str = ""
    CLIENT_PREREG_API_URL: str = ""
    CLIENT_PREREG_API_KEY: str = ""
    CLIENT_API_TIMEOUT: float = 10.0

    # How the API key is attached to outbound requests. Default is a
    # Bearer token (``Authorization: Bearer <key>``). For an
    # ``X-API-Key: <key>`` style, set AUTH_HEADER="X-API-Key" and
    # AUTH_SCHEME="" (empty scheme → the raw key is sent). Applies to
    # both client calls; a per-call key still comes from the two
    # *_API_KEY vars above.
    CLIENT_API_AUTH_HEADER: str = "Authorization"
    CLIENT_API_AUTH_SCHEME: str = "Bearer"

    # ── Export worker (outbound delivery of punches + pre-regs) ──────────
    # A single background thread drains ``punch_export_queue`` and retries
    # failed ``pre_registration_log`` pushes. Only started when at least
    # one client URL is configured.
    EXPORT_POLL_INTERVAL: float = 15.0     # seconds between queue sweeps
    EXPORT_MAX_ATTEMPTS: int = 10          # give up (dead-letter) after this
    EXPORT_BACKOFF_BASE: float = 30.0      # 1st retry delay; doubles each time
    EXPORT_BACKOFF_CAP: float = 3600.0     # max retry delay
    EXPORT_BATCH_SIZE: int = 20            # rows processed per sweep per queue
    EXPORT_STALE_SECONDS: float = 120.0    # reclaim a stuck SENDING row after

    # ── Kiosk ────────────────────────────────────────────────────────────
    # Ignore a repeat punch of the same person + direction within this
    # window (seconds) — a person lingering at the kiosk punches once.
    KIOSK_DUPLICATE_WINDOW: int = 120

    # ── Recognition ──────────────────────────────────────────────────────
    SIMILARITY_THRESHOLD: float = 0.45
    EMBEDDING_DIM: int = 512
    GPU_DEVICE_ID: int = 0

    # ── Auth / session cookies ───────────────────────────────────────────
    # Master toggle. While False (the demo-stable default) the auth routes
    # exist but no route is guarded — the app behaves exactly as before.
    # Flip to True (env AUTH_ENABLED=true) once the login UI ships so the
    # RBAC guards in Phase 2 start enforcing. Keeps `main` demo-safe while
    # the feature lands incrementally.
    AUTH_ENABLED: bool = False

    # Secret used to sign session cookies. MUST be set via the environment
    # in any real deployment. The empty default is only tolerated when
    # DEBUG is on; `auth_service` fails loudly at first use otherwise so a
    # misconfigured prod box can't mint forgeable cookies.
    SESSION_SECRET: str = ""

    # Cookie attributes. `SESSION_COOKIE_SECURE` must be True in production
    # (HTTPS); it's False by default so http://localhost dev works. Lax is
    # the right SameSite for a same-origin SPA that also needs cookies to
    # ride along on top-level navigations.
    SESSION_COOKIE_NAME: str = "iris_session"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"
    SESSION_TTL_HOURS: int = 12

    # Brute-force lockout: after this many consecutive failed logins for a
    # (username, client-ip) pair, further attempts are refused until the
    # window elapses.
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15


settings = Settings()
