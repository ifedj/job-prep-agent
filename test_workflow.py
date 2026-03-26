"""
End-to-end workflow test — seeds a fake interview event and runs the full pipeline:
  1. Insert a realistic calendar event directly into the DB
  2. Classify it with Claude
  3. Generate a prep pack
  4. Print the results

Run with: python3 test_workflow.py
The server does NOT need to be running.
"""
import json
import os
import sys
from datetime import datetime, timedelta

# Ensure imports resolve from project root
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import Base, get_engine, SessionLocal
from backend.models import CalendarEvent, EventClassification, PrepPack, User, OAuthToken

# ── Bootstrap DB ──────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=get_engine())
db = SessionLocal()

# ── 1. Find or create a test user ─────────────────────────────────────────────
user = db.query(User).first()
if user is None:
    user = User(
        email="test@example.com",
        name="Test User",
        target_roles=json.dumps(["Software Engineer", "Backend Engineer"]),
        background_summary="5 years of Python, distributed systems, and API design. Previously at a fintech startup building payment infrastructure.",
        key_projects=json.dumps(["payment-api", "real-time-data-pipeline", "open-source-orm-plugin"]),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    print(f"✓ Created test user: {user.email}")
else:
    print(f"✓ Using existing user: {user.email} (id={user.id})")

# ── 2. Seed three test events ──────────────────────────────────────────────────
test_events = [
    {
        "google_event_id": "test-interview-acme-001",
        "title": "Technical Interview – Acme Corp (Final Round)",
        "description": "Final round technical interview with the Acme Corp engineering team. "
                       "Please be prepared to discuss system design and past projects. "
                       "Interviewers: Sarah (EM), David (Senior SWE).",
        "attendees": [
            {"email": "sarah.chen@acmecorp.com", "name": "Sarah Chen", "response_status": "accepted"},
            {"email": "david.park@acmecorp.com", "name": "David Park",  "response_status": "accepted"},
        ],
        "organizer_email": "recruiting@acmecorp.com",
        "days_ahead": 2,
    },
    {
        "google_event_id": "test-networking-stripe-001",
        "title": "Coffee chat with Maria – Stripe",
        "description": "Informational chat about engineering culture and open roles at Stripe. "
                       "Maria is a Staff Engineer on the payments team.",
        "attendees": [
            {"email": "maria.g@stripe.com", "name": "Maria Gonzalez", "response_status": "accepted"},
        ],
        "organizer_email": "maria.g@stripe.com",
        "days_ahead": 4,
    },
    {
        "google_event_id": "test-recruiter-bigtech-001",
        "title": "Recruiter Screen – BigTech Inc",
        "description": "Initial phone screen with recruiting coordinator about Software Engineer openings.",
        "attendees": [
            {"email": "recruiter@bigtech.com", "name": "Alex Recruiter", "response_status": "accepted"},
        ],
        "organizer_email": "recruiter@bigtech.com",
        "days_ahead": 6,
    },
]

seeded_events = []
for ev in test_events:
    existing = db.query(CalendarEvent).filter(
        CalendarEvent.google_event_id == ev["google_event_id"]
    ).first()
    if existing:
        print(f"  (already exists) {ev['title']}")
        seeded_events.append(existing)
        continue

    start = datetime.utcnow() + timedelta(days=ev["days_ahead"])
    event = CalendarEvent(
        user_id=user.id,
        google_event_id=ev["google_event_id"],
        calendar_id="primary",
        title=ev["title"],
        description=ev["description"],
        start_time=start,
        end_time=start + timedelta(hours=1),
        attendees=json.dumps(ev["attendees"]),
        organizer_email=ev["organizer_email"],
        synced_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    seeded_events.append(event)
    print(f"✓ Seeded event: {event.title}")

# ── 3. Classify each event ────────────────────────────────────────────────────
print("\n── Classifying events ──────────────────────────────────────────────────")
from backend.services.classifier import classify_event

target_roles = json.loads(user.target_roles) if user.target_roles else []

for event in seeded_events:
    # Skip if already classified
    if event.classification:
        clf = event.classification
        print(f"  (cached) {event.title[:50]}")
        print(f"    → {clf.label} ({clf.confidence:.0%}) — {clf.reasoning}")
        continue
    try:
        clf = classify_event(event, target_roles, db)
        print(f"✓ {event.title[:50]}")
        print(f"    → {clf.label} ({clf.confidence:.0%})")
        print(f"    Company: {clf.company_name or 'unknown'}  |  Role: {clf.role_title or 'unknown'}")
        print(f"    Reasoning: {clf.reasoning}")
    except Exception as e:
        print(f"✗ Classification failed for '{event.title}': {e}")

# ── 4. Generate prep packs ────────────────────────────────────────────────────
print("\n── Generating prep packs ───────────────────────────────────────────────")
from backend.services.prep_generator import generate_prep_pack
from backend.services.classifier import is_job_related_label

db.expire_all()  # Refresh relationships

for event in seeded_events:
    db.refresh(event)
    clf = event.classification
    if clf is None:
        print(f"  (no classification) {event.title[:50]}")
        continue

    effective = clf.user_override or clf.label
    if not is_job_related_label(effective):
        print(f"  (skipping — {effective}) {event.title[:50]}")
        continue

    if event.prep_pack and event.prep_pack.generation_status == "done":
        print(f"  (cached) {event.title[:50]}")
        continue

    try:
        pack = generate_prep_pack(event, clf, user, db)
        print(f"✓ {event.title[:50]}")
    except Exception as e:
        import traceback
        print(f"✗ Generation failed for '{event.title}':")
        traceback.print_exc()

# ── 5. Print results ──────────────────────────────────────────────────────────
print("\n\n══════════════════════════════════════════════════════════════════════")
print("  PREP PACKS GENERATED")
print("══════════════════════════════════════════════════════════════════════\n")

db.expire_all()
for event in seeded_events:
    db.refresh(event)
    clf = event.classification
    pack = event.prep_pack

    label = (clf.user_override or clf.label) if clf else "unclassified"
    confidence = f"{clf.confidence:.0%}" if clf else "—"
    company = clf.company_name if clf else "—"

    print(f"📅  {event.title}")
    print(f"    Label: {label}  |  Confidence: {confidence}  |  Company: {company}")

    if pack and pack.generation_status == "done":
        print(f"\n    SUMMARY")
        print(f"    {pack.meeting_summary}")

        tps = json.loads(pack.talking_points or "[]")
        if tps:
            print(f"\n    TALKING POINTS")
            for tp in tps:
                print(f"    • {tp}")

        qs = json.loads(pack.expected_questions or "[]")
        if qs:
            print(f"\n    QUESTIONS TO EXPECT")
            for q in qs[:3]:  # Print first 3
                print(f"    Q: {q.get('question', q)}")

        checklist = json.loads(pack.prep_checklist or "[]")
        if checklist:
            print(f"\n    30-MIN CHECKLIST")
            for item in checklist:
                label_text = item["item"] if isinstance(item, dict) else item
                print(f"    ☐ {label_text}")
    elif pack:
        print(f"    ⚠ Prep pack status: {pack.generation_status}")
        if pack.generation_error:
            print(f"    Error: {pack.generation_error}")
    else:
        print(f"    (no prep pack generated)")

    print()

db.close()
print("══════════════════════════════════════════════════════════════════════")
print("Done. Open http://localhost:8080 to see these events in the dashboard.")
print("If the server is running, click Sync Now — or just refresh the page.")
