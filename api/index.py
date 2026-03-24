"""Vercel entry point for Job Prep Agent."""
import os
import shutil
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

os.environ.setdefault("IS_VERCEL", "1")

# ── Database bootstrap ────────────────────────────────────────────────────────
# If a real DATABASE_URL (PostgreSQL) is set in Vercel env vars, use it.
# Otherwise fall back to a bundled SQLite copy in /tmp for the demo.
_db_url = os.environ.get("DATABASE_URL", "")

if not _db_url or _db_url.startswith("sqlite"):
    # SQLite fallback: copy bundled demo DB to writable /tmp on cold start
    _db_src = os.path.join(_root, "job_prep.db")
    _db_dst = "/tmp/job_prep.db"
    if os.path.exists(_db_src) and not os.path.exists(_db_dst):
        shutil.copy2(_db_src, _db_dst)
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dst}")
else:
    # PostgreSQL: Neon (and similar) use "postgres://" — SQLAlchemy needs "postgresql://"
    if _db_url.startswith("postgres://"):
        os.environ["DATABASE_URL"] = _db_url.replace("postgres://", "postgresql://", 1)

from backend.main import app  # noqa: E402 — must come after env setup
