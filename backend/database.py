import ssl
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from backend.config import get_settings

Base = declarative_base()

# ── Lazy engine: created on first call, not at import time ────────────────────
# This ensures a bad DATABASE_URL never crashes the app on startup.
@lru_cache(maxsize=1)
def _get_engine():
    settings = get_settings()
    url = settings.database_url

    if "sqlite" in url:
        return create_engine(url, connect_args={"check_same_thread": False})

    # PostgreSQL via pg8000 (pure-Python driver — works on all serverless runtimes)
    # NullPool is correct for serverless: no persistent connections between requests
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

    return create_engine(
        url,
        connect_args={"ssl_context": _ssl_ctx},
        poolclass=NullPool,
    )


def get_engine():
    return _get_engine()


# Proxy objects that resolve to the real engine on first use
class _LazySessionLocal:
    """sessionmaker proxy — creates the real session factory on first call."""
    _factory = None

    def __call__(self):
        if self._factory is None:
            self._factory = sessionmaker(
                autocommit=False, autoflush=False, bind=_get_engine()
            )
        return self._factory()


SessionLocal = _LazySessionLocal()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables. Called explicitly on startup and health check."""
    engine = _get_engine()
    Base.metadata.create_all(bind=engine)
