"""Vercel entry point for Job Prep Agent."""
import os
import shutil
import ssl
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

os.environ.setdefault("IS_VERCEL", "1")

# ── Database bootstrap ────────────────────────────────────────────────────────
_db_url = os.environ.get("DATABASE_URL", "")

if not _db_url or "sqlite" in _db_url:
    # SQLite fallback: copy bundled demo DB to writable /tmp on cold start
    _db_src = os.path.join(_root, "job_prep.db")
    _db_dst = "/tmp/job_prep.db"
    if os.path.exists(_db_src) and not os.path.exists(_db_dst):
        shutil.copy2(_db_src, _db_dst)
    os.environ["DATABASE_URL"] = f"sqlite:///{_db_dst}"
else:
    # PostgreSQL: normalise scheme and force pg8000 driver (pure-Python, no binaries)
    _db_url = _db_url.strip()
    # Supabase / Heroku ship "postgres://", SQLAlchemy needs "postgresql+pg8000://"
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+pg8000://", 1)
    elif _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+pg8000://", 1)
    # Already has +pg8000 — leave as-is
    os.environ["DATABASE_URL"] = _db_url

from backend.main import app  # noqa: E402 — must come after env setup
