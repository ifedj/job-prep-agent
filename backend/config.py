from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os


def _read_env_file(path: str = ".env") -> dict:
    """Parse .env file directly, ignoring blank env vars set in the shell."""
    values = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    return values


# Override empty shell env vars with values from .env file
_env = _read_env_file()
for _k, _v in _env.items():
    if not os.environ.get(_k):  # Only override if shell value is empty/unset
        os.environ[_k] = _v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Anthropic
    anthropic_api_key: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    token_encryption_key: str = ""  # Fernet key; auto-generated if empty

    # Database
    database_url: str = "sqlite:///./job_prep.db"

    # Sync
    sync_interval_minutes: int = 30

    # Classification thresholds
    classification_high_confidence: float = 0.85
    classification_ambiguous_lower: float = 0.65

    # Claude model
    claude_model: str = "claude-sonnet-4-6"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
