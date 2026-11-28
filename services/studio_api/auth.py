"""Authentication and authorization service for StoryClip Studio.

Provides:
- Argon2id password hashing and verification
- Signed JWT session tokens via HttpOnly cookies
- Double-submit CSRF cookie protection
- Fast single-use ticket generator for WebSockets
"""
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Query, Request, Response, status

from services.studio_api.db import get_db

# JWT & Session configuration: persist key across restarts via env or fallback to stable secret
SECRET_KEY = os.getenv("STUDIO_SECRET_KEY")
if not SECRET_KEY:
    # Read or generate persistent local secret in storage/studio.key
    key_path = os.path.join(os.path.dirname(__file__), "..", "..", "storage", "studio.key")
    try:
        if os.path.exists(key_path):
            with open(key_path, "r", encoding="utf-8") as f:
                SECRET_KEY = f.read().strip()
        if not SECRET_KEY:
            SECRET_KEY = secrets.token_hex(32)
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(SECRET_KEY)
    except Exception:
        SECRET_KEY = "storyclip-studio-default-dev-secret-key-change-in-prod"

JWT_ALGORITHM = "HS256"
SESSION_COOKIE_NAME = "storyclip_session"
CSRF_COOKIE_NAME = "csrf_token"
SESSION_DURATION_SECONDS = 7 * 24 * 3600  # 7 days

# Argon2id password hasher with production parameters
ph = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Hash plaintext password using Argon2id."""
    return ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against Argon2id hash."""
    try:
        return ph.verify(hashed, password)
    except (VerifyMismatchError, Exception):
        return False


def create_session_token(user_id: str, username: str, role: str) -> str:
    """Generate signed JWT session token."""
    now = time.time()
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + SESSION_DURATION_SECONDS,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict:
    """Validate and decode JWT session token."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token")


def set_auth_cookies(response: Response, user_id: str, username: str, role: str) -> tuple[str, str]:
    """Set HttpOnly session cookie and JavaScript-readable CSRF token cookie. Returns (token, csrf_token)."""
    token = create_session_token(user_id, username, role)
    csrf_token = secrets.token_hex(24)

    # HttpOnly, SameSite=Lax session cookie
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True when served over HTTPS in production
        path="/",
    )

    # JS-readable CSRF token cookie
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=False,
        samesite="lax",
        secure=False,
        path="/",
    )
    return token, csrf_token


def clear_auth_cookies(response: Response):
    """Clear session and CSRF cookies on logout."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def _authenticate_proxy_headers(
    user_id: Optional[str],
    username: Optional[str],
    ts: Optional[str],
    signature: Optional[str],
) -> Optional[dict]:
    """Verify HMAC-signed trusted proxy headers from the Next.js better-auth frontend."""
    if not (user_id and ts and signature):
        return None
    secret = os.getenv("STUDIO_PROXY_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="STUDIO_PROXY_SECRET is not configured on studio_api",
        )
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid proxy timestamp")
    if abs(time.time() - ts_int) > 60:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired proxy signature")

    expected = hmac.new(secret.encode("utf-8"), f"{user_id}:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid proxy signature")

    display_name = (username or user_id).strip() or user_id
    with get_db() as conn:
        row = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            # Ensure a shadow operator row exists so render_jobs/ws_tickets foreign keys succeed
            candidate_username = display_name
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (candidate_username,)).fetchone():
                candidate_username = f"{display_name}_{user_id[:8]}"
            conn.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash, role) VALUES (?, ?, 'better-auth-proxy', 'admin')",
                (user_id, candidate_username),
            )
            conn.commit()
            return {"id": user_id, "username": candidate_username, "role": "admin"}
        return {"id": row["id"], "username": row["username"], "role": row["role"]}


def get_current_user(
    request: Request,
    storyclip_session: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_csrf_token: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    x_studio_user_id: Optional[str] = Header(None),
    x_studio_username: Optional[str] = Header(None),
    x_studio_ts: Optional[str] = Header(None),
    x_studio_signature: Optional[str] = Header(None),
) -> dict:
    """FastAPI dependency: Authenticates via HMAC proxy headers or JWT cookie/Bearer token."""
    proxy_user = _authenticate_proxy_headers(x_studio_user_id, x_studio_username, x_studio_ts, x_studio_signature)
    if proxy_user is not None:
        return proxy_user

    active_token = storyclip_session or token
    if not active_token and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            active_token = parts[1]

    if not active_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_session_token(active_token)
    user_id = payload.get("sub")

    with get_db() as conn:
        row = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account not found")

    # Double-submit CSRF protection for non-idempotent HTTP methods
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
        # If authenticated via bearer header alone without cookie, CSRF is not required (header authentication is CSRF-immune)
        # If cookie is present or CSRF header is provided, validate token
        if csrf_cookie or not authorization:
            if not csrf_cookie or not x_csrf_token or not secrets.compare_digest(csrf_cookie, x_csrf_token):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token validation failed")

    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def issue_ws_ticket(user_id: str) -> str:
    """Generate a 30-second single-use ticket for WebSocket authentication."""
    ticket = str(uuid.uuid4())
    now = time.time()
    with get_db() as conn:
        # Purge stale tickets
        conn.execute("DELETE FROM ws_tickets WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO ws_tickets (ticket, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     (ticket, user_id, now, now + 30.0))
        conn.commit()
    return ticket


def consume_ws_ticket(ticket: str) -> Optional[str]:
    """Validate and immediately delete a single-use WebSocket ticket."""
    now = time.time()
    with get_db() as conn:
        row = conn.execute("SELECT user_id, expires_at FROM ws_tickets WHERE ticket = ?", (ticket,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM ws_tickets WHERE ticket = ?", (ticket,))
        conn.commit()

        if row["expires_at"] < now:
            return None
        return row["user_id"]
