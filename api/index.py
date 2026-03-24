"""Vercel entry point for Job Prep Agent."""
import os
import shutil
import sys

# ── Bootstrap: copy bundled demo DB to writable /tmp on cold start ────────────
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_src = os.path.join(_root, "job_prep.db")
_db_dst = "/tmp/job_prep.db"

if os.path.exists(_db_src) and not os.path.exists(_db_dst):
    shutil.copy2(_db_src, _db_dst)

# Point to writable copy
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dst}")
os.environ.setdefault("IS_VERCEL", "1")

sys.path.insert(0, _root)

from backend.main import app  # noqa: E402 — must come after env setup
