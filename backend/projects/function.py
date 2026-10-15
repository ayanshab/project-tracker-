"""
Projects service: projects, team assignments, budgets at a glance,
completion, archiving, and resource allocation.

Routes (all under /api/projects, all require Authorization: Bearer <token>):
    GET    /                          -> list visible projects with health + budget stats
                                         query: q, status=active|completed, archived=true (admin),
                                         health=green|yellow|red, at_risk=true
    GET    /allocation                -> load per person per week across active projects (admin, manager)
                                         query: from (snapped to Monday), weeks (default 12, max 26)
    GET    /capacity                  -> preview before assigning: user_id, hours, from, to, project_id?
                                         returns the busiest week, what's left, and a per-week breakdown
    GET    /{id}                      -> project detail with members and milestones
    POST   /                          -> create (admin, manager); optional members: [{user_id, hours_per_week,
                                         assigned_from?, assigned_to?}] staffs it in the same request
    PATCH  /{id}                      -> edit (admin, or the project's manager)
    POST   /{id}/complete             -> mark complete, auto-archives (admin, project manager)
    POST   /{id}/archive              -> archive (admin)
    POST   /{id}/unarchive            -> restore (admin)
    DELETE /{id}                      -> always refused: projects are archived, never deleted
    POST   /{id}/members              -> assign {user_id, hours_per_week, assigned_from?, assigned_to?}
    PATCH  /{id}/members/{user_id}    -> change hours or dates
    DELETE /{id}/members/{user_id}    -> remove an assignment
    POST   /approvals/{id}/approve|reject|cancel -> decide a pending project change

Maker-checker: changes to planned_budget, budget_hours or manager_id are not applied directly.
They become a pending request (a "reason" is required) that a different admin must approve.

Forecast: every project carries an earned value forecast (SPI, CPI, forecast cost and finish date).

Health: every project is scored green, yellow or red with the reasons, across four areas:
schedule (planned vs actual progress, critical path delay), budget (actual vs planned spend),
milestones (overdue milestones and their open tasks), resources (team members over 40 h/week).
projected_completion is the latest of the critical path projection, the earned value pace, and
the remaining budgeted hours at the team's weekly capacity.

Rules:
    - A project always has exactly one manager (admins pick it; managers own what they create).
    - Only admins see archived projects. Completing a project archives it.
    - Managers can assign their direct reports; admins can assign anyone active.
    - Nobody can be booked over 40 hours/week across overlapping active assignments.
"""

import logging
import re
from datetime import date, timedelta

from common import (
    ApiError,
    audit,
    changes,
    decide_change_request,
    pending_changes,
    request_change,
    split_sensitive,
    error,
    get_current_user,
    parse_event,
    require_fields,
    require_role,
    response,
)
from db import DatabaseUnavailable, ensure_schema, execute, query, query_one
from schedule import build_schedule
import math

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SERVICE_NAME = "projects"
WEEKLY_CAP = 40.0
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


# ---------- Validation helpers ----------

def _uuid(value, label="id"):
    if not value or not UUID_RE.match(str(value)):
        raise ApiError(400, f"Invalid {label}")
    return str(value)


def _date(value, label):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ApiError(400, f"{label} must be a date like 2026-10-31")


def _number(value, label, minimum=0.0, maximum=None):
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{label} must be a number")
    if num < minimum or (maximum is not None and num > maximum):
        limit = f" and {maximum}" if maximum is not None else ""
        raise ApiError(400, f"{label} must be between {minimum}{limit}")
    return num


def _text(value, label, required=True, max_len=200):
    text = (value or "").strip()
    if required and not text:
        raise ApiError(400, f"{label} is required")
    if len(text) > max_len:
        raise ApiError(400, f"{label} must be at most {max_len} characters")
    return text or None


# ---------- Access helpers ----------

def _project(project_id):
    return query_one("SELECT * FROM projects WHERE id = %s", (project_id,))


def _is_member(project_id, user_id):
    return query_one(
        "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s",
        (project_id, user_id),
    ) is not None


def _can_view(viewer, project):
    if viewer["role"] == "admin":
        return True
    if project["is_archived"]:
        return False
    if str(project["manager_id"]) == str(viewer["id"]):
        return True
    return _is_member(project["id"], viewer["id"])


def _can_manage(viewer, project):
    if viewer["role"] == "admin":
        return True
    return viewer["role"] == "manager" and str(project["manager_id"]) == str(viewer["id"]) \
        and not project["is_archived"]


def _load_for(viewer, project_id, manage=False):
    project = _project(_uuid(project_id))
    if not project or not _can_view(viewer, project):
        raise ApiError(404, "Project not found")
    if manage and not _can_manage(viewer, project):
        raise ApiError(403, "Only an admin or this project's manager can do that")
    return project


def _name(user_id):
    row = query_one("SELECT name FROM users WHERE id = %s", (user_id,))
    return row["name"] if row else None


def _validate_project_manager(manager_id):
    manager_id = _uuid(manager_id, "manager_id")
    mgr = query_one("SELECT role, is_archived FROM users WHERE id = %s", (manager_id,))
    if not mgr or mgr["is_archived"]:
        raise ApiError(400, "Manager not found or archived")
    if mgr["role"] not in ("manager", "admin"):
        raise ApiError(400, "A project manager must have the manager or admin role")
    return manager_id


# ---------- Stats ----------

