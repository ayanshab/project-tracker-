"""
Shared helpers for every service: HTTP event parsing, JSON responses,
password hashing, tokens, and the current-user lookup.
Copy this file (with db.py) into every service folder.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
from datetime import date, datetime
from decimal import Decimal

from db import execute, query_one

logger = logging.getLogger()


def _auth_secret():
    """Signing key for login tokens.

    Uses AUTH_SECRET when set. Otherwise derives a key from the database
    credentials, which every service already receives as secret environment
    variables, so no signing key ever lives in source code."""
    explicit = os.getenv("AUTH_SECRET")
    if explicit:
        return explicit.encode()
    seed = "|".join(os.getenv(k) or "" for k in ("POSTGRES_PASS", "POSTGRES_USER", "POSTGRES_HOST", "POSTGRES_NAME"))
    return hashlib.sha256(f"project-tracker-auth:{seed}".encode()).digest()


AUTH_SECRET = _auth_secret()
TOKEN_TTL_SECONDS = 8 * 60 * 60

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}


# ---------- JSON responses ----------

def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def response(status, body=None):
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body if body is not None else {}, default=_json_default),
    }


def error(status, message):
    return response(status, {"error": message})


class ApiError(Exception):
    """Raise anywhere in a handler to return a clean error response."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- Event parsing ----------

def parse_event(event, service_name):
    """
    Normalize a Lambda Function URL / API Gateway event into
    (method, path, body_dict, headers_dict, query_dict).

    The path has the '/api/<service_name>' prefix stripped, so handlers
    route on paths like '/login' or '/me'.
    """
    event = event or {}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    ctx = event.get("requestContext") or {}
    forwarded = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    _REQUEST.clear()
    _REQUEST.update(
        ip=forwarded or (ctx.get("http") or {}).get("sourceIp") or (ctx.get("identity") or {}).get("sourceIp"),
        user_agent=(headers.get("user-agent") or "")[:300] or None,
    )
    method = (
        (ctx.get("http") or {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()

    path = event.get("rawPath") or event.get("path") or "/"
    for prefix in ("/api", f"/{service_name}"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    path = "/" + path.strip("/")

    raw = event.get("body") or ""
    if event.get("isBase64Encoded") and raw:
        raw = base64.b64decode(raw).decode("utf-8")
    body = {}
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "Request body must be valid JSON")

    query = event.get("queryStringParameters") or {}
    return method, path, body, headers, query


def require_fields(body, *fields):
    missing = [f for f in fields if body.get(f) in (None, "")]
    if missing:
        raise ApiError(400, f"Missing required field(s): {', '.join(missing)}")


# ---------- Passwords and usernames ----------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def generate_username(length=10):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_password(length=14):
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------- Tokens ----------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user):
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    data = _b64(json.dumps(payload).encode())
    sig = hmac.new(AUTH_SECRET, data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def parse_token(token):
    try:
        data, sig = token.split(".")
    except ValueError:
        return None
    expected = hmac.new(AUTH_SECRET, data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    payload = json.loads(_unb64(data))
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ---------- Current user ----------

PUBLIC_USER_COLUMNS = (
    "id, employee_number, username, name, email, job_title, role, "
    "manager_id, pay_rate, is_archived, must_change_password, created_at, updated_at"
)


def public_user(row):
    """Strip the password hash before returning a user to a client."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k != "password_hash"}


def get_current_user(headers, allow_pending=False):
    """Return the logged-in user row or raise 401.

    Users still on a temporary password are blocked from everything except
    viewing their own account and changing the password (allow_pending=True)."""
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ApiError(401, "Missing bearer token")
    payload = parse_token(auth[7:].strip())
    if not payload:
        raise ApiError(401, "Invalid or expired token")
    user = query_one(
        f"SELECT {PUBLIC_USER_COLUMNS} FROM users WHERE id = %s AND is_archived = FALSE",
        (payload["sub"],),
    )
    if not user:
        raise ApiError(401, "User not found or archived")
    if user["must_change_password"] and not allow_pending:
        raise ApiError(403, "Change your temporary password before continuing")
    return user


def require_role(user, *roles):
    if user["role"] not in roles:
        raise ApiError(403, f"Requires role: {', '.join(roles)}")


# ---------- Audit log ----------

_REQUEST = {}  # source IP and user agent of the request being handled (one per Lambda invocation)


def audit(actor, action, entity_type, entity_id=None, entity_name=None, details=None):
    """Append one entry to the audit log. Never record passwords or tokens here.

    actor: the user row performing the action (or None for anonymous events such as a failed login).
    action: a short verb phrase like 'project.create' or 'user.archive'.
    """
    try:
        execute(
            """
            INSERT INTO audit_log (actor_id, actor_name, actor_role, action, entity_type, entity_id,
                                   entity_name, details, source_ip, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            """,
            (
                actor["id"] if actor else None,
                actor["name"] if actor else None,
                actor["role"] if actor else None,
                action, entity_type,
                str(entity_id) if entity_id is not None else None,
                entity_name,
                json.dumps(details or {}, default=_json_default),
                _REQUEST.get("ip"), _REQUEST.get("user_agent"),
            ),
        )
    except Exception:  # noqa: BLE001 - auditing must never hide the result of the action itself
        logger.exception("Failed to write audit log entry for %s", action)


def changes(before, body, fields):
    """{field: [old, new]} for the fields in body that actually change."""
    out = {}
    for f in fields:
        if f in body:
            old, new = before.get(f), body[f]
            try:
                same = float(old) == float(new)
            except (TypeError, ValueError):
                same = str(old if old is not None else "").strip() == str(new if new is not None else "").strip()
            if not same:
                out[f] = [old, new]
    return out