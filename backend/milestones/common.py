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

from db import execute, query, query_one

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


# ---------- Maker-checker approvals ----------

# Fields whose changes must be approved by a second person (an admin other than the requester).
SENSITIVE_FIELDS = {
    "project": {"planned_budget": "budget", "budget_hours": "hour budget", "manager_id": "project manager"},
    "user": {"pay_rate": "pay rate", "manager_id": "manager", "role": "role"},
}


def pending_changes(entity_type, entity_id):
    return query(
        "SELECT * FROM change_requests WHERE entity_type = %s AND entity_id = %s AND status = 'pending' "
        "ORDER BY requested_at",
        (entity_type, entity_id),
    )


def request_change(viewer, entity_type, entity, proposed, reason):
    """Record a pending change instead of applying it. `proposed` holds only fields that really change."""
    if not proposed:
        return None
    labels = SENSITIVE_FIELDS[entity_type]
    reason = (reason or "").strip()
    if len(reason) < 5:
        what = " and ".join(labels[k] for k in proposed)
        raise ApiError(400, f"Add a reason for the {what} change. It goes to the approving admin.")
    for p in pending_changes(entity_type, entity["id"]):
        overlap = set(p["payload"]) & set(proposed)
        if overlap:
            raise ApiError(409, f"A {' and '.join(labels[k] for k in overlap)} change is already waiting for "
                                "approval. Cancel it or wait for a decision first.")
    previous = {k: entity.get(k) for k in proposed}
    row = execute(
        """
        INSERT INTO change_requests (kind, entity_type, entity_id, entity_name, payload, previous, reason, requested_by)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING *
        """,
        (
            f"{entity_type}.{'+'.join(sorted(proposed))}", entity_type, entity["id"], entity.get("name"),
            json.dumps(proposed, default=_json_default), json.dumps(previous, default=_json_default),
            reason[:1000], viewer["id"],
        ),
    )
    audit(viewer, "approval.request", entity_type, entity["id"], entity.get("name"), {
        "request_id": row["id"], "reason": reason[:1000],
        "changes": {k: [previous[k], v] for k, v in proposed.items()},
    })
    return row


def _same(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a if a is not None else "") == str(b if b is not None else "")


def split_sensitive(entity_type, current, cleaned):
    """Split validated new values into (apply_now, needs_approval), dropping values that don't change."""
    now, later = {}, {}
    for k, v in cleaned.items():
        if k in SENSITIVE_FIELDS[entity_type]:
            if not _same(current.get(k), v):
                later[k] = v
        else:
            now[k] = v
    return now, later


def list_change_requests(viewer, params):
    """Admins see everything; everyone else sees the requests they made."""
    where, args = [], []
    if viewer["role"] != "admin" or str(params.get("mine", "")).lower() == "true":
        where.append("r.requested_by = %s"); args.append(viewer["id"])
    if params.get("status") in ("pending", "approved", "rejected", "cancelled"):
        where.append("r.status = %s"); args.append(params["status"])
    if params.get("entity_id"):
        where.append("r.entity_id = %s"); args.append(params["entity_id"])
    rows = query(
        f"""
        SELECT r.*, rq.name AS requested_by_name, rq.role AS requested_by_role, dc.name AS decided_by_name
        FROM change_requests r
        JOIN users rq ON rq.id = r.requested_by
        LEFT JOIN users dc ON dc.id = r.decided_by
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY (r.status = 'pending') DESC, r.requested_at DESC
        LIMIT 200
        """,
        args,
    )
    # Show people's names instead of IDs for manager changes.
    ids = {str(v) for r in rows for side in (r["payload"], r["previous"]) for k, v in side.items()
           if k == "manager_id" and v}
    names = {}
    if ids:
        names = {str(u["id"]): u["name"] for u in query("SELECT id, name FROM users WHERE id = ANY(%s::uuid[])", (list(ids),))}
    for r in rows:
        r["changes"] = [
            {"field": k, "label": SENSITIVE_FIELDS[r["entity_type"]].get(k, k),
             "from": names.get(str(r["previous"].get(k)), r["previous"].get(k)) if k == "manager_id" else r["previous"].get(k),
             "to": names.get(str(v), v) if k == "manager_id" else v}
            for k, v in r["payload"].items()
        ]
        r["can_decide"] = viewer["role"] == "admin" and r["status"] == "pending" and str(r["requested_by"]) != str(viewer["id"])
        r["can_cancel"] = r["status"] == "pending" and str(r["requested_by"]) == str(viewer["id"])
    pending_for_me = 0
    if viewer["role"] == "admin":
        pending_for_me = query_one(
            "SELECT COUNT(*) AS n FROM change_requests WHERE status = 'pending' AND requested_by <> %s", (viewer["id"],)
        )["n"]
    return {"requests": rows, "waiting_for_you": pending_for_me}


