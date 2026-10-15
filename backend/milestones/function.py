"""
Milestones service: ordered milestones inside a project, their teams, and
the tasks employees check off.

Routes (all under /api/milestones, all require Authorization: Bearer <token>):
    GET    /?project_id=...              -> milestones of a project, in order, with progress and dependencies
    GET    /schedule?project_id=...      -> Gantt data: critical path, slack, projected dates and delay;
                                            add simulate=<milestone_id>:<days> to test a slip
    GET    /timeline                     -> every active project the caller can see, with its schedule
    GET    /mine                         -> caller's open milestones with their tasks (dashboard)
    GET    /{id}                         -> one milestone with members and tasks
    POST   /                             -> create {project_id, name, start_date, end_date, description?,
                                            member_ids?: [user_id], tasks?: [{title, assigned_to?}],
                                            depends_on?: [milestone_id] (default: the previous milestone)}
    PATCH  /{id}                         -> edit name, description, dates, depends_on
    DELETE /{id}                         -> delete a milestone that has not started
    POST   /{id}/start                   -> begin work (everything it depends on must be completed)
    POST   /{id}/complete                -> manager signs off; {"force": true} skips open tasks
    POST   /{id}/reopen                  -> undo completion (only if nothing depending on it has started)
    POST   /{id}/members                 -> add {user_id} (must be on the project)
    DELETE /{id}/members/{user_id}       -> remove from milestone
    POST   /{id}/tasks                   -> add {title, description?, assigned_to?}
    PATCH  /tasks/{task_id}              -> {completed: true|false} (assignee or milestone member),
                                            or title/description/assigned_to (manager)
    DELETE /tasks/{task_id}              -> delete a task (manager)

Rules:
    - Milestones depend on one another. A milestone cannot start until everything it depends on
      is completed, and it may only depend on milestones earlier in the list (so there are no loops).
      By default each new milestone depends on the one before it; managers can change that, and
      milestones that do not depend on each other may run at the same time.
    - A milestone starts on or after the day its prerequisites end (a shared handoff day is fine),
      ends on or before the day its dependents start, and sits inside the project dates.
    - The critical path is the chain of dependent milestones with no slack: a slip on any of them
      moves the project finish. GET /schedule works this out and projects the finish date.
    - Many people can work on one milestone, but each person has at most one
      open (not completed) milestone per project at a time.
    - Employees check off tasks; only the project's manager or an admin completes a milestone.
"""

import logging
import re
from datetime import date

from common import ApiError, audit, changes, error, get_current_user, parse_event, require_fields, response
from db import DatabaseUnavailable, ensure_schema, execute, query, query_one
from schedule import build_schedule

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SERVICE_NAME = "milestones"
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


# ---------- Validation ----------

def _uuid(value, label="id"):
    if not value or not UUID_RE.match(str(value)):
        raise ApiError(400, f"Invalid {label}")
    return str(value)


def _date(value, label):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ApiError(400, f"{label} must be a date like 2026-10-31")


def _text(value, label, required=True, max_len=200):
    text = (value or "").strip()
    if required and not text:
        raise ApiError(400, f"{label} is required")
    if len(text) > max_len:
        raise ApiError(400, f"{label} must be at most {max_len} characters")
    return text or None


# ---------- Access ----------

def _project_access(viewer, project_id, manage=False):
    project = query_one("SELECT * FROM projects WHERE id = %s", (_uuid(project_id, "project_id"),))
    if not project:
        raise ApiError(404, "Project not found")
    is_admin = viewer["role"] == "admin"
    is_manager = str(project["manager_id"]) == str(viewer["id"])
    is_member = query_one(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s",
        (project["id"], viewer["id"]),
    ) is not None
    if not is_admin and (project["is_archived"] or not (is_manager or is_member)):
        raise ApiError(404, "Project not found")
    can_manage = is_admin or (is_manager and viewer["role"] == "manager")
    if manage:
        if not can_manage:
            raise ApiError(403, "Only an admin or this project's manager can do that")
        if project["status"] == "completed":
            raise ApiError(409, "This project is completed")
    return project, can_manage


