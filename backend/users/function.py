"""
Users service: employee directory, creation, profile edits, manager
assignment, and archiving.

Routes (all under /api/users, all require Authorization: Bearer <token>):
    GET    /                     -> list users visible to the caller
                                    query: q (search), role, include_archived=true (admin)
    GET    /{id}                 -> one user, if visible to the caller
    POST   /                     -> admin: create user, returns a one-time temp password
    PATCH  /{id}                 -> edit user (fields allowed depend on role, see below)
    POST   /{id}/archive         -> admin: archive a user
    POST   /{id}/unarchive       -> admin: restore a user
    POST   /{id}/reset-password  -> admin: issue a new temp password
    GET    /audit                -> admin: audit log; query: q, action, entity_type, entity_id,
                                    actor_id, from, to (YYYY-MM-DD), limit, offset

Visibility:
    admin    -> everyone
    manager  -> self, own manager, direct reports
    employee -> self, own manager, teammates (same manager)

Edit rights:
    admin    -> name, email, job_title, role, manager_id, pay_rate
    manager  -> name, email of direct reports (and self)
    employee -> own name, email

Pay rate is only shown to admins, the user themself, and their direct manager.
"""

import logging
import re

from common import (
    PUBLIC_USER_COLUMNS,
    ApiError,
    audit,
    changes,
    error,
    generate_password,
    generate_username,
    get_current_user,
    hash_password,
    parse_event,
    require_fields,
    require_role,
    response,
)
from db import ensure_schema, execute, query, query_one

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SERVICE_NAME = "users"
ROLES = ("admin", "manager", "employee")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


# ---------- Helpers ----------

def _clean_email(value):
    email = (value or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ApiError(400, "A valid email is required")
    return email


def _clean_name(value):
    name = (value or "").strip()
    if not name or len(name) > 200:
        raise ApiError(400, "Name is required (max 200 characters)")
    return name


def _clean_pay_rate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "pay_rate must be a number")
    if rate < 0 or rate > 100000:
        raise ApiError(400, "pay_rate must be between 0 and 100000")
    return rate


def _check_uuid(value, label="id"):
    if not value or not UUID_RE.match(str(value)):
        raise ApiError(400, f"Invalid {label}")
    return str(value)


