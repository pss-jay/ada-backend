"""
JWT Authentication Service for Ada Platform.

Uses Python stdlib only (hmac, hashlib, base64, json) — no PyJWT dependency.
Passwords hashed with PBKDF2-SHA256 via hashlib.
"""

import hmac
import hashlib
import base64
import json
import time
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "ada-default-secret-CHANGE-IN-PRODUCTION")
JWT_EXPIRY_SECONDS = int(os.getenv("JWT_EXPIRY_SECONDS", "86400"))  # 24 hours


# ---------------------------------------------------------------------------
# Base64url helpers (RFC 7515)
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padding = 4 - (len(data) % 4)
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_token(username: str, email: str, role: str = "operator", full_name: str = "") -> str:
    """Create a HS256 JWT token."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": username,
        "email": email,
        "role": role,
        "name": full_name,
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
    }
    h_enc = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p_enc = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(JWT_SECRET.encode(), f"{h_enc}.{p_enc}".encode(), hashlib.sha256).digest()
    return f"{h_enc}.{p_enc}.{_b64url_encode(sig)}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify a JWT token. Returns the payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        h_enc, p_enc, s_enc = parts

        expected_sig = hmac.new(
            JWT_SECRET.encode(), f"{h_enc}.{p_enc}".encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(s_enc, _b64url_encode(expected_sig)):
            logger.warning("JWT signature mismatch")
            return None

        payload = json.loads(_b64url_decode(p_enc).decode())
        if payload.get("exp", 0) < time.time():
            logger.info(f"JWT expired for user {payload.get('sub')}")
            return None
        return payload
    except Exception as e:
        logger.warning(f"JWT verification failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Password hashing  (PBKDF2-SHA256, stdlib)
# ---------------------------------------------------------------------------
_PW_ITERATIONS = 150_000


def hash_password(password: str) -> str:
    """Return 'pbkdf2:sha256:<iter>$<salt_b64>$<key_b64>'."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PW_ITERATIONS)
    return (
        f"pbkdf2:sha256:{_PW_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(key).decode()}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored PBKDF2 hash."""
    try:
        meta, salt_b64, key_b64 = stored_hash.split("$", 2)
        iterations = int(meta.split(":")[2])
        salt = base64.b64decode(salt_b64)
        stored_key = base64.b64decode(key_b64)
        computed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return hmac.compare_digest(computed, stored_key)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


# ---------------------------------------------------------------------------
# Auth context helper
# ---------------------------------------------------------------------------
class AuthContext:
    """Extracted identity from a verified JWT."""

    __slots__ = ("username", "email", "role", "full_name")

    def __init__(self, username: str, email: str, role: str, full_name: str = ""):
        self.username = username
        self.email = email
        self.role = role
        self.full_name = full_name

    def is_admin(self) -> bool:
        return self.role == "admin"


def extract_auth_from_header(auth_header: str) -> Optional[AuthContext]:
    """Parse 'Bearer <token>' and return an AuthContext or None."""
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    payload = verify_token(parts[1])
    if not payload:
        return None
    return AuthContext(
        username=payload.get("sub", ""),
        email=payload.get("email", ""),
        role=payload.get("role", "operator"),
        full_name=payload.get("name", ""),
    )