def _milestone(viewer, milestone_id, manage=False):
    ms = query_one("SELECT * FROM milestones WHERE id = %s", (_uuid(milestone_id),))
    if not ms:
        raise ApiError(404, "Milestone not found")
    project, can_manage = _project_access(viewer, ms["project_id"], manage=manage)
    return ms, project, can_manage


def _prereqs(ms_id):
    """Milestones this one depends on, earliest first."""
    return query(
        "SELECT m.* FROM milestone_dependencies d JOIN milestones m ON m.id = d.depends_on_id "
        "WHERE d.milestone_id = %s ORDER BY m.position",
        (ms_id,),
    )


def _dependents(ms_id):
    """Milestones that depend on this one."""
    return query(
        "SELECT m.* FROM milestone_dependencies d JOIN milestones m ON m.id = d.milestone_id "
        "WHERE d.depends_on_id = %s ORDER BY m.position",
        (ms_id,),
    )


def _validated_depends_on(project, body, position, default=None):
    """Return the milestones a (new or edited) milestone at `position` may depend on.

    Only earlier milestones of the same project are allowed, which keeps the graph free of loops.
    When the body does not mention depends_on, `default` (usually the previous milestone) is used.
    """
    if "depends_on" not in body:
        return default or []
    raw = body.get("depends_on") or []
    if not isinstance(raw, list):
        raise ApiError(400, "depends_on must be a list of milestone ids")
    seen, out = set(), []
    for item in raw:
        dep_id = _uuid(item, "depends_on")
        if dep_id in seen:
            continue
        seen.add(dep_id)
        dep = query_one("SELECT * FROM milestones WHERE id = %s AND project_id = %s", (dep_id, project["id"]))
        if not dep:
            raise ApiError(400, "Milestones can only depend on milestones in the same project")
        if dep["position"] >= position:
            raise ApiError(
                400,
                f"'{dep['name']}' comes later in the plan. A milestone can only depend on earlier milestones, "
                "so the plan can never loop.",
            )
        out.append(dep)
    return out


def _check_dates(project, start, end, prereqs=(), dependents=()):
    if end < start:
        raise ApiError(400, "end_date must be on or after start_date")
    if start < project["start_date"] or end > project["end_date"]:
        raise ApiError(
            400,
            f"Milestone dates must fall within the project ({project['start_date']} to {project['end_date']})",
        )
    # A handoff day is allowed: a milestone may start on the day a prerequisite ends,
    # so several short milestones can share one day while staying in order.
    for prev in prereqs:
        if start < prev["end_date"]:
            raise ApiError(
                409,
                f"Depends on milestone #{prev['position']} '{prev['name']}', which ends {prev['end_date']}. "
                "Start on or after that date, or remove the dependency.",
            )
    for nxt in dependents:
        if end > nxt["start_date"]:
            raise ApiError(
                409,
                f"Milestone #{nxt['position']} '{nxt['name']}' depends on this one and starts {nxt['start_date']}. "
                "End on or before that date.",
            )


# ---------- Shaping ----------

LIST_SQL = """
SELECT m.*,
       COALESCE(t.total, 0) AS tasks_total,
       COALESCE(t.done, 0)  AS tasks_completed,
       COALESCE(mm.members, '[]') AS members,
       COALESCE(te.hours, 0) AS hours_logged
FROM milestones m
LEFT JOIN (SELECT milestone_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE completed) AS done
           FROM tasks GROUP BY milestone_id) t ON t.milestone_id = m.id
LEFT JOIN (SELECT x.milestone_id,
                  json_agg(json_build_object('user_id', u.id, 'name', u.name, 'job_title', u.job_title)
                           ORDER BY u.name) AS members
           FROM milestone_members x JOIN users u ON u.id = x.user_id
           GROUP BY x.milestone_id) mm ON mm.milestone_id = m.id
LEFT JOIN (SELECT milestone_id, SUM(hours) AS hours FROM time_entries
           WHERE milestone_id IS NOT NULL GROUP BY milestone_id) te ON te.milestone_id = m.id
"""


