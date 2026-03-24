"""User profile management routes."""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.deps import get_current_user, get_optional_user
from backend.models import User
from backend.schemas import ProfileRead, ProfileUpdate
from backend.security import create_access_token
from backend.services.resume_parser import extract_text_from_pdf, structure_resume

router = APIRouter()


def _serialize_user(user: User) -> ProfileRead:
    return ProfileRead(
        id=user.id,
        email=user.email,
        name=user.name,
        target_roles=json.loads(user.target_roles) if user.target_roles else [],
        background_summary=user.background_summary,
        key_projects=json.loads(user.key_projects) if user.key_projects else [],
        preferences=json.loads(user.preferences) if user.preferences else {},
        resume_filename=user.resume_filename,
        has_resume=bool(user.resume_raw_text),
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("", response_model=ProfileRead)
def get_profile(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


@router.post("", response_model=ProfileRead)
def create_or_update_profile(
    body: ProfileUpdate,
    session_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
    response: Response = None,
):
    """Create profile if none exists, or update existing. Returns session cookie."""
    # Try to find existing user by email
    user = None
    if session_token:
        from backend.security import decode_access_token
        uid = decode_access_token(session_token)
        if uid:
            user = db.query(User).filter(User.id == uid).first()

    if user is None and body.email:
        user = db.query(User).filter(User.email == body.email).first()

    if user is None:
        if not body.email:
            raise HTTPException(status_code=400, detail="Email required for new profile")
        user = User(email=body.email)
        db.add(user)
        db.flush()

    if body.name is not None:
        user.name = body.name
    if body.email is not None:
        user.email = body.email
    if body.target_roles is not None:
        user.target_roles = json.dumps(body.target_roles)
    if body.background_summary is not None:
        user.background_summary = body.background_summary
    if body.key_projects is not None:
        user.key_projects = json.dumps(body.key_projects)
    if body.preferences is not None:
        user.preferences = json.dumps(body.preferences)

    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    result = _serialize_user(user)

    # Issue session cookie if not already authenticated
    if not session_token:
        token = create_access_token(user.id)
        response.set_cookie(
            key="session_token",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )

    return result


@router.patch("", response_model=ProfileRead)
def patch_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.name is not None:
        current_user.name = body.name
    if body.email is not None:
        current_user.email = body.email
    if body.target_roles is not None:
        current_user.target_roles = json.dumps(body.target_roles)
    if body.background_summary is not None:
        current_user.background_summary = body.background_summary
    if body.key_projects is not None:
        current_user.key_projects = json.dumps(body.key_projects)
    if body.preferences is not None:
        current_user.preferences = json.dumps(body.preferences)

    current_user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


@router.post("/resume", response_model=ProfileRead)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10 MB cap
        raise HTTPException(status_code=400, detail="Resume file too large (max 10 MB)")

    raw_text = extract_text_from_pdf(contents)
    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from the PDF")

    structured = structure_resume(raw_text)

    current_user.resume_raw_text = raw_text
    current_user.resume_structured = json.dumps(structured)
    current_user.resume_filename = file.filename
    current_user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current_user)

    return _serialize_user(current_user)


@router.delete("/resume")
def delete_resume(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.resume_raw_text = None
    current_user.resume_structured = None
    current_user.resume_filename = None
    current_user.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Resume deleted"}
