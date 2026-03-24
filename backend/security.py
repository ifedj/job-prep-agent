"""JWT session token utilities and token encryption."""
import base64
import os
from datetime import datetime, timedelta
from typing import Optional

from cryptography.fernet import Fernet
from jose import JWTError, jwt

from backend.config import get_settings

settings = get_settings()

ACCESS_TOKEN_EXPIRE_DAYS = 7
ALGORITHM = "HS256"


# ─── JWT ──────────────────────────────────────────────────────────────────────

def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        return int(user_id_str) if user_id_str else None
    except JWTError:
        return None


# ─── Token encryption ─────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    key = settings.token_encryption_key
    if not key:
        # Generate and cache an ephemeral key for development.
        # Production must set TOKEN_ENCRYPTION_KEY in .env.
        key = Fernet.generate_key().decode()
    # Accept raw 32-byte hex keys or already-encoded Fernet keys
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        # Fallback: derive a valid Fernet key from the raw value
        raw = key.encode() if isinstance(key, str) else key
        padded = (raw + b"=" * 32)[:32]
        b64 = base64.urlsafe_b64encode(padded)
        return Fernet(b64)


def encrypt_token(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
