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
# ---------- Capacity heatmap and the pre-assignment preview ----------
# Eve: 5 h on Borealis (Sep 1 to Dec 31). Two more projects, back to back, must not count against each other.
q1 = check("Q1 project", call(proj, "POST", P, {"name": "Q1 Work", "start_date": "2027-01-04", "end_date": "2027-03-26", "manager_id": m2["user"]["id"]}, A), 201)["project"]
q2 = check("Q2 project", call(proj, "POST", P, {"name": "Q2 Work", "start_date": "2027-03-29", "end_date": "2027-06-25", "manager_id": m2["user"]["id"]}, A), 201)["project"]
check("Eve 35h on Q1", call(proj, "POST", f"{P}/{q1['id']}/members", {"user_id": e2["user"]["id"], "hours_per_week": 35, "assigned_from": "2027-01-04"}, A), 201)
check("Eve 35h on Q2 too: no overlap, so it fits", call(proj, "POST", f"{P}/{q2['id']}/members", {"user_id": e2["user"]["id"], "hours_per_week": 35, "assigned_from": "2027-03-29"}, A), 201)
q3 = check("year-long project", call(proj, "POST", P, {"name": "Platform", "start_date": "2027-01-01", "end_date": "2027-12-31", "manager_id": m2["user"]["id"]}, A), 201)["project"]
r = check("Eve 10h more across both quarters: busiest week is 35h, refused", call(proj, "POST", f"{P}/{q3['id']}/members", {"user_id": e2["user"]["id"], "hours_per_week": 10, "assigned_from": "2027-02-01", "assigned_to": "2027-05-01"}, A), 409)
assert "35 h/week" in r["error"] and "5 h/week available" in r["error"]; print("PASS refusal names the busiest week's load:", r["error"][:60])
hm = check("heatmap window: 13 weeks from Dec 28", call(proj, "GET", f"{P}/allocation", None, A, {"from": "2026-12-30", "weeks": "13"}), 200)
assert hm["from"] == "2026-12-28" and len(hm["weeks"]) == 13 and hm["near_capacity"] == 32; print("PASS window snaps to Monday and returns 13 week columns")
eve = next(x for x in hm["people"] if x["name"] == "Eve")
loads = [(w["week_start"], w["hours"], w["status"]) for w in eve["weeks"]]
assert loads[0] == ("2026-12-28", 5.0, "ok") and loads[1] == ("2027-01-04", 35.0, "near"); print("PASS Eve: 5h over New Year, then 35h (near capacity) once Q1 starts")
assert eve["weeks"][12]["week_start"] == "2027-03-22" and eve["weeks"][12]["hours"] == 35.0 and len(eve["weeks"][12]["assignments"]) == 1
assert eve["peak_hours"] == 35.0 and eve["weeks_over"] == 0 and eve["weeks_near"] == 12; print("PASS per-week breakdown lists the projects behind each cell")
check("heatmap rejects a silly window", call(proj, "GET", f"{P}/allocation", None, A, {"weeks": "lots"}), 400)
cap = check("capacity preview: Eve +10h over Feb to May", call(proj, "GET", f"{P}/capacity", None, A, {"user_id": e2["user"]["id"], "hours": "10", "from": "2027-02-01", "to": "2027-05-01"}), 200)
assert cap["booked_peak"] == 35.0 and cap["available"] == 5.0 and cap["with_new_peak"] == 45.0 and cap["status"] == "over" and cap["over_by"] == 5.0 and cap["weeks_over"] == 13
assert cap["weeks"][0]["with_new"] == 45.0 and cap["weeks"][0]["status_with_new"] == "over"; print("PASS preview says over by 5h in all 13 weeks, starting", cap["worst_week"])
cap = check("capacity preview: Eve +5h fits exactly", call(proj, "GET", f"{P}/capacity", None, A, {"user_id": e2["user"]["id"], "hours": "5", "from": "2027-02-01", "to": "2027-05-01"}), 200)
assert cap["status"] == "near" and cap["over_by"] == 0; print("PASS 40h exactly is allowed but flagged as approaching capacity")
check("capacity preview: manager cannot check another team", call(proj, "GET", f"{P}/capacity", None, M, {"user_id": e2["user"]["id"], "hours": "5"}), 403)
check("capacity preview: employee forbidden", call(proj, "GET", f"{P}/capacity", None, E, {"user_id": e2["user"]["id"]}), 403)
check("capacity preview needs user_id", call(proj, "GET", f"{P}/capacity", None, A, {}), 400)
print("ALL PROJECT TESTS PASSED")
