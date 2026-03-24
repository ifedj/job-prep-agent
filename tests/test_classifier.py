"""Tests for LLM event classification."""
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.models import EventClassification
from backend.services.classifier import (
    classify_event,
    should_auto_generate,
    is_job_related_label,
    VALID_LABELS,
)
from tests.conftest import make_claude_classification


class TestClassifyEvent:
    def test_obvious_interview(self, db, test_user, interview_event):
        """A clear technical interview should be classified with high confidence."""
        mock_response = make_claude_classification("interview", 0.97, "Acme Corp", "Software Engineer")
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(interview_event, ["Software Engineer"], db)

        assert clf.label == "interview"
        assert clf.confidence >= 0.90
        assert clf.company_name == "Acme Corp"
        assert clf.role_title == "Software Engineer"

    def test_obvious_networking(self, db, test_user, networking_event):
        """A coffee chat with a known company employee should be networking."""
        mock_response = make_claude_classification("networking", 0.91, "Stripe")
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(networking_event, ["Software Engineer"], db)

        assert clf.label == "networking"
        assert clf.confidence >= 0.85
        assert clf.company_name == "Stripe"

    def test_ambiguous_event(self, db, test_user, ambiguous_event):
        """A generic 'Catch up' with unknown attendee should be ambiguous."""
        mock_response = make_claude_classification("ambiguous", 0.55)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(ambiguous_event, ["Software Engineer"], db)

        assert clf.label == "ambiguous"
        assert clf.confidence < 0.70

    def test_personal_event_not_job_related(self, db, test_user, personal_event):
        """A dentist appointment should be not_job_related with high confidence."""
        mock_response = make_claude_classification("not_job_related", 0.99)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(personal_event, ["Software Engineer"], db)

        assert clf.label == "not_job_related"
        assert clf.confidence >= 0.90

    def test_classification_persisted(self, db, test_user, interview_event):
        """Classification result should be stored in the database."""
        mock_response = make_claude_classification("interview", 0.95, "Acme Corp")
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(interview_event, [], db)

        stored = db.query(EventClassification).filter(
            EventClassification.event_id == interview_event.id
        ).first()
        assert stored is not None
        assert stored.label == "interview"
        assert stored.model_version != ""

    def test_reclassification_updates_existing(self, db, test_user, interview_event):
        """Reclassifying an event should update the existing row, not create a duplicate."""
        mock_1 = make_claude_classification("ambiguous", 0.55)
        mock_2 = make_claude_classification("interview", 0.95, "Acme Corp")

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_1
            classify_event(interview_event, [], db)

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_2
            classify_event(interview_event, [], db)

        count = db.query(EventClassification).filter(
            EventClassification.event_id == interview_event.id
        ).count()
        assert count == 1  # Should be updated, not duplicated

        final = db.query(EventClassification).filter(
            EventClassification.event_id == interview_event.id
        ).first()
        assert final.label == "interview"

    def test_invalid_label_falls_back_to_ambiguous(self, db, test_user, interview_event):
        """An invalid label from the LLM should default to ambiguous."""
        mock_response = make_claude_classification("definitely_an_interview", 0.99)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(interview_event, [], db)

        assert clf.label == "ambiguous"

    def test_confidence_clamped(self, db, test_user, interview_event):
        """Confidence outside 0-1 should be clamped."""
        mock_response = make_claude_classification("interview", 1.5)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            clf = classify_event(interview_event, [], db)

        assert clf.confidence <= 1.0


class TestShouldAutoGenerate:
    def _make_clf(self, label, confidence, override=None):
        clf = MagicMock(spec=EventClassification)
        clf.label = label
        clf.confidence = confidence
        clf.user_override = override
        return clf

    def test_high_confidence_interview_auto_generates(self):
        clf = self._make_clf("interview", 0.95)
        assert should_auto_generate(clf) is True

    def test_low_confidence_does_not_auto_generate(self):
        clf = self._make_clf("interview", 0.70)
        assert should_auto_generate(clf) is False

    def test_ambiguous_does_not_auto_generate(self):
        clf = self._make_clf("ambiguous", 0.90)
        assert should_auto_generate(clf) is False

    def test_not_job_related_does_not_auto_generate(self):
        clf = self._make_clf("not_job_related", 0.99)
        assert should_auto_generate(clf) is False

    def test_user_override_ignores_confidence(self):
        """A user-confirmed label should always auto-generate regardless of confidence."""
        clf = self._make_clf("ambiguous", 0.50, override="interview")
        assert should_auto_generate(clf) is True

    def test_user_override_not_job_related_does_not_generate(self):
        clf = self._make_clf("interview", 0.95, override="not_job_related")
        assert should_auto_generate(clf) is False


class TestIsJobRelatedLabel:
    def test_job_related_labels(self):
        for label in ["interview", "recruiter_screen", "networking", "company_intro"]:
            assert is_job_related_label(label) is True

    def test_non_job_labels(self):
        for label in ["not_job_related", "ambiguous"]:
            assert is_job_related_label(label) is False
