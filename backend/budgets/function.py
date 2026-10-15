"""
Budgets service: time logging, other project costs, and budget vs. actual.

Routes (all under /api/budgets, all require Authorization: Bearer <token>):
    GET    /time                  -> time entries; query: project_id, user_id, from, to
    GET    /time/week             -> the caller's hours this week (or ?week_of=YYYY-MM-DD)
    POST   /time                  -> log own time {project_id, hours, work_date?, milestone_id?, note?}
    PATCH  /time/{id}             -> correct own entry {work_date?, hours?, milestone_id?, note?} while its week is open
    DELETE /time/{id}             -> delete own entry while its week is open (admins: any entry, any time)
    GET    /costs?project_id=...  -> other costs for a project (manager, admin)
    POST   /costs                 -> add {project_id, category, amount, description?, incurred_on?}
    DELETE /costs/{id}            -> remove a cost (manager, admin)
    GET    /summary?project_id=.. -> planned vs. actual: by person, by milestone, by week, by category
    GET    /portfolio             -> planned vs. actual for every visible active project (manager, admin)

Rules:
    - The work week is 40 hours: 8 hours a day, 5 days. Nobody can log more than
      8 hours in a day or 40 hours in a Monday to Sunday week, across all projects.
    - People log time only for projects they are assigned to, on dates within their assignment.
    - No logging in the future or against completed/archived projects.
    - Labor cost = hours x the employee's pay rate. Pay details are hidden from employees.
    - Timesheets close after one week: people can correct or delete entries from this week and
      last week only. Older corrections go through an admin, and every change is audited.
"""

import logging
import re
from datetime import date, timedelta

from common import ApiError, audit, error, get_current_user, parse_event, require_fields, require_role, response
from db import DatabaseUnavailable, ensure_schema, execute, query, query_one

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SERVICE_NAME = "budgets"
DAILY_CAP = 8.0
WEEKLY_CAP = 40.0
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
COST_CATEGORIES = ("technology", "infrastructure", "software", "travel", "contractor", "other")


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


def _amount(value, label, minimum, maximum):
    try:
        num = round(float(value), 2)
    except (TypeError, ValueError):
        raise ApiError(400, f"{label} must be a number")
    if num < minimum or num > maximum:
        raise ApiError(400, f"{label} must be between {minimum:g} and {maximum:g}")
    return num


def _week_bounds(day):
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


# ---------- Access ----------

def _project(viewer, project_id, manage=False):
    project = query_one("SELECT * FROM projects WHERE id = %s", (_uuid(project_id, "project_id"),))
    if not project:
        raise ApiError(404, "Project not found")
    is_admin = viewer["role"] == "admin"
    is_manager = str(project["manager_id"]) == str(viewer["id"])
    is_member = query_one(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s", (project["id"], viewer["id"])
    ) is not None
    if not is_admin and (project["is_archived"] or not (is_manager or is_member)):
        raise ApiError(404, "Project not found")
    can_manage = is_admin or (is_manager and viewer["role"] == "manager")
    if manage and not can_manage:
        raise ApiError(403, "Only an admin or this project's manager can see or change budget details")
    return project, can_manage


# ---------- Time entries ----------

def _check_entry(viewer, project, work_date, hours, milestone_id, exclude_entry=None):
    """Every rule a time entry must meet, for a new entry or an edit. Writes nothing.
    exclude_entry: the entry being edited, so its current hours don't count against the caps."""
    if project["status"] != "active" or project["is_archived"]:
        raise ApiError(409, "Time can only be logged on active projects")
    if work_date > date.today():
        raise ApiError(400, "You cannot log time in the future")
    if work_date < project["start_date"] or work_date > project["end_date"]:
        raise ApiError(400, "work_date must fall within the project dates")
    assignment = query_one(
        "SELECT * FROM project_members WHERE project_id = %s AND user_id = %s", (project["id"], viewer["id"])
    )
    if not assignment:
        raise ApiError(403, "You can only log time on projects you are assigned to")
    if work_date < assignment["assigned_from"] or (assignment["assigned_to"] and work_date > assignment["assigned_to"]):
        raise ApiError(400, "That date is outside your assignment on this project")
    if milestone_id:
        ms = query_one("SELECT project_id, status FROM milestones WHERE id = %s", (milestone_id,))
        if not ms or str(ms["project_id"]) != str(project["id"]):
            raise ApiError(400, "That milestone does not belong to this project")
        if ms["status"] == "pending":
            raise ApiError(409, "That milestone has not started yet")
    skip = exclude_entry or "00000000-0000-0000-0000-000000000000"
    day_total = float(query_one(
        "SELECT COALESCE(SUM(hours), 0) AS h FROM time_entries WHERE user_id = %s AND work_date = %s AND id <> %s",
        (viewer["id"], work_date, skip),
    )["h"])
    if day_total + hours > DAILY_CAP:
        raise ApiError(
            409,
            f"You already logged {day_total:g} h on {work_date}. The daily limit is {DAILY_CAP:g} h "
            f"({DAILY_CAP - day_total:g} h left).",
        )
    monday, sunday = _week_bounds(work_date)
    week_total = float(query_one(
        "SELECT COALESCE(SUM(hours), 0) AS h FROM time_entries "
        "WHERE user_id = %s AND work_date BETWEEN %s AND %s AND id <> %s",
        (viewer["id"], monday, sunday, skip),
    )["h"])
    if week_total + hours > WEEKLY_CAP:
        raise ApiError(
            409,
            f"You already logged {week_total:g} h the week of {monday}. The weekly limit is {WEEKLY_CAP:g} h "
            f"({WEEKLY_CAP - week_total:g} h left).",
        )
    return day_total, week_total


