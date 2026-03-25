"""FastAPI application factory."""
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import Base, SessionLocal, engine
from backend.models import CalendarEvent, EventClassification, PrepPack, User
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


def _seed_demo_data(db, user):
    """Seed 3 demo events + prep packs if none exist yet."""
    if db.query(CalendarEvent).filter(CalendarEvent.user_id == user.id).count() > 0:
        return  # Already seeded

    now = datetime.utcnow()
    demo_events = [
        {
            "google_event_id": "demo-event-001",
            "title": "Technical Interview – Acme Corp (Final Round)",
            "description": "Final round with the engineering team at Acme Corp.",
            "start_time": now + timedelta(days=2),
            "end_time": now + timedelta(days=2, hours=1),
            "attendees": json.dumps([{"email": "recruiter@acme.com", "name": "Alice Smith", "response_status": "accepted"}]),
            "organizer_email": "recruiter@acme.com",
            "classification": "interview",
            "confidence": 0.98,
            "company_name": "Acme Corp",
            "role_title": "Software Engineer",
            "summary": "This is a final-round technical interview at Acme Corp for a Software Engineer role. Expect system design and coding questions.",
            "talking_points": ["Led backend migration reducing latency by 40%", "Shipped payment platform used by 2M users", "Open-source contributor with 500+ GitHub stars"],
            "expected_questions": [
                {"question": "Walk me through a system you designed from scratch.", "suggested_answer": "Use the payment-api project — describe the architecture, tradeoffs, and what you'd change now."},
                {"question": "How do you handle disagreements with senior engineers?", "suggested_answer": "Focus on data-driven discussion and escalating when needed, with a specific example."},
                {"question": "Tell me about a time you improved system performance.", "suggested_answer": "The backend migration — quantify the 40% latency improvement and describe your debugging approach."},
            ],
            "questions_to_ask": ["What does the on-call rotation look like?", "How is engineering impact measured at Acme?", "What are the biggest technical challenges the team faces?"],
            "checklist": ["Re-read the job description", "Review your resume top-to-bottom", "Research Acme Corp products", "Prepare 3 STAR stories", "Test audio/video setup", "Block 30 min before the call", "Have water ready", "Close distracting tabs"],
            "caveats": ["Role inferred from title — confirm scope before the call"],
        },
        {
            "google_event_id": "demo-event-002",
            "title": "Coffee chat with Maria – Stripe",
            "description": "Informational chat with a Staff Engineer at Stripe.",
            "start_time": now + timedelta(days=4),
            "end_time": now + timedelta(days=4, hours=1),
            "attendees": json.dumps([{"email": "maria@stripe.com", "name": "Maria Chen", "response_status": "accepted"}]),
            "organizer_email": "maria@stripe.com",
            "classification": "networking",
            "confidence": 0.92,
            "company_name": "Stripe",
            "role_title": "Staff Engineer",
            "summary": "Informal networking coffee chat with Maria Chen, a Staff Engineer at Stripe. Great opportunity to learn about Stripe's engineering culture.",
            "talking_points": ["Your interest in fintech and payment infrastructure", "The ML pipeline project and its scale", "What you're looking for in your next role"],
            "expected_questions": [
                {"question": "What brings you to fintech?", "suggested_answer": "Connect your payment-api experience to genuine interest in the space."},
                {"question": "What are you working on now?", "suggested_answer": "Lead with the ML pipeline — concrete numbers make it memorable."},
            ],
            "questions_to_ask": ["What does growth look like for engineers at Stripe?", "What's the best part of working there?", "What problems is the team focused on this year?"],
            "checklist": ["Read Maria's LinkedIn profile", "Review Stripe's engineering blog", "Prepare your 30-second intro", "Have 3 thoughtful questions ready", "Test your connection", "Be 2 minutes early", "Take notes during the call", "Send a follow-up within 24h"],
            "caveats": ["This is a networking call — keep it conversational, not an interview"],
        },
        {
            "google_event_id": "demo-event-003",
            "title": "Recruiter Screen – BigTech Inc",
            "description": "Initial phone screen with the recruiting team at BigTech Inc.",
            "start_time": now + timedelta(days=6),
            "end_time": now + timedelta(days=6, hours=1),
            "attendees": json.dumps([{"email": "recruiter@bigtech.com", "name": "James Wu", "response_status": "accepted"}]),
            "organizer_email": "recruiter@bigtech.com",
            "classification": "recruiter_screen",
            "confidence": 0.97,
            "company_name": "BigTech Inc",
            "role_title": "Software Engineer",
            "summary": "First-touch recruiter screen with BigTech Inc. They will assess basic fit, compensation expectations, and timeline.",
            "talking_points": ["Your 5 years of Python and distributed systems experience", "Why you're interested in BigTech Inc specifically", "Compensation range and start date flexibility"],
            "expected_questions": [
                {"question": "Tell me about yourself.", "suggested_answer": "2-minute pitch: background → key projects → why BigTech now."},
                {"question": "What are your compensation expectations?", "suggested_answer": "Give a range based on market research, leave room to negotiate."},
                {"question": "Why are you looking to leave your current role?", "suggested_answer": "Keep it positive — focus on growth and new challenges."},
            ],
            "questions_to_ask": ["What does the interview process look like from here?", "What's the team I'd be joining focused on?", "What's the timeline you're working to?"],
            "checklist": ["Research BigTech Inc recent news", "Know your target salary range", "Prepare your elevator pitch", "Have your resume in front of you", "Find a quiet room", "Charge your phone", "Note the recruiter's name", "Prepare questions about next steps"],
            "caveats": ["Keep answers concise — this is a screen, not a deep-dive"],
        },
    ]

    for d in demo_events:
        event = CalendarEvent(
            user_id=user.id,
            google_event_id=d["google_event_id"],
            calendar_id="primary",
            title=d["title"],
            description=d["description"],
            start_time=d["start_time"],
            end_time=d["end_time"],
            attendees=d["attendees"],
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

        pack = PrepPack(
            event_id=event.id,
            user_id=user.id,
            generation_status="done",
            meeting_summary=d["summary"],
            talking_points=json.dumps(d["talking_points"]),
            expected_questions=json.dumps(d["expected_questions"]),
            questions_to_ask=json.dumps(d["questions_to_ask"]),
            prep_checklist=json.dumps([{"item": i, "done": False} for i in d["checklist"]]),
            caveats=json.dumps(d["caveats"]),
            content_hash=f"demo-{d['google_event_id']}",
            model_version="demo",
            generated_at=datetime.utcnow(),
        )
        db.add(pack)

    db.commit()


@app.get("/demo", include_in_schema=False)
def serve_demo():
    """Serve the app pre-authenticated as the demo user, seeding data if needed."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "demo@jobprepagent.com").first()
        if user is None:
            user = User(
                email="demo@jobprepagent.com",
                name="Ife Dare-Johnson",
                target_roles=json.dumps(["Product Manager", "Senior PM"]),
                background_summary="Product leader with 6 years experience across fintech and SaaS.",
                key_projects=json.dumps(["Payment platform", "ML pipeline", "Open-source SDK"]),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        _seed_demo_data(db, user)
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