STATS_SQL = """
SELECT p.*,
       mgr.name AS manager_name,
       COALESCE(tm.member_count, 0)   AS member_count,
       COALESCE(te.hours_logged, 0)   AS hours_logged,
       COALESCE(te.labor_cost, 0)     AS labor_cost,
       COALESCE(pc.other_cost, 0)     AS other_cost,
       COALESCE(ms.total, 0)          AS milestones_total,
       COALESCE(ms.done, 0)           AS milestones_completed
FROM projects p
JOIN users mgr ON mgr.id = p.manager_id
LEFT JOIN (SELECT project_id, COUNT(*) AS member_count
           FROM project_members GROUP BY project_id) tm ON tm.project_id = p.id
LEFT JOIN (SELECT t.project_id, SUM(t.hours) AS hours_logged,
                  SUM(t.hours * u.pay_rate) AS labor_cost
           FROM time_entries t JOIN users u ON u.id = t.user_id
           GROUP BY t.project_id) te ON te.project_id = p.id
LEFT JOIN (SELECT project_id, SUM(amount) AS other_cost
           FROM project_costs GROUP BY project_id) pc ON pc.project_id = p.id
LEFT JOIN (SELECT project_id, COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status = 'completed') AS done
           FROM milestones GROUP BY project_id) ms ON ms.project_id = p.id
"""


def _health(row, today=None):
    """Add progress, budget use, and at-risk reasons to a project row."""
    today = today or date.today()
    out = dict(row)
    total_days = max((row["end_date"] - row["start_date"]).days, 1)
    elapsed = min(max((today - row["start_date"]).days, 0), total_days)
    time_pct = round(100 * elapsed / total_days)
    progress_pct = round(100 * row["milestones_completed"] / row["milestones_total"]) \
        if row["milestones_total"] else 0
    total_cost = float(row["labor_cost"]) + float(row["other_cost"])
    budget_pct = round(100 * total_cost / float(row["planned_budget"])) \
        if float(row["planned_budget"]) > 0 else None
    hours_pct = round(100 * float(row["hours_logged"]) / float(row["budget_hours"])) \
        if float(row["budget_hours"]) > 0 else None

    reasons = []
    if row["status"] != "completed":
        if today > row["end_date"]:
            reasons.append("Past end date")
        elif row["milestones_total"] and time_pct - progress_pct >= 25:
            reasons.append("Milestones behind schedule")
        if budget_pct is not None and budget_pct > 100:
            reasons.append("Over budget")
        elif budget_pct is not None and budget_pct > time_pct + 25:
            reasons.append("Spending ahead of schedule")
        if hours_pct is not None and hours_pct > 100:
            reasons.append("Over budgeted hours")

    out.update({
        "time_elapsed_pct": time_pct,
        "progress_pct": progress_pct,
        "total_cost": round(total_cost, 2),
        "budget_used_pct": budget_pct,
        "hours_used_pct": hours_pct,
        "at_risk": bool(reasons),
        "risk_reasons": reasons,
    })
    return out


# ---------- Forecasting (earned value management) ----------

MILESTONE_PROGRESS_SQL = """
SELECT m.id, m.project_id, m.position, m.name, m.status, m.start_date, m.end_date, m.completed_at,
       COALESCE(t.total, 0) AS tasks_total, COALESCE(t.done, 0) AS tasks_completed,
       COALESCE(d.deps, '{}') AS depends_on
FROM milestones m
LEFT JOIN (SELECT milestone_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE completed) AS done
           FROM tasks GROUP BY milestone_id) t ON t.milestone_id = m.id
LEFT JOIN (SELECT milestone_id, array_agg(depends_on_id::text) AS deps
           FROM milestone_dependencies GROUP BY milestone_id) d ON d.milestone_id = m.id
"""


def _earned_fraction(m):
    """How much of a milestone's work is done: 100% when completed; for one in progress, the share
    of tasks checked off, or 50% when it has no tasks (the standard 50/50 earned-value rule)."""
    if m["status"] == "completed":
        return 1.0
    if m["status"] == "in_progress":
        return m["tasks_completed"] / m["tasks_total"] if m["tasks_total"] else 0.5
    return 0.0


def _forecast(p, stops, today=None):
    """Earned value forecast for one project.

    PV (planned value) = share of work scheduled to be done by today, from milestone dates.
    EV (earned value)  = share of work actually done, from milestone and task progress.
    AC (actual cost)   = labor (hours x pay rate) plus other costs.
    SPI = EV / PV (below 1 means behind schedule); CPI = EV$ / AC (below 1 means over cost).
    Forecast cost (EAC) = budget / CPI; forecast finish = planned duration / SPI.
    """
    today = today or date.today()
    if p["status"] == "completed":
        return {"available": False, "reason": "Project is completed."}
    if not stops:
        return {"available": False, "reason": "Add milestones to get a forecast."}
    if today < p["start_date"]:
        return {"available": False, "reason": "Project has not started yet."}

    durations = [max((m["end_date"] - m["start_date"]).days + 1, 1) for m in stops]
    total = sum(durations)
    pv = sum(d / total * min(max(((today - m["start_date"]).days + 1) / d, 0), 1) for m, d in zip(stops, durations))
    ev = sum(d / total * _earned_fraction(m) for m, d in zip(stops, durations))
    bac = float(p["planned_budget"])
    ac = float(p["labor_cost"]) + float(p["other_cost"])
    hours = float(p["hours_logged"])

    if pv < 0.05:
        return {"available": False, "reason": "Too early to forecast. Check back once more work is scheduled."}

    spi = ev / pv if pv > 0 else None
    cpi = (ev * bac) / ac if (ac > 0 and bac > 0 and ev > 0) else None
    if cpi:
        eac = bac / cpi
    elif ac > 0 and ev > 0:
        eac = ac / ev
    else:
        eac = None

    planned_days = max((p["end_date"] - p["start_date"]).days, 1)
    finish = None
    if ev >= 0.999:
        finish = today
    elif spi and spi > 0:
        finish = p["start_date"] + timedelta(days=round(planned_days / spi))
        finish = max(finish, today + timedelta(days=1))  # unfinished work cannot end in the past
    days_late = (finish - p["end_date"]).days if finish else None

    def band(value, good, fair):
        if value is None:
            return None
        return "good" if value >= good else "fair" if value >= fair else "poor"

    return {
        "available": True,
        "planned_pct": round(100 * pv),
        "earned_pct": round(100 * ev),
        "planned_value": round(pv * bac, 2),
        "earned_value": round(ev * bac, 2),
        "actual_cost": round(ac, 2),
        "spi": round(spi, 2) if spi is not None else None,
        "cpi": round(cpi, 2) if cpi is not None else None,
        "schedule_health": band(spi, 0.95, 0.8),
        "cost_health": band(cpi, 0.95, 0.85),
        "forecast_cost": round(eac, 2) if eac is not None else None,
        "forecast_variance": round(bac - eac, 2) if (eac is not None and bac > 0) else None,
        "forecast_hours": round(hours / ev, 1) if (ev > 0 and hours > 0) else None,
        "forecast_finish": finish,
        "days_late": days_late,
        "confidence": "low" if ev < 0.2 else "medium" if ev < 0.5 else "high",
    }


