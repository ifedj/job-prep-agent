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


def _parse_json_safe(raw: str) -> dict:
    """Parse JSON from Claude output with fallback for literal newlines inside string values.

    Claude sometimes puts unescaped newlines inside JSON strings (e.g. in long suggested
    answers), which makes json.loads fail with 'Expecting delimiter'. Collapsing all
    newlines to spaces and retrying handles 99% of these cases.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Collapse literal newlines to spaces and retry
        collapsed = " ".join(line.strip() for line in raw.splitlines())
        return json.loads(collapsed)


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
        client = anthropic.Anthropic(
            api_key=api_key or settings.anthropic_api_key,
            timeout=180.0,
            max_retries=0,
        )

        attendees = json.loads(event.attendees or "[]")
        attendee_names_emails = ", ".join(
            f"{a.get('name', '')} <{a.get('email', '')}>"
            for a in attendees
            if a.get("email")
        )

        effective_label = classification.user_override or classification.label

        user_message = f"""You are a senior career coach writing a prep pack for a candidate about to have a specific job-related meeting. Your output will be read by that candidate minutes before the call — it must be specific, strategic, and immediately actionable.

QUALITY BAR: Every section must reference real details from this candidate's actual background — their real company names, real project names, real metrics, real timelines, real titles. If any sentence could be copy-pasted into a prep pack for a different candidate without changing a word, rewrite it until it cannot. Generic prep is useless and embarrassing.

━━━ MEETING ━━━
Title: {event.title}
Type: {effective_label.replace('_', ' ').title()}
Company: {classification.company_name or 'Unknown'}
Listed Role: {classification.role_title or 'Unknown'}
Date: {event.start_time.strftime('%A, %B %d %Y at %I:%M %p UTC')}
Attendees: {attendee_names_emails or 'not listed'}
Description: {(event.description or 'none')[:500]}

━━━ CANDIDATE ━━━
{_build_user_context(user)}

━━━ ROLE MISMATCH ANALYSIS ━━━
Compare the candidate's target roles (above) against the Listed Role for this meeting. If they differ, the prep pack MUST address this head-on — the meeting_summary must name the mismatch and state the strategic goal, and the talking_points must include specific language for redirecting the conversation. Do not ignore mismatches or pretend they don't exist.

━━━ SECTION-BY-SECTION INSTRUCTIONS ━━━

meeting_summary (2–3 sentences):
State what this meeting is, who it is with, and the strategic goal. If there is a role mismatch, name it explicitly in one sentence.

talking_points (3–4 items, each 2–3 sentences):
Write actual coaching content in second person. Reference real resume details by name — project names, metrics, companies. Tell the candidate what to lead with and why.

expected_questions (5–6 items):
Each item needs two fields:
  - "question": the likely question verbatim
  - "suggested_answer": 2–3 sentences of coaching — which specific project or metric to reference and how to frame it.

questions_to_ask (4 items, 1 sentence each):
Sharp, specific questions that signal the candidate did their homework and is thinking strategically about fit.

prep_checklist (6 items, 1–2 sentences each):
Specific actionable tasks for before the call — research, practice, logistics. Tailored to this meeting and this candidate.

caveats (3 items, 1–2 sentences each):
Honest flags about unknowns or assumptions the candidate must verify before the call.

━━━ OUTPUT FORMAT ━━━
Return ONLY a valid JSON object. No markdown code fences. No explanation before or after. No preamble. Start with {{ and end with }}.

Required keys:
- "meeting_summary": string
- "talking_points": array of strings (3–4 items)
- "expected_questions": array of objects with "question" (string) and "suggested_answer" (string) — 5–6 items
- "questions_to_ask": array of strings (4 items)
- "prep_checklist": array of strings (6 items)
- "caveats": array of strings (3 items)

JSON:"""

        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.max_tokens,
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

        result = _parse_json_safe(raw)
        new_hash = _compute_hash(result)

        # Detect if content actually changed (for dedup)
        content_changed = pack.content_hash != new_hash

        pack.meeting_summary = result.get("meeting_summary")
        pack.talking_points = json.dumps(result.get("talking_points", []))
        pack.expected_questions = json.dumps(result.get("expected_questions", []))
        pack.questions_to_ask = json.dumps(result.get("questions_to_ask", []))
        raw_checklist = result.get("prep_checklist", [])
        pack.prep_checklist = json.dumps(
            [i["item"] if isinstance(i, dict) else i for i in raw_checklist]
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
