"""Generate tailored prep packs for job-related meetings using Claude."""
import hashlib
import json
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import CalendarEvent, EventClassification, PrepPack, User, SyncLog

settings = get_settings()

PREP_TOOL = {
    "name": "generate_prep_pack",
    "description": "Generate a meeting preparation pack for a job seeker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "meeting_summary": {
                "type": "string",
                "description": "2-3 sentence briefing on what this meeting is about and who it's with",
            },
            "talking_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "4-6 specific, concrete talking points the candidate should raise",
            },
            "expected_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "suggested_answer": {"type": "string"},
                    },
                    "required": ["question", "suggested_answer"],
                },
                "description": "5-7 questions likely to be asked and suggested approaches to answering them",
            },
            "questions_to_ask": {
                "type": "array",
                "items": {"type": "string"},
                "description": "4-6 thoughtful questions the candidate should ask the interviewer",
            },
            "prep_checklist": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exactly 8 concrete prep tasks for the 30 minutes before the meeting",
            },
            "caveats": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Any assumptions made or things the candidate should verify",
            },
        },
        "required": [
            "meeting_summary",
            "talking_points",
            "expected_questions",
            "questions_to_ask",
            "prep_checklist",
            "caveats",
        ],
    },
}


def _build_user_context(user: User) -> str:
    parts = []
    if user.name:
        parts.append(f"Name: {user.name}")
    if user.target_roles:
        roles = json.loads(user.target_roles) if isinstance(user.target_roles, str) else user.target_roles
        parts.append(f"Target roles: {', '.join(roles)}")
    if user.background_summary:
        parts.append(f"Background: {user.background_summary}")
    if user.key_projects:
        projects = json.loads(user.key_projects) if isinstance(user.key_projects, str) else user.key_projects
        parts.append(f"Key projects: {', '.join(projects)}")
    if user.resume_structured:
        structured = json.loads(user.resume_structured) if isinstance(user.resume_structured, str) else user.resume_structured
        if structured.get("summary"):
            parts.append(f"Resume summary: {structured['summary']}")
        if structured.get("skills"):
            parts.append(f"Skills: {', '.join(structured['skills'][:20])}")
        if structured.get("experience"):
            exp_lines = []
            for exp in structured["experience"][:3]:
                exp_lines.append(f"  - {exp.get('role', '')} at {exp.get('company', '')} ({exp.get('start', '')}–{exp.get('end', '')})")
            parts.append("Experience:\n" + "\n".join(exp_lines))
    elif user.resume_raw_text:
        # Structured data not available — include full raw text (Claude will parse in context)
        parts.append(f"Resume (full text):\n{user.resume_raw_text[:8000]}")
    return "\n".join(parts) if parts else "No profile data available."


def _compute_hash(pack: dict) -> str:
    content = (
        (pack.get("meeting_summary") or "")
        + json.dumps(pack.get("talking_points") or [])
        + json.dumps(pack.get("expected_questions") or [])
        + json.dumps(pack.get("questions_to_ask") or [])
        + json.dumps(pack.get("prep_checklist") or [])
    )
    return hashlib.sha256(content.encode()).hexdigest()


