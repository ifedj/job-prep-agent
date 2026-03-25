import ssl

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from backend.config import get_settings

settings = get_settings()

_url = settings.database_url
_is_sqlite = "sqlite" in _url

if _is_sqlite:
    engine = create_engine(
        _url,
        connect_args={"check_same_thread": False},
    )
else:
    # pg8000 requires ssl_context for Supabase / any managed PostgreSQL
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
    engine = create_engine(
        _url,
        connect_args={"ssl_context": _ssl_ctx},
        pool_pre_ping=True,
        pool_size=2,        # keep small for serverless — each instance is short-lived
        max_overflow=2,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