def _apply_forecast(p, stops):
    """Attach the forecast and replace rough heuristics with forecast-based risk reasons."""
    f = _forecast(p, stops)
    p["forecast"] = f
    if not f["available"]:
        return p
    reasons = [r for r in p["risk_reasons"] if r not in ("Milestones behind schedule", "Spending ahead of schedule")]
    if f["days_late"] is not None and f["days_late"] > 7 and "Past end date" not in reasons:
        reasons.append(f"Forecast to finish {f['days_late']} days late")
    bac = float(p["planned_budget"])
    if f["forecast_variance"] is not None and bac > 0 and f["forecast_variance"] < -0.05 * bac and "Over budget" not in reasons:
        reasons.append(f"Forecast ${-f['forecast_variance']:,.0f} over budget")
    p["risk_reasons"] = reasons
    p["at_risk"] = bool(reasons)
    return p


# ---------- Health scoring ----------
#
# Four areas, each checked for a yellow (watch) or red (act) condition:
#   schedule   planned progress vs actual progress (earned value SPI, forecast finish, critical path delay)
#   budget     actual spend vs planned spend (CPI, forecast cost, budget used vs time elapsed)
#   milestones overdue milestones and the open tasks on them, weighted by whether they are on the critical path
#   resources  anyone on the team booked over the 40-hour week during the rest of the project
# The project is red if any area is red, yellow if any is yellow, otherwise green.

def _team_load(project, members_assignments, today):
    """Busiest week per team member between today and the project end, across all their projects."""
    end = max(project["end_date"], today)
    out = []
    for name, assignments in members_assignments.items():
        weeks = weekly_load(assignments, today, end)
        peak = max((w["hours"] for w in weeks), default=0.0)
        out.append((name, peak))
    return out


def _score_health(p, stops, members_assignments=None, today=None):
    """Add health (green/yellow/red), health_reasons and a projected completion date to a project."""
    today = today or date.today()
    f = p.get("forecast") or {}
    reasons = []

    def add(area, level, text):
        reasons.append({"area": area, "level": level, "text": text})

    if p["status"] == "completed":
        p.update({"health": "green", "health_reasons": [], "projected_completion": p["end_date"],
                  "projected_completion_basis": "Completed", "schedule": None})
        return p

    sched = build_schedule(p, stops, today=today) if stops else None
    planned_days = max((p["end_date"] - p["start_date"]).days, 1)

    # Schedule: planned vs actual progress
    if today > p["end_date"]:
        add("schedule", "red", f"Past the end date by {(today - p['end_date']).days} days")
    elif sched and sched["days_past_project_end"] > max(14, 0.1 * planned_days):
        add("schedule", "red", f"Critical path finishes {sched['days_past_project_end']} days after the end date")
    elif sched and sched["days_past_project_end"] > 0:
        add("schedule", "yellow", f"Critical path finishes {sched['days_past_project_end']} days after the end date")
    if f.get("available"):
        if f["spi"] is not None and f["spi"] < 0.8:
            add("schedule", "red", f"Only {f['earned_pct']}% of the work is done where {f['planned_pct']}% was planned (SPI {f['spi']})")
        elif f["spi"] is not None and f["spi"] < 0.95:
            add("schedule", "yellow", f"{f['earned_pct']}% of the work is done where {f['planned_pct']}% was planned (SPI {f['spi']})")
        if f["days_late"] is not None and f["days_late"] > 7 and not any(r["area"] == "schedule" and r["level"] == "red" for r in reasons):
            add("schedule", "yellow" if f["days_late"] <= 14 else "red", f"At the current pace the project finishes {f['days_late']} days late")
    elif p["milestones_total"] and p["time_elapsed_pct"] - p["progress_pct"] >= 25:
        add("schedule", "yellow", f"{p['time_elapsed_pct']}% of the time has passed but only {p['progress_pct']}% of milestones are done")

    # Budget: actual vs planned spend
    bac = float(p["planned_budget"])
    if p["budget_used_pct"] is not None and p["budget_used_pct"] > 100:
        add("budget", "red", f"Over budget: {p['budget_used_pct']}% of ${bac:,.0f} spent")
    elif f.get("available") and f.get("forecast_variance") is not None and bac > 0 and f["forecast_variance"] < -0.10 * bac:
        add("budget", "red", f"Forecast ${-f['forecast_variance']:,.0f} over budget (CPI {f['cpi']})")
    elif f.get("available") and f.get("forecast_variance") is not None and bac > 0 and f["forecast_variance"] < -0.05 * bac:
        add("budget", "yellow", f"Forecast ${-f['forecast_variance']:,.0f} over budget (CPI {f['cpi']})")
    elif p["budget_used_pct"] is not None and p["budget_used_pct"] > p["time_elapsed_pct"] + 25:
        add("budget", "yellow", f"{p['budget_used_pct']}% of the budget spent with {p['time_elapsed_pct']}% of the time elapsed")
    if p["hours_used_pct"] is not None and p["hours_used_pct"] > 100:
        add("budget", "yellow", f"{p['hours_used_pct']}% of the budgeted hours used")

    # Milestones and tasks
    overdue = [m for m in stops if m["status"] != "completed" and today > m["end_date"]]
    if overdue:
        open_tasks = sum(m["tasks_total"] - m["tasks_completed"] for m in overdue)
        critical_ids = set(sched["critical_path"]) if sched else set()
        on_path = [m for m in overdue if str(m["id"]) in critical_ids]
        worst = max((today - m["end_date"]).days for m in overdue)
        text = f"{len(overdue)} overdue milestone{'s' if len(overdue) > 1 else ''}" \
               f" ({', '.join(m['name'] for m in overdue[:3])}{', more' if len(overdue) > 3 else ''})" \
               f"{f' with {open_tasks} open task{'s' if open_tasks != 1 else ''}' if open_tasks else ''}"
        add("milestones", "red" if (on_path and worst > 7) else "yellow", text + (", on the critical path" if on_path else ""))

    # Resources: anyone on the team over the 40-hour week in the weeks ahead
    for name, peak in _team_load(p, members_assignments or {}, today):
        if peak > WEEKLY_CAP:
            add("resources", "red", f"{name} is booked {peak:g} h/week across projects (limit {WEEKLY_CAP:g})")
        elif peak >= NEAR_CAPACITY:
            add("resources", "yellow", f"{name} is at {peak:g} h/week across projects")

    # Projected completion: the latest of the critical path projection, the earned value forecast,
    # and how long the remaining budgeted hours take at the team's weekly capacity.
    candidates = []
    if sched:
        candidates.append((sched["projected_finish"], "critical path at current progress"))
    if f.get("available") and f.get("forecast_finish"):
        candidates.append((f["forecast_finish"], "earned value pace (SPI)"))
    budget_hours, logged = float(p["budget_hours"]), float(p["hours_logged"])
    capacity = sum(float(a["hours_per_week"]) for rows in (members_assignments or {}).values() for a in rows
                   if str(a["project_id"]) == str(p["id"]) and a["assigned_to"] >= today)
    if budget_hours > logged and capacity > 0:
        weeks = (budget_hours - logged) / capacity
        candidates.append((today + timedelta(days=math.ceil(weeks * 7)), f"{budget_hours - logged:g} h of work left at {capacity:g} h/week"))
    if candidates:
        finish, basis = max(candidates, key=lambda c: c[0])
        finish = max(finish, today)
    else:
        finish, basis = p["end_date"], "planned end date"

    level = "red" if any(r["level"] == "red" for r in reasons) else "yellow" if reasons else "green"
    p.update({
        "health": level,
        "health_reasons": reasons,
        "projected_completion": finish,
        "projected_completion_basis": basis,
        "projected_days_late": max((finish - p["end_date"]).days, 0),
        "schedule": {k: sched[k] for k in ("critical_path", "critical_length_days", "earliest_finish",
                                            "projected_finish", "delay_days", "days_past_project_end")} if sched else None,
    })
    # Keep the older fields in step with the score so every page agrees.
    p["at_risk"] = level != "green" or p["at_risk"]
    return p


