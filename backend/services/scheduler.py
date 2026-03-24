"""Background scheduler: syncs calendar, classifies events, generates and sends prep packs."""
import json
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import SessionLocal
from backend.models import User, OAuthToken

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler = None


def run_sync_for_user(user_id: int, db: Session):
    """Full sync pipeline for one user: calendar → classify → generate → email."""
    from backend.services.gcalendar import sync_events
    from backend.services.classifier import classify_unclassified_events, should_auto_generate
    from backend.services.prep_generator import generate_pending_packs
    from backend.services.email_sender import send_prep_pack_email
    from backend.models import CalendarEvent, EventClassification, PrepPack
    from backend.config import get_settings

    cfg = get_settings()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    target_roles = json.loads(user.target_roles) if user.target_roles else []

    logger.info(f"[scheduler] Starting sync for user {user_id}")

    # Step 1: Sync calendar events
    try:
        sync_result = sync_events(user_id, db)
        logger.info(f"[scheduler] Calendar sync: {sync_result}")
    except Exception as e:
        logger.error(f"[scheduler] Calendar sync failed for user {user_id}: {e}")
        return  # No point continuing if we can't get events

    # Step 2: Classify unclassified events
    try:
        classify_unclassified_events(user_id, db, target_roles)
    except Exception as e:
        logger.error(f"[scheduler] Classification failed for user {user_id}: {e}")

    # Step 3: Generate prep packs for ready events
    try:
        generate_pending_packs(user_id, db)
    except Exception as e:
        logger.error(f"[scheduler] Prep generation failed for user {user_id}: {e}")

    # Step 4: Send emails for completed prep packs that haven't been emailed yet
    try:
        ready_packs = (
            db.query(PrepPack)
            .join(CalendarEvent)
            .join(EventClassification)
            .filter(
                CalendarEvent.user_id == user_id,
                CalendarEvent.start_time > datetime.utcnow(),
                PrepPack.generation_status == "done",
            )
            .all()
        )

        for pack in ready_packs:
            clf = pack.event.classification
            if clf is None:
                continue
            if not should_auto_generate(clf):
                continue

            # Check if already emailed successfully
            from backend.models import EmailDeliveryLog
            already_sent = (
                db.query(EmailDeliveryLog)
                .filter(
                    EmailDeliveryLog.prep_pack_id == pack.id,
                    EmailDeliveryLog.status == "sent",
                )
                .first()
            )
            if already_sent:
                # Only resend if pack content changed (hash mismatch)
                latest_hash = pack.content_hash
                continue  # Dedup at send level handles this too

            try:
                result = send_prep_pack_email(pack.id, user_id, db)
                logger.info(f"[scheduler] Email send result for pack {pack.id}: {result}")
            except Exception as e:
                logger.error(f"[scheduler] Email send failed for pack {pack.id}: {e}")

    except Exception as e:
        logger.error(f"[scheduler] Email phase failed for user {user_id}: {e}")

    logger.info(f"[scheduler] Completed sync for user {user_id}")


def sync_all_users():
    """APScheduler job: sync all users with connected Google accounts."""
    db: Session = SessionLocal()
    try:
        user_ids = (
            db.query(OAuthToken.user_id)
            .filter(OAuthToken.provider == "google")
            .all()
        )
        for (user_id,) in user_ids:
            try:
                run_sync_for_user(user_id, db)
            except Exception as e:
                logger.error(f"[scheduler] Unhandled error for user {user_id}: {e}")
    finally:
        db.close()


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        sync_all_users,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="sync_all_users",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(f"[scheduler] Started — will sync every {settings.sync_interval_minutes} minutes")


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_next_run() -> float | None:
    """Return seconds until the next scheduled run, or None."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job("sync_all_users")
    if job is None or job.next_run_time is None:
        return None
    delta = (job.next_run_time.replace(tzinfo=None) - datetime.utcnow()).total_seconds()
    return max(0.0, delta)
