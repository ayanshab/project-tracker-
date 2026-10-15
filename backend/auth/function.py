"""
Auth service: login, current user, change password.

Routes (all under /api/auth):
    GET  /health            -> service and database check
    POST /login             -> {username, password} => {token, user}
    GET  /me                -> current user (Authorization: Bearer <token>)
    POST /change-password   -> {current_password, new_password}
    POST /logout            -> client discards token (stateless, always 200)

On first run it creates the schema and seeds an admin account
(username 'admin', password from ADMIN_PASSWORD env var, default 'Admin123!').
"""

import logging
import os

from common import (
    ApiError,
    audit,
    error,
    get_current_user,
    hash_password,
    make_token,
    parse_event,
    public_user,
    require_fields,
    response,
    verify_password,
)
from db import DatabaseUnavailable, ensure_schema, execute, query_one

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SERVICE_NAME = "auth"
_SEEDED = False


def seed_admin():
    """Create the first admin if the users table is empty."""
    global _SEEDED
    if _SEEDED:
        return
    count = query_one("SELECT COUNT(*) AS n FROM users")["n"]
    if count == 0:
        # In the cloud, the built-in default password must be changed at first sign-in.
        force_change = os.getenv("IS_LOCAL") == "false" and not os.getenv("ADMIN_PASSWORD")
        execute(
            """
            INSERT INTO users (username, password_hash, name, email, job_title, role, must_change_password)
            VALUES (%s, %s, %s, %s, %s, 'admin', %s)
            """,
            (
                "admin",
                hash_password(os.getenv("ADMIN_PASSWORD", "Admin123!")),
                "System Administrator",
                "admin@acme.local",
                "Administrator",
                force_change,
            ),
        )
        logger.info("Seeded initial admin account")
    _SEEDED = True


def login(body):
    require_fields(body, "username", "password")
    user = query_one(
        "SELECT * FROM users WHERE username = %s", (body["username"].strip(),)
    )
    if not user or not verify_password(body["password"], user["password_hash"]):
        audit(user if user else None, "auth.login_failed", "user", user and user["id"],
              user["name"] if user else None, {"username": body["username"].strip()[:100]})
        raise ApiError(401, "Invalid username or password")
    if user["is_archived"]:
        audit(user, "auth.login_blocked", "user", user["id"], user["name"], {"reason": "archived"})
        raise ApiError(403, "This account has been archived")
    audit(user, "auth.login", "user", user["id"], user["name"])
    return response(200, {"token": make_token(user), "user": public_user(user)})


def me(headers):
    return response(200, {"user": get_current_user(headers, allow_pending=True)})


def change_password(headers, body):
    user = get_current_user(headers, allow_pending=True)
    require_fields(body, "current_password", "new_password")
    if len(body["new_password"]) < 8:
        raise ApiError(400, "New password must be at least 8 characters")
    if body["new_password"] == body["current_password"]:
        raise ApiError(400, "Choose a password different from your current one")
    stored = query_one("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
    if not verify_password(body["current_password"], stored["password_hash"]):
        audit(user, "auth.password_change_failed", "user", user["id"], user["name"])
        raise ApiError(401, "Current password is incorrect")
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = FALSE, updated_at = now() WHERE id = %s",
        (hash_password(body["new_password"]), user["id"]),
    )
    audit(user, "auth.password_change", "user", user["id"], user["name"])
    return response(200, {"message": "Password updated"})


def health():
    # Public and unauthenticated: confirm the database answers, but reveal nothing about it
    # (no version string or user count that would help an attacker).
    query_one("SELECT 1 AS ok")
    return response(200, {"service": SERVICE_NAME, "status": "ok"})


def handler(event=None, context=None):
    try:
        method, path, body, headers, _query = parse_event(event, SERVICE_NAME)
        logger.info("%s %s", method, path)

        if method == "OPTIONS":
            return response(204)

        ensure_schema()
        seed_admin()

        if method == "GET" and path in ("/", "/health"):
            return health()
        if method == "POST" and path == "/login":
            return login(body)
        if method == "GET" and path == "/me":
            return me(headers)
        if method == "POST" and path == "/change-password":
            return change_password(headers, body)
        if method == "POST" and path == "/logout":
            return response(200, {"message": "Logged out"})

        return error(404, f"No route for {method} {path}")

    except ApiError as e:
        return error(e.status, e.message)
    except DatabaseUnavailable:
        logger.warning("Database unavailable: asking the client to retry")
        return error(503, "The database is starting up. Try again in a few seconds.")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unhandled error")
        return error(500, str(e))


if __name__ == "__main__":
    print(handler({"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}}))