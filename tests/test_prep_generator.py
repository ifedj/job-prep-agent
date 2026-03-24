"""Tests for prep pack generation."""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.models import EventClassification, PrepPack
from backend.services.prep_generator import generate_prep_pack, _compute_hash
from tests.conftest import make_claude_prep_pack


def _make_clf(db, event, label="interview", company="Acme Corp", role="Software Engineer", confidence=0.95):
    clf = EventClassification(
        event_id=event.id,
        label=label,
        confidence=confidence,
        reasoning="Mock reasoning",
        company_name=company,
        role_title=role,
        model_version="test",
    )
    db.add(clf)
    db.commit()
    db.refresh(clf)
    return clf


class TestGeneratePrepPack:
    def test_generates_all_sections(self, db, test_user, interview_event):
        """Generated pack should contain all required sections."""
        clf = _make_clf(db, interview_event)
        mock_response = make_claude_prep_pack()

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock_response
            pack = generate_prep_pack(interview_event, clf, test_user, db)

        assert pack.generation_status == "done"
        assert pack.meeting_summary is not None and len(pack.meeting_summary) > 10
        assert len(json.loads(pack.talking_points)) >= 2
        assert len(json.loads(pack.expected_questions)) >= 2
        assert len(json.loads(pack.questions_to_ask)) >= 2
        checklist = json.loads(pack.prep_checklist)
        assert len(checklist) == 8  # Exactly 8 checklist items required
        assert pack.content_hash != ""

    def test_prep_pack_persisted(self, db, test_user, interview_event):
        """Pack should be saved to the database with correct event link."""
        clf = _make_clf(db, interview_event)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = make_claude_prep_pack()
            pack = generate_prep_pack(interview_event, clf, test_user, db)

        stored = db.query(PrepPack).filter(PrepPack.event_id == interview_event.id).first()
        assert stored is not None
        assert stored.generation_status == "done"
        assert stored.event_id == interview_event.id
        assert stored.user_id == test_user.id

    def test_regeneration_updates_hash(self, db, test_user, interview_event):
        """Regenerating with the same content should produce the same hash."""
        clf = _make_clf(db, interview_event)
        mock = make_claude_prep_pack()

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock
            pack1 = generate_prep_pack(interview_event, clf, test_user, db)
            hash1 = pack1.content_hash

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = mock
            pack2 = generate_prep_pack(interview_event, clf, test_user, db)
            hash2 = pack2.content_hash

        assert hash1 == hash2  # Same content → same hash (dedup signal)

        # Only one PrepPack row should exist
        count = db.query(PrepPack).filter(PrepPack.event_id == interview_event.id).count()
        assert count == 1

    def test_generation_failure_marks_failed(self, db, test_user, interview_event):
        """If Claude fails, the pack status should be 'failed' with error message."""
        clf = _make_clf(db, interview_event)

        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.side_effect = Exception("API timeout")
            with pytest.raises(Exception, match="API timeout"):
                generate_prep_pack(interview_event, clf, test_user, db)

        pack = db.query(PrepPack).filter(PrepPack.event_id == interview_event.id).first()
        assert pack.generation_status == "failed"
        assert "API timeout" in pack.generation_error

    def test_networking_event_generates_pack(self, db, test_user, networking_event):
        """Networking events should also produce complete prep packs."""
        clf = _make_clf(db, networking_event, label="networking", company="Stripe", role=None)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = make_claude_prep_pack()
            pack = generate_prep_pack(networking_event, clf, test_user, db)

        assert pack.generation_status == "done"

    def test_checklist_items_have_done_false(self, db, test_user, interview_event):
        """Each checklist item should have done=False by default."""
        clf = _make_clf(db, interview_event)
        with patch("anthropic.Anthropic") as MockClaude:
            MockClaude.return_value.messages.create.return_value = make_claude_prep_pack()
            pack = generate_prep_pack(interview_event, clf, test_user, db)

        for item in json.loads(pack.prep_checklist):
            assert item["done"] is False


class TestContentHash:
    def test_same_content_same_hash(self):
        pack = {
            "meeting_summary": "A technical interview",
            "talking_points": ["point 1", "point 2"],
            "expected_questions": [{"question": "Q1", "suggested_answer": "A1"}],
            "questions_to_ask": ["Ask about culture"],
            "prep_checklist": ["Review resume"],
        }
        assert _compute_hash(pack) == _compute_hash(pack)

    def test_different_content_different_hash(self):
        pack1 = {"meeting_summary": "Interview at Acme", "talking_points": [], "expected_questions": [], "questions_to_ask": [], "prep_checklist": []}
        pack2 = {"meeting_summary": "Interview at Stripe", "talking_points": [], "expected_questions": [], "questions_to_ask": [], "prep_checklist": []}
        assert _compute_hash(pack1) != _compute_hash(pack2)
