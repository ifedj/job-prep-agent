"""Review queue for ambiguous events."""
import json
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user
from backend.models import CalendarEvent, EventClassification, User
from backend.schemas import EventRead, ReviewDecision
from backend.services.classifier import VALID_LABELS, is_job_related_label
from backend.routers.events import _serialize_event

router = APIRouter()


@router.get("", response_model=List[EventRead])
def get_review_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all ambiguous upcoming events awaiting user decision."""
    events = (
        db.query(CalendarEvent)
        .join(EventClassification)
        .filter(
            CalendarEvent.user_id == current_user.id,
            CalendarEvent.start_time > datetime.utcnow(),
            EventClassification.label == "ambiguous",
            EventClassification.user_override.is_(None),
        )
        .order_by(CalendarEvent.start_time)
        .all()
    )
    return [_serialize_event(e) for e in events]


@router.post("/{event_id}/decide")
def decide_event(
    event_id: int,
    body: ReviewDecision,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User resolves an ambiguous event. Optionally triggers prep pack generation."""
    if body.label not in VALID_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid label. Must be one of: {', '.join(VALID_LABELS)}",
        )

    event = db.query(CalendarEvent).filter(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    clf = event.classification
    if clf is None:
        clf = EventClassification(
            event_id=event.id,
            label="ambiguous",
            confidence=0.5,
            reasoning="Manually resolved from review queue",
            model_version="manual",
        )
        db.add(clf)

    clf.user_override = body.label
    clf.user_override_at = datetime.utcnow()
    db.commit()

    # Trigger prep pack generation if the user says it's job-related
    if body.generate_prep and is_job_related_label(body.label):
        from backend.routers.prep_packs import _regen_and_send
        background_tasks.add_task(_regen_and_send, None, event.id, current_user.id)
        return {
            "message": f"Event labeled as '{body.label}'. Prep pack generation started.",
            "event_id": event_id,
        }

    return {
        "message": f"Event labeled as '{body.label}'.",
        "event_id": event_id,
    }