def _timesheet_open_from():
    """Timesheets close like a payroll week: only the current and the previous
    Monday-to-Sunday weeks can be corrected. Older entries are locked."""
    monday, _ = _week_bounds(date.today())
    return monday - timedelta(days=7)


def log_time(viewer, body):
    require_fields(body, "project_id", "hours")
    project, _ = _project(viewer, body["project_id"])
    hours = _amount(body["hours"], "hours", 0.25, DAILY_CAP)
    work_date = _date(body["work_date"], "work_date") if body.get("work_date") else date.today()
    milestone_id = _uuid(body["milestone_id"], "milestone_id") if body.get("milestone_id") else None
    day_total, week_total = _check_entry(viewer, project, work_date, hours, milestone_id)
    row = execute(
        "INSERT INTO time_entries (user_id, project_id, milestone_id, work_date, hours, note) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (viewer["id"], project["id"], milestone_id, work_date, hours, (body.get("note") or "").strip()[:500] or None),
    )
    audit(viewer, "time.log", "project", project["id"], project["name"],
          {"entry_id": row["id"], "work_date": work_date, "hours": hours, "milestone_id": milestone_id})
    return response(201, {"entry": row, "day_total": day_total + hours, "week_total": week_total + hours})


EDITABLE_TIME_FIELDS = ("work_date", "hours", "milestone_id", "note")


def edit_time(viewer, entry_id, body):
    """Correct one of your own entries. Limits:
    - only the person who logged it (not managers, not admins: nobody edits someone else's timesheet);
    - only while its timesheet week is open (this week or last week), and the new date must be too;
    - the project can't change (delete and re-log instead, so each project's cost history stays clean);
    - every logging rule applies again: caps, assignment dates, no future dates, started milestones;
    - the change is audited with the old and new values."""
    entry = query_one("SELECT * FROM time_entries WHERE id = %s", (_uuid(entry_id),))
    if not entry:
        raise ApiError(404, "Time entry not found")
    if str(entry["user_id"]) != str(viewer["id"]):
        raise ApiError(403, "You can only edit your own time entries")
    if "project_id" in body and str(body["project_id"]) != str(entry["project_id"]):
        raise ApiError(400, "The project of an entry can't be changed. Delete it and log the time on the right project.")
    fields = [k for k in EDITABLE_TIME_FIELDS if k in body]
    if not fields:
        raise ApiError(400, "No editable fields provided")
    open_from = _timesheet_open_from()
    if entry["work_date"] < open_from:
        raise ApiError(409, f"Timesheets before {open_from} are closed. Ask an admin to correct this entry.")
    project = query_one("SELECT * FROM projects WHERE id = %s", (entry["project_id"],))
    work_date = _date(body["work_date"], "work_date") if "work_date" in body else entry["work_date"]
    if work_date < open_from:
        raise ApiError(409, f"Entries can only be moved to dates from {open_from} on, while the timesheet is open.")
    hours = _amount(body["hours"], "hours", 0.25, DAILY_CAP) if "hours" in body else float(entry["hours"])
    if "milestone_id" in body:
        milestone_id = _uuid(body["milestone_id"], "milestone_id") if body["milestone_id"] else None
    else:
        milestone_id = str(entry["milestone_id"]) if entry["milestone_id"] else None
    note = ((body.get("note") or "").strip()[:500] or None) if "note" in body else entry["note"]
    _check_entry(viewer, project, work_date, hours, milestone_id, exclude_entry=entry["id"])
    after = execute(
        "UPDATE time_entries SET work_date = %s, hours = %s, milestone_id = %s, note = %s WHERE id = %s RETURNING *",
        (work_date, hours, milestone_id, note, entry["id"]),
    )
    diff = {k: [entry[k], after[k]] for k in EDITABLE_TIME_FIELDS if str(entry[k]) != str(after[k])}
    if diff:
        audit(viewer, "time.edit", "project", project["id"], project["name"], {"entry_id": entry["id"], "changes": diff})
    return response(200, {"entry": after})


