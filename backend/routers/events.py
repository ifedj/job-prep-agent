"""Calendar events routes."""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user
from backend.models import CalendarEvent, EventClassification, PrepPack, User
from backend.schemas import AttendeeInfo, ClassificationRead, EventRead, ManualLabelRequest
from backend.services.classifier import classify_event, VALID_LABELS

router = APIRouter()


def _serialize_event(event: CalendarEvent) -> EventRead:
    attendees_raw = json.loads(event.attendees or "[]")
    attendees = [
        AttendeeInfo(
            email=a.get("email", ""),
            name=a.get("name"),
            response_status=a.get("response_status"),
        )
        for a in attendees_raw
    ]

    clf = None
    if event.classification:
        clf = ClassificationRead(
            label=event.classification.label,
            effective_label=event.classification.effective_label,
            confidence=event.classification.confidence,
            reasoning=event.classification.reasoning,
            company_name=event.classification.company_name,
            role_title=event.classification.role_title,
            classified_at=event.classification.classified_at,
            user_override=event.classification.user_override,
        )

    pack_status = None
    pack_id = None
    if event.prep_pack:
        pack_status = event.prep_pack.generation_status
        pack_id = event.prep_pack.id

    return EventRead(
        id=event.id,
        google_event_id=event.google_event_id,
        title=event.title,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
        attendees=attendees,
        organizer_email=event.organizer_email,
        location=event.location,
        html_link=event.html_link,
        synced_at=event.synced_at,
        classification=clf,
        has_prep_pack=event.prep_pack is not None,
        prep_pack_status=pack_status,
        prep_pack_id=pack_id,
    )


@router.get("", response_model=List[EventRead])
def list_events(
    include_not_job_related: bool = Query(default=False),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    label: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(CalendarEvent).filter(
        CalendarEvent.user_id == current_user.id,
        CalendarEvent.start_time > (start or datetime.utcnow()),
    )
    if end:
        q = q.filter(CalendarEvent.start_time < end)

    events = q.order_by(CalendarEvent.start_time).all()

    result = []
    for event in events:
        effective = None
        if event.classification:
            effective = event.classification.user_override or event.classification.label
        if not include_not_job_related and effective == "not_job_related":
            continue
        if label and effective != label:
            continue
        result.append(_serialize_event(event))

    return result


@router.get("/{event_id}", response_model=EventRead)
def get_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(CalendarEvent).filter(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return _serialize_event(event)


@router.post("/{event_id}/classify")
def reclassify_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Trigger LLM reclassification for a specific event."""
    event = db.query(CalendarEvent).filter(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    target_roles = json.loads(current_user.target_roles) if current_user.target_roles else []
    classification = classify_event(event, target_roles, db)
    return _serialize_event(event)


@router.patch("/{event_id}/label")
def override_label(
    event_id: int,
    body: ManualLabelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually override the classification label for an event."""
    if body.label not in VALID_LABELS:
        raise HTTPException(status_code=400, detail=f"Invalid label. Must be one of: {', '.join(VALID_LABELS)}")

    event = db.query(CalendarEvent).filter(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    clf = event.classification
    if clf is None:
        # Create a minimal classification
        clf = EventClassification(
            event_id=event.id,
            label=body.label,
            confidence=1.0,
            reasoning="Manually set by user",
            model_version="manual",
        )
        db.add(clf)
    else:
        clf.user_override = body.label
        clf.user_override_at = datetime.utcnow()

    db.commit()
    db.refresh(event)
    return _serialize_event(event)
