"""FastAPI application factory."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import Base, SessionLocal, engine
from backend.models import User
from backend.routers import auth, events, prep_packs, profile, review, sync
from backend.security import create_access_token
from backend.services.scheduler import start_scheduler, stop_scheduler


_is_serverless = bool(os.environ.get("IS_VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all database tables
    Base.metadata.create_all(bind=engine)
    # Scheduler doesn't run in serverless environments
    if not _is_serverless:
        start_scheduler()
    yield
    if not _is_serverless:
        stop_scheduler()


app = FastAPI(
    title="Job Prep Agent",
    description="Automatically prepares you for job-related meetings",
    version="1.0.0",
    lifespan=lifespan,
)

_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8080",
]
# Allow any Vercel deployment URL automatically
_vercel_url = os.environ.get("VERCEL_URL")
if _vercel_url:
    _CORS_ORIGINS.append(f"https://{_vercel_url}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(prep_packs.router, prefix="/api/prep-packs", tags=["prep-packs"])
app.include_router(review.router, prefix="/api/review", tags=["review"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse("frontend/index.html")


@app.get("/demo", include_in_schema=False)
def serve_demo():
    """Serve the app pre-authenticated as the demo user."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        if user is None:
            user = User(email="demo@jobprepagent.com", name="Demo User")
            db.add(user)
            db.commit()
            db.refresh(user)
        token = create_access_token(user.id)
    finally:
        db.close()

    response = FileResponse("frontend/index.html")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.get("/health")
def health():
    return {"status": "ok"}
