"""FastAPI dependency injectors."""
from typing import Optional
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import User
from backend.security import decode_access_token


def get_current_user(
    session_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user_id = decode_access_token(session_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session token",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def get_optional_user(
    session_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if not session_token:
        return None
    user_id = decode_access_token(session_token)
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id).first()
