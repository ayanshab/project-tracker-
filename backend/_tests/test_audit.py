"""
End-to-end tests for the audit log, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    POSTGRES_NAME=test_db python3 backend/_tests/test_audit.py

WARNING: creates test users and projects. Use a scratch database (test_db).
"""
import os, sys, json, uuid, importlib.util
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
def load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(BACKEND, n, "function.py")); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
auth, users, proj, mil, bud = (load(n) for n in ("auth", "users", "projects", "milestones", "budgets"))
import db
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
mgr = call(users, "POST", "/api/users", {"name": f"Audit Mgr {tag}", "email": f"m@{tag}.test", "role": "manager", "pay_rate": 70}, A)[1]
emp = call(users, "POST", "/api/users", {"name": f"Audit Emp {tag}", "email": f"e@{tag}.test", "role": "employee", "manager_id": mgr["user"]["id"], "pay_rate": 40}, A)[1]
def first_login(username, temp_password, ip=None):
    """Sign in with a temporary password and set a real one, as every new user must."""
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password}, **({"ip": ip} if ip else {}))[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
proj_mod = proj

# A second admin, because sensitive changes need someone other than the requester to approve them.
_a2 = call(users, "POST", "/api/users", {"name": "Second Admin", "email": f"admin2@{tag}.test", "role": "admin"}, A)[1]
A2 = first_login(_a2["user"]["username"], _a2["temporary_password"])
def approved(res, approver_token):
    """res = (status, body) of a PATCH that created a pending change. Approves it and returns the approval."""
    assert res[0] == 200 and res[1].get("pending_approval"), res
    req = res[1]["pending_approval"]
    svc = users if req["entity_type"] == "user" else proj_mod
    return call(svc, "POST", f"/api/{req['entity_type']}s/approvals/{req['id']}/approve", {}, approver_token)
M = first_login(mgr["user"]["username"], mgr["temporary_password"], ip="203.0.113.7")
E = first_login(emp["user"]["username"], emp["temporary_password"])

check("employee cannot read audit log", call(users, "GET", "/api/users/audit", None, E), 403)
check("manager cannot read audit log", call(users, "GET", "/api/users/audit", None, M), 403)
check("failed login", call(auth, "POST", "/api/auth/login", {"username": emp["user"]["username"], "password": "wrong-password"}), 401)
assert log(A, action="auth.password_change", entity_id=emp["user"]["id"]); print("PASS first password change logged")
e = log(A, q=tag, action="user.create")
assert len(e) == 3 and all(x["actor_name"] == "System Administrator" for x in e); print("PASS user.create logged by admin, details:", e[0]["details"]["role"])
fail = log(A, action="auth.login_failed", entity_id=emp["user"]["id"])[0]
assert "wrong-password" not in json.dumps(fail); print("PASS failed login logged without the password:", fail["details"])
mlogin = log(A, action="auth.login", entity_id=mgr["user"]["id"])[0]
assert mlogin["source_ip"] == "203.0.113.7" and mlogin["user_agent"] == "audit-test"; print("PASS login records IP and browser:", mlogin["source_ip"])

check("pay rate change approved", approved(call(users, "PATCH", f"/api/users/{emp['user']['id']}", {"pay_rate": 45, "reason": "Annual review"}, A), A2), 200)
ch = log(A, action="user.update", entity_id=emp["user"]["id"])[0]["details"]["changes"]
assert ch["pay_rate"] == [40.0, 45.0]; print("PASS update stores before and after:", ch)
_noop = call(users, "PATCH", f"/api/users/{emp['user']['id']}", {"pay_rate": 45}, A); check("no-op update", _noop, 200); assert _noop[1]["pending_approval"] is None
assert len(log(A, action="user.update", entity_id=emp["user"]["id"])) == 1; print("PASS unchanged save adds no entry")

p = call(proj, "POST", "/api/projects", {"name": f"Audit P {tag}", "start_date": "2026-01-01", "end_date": "2026-12-31", "planned_budget": 1000}, M)[1]["project"]
call(proj, "POST", f"/api/projects/{p['id']}/members", {"user_id": emp["user"]["id"], "hours_per_week": 10, "assigned_from": "2026-01-01"}, M)
call(proj, "PATCH", f"/api/projects/{p['id']}", {"end_date": "2027-01-31"}, M)
ms = call(mil, "POST", "/api/milestones", {"project_id": p["id"], "name": "Only", "start_date": "2026-01-01", "end_date": "2026-06-30"}, M)[1]["milestone"]
call(mil, "POST", f"/api/milestones/{ms['id']}/members", {"user_id": emp["user"]["id"]}, M)
t = call(mil, "POST", f"/api/milestones/{ms['id']}/tasks", {"title": "Do it"}, M)[1]["milestone"]["tasks"][0]
call(mil, "POST", f"/api/milestones/{ms['id']}/start", None, M)
call(mil, "PATCH", f"/api/milestones/tasks/{t['id']}", {"completed": True}, E)
call(bud, "POST", "/api/budgets/time", {"project_id": p["id"], "hours": 3, "work_date": "2026-03-02"}, E)
call(bud, "POST", "/api/budgets/costs", {"project_id": p["id"], "category": "software", "amount": 99}, M)
call(mil, "POST", f"/api/milestones/{ms['id']}/complete", None, M)
call(proj, "POST", f"/api/projects/{p['id']}/complete", None, M)
call(proj, "POST", f"/api/projects/{p['id']}/unarchive", None, A)
approved(call(users, "PATCH", f"/api/users/{emp['user']['id']}", {"manager_id": None, "reason": "Leaving team"}, A), A2)

trail = [x["action"] for x in reversed(log(A, q=tag, limit=100)) if x["action"] not in ("auth.login",)]
print("    trail:", trail)
for a in ("project.create", "project.member_add", "project.update", "milestone.create", "milestone.member_add",
          "task.create", "milestone.start", "task.complete", "time.log", "cost.add", "milestone.complete",
          "project.complete", "project.unarchive", "user.reassign_manager", "approval.request", "approval.approve"):
    assert a in trail, a
print("PASS every kind of change is in the trail")
tc = log(A, action="task.complete")[0]
assert tc["actor_name"] == f"Audit Emp {tag}"; print("PASS task check-off attributed to the employee")
pu = log(A, action="project.update", entity_id=p["id"])[0]["details"]["changes"]
assert pu["end_date"] == ["2026-12-31", "2027-01-31"]; print("PASS date change recorded:", pu)

try:
    db.execute("UPDATE audit_log SET actor_name = 'someone else' WHERE id = (SELECT max(id) FROM audit_log)")
    raise AssertionError("audit log was editable")
except Exception as ex:
    assert "append-only" in str(ex); print("PASS audit log cannot be edited")
try:
    db.execute("DELETE FROM audit_log WHERE id = (SELECT max(id) FROM audit_log)")
    raise AssertionError("audit log was deletable")
except Exception as ex:
    assert "append-only" in str(ex); print("PASS audit log cannot be deleted")
page = call(users, "GET", "/api/users/audit", None, A, {"limit": 2})[1]
assert len(page["entries"]) == 2 and page["has_more"] and "project.create" in page["actions"]; print("PASS paging and action list")

# ---------- CSV export: every matching row, oldest first, and the export is itself logged ----------
check("employee cannot export", call(users, "GET", "/api/users/audit/export", None, E), 403)
check("manager cannot export", call(users, "GET", "/api/users/audit/export", None, M), 403)
s, ex = call(users, "GET", "/api/users/audit/export", None, A, {"entity_id": p["id"]}); check("admin export", (s, ex), 200)
assert ex["entries"] and ex["truncated"] is False and ex["cap"] == 10000
times = [x["occurred_at"] for x in ex["entries"]]
assert times == sorted(times); print("PASS export is oldest first:", len(ex["entries"]), "rows for one project")
assert {x["action"] for x in ex["entries"]} >= {"project.create", "project.complete"}; print("PASS export carries the project's full history")
logged = log(A, action="audit.export")[0]
assert logged["actor_name"] == "System Administrator" and logged["details"]["rows"] == len(ex["entries"]) and logged["details"]["filters"] == {"entity_id": p["id"]}
print("PASS the export itself is recorded with its filters and row count:", logged["details"])
print("ALL AUDIT TESTS PASSED")
