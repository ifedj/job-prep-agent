"""Gmail API client — read threads and send messages."""
import base64
import json
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from backend.services.oauth import get_credentials


def _build_service(user_id: int, db: Session):
    creds = get_credentials(user_id, db)
    if not creds:
        raise ValueError(f"No valid Gmail credentials for user {user_id}")
    return build("gmail", "v1", credentials=creds)


def get_email_context_for_event(
    user_id: int,
    db: Session,
    attendee_emails: list[str],
    days_back: int = 30,
) -> str:
    """Search Gmail for emails related to an event's attendees.

    Returns a short text summary (truncated) for use in LLM prompts.
    """
    if not attendee_emails:
        return ""
    try:
        service = _build_service(user_id, db)
        # Build a query from attendee email addresses
        email_parts = " OR ".join(
            f"from:{e} OR to:{e}" for e in attendee_emails[:5]
        )
        query = f"({email_parts}) newer_than:{days_back}d"

        result = service.users().messages().list(
            userId="me", q=query, maxResults=10
        ).execute()

        messages = result.get("messages", [])
        snippets = []
        for msg in messages[:5]:
            m = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            snippet = m.get("snippet", "")
            snippets.append(
                f"Subject: {headers.get('Subject', '(no subject)')}\n"
                f"From: {headers.get('From', '')}\n"
                f"Snippet: {snippet[:200]}"
            )

        return "\n\n".join(snippets)[:3000]  # Cap at 3000 chars

    except Exception as e:
        print(f"[gmail] Could not fetch email context: {e}")
        return ""


def send_email(
    user_id: int,
    db: Session,
    to: str,
    subject: str,
    html_body: str,
    plain_body: str,
) -> str:
    """Send an email via Gmail API. Returns the gmail message_id."""
    service = _build_service(user_id, db)

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    return sent.get("id", "")
