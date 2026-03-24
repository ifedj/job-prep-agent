"""All SQLAlchemy ORM models."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Float,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    target_roles = Column(Text)          # JSON array of strings
    background_summary = Column(Text)   # Free-text bio
    key_projects = Column(Text)         # JSON array of strings
    preferences = Column(Text)          # JSON object (e.g. preferred_style)
    resume_raw_text = Column(Text)
    resume_structured = Column(Text)    # JSON: {summary, skills[], experience[], education[]}
    resume_filename = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    oauth_token = relationship("OAuthToken", back_populates="user", uselist=False)
    calendar_events = relationship("CalendarEvent", back_populates="user")
    prep_packs = relationship("PrepPack", back_populates="user")
    email_logs = relationship("EmailDeliveryLog", back_populates="user")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String(50), nullable=False, default="google")
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text)
    token_expiry = Column(DateTime)
    scopes = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="oauth_token")

    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    google_event_id = Column(String(255), nullable=False)
    calendar_id = Column(String(255), nullable=False, default="primary")
    title = Column(Text, nullable=False)
    description = Column(Text)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    attendees = Column(Text)            # JSON array of {email, name, responseStatus}
    organizer_email = Column(String(255))
    location = Column(Text)
    html_link = Column(Text)
    google_updated = Column(DateTime)   # The `updated` field from Google API
    raw_json = Column(Text)
    synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="calendar_events")
    classification = relationship("EventClassification", back_populates="event", uselist=False)
    prep_pack = relationship("PrepPack", back_populates="event", uselist=False)

    __table_args__ = (UniqueConstraint("user_id", "google_event_id", name="uq_user_event"),)


class EventClassification(Base):
    __tablename__ = "event_classifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("calendar_events.id"), unique=True, nullable=False)
    label = Column(String(50), nullable=False)
    # interview | networking | recruiter_screen | company_intro | not_job_related | ambiguous
    confidence = Column(Float, nullable=False)
    reasoning = Column(Text)
    company_name = Column(String(255))
    role_title = Column(String(255))
    classified_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    model_version = Column(String(50), nullable=False, default="")
    user_override = Column(String(50))   # Set when user manually reclassifies
    user_override_at = Column(DateTime)

    event = relationship("CalendarEvent", back_populates="classification")

    @property
    def effective_label(self):
        return self.user_override if self.user_override else self.label


class PrepPack(Base):
    __tablename__ = "prep_packs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("calendar_events.id"), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    meeting_summary = Column(Text)
    talking_points = Column(Text)        # JSON array of strings
    expected_questions = Column(Text)    # JSON array of {question, suggested_answer}
    questions_to_ask = Column(Text)      # JSON array of strings
    prep_checklist = Column(Text)        # JSON array of {item, done: false}
    caveats = Column(Text)               # JSON array of strings
    generation_status = Column(String(20), nullable=False, default="pending")
    # pending | generating | done | failed
    generation_error = Column(Text)
    generated_at = Column(DateTime)
    model_version = Column(String(50), default="")
    content_hash = Column(String(64), default="")

    event = relationship("CalendarEvent", back_populates="prep_pack")
    user = relationship("User", back_populates="prep_packs")
    email_logs = relationship("EmailDeliveryLog", back_populates="prep_pack")


class EmailDeliveryLog(Base):
    __tablename__ = "email_delivery_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    prep_pack_id = Column(Integer, ForeignKey("prep_packs.id"), nullable=False)
    recipient_email = Column(String(255), nullable=False)
    subject = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False, unique=True)
    gmail_message_id = Column(String(255))
    status = Column(String(20), nullable=False)   # sent | failed | skipped_duplicate
    sent_at = Column(DateTime)
    error_message = Column(Text)

    user = relationship("User", back_populates="email_logs")
    prep_pack = relationship("PrepPack", back_populates="email_logs")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sync_type = Column(String(50), nullable=False)  # calendar | classification | prep_gen | email
    status = Column(String(20), nullable=False)      # started | success | failed
    details = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
