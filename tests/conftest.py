"""Shared pytest fixtures."""
import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use in-memory SQLite for tests
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["TOKEN_ENCRYPTION_KEY"] = ""

from backend.database import Base, get_db
from backend.main import app
from backend.models import (
    CalendarEvent, EventClassification, PrepPack, User, OAuthToken,
)
from backend.security import create_access_token

TEST_ENGINE = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=TEST_ENGINE)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture
def test_user(db):
    user = User(
        email="test@example.com",
        name="Test User",
        target_roles=json.dumps(["Software Engineer", "Backend Engineer"]),
        background_summary="5 years of Python and distributed systems experience.",
        key_projects=json.dumps(["payment-api", "ml-pipeline"]),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def interview_event(db, test_user):
    event = CalendarEvent(
        user_id=test_user.id,
        google_event_id="google-interview-001",
        calendar_id="primary",
        title="Technical Interview - Acme Corp",
        description="Final round technical interview with engineering team. Please bring portfolio.",
        start_time=datetime.utcnow() + timedelta(days=2),
        end_time=datetime.utcnow() + timedelta(days=2, hours=1),
        attendees=json.dumps([
            {"email": "recruiter@acme.com", "name": "Alice Smith", "response_status": "accepted"},
            {"email": "eng@acme.com", "name": "Bob Jones", "response_status": "accepted"},
        ]),
        organizer_email="recruiter@acme.com",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@pytest.fixture
def networking_event(db, test_user):
    event = CalendarEvent(
        user_id=test_user.id,
        google_event_id="google-network-001",
        calendar_id="primary",
        title="Coffee chat with Jane from Stripe",
        description="Informational interview about engineering culture at Stripe.",
        start_time=datetime.utcnow() + timedelta(days=3),
        end_time=datetime.utcnow() + timedelta(days=3, hours=1),
        attendees=json.dumps([
            {"email": "jane@stripe.com", "name": "Jane Doe", "response_status": "accepted"},
        ]),
        organizer_email="jane@stripe.com",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@pytest.fixture
def ambiguous_event(db, test_user):
    event = CalendarEvent(
        user_id=test_user.id,
        google_event_id="google-ambiguous-001",
        calendar_id="primary",
        title="Catch up",
        description="",
        start_time=datetime.utcnow() + timedelta(days=1),
        end_time=datetime.utcnow() + timedelta(days=1, hours=1),
        attendees=json.dumps([
            {"email": "someone@startup.io", "name": "Unknown Person", "response_status": "accepted"},
        ]),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@pytest.fixture
def personal_event(db, test_user):
    event = CalendarEvent(
        user_id=test_user.id,
        google_event_id="google-personal-001",
        calendar_id="primary",
        title="Dentist appointment",
        description="Annual checkup",
        start_time=datetime.utcnow() + timedelta(days=5),
        end_time=datetime.utcnow() + timedelta(days=5, hours=1),
        attendees=json.dumps([]),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@pytest.fixture
def rescheduled_event(db, test_user):
    """An event that was rescheduled (start_time changed)."""
    event = CalendarEvent(
        user_id=test_user.id,
        google_event_id="google-reschedule-001",
        calendar_id="primary",
        title="Recruiter Screen - BigTech",
        description="Phone screen with recruiting coordinator.",
        start_time=datetime.utcnow() + timedelta(days=4),
        end_time=datetime.utcnow() + timedelta(days=4, hours=1),
        attendees=json.dumps([
            {"email": "hiring@bigtech.com", "name": "Recruiter", "response_status": "accepted"},
        ]),
        organizer_email="hiring@bigtech.com",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def make_claude_classification(label: str, confidence: float, company: str = None, role: str = None):
    """Return a mock Anthropic response for classification."""
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.input = {
        "label": label,
        "confidence": confidence,
        "company_name": company,
        "role_title": role,
        "reasoning": f"Mock reasoning for {label}",
    }
    mock_response.content = [mock_block]
    return mock_response


def make_claude_prep_pack():
    """Return a mock Anthropic response for prep pack generation.

    prep_generator.py uses a plain-text JSON prompt (not tool_use), so the
    mock must expose the JSON string via .content[0].text, not .content[0].input.
    """
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.text = json.dumps({
        "meeting_summary": "This is a technical interview at Acme Corp for a Software Engineer role.",
        "talking_points": [
            "Highlight distributed systems experience",
            "Discuss payment-api project impact",
            "Ask about engineering team structure",
            "Mention the ML pipeline project",
        ],
        "expected_questions": [
            {"question": "Tell me about yourself", "suggested_answer": "Focus on your 5 years of Python experience."},
            {"question": "System design: design a URL shortener", "suggested_answer": "Start with requirements."},
            {"question": "Tell me about a hard bug you fixed", "suggested_answer": "Use STAR format."},
            {"question": "Why Acme Corp?", "suggested_answer": "Research their products and mission."},
        ],
        "questions_to_ask": [
            "What does the on-call rotation look like?",
            "How is engineering impact measured?",
            "What are the biggest technical challenges right now?",
            "How does the team handle code review?",
        ],
        "prep_checklist": [
            "Review your resume top-to-bottom",
            "Re-read the job description",
            "Research Acme Corp on LinkedIn",
            "Prepare 3 STAR stories",
            "Test your audio/video setup",
            "Block out 30 minutes before for this prep",
            "Have a glass of water ready",
            "Close distracting tabs",
        ],
        "caveats": [
            "Role title inferred from title — verify before the call",
        ],
    })
    mock_response.content = [mock_block]
    return mock_response