def _deps_for_project(project_id):
    """{milestone_id: [{id, position, name, status}, ...]} for every milestone in the project."""
    rows = query(
        """
        SELECT d.milestone_id, m.id, m.position, m.name, m.status
        FROM milestone_dependencies d JOIN milestones m ON m.id = d.depends_on_id
        WHERE m.project_id = %s ORDER BY m.position
        """,
        (project_id,),
    )
    out = {}
    for r in rows:
        out.setdefault(str(r["milestone_id"]), []).append(
            {"id": r["id"], "position": r["position"], "name": r["name"], "status": r["status"]})
    return out


def _shape(rows):
    """Add progress and 'blocked by' info from each milestone's dependencies."""
    if not rows:
        return []
    deps = _deps_for_project(rows[0]["project_id"])
    today = date.today()
    out = []
    for r in rows:
        item = dict(r)
        mine = deps.get(str(r["id"]), [])
        waiting = [d for d in mine if d["status"] != "completed"]
        item["progress_pct"] = round(100 * r["tasks_completed"] / r["tasks_total"]) if r["tasks_total"] else (
            100 if r["status"] == "completed" else 0)
        item["depends_on"] = mine
        item["blocked_by"] = waiting
        item["can_start"] = r["status"] == "pending" and not waiting
        item["blocked_by_previous"] = r["status"] == "pending" and bool(waiting)
        item["overdue"] = r["status"] != "completed" and today > r["end_date"]
        out.append(item)
    return out


def _detail(viewer, ms_id):
    ms, project, can_manage = _milestone(viewer, ms_id)
    rows = query(f"{LIST_SQL} WHERE m.project_id = %s ORDER BY m.position", (ms["project_id"],))
    item = next(r for r in _shape(rows) if str(r["id"]) == str(ms["id"]))
    item["tasks"] = query(
        """
        SELECT t.id, t.title, t.description, t.assigned_to, u.name AS assigned_to_name,
               t.completed, t.completed_at, t.created_at
        FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to
        WHERE t.milestone_id = %s ORDER BY t.completed, t.created_at
        """,
        (ms["id"],),
    )
    item["project_name"] = project["name"]
    item["can_manage"] = can_manage
    return item


# ---------- Milestone handlers ----------

def my_milestones(viewer):
    """Open milestones the caller is assigned to, with their tasks, across active projects."""
    rows = query(
        f"""{LIST_SQL}
        JOIN milestone_members me ON me.milestone_id = m.id AND me.user_id = %s
        JOIN projects p ON p.id = m.project_id
        WHERE m.status <> 'completed' AND p.is_archived = FALSE AND p.status = 'active'
        ORDER BY m.end_date""",
        (viewer["id"],),
    )
    out = []
    for r in rows:
        item = dict(r)
        project = query_one("SELECT name FROM projects WHERE id = %s", (r["project_id"],))
        item["project_name"] = project["name"]
        item["tasks"] = query(
            """
            SELECT t.id, t.title, t.assigned_to, u.name AS assigned_to_name, t.completed
            FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to
            WHERE t.milestone_id = %s AND (t.assigned_to = %s OR t.assigned_to IS NULL)
            ORDER BY t.completed, t.created_at
            """,
            (r["id"], viewer["id"]),
        )
        out.append(item)
    return response(200, {"milestones": out})


def list_milestones(viewer, params):
    project, can_manage = _project_access(viewer, params.get("project_id"))
    rows = query(f"{LIST_SQL} WHERE m.project_id = %s ORDER BY m.position", (project["id"],))
    return response(200, {"project_id": project["id"], "can_manage": can_manage, "milestones": _shape(rows)})


