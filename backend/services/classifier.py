"""LLM-based classification of calendar events."""
import json
from datetime import datetime
from typing import Optional

import anthropic
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import CalendarEvent, EventClassification, SyncLog

settings = get_settings()

VALID_LABELS = {
    "interview",
    "recruiter_screen",
    "networking",
    "company_intro",
    "not_job_related",
    "ambiguous",
}

CLASSIFICATION_TOOL = {
    "name": "classify_event",
    "description": "Classify a calendar event as job-related or not.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": list(VALID_LABELS),
                "description": "Event classification label",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score from 0.0 to 1.0",
            },
            "company_name": {
                "type": "string",
                "description": "Name of the company involved, if detectable",
            },
            "role_title": {
                "type": "string",
                "description": "Job title/role being discussed, if detectable",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the classification",
            },
        },
        "required": ["label", "confidence", "reasoning"],
    },
}

SYSTEM_PROMPT = """You are a calendar event classifier for a job seeker.

Your job is to determine whether a calendar event is related to their job search.

Labels:
- interview: A formal interview (phone, video, or in-person) with a company
- recruiter_screen: An initial recruiter call or HR screen
- networking: A coffee chat, informational interview, or professional networking meeting
- company_intro: A company info session, career fair booth, or introductory call
- not_job_related: Personal appointments, internal team meetings, dentist, family events, etc.
- ambiguous: You genuinely cannot determine if this is job-related

Rules:
1. Look for external attendee domains (not the user's own company domain) as a strong signal
2. Words like "interview", "screen", "hiring", "recruiter", "offer" strongly suggest job-related
3. If attendees are all from the user's known domain and there is no external company context, lean toward not_job_related
4. A meeting with a generic title like "Catch up" but with an attendee from a company the user is targeting is ambiguous
5. Use high confidence (>0.85) only when you are certain
6. Use ambiguous when you genuinely cannot tell, do not force a label"""


def classify_event(
    event: CalendarEvent,
    user_target_roles: list[str],
    db: Session,
) -> EventClassification:
    """Classify a single calendar event using Claude. Persists the result."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    attendees = json.loads(event.attendees or "[]")
    attendee_emails = [a.get("email", "") for a in attendees]
    attendee_domains = list({e.split("@")[-1] for e in attendee_emails if "@" in e})

    user_message = f"""Calendar event to classify:

Title: {event.title}
Description: {(event.description or '')[:1500]}
Attendee email domains: {', '.join(attendee_domains) or 'none'}
Organizer: {event.organizer_email or 'unknown'}
Date: {event.start_time.strftime('%A, %B %d %Y at %H:%M UTC')}

User's target roles: {', '.join(user_target_roles) if user_target_roles else 'not specified'}

Classify this event."""

    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[CLASSIFICATION_TOOL],
        tool_choice={"type": "tool", "name": "classify_event"},
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract tool use result
    tool_result = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_result is None:
        # Fallback if tool use fails
        result = {
            "label": "ambiguous",
            "confidence": 0.5,
            "reasoning": "Classification model did not return expected output",
            "company_name": None,
            "role_title": None,
        }
    else:
        result = tool_result.input

    label = result.get("label", "ambiguous")
    if label not in VALID_LABELS:
        label = "ambiguous"

    confidence = float(result.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    # role_title must be filled for any job-related event so prep pack generation
    # always has a role to work with. If the LLM couldn't extract one from the event,
    # fall back to the user's primary target role.
    raw_role = result.get("role_title", "")
    _PLACEHOLDERS = {"<unknown>", "<none>", "<n/a>", "unknown", "n/a", "none", "not specified", ""}
    if not raw_role or raw_role.strip("<>").lower() in _PLACEHOLDERS:
        raw_role = None
    if raw_role is None and label in {"interview", "recruiter_screen", "networking", "company_intro"}:
        raw_role = user_target_roles[0] if user_target_roles else None

    # Persist or update classification
    classification = db.query(EventClassification).filter(
        EventClassification.event_id == event.id
    ).first()

    if classification:
        classification.label = label
        classification.confidence = confidence
        classification.reasoning = result.get("reasoning", "")
        classification.company_name = result.get("company_name")
        classification.role_title = raw_role
        classification.classified_at = datetime.utcnow()
        classification.model_version = settings.claude_model
    else:
        classification = EventClassification(
            event_id=event.id,
            label=label,
            confidence=confidence,
            reasoning=result.get("reasoning", ""),
            company_name=result.get("company_name"),
            role_title=raw_role,
            model_version=settings.claude_model,
        )
        db.add(classification)

    db.commit()
    db.refresh(classification)

    _log(
        db,
        event.user_id,
        "classification",
        "success",
        f"Event '{event.title}': {label} ({confidence:.2f}) — {result.get('reasoning', '')}",
    )

    return classification


def should_auto_generate(classification: EventClassification) -> bool:
    """Return True if the event is confident enough to auto-generate a prep pack."""
    effective = classification.user_override or classification.label
    if effective == "not_job_related":
        return False
    if effective == "ambiguous":
        return False
    if classification.user_override:
        return True  # User manually confirmed it
    return classification.confidence >= settings.classification_high_confidence


def is_job_related_label(label: str) -> bool:
    return label in {"interview", "recruiter_screen", "networking", "company_intro"}


def classify_unclassified_events(user_id: int, db: Session, target_roles: list[str]):
    """Classify all unclassified upcoming events for a user."""
    from datetime import datetime

    events = (
        db.query(CalendarEvent)
        .outerjoin(EventClassification)
        .filter(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_time > datetime.utcnow(),
            EventClassification.id.is_(None),
        )
        .all()
    )

    for event in events:
        try:
            classify_event(event, target_roles, db)
        except Exception as e:
            _log(db, user_id, "classification", "failed", f"Event {event.id}: {e}")


def _log(db: Session, user_id: int, sync_type: str, status: str, details: str):
    entry = SyncLog(
        user_id=user_id,
        sync_type=sync_type,
        status=status,
        details=details[:2000],
    )
    db.add(entry)
    db.commit()