def generate_prep_pack(
    event: CalendarEvent,
    classification: EventClassification,
    user: User,
    db: Session,
    api_key: Optional[str] = None,
) -> PrepPack:
    """Generate (or regenerate) a prep pack for an event. Returns the PrepPack row."""
    # Get or create the prep pack row
    pack = db.query(PrepPack).filter(PrepPack.event_id == event.id).first()
    if pack is None:
        pack = PrepPack(
            event_id=event.id,
            user_id=user.id,
            generation_status="pending",
        )
        db.add(pack)
        db.commit()
        db.refresh(pack)

    pack.generation_status = "generating"
    db.commit()

    try:
        client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key, timeout=60.0)

        attendees = json.loads(event.attendees or "[]")
        attendee_names_emails = ", ".join(
            f"{a.get('name', '')} <{a.get('email', '')}>"
            for a in attendees
            if a.get("email")
        )

        effective_label = classification.user_override or classification.label

        user_message = f"""You are a professional interview coach. Generate a meeting prep pack as valid JSON only — no markdown, no explanation, just the JSON object.

MEETING:
Title: {event.title}
Type: {effective_label.replace('_', ' ').title()}
Company: {classification.company_name or 'unknown'}
Role: {classification.role_title or 'unknown'}
Date: {event.start_time.strftime('%A, %B %d %Y')}
Attendees: {attendee_names_emails or 'not listed'}
Description: {(event.description or 'none')[:500]}

CANDIDATE:
{_build_user_context(user)}

Important: Mine the CANDIDATE section carefully for specific company names, product names, measurable outcomes (users, accuracy %, revenue, scale), technical stack, and role titles. Reference these specifics explicitly throughout every section — talking points, expected questions, checklist items, and caveats. Never use generic descriptions when specific ones are available from the candidate's background.

Return a JSON object with exactly these keys:
- "meeting_summary": string (2-3 sentences)
- "talking_points": array of 4-5 strings
- "expected_questions": array of 4-5 objects with "question" and "suggested_answer"
- "questions_to_ask": array of 4-5 strings
- "prep_checklist": array of exactly 8 strings
- "caveats": array of 1-3 strings

JSON:"""

        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=2500,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.split("```")[0]
        raw = raw.strip()

        # If JSON is truncated, close it so it parses
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Count unclosed braces/brackets and close them
            open_braces = raw.count("{") - raw.count("}")
            open_brackets = raw.count("[") - raw.count("]")
            raw = raw.rstrip(",").rstrip()
            raw += "]" * open_brackets + "}" * open_braces
            result = json.loads(raw)
        new_hash = _compute_hash(result)

        # Detect if content actually changed (for dedup)
        content_changed = pack.content_hash != new_hash

        pack.meeting_summary = result.get("meeting_summary")
        pack.talking_points = json.dumps(result.get("talking_points", []))
        pack.expected_questions = json.dumps(result.get("expected_questions", []))
        pack.questions_to_ask = json.dumps(result.get("questions_to_ask", []))
        pack.prep_checklist = json.dumps(
            [{"item": i, "done": False} for i in result.get("prep_checklist", [])]
        )
        pack.caveats = json.dumps(result.get("caveats", []))
        pack.generation_status = "done"
        pack.generation_error = None
        pack.generated_at = datetime.utcnow()
        pack.model_version = settings.claude_model
        pack.content_hash = new_hash

        db.commit()
        db.refresh(pack)

        _log(
            db,
            user.id,
            "prep_gen",
            "success",
            f"Generated prep pack for event '{event.title}' (content_changed={content_changed})",
        )

        return pack

    except Exception as e:
        pack.generation_status = "failed"
        pack.generation_error = str(e)
        db.commit()
        _log(db, user.id, "prep_gen", "failed", f"Event '{event.title}': {e}")
        raise


def generate_pending_packs(user_id: int, db: Session):
    """Generate prep packs for all events that are classified and ready."""
    from backend.services.classifier import is_job_related_label

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    target_roles_raw = user.target_roles
    events_ready = (
        db.query(CalendarEvent)
        .join(EventClassification)
        .outerjoin(PrepPack)
        .filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_time > datetime.utcnow(),
        )
        .all()
    )

    for event in events_ready:
        clf = event.classification
        if clf is None:
            continue
        effective = clf.user_override or clf.label
        if not is_job_related_label(effective):
            continue
        # Skip if prep pack is already done (and hasn't been reset to pending)
        if event.prep_pack and event.prep_pack.generation_status in ("done", "generating"):
            continue
        try:
            generate_prep_pack(event, clf, user, db)
        except Exception as e:
            print(f"[prep_gen] Failed for event {event.id}: {e}")


def _log(db: Session, user_id: int, sync_type: str, status: str, details: str):
    entry = SyncLog(
        user_id=user_id,
        sync_type=sync_type,
        status=status,
        details=details[:2000],
    )
    db.add(entry)
    db.commit()