def _assignments_for_projects(project_ids, today):
    """{project_id: {member name: [assignments across ALL their active projects]}} for resource checks."""
    members = query(
        "SELECT pm.project_id, u.id AS user_id, u.name FROM project_members pm JOIN users u ON u.id = pm.user_id "
        "WHERE pm.project_id = ANY(%s) AND u.is_archived = FALSE",
        (list(project_ids),),
    )
    if not members:
        return {}
    user_ids = list({str(m["user_id"]) for m in members})
    rows = query(
        """
        SELECT pm.user_id, pm.project_id, p.name AS project_name, pm.hours_per_week,
               pm.assigned_from, COALESCE(pm.assigned_to, p.end_date) AS assigned_to
        FROM project_members pm JOIN projects p ON p.id = pm.project_id
        WHERE pm.user_id = ANY(%s::uuid[]) AND p.is_archived = FALSE AND p.status = 'active'
          AND COALESCE(pm.assigned_to, p.end_date) >= %s
        """,
        (user_ids, today),
    )
    by_user = {}
    for r in rows:
        by_user.setdefault(str(r["user_id"]), []).append(r)
    out = {}
    for m in members:
        out.setdefault(str(m["project_id"]), {})[m["name"]] = by_user.get(str(m["user_id"]), [])
    return out


def _hide_cost_forecast(p):
    p["health_reasons"] = [r for r in p.get("health_reasons", []) if r["area"] != "budget"]
    f = p.get("forecast") or {}
    for k in ("planned_value", "earned_value", "actual_cost", "cpi", "cost_health", "forecast_cost", "forecast_variance"):
        f.pop(k, None)


# ---------- Allocation ----------

# ---------- Capacity: hours per week across overlapping assignments ----------

NEAR_CAPACITY = 32.0  # 80% of the week: shown as "approaching capacity"


def _monday(d):
    return d - timedelta(days=d.weekday())


def capacity_status(hours):
    """Traffic light for a week's load: free, ok (within), near (80% or more), over (above the cap)."""
    if hours > WEEKLY_CAP:
        return "over"
    if hours >= NEAR_CAPACITY:
        return "near"
    return "ok" if hours > 0 else "free"


def _assignments_between(user_id, start, end, exclude_project=None):
    """Active assignments for one person that touch [start, end], with the project's name and end date."""
    return query(
        """
        SELECT pm.project_id, p.name AS project_name, pm.hours_per_week,
               pm.assigned_from, COALESCE(pm.assigned_to, p.end_date) AS assigned_to
        FROM project_members pm
        JOIN projects p ON p.id = pm.project_id
        WHERE pm.user_id = %s
          AND p.is_archived = FALSE AND p.status = 'active'
          AND (%s::uuid IS NULL OR pm.project_id <> %s::uuid)
          AND pm.assigned_from <= %s
          AND COALESCE(pm.assigned_to, p.end_date) >= %s
        ORDER BY p.name
        """,
        (user_id, exclude_project, exclude_project, end, start),
    )