def list_time(viewer, params):
    where, args = [], []
    if params.get("project_id"):
        project, can_manage = _project(viewer, params["project_id"])
        where.append("t.project_id = %s"); args.append(project["id"])
        if not can_manage:
            where.append("t.user_id = %s"); args.append(viewer["id"])
    elif viewer["role"] == "employee":
        where.append("t.user_id = %s"); args.append(viewer["id"])
    elif viewer["role"] == "manager":
        where.append("(t.user_id = %s OR u.manager_id = %s OR p.manager_id = %s)")
        args += [viewer["id"]] * 3

    if params.get("user_id"):
        where.append("t.user_id = %s"); args.append(_uuid(params["user_id"], "user_id"))
    if params.get("from"):
        where.append("t.work_date >= %s"); args.append(_date(params["from"], "from"))
    if params.get("to"):
        where.append("t.work_date <= %s"); args.append(_date(params["to"], "to"))

    rows = query(
        f"""
        SELECT t.id, t.user_id, u.name AS user_name, t.project_id, p.name AS project_name,
               t.milestone_id, m.name AS milestone_name, t.work_date, t.hours, t.note, t.created_at
        FROM time_entries t
        JOIN users u ON u.id = t.user_id
        JOIN projects p ON p.id = t.project_id
        LEFT JOIN milestones m ON m.id = t.milestone_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY t.work_date DESC, t.created_at DESC
        LIMIT 1000
        """,
        args,
    )
    open_from = _timesheet_open_from()
    for r in rows:
        r["editable"] = str(r["user_id"]) == str(viewer["id"]) and r["work_date"] >= open_from
    return response(200, {"entries": rows, "total_hours": round(sum(float(r["hours"]) for r in rows), 2),
                          "timesheet_open_from": open_from})


def my_week(viewer, params):
    day = _date(params["week_of"], "week_of") if params.get("week_of") else date.today()
    monday, sunday = _week_bounds(day)
    rows = query(
        """
        SELECT t.work_date, p.id AS project_id, p.name AS project_name, SUM(t.hours) AS hours
        FROM time_entries t JOIN projects p ON p.id = t.project_id
        WHERE t.user_id = %s AND t.work_date BETWEEN %s AND %s
        GROUP BY t.work_date, p.id, p.name ORDER BY t.work_date
        """,
        (viewer["id"], monday, sunday),
    )
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        entries = [r for r in rows if r["work_date"] == d]
        days.append({"date": d, "hours": sum(float(r["hours"]) for r in entries), "projects": entries})
    total = sum(x["hours"] for x in days)
    assignments = query(
        """
        SELECT p.id AS project_id, p.name AS project_name, pm.hours_per_week
        FROM project_members pm JOIN projects p ON p.id = pm.project_id
        WHERE pm.user_id = %s AND p.status = 'active' AND p.is_archived = FALSE
        ORDER BY p.name
        """,
        (viewer["id"],),
    )
    return response(200, {
        "week_start": monday, "week_end": sunday, "days": days,
        "total_hours": total, "weekly_cap": WEEKLY_CAP, "daily_cap": DAILY_CAP,
        "remaining_hours": max(WEEKLY_CAP - total, 0), "assignments": assignments,
    })


def delete_time(viewer, entry_id):
    entry = query_one("SELECT * FROM time_entries WHERE id = %s", (_uuid(entry_id),))
    if not entry:
        raise ApiError(404, "Time entry not found")
    if viewer["role"] != "admin" and str(entry["user_id"]) != str(viewer["id"]):
        raise ApiError(403, "You can only delete your own time entries")
    open_from = _timesheet_open_from()
    if viewer["role"] != "admin" and entry["work_date"] < open_from:
        raise ApiError(409, f"Timesheets before {open_from} are closed. Ask an admin to correct this entry.")
    execute("DELETE FROM time_entries WHERE id = %s", (entry["id"],))
    audit(viewer, "time.delete", "project", entry["project_id"], None,
          {"entry_id": entry["id"], "owner_id": entry["user_id"], "work_date": entry["work_date"], "hours": entry["hours"]})
    return response(200, {"message": "Time entry deleted"})


