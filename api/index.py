"""Vercel entry point for Job Prep Agent."""
import os
import shutil
import ssl
import sys
import traceback

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

os.environ.setdefault("IS_VERCEL", "1")

# ── Database bootstrap ────────────────────────────────────────────────────────
_db_url = os.environ.get("DATABASE_URL", "").strip()

if not _db_url or "sqlite" in _db_url:
    _db_src = os.path.join(_root, "job_prep.db")
    _db_dst = "/tmp/job_prep.db"
    if os.path.exists(_db_src) and not os.path.exists(_db_dst):
        shutil.copy2(_db_src, _db_dst)
    os.environ["DATABASE_URL"] = f"sqlite:///{_db_dst}"
else:
    # Normalise to pg8000 scheme (pure-Python driver, no binaries needed)
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+pg8000://", 1)
    elif _db_url.startswith("postgresql://") and "+pg8000" not in _db_url:
        _db_url = _db_url.replace("postgresql://", "postgresql+pg8000://", 1)
    os.environ["DATABASE_URL"] = _db_url

# ── Import app — catch ALL errors so we can return a readable diagnostic ─────
_import_error = None
try:
    from backend.main import app  # noqa: E402
except Exception:
    _import_error = traceback.format_exc()
    print("STARTUP ERROR:\n", _import_error)

    # Fallback micro-app that shows the real error instead of a blank 500
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()

    @app.get("/{path:path}")
    def _crash(path: str = ""):
        return PlainTextResponse(
            f"App failed to start. Startup traceback:\n\n{_import_error}",
            status_code=500,
        )
