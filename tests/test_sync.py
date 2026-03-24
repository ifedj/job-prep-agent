"""Tests for calendar sync, deduplication, and rescheduled events."""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.models import CalendarEvent, EventClassification, PrepPack, EmailDeliveryLog
from backend.services.email_sender import send_prep_pack_email
from tests.conftest import make_claude_classification, make_claude_prep_pack


def _make_google_event(event_id, title, start, end, attendees=None, description="", updated=None):
    """Build a fake Google Calendar API event dict."""
    return {
        "id": event_id,
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat() + "Z"},
        "end": {"dateTime": end.isoformat() + "Z"},
        "attendees": attendees or [],
        "organizer": {"email": "org@example.com"},
        "htmlLink": f"https://calendar.google.com/event/{event_id}",
        "updated": (updated or start).isoformat() + "Z",
    }


class TestCalendarSync:
    def test_new_events_are_created(self, db, test_user):
        """New events from Google Calendar should be inserted."""
        from backend.services.gcalendar import sync_events

        future = datetime.utcnow() + timedelta(days=3)
        fake_events = [
            _make_google_event("evt-001", "Interview at TechCo", future, future + timedelta(hours=1),
                attendees=[{"email": "hr@techco.com", "displayName": "HR", "responseStatus": "accepted"}])
        ]

        with patch("backend.services.gcalendar._build_service") as mock_svc:
            mock_svc.return_value.events.return_value.list.return_value.execute.return_value = {
                "items": fake_events
            }
            result = sync_events(test_user.id, db)

        assert result["new"] == 1
        event = db.query(CalendarEvent).filter(CalendarEvent.google_event_id == "evt-001").first()
        assert event is not None
        assert event.title == "Interview at TechCo"

    def test_duplicate_sync_does_not_create_duplicates(self, db, test_user):
        """Running sync twice should not duplicate events."""
        from backend.services.gcalendar import sync_events

        future = datetime.utcnow() + timedelta(days=3)
        fake_events = [_make_google_event("evt-002", "Coffee with recruiter", future, future + timedelta(hours=1))]

        svc_mock = MagicMock()
        svc_mock.events.return_value.list.return_value.execute.return_value = {"items": fake_events}

        with patch("backend.services.gcalendar._build_service", return_value=svc_mock):
            sync_events(test_user.id, db)
            sync_events(test_user.id, db)

        count = db.query(CalendarEvent).filter(CalendarEvent.google_event_id == "evt-002").count()
        assert count == 1

    def test_rescheduled_event_clears_prep_pack(self, db, test_user, rescheduled_event):
        """If an event's time changes, the existing done prep pack should reset to pending."""
        from backend.services.gcalendar import sync_events

        # Create a "done" prep pack for the event
        clf = EventClassification(
            event_id=rescheduled_event.id,
            label="recruiter_screen",
            confidence=0.95,
            reasoning="clear screen",
            model_version="test",
        )
        db.add(clf)
        pack = PrepPack(
            event_id=rescheduled_event.id,
            user_id=test_user.id,
            generation_status="done",
            content_hash="old-hash",
        )
        db.add(pack)
        db.commit()

        # Simulate a rescheduled event (updated timestamp newer)
        new_start = rescheduled_event.start_time + timedelta(days=1)
        updated_time = datetime.utcnow()
        fake_events = [_make_google_event(
            rescheduled_event.google_event_id,
            rescheduled_event.title,
            new_start,
            new_start + timedelta(hours=1),
            attendees=[{"email": "hiring@bigtech.com", "displayName": "Recruiter", "responseStatus": "accepted"}],
            updated=updated_time,
        )]

        # Advance the google_updated timestamp on the event so sync detects a change
        rescheduled_event.google_updated = datetime.utcnow() - timedelta(hours=1)
        db.commit()

        svc_mock = MagicMock()
        svc_mock.events.return_value.list.return_value.execute.return_value = {"items": fake_events}

        with patch("backend.services.gcalendar._build_service", return_value=svc_mock):
            sync_events(test_user.id, db)

        db.refresh(pack)
        assert pack.generation_status == "pending"


class TestEmailDeduplication:
    def _setup_done_pack(self, db, test_user, interview_event):
        clf = EventClassification(
            event_id=interview_event.id,
            label="interview",
            confidence=0.97,
            reasoning="clear interview",
            company_name="Acme Corp",
            model_version="test",
        )
        db.add(clf)
        pack = PrepPack(
            event_id=interview_event.id,
            user_id=test_user.id,
            generation_status="done",
            meeting_summary="Technical interview at Acme Corp.",
            talking_points=json.dumps(["Point 1", "Point 2"]),
            expected_questions=json.dumps([{"question": "Q1", "suggested_answer": "A1"}]),
            questions_to_ask=json.dumps(["Ask about culture"]),
            prep_checklist=json.dumps([{"item": "Review resume", "done": False}]),
            caveats=json.dumps([]),
            content_hash="abc123",
            model_version="test",
            generated_at=datetime.utcnow(),
        )
        db.add(pack)
        db.commit()
        db.refresh(pack)
        return pack

    def test_first_send_succeeds(self, db, test_user, interview_event):
        """First send should deliver and log status=sent."""
        pack = self._setup_done_pack(db, test_user, interview_event)

        with patch("backend.services.email_sender.ggmail.send_email", return_value="msg-001"):
            result = send_prep_pack_email(pack.id, test_user.id, db)

        assert result["status"] == "sent"
        log = db.query(EmailDeliveryLog).filter(
            EmailDeliveryLog.prep_pack_id == pack.id,
            EmailDeliveryLog.status == "sent",
        ).first()
        assert log is not None
        assert log.gmail_message_id == "msg-001"

    def test_duplicate_send_is_skipped(self, db, test_user, interview_event):
        """Sending the same pack twice should skip the second send."""
        pack = self._setup_done_pack(db, test_user, interview_event)

        with patch("backend.services.email_sender.ggmail.send_email", return_value="msg-002") as mock_send:
            send_prep_pack_email(pack.id, test_user.id, db)
            result = send_prep_pack_email(pack.id, test_user.id, db)

        assert result["status"] == "skipped_duplicate"
        assert mock_send.call_count == 1  # Gmail API called only once

    def test_send_failure_logs_error(self, db, test_user, interview_event):
        """A Gmail API failure should be logged with status=failed."""
        pack = self._setup_done_pack(db, test_user, interview_event)

        with patch("backend.services.email_sender.ggmail.send_email", side_effect=Exception("Gmail error")):
            with pytest.raises(Exception, match="Gmail error"):
                send_prep_pack_email(pack.id, test_user.id, db)

        log = db.query(EmailDeliveryLog).filter(
            EmailDeliveryLog.prep_pack_id == pack.id,
            EmailDeliveryLog.status == "failed",
        ).first()
        assert log is not None
        assert "Gmail error" in log.error_message