def _get_user(user_id):
    return query_one(f"SELECT {PUBLIC_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))


def _validate_manager(manager_id, user_id=None):
    """Manager must exist, be active, be a manager or admin, and not create a loop."""
    if manager_id is None:
        return None
    manager_id = _check_uuid(manager_id, "manager_id")
    if user_id and manager_id == str(user_id):
        raise ApiError(400, "A user cannot be their own manager")
    mgr = query_one("SELECT id, role, is_archived FROM users WHERE id = %s", (manager_id,))
    if not mgr or mgr["is_archived"]:
        raise ApiError(400, "Manager not found or archived")
    if mgr["role"] not in ("manager", "admin"):
        raise ApiError(400, "Assigned manager must have the manager or admin role")
    if user_id:
        # Walk up the chain from the new manager; if we reach user_id it is a loop.
        loop = query_one(
            """
            WITH RECURSIVE chain AS (
                SELECT id, manager_id, 1 AS depth FROM users WHERE id = %s
                UNION ALL
                SELECT u.id, u.manager_id, c.depth + 1
                FROM users u JOIN chain c ON u.id = c.manager_id
                WHERE c.depth < 100
            )
            SELECT 1 AS found FROM chain WHERE id = %s LIMIT 1
            """,
            (manager_id, user_id),
        )
        if loop:
            raise ApiError(400, "That assignment would create a reporting loop")
    return manager_id


def _visible_ids_clause(viewer):
    """SQL fragment + params limiting rows to what the viewer may see."""
    if viewer["role"] == "admin":
        return "TRUE", ()
    if viewer["role"] == "manager":
        return "(u.id = %s OR u.id = %s OR u.manager_id = %s)", (
            viewer["id"], viewer["manager_id"], viewer["id"],
        )
    # employee: self, own manager, teammates sharing the same manager
    return "(u.id = %s OR u.id = %s OR (u.manager_id IS NOT NULL AND u.manager_id = %s))", (
        viewer["id"], viewer["manager_id"], viewer["manager_id"],
    )


def _shape(row, viewer):
    """Hide pay rate unless the viewer is admin, the user, or their manager."""
    out = dict(row)
    can_see_pay = (
        viewer["role"] == "admin"
        or str(row["id"]) == str(viewer["id"])
        or (row.get("manager_id") and str(row["manager_id"]) == str(viewer["id"]))
    )
    if not can_see_pay:
        out.pop("pay_rate", None)
    return out


def _can_view(viewer, target):
    if viewer["role"] == "admin":
        return True
    vid, tid = str(viewer["id"]), str(target["id"])
    t_mgr = str(target["manager_id"]) if target["manager_id"] else None
    v_mgr = str(viewer["manager_id"]) if viewer["manager_id"] else None
    if tid == vid or (v_mgr and tid == v_mgr):
        return True
    if viewer["role"] == "manager":
        return t_mgr == vid
    return v_mgr is not None and t_mgr == v_mgr


# ---------- Handlers ----------

def list_users(viewer, params):
    clause, args = _visible_ids_clause(viewer)
    sql = f"SELECT {', '.join('u.' + c.strip() for c in PUBLIC_USER_COLUMNS.split(','))}, " \
          "m.name AS manager_name FROM users u LEFT JOIN users m ON m.id = u.manager_id " \
          f"WHERE {clause}"
    args = list(args)

    include_archived = str(params.get("include_archived", "")).lower() == "true"
    if not (include_archived and viewer["role"] == "admin"):
        sql += " AND u.is_archived = FALSE"
    if params.get("role") in ROLES:
        sql += " AND u.role = %s"
        args.append(params["role"])
    if params.get("q"):
        term = f"%{params['q'].strip()}%"
        sql += (" AND (u.name ILIKE %s OR u.email ILIKE %s OR u.job_title ILIKE %s"
                " OR u.username ILIKE %s OR CAST(u.employee_number AS TEXT) LIKE %s)")
        args += [term] * 5
    sql += " ORDER BY u.name LIMIT 500"

    rows = query(sql, args)
    return response(200, {"users": [_shape(r, viewer) for r in rows]})


def get_user(viewer, user_id):
    target = _get_user(_check_uuid(user_id))
    if not target or not _can_view(viewer, target):
        raise ApiError(404, "User not found")
    if target["is_archived"] and viewer["role"] != "admin":
        raise ApiError(404, "User not found")
    return response(200, {"user": _shape(target, viewer)})


def create_user(viewer, body):
    require_role(viewer, "admin")
    require_fields(body, "name", "email", "role")
    name = _clean_name(body["name"])
    email = _clean_email(body["email"])
    role = body["role"]
    if role not in ROLES:
        raise ApiError(400, f"role must be one of: {', '.join(ROLES)}")
    job_title = (body.get("job_title") or "").strip() or None
    pay_rate = _clean_pay_rate(body.get("pay_rate", 0))
    manager_id = _validate_manager(body.get("manager_id"))

    if query_one("SELECT 1 FROM users WHERE email = %s", (email,)):
        raise ApiError(409, "A user with that email already exists")

    temp_password = generate_password()
    for _ in range(5):  # retry on the rare username collision
        username = generate_username()
        if not query_one("SELECT 1 FROM users WHERE username = %s", (username,)):
            break
    else:
        raise ApiError(500, "Could not generate a unique username")

    row = execute(
        f"""
        INSERT INTO users (username, password_hash, name, email, job_title, role, manager_id, pay_rate,
                           must_change_password)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING {PUBLIC_USER_COLUMNS}
        """,
        (username, hash_password(temp_password), name, email, job_title, role, manager_id, pay_rate),
    )
    audit(viewer, "user.create", "user", row["id"], name,
          {"role": role, "email": email, "job_title": job_title, "manager_id": manager_id, "pay_rate": pay_rate})
    return response(201, {
        "user": row,
        "temporary_password": temp_password,
        "message": "Share the username and temporary password with the employee once. It is not stored in plain text.",
    })


def update_user(viewer, user_id, body):
    user_id = _check_uuid(user_id)
    target = _get_user(user_id)
    if not target or not _can_view(viewer, target):
        raise ApiError(404, "User not found")
    if target["is_archived"]:
        raise ApiError(409, "Archived users cannot be edited. Unarchive first.")

    is_self = str(target["id"]) == str(viewer["id"])
    is_direct_report = str(target["manager_id"] or "") == str(viewer["id"])

    if viewer["role"] == "admin":
        allowed = {"name", "email", "job_title", "role", "manager_id", "pay_rate"}
    elif viewer["role"] == "manager" and (is_self or is_direct_report):
        allowed = {"name", "email"}
    elif is_self:
        allowed = {"name", "email"}
    else:
        raise ApiError(403, "You can only edit your own profile")

    requested = {k for k in body if k in {"name", "email", "job_title", "role", "manager_id", "pay_rate"}}
    blocked = requested - allowed
    if blocked:
        if "manager_id" in blocked:
            raise ApiError(403, "Only an admin can change an employee's manager")
        raise ApiError(403, f"You cannot change: {', '.join(sorted(blocked))}")
    if not requested:
        raise ApiError(400, "No editable fields provided")

    sets, args = [], []
    if "name" in requested:
        sets.append("name = %s"); args.append(_clean_name(body["name"]))
    if "email" in requested:
        email = _clean_email(body["email"])
        if query_one("SELECT 1 FROM users WHERE email = %s AND id <> %s", (email, user_id)):
            raise ApiError(409, "A user with that email already exists")
        sets.append("email = %s"); args.append(email)
    if "job_title" in requested:
        sets.append("job_title = %s"); args.append((body["job_title"] or "").strip() or None)
    if "pay_rate" in requested:
        sets.append("pay_rate = %s"); args.append(_clean_pay_rate(body["pay_rate"]))
    if "manager_id" in requested:
        sets.append("manager_id = %s"); args.append(_validate_manager(body["manager_id"], user_id))
    if "role" in requested:
        role = body["role"]
        if role not in ROLES:
            raise ApiError(400, f"role must be one of: {', '.join(ROLES)}")
        if is_self and role != "admin":
            raise ApiError(400, "You cannot remove your own admin role")
        if target["role"] in ("manager", "admin") and role == "employee":
            if query_one("SELECT 1 FROM projects WHERE manager_id = %s AND is_archived = FALSE", (user_id,)):
                raise ApiError(409, "This user still manages active projects. Reassign them first.")
            if query_one("SELECT 1 FROM users WHERE manager_id = %s AND is_archived = FALSE", (user_id,)):
                raise ApiError(409, "This user still has direct reports. Reassign them first.")
        sets.append("role = %s"); args.append(role)

    sets.append("updated_at = now()")
    args.append(user_id)
    row = execute(
        f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {PUBLIC_USER_COLUMNS}",
        args,
    )
    diff = changes(target, row, requested)
    if diff:
        action = "user.reassign_manager" if "manager_id" in diff else "user.update"
        audit(viewer, action, "user", user_id, row["name"], {"changes": diff})
    return response(200, {"user": _shape(row, viewer)})


def set_archived(viewer, user_id, archived):
    require_role(viewer, "admin")
    user_id = _check_uuid(user_id)
    target = _get_user(user_id)
    if not target:
        raise ApiError(404, "User not found")
    if archived:
        if str(target["id"]) == str(viewer["id"]):
            raise ApiError(400, "You cannot archive yourself")
        if query_one("SELECT 1 FROM projects WHERE manager_id = %s AND is_archived = FALSE", (user_id,)):
            raise ApiError(409, "This user manages active projects. Reassign those projects first.")
        if query_one("SELECT 1 FROM users WHERE manager_id = %s AND is_archived = FALSE", (user_id,)):
            raise ApiError(409, "This user has direct reports. Reassign them to another manager first.")
    row = execute(
        f"UPDATE users SET is_archived = %s, updated_at = now() WHERE id = %s RETURNING {PUBLIC_USER_COLUMNS}",
        (archived, user_id),
    )
    audit(viewer, "user.archive" if archived else "user.unarchive", "user", user_id, row["name"])
    return response(200, {"user": row})


def reset_password(viewer, user_id):
    require_role(viewer, "admin")
    user_id = _check_uuid(user_id)
    if not _get_user(user_id):
        raise ApiError(404, "User not found")
    temp_password = generate_password()
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = TRUE, updated_at = now() WHERE id = %s",
        (hash_password(temp_password), user_id),
    )
    target = _get_user(user_id)
    audit(viewer, "user.reset_password", "user", user_id, target["name"])
    return response(200, {"temporary_password": temp_password})


