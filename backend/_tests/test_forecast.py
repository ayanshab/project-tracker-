"""
End-to-end tests for earned value forecasting, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    POSTGRES_NAME=test_db python3 backend/_tests/test_forecast.py

WARNING: creates test users and projects. Use a scratch database (test_db).
"""
import os, sys, json, uuid, importlib.util
from datetime import date, timedelta
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
def load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(BACKEND, n, "function.py")); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
auth, users, proj, mil, bud = (load(n) for n in ("auth", "users", "projects", "milestones", "budgets"))
def call(mod, method, path, body=None, token=None, qs=None, ip=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method, "sourceIp": "10.0.0.9"}}, "headers": {"User-Agent": "audit-test"}, "queryStringParameters": qs}
    if ip: ev["headers"]["X-Forwarded-For"] = ip
    if token: ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None: ev["body"] = json.dumps(body)
    r = mod.handler(ev); return r["statusCode"], json.loads(r["body"])
def check(label, got, want):
    ok = got[0] == want; print(("PASS" if ok else "FAIL"), label, got[0], "" if ok else got[1]); assert ok; return got[1]
def log(token, **qs):
    return call(users, "GET", "/api/users/audit", None, token, qs)[1]["entries"]
tag = uuid.uuid4().hex[:8]
A = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": "Admin123!"})[1]["token"]
def first_login(username, temp_password):
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password})[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
def person(name, role, mgr=None, pay=50):
    d = call(users, "POST", "/api/users", {"name": f"{name} {tag}", "email": f"{name.lower()}@{tag}.test", "role": role, "manager_id": mgr, "pay_rate": pay}, A)[1]
    return d["user"], first_login(d["user"]["username"], d["temporary_password"])

today = date.today(); d = lambda n: (today + timedelta(days=n)).isoformat()
mgr, M = person("Fmgr", "manager", pay=80)
emp, E = person("Fwork", "employee", mgr["id"], pay=50)
p = call(proj, "POST", "/api/projects", {"name": f"Forecast P {tag}", "start_date": d(-50), "end_date": d(50), "planned_budget": 10000, "budget_hours": 300}, M)[1]["project"]
P = f"/api/projects/{p['id']}"
s, b = call(proj, "GET", P, None, M)
assert not b["project"]["forecast"]["available"]; print("PASS no milestones, no forecast:", b["project"]["forecast"]["reason"])
call(proj, "POST", f"{P}/members", {"user_id": emp["id"], "hours_per_week": 30, "assigned_from": d(-50)}, M)
m1 = call(mil, "POST", "/api/milestones", {"project_id": p["id"], "name": "Plan", "start_date": d(-50), "end_date": d(-26)}, M)[1]["milestone"]
m2 = call(mil, "POST", "/api/milestones", {"project_id": p["id"], "name": "Build", "start_date": d(-25), "end_date": d(24)}, M)[1]["milestone"]
call(mil, "POST", "/api/milestones", {"project_id": p["id"], "name": "Ship", "start_date": d(25), "end_date": d(50)}, M)
call(mil, "POST", f"/api/milestones/{m1['id']}/start", None, M); call(mil, "POST", f"/api/milestones/{m1['id']}/complete", None, M)
call(mil, "POST", f"/api/milestones/{m2['id']}/start", None, M)
call(mil, "POST", f"/api/milestones/{m2['id']}/members", {"user_id": emp["id"]}, M)
tasks = [call(mil, "POST", f"/api/milestones/{m2['id']}/tasks", {"title": f"T{i}"}, M)[1]["milestone"]["tasks"] for i in range(4)][-1]
call(mil, "PATCH", f"/api/milestones/tasks/{tasks[0]['id']}", {"completed": True}, E)
logged, n = 0, 1
while logged < 60:
    day = today - timedelta(days=n); n += 1
    if day.weekday() < 5:
        call(bud, "POST", "/api/budgets/time", {"project_id": p["id"], "hours": 6, "work_date": day.isoformat()}, E); logged += 6
call(bud, "POST", "/api/budgets/costs", {"project_id": p["id"], "category": "software", "amount": 1000}, M)

f = call(proj, "GET", P, None, M)[1]["project"]["forecast"]
# Expected values from the earned value formulas
dur = [25, 50, 26]; tot = sum(dur)
pv = (25 + 26) / tot                  # M1 fully scheduled, 26 of M2's 50 days elapsed, M3 not yet
ev = (25 * 1 + 50 * 0.25) / tot       # M1 done, M2 one task of four
ac = 60 * 50 + 1000
spi, cpi = ev / pv, ev * 10000 / ac
eac = 10000 / cpi
print(f"    planned {f['planned_pct']}% earned {f['earned_pct']}%  SPI {f['spi']}  CPI {f['cpi']}  forecast cost ${f['forecast_cost']:,.0f}  finish {f['forecast_finish']} ({f['days_late']} days late)")
assert f["available"] and f["planned_pct"] == round(100 * pv) and f["earned_pct"] == round(100 * ev)
assert abs(f["spi"] - spi) < 0.011 and abs(f["cpi"] - cpi) < 0.011 and abs(f["forecast_cost"] - eac) < 1
expected_finish = date.fromisoformat(d(-50)) + timedelta(days=round(100 / spi))
assert f["forecast_finish"] == expected_finish.isoformat() and f["days_late"] == (expected_finish - date.fromisoformat(d(50))).days
assert f["schedule_health"] == "poor" and f["cost_health"] == "fair"
print("PASS SPI, CPI, forecast cost and finish date match the formulas")
detail = call(proj, "GET", P, None, M)[1]["project"]
print("    risk reasons:", detail["risk_reasons"])
assert any(r.startswith("Forecast to finish") for r in detail["risk_reasons"]) and any(r.startswith("Forecast $") for r in detail["risk_reasons"])
assert "Milestones behind schedule" not in detail["risk_reasons"]; print("PASS forecast drives the at-risk reasons")
lst = next(x for x in call(proj, "GET", "/api/projects", None, M)[1]["projects"] if x["id"] == p["id"])
assert lst["forecast"]["forecast_cost"] == f["forecast_cost"]; print("PASS project list carries the same forecast")
ef = call(proj, "GET", P, None, E)[1]["project"]["forecast"]
assert "forecast_cost" not in ef and "cpi" not in ef and "forecast_finish" in ef; print("PASS employees see schedule forecast only, no cost figures")
el = next(x for x in call(proj, "GET", "/api/projects", None, E)[1]["projects"] if x["id"] == p["id"])
assert "planned_budget" not in el and "total_cost" not in el; print("PASS project list hides budget figures from employees")
# ---------- Health score: green / yellow / red with reasons ----------
h = call(proj, "GET", P, None, M)[1]["project"]
print("    health:", h["health"], [f"{r['area']}/{r['level']}: {r['text']}" for r in h["health_reasons"]])
areas = {r["area"] for r in h["health_reasons"]}
assert h["health"] == "red" and {"schedule", "budget"} <= areas, h["health_reasons"]
assert h["projected_completion"] >= today.isoformat() and h["projected_completion_basis"]; print("PASS scored with reasons; projected completion", h["projected_completion"], "from", h["projected_completion_basis"])
assert h["schedule"] and h["schedule"]["critical_path"] and h["schedule"]["critical_length_days"] == 101; print("PASS detail carries the critical path summary")
eh = call(proj, "GET", P, None, E)[1]["project"]
assert eh["health"] == h["health"] and not any(r["area"] == "budget" for r in eh["health_reasons"]); print("PASS employees see the same colour but no budget reasons")
for t in tasks[1:]:
    call(mil, "PATCH", f"/api/milestones/tasks/{t['id']}", {"completed": True}, E)
f2 = call(proj, "GET", P, None, M)[1]["project"]["forecast"]
assert f2["spi"] > f["spi"]; print(f"PASS finishing tasks improves the forecast: SPI {f['spi']} to {f2['spi']}")
# A fresh project with a clean plan and nothing wrong is green.
g = call(proj, "POST", "/api/projects", {"name": f"Green P {tag}", "start_date": d(-10), "end_date": d(80), "planned_budget": 50000, "budget_hours": 400}, M)[1]["project"]
emp2, E2 = person("Fcalm", "employee", mgr["id"])
call(proj, "POST", f"/api/projects/{g['id']}/members", {"user_id": emp2["id"], "hours_per_week": 8, "assigned_from": d(-10)}, M)
gm = call(mil, "POST", "/api/milestones", {"project_id": g["id"], "name": "Only", "start_date": d(-10), "end_date": d(80)}, M)[1]["milestone"]
call(mil, "POST", f"/api/milestones/{gm['id']}/start", None, M)
gh = call(proj, "GET", f"/api/projects/{g['id']}", None, M)[1]["project"]
assert gh["health"] == "green" and gh["health_reasons"] == [], gh["health_reasons"]; print("PASS a project on plan is green")
# Overloading a team member turns resources red; an overdue milestone turns milestones yellow.
other = call(proj, "POST", "/api/projects", {"name": f"Overload P {tag}", "start_date": d(0), "end_date": d(60)}, M)[1]["project"]
proj.execute("INSERT INTO project_members (project_id, user_id, hours_per_week, assigned_from) VALUES (%s, %s, 40, %s)", (other["id"], emp2["id"], d(0)))
gh = call(proj, "GET", f"/api/projects/{g['id']}", None, M)[1]["project"]
res = [r for r in gh["health_reasons"] if r["area"] == "resources"]
assert gh["health"] == "red" and res and res[0]["level"] == "red" and "48 h/week" in res[0]["text"]; print("PASS a team member over 40 h turns the project red:", res[0]["text"])
proj.execute("DELETE FROM project_members WHERE project_id = %s", (other["id"],))
proj.execute("UPDATE milestones SET end_date = %s WHERE id = %s", (d(-1), gm["id"]))
gh = call(proj, "GET", f"/api/projects/{g['id']}", None, M)[1]["project"]
ms = [r for r in gh["health_reasons"] if r["area"] == "milestones"]
assert ms and "overdue" in ms[0]["text"] and gh["health"] != "green"; print("PASS an overdue milestone is reported:", ms[0]["text"])
reds = call(proj, "GET", "/api/projects", None, M, {"health": "red"})[1]["projects"]
assert all(x["health"] == "red" for x in reds); print("PASS list filters by health colour")
print("ALL FORECAST TESTS PASSED")