def decide_change_request(viewer, request_id, action, body, entity_type, apply_fn):
    """approve / reject / cancel a request. apply_fn(approver, request) performs an approved change."""
    req = query_one("SELECT * FROM change_requests WHERE id = %s", (request_id,))
    if not req:
        raise ApiError(404, "Request not found")
    if req["status"] != "pending":
        raise ApiError(409, f"This request was already {req['status']}")
    note = (body.get("note") or "").strip()[:1000] or None

    if action == "cancel":
        if str(req["requested_by"]) != str(viewer["id"]):
            raise ApiError(403, "Only the person who asked for this change can cancel it")
        execute("UPDATE change_requests SET status = 'cancelled', decided_at = now() WHERE id = %s AND status = 'pending'",
                (req["id"],))
        audit(viewer, "approval.cancel", req["entity_type"], req["entity_id"], req["entity_name"], {"request_id": req["id"]})
        return query_one("SELECT * FROM change_requests WHERE id = %s", (req["id"],))

    if viewer["role"] != "admin":
        raise ApiError(403, "Only an admin can approve or reject changes")
    if str(req["requested_by"]) == str(viewer["id"]):
        raise ApiError(403, "You cannot approve or reject your own request. Another admin has to review it.")

    if action == "reject":
        if not note:
            raise ApiError(400, "Add a note explaining why the change is rejected")
        execute(
            "UPDATE change_requests SET status = 'rejected', decided_by = %s, decided_at = now(), decision_note = %s "
            "WHERE id = %s AND status = 'pending'",
            (viewer["id"], note, req["id"]),
        )
        audit(viewer, "approval.reject", req["entity_type"], req["entity_id"], req["entity_name"],
              {"request_id": req["id"], "note": note})
        return query_one("SELECT * FROM change_requests WHERE id = %s", (req["id"],))

    if req["entity_type"] != entity_type:
        raise ApiError(400, f"Approve {req['entity_type']} changes through the {req['entity_type']}s service")
    claimed = execute(
        "UPDATE change_requests SET status = 'approved', decided_by = %s, decided_at = now(), decision_note = %s "
        "WHERE id = %s AND status = 'pending' RETURNING *",
        (viewer["id"], note, req["id"]),
    )
    if not claimed or isinstance(claimed, int):
        raise ApiError(409, "Another admin decided this request first")
    try:
        apply_fn(viewer, claimed)
    except ApiError as e:
        execute("UPDATE change_requests SET status = 'pending', decided_by = NULL, decided_at = NULL, "
                "decision_note = NULL WHERE id = %s", (req["id"],))
        raise ApiError(409, f"The change can no longer be applied: {e.message} Reject it instead.")
    audit(viewer, "approval.approve", req["entity_type"], req["entity_id"], req["entity_name"], {
        "request_id": req["id"], "requested_by": str(req["requested_by"]), "note": note,
        "changes": {k: [req["previous"].get(k), v] for k, v in req["payload"].items()},
    })
    return query_one("SELECT * FROM change_requests WHERE id = %s", (req["id"],))


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