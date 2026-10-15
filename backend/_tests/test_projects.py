"""
End-to-end tests for the backend services, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    python3 backend/_tests/test_projects.py

WARNING: creates test users and projects in the target database.
Point it at a scratch database with the POSTGRES_* variables, for example:
    POSTGRES_NAME=test_db python3 backend/_tests/test_projects.py
"""
import os, sys, json, importlib.util
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
import uuid
TAG = uuid.uuid4().hex[:8]  # unique emails so the suite can run repeatedly
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
auth = load("a", os.path.join(BACKEND, "auth", "function.py")); users = load("u", os.path.join(BACKEND, "users", "function.py")); proj = load("p", os.path.join(BACKEND, "projects", "function.py"))
import db
def call(mod, method, path, body=None, token=None, qs=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {}, "queryStringParameters": qs}
    if token: ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None: ev["body"] = json.dumps(body)
    r = mod.handler(ev); return r["statusCode"], json.loads(r["body"])
def check(label, got, want):
    ok = got[0] == want; print(("PASS" if ok else "FAIL"), label, got[0], "" if ok else got[1]); assert ok; return got[1]
A = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": "Admin123!"})[1]["token"]
mk = lambda n, e, r, mgr=None, pay=50: call(users, "POST", "/api/users", {"name": n, "email": e, "role": r, "manager_id": mgr, "pay_rate": pay}, A)[1]
def first_login(username, temp_password, ip=None):
    """Sign in with a temporary password and set a real one, as every new user must."""
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password}, **({"ip": ip} if ip else {}))[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
lg = lambda u: first_login(u["user"]["username"], u["temporary_password"])
m = mk("Maria", f"maria@{TAG}.test", "manager"); m2 = mk("Omar", f"omar@{TAG}.test", "manager")
e1 = mk("Eli", f"eli@{TAG}.test", "employee", m["user"]["id"], 40); e2 = mk("Eve", f"eve@{TAG}.test", "employee", m2["user"]["id"])
M, M2, E = lg(m), lg(m2), lg(e1)
P = "/api/projects"
check("employee cannot create", call(proj, "POST", P, {"name": "x", "start_date": "2026-01-01", "end_date": "2026-12-31"}, E), 403)
check("admin needs manager_id", call(proj, "POST", P, {"name": "x", "start_date": "2026-01-01", "end_date": "2026-12-31"}, A), 400)
check("employee cannot be project manager", call(proj, "POST", P, {"name": "x", "start_date": "2026-01-01", "end_date": "2026-12-31", "manager_id": e1["user"]["id"]}, A), 400)
check("bad dates", call(proj, "POST", P, {"name": "x", "start_date": "2026-12-01", "end_date": "2026-01-01"}, M), 400)
p1 = check("manager creates project", call(proj, "POST", P, {"name": "Apollo", "start_date": "2026-09-01", "end_date": "2026-12-31", "budget_hours": 500, "planned_budget": 20000}, M), 201)["project"]
assert p1["manager_name"] == "Maria"
p2 = check("admin creates project for Omar", call(proj, "POST", P, {"name": "Borealis", "start_date": "2026-09-01", "end_date": "2026-12-31", "manager_id": m2["user"]["id"]}, A), 201)["project"]
check("manager assigns own report 30h", call(proj, "POST", f"{P}/{p1['id']}/members", {"user_id": e1["user"]["id"], "hours_per_week": 30}, M), 201)
check("manager cannot assign other team", call(proj, "POST", f"{P}/{p1['id']}/members", {"user_id": e2["user"]["id"]}, M), 403)
check("duplicate assign", call(proj, "POST", f"{P}/{p1['id']}/members", {"user_id": e1["user"]["id"]}, M), 409)
r = check("admin over-allocates Eli (+20 on Borealis)", call(proj, "POST", f"{P}/{p2['id']}/members", {"user_id": e1["user"]["id"], "hours_per_week": 20}, A), 409); print("   ", r["error"])
check("admin assigns Eli 10h on Borealis", call(proj, "POST", f"{P}/{p2['id']}/members", {"user_id": e1["user"]["id"], "hours_per_week": 10}, A), 201)
check("raising Apollo to 31h blocked", call(proj, "PATCH", f"{P}/{p1['id']}/members/{e1['user']['id']}", {"hours_per_week": 31}, M), 409)
check("lowering Apollo to 25h ok", call(proj, "PATCH", f"{P}/{p1['id']}/members/{e1['user']['id']}", {"hours_per_week": 25}, M), 200)
s, b = call(proj, "GET", P, None, E); print("    employee sees:", [p["name"] for p in b["projects"]]); assert len(b["projects"]) == 2
s, b = call(proj, "GET", f"{P}/{p1['id']}", None, E); assert "planned_budget" not in b["project"]; print("PASS employee detail hides budget")
check("Omar staffs himself on Borealis", call(proj, "POST", f"{P}/{p2['id']}/members", {"user_id": m2["user"]["id"], "hours_per_week": 10}, M2), 201)
s, b = call(proj, "GET", P, None, M2, {"mine": "true"}); assert [p["name"] for p in b["projects"]] == ["Borealis"]; print("PASS manager mine=true lists staffed projects")
s, b = call(proj, "GET", P, None, M, {"mine": "true"}); assert b["projects"] == []; print("PASS manager not staffed sees none with mine=true")
check("Omar cannot see Apollo",call(proj, "GET", f"{P}/{p1['id']}", None, M2), 404)
check("Omar cannot edit Apollo", call(proj, "PATCH", f"{P}/{p1['id']}", {"name": "Hack"}, M2), 404)
check("manager edits own project", call(proj, "PATCH", f"{P}/{p1['id']}", {"name": "Apollo v2", "end_date": "2027-01-31"}, M), 200)
check("manager cannot change project manager", call(proj, "PATCH", f"{P}/{p1['id']}", {"manager_id": m2["user"]["id"]}, M), 403)
check("delete refused", call(proj, "DELETE", f"{P}/{p1['id']}", None, A), 405)
al = check("allocation (admin)", call(proj, "GET", f"{P}/allocation", None, A), 200)
print("    Eli hours/week:", [p["hours_this_week"] for p in al["people"] if p["name"] == "Eli"])
check("allocation forbidden for employee", call(proj, "GET", f"{P}/allocation", None, E), 403)
# Starting team sent with the new project
before = db.query_one("SELECT COUNT(*) AS n FROM projects")["n"]
check("create with another manager's report refused", call(proj, "POST", P, {"name": "Comet", "start_date": "2026-09-01", "end_date": "2026-12-31", "members": [{"user_id": e2["user"]["id"]}]}, M), 403)
check("create with over-allocated person refused", call(proj, "POST", P, {"name": "Comet", "start_date": "2026-09-01", "end_date": "2026-12-31", "members": [{"user_id": e1["user"]["id"], "hours_per_week": 10}]}, M), 409)
check("create with the same person twice refused", call(proj, "POST", P, {"name": "Comet", "start_date": "2026-09-01", "end_date": "2026-12-31", "members": [{"user_id": m["user"]["id"]}, {"user_id": m["user"]["id"]}]}, M), 400)
assert db.query_one("SELECT COUNT(*) AS n FROM projects")["n"] == before; print("PASS refused creates leave no project behind")
c = check("manager creates project with a team", call(proj, "POST", P, {"name": "Comet", "start_date": "2026-09-01", "end_date": "2026-12-31", "members": [{"user_id": e1["user"]["id"], "hours_per_week": 5}, {"user_id": m["user"]["id"], "hours_per_week": 4}]}, M), 201)["project"]
assert c["member_count"] == 2 and sorted(float(x["hours_per_week"]) for x in c["members"]) == [4.0, 5.0]; print("PASS starting team saved with hours")
check("Comet team member removable", call(proj, "DELETE", f"{P}/{c['id']}/members/{e1['user']['id']}", None, M), 200)
# milestone blocks completion
db.execute("INSERT INTO milestones (project_id, position, name, start_date, end_date) VALUES (%s, 1, 'M1', '2026-09-01', '2026-10-01')", (p1["id"],))
check("cannot shrink dates past milestone", call(proj, "PATCH", f"{P}/{p1['id']}", {"start_date": "2026-09-15"}, M), 409)
check("complete blocked by open milestone", call(proj, "POST", f"{P}/{p1['id']}/complete", None, M), 409)
db.execute("UPDATE milestones SET status='completed' WHERE project_id=%s", (p1["id"],))
db.execute("INSERT INTO time_entries (user_id, project_id, hours, work_date) VALUES (%s, %s, 8, '2026-09-02')", (e1["user"]["id"], p1["id"]))
s, b = call(proj, "GET", f"{P}/{p1['id']}", None, M); pr = b["project"]
print(f"    hours_logged={pr['hours_logged']} labor_cost={pr['labor_cost']} progress={pr['progress_pct']}% at_risk={pr['at_risk']} {pr['risk_reasons']}")
assert float(pr["labor_cost"]) == 320.0
check("employee cannot complete", call(proj, "POST", f"{P}/{p1['id']}/complete", None, E), 404 if False else 403)
check("manager completes", call(proj, "POST", f"{P}/{p1['id']}/complete", None, M), 200)
check("manager no longer sees archived", call(proj, "GET", f"{P}/{p1['id']}", None, M), 404)
s, b = call(proj, "GET", P, None, A, {"archived": "true"}); print("    admin archive:", [p["name"] for p in b["projects"]]); assert b["projects"][0]["status"] == "completed"
check("manager cannot unarchive", call(proj, "POST", f"{P}/{p1['id']}/unarchive", None, M), 403)
check("admin unarchive", call(proj, "POST", f"{P}/{p1['id']}/unarchive", None, A), 200)
check("Eli's Apollo hours no longer count after completion (assign 30 more on Borealis)", call(proj, "PATCH", f"{P}/{p2['id']}/members/{e1['user']['id']}", {"hours_per_week": 40}, A), 200)
check("admin adds Eve (Omar's team) to Borealis", call(proj, "POST", f"{P}/{p2['id']}/members", {"user_id": e2["user"]["id"], "hours_per_week": 5}, A), 201)
s, b = call(proj, "GET", f"{P}/{p2['id']}", None, E)
eve = next(x for x in b["project"]["members"] if x["user_id"] == e2["user"]["id"]); me = next(x for x in b["project"]["members"] if x["user_id"] == e1["user"]["id"])
assert eve.get("other_team") and "email" not in eve and "hours_logged" not in eve and "email" in me
print("PASS employee sees other-team colleague by name only:", sorted(eve.keys()))
check("remove member", call(proj, "DELETE", f"{P}/{p2['id']}/members/{e1['user']['id']}", None, A), 200)
check("search", call(proj, "GET", P, None, A, {"q": "borea"}), 200)
check("archiving manager with active project blocked", call(users, "POST", f"/api/users/{m2['user']['id']}/archive", None, A), 409)
print("ALL PROJECT TESTS PASSED")
