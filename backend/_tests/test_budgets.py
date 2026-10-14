"""
End-to-end tests for the budgets service, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    POSTGRES_NAME=test_db python3 backend/_tests/test_budgets.py

WARNING: creates test users, projects and time entries. Use a scratch database (test_db).
"""
import os, sys, json, uuid, importlib.util
from datetime import date, timedelta
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
auth = load("a", os.path.join(BACKEND, "auth", "function.py")); users = load("u", os.path.join(BACKEND, "users", "function.py"))
proj = load("p", os.path.join(BACKEND, "projects", "function.py")); mil = load("m", os.path.join(BACKEND, "milestones", "function.py"))
bud = load("b", os.path.join(BACKEND, "budgets", "function.py"))
def call(mod, method, path, body=None, token=None, qs=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {}, "queryStringParameters": qs}
    if token: ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None: ev["body"] = json.dumps(body)
    r = mod.handler(ev); return r["statusCode"], json.loads(r["body"])
def check(label, got, want):
    ok = got[0] == want; print(("PASS" if ok else "FAIL"), label, got[0], "" if ok else got[1]); assert ok; return got[1]
tag = uuid.uuid4().hex[:6]
today = date.today(); last_mon = today - timedelta(days=today.weekday() + 7)
d = lambda n: (last_mon + timedelta(days=n)).isoformat()
A = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": "Admin123!"})[1]["token"]
mk = lambda n, r, mgr=None, pay=50: call(users, "POST", "/api/users", {"name": n, "email": f"{n.lower()}.{tag}@t.com", "role": r, "manager_id": mgr, "pay_rate": pay}, A)[1]
lg = lambda u: call(auth, "POST", "/api/auth/login", {"username": u["user"]["username"], "password": u["temporary_password"]})[1]["token"]
m = mk("Mgr", "manager", pay=80); e1 = mk("Ann", "employee", m["user"]["id"], 40); e2 = mk("Ben", "employee", m["user"]["id"], 30)
M, E1, E2 = lg(m), lg(e1), lg(e2)
start = (today - timedelta(days=60)).isoformat(); end = (today + timedelta(days=60)).isoformat()
p = call(proj, "POST", "/api/projects", {"name": f"Budget {tag}", "start_date": start, "end_date": end, "budget_hours": 100, "planned_budget": 3000}, M)[1]["project"]
q = call(proj, "POST", "/api/projects", {"name": f"Side {tag}", "start_date": start, "end_date": end}, M)[1]["project"]
call(proj, "POST", f"/api/projects/{p['id']}/members", {"user_id": e1["user"]["id"], "hours_per_week": 20, "assigned_from": start}, M)
call(proj, "POST", f"/api/projects/{q['id']}/members", {"user_id": e1["user"]["id"], "hours_per_week": 20, "assigned_from": start}, M)
ms = call(mil, "POST", "/api/milestones", {"project_id": p["id"], "name": "Phase 1", "start_date": start, "end_date": end}, M)[1]["milestone"]
T = "/api/budgets/time"
check("not assigned (Ben) cannot log", call(bud, "POST", T, {"project_id": p["id"], "hours": 2, "work_date": d(0)}, E2), 404)
check("future date rejected", call(bud, "POST", T, {"project_id": p["id"], "hours": 2, "work_date": (today + timedelta(days=1)).isoformat()}, E1), 400)
check("more than 8h in one entry rejected", call(bud, "POST", T, {"project_id": p["id"], "hours": 9, "work_date": d(0)}, E1), 400)
check("pending milestone rejected", call(bud, "POST", T, {"project_id": p["id"], "hours": 2, "work_date": d(0), "milestone_id": ms["id"]}, E1), 409)
call(mil, "POST", f"/api/milestones/{ms['id']}/start", None, M)
check("log 5h Mon on project (milestone)", call(bud, "POST", T, {"project_id": p["id"], "hours": 5, "work_date": d(0), "milestone_id": ms["id"], "note": "wireframes"}, E1), 201)
r = check("+4h Mon on side project exceeds 8h/day", call(bud, "POST", T, {"project_id": q["id"], "hours": 4, "work_date": d(0)}, E1), 409); print("   ", r["error"])
check("+3h Mon on side project ok", call(bud, "POST", T, {"project_id": q["id"], "hours": 3, "work_date": d(0)}, E1), 201)
for i in range(1, 5):
    call(bud, "POST", T, {"project_id": p["id"], "hours": 8, "work_date": d(i)}, E1)
r = check("Saturday after 40h week rejected", call(bud, "POST", T, {"project_id": p["id"], "hours": 1, "work_date": d(5)}, E1), 409); print("   ", r["error"])
s, b = call(bud, "GET", T, None, E1); check("employee lists own time", (s, b), 200); print("    Ann total logged:", b["total_hours"]); assert b["total_hours"] == 40
check("employee cannot see costs", call(bud, "GET", "/api/budgets/costs", None, E1, {"project_id": p["id"]}), 403)
check("employee cannot see summary", call(bud, "GET", "/api/budgets/summary", None, E1, {"project_id": p["id"]}), 403)
check("bad cost category", call(bud, "POST", "/api/budgets/costs", {"project_id": p["id"], "category": "snacks", "amount": 10}, M), 400)
c = check("manager adds infrastructure cost", call(bud, "POST", "/api/budgets/costs", {"project_id": p["id"], "category": "infrastructure", "amount": 500, "description": "AWS"}, M), 201)["cost"]
check("manager adds software cost", call(bud, "POST", "/api/budgets/costs", {"project_id": p["id"], "category": "software", "amount": 250}, M), 201)
sm = check("manager summary", call(bud, "GET", "/api/budgets/summary", None, M, {"project_id": p["id"]}), 200)
t = sm["totals"]; print(f"    hours={t['hours_logged']} labor=${t['labor_cost']} other=${t['other_cost']} actual=${t['actual_cost']} of ${t['planned_budget']} -> {t['budget_used_pct']}% status={t['status']}")
assert t["labor_cost"] == 37 * 40 and t["other_cost"] == 750 and t["status"] == "over_budget" or t["actual_cost"] == 2230
print("    by milestone:", [(x["name"], float(x["hours"])) for x in sm["by_milestone"]], "| weeks:", len(sm["by_week"]))
wk = check("my week", call(bud, "GET", T + "/week", None, E1, {"week_of": d(2)}), 200); print("    week total:", wk["total_hours"], "remaining:", wk["remaining_hours"])
pf = check("manager portfolio", call(bud, "GET", "/api/budgets/portfolio", None, M), 200); print("    portfolio:", [(x["name"][:6], x["actual_cost"], x["status"]) for x in pf["projects"]])
check("employee portfolio forbidden", call(bud, "GET", "/api/budgets/portfolio", None, E1), 403)
s, b = call(bud, "GET", T, None, E1); eid = b["entries"][0]["id"]
check("Ben cannot delete Ann's entry", call(bud, "DELETE", f"{T}/{eid}", None, E2), 403)
check("Ann deletes own entry", call(bud, "DELETE", f"{T}/{eid}", None, E1), 200)
check("delete cost", call(bud, "DELETE", f"/api/budgets/costs/{c['id']}", None, M), 200)
s, b = call(proj, "GET", f"/api/projects/{p['id']}", None, M); print("    project card: hours_logged", b["project"]["hours_logged"], "total_cost", b["project"]["total_cost"], "at_risk", b["project"]["at_risk"], b["project"]["risk_reasons"])
print("ALL BUDGET TESTS PASSED")