def weekly_load(assignments, start, end):
    """Split assignments into Monday-start weeks from `start` to `end`.

    Each week sums the hours of every assignment active during that week, so two projects
    that overlap add up and two that run back to back do not. Returns one row per week:
    {week_start, hours, status, assignments: [{project_id, project_name, hours}]}.
    """
    weeks = []
    monday = _monday(start)
    last = _monday(end)
    while monday <= last:
        sunday = monday + timedelta(days=6)
        active = [a for a in assignments if a["assigned_from"] <= sunday and a["assigned_to"] >= monday]
        hours = float(sum(float(a["hours_per_week"]) for a in active))
        weeks.append({
            "week_start": monday, "hours": hours, "status": capacity_status(hours),
            "assignments": [{"project_id": a["project_id"], "project_name": a["project_name"],
                             "hours": float(a["hours_per_week"])} for a in active],
        })
        monday += timedelta(days=7)
    return weeks


def _overlap_hours(user_id, start, end, exclude_project=None):
    """Busiest week for a person between start and end: the most hours they are booked in any one week.

    Assignments on different projects only count against each other in the weeks they actually
    share, so a person can take 30 h on a project that ends in March and 30 h on one starting in April.
    """
    weeks = weekly_load(_assignments_between(user_id, start, end, exclude_project), start, end)
    return max((w["hours"] for w in weeks), default=0.0)


def _check_capacity(user_id, hours, start, end, project_id):
    booked = _overlap_hours(user_id, start, end, exclude_project=project_id)
    if booked + hours > WEEKLY_CAP:
        raise ApiError(
            409,
            f"Over-allocation: this person is already booked {booked:g} h/week in that period. "
            f"Adding {hours:g} would exceed the {WEEKLY_CAP:g} h/week limit "
            f"({WEEKLY_CAP - booked:g} h/week available).",
        )


def _window(params, default_weeks=12, max_weeks=26):
    """Week window for the heatmap: `from` (any date, snapped to its Monday) and `weeks`."""
    start = _monday(_date(params["from"], "from") if params.get("from") else date.today())
    try:
        weeks = max(1, min(int(params.get("weeks", default_weeks)), max_weeks))
    except ValueError:
        raise ApiError(400, "weeks must be a number")
    return start, start + timedelta(days=7 * weeks - 1)


