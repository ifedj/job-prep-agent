"""FastAPI application factory."""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from backend.database import Base, SessionLocal, create_tables, get_db, get_engine
from backend.models import CalendarEvent, EventClassification, PrepPack, User
from backend.routers import auth, events, prep_packs, profile, review, sync
from backend.security import create_access_token
from backend.services.scheduler import start_scheduler, stop_scheduler


_is_serverless = bool(os.environ.get("IS_VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly if a known-insecure default secret key is used in production
    if _is_serverless:
        from backend.config import get_settings as _gs
        if _gs().secret_key == "dev-secret-key-change-in-production":
            raise RuntimeError(
                "SECRET_KEY is set to the insecure default. "
                "Set a random SECRET_KEY in your Vercel environment variables before deploying."
            )
    # Create all database tables — wrapped so a slow cold-start DB connection
    # doesn't crash the entire function before it can serve any request
    try:
        create_tables()
    except Exception as exc:
        print(f"[startup] DB schema creation failed (will retry on first request): {exc}")
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


# Surface tracebacks on Vercel only when DEBUG=true — never expose in production by default
if _is_serverless:
    import traceback as _tb
    from starlette.requests import Request as _Req
    from starlette.responses import JSONResponse as _JR

    @app.exception_handler(Exception)
    async def _debug_exc(_req: _Req, exc: Exception):
        from backend.config import get_settings as _gs
        if _gs().debug:
            return _JR(status_code=500, content={"detail": str(exc), "traceback": _tb.format_exc()})
        return _JR(status_code=500, content={"detail": str(exc)})

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


@app.get("/dashboard", include_in_schema=False)
def serve_dashboard():
    """Serve the app at /dashboard — used after Google OAuth callback."""
    return FileResponse("frontend/index.html")


_DEMO_EVENTS = [
    {
        "google_event_id": "demo-event-001",
        "title": "Technical Interview – Acme Corp (Final Round)",
        "description": "Final round interview with the product team at Acme Corp for a Senior Product Manager role.",
        "attendees": [{"email": "recruiter@acme.com", "name": "Alice Smith", "response_status": "accepted"}],
        "organizer_email": "recruiter@acme.com",
        "classification": "interview",
        "confidence": 0.98,
        "day_offset": 2,
        "company_name": "Acme Corp",
        "role_title": "Senior Product Manager",
    },
    {
        "google_event_id": "demo-event-002",
        "title": "Coffee chat with Maria – Stripe",
        "description": "Informational chat with a Product Lead at Stripe about the platform PM team.",
        "attendees": [{"email": "maria@stripe.com", "name": "Maria Chen", "response_status": "accepted"}],
        "organizer_email": "maria@stripe.com",
        "classification": "networking",
        "confidence": 0.92,
        "day_offset": 4,
        "company_name": "Stripe",
        "role_title": "Product Manager",
    },
    {
        "google_event_id": "demo-event-003",
        "title": "Recruiter Screen – BigTech Inc",
        "description": "Initial phone screen with the recruiting team at BigTech Inc. Role listed as Software Engineer but candidate is targeting PM roles.",
        "attendees": [{"email": "recruiter@bigtech.com", "name": "James Wu", "response_status": "accepted"}],
        "organizer_email": "recruiter@bigtech.com",
        "classification": "recruiter_screen",
        "confidence": 0.97,
        "day_offset": 6,
        "company_name": "BigTech Inc",
        "role_title": "Software Engineer",
    },
]

# Demo resume text — used as resume_raw_text for the demo user so Claude API generates
# real, personalized prep packs (same pipeline as real users).
_DEMO_RESUME_TEXT = """IFEOLUWA DARE-JOHNSON
Product Leader | AI/ML & Health Infrastructure | MIT Sloan MBA Candidate

CONTACT
Email: ife.dj@example.com | LinkedIn: linkedin.com/in/ifedj | Location: Cambridge, MA

SUMMARY
Product leader with 10+ years at the intersection of health data and AI. Co-founded HealthTracka,
a consumer diagnostics platform that grew from zero to 50,000 users across Nigeria. Designed and
built Lola AI, a retrieval-first RAG system achieving 95% accuracy on menstrual cycle prediction
for Nigerian women — a population with zero pre-existing ML models for their health data.
Currently at MIT Sloan pursuing an MBA to bridge emerging-market health innovation with US
enterprise infrastructure, regulatory frameworks, and distribution at scale. Targeting Product
Manager roles in agentic health infrastructure and AI-native platform teams.

EDUCATION
MIT Sloan School of Management — MBA Candidate (Expected 2026)
  Concentrations: AI/ML Strategy, Healthcare Innovation, Product Management
  Activities: MIT Product Club (VP), MIT Africa Business Club, MIT AI Conference organizer

University of Lagos — B.Sc. Computer Science (First Class Honours, 2015)
  Thesis: Predictive modeling for maternal health outcomes using mobile-collected data

EXPERIENCE
Co-Founder & Chief Product Officer — HealthTracka (2021–Present)
  Consumer health diagnostics platform serving 50,000+ users across Nigeria.
  - Grew the platform from zero infrastructure to 50,000 active users in under 2 years
  - Led product strategy, acquisition, activation, and retention — owned the full growth funnel
  - Made core technical decisions: API-first architecture, data pipeline design, ML integration
  - Built and managed a cross-functional team of 12 (engineers, data scientists, clinical advisors)
  - Designed the payments and diagnostics workflow processing 100,000+ lab test orders
  - Navigated Nigerian health data regulations and clinical lab partnerships
  - Raised pre-seed and seed funding from health-focused investors

AI/ML Product Lead — HealthTracka / Lola AI (2023–Present)
  Built Lola AI, a retrieval-first RAG system for women's health predictions.
  - Architected retrieval-first RAG system trained on proprietary Nigerian women's lab data
  - Achieved 95% accuracy on menstrual cycle prediction — first ML model for this population
  - Made key architecture decisions: chose retrieval over fine-tuning for data scarcity,
    interpretability, and update flexibility
  - Built the entire data pipeline from scratch: collection, validation, labeling, and inference
  - Designed the system to serve real users in production, not just a research prototype
  - Managed tradeoffs between accuracy and latency in the retrieval pipeline

Senior Product Manager — Andela (2018–2021)
  Technical talent marketplace connecting African developers with global companies.
  - Owned the developer matching and vetting product, serving 100+ enterprise clients
  - Led the migration from manual matching to ML-powered talent recommendations
  - Reduced time-to-match from 14 days to 3 days through product and process redesign
  - Managed a cross-functional team across Lagos, Nairobi, and New York
  - Shipped API integrations with enterprise HR systems (Workday, Greenhouse, Lever)

Product Manager — Interswitch Group (2015–2018)
  Leading African integrated payments and digital commerce company.
  - Managed payment gateway products processing $4B+ in annual transaction volume
  - Led the launch of a developer API portal, growing from 0 to 2,000+ active developers
  - Owned the merchant onboarding product, reducing activation time by 60%
  - Collaborated with compliance teams on PCI-DSS certification and CBN regulatory requirements

SKILLS
Product: Product strategy, roadmapping, user research, A/B testing, growth metrics, regulatory
  compliance, clinical data governance, cross-functional leadership, stakeholder management
Technical: Python, FastAPI, RAG architectures, vector databases, LLM integration, API design,
  data pipeline architecture, SQL, system design, technical architecture decisions
Domain: Health data, AI/ML in healthcare, consumer diagnostics, fintech, payment infrastructure,
  emerging market product development, clinical lab partnerships

PROJECTS
Lola AI — Retrieval-first RAG system for women's health predictions (95% accuracy)
HealthTracka Platform — Consumer diagnostics platform (50K users, 100K+ lab orders)
Andela ML Matching — ML-powered developer-to-company matching system
Interswitch Developer Portal — Payment API platform (2,000+ developers)

AWARDS & RECOGNITION
MIT Sloan Healthcare Innovation Prize — Finalist (2025)
Andela Product Excellence Award (2020)
Lagos Angel Network — Top 10 Health Startups (2022)
"""


def _seed_demo_data(db, user):
    """Seed 3 demo calendar events + classifications if none exist yet.

    Prep packs are NOT seeded — they are generated via the real Claude API pipeline
    in a background task (see _generate_demo_packs). This means the demo uses the
    same generation path as real users, producing genuinely personalized content.
    """
    if db.query(CalendarEvent).filter(CalendarEvent.user_id == user.id).count() > 0:
        return False  # Already seeded

    now = datetime.utcnow()

    for d in _DEMO_EVENTS:
        days = d["day_offset"]
        event = CalendarEvent(
            user_id=user.id,
            google_event_id=d["google_event_id"],
            calendar_id="primary",
            title=d["title"],
            description=d["description"],
            start_time=now + timedelta(days=days),
            end_time=now + timedelta(days=days, hours=1),
            attendees=json.dumps(d["attendees"]),
            organizer_email=d["organizer_email"],
            raw_json=json.dumps({"summary": d["title"]}),
        )
        db.add(event)
        db.flush()

        clf = EventClassification(
            event_id=event.id,
            label=d["classification"],
            confidence=d["confidence"],
            reasoning="Pre-seeded demo classification",
            company_name=d["company_name"],
            role_title=d["role_title"],
            model_version="demo",
        )
        db.add(clf)

    db.commit()
    return True  # Newly seeded — needs generation


def _generate_demo_packs(user_id: int):
    """Background task: generate real prep packs for demo events via Claude API."""
    from backend.services.prep_generator import generate_pending_packs

    db = SessionLocal()
    try:
        generate_pending_packs(user_id, db)
    except Exception as e:
        print(f"[demo] Background generation failed: {e}")
    finally:
        db.close()


@app.get("/demo", include_in_schema=False)
def serve_demo(background_tasks: BackgroundTasks):
    """Serve the app pre-authenticated as the demo user, seeding data if needed."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "demo@jobprepagent.com").first()
        if user is None:
            user = User(
                email="demo@jobprepagent.com",
                name="Ife Dare-Johnson",
                target_roles=json.dumps(["Product Manager", "Senior PM"]),
                background_summary=(
                    "Product leader with 10+ years at the intersection of health data and AI. "
                    "Co-founded HealthTracka (0 to 50K users), built Lola AI (retrieval-first RAG, "
                    "95% accuracy on Nigerian women's health data). MIT Sloan MBA candidate "
                    "targeting PM roles in agentic health infrastructure."
                ),
                key_projects=json.dumps([
                    "Lola AI — retrieval-first RAG system, 95% accuracy",
                    "HealthTracka — consumer diagnostics platform, 50K users",
                    "Andela ML Matching — ML-powered talent recommendations",
                ]),
                resume_raw_text=_DEMO_RESUME_TEXT,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            # ALWAYS ensure demo user has the latest resume + profile
            # (fixes stale demo users who had old/empty resume_raw_text)
            user.resume_raw_text = _DEMO_RESUME_TEXT
            user.background_summary = (
                "Product leader with 10+ years at the intersection of health data and AI. "
                "Co-founded HealthTracka (0 to 50K users), built Lola AI (retrieval-first RAG, "
                "95% accuracy on Nigerian women's health data). MIT Sloan MBA candidate "
                "targeting PM roles in agentic health infrastructure."
            )
            user.key_projects = json.dumps([
                "Lola AI — retrieval-first RAG system, 95% accuracy",
                "HealthTracka — consumer diagnostics platform, 50K users",
                "Andela ML Matching — ML-powered talent recommendations",
            ])
            db.commit()

        print(f"[demo] User {user.id} resume_raw_text length: {len(user.resume_raw_text or '')} chars")
        print(f"[demo] resume_raw_text starts with: {(user.resume_raw_text or '')[:120]!r}")

        newly_seeded = _seed_demo_data(db, user)

        # Reset prep packs that are stale: failed, or "done" but older than 6 hours,
        # or generated with an outdated model. Returning visitors within 6 hours see
        # their existing packs — no unnecessary API calls.
        from datetime import timedelta
        from backend.config import get_settings as _gs
        _cutoff = datetime.utcnow() - timedelta(hours=6)
        _current_model = _gs().claude_model
        all_packs = db.query(PrepPack).filter(PrepPack.user_id == user.id).all()
        reset_count = 0
        for pack in all_packs:
            is_stale = (
                pack.generation_status == "failed"
                or (
                    pack.generation_status == "done"
                    and (
                        pack.generated_at is None
                        or pack.generated_at < _cutoff
                        or pack.model_version != _current_model
                    )
                )
            )
            if is_stale:
                pack.generation_status = "pending"
                pack.meeting_summary = None
                pack.talking_points = None
                pack.expected_questions = None
                pack.questions_to_ask = None
                pack.prep_checklist = None
                pack.caveats = None
                pack.content_hash = None
                pack.generated_at = None
                reset_count += 1
        if reset_count:
            db.commit()
            print(f"[demo] Reset {reset_count} stale prep packs to 'pending' for regeneration")

        token = create_access_token(user.id)
        user_id = user.id
    finally:
        db.close()

    # Trigger real Claude API generation in background.
    # On first visit: generates all 3 packs. On subsequent visits: retries any failed packs.
    # generate_pending_packs() is idempotent — skips events with status "done" or "generating".
    background_tasks.add_task(_generate_demo_packs, user_id)

    response = FileResponse("frontend/index.html")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


def _run_full_sync(user_id: int):
    """Background task: run the full sync → classify → generate → email pipeline."""
    from backend.services.scheduler import run_sync_for_user

    db = SessionLocal()
    try:
        run_sync_for_user(user_id, db)
    except Exception as e:
        print(f"[onboarding] Background sync failed: {e}")
    finally:
        db.close()


@app.post("/api/onboarding/complete", include_in_schema=False)
def onboarding_complete(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Called after a user completes onboarding.

    For real users with Google connected: triggers the full pipeline
    (sync calendar → classify events → generate prep packs → send emails)
    in the background so packs are ready by the time the dashboard loads.

    For demo users: seeds demo events and triggers real Claude API generation.
    """
    from typing import Optional as _Opt
    from backend.security import decode_access_token
    from backend.models import OAuthToken

    session_token: _Opt[str] = request.cookies.get("session_token")
    if not session_token:
        return {"status": "unauthenticated"}

    user_id = decode_access_token(session_token)
    if not user_id:
        return {"status": "unauthenticated"}

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"status": "user_not_found"}

    # Auto-trigger the full pipeline if Google is connected.
    # This runs sync → classify → generate_pending_packs → send emails in background.
    # For users who just completed OAuth, this means their calendar events are synced
    # and prep packs start generating immediately — no manual "Sync Now" needed.
    has_google = db.query(OAuthToken).filter(
        OAuthToken.user_id == user_id,
        OAuthToken.provider == "google",
    ).first() is not None

    if has_google:
        # Full pipeline: sync → classify → generate → email (email dedup handled inside)
        background_tasks.add_task(_run_full_sync, user_id)
    else:
        # No Google connected — trigger generation for any seeded demo events
        background_tasks.add_task(_generate_demo_packs, user_id)

    return {"status": "ok"}


@app.get("/health")
def health():
    try:
        create_tables()
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.close()
        return {"status": "ok", "db": "connected"}
    except Exception as exc:
        return {"status": "degraded", "db": str(exc)}
