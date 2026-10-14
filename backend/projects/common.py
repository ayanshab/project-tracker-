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

from db import query_one

logger = logging.getLogger()

AUTH_SECRET = os.getenv("AUTH_SECRET", "dev-only-secret-change-me").encode()
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
    "manager_id, pay_rate, is_archived, created_at, updated_at"
)


def public_user(row):
    """Strip the password hash before returning a user to a client."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k != "password_hash"}


def get_current_user(headers):
    """Return the logged-in user row or raise 401."""
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
    return user


def require_role(user, *roles):
    if user["role"] not in roles:
        raise ApiError(403, f"Requires role: {', '.join(roles)}")