# ---------- Audit log (admin only) ----------

def audit_log(viewer, params):
    require_role(viewer, "admin")
    where, args = [], []
    if params.get("actor_id"):
        where.append("actor_id = %s"); args.append(_check_uuid(params["actor_id"], "actor_id"))
    if params.get("action"):
        where.append("action LIKE %s"); args.append(params["action"].strip() + "%")
    if params.get("entity_type"):
        where.append("entity_type = %s"); args.append(params["entity_type"].strip())
    if params.get("entity_id"):
        where.append("entity_id = %s"); args.append(params["entity_id"].strip())
    if params.get("q"):
        term = f"%{params['q'].strip()}%"
        where.append("(actor_name ILIKE %s OR entity_name ILIKE %s OR action ILIKE %s OR details::text ILIKE %s)")
        args += [term] * 4
    for key, op in (("from", ">="), ("to", "<")):
        if params.get(key):
            where.append(f"occurred_at {op} %s::date" + (" + 1" if key == "to" else ""))
            args.append(params[key])
    try:
        limit = max(1, min(int(params.get("limit", 100)), 500))
        offset = max(0, int(params.get("offset", 0)))
    except ValueError:
        raise ApiError(400, "limit and offset must be numbers")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = query(
        f"SELECT * FROM audit_log {clause} ORDER BY occurred_at DESC, id DESC LIMIT %s OFFSET %s",
        args + [limit + 1, offset],
    )
    actions = query("SELECT DISTINCT action FROM audit_log ORDER BY action")
    return response(200, {
        "entries": rows[:limit],
        "has_more": len(rows) > limit,
        "actions": [a["action"] for a in actions],
    })