def allocation(viewer, params=None):
    """Everyone the viewer manages, with their load for every week in the window (default: 12 weeks from now)."""
    require_role(viewer, "admin", "manager")
    params = params or {}
    start, end = _window(params)
    scope, args = "", []
    if viewer["role"] == "manager":
        scope = "AND (u.manager_id = %s OR u.id = %s)"
        args = [viewer["id"], viewer["id"]]
    rows = query(
        f"""
        SELECT u.id, u.name, u.job_title, u.role,
               COALESCE(json_agg(json_build_object(
                   'project_id', p.id, 'project_name', p.name,
                   'hours_per_week', pm.hours_per_week,
                   'assigned_from', pm.assigned_from, 'assigned_to', COALESCE(pm.assigned_to, p.end_date)))
                   FILTER (WHERE p.id IS NOT NULL), '[]') AS assignments
        FROM users u
        LEFT JOIN project_members pm ON pm.user_id = u.id
        LEFT JOIN projects p ON p.id = pm.project_id
             AND p.is_archived = FALSE AND p.status = 'active'
        WHERE u.is_archived = FALSE {scope}
        GROUP BY u.id
        ORDER BY u.name
        """,
        args,
    )
    this_week = _monday(date.today())
    for r in rows:
        for a in r["assignments"]:
            a["assigned_from"] = date.fromisoformat(a["assigned_from"])
            a["assigned_to"] = date.fromisoformat(a["assigned_to"])
        weeks = weekly_load(r["assignments"], start, end)
        now = weekly_load(r["assignments"], this_week, this_week)[0]
        r["weeks"] = weeks
        r["hours_this_week"] = now["hours"]
        r["available_hours"] = max(WEEKLY_CAP - now["hours"], 0)
        r["over_allocated"] = now["hours"] > WEEKLY_CAP
        r["peak_hours"] = max((w["hours"] for w in weeks), default=0.0)
        r["weeks_over"] = sum(1 for w in weeks if w["status"] == "over")
        r["weeks_near"] = sum(1 for w in weeks if w["status"] == "near")
        r["active_projects"] = len({a["project_id"] for a in r["assignments"]
                                    if a["assigned_from"] <= end and a["assigned_to"] >= start})
    rows.sort(key=lambda r: (-r["peak_hours"], r["name"]))
    return response(200, {
        "weekly_cap": WEEKLY_CAP, "near_capacity": NEAR_CAPACITY,
        "from": start, "to": end, "weeks": [start + timedelta(days=7 * i) for i in range((end - start).days // 7 + 1)],
        "people": rows,
    })


def capacity(viewer, params):
    """Preview one person's load before assigning them: would `hours` more per week fit between from and to?

    Managers can ask about their own reports (and themselves); admins about anyone.
    """
    require_role(viewer, "admin", "manager")
    require_fields(params, "user_id")
    user_id = _uuid(params["user_id"], "user_id")
    person = query_one("SELECT id, name, manager_id, is_archived FROM users WHERE id = %s", (user_id,))
    if not person or person["is_archived"]:
        raise ApiError(404, "User not found")
    if viewer["role"] == "manager" and str(person["manager_id"]) != str(viewer["id"]) \
            and str(person["id"]) != str(viewer["id"]):
        raise ApiError(403, "Managers can only check their own direct reports")
    start = _date(params["from"], "from") if params.get("from") else date.today()
    end = _date(params["to"], "to") if params.get("to") else start + timedelta(days=7 * 12 - 1)
    if end < start:
        raise ApiError(400, "to must be on or after from")
    hours = _number(params.get("hours", 0), "hours", 0, WEEKLY_CAP)
    exclude = _uuid(params["project_id"], "project_id") if params.get("project_id") else None
    weeks = weekly_load(_assignments_between(user_id, start, end, exclude), start, end)
    for w in weeks:
        w["with_new"] = w["hours"] + hours
        w["status_with_new"] = capacity_status(w["with_new"])
    peak = max((w["hours"] for w in weeks), default=0.0)
    worst = max(weeks, key=lambda w: w["hours"]) if weeks else None
    return response(200, {
        "user_id": user_id, "name": person["name"], "weekly_cap": WEEKLY_CAP, "near_capacity": NEAR_CAPACITY,
        "from": start, "to": end, "hours": hours,
        "booked_peak": peak, "available": max(WEEKLY_CAP - peak, 0),
        "with_new_peak": peak + hours, "status": capacity_status(peak + hours),
        "over_by": max(peak + hours - WEEKLY_CAP, 0),
        "worst_week": worst["week_start"] if worst else None,
        "weeks_over": sum(1 for w in weeks if w["status_with_new"] == "over"),
        "weeks": weeks,
    })


# ---------- Project handlers ----------

def list_projects(viewer, params):
    where, args = [], []
    show_archived = viewer["role"] == "admin" and str(params.get("archived", "")).lower() == "true"
    where.append("p.is_archived = %s")
    args.append(show_archived)
    if viewer["role"] != "admin":
        where.append("(p.manager_id = %s OR EXISTS (SELECT 1 FROM project_members x "
                     "WHERE x.project_id = p.id AND x.user_id = %s))")
        args += [viewer["id"], viewer["id"]]
    if str(params.get("mine", "")).lower() == "true":
        # Only projects the caller is staffed on (a manager's own hands-on work).
        where.append("EXISTS (SELECT 1 FROM project_members y WHERE y.project_id = p.id AND y.user_id = %s)")
        args.append(viewer["id"])
    if params.get("status") in ("active", "completed"):
        where.append("p.status = %s")
        args.append(params["status"])
    if params.get("q"):
        where.append("(p.name ILIKE %s OR p.description ILIKE %s OR mgr.name ILIKE %s)")
        args += [f"%{params['q'].strip()}%"] * 3

    rows = query(f"{STATS_SQL} WHERE {' AND '.join(where)} ORDER BY p.end_date, p.name", args)
    projects = [_health(r) for r in rows]
    if projects:
        today = date.today()
        stops = query(
            f"{MILESTONE_PROGRESS_SQL} WHERE m.project_id = ANY(%s) ORDER BY m.project_id, m.position",
            ([p["id"] for p in projects],),
        )
        by_project = {}
        for s in stops:
            s["overdue"] = s["status"] != "completed" and today > s["end_date"]
            by_project.setdefault(s["project_id"], []).append(s)
        loads = _assignments_for_projects([p["id"] for p in projects], today)
        for p in projects:
            p["milestones"] = by_project.get(p["id"], [])
            _apply_forecast(p, p["milestones"])
            _score_health(p, p["milestones"], loads.get(str(p["id"]), {}), today)
            if viewer["role"] == "employee":
                _hide_cost_forecast(p)
                for key in ("labor_cost", "other_cost", "total_cost", "planned_budget", "budget_used_pct"):
                    p.pop(key, None)
    if str(params.get("at_risk", "")).lower() == "true":
        projects = [p for p in projects if p["at_risk"]]
    if params.get("health") in ("green", "yellow", "red"):
        projects = [p for p in projects if p["health"] == params["health"]]
    return response(200, {"projects": projects})


def _project_detail(viewer, project_id):
    project = _load_for(viewer, project_id)
    row = query_one(f"{STATS_SQL} WHERE p.id = %s", (project["id"],))
    detail = _health(row)
    stops = query(f"{MILESTONE_PROGRESS_SQL} WHERE m.project_id = %s ORDER BY m.position", (project["id"],))
    _apply_forecast(detail, stops)
    _score_health(detail, stops, _assignments_for_projects([project["id"]], date.today()).get(str(project["id"]), {}))
    members = query(
        """
        SELECT u.id AS user_id, u.name, u.email, u.job_title, u.role,
               pm.hours_per_week, pm.assigned_from, pm.assigned_to,
               COALESCE((SELECT SUM(hours) FROM time_entries t
                         WHERE t.user_id = u.id AND t.project_id = pm.project_id), 0) AS hours_logged
        FROM project_members pm JOIN users u ON u.id = pm.user_id
        WHERE pm.project_id = %s ORDER BY u.name
        """,
        (project["id"],),
    )
    milestones = query(
        "SELECT id, position, name, start_date, end_date, status FROM milestones "
        "WHERE project_id = %s ORDER BY position",
        (project["id"],),
    )
    detail["members"] = members
    detail["milestones"] = milestones
    detail["can_manage"] = _can_manage(viewer, project)
    if detail["can_manage"]:
        detail["pending_changes"] = pending_changes("project", project["id"])
    if viewer["role"] == "employee":
        _hide_cost_forecast(detail)
        for key in ("labor_cost", "other_cost", "total_cost", "planned_budget", "budget_used_pct"):
            detail.pop(key, None)
        # Employees only get full details for their immediate team (same manager) and their manager.
        # Colleagues from other teams on the same project are shown by name and title only.
        team = {str(r["id"]) for r in query(
            "SELECT id FROM users WHERE id = %s OR id = %s OR (manager_id IS NOT NULL AND manager_id = %s)",
            (viewer["id"], viewer["manager_id"], viewer["manager_id"]),
        )}
        for m in members:
            if str(m["user_id"]) not in team:
                m.pop("email", None)
                m.pop("hours_logged", None)
                m["other_team"] = True
    return detail


def get_project(viewer, project_id, status=200, **extra):
    return response(status, {"project": _project_detail(viewer, project_id), **extra})


def create_project(viewer, body):
    require_role(viewer, "admin", "manager")
    require_fields(body, "name", "start_date", "end_date")
    name = _text(body["name"], "name")
    start = _date(body["start_date"], "start_date")
    end = _date(body["end_date"], "end_date")
    if end < start:
        raise ApiError(400, "end_date must be on or after start_date")
    if viewer["role"] == "admin":
        require_fields(body, "manager_id")
        manager_id = _validate_project_manager(body["manager_id"])
    else:
        manager_id = str(viewer["id"])

    # Optional starting team. Every person is checked before anything is written,
    # so a bad entry never leaves a half-staffed project behind.
    draft = {"id": None, "start_date": start, "end_date": end}
    members = body.get("members") or []
    if not isinstance(members, list):
        raise ApiError(400, "members must be a list")
    seen, team = set(), []
    for entry in members:
        if not isinstance(entry, dict):
            raise ApiError(400, "Each member needs a user_id")
        member = _validated_member(viewer, draft, entry)
        if member[0] in seen:
            raise ApiError(400, "The same person is listed twice")
        seen.add(member[0])
        team.append(member)

    row = execute(
        """
        INSERT INTO projects (name, description, manager_id, start_date, end_date, budget_hours, planned_budget)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            name,
            _text(body.get("description"), "description", required=False, max_len=2000),
            manager_id, start, end,
            _number(body.get("budget_hours", 0), "budget_hours"),
            _number(body.get("planned_budget", 0), "planned_budget"),
        ),
    )
    audit(viewer, "project.create", "project", row["id"], name, {
        "manager_id": manager_id, "start_date": start, "end_date": end,
        "budget_hours": body.get("budget_hours", 0), "planned_budget": body.get("planned_budget", 0)})
    project = {"id": row["id"], "name": name}
    for member in team:
        _insert_member(viewer, project, *member)
    return get_project(viewer, row["id"], status=201)


def _validated_project_changes(viewer, project, body):
    """Validate every field in body and return {column: clean value}. Applies nothing."""
    out = {}
    if "name" in body:
        out["name"] = _text(body["name"], "name")
    if "description" in body:
        out["description"] = _text(body["description"], "description", required=False, max_len=2000)
    if "budget_hours" in body:
        out["budget_hours"] = _number(body["budget_hours"], "budget_hours")
    if "planned_budget" in body:
        out["planned_budget"] = _number(body["planned_budget"], "planned_budget")
    if "manager_id" in body:
        if viewer["role"] != "admin":
            raise ApiError(403, "Only an admin can change a project's manager")
        out["manager_id"] = _validate_project_manager(body["manager_id"])
    if "start_date" in body or "end_date" in body:
        start = _date(body["start_date"], "start_date") if "start_date" in body else project["start_date"]
        end = _date(body["end_date"], "end_date") if "end_date" in body else project["end_date"]
        if end < start:
            raise ApiError(400, "end_date must be on or after start_date")
        outside = query_one(
            "SELECT name FROM milestones WHERE project_id = %s AND (start_date < %s OR end_date > %s) LIMIT 1",
            (project["id"], start, end),
        )
        if outside:
            raise ApiError(409, f"Milestone '{outside['name']}' falls outside the new dates. Adjust it first.")
        out["start_date"], out["end_date"] = start, end
    return out


def _apply_project_changes(viewer, project, values, audit_extra=None):
    if not values:
        return
    cols = list(values)
    execute(
        f"UPDATE projects SET {', '.join(f'{c} = %s' for c in cols)}, updated_at = now() WHERE id = %s",
        [values[c] for c in cols] + [project["id"]],
    )
    after = _project(project["id"])
    diff = changes(project, after, cols)
    if diff:
        action = "project.reassign_manager" if "manager_id" in diff else "project.update"
        audit(viewer, action, "project", project["id"], after["name"], {"changes": diff, **(audit_extra or {})})


def update_project(viewer, project_id, body):
    project = _load_for(viewer, project_id, manage=True)
    if project["status"] == "completed":
        raise ApiError(409, "Completed projects cannot be edited")
    cleaned = _validated_project_changes(viewer, project, body)
    if not cleaned:
        raise ApiError(400, "No editable fields provided")
    # Budget and project-manager changes need a second admin's approval (maker-checker).
    now, later = split_sensitive("project", project, cleaned)
    pending = request_change(viewer, "project", project, later, body.get("reason"))
    _apply_project_changes(viewer, project, now)
    return get_project(viewer, project["id"], pending_approval=pending)


def apply_approved_project_change(approver, req):
    project = _project(str(req["entity_id"]))
    if not project or project["status"] == "completed" or project["is_archived"]:
        raise ApiError(409, "The project is completed or archived.")
    cleaned = _validated_project_changes(approver, project, req["payload"])
    _apply_project_changes(approver, project, cleaned, {"approved_request": str(req["id"]),
                                                        "requested_by": str(req["requested_by"])})


def complete_project(viewer, project_id):
    project = _load_for(viewer, project_id, manage=True)
    if project["status"] == "completed":
        raise ApiError(409, "Project is already completed")
    open_ms = query_one(
        "SELECT COUNT(*) AS n FROM milestones WHERE project_id = %s AND status <> 'completed'",
        (project["id"],),
    )["n"]
    if open_ms:
        raise ApiError(409, f"{open_ms} milestone(s) are not completed yet")
    execute(
        "UPDATE projects SET status = 'completed', completed_at = now(), is_archived = TRUE, "
        "updated_at = now() WHERE id = %s",
        (project["id"],),
    )
    audit(viewer, "project.complete", "project", project["id"], project["name"], {"auto_archived": True})
    return response(200, {"message": "Project completed and moved to the archive", "id": project["id"]})


def set_archived(viewer, project_id, archived):
    require_role(viewer, "admin")
    project = _project(_uuid(project_id))
    if not project:
        raise ApiError(404, "Project not found")
    execute("UPDATE projects SET is_archived = %s, updated_at = now() WHERE id = %s",
            (archived, project["id"]))
    audit(viewer, "project.archive" if archived else "project.unarchive", "project", project["id"], project["name"])
    return response(200, {"id": project["id"], "is_archived": archived})


# ---------- Member handlers ----------

def _member_window(body, project, current=None):
    start = _date(body["assigned_from"], "assigned_from") if body.get("assigned_from") \
        else (current["assigned_from"] if current else max(project["start_date"], date.today()))
    if "assigned_to" in body:
        end = _date(body["assigned_to"], "assigned_to") if body["assigned_to"] else None
    else:
        end = current["assigned_to"] if current else None
    if start < project["start_date"] or start > project["end_date"]:
        raise ApiError(400, "assigned_from must fall within the project dates")
    if end and (end < start or end > project["end_date"]):
        raise ApiError(400, "assigned_to must be between assigned_from and the project end date")
    return start, end


def _validated_member(viewer, project, body):
    """Check one assignment and return (user_id, hours, start, end). Writes nothing.
    `project` may be a draft with id None while a new project is being created."""
    require_fields(body, "user_id")
    user_id = _uuid(body["user_id"], "user_id")
    person = query_one("SELECT id, name, role, manager_id, is_archived FROM users WHERE id = %s", (user_id,))
    if not person or person["is_archived"]:
        raise ApiError(400, "User not found or archived")
    if viewer["role"] == "manager" and str(person["manager_id"]) != str(viewer["id"]) \
            and str(person["id"]) != str(viewer["id"]):
        raise ApiError(403, f"Managers can only assign their own direct reports ({person['name']} is not)")
    if project["id"] and _is_member(project["id"], user_id):
        raise ApiError(409, f"{person['name']} is already on this project")
    hours = _number(body.get("hours_per_week", 8), "hours_per_week", 0.5, WEEKLY_CAP)
    start, end = _member_window(body, project)
    booked = _overlap_hours(user_id, start, end or project["end_date"], exclude_project=project["id"])
    if booked + hours > WEEKLY_CAP:
        raise ApiError(
            409,
            f"Over-allocation: {person['name']} is already booked {booked:g} h/week in that period. "
            f"Adding {hours:g} would exceed the {WEEKLY_CAP:g} h/week limit "
            f"({WEEKLY_CAP - booked:g} h/week available).",
        )
    return user_id, hours, start, end


def _insert_member(viewer, project, user_id, hours, start, end):
    execute(
        "INSERT INTO project_members (project_id, user_id, hours_per_week, assigned_from, assigned_to) "
        "VALUES (%s, %s, %s, %s, %s)",
        (project["id"], user_id, hours, start, end),
    )
    audit(viewer, "project.member_add", "project", project["id"], project["name"],
          {"user_id": user_id, "user_name": _name(user_id), "hours_per_week": hours,
           "assigned_from": start, "assigned_to": end})


def add_member(viewer, project_id, body):
    project = _load_for(viewer, project_id, manage=True)
    if project["status"] == "completed":
        raise ApiError(409, "Cannot assign people to a completed project")
    _insert_member(viewer, project, *_validated_member(viewer, project, body))
    return get_project(viewer, project["id"], status=201)


def update_member(viewer, project_id, user_id, body):
    project = _load_for(viewer, project_id, manage=True)
    user_id = _uuid(user_id, "user_id")
    current = query_one(
        "SELECT * FROM project_members WHERE project_id = %s AND user_id = %s", (project["id"], user_id)
    )
    if not current:
        raise ApiError(404, "That person is not on this project")
    hours = _number(body["hours_per_week"], "hours_per_week", 0.5, WEEKLY_CAP) \
        if "hours_per_week" in body else float(current["hours_per_week"])
    start, end = _member_window(body, project, current)
    _check_capacity(user_id, hours, start, end or project["end_date"], project["id"])
    execute(
        "UPDATE project_members SET hours_per_week = %s, assigned_from = %s, assigned_to = %s "
        "WHERE project_id = %s AND user_id = %s",
        (hours, start, end, project["id"], user_id),
    )
    diff = changes(current, {"hours_per_week": hours, "assigned_from": start, "assigned_to": end},
                   ("hours_per_week", "assigned_from", "assigned_to"))
    if diff:
        audit(viewer, "project.member_update", "project", project["id"], project["name"],
              {"user_id": user_id, "user_name": _name(user_id), "changes": diff})
    return get_project(viewer, project["id"])


def remove_member(viewer, project_id, user_id):
    project = _load_for(viewer, project_id, manage=True)
    user_id = _uuid(user_id, "user_id")
    execute(
        "DELETE FROM milestone_members WHERE user_id = %s AND milestone_id IN "
        "(SELECT id FROM milestones WHERE project_id = %s)",
        (user_id, project["id"]),
    )
    removed = execute(
        "DELETE FROM project_members WHERE project_id = %s AND user_id = %s", (project["id"], user_id)
    )
    if not removed:
        raise ApiError(404, "That person is not on this project")
    audit(viewer, "project.member_remove", "project", project["id"], project["name"],
          {"user_id": user_id, "user_name": _name(user_id)})
    return get_project(viewer, project["id"])


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
                return list_projects(viewer, params)
            if method == "POST":
                return create_project(viewer, body)
        elif parts == ["allocation"] and method == "GET":
            return allocation(viewer, params)
        elif parts == ["capacity"] and method == "GET":
            return capacity(viewer, params)
        elif len(parts) == 3 and parts[0] == "approvals" and method == "POST" and parts[2] in ("approve", "reject", "cancel"):
            req = decide_change_request(viewer, _uuid(parts[1]), parts[2], body, "project", apply_approved_project_change)
            return response(200, {"request": req})
        elif len(parts) == 1:
            if method == "GET":
                return get_project(viewer, parts[0])
            if method in ("PATCH", "PUT"):
                return update_project(viewer, parts[0], body)
            if method == "DELETE":
                return error(405, "Projects cannot be deleted. Ask an admin to archive it instead.")
        elif len(parts) == 2 and method == "POST":
            if parts[1] == "complete":
                return complete_project(viewer, parts[0])
            if parts[1] == "archive":
                return set_archived(viewer, parts[0], True)
            if parts[1] == "unarchive":
                return set_archived(viewer, parts[0], False)
            if parts[1] == "members":
                return add_member(viewer, parts[0], body)
        elif len(parts) == 3 and parts[1] == "members":
            if method in ("PATCH", "PUT"):
                return update_member(viewer, parts[0], parts[2], body)
            if method == "DELETE":
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
