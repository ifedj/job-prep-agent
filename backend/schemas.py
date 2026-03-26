"""All Pydantic schemas for request/response validation."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr


# ─── User / Profile ───────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    target_roles: Optional[List[str]] = None
    background_summary: Optional[str] = None
    key_projects: Optional[List[str]] = None
    preferences: Optional[Dict[str, Any]] = None


class ProfileRead(BaseModel):
    id: int
    email: str
    name: Optional[str]
    target_roles: Optional[List[str]]
    background_summary: Optional[str]
    key_projects: Optional[List[str]]
    preferences: Optional[Dict[str, Any]]
    resume_filename: Optional[str]
    has_resume: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ─── Auth ─────────────────────────────────────────────────────────────────────

class AuthStatus(BaseModel):
    is_authenticated: bool
    google_connected: bool
    user_id: Optional[int] = None
    email: Optional[str] = None
    scopes: Optional[List[str]] = None


# ─── Calendar Events ──────────────────────────────────────────────────────────

class AttendeeInfo(BaseModel):
    email: str
    name: Optional[str] = None
    response_status: Optional[str] = None


class ClassificationRead(BaseModel):
    label: str
    effective_label: str
    confidence: float
    reasoning: Optional[str]
    company_name: Optional[str]
    role_title: Optional[str]
    classified_at: datetime
    user_override: Optional[str]

    class Config:
        from_attributes = True


class EventRead(BaseModel):
    id: int
    google_event_id: str
    title: str
    description: Optional[str]
    start_time: datetime
    end_time: datetime
    attendees: Optional[List[AttendeeInfo]]
    organizer_email: Optional[str]
    location: Optional[str]
    html_link: Optional[str]
    synced_at: datetime
    classification: Optional[ClassificationRead]
    has_prep_pack: bool
    prep_pack_status: Optional[str]
    prep_pack_id: Optional[int] = None

    class Config:
        from_attributes = True


class ManualLabelRequest(BaseModel):
    label: str  # Must be one of the valid labels


# ─── Review Queue ─────────────────────────────────────────────────────────────

class ReviewDecision(BaseModel):
    label: str
    notes: Optional[str] = None
    generate_prep: bool = True


# ─── Prep Packs ───────────────────────────────────────────────────────────────

class ChecklistItem(BaseModel):
    item: str
    done: bool = False


class ExpectedQuestion(BaseModel):
    question: str
    suggested_answer: str


class PrepPackRead(BaseModel):
    id: int
    event_id: int
    meeting_summary: Optional[str]
    talking_points: Optional[List[str]]
    expected_questions: Optional[List[ExpectedQuestion]]
    questions_to_ask: Optional[List[str]]
    prep_checklist: Optional[List[ChecklistItem]]
    caveats: Optional[List[str]]
    generation_status: str
    generation_error: Optional[str]
    generated_at: Optional[datetime]
    model_version: Optional[str]

    class Config:
        from_attributes = True


# ─── Email Delivery ───────────────────────────────────────────────────────────

class EmailLogRead(BaseModel):
    id: int
    recipient_email: str
    subject: str
    status: str
    sent_at: Optional[datetime]
    error_message: Optional[str]

    class Config:
        from_attributes = True


# ─── Sync ─────────────────────────────────────────────────────────────────────

class SyncStatus(BaseModel):
    last_sync_at: Optional[datetime]
    events_in_db: int
    next_run_in_seconds: Optional[float]
    google_connected: bool
