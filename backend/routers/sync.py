"""Manual sync trigger and status route."""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user
from backend.models import CalendarEvent, OAuthToken, User, SyncLog
from backend.schemas import SyncStatus
from backend.services.scheduler import get_next_run

router = APIRouter()


@router.post("/trigger")
def trigger_sync(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger a full sync for the current user."""
    token_row = db.query(OAuthToken).filter(
        OAuthToken.user_id == current_user.id,
        OAuthToken.provider == "google",
    ).first()
    if not token_row:
        raise HTTPException(status_code=400, detail="Google account not connected")

    from backend.services.scheduler import run_sync_for_user
    background_tasks.add_task(_run_sync, current_user.id)
    return {"message": "Sync started in the background"}


@router.get("/status", response_model=SyncStatus)
def get_sync_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    last_log = (
        db.query(SyncLog)
        .filter(
            SyncLog.user_id == current_user.id,
            SyncLog.sync_type == "calendar",
            SyncLog.status == "success",
        )
        .order_by(SyncLog.id.desc())
        .first()
    )

    token_row = db.query(OAuthToken).filter(
        OAuthToken.user_id == current_user.id,
        OAuthToken.provider == "google",
    ).first()

    event_count = db.query(CalendarEvent).filter(
        CalendarEvent.user_id == current_user.id,
        CalendarEvent.start_time > datetime.utcnow(),
    ).count()

    return SyncStatus(
        last_sync_at=last_log.created_at if last_log else None,
        events_in_db=event_count,
        next_run_in_seconds=get_next_run(),
        google_connected=token_row is not None,
    )


@router.get("/logs")
def get_sync_logs(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logs = (
        db.query(SyncLog)
        .filter(SyncLog.user_id == current_user.id)
        .order_by(SyncLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "sync_type": l.sync_type,
            "status": l.status,
            "details": l.details,
            "created_at": l.created_at.isoformat(),
        }
        for l in logs
    ]


def _run_sync(user_id: int):
    from backend.database import SessionLocal
    from backend.services.scheduler import run_sync_for_user
    db = SessionLocal()
    try:
        run_sync_for_user(user_id, db)
    finally:
        db.close()
