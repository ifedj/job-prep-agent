"""Prep pack routes."""
import json
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user
from backend.models import CalendarEvent, EventClassification, PrepPack, User
from backend.schemas import ChecklistItem, EmailLogRead, ExpectedQuestion, PrepPackRead


class RegenerateRequest(BaseModel):
    email: Optional[str] = None

router = APIRouter()


def _serialize_pack(pack: PrepPack) -> PrepPackRead:
    talking_points = json.loads(pack.talking_points or "[]")
    expected_q_raw = json.loads(pack.expected_questions or "[]")
    expected_q = [
        ExpectedQuestion(
            question=q.get("question", "") if isinstance(q, dict) else q,
            suggested_answer=q.get("suggested_answer", "") if isinstance(q, dict) else "",
        )
        for q in expected_q_raw
    ]
    questions_to_ask = json.loads(pack.questions_to_ask or "[]")
    checklist_raw = json.loads(pack.prep_checklist or "[]")
    checklist = [
        ChecklistItem(
            item=c.get("item", c) if isinstance(c, dict) else c,
            done=c.get("done", False) if isinstance(c, dict) else False,
        )
        for c in checklist_raw
    ]
    caveats = json.loads(pack.caveats or "[]")

    return PrepPackRead(
        id=pack.id,
        event_id=pack.event_id,
        meeting_summary=pack.meeting_summary,
        talking_points=talking_points,
        expected_questions=expected_q,
        questions_to_ask=questions_to_ask,
        prep_checklist=checklist,
        caveats=caveats,
        generation_status=pack.generation_status,
        generation_error=pack.generation_error,
        generated_at=pack.generated_at,
        model_version=pack.model_version,
    )


@router.get("", response_model=List[PrepPackRead])
def list_prep_packs(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(PrepPack).join(CalendarEvent).filter(
        PrepPack.user_id == current_user.id,
    )
    if status:
        q = q.filter(PrepPack.generation_status == status)
    packs = q.order_by(CalendarEvent.start_time).all()
    return [_serialize_pack(p) for p in packs]


@router.get("/{prep_pack_id}", response_model=PrepPackRead)
def get_prep_pack(
    prep_pack_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pack = db.query(PrepPack).filter(
        PrepPack.id == prep_pack_id,
        PrepPack.user_id == current_user.id,
    ).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Prep pack not found")
    return _serialize_pack(pack)


@router.post("/{prep_pack_id}/regenerate")
def regenerate_prep_pack(
    prep_pack_id: int,
    background_tasks: BackgroundTasks,
    body: RegenerateRequest = RegenerateRequest(),
    x_anthropic_key: Optional[str] = Header(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pack = db.query(PrepPack).filter(
        PrepPack.id == prep_pack_id,
        PrepPack.user_id == current_user.id,
    ).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Prep pack not found")

    pack.generation_status = "pending"
    db.commit()

    event = pack.event
    clf = event.classification
    if clf is None:
        raise HTTPException(status_code=422, detail="Event has no classification yet")

    # Pass email override to the background task — do NOT persist it to user.email
    background_tasks.add_task(_regen_and_send, pack.id, event.id, current_user.id, x_anthropic_key, body.email)

    return {"message": "Regeneration started", "prep_pack_id": prep_pack_id}


@router.post("/generate/{event_id}")
def trigger_generate_for_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger prep pack generation for a specific event."""
    event = db.query(CalendarEvent).filter(
        CalendarEvent.id == event_id,
        CalendarEvent.user_id == current_user.id,
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not event.classification:
        raise HTTPException(status_code=422, detail="Event must be classified first")

    background_tasks.add_task(_regen_and_send, None, event.id, current_user.id)
    return {"message": "Generation started", "event_id": event_id}


@router.get("/{prep_pack_id}/email-log", response_model=List[EmailLogRead])
def get_email_log(
    prep_pack_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from backend.models import EmailDeliveryLog
    logs = db.query(EmailDeliveryLog).filter(
        EmailDeliveryLog.prep_pack_id == prep_pack_id,
        EmailDeliveryLog.user_id == current_user.id,
    ).order_by(EmailDeliveryLog.id.desc()).all()
    return [EmailLogRead(
        id=l.id,
        recipient_email=l.recipient_email,
        subject=l.subject,
        status=l.status,
        sent_at=l.sent_at,
        error_message=l.error_message,
    ) for l in logs]


@router.post("/{prep_pack_id}/send-email")
def send_email_now(
    prep_pack_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually send the prep pack email (respects dedup)."""
    from backend.services.email_sender import send_prep_pack_email
    result = send_prep_pack_email(prep_pack_id, current_user.id, db)
    return result


def _regen_and_send(pack_id: Optional[int], event_id: int, user_id: int, api_key: Optional[str] = None, recipient_email: Optional[str] = None):
    """Background task: generate prep pack then send email."""
    from backend.database import SessionLocal
    from backend.services.prep_generator import generate_prep_pack
    from backend.services.email_sender import send_prep_pack_email

    db = SessionLocal()
    try:
        event = db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        clf = event.classification if event else None

        pack = generate_prep_pack(event, clf, user, db, api_key=api_key)

        # Use effective_label (respects user overrides) to decide auto-send.
        # recipient_email is passed through only to the email transport — never saved to DB.
        effective = clf.effective_label if clf else None
        if recipient_email or (effective and effective not in ("not_job_related", "ambiguous")):
            try:
                send_prep_pack_email(pack.id, user_id, db, recipient_email=recipient_email)
            except Exception as mail_err:
                print(f"[prep_packs] Email send failed (non-fatal): {mail_err}")
    except Exception as e:
        print(f"[prep_packs] Background regen failed: {e}")
    finally:
        db.close()