# ---------- Other costs ----------

def list_costs(viewer, params):
    project, _ = _project(viewer, params.get("project_id"), manage=True)
    rows = query(
        "SELECT * FROM project_costs WHERE project_id = %s ORDER BY incurred_on DESC, created_at DESC",
        (project["id"],),
    )
    return response(200, {"costs": rows, "categories": COST_CATEGORIES,
                          "total": round(sum(float(r["amount"]) for r in rows), 2)})


def add_cost(viewer, body):
    require_fields(body, "project_id", "category", "amount")
    project, _ = _project(viewer, body["project_id"], manage=True)
    if project["status"] != "active":
        raise ApiError(409, "Costs can only be added to active projects")
    category = str(body["category"]).strip().lower()
    if category not in COST_CATEGORIES:
        raise ApiError(400, f"category must be one of: {', '.join(COST_CATEGORIES)}")
    row = execute(
        "INSERT INTO project_costs (project_id, category, description, amount, incurred_on) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (
            project["id"], category, (body.get("description") or "").strip()[:500] or None,
            _amount(body["amount"], "amount", 0.01, 100_000_000),
            _date(body["incurred_on"], "incurred_on") if body.get("incurred_on") else date.today(),
        ),
    )
    audit(viewer, "cost.add", "project", project["id"], project["name"],
          {"cost_id": row["id"], "category": row["category"], "amount": row["amount"], "description": row["description"]})
    return response(201, {"cost": row})


def delete_cost(viewer, cost_id):
    cost = query_one("SELECT * FROM project_costs WHERE id = %s", (_uuid(cost_id),))
    if not cost:
        raise ApiError(404, "Cost not found")
    _project(viewer, cost["project_id"], manage=True)
    execute("DELETE FROM project_costs WHERE id = %s", (cost["id"],))
    audit(viewer, "cost.delete", "project", cost["project_id"], None,
          {"cost_id": cost["id"], "category": cost["category"], "amount": cost["amount"]})
    return response(200, {"message": "Cost deleted"})


# ---------- Reports ----------

def _status(planned, actual, time_pct):
    if planned <= 0:
        return "no_budget"
    used = 100 * actual / planned
    if used > 100:
        return "over_budget"
    if used > time_pct + 25:
        return "at_risk"
    return "on_track"


def summary(viewer, params):
    project, _ = _project(viewer, params.get("project_id"), manage=True)
    pid = project["id"]
    by_person = query(
        """
        SELECT u.id AS user_id, u.name, u.job_title, u.pay_rate,
               SUM(t.hours) AS hours, SUM(t.hours * u.pay_rate) AS cost
        FROM time_entries t JOIN users u ON u.id = t.user_id
        WHERE t.project_id = %s GROUP BY u.id ORDER BY cost DESC
        """,
        (pid,),
    )
    by_milestone = query(
        """
        SELECT m.id AS milestone_id, m.position, m.name, m.status,
               COALESCE(SUM(t.hours), 0) AS hours, COALESCE(SUM(t.hours * u.pay_rate), 0) AS cost
        FROM milestones m
        LEFT JOIN time_entries t ON t.milestone_id = m.id
        LEFT JOIN users u ON u.id = t.user_id
        WHERE m.project_id = %s GROUP BY m.id ORDER BY m.position
        """,
        (pid,),
    )
    by_week = query(
        """
        SELECT date_trunc('week', t.work_date)::date AS week_start,
               SUM(t.hours) AS hours, SUM(t.hours * u.pay_rate) AS cost
        FROM time_entries t JOIN users u ON u.id = t.user_id
        WHERE t.project_id = %s GROUP BY 1 ORDER BY 1
        """,
        (pid,),
    )
    by_category = query(
        "SELECT category, SUM(amount) AS amount FROM project_costs WHERE project_id = %s GROUP BY category ORDER BY 2 DESC",
        (pid,),
    )
    labor = sum(float(r["cost"]) for r in by_person)
    other = sum(float(r["amount"]) for r in by_category)
    hours = sum(float(r["hours"]) for r in by_person)
    planned = float(project["planned_budget"])

    total_days = max((project["end_date"] - project["start_date"]).days, 1)
    elapsed = min(max((date.today() - project["start_date"]).days, 0), total_days)
    time_pct = round(100 * elapsed / total_days)

    running, burn = 0.0, []
    for w in by_week:
        running += float(w["cost"])
        burn.append({**w, "cumulative_cost": round(running, 2)})

    return response(200, {
        "project": {k: project[k] for k in ("id", "name", "start_date", "end_date", "status",
                                            "planned_budget", "budget_hours")},
        "totals": {
            "planned_budget": planned,
            "labor_cost": round(labor, 2),
            "other_cost": round(other, 2),
            "actual_cost": round(labor + other, 2),
            "remaining_budget": round(planned - labor - other, 2),
            "budget_used_pct": round(100 * (labor + other) / planned) if planned > 0 else None,
            "budget_hours": float(project["budget_hours"]),
            "hours_logged": round(hours, 2),
            "hours_used_pct": round(100 * hours / float(project["budget_hours"]))
            if float(project["budget_hours"]) > 0 else None,
            "time_elapsed_pct": time_pct,
            "status": _status(planned, labor + other, time_pct),
        },
        "by_person": by_person,
        "by_milestone": by_milestone,
        "by_week": burn,
        "by_category": by_category,
    })


