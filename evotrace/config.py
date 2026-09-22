"""Configuration module for NoSQL-EvoTrace.

Centralizes environment variables, collection names, and default operational parameters.
"""

from __future__ import annotations
import logging
import os
from pathlib import Path
import secrets
from typing import Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env if present in root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ConfigMeta(type):
    """Metaclass allowing dynamic resolution of class properties such as SECRET_KEY."""

    @property
    def SECRET_KEY(cls) -> str:
        return cls.get_secret_key()


class Config(metaclass=ConfigMeta):
    """Central configuration class."""

    _generated_dev_secret: Optional[str] = None

    # Server settings
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "5000"))
    DEBUG: bool = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")

    # MongoDB settings (MongoDB Community Server is the primary default backend)
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "nosql_evotrace")
    MONGO_CONNECT_TIMEOUT_MS: int = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "2500"))
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "2500"))

    # Fallback / Mock mode: strictly disabled by default.
    # The application connects directly to MongoDB Community Server and displays a clear error
    # instead of silently falling back to mongomock when the server is unavailable.
    ALLOW_MOCK_FALLBACK: bool = os.getenv("ALLOW_MOCK_FALLBACK", "false").lower() in ("1", "true", "yes")
    FORCE_MOCK: bool = os.getenv("EVOTRACE_FORCE_MOCK", "false").lower() in ("1", "true", "yes")

    # Privacy / Redaction settings
    REDACT_SENSITIVE_PROVENANCE: bool = os.getenv("REDACT_SENSITIVE_PROVENANCE", "false").lower() in ("1", "true", "yes")

    # Centralized collection names
    COLLECTION_SNAPSHOTS: str = "evotrace_schema_snapshots"
    COLLECTION_OPERATIONS: str = "evotrace_evolution_operations"
    COLLECTION_PROVENANCE: str = "evotrace_provenance"
    COLLECTION_QUERIES: str = "evotrace_registered_queries"
    COLLECTION_EXPERIMENTS: str = "evotrace_experiment_results"

    # Demonstration dataset collection
    DEFAULT_DEMO_COLLECTION: str = "demo_customers"

    @classmethod
    def get_secret_key(
        cls,
        env_mode: Optional[str] = None,
        raw_secret: Optional[str] = None,
    ) -> str:
        """Resolve the Flask secret key safely based on deployment mode.

        In production mode (FLASK_ENV=production):
            Requires an explicitly configured, non-empty secret key. Fails clearly if missing.
        In development / non-production mode:
            Uses the configured secret if provided. If not provided, generates a cryptographically
            secure random secret and warns the user that sessions will reset across restarts.
        """
        env = (env_mode if env_mode is not None else os.getenv("FLASK_ENV", "development")).strip().lower()
        secret = (raw_secret if raw_secret is not None else os.getenv("SECRET_KEY", "")).strip()

        if env == "production":
            if not secret:
                raise ValueError(
                    "SECRET_KEY must be explicitly configured and non-empty in production mode "
                    "(FLASK_ENV=production). Refusing to start with an unconfigured or empty secret key."
                )
            return secret

        # Development / non-production mode
        if secret:
            return secret

        if cls._generated_dev_secret is None:
            cls._generated_dev_secret = secrets.token_hex(32)
            logger.warning(
                "No SECRET_KEY configured in %s mode. Generated a cryptographically secure temporary "
                "secret key. Note: User sessions will reset across application restarts.",
                env,
            )
        return cls._generated_dev_secret

    @classmethod
    def reset_generated_dev_secret(cls) -> None:
        """Reset the cached in-memory development secret (useful for test isolation)."""
        cls._generated_dev_secret = None

    @classmethod
    def get_system_collections(cls) -> set[str]:
        """Return the set of metadata collections reserved for EvoTrace internals."""
        return {
            cls.COLLECTION_SNAPSHOTS,
            cls.COLLECTION_OPERATIONS,
            cls.COLLECTION_PROVENANCE,
            cls.COLLECTION_QUERIES,
            cls.COLLECTION_EXPERIMENTS,
        }
