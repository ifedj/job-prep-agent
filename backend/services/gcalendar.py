"""Google Calendar API client and sync logic."""
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models import CalendarEvent, SyncLog
from backend.services.oauth import get_credentials


def _build_service(user_id: int, db: Session):
    creds = get_credentials(user_id, db)
    if not creds:
        raise ValueError(f"No valid Google credentials for user {user_id}")
    return build("calendar", "v3", credentials=creds)


def _parse_datetime(dt_dict: dict) -> datetime:
    """Parse Google Calendar dateTime or date field."""
    if "dateTime" in dt_dict:
        raw = dt_dict["dateTime"]
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    elif "date" in dt_dict:
        return datetime.strptime(dt_dict["date"], "%Y-%m-%d")
    return datetime.utcnow()


def sync_events(user_id: int, db: Session, days_ahead: int = 14) -> dict:
    """Sync upcoming calendar events for a user.

    Returns a summary dict with counts of new/updated events.
    """
    _log(db, user_id, "calendar", "started", "Beginning calendar sync")

    try:
        service = _build_service(user_id, db)

        now = datetime.utcnow()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days_ahead)).isoformat() + "Z"

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=100,
        ).execute()

        items = result.get("items", [])
        new_count = 0
        updated_count = 0

        for item in items:
            event_id = item.get("id", "")
            title = item.get("summary", "(No Title)")
            description = item.get("description", "")
            start = _parse_datetime(item.get("start", {}))
            end = _parse_datetime(item.get("end", {}))
            organizer = item.get("organizer", {}).get("email")
            location = item.get("location")
            html_link = item.get("htmlLink")
            google_updated_str = item.get("updated")
            google_updated = None
            if google_updated_str:
                try:
                    google_updated = datetime.fromisoformat(
                        google_updated_str.replace("Z", "+00:00")
                    ).astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass

            raw_attendees = item.get("attendees", [])
            attendees_json = json.dumps([
                {
                    "email": a.get("email", ""),
                    "name": a.get("displayName", ""),
                    "response_status": a.get("responseStatus", ""),
                }
                for a in raw_attendees
            ])

            # Check if event already exists
            existing = db.query(CalendarEvent).filter(
                CalendarEvent.user_id == user_id,
                CalendarEvent.google_event_id == event_id,
            ).first()

            if existing is None:
                new_event = CalendarEvent(
                    user_id=user_id,
                    google_event_id=event_id,
                    calendar_id="primary",
                    title=title,
                    description=description,
                    start_time=start,
                    end_time=end,
                    attendees=attendees_json,
                    organizer_email=organizer,
                    location=location,
                    html_link=html_link,
                    google_updated=google_updated,
                    raw_json=json.dumps(item),
                    synced_at=datetime.utcnow(),
                )
                db.add(new_event)
                new_count += 1
            else:
                # Check if event was updated on Google's side
                needs_reclassify = False
                if google_updated and existing.google_updated:
                    needs_reclassify = google_updated > existing.google_updated

                existing.title = title
                existing.description = description
                existing.start_time = start
                existing.end_time = end
                existing.attendees = attendees_json
                existing.organizer_email = organizer
                existing.location = location
                existing.html_link = html_link
                existing.google_updated = google_updated
                existing.raw_json = json.dumps(item)
                existing.synced_at = datetime.utcnow()

                # If event changed materially, clear prep pack so it regenerates
                if needs_reclassify and existing.prep_pack and existing.prep_pack.generation_status == "done":
                    existing.prep_pack.generation_status = "pending"
                    existing.prep_pack.content_hash = ""

                updated_count += 1

        db.commit()

        details = f"Synced {len(items)} events: {new_count} new, {updated_count} updated"
        _log(db, user_id, "calendar", "success", details)
        return {"new": new_count, "updated": updated_count, "total": len(items)}

    except Exception as e:
        db.rollback()
        _log(db, user_id, "calendar", "failed", str(e))
        raise


def _log(db: Session, user_id: int, sync_type: str, status: str, details: str):
    entry = SyncLog(
        user_id=user_id,
        sync_type=sync_type,
        status=status,
        details=details,
    )
    db.add(entry)
    db.commit()