def portfolio(viewer):
    require_role(viewer, "admin", "manager")
    scope, args = "", []
    if viewer["role"] == "manager":
        scope = "AND p.manager_id = %s"
        args.append(viewer["id"])
    rows = query(
        f"""
        SELECT p.id, p.name, p.start_date, p.end_date, p.planned_budget, p.budget_hours, mgr.name AS manager_name,
               COALESCE(l.labor, 0) AS labor_cost, COALESCE(l.hours, 0) AS hours_logged,
               COALESCE(o.other, 0) AS other_cost
        FROM projects p
        JOIN users mgr ON mgr.id = p.manager_id
        LEFT JOIN (SELECT t.project_id, SUM(t.hours) AS hours, SUM(t.hours * u.pay_rate) AS labor
                   FROM time_entries t JOIN users u ON u.id = t.user_id GROUP BY t.project_id) l ON l.project_id = p.id
        LEFT JOIN (SELECT project_id, SUM(amount) AS other FROM project_costs GROUP BY project_id) o ON o.project_id = p.id
        WHERE p.is_archived = FALSE AND p.status = 'active' {scope}
        ORDER BY p.name
        """,
        args,
    )
    today = date.today()
    out = []
    for r in rows:
        planned = float(r["planned_budget"])
        actual = float(r["labor_cost"]) + float(r["other_cost"])
        total_days = max((r["end_date"] - r["start_date"]).days, 1)
        time_pct = round(100 * min(max((today - r["start_date"]).days, 0), total_days) / total_days)
        out.append({**r, "actual_cost": round(actual, 2),
                    "budget_used_pct": round(100 * actual / planned) if planned > 0 else None,
                    "time_elapsed_pct": time_pct, "status": _status(planned, actual, time_pct)})
    return response(200, {
        "projects": out,
        "totals": {
            "planned_budget": round(sum(float(r["planned_budget"]) for r in out), 2),
            "actual_cost": round(sum(r["actual_cost"] for r in out), 2),
            "hours_logged": round(sum(float(r["hours_logged"]) for r in out), 2),
        },
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

        if parts == ["time"]:
            if method == "GET":
                return list_time(viewer, params)
            if method == "POST":
                return log_time(viewer, body)
        elif parts == ["time", "week"] and method == "GET":
            return my_week(viewer, params)
        elif len(parts) == 2 and parts[0] == "time" and method == "DELETE":
            return delete_time(viewer, parts[1])
        elif len(parts) == 2 and parts[0] == "time" and method in ("PATCH", "PUT"):
            return edit_time(viewer, parts[1], body)
        elif parts == ["costs"]:
            if method == "GET":
                return list_costs(viewer, params)
            if method == "POST":
                return add_cost(viewer, body)
        elif len(parts) == 2 and parts[0] == "costs" and method == "DELETE":
            return delete_cost(viewer, parts[1])
        elif parts == ["summary"] and method == "GET":
            return summary(viewer, params)
        elif parts == ["portfolio"] and method == "GET":
            return portfolio(viewer)

        return error(404, f"No route for {method} {path}")

    except ApiError as e:
        return error(e.status, e.message)
    except DatabaseUnavailable:
        logger.warning("Database unavailable: asking the client to retry")
        return error(503, "The database is starting up. Try again in a few seconds.")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unhandled error")
        return error(500, str(e))
