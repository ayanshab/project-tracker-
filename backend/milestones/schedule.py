"""
Milestone scheduling: dependencies, the critical path, and projected finish dates.

Shared by the milestones and projects services (keep the copies identical).

How it works, in plain terms:
    - Every milestone has planned dates and a list of milestones it depends on.
    - Forward pass: the earliest a milestone can start is the later of its planned start and
      the finish of everything it depends on. Walking the list in position order gives the
      earliest possible finish of the whole project.
    - Backward pass: the latest a milestone may finish without moving the project finish.
      Slack (float) = latest finish - earliest finish. Milestones with zero slack form the
      critical path: if any of them slips, the project finish slips with it.
    - Projection: completed milestones use their actual finish; one in progress is pushed out
      when its remaining work would not fit before its planned end; pending ones keep their
      planned length but start when their prerequisites actually finish. The latest projected
      finish is the projected project finish, and the gap to the planned end is the delay.
    - "What if": add `simulate={milestone_id: extra_days}` to see how a slip ripples through.

Dates in, dates out; nothing here touches the database.
"""
from datetime import date, datetime, timedelta
import math


def _days(a, b):
    return (b - a).days


def _progress_fraction(m):
    if m["status"] == "completed":
        return 1.0
    if m.get("tasks_total"):
        return m["tasks_completed"] / m["tasks_total"]
    return 0.5 if m["status"] == "in_progress" else 0.0


def _projected_end(m, today):
    """When one milestone will really finish, ignoring its prerequisites.

    Completed: the day it was completed. In progress: its planned end, pushed out if the work
    still open would not fit (remaining share of the work x planned length), or at least today
    when it is already overdue. Pending: its planned end (shifting for prerequisites comes later).
    """
    if m["status"] == "completed":
        done = m.get("completed_at")
        if isinstance(done, datetime):
            return done.date()
        return done or m["end_date"]
    if m["status"] == "in_progress":
        length = max(_days(m["start_date"], m["end_date"]), 1)
        remaining = math.ceil((1 - _progress_fraction(m)) * length) if m.get("tasks_total") else 0
        return max(m["end_date"], today + timedelta(days=remaining))
    return m["end_date"]


def build_schedule(project, milestones, today=None, simulate=None):
    """Critical path and projected dates for one project.

    milestones: rows with id, position, name, status, start_date, end_date, completed_at,
                tasks_total, tasks_completed, depends_on (list of milestone ids), in position order.
    simulate:   {milestone_id: extra_days} to test a slip before it happens.
    Returns {"milestones": [...], "critical_path": [ids], "earliest_finish", "projected_finish",
             "planned_finish", "delay_days", "critical_length_days"}.
    """
    today = today or date.today()
    simulate = {str(k): int(v) for k, v in (simulate or {}).items()}
    by_id = {str(m["id"]): dict(m) for m in milestones}
    order = [str(m["id"]) for m in milestones]
    for mid in order:
        by_id[mid]["depends_on"] = [str(d) for d in by_id[mid].get("depends_on", []) if str(d) in by_id]
    dependents = {mid: [] for mid in order}
    for mid in order:
        for d in by_id[mid]["depends_on"]:
            dependents[d].append(mid)

    # Forward pass on the plan: earliest start / finish from dependencies and planned dates.
    es, ef = {}, {}
    for mid in order:
        m = by_id[mid]
        length = _days(m["start_date"], m["end_date"])
        start = m["start_date"]
        for d in m["depends_on"]:
            start = max(start, ef[d])  # a handoff day is allowed: start on the day the prerequisite ends
        es[mid], ef[mid] = start, start + timedelta(days=length)
    earliest_finish = max(ef.values()) if ef else project["end_date"]

    # Backward pass: latest finish that keeps the earliest project finish. Slack 0 = critical.
    lf, ls = {}, {}
    for mid in reversed(order):
        m = by_id[mid]
        length = _days(m["start_date"], m["end_date"])
        finish = earliest_finish
        for d in dependents[mid]:
            finish = min(finish, ls[d])
        lf[mid], ls[mid] = finish, finish - timedelta(days=length)
    slack = {mid: _days(ef[mid], lf[mid]) for mid in order}
    critical = [mid for mid in order if slack[mid] <= 0]

    # Projection: where things really are, with slips rippling into dependents.
    p_start, p_end = {}, {}
    for mid in order:
        m = by_id[mid]
        length = _days(m["start_date"], m["end_date"])
        if m["status"] == "completed":
            p_start[mid] = m["start_date"]
            p_end[mid] = _projected_end(m, today)
        elif m["status"] == "in_progress":
            p_start[mid] = m["start_date"]
            p_end[mid] = _projected_end(m, today)
        else:
            start = max(m["start_date"], today)  # not started yet, so it cannot start in the past
            for d in m["depends_on"]:
                start = max(start, p_end[d])
            p_start[mid] = start
            p_end[mid] = start + timedelta(days=length)
        if mid in simulate and m["status"] != "completed":
            p_end[mid] += timedelta(days=simulate[mid])
    projected_finish = max(p_end.values()) if p_end else project["end_date"]
    planned_finish = max((m["end_date"] for m in by_id.values()), default=project["end_date"])
    delay = _days(planned_finish, projected_finish)

    out = []
    for mid in order:
        m = by_id[mid]
        slip = _days(m["end_date"], p_end[mid])
        on_critical = mid in critical
        out.append({
            "id": m["id"], "position": m["position"], "name": m["name"], "status": m["status"],
            "start_date": m["start_date"], "end_date": m["end_date"],
            "duration_days": _days(m["start_date"], m["end_date"]) + 1,
            "progress_pct": round(100 * _progress_fraction(m)),
            "depends_on": m["depends_on"],
            "dependents": dependents[mid],
            "earliest_start": es[mid], "earliest_finish": ef[mid],
            "latest_start": ls[mid], "latest_finish": lf[mid],
            "slack_days": max(slack[mid], 0),
            "critical": on_critical,
            "projected_start": p_start[mid], "projected_end": p_end[mid],
            "slip_days": slip,
            "overdue": m["status"] != "completed" and today > m["end_date"],
            # If this milestone slips one more day, how far does the project finish move?
            "finish_impact_per_day": 1 if on_critical else 0,
            "simulated_days": simulate.get(mid, 0),
        })
    return {
        "today": today,
        "milestones": out,
        "critical_path": critical,
        "critical_length_days": _days(min(es.values()), earliest_finish) + 1 if es else 0,
        "earliest_finish": earliest_finish,
        "planned_finish": planned_finish,
        "projected_finish": projected_finish,
        "delay_days": delay,
        "project_end": project["end_date"],
        "days_past_project_end": max(_days(project["end_date"], projected_finish), 0),
    }