def create_milestone(viewer, body):
    require_fields(body, "project_id", "name", "start_date", "end_date")
    project, _ = _project_access(viewer, body["project_id"], manage=True)
    start, end = _date(body["start_date"], "start_date"), _date(body["end_date"], "end_date")
    last = query_one(
        "SELECT * FROM milestones WHERE project_id = %s ORDER BY position DESC LIMIT 1", (project["id"],)
    )
    position = (last["position"] + 1) if last else 1
    prereqs = _validated_depends_on(project, body, position, default=[last] if last else [])
    _check_dates(project, start, end, prereqs=prereqs)
    member_ids, tasks = _validated_new_milestone_people(project, body)
    row = execute(
        """
        INSERT INTO milestones (project_id, position, name, description, start_date, end_date)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            project["id"], position,
            _text(body["name"], "name"),
            _text(body.get("description"), "description", required=False, max_len=2000),
            start, end,
        ),
    )
    ms_name = body["name"].strip()
    for dep in prereqs:
        execute("INSERT INTO milestone_dependencies (milestone_id, depends_on_id) VALUES (%s, %s)", (row["id"], dep["id"]))
    audit(viewer, "milestone.create", "milestone", row["id"], ms_name,
          {"project_id": project["id"], "project_name": project["name"], "start_date": start, "end_date": end,
           "depends_on": [d["name"] for d in prereqs]})
    for user_id, user_name in member_ids:
        execute("INSERT INTO milestone_members (milestone_id, user_id) VALUES (%s, %s)", (row["id"], user_id))
        audit(viewer, "milestone.member_add", "milestone", row["id"], ms_name, {"user_id": user_id, "user_name": user_name})
    for title, assignee in tasks:
        execute("INSERT INTO tasks (milestone_id, title, assigned_to) VALUES (%s, %s, %s)", (row["id"], title, assignee))
        audit(viewer, "task.create", "milestone", row["id"], ms_name, {"title": title})
    return response(201, {"milestone": _detail(viewer, row["id"])})


def _validated_new_milestone_people(project, body):
    """Check the optional people and tasks sent with a new milestone. Writes nothing.
    Returns ([(user_id, name)], [(title, assigned_to)])."""
    raw_members = body.get("member_ids") or []
    raw_tasks = body.get("tasks") or []
    if not isinstance(raw_members, list) or not isinstance(raw_tasks, list):
        raise ApiError(400, "member_ids and tasks must be lists")
    members, seen = [], set()
    for value in raw_members:
        user_id = _uuid(value, "member_ids")
        if user_id in seen:
            continue
        seen.add(user_id)
        person = query_one(
            "SELECT u.name FROM project_members pm JOIN users u ON u.id = pm.user_id "
            "WHERE pm.project_id = %s AND pm.user_id = %s AND u.is_archived = FALSE",
            (project["id"], user_id),
        )
        if not person:
            raise ApiError(400, "Only people on the project team can join a milestone. Add them on the Team tab first.")
        other = query_one(
            """
            SELECT m.position, m.name FROM milestone_members x JOIN milestones m ON m.id = x.milestone_id
            WHERE x.user_id = %s AND m.project_id = %s AND m.status <> 'completed'
            """,
            (user_id, project["id"]),
        )
        if other:
            raise ApiError(
                409,
                f"{person['name']} is already working on milestone #{other['position']} '{other['name']}'. "
                "People can only have one open milestone per project.",
            )
        members.append((user_id, person["name"]))
    if len(raw_tasks) > 50:
        raise ApiError(400, "Add at most 50 tasks at once")
    tasks = []
    for t in raw_tasks:
        t = t if isinstance(t, dict) else {"title": t}
        title = _text(t.get("title"), "task title")
        assignee = t.get("assigned_to") or None
        if assignee is not None:
            assignee = _uuid(assignee, "assigned_to")
            if assignee not in seen:
                raise ApiError(400, f"Task '{title}' is assigned to someone who is not on this milestone")
        tasks.append((title, assignee))
    return members, tasks


def update_milestone(viewer, ms_id, body):
    ms, project, _ = _milestone(viewer, ms_id, manage=True)
    sets, args = [], []
    if "name" in body:
        sets.append("name = %s"); args.append(_text(body["name"], "name"))
    if "description" in body:
        sets.append("description = %s")
        args.append(_text(body["description"], "description", required=False, max_len=2000))
    new_deps = None
    if "depends_on" in body:
        if ms["status"] == "completed":
            raise ApiError(409, "Dependencies of a completed milestone cannot change")
        new_deps = _validated_depends_on(project, body, ms["position"])
        if ms["status"] == "in_progress" and any(d["status"] != "completed" for d in new_deps):
            raise ApiError(409, "This milestone is already in progress, so it can only depend on completed milestones")
    if "start_date" in body or "end_date" in body or new_deps is not None:
        if ms["status"] == "completed" and ("start_date" in body or "end_date" in body):
            raise ApiError(409, "Dates of a completed milestone cannot change")
        start = _date(body["start_date"], "start_date") if "start_date" in body else ms["start_date"]
        end = _date(body["end_date"], "end_date") if "end_date" in body else ms["end_date"]
        prereqs = new_deps if new_deps is not None else _prereqs(ms["id"])
        _check_dates(project, start, end, prereqs=prereqs, dependents=_dependents(ms["id"]))
        if "start_date" in body or "end_date" in body:
            sets += ["start_date = %s", "end_date = %s"]
            args += [start, end]
    if not sets and new_deps is None:
        raise ApiError(400, "No editable fields provided")
    old_deps = [d["name"] for d in _prereqs(ms["id"])]
    if new_deps is not None:
        execute("DELETE FROM milestone_dependencies WHERE milestone_id = %s", (ms["id"],))
        for dep in new_deps:
            execute("INSERT INTO milestone_dependencies (milestone_id, depends_on_id) VALUES (%s, %s)", (ms["id"], dep["id"]))
    if sets:
        sets.append("updated_at = now()")
        execute(f"UPDATE milestones SET {', '.join(sets)} WHERE id = %s", args + [ms["id"]])
    after = query_one("SELECT * FROM milestones WHERE id = %s", (ms["id"],))
    diff = changes(ms, after, ("name", "description", "start_date", "end_date"))
    if new_deps is not None and [d["name"] for d in new_deps] != old_deps:
        diff["depends_on"] = [", ".join(old_deps) or "nothing", ", ".join(d["name"] for d in new_deps) or "nothing"]
    if diff:
        audit(viewer, "milestone.update", "milestone", ms["id"], after["name"],
              {"project_name": project["name"], "changes": diff})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


def delete_milestone(viewer, ms_id):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] != "pending":
        raise ApiError(409, "Only milestones that have not started can be deleted")
    if query_one("SELECT 1 FROM time_entries WHERE milestone_id = %s", (ms["id"],)):
        raise ApiError(409, "Time has been logged against this milestone, so it cannot be deleted")
    execute("DELETE FROM tasks WHERE milestone_id = %s", (ms["id"],))
    execute("DELETE FROM milestone_members WHERE milestone_id = %s", (ms["id"],))
    execute("DELETE FROM milestone_dependencies WHERE milestone_id = %s OR depends_on_id = %s", (ms["id"], ms["id"]))
    execute("DELETE FROM milestones WHERE id = %s", (ms["id"],))
    audit(viewer, "milestone.delete", "milestone", ms["id"], ms["name"], {"project_id": ms["project_id"], "position": ms["position"]})
    # Close the gap in numbering (two steps to avoid clashing with the unique constraint).
    execute("UPDATE milestones SET position = position + 100000 WHERE project_id = %s AND position > %s",
            (ms["project_id"], ms["position"]))
    execute("UPDATE milestones SET position = position - 100001 WHERE project_id = %s AND position > 100000",
            (ms["project_id"],))
    return response(200, {"message": "Milestone deleted"})


def start_milestone(viewer, ms_id):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] != "pending":
        raise ApiError(409, f"Milestone is already {ms['status'].replace('_', ' ')}")
    waiting = [d for d in _prereqs(ms["id"]) if d["status"] != "completed"]
    if waiting:
        names = " and ".join(f"#{d['position']} '{d['name']}'" for d in waiting)
        raise ApiError(409, f"Finish milestone {names} before starting this one. It depends on them.")
    execute("UPDATE milestones SET status = 'in_progress', updated_at = now() WHERE id = %s", (ms["id"],))
    audit(viewer, "milestone.start", "milestone", ms["id"], ms["name"], {"project_id": ms["project_id"]})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


def complete_milestone(viewer, ms_id, body):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] == "completed":
        raise ApiError(409, "Milestone is already completed")
    waiting = [d for d in _prereqs(ms["id"]) if d["status"] != "completed"]
    if waiting:
        names = " and ".join(f"#{d['position']} '{d['name']}'" for d in waiting)
        raise ApiError(409, f"Milestone {names} must be completed first. This one depends on them.")
    open_tasks = query_one(
        "SELECT COUNT(*) AS n FROM tasks WHERE milestone_id = %s AND completed = FALSE", (ms["id"],)
    )["n"]
    if open_tasks and not body.get("force"):
        raise ApiError(
            409,
            f"{open_tasks} task(s) are still open. Send {{\"force\": true}} to complete the milestone anyway.",
        )
    execute(
        "UPDATE milestones SET status = 'completed', completed_at = now(), updated_at = now() WHERE id = %s",
        (ms["id"],),
    )
    audit(viewer, "milestone.complete", "milestone", ms["id"], ms["name"],
          {"project_id": ms["project_id"], "forced": bool(open_tasks), "open_tasks": open_tasks})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


def reopen_milestone(viewer, ms_id):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] != "completed":
        raise ApiError(409, "Only completed milestones can be reopened")
    started = [d for d in _dependents(ms["id"]) if d["status"] != "pending"]
    if started:
        raise ApiError(409, f"Milestone #{started[0]['position']} '{started[0]['name']}' depends on this one and has "
                            "already started, so this one cannot reopen")
    execute(
        "UPDATE milestones SET status = 'in_progress', completed_at = NULL, updated_at = now() WHERE id = %s",
        (ms["id"],),
    )
    audit(viewer, "milestone.reopen", "milestone", ms["id"], ms["name"], {"project_id": ms["project_id"]})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


# ---------- Schedule: Gantt data, critical path, projected finish ----------

SCHEDULE_SQL = """
SELECT m.id, m.project_id, m.position, m.name, m.status, m.start_date, m.end_date, m.completed_at,
       COALESCE(t.total, 0) AS tasks_total, COALESCE(t.done, 0) AS tasks_completed,
       COALESCE(d.deps, '{}') AS depends_on
FROM milestones m
LEFT JOIN (SELECT milestone_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE completed) AS done
           FROM tasks GROUP BY milestone_id) t ON t.milestone_id = m.id