# ---------- Router ----------

def handler(event=None, context=None):
    try:
        method, path, body, headers, params = parse_event(event, SERVICE_NAME)
        logger.info("%s %s", method, path)
        if method == "OPTIONS":
            return response(204)

        ensure_schema()
        viewer = get_current_user(headers)
        parts = [p for p in path.split("/") if p]

        if not parts:
            if method == "GET":
                return list_users(viewer, params)
            if method == "POST":
                return create_user(viewer, body)
        elif len(parts) == 1:
            if parts[0] == "audit" and method == "GET":
                return audit_log(viewer, params)
            if parts[0] == "me" and method == "GET":
                return response(200, {"user": viewer})
            if method == "GET":
                return get_user(viewer, parts[0])
            if method in ("PATCH", "PUT"):
                return update_user(viewer, parts[0], body)
        elif len(parts) == 2 and method == "POST":
            if parts[1] == "archive":
                return set_archived(viewer, parts[0], True)
            if parts[1] == "unarchive":
                return set_archived(viewer, parts[0], False)
            if parts[1] == "reset-password":
                return reset_password(viewer, parts[0])

        return error(404, f"No route for {method} {path}")

    except ApiError as e:
        return error(e.status, e.message)
    except Exception as e:  # noqa: BLE001
        logger.exception("Unhandled error")
        return error(500, str(e))