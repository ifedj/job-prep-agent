"""Google OAuth routes."""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import get_db
from backend.deps import get_optional_user
from backend.models import OAuthToken, User
from backend.schemas import AuthStatus
from backend.security import create_access_token
from backend.services.oauth import exchange_code, get_authorization_url, save_tokens

router = APIRouter()
_settings = get_settings()


def _make_state() -> str:
    """Generate a signed OAuth state token that survives across serverless instances."""
    nonce = secrets.token_hex(16)
    ts = str(int(time.time()))
    msg = f"{nonce}.{ts}"
    sig = hmac.new(_settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def _verify_state(state: str) -> bool:
    """Verify state signature and freshness (10-minute window)."""
    try:
        msg, sig = state.rsplit(".", 1)
        ts_str = msg.split(".")[-1]
        expected = hmac.new(_settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        if int(time.time()) - int(ts_str) > 600:
            return False
        return True
    except Exception:
        return False


@router.get("/google/start")
def google_start(db: Session = Depends(get_db)):
    """Redirect directly to Google OAuth authorization URL."""
    state = _make_state()
    url, _ = get_authorization_url(state=state)
    return RedirectResponse(url=url)


@router.get("/google/callback")
def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Handle Google OAuth callback."""
    if error:
        return RedirectResponse(url=f"/?error={error}")

    if not _verify_state(state):
        return RedirectResponse(url="/?error=invalid_state")

    try:
        credentials = exchange_code(code, state)
    except Exception as e:
        print(f"[auth] OAuth exchange error: {e}")
        return RedirectResponse(url=f"/?error=oauth_exchange_failed")

    # Get user info from Google
    try:
        oauth2_service = build("oauth2", "v2", credentials=credentials)
        user_info = oauth2_service.userinfo().get().execute()
        google_email = user_info.get("email", "")
        google_name = user_info.get("name", "")
    except Exception:
        google_email = ""
        google_name = ""

    # Find or create the user
    user = db.query(User).filter(User.email == google_email).first() if google_email else None
    if user is None:
        user = db.query(User).first()  # Single-user fallback
    if user is None:
        user = User(email=google_email, name=google_name)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.email and google_email:
        user.email = google_email
        user.name = google_name or user.name
        db.commit()

    save_tokens(user.id, credentials, db)

    token = create_access_token(user.id)
    response = RedirectResponse(url="/dashboard")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.get("/demo")
def demo_login(db: Session = Depends(get_db)):
    """Log in instantly as the demo user (no Google OAuth required)."""
    user = db.query(User).filter(User.email == "demo@jobprepagent.com").first()
    if user is None:
        user = User(email="demo@jobprepagent.com", name="Demo User")
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(user.id)
    response = RedirectResponse(url="/")
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.post("/logout")
def logout():
    # Use JSONResponse directly — FastAPI Response injection fails on Vercel serverless
    resp = JSONResponse(content={"message": "Logged out"})
    resp.delete_cookie("session_token")
    return resp


@router.get("/me", response_model=AuthStatus)
def get_auth_status(
    session_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    from backend.security import decode_access_token

    if not session_token:
        return AuthStatus(is_authenticated=False, google_connected=False)

    user_id = decode_access_token(session_token)
    if user_id is None:
        return AuthStatus(is_authenticated=False, google_connected=False)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return AuthStatus(is_authenticated=False, google_connected=False)

    token_row = db.query(OAuthToken).filter(
        OAuthToken.user_id == user_id,
        OAuthToken.provider == "google",
    ).first()

    return AuthStatus(
        is_authenticated=True,
        google_connected=token_row is not None,
        user_id=user.id,
        email=user.email,
        scopes=token_row.scopes.split(" ") if token_row else None,
    )