LEFT JOIN (SELECT milestone_id, array_agg(depends_on_id::text) AS deps
           FROM milestone_dependencies GROUP BY milestone_id) d ON d.milestone_id = m.id
"""


def _parse_simulate(params):
    """simulate=<milestone_id>:<days>[,<id>:<days>] -> {id: days}."""
    out = {}
    for part in str(params.get("simulate", "") or "").split(","):
        if not part.strip():
            continue
        if ":" not in part:
            raise ApiError(400, "simulate must look like <milestone_id>:<days>")
        mid, days = part.split(":", 1)
        try:
            out[_uuid(mid.strip(), "simulate")] = max(0, min(int(days), 365))
        except ValueError:
            raise ApiError(400, "simulate days must be a whole number")
    return out


def project_schedule(viewer, params):
    project, can_manage = _project_access(viewer, params.get("project_id"))
    rows = query(f"{SCHEDULE_SQL} WHERE m.project_id = %s ORDER BY m.position", (project["id"],))
    sched = build_schedule(project, rows, simulate=_parse_simulate(params))
    sched.update({"project_id": project["id"], "project_name": project["name"],
                  "project_start": project["start_date"], "can_manage": can_manage})
    return response(200, sched)


def timeline(viewer):
    """Every active project the caller can see, each with its schedule, for the portfolio Gantt."""
    where, args = ["p.is_archived = FALSE", "p.status = 'active'"], []
    if viewer["role"] != "admin":
        where.append("(p.manager_id = %s OR EXISTS (SELECT 1 FROM project_members x "
                     "WHERE x.project_id = p.id AND x.user_id = %s))")
        args += [viewer["id"], viewer["id"]]
    projects = query(
        f"SELECT p.id, p.name, p.start_date, p.end_date, p.status, u.name AS manager_name FROM projects p "
        f"JOIN users u ON u.id = p.manager_id WHERE {' AND '.join(where)} ORDER BY p.start_date, p.name",
        args,
    )
    if not projects:
        return response(200, {"projects": []})
    rows = query(f"{SCHEDULE_SQL} WHERE m.project_id = ANY(%s) ORDER BY m.project_id, m.position",
                 ([p["id"] for p in projects],))
    by_project = {}
    for r in rows:
        by_project.setdefault(str(r["project_id"]), []).append(r)
    out = []
    for p in projects:
        sched = build_schedule(p, by_project.get(str(p["id"]), []))
        out.append({"id": p["id"], "name": p["name"], "manager_name": p["manager_name"],
                    "start_date": p["start_date"], "end_date": p["end_date"], **sched})
    return response(200, {"projects": out})


# ---------- Members ----------

def add_member(viewer, ms_id, body):
    ms, project, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] == "completed":
        raise ApiError(409, "Cannot add people to a completed milestone")
    require_fields(body, "user_id")
    user_id = _uuid(body["user_id"], "user_id")
    on_project = query_one(
        "SELECT u.name FROM project_members pm JOIN users u ON u.id = pm.user_id "
        "WHERE pm.project_id = %s AND pm.user_id = %s AND u.is_archived = FALSE",
        (project["id"], user_id),
    )
    if not on_project:
        raise ApiError(400, "Add this person to the project before assigning them to a milestone")
    other = query_one(
        """
        SELECT m.position, m.name FROM milestone_members x JOIN milestones m ON m.id = x.milestone_id
        WHERE x.user_id = %s AND m.project_id = %s AND m.status <> 'completed' AND m.id <> %s
        """,
        (user_id, project["id"], ms["id"]),
    )
    if other:
        raise ApiError(
            409,
            f"{on_project['name']} is already working on milestone #{other['position']} '{other['name']}'. "
            "People can only have one open milestone per project.",
        )
    if query_one("SELECT 1 FROM milestone_members WHERE milestone_id = %s AND user_id = %s", (ms["id"], user_id)):
        raise ApiError(409, "That person is already on this milestone")
    execute("INSERT INTO milestone_members (milestone_id, user_id) VALUES (%s, %s)", (ms["id"], user_id))
    audit(viewer, "milestone.member_add", "milestone", ms["id"], ms["name"],
          {"user_id": user_id, "user_name": on_project["name"]})
    return response(201, {"milestone": _detail(viewer, ms["id"])})


def remove_member(viewer, ms_id, user_id):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    user_id = _uuid(user_id, "user_id")
    if not execute("DELETE FROM milestone_members WHERE milestone_id = %s AND user_id = %s", (ms["id"], user_id)):
        raise ApiError(404, "That person is not on this milestone")
    audit(viewer, "milestone.member_remove", "milestone", ms["id"], ms["name"], {"user_id": user_id})
    execute("UPDATE tasks SET assigned_to = NULL WHERE milestone_id = %s AND assigned_to = %s AND completed = FALSE",
            (ms["id"], user_id))
    return response(200, {"milestone": _detail(viewer, ms["id"])})


# ---------- Tasks ----------

def _check_assignee(ms_id, user_id):
    if user_id is None:
        return None
    user_id = _uuid(user_id, "assigned_to")
    if not query_one("SELECT 1 FROM milestone_members WHERE milestone_id = %s AND user_id = %s", (ms_id, user_id)):
        raise ApiError(400, "Tasks can only be assigned to people on this milestone")
    return user_id


def add_task(viewer, ms_id, body):
    ms, _, _ = _milestone(viewer, ms_id, manage=True)
    if ms["status"] == "completed":
        raise ApiError(409, "Cannot add tasks to a completed milestone")
    require_fields(body, "title")
    execute(
        "INSERT INTO tasks (milestone_id, title, description, assigned_to) VALUES (%s, %s, %s, %s)",
        (
            ms["id"], _text(body["title"], "title"),
            _text(body.get("description"), "description", required=False, max_len=2000),
            _check_assignee(ms["id"], body.get("assigned_to")),
        ),
    )
    audit(viewer, "task.create", "milestone", ms["id"], ms["name"], {"title": body["title"].strip()})
    return response(201, {"milestone": _detail(viewer, ms["id"])})


def update_task(viewer, task_id, body):
    task = query_one("SELECT * FROM tasks WHERE id = %s", (_uuid(task_id, "task_id"),))
    if not task:
        raise ApiError(404, "Task not found")
    ms, _, can_manage = _milestone(viewer, task["milestone_id"])
    sets, args = [], []

    if "completed" in body:
        is_assignee = str(task["assigned_to"] or "") == str(viewer["id"])
        on_milestone = query_one(
            "SELECT 1 FROM milestone_members WHERE milestone_id = %s AND user_id = %s", (ms["id"], viewer["id"])
        ) is not None
        if not (can_manage or is_assignee or (task["assigned_to"] is None and on_milestone)):
            raise ApiError(403, "Only the assignee, milestone members (for unassigned tasks), or the manager can check this off")
        if ms["status"] != "in_progress":
            raise ApiError(409, "Tasks can only be checked off while the milestone is in progress")
        done = bool(body["completed"])
        sets += ["completed = %s", "completed_at = " + ("now()" if done else "NULL")]
        args.append(done)

    manager_fields = {"title", "description", "assigned_to"} & set(body)
    if manager_fields:
        if not can_manage:
            raise ApiError(403, "Only the project's manager or an admin can edit task details")
        if ms["status"] == "completed":
            raise ApiError(409, "Tasks on a completed milestone can't be changed. Reopen the milestone first.")
        if "title" in body:
            sets.append("title = %s"); args.append(_text(body["title"], "title"))
        if "description" in body:
            sets.append("description = %s")
            args.append(_text(body["description"], "description", required=False, max_len=2000))
        if "assigned_to" in body:
            sets.append("assigned_to = %s"); args.append(_check_assignee(ms["id"], body["assigned_to"]))

    if not sets:
        raise ApiError(400, "No editable fields provided")
    sets.append("updated_at = now()")
    execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s", args + [task["id"]])
    if "completed" in body:
        audit(viewer, "task.complete" if body["completed"] else "task.reopen", "task", task["id"], task["title"],
              {"milestone_id": ms["id"], "milestone_name": ms["name"]})
    if manager_fields:
        after = query_one("SELECT * FROM tasks WHERE id = %s", (task["id"],))
        diff = changes(task, after, ("title", "description", "assigned_to"))
        if diff:
            audit(viewer, "task.update", "task", task["id"], after["title"], {"changes": diff})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


def delete_task(viewer, task_id):
    task = query_one("SELECT * FROM tasks WHERE id = %s", (_uuid(task_id, "task_id"),))
    if not task:
        raise ApiError(404, "Task not found")
    ms, _, _ = _milestone(viewer, task["milestone_id"], manage=True)
    execute("DELETE FROM tasks WHERE id = %s", (task["id"],))
    audit(viewer, "task.delete", "task", task["id"], task["title"], {"milestone_id": ms["id"], "milestone_name": ms["name"]})
    return response(200, {"milestone": _detail(viewer, ms["id"])})


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
                return list_milestones(viewer, params)
            if method == "POST":
                return create_milestone(viewer, body)
        elif parts == ["mine"] and method == "GET":
            return my_milestones(viewer)
        elif parts == ["schedule"] and method == "GET":
            return project_schedule(viewer, params)
        elif parts == ["timeline"] and method == "GET":
            return timeline(viewer)
        elif parts[0] == "tasks" and len(parts) == 2:
            if method in ("PATCH", "PUT"):
                return update_task(viewer, parts[1], body)
            if method == "DELETE":
                return delete_task(viewer, parts[1])
        elif len(parts) == 1:
            if method == "GET":
                return response(200, {"milestone": _detail(viewer, parts[0])})
            if method in ("PATCH", "PUT"):
                return update_milestone(viewer, parts[0], body)
            if method == "DELETE":
                return delete_milestone(viewer, parts[0])
        elif len(parts) == 2 and method == "POST":
            action = parts[1]
            if action == "start":
                return start_milestone(viewer, parts[0])
            if action == "complete":
                return complete_milestone(viewer, parts[0], body)
            if action == "reopen":
                return reopen_milestone(viewer, parts[0])
            if action == "members":
                return add_member(viewer, parts[0], body)
            if action == "tasks":
                return add_task(viewer, parts[0], body)
        elif len(parts) == 3 and parts[1] == "members" and method == "DELETE":
            return remove_member(viewer, parts[0], parts[2])

        return error(404, f"No route for {method} {path}")

    except ApiError as e:
        return error(e.status, e.message)
    except DatabaseUnavailable:
        logger.warning("Database unavailable: asking the client to retry")
        return error(503, "The database is starting up. Try again in a few seconds.")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unhandled error")
        return error(500, str(e))
