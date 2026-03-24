"""Google OAuth 2.0 flow and credential management."""
import os
from datetime import datetime
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import OAuthToken, User
from backend.security import encrypt_token, decrypt_token

settings = get_settings()

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uris": [settings.google_redirect_uri],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


# Store the flow object so the PKCE verifier is preserved across the redirect
_flow_store: dict[str, Flow] = {}


def get_authorization_url() -> tuple[str, str]:
    """Returns (authorization_url, state)."""
    flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    _flow_store[state] = flow
    return url, state


def exchange_code(code: str, state: str) -> Credentials:
    """Exchange authorization code for credentials using the stored flow."""
    flow = _flow_store.pop(state, None)
    if flow is None:
        # Fallback: create a new flow without PKCE (works if server didn't reload)
        flow = Flow.from_client_config(CLIENT_CONFIG, scopes=SCOPES, state=state)
        flow.redirect_uri = settings.google_redirect_uri
    flow.fetch_token(code=code)
    return flow.credentials


def save_tokens(user_id: int, credentials: Credentials, db: Session) -> OAuthToken:
    """Encrypt and persist tokens for a user."""
    expiry = credentials.expiry  # datetime or None
    token_row = db.query(OAuthToken).filter(
        OAuthToken.user_id == user_id,
        OAuthToken.provider == "google",
    ).first()

    encrypted_access = encrypt_token(credentials.token)
    encrypted_refresh = encrypt_token(credentials.refresh_token) if credentials.refresh_token else None
    scopes_str = " ".join(credentials.scopes or SCOPES)

    if token_row:
        token_row.access_token = encrypted_access
        if encrypted_refresh:
            token_row.refresh_token = encrypted_refresh
        token_row.token_expiry = expiry
        token_row.scopes = scopes_str
        token_row.updated_at = datetime.utcnow()
    else:
        token_row = OAuthToken(
            user_id=user_id,
            provider="google",
            access_token=encrypted_access,
            refresh_token=encrypted_refresh,
            token_expiry=expiry,
            scopes=scopes_str,
        )
        db.add(token_row)

    db.commit()
    db.refresh(token_row)
    return token_row


def get_credentials(user_id: int, db: Session) -> Optional[Credentials]:
    """Load, refresh if needed, and return live Google credentials."""
    token_row = db.query(OAuthToken).filter(
        OAuthToken.user_id == user_id,
        OAuthToken.provider == "google",
    ).first()
    if not token_row:
        return None

    access_token = decrypt_token(token_row.access_token)
    refresh_token = decrypt_token(token_row.refresh_token) if token_row.refresh_token else None

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=token_row.scopes.split(" ") if token_row.scopes else SCOPES,
    )
    if token_row.token_expiry:
        creds.expiry = token_row.token_expiry

    # Refresh if expired (or expiring within 5 minutes)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_tokens(user_id, creds, db)
        except Exception as e:
            print(f"[oauth] Token refresh failed for user {user_id}: {e}")
            return None

    return creds
