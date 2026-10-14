"""
End-to-end tests for maker-checker approvals, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    POSTGRES_NAME=test_db python3 backend/_tests/test_approvals.py

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
def first_login(username, temp_password):
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password})[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
def person(name, role, mgr=None, pay=50):
    d = call(users, "POST", "/api/users", {"name": f"{name} {tag}", "email": f"{name.lower()}@{tag}.test", "role": role, "manager_id": mgr, "pay_rate": pay}, A)[1]
    return d["user"], first_login(d["user"]["username"], d["temporary_password"])

admin2, A2 = person("Checker", "admin")
mgr, M = person("Maker", "manager", pay=80)
emp, E = person("Worker", "employee", mgr["id"])
p = call(proj, "POST", "/api/projects", {"name": f"Approval P {tag}", "start_date": "2026-01-01", "end_date": "2026-12-31", "planned_budget": 50000, "budget_hours": 600}, M)[1]["project"]
P = f"/api/projects/{p['id']}"
check("budget change without reason rejected", call(proj, "PATCH", P, {"name": f"Renamed {tag}", "planned_budget": 80000}, M), 400)
assert call(proj, "GET", P, None, M)[1]["project"]["name"] == p["name"]; print("PASS nothing applied when the request is refused")
r = check("manager edits name and budget", call(proj, "PATCH", P, {"name": f"Renamed {tag}", "planned_budget": 80000, "reason": "Scope added: two new markets"}, M), 200)
assert r["project"]["name"] == f"Renamed {tag}" and r["project"]["planned_budget"] == 50000 and r["pending_approval"]["payload"] == {"planned_budget": 80000.0}
print("PASS name applied now, budget held for approval")
assert len(r["project"]["pending_changes"]) == 1; print("PASS project shows the pending change")
check("second budget request blocked while one is pending", call(proj, "PATCH", P, {"planned_budget": 90000, "reason": "Even more scope"}, M), 409)
req = r["pending_approval"]
mine = call(users, "GET", "/api/users/approvals", None, M)[1]
assert mine["requests"][0]["can_cancel"] and not mine["requests"][0]["can_decide"] and mine["waiting_for_you"] == 0; print("PASS manager sees own request and can cancel it")
inbox = call(users, "GET", "/api/users/approvals", None, A, {"status": "pending"})[1]
ours = next(x for x in inbox["requests"] if x["id"] == req["id"])
assert ours["can_decide"] and ours["changes"][0] == {"field": "planned_budget", "label": "budget", "from": 50000.0, "to": 80000.0}; print("PASS admin inbox shows before and after:", ours["changes"][0])
assert call(users, "GET", "/api/users/approvals", None, E)[1]["requests"] == []; print("PASS employees see no one else's requests")
check("employee cannot approve", call(proj, "POST", f"/api/projects/approvals/{req['id']}/approve", {}, E), 403)
check("manager cannot approve own", call(proj, "POST", f"/api/projects/approvals/{req['id']}/approve", {}, M), 403)
check("project request cannot be approved via users service", call(users, "POST", f"/api/users/approvals/{req['id']}/approve", {}, A), 400)
check("reject needs a note", call(proj, "POST", f"/api/projects/approvals/{req['id']}/reject", {}, A), 400)
check("admin rejects with note", call(proj, "POST", f"/api/projects/approvals/{req['id']}/reject", {"note": "Bring a revised estimate"}, A), 200)
assert call(proj, "GET", P, None, M)[1]["project"]["planned_budget"] == 50000; print("PASS rejected change not applied")
check("cannot decide twice", call(proj, "POST", f"/api/projects/approvals/{req['id']}/approve", {}, A2), 409)
r2 = call(proj, "PATCH", P, {"planned_budget": 65000, "reason": "Revised estimate attached"}, M)[1]["pending_approval"]
check("requester cancels", call(proj, "POST", f"/api/projects/approvals/{r2['id']}/cancel", {}, M), 200)
r3 = call(proj, "PATCH", P, {"planned_budget": 65000, "budget_hours": 700, "reason": "Revised estimate attached"}, M)[1]["pending_approval"]
check("other admin cannot cancel", call(proj, "POST", f"/api/projects/approvals/{r3['id']}/cancel", {}, A), 403)
check("admin approves", call(proj, "POST", f"/api/projects/approvals/{r3['id']}/approve", {"note": "OK"}, A), 200)
after = call(proj, "GET", P, None, M)[1]["project"]
assert after["planned_budget"] == 65000 and after["budget_hours"] == 700 and after["pending_changes"] == []; print("PASS approved budget and hours applied")
au = call(users, "GET", "/api/users/audit", None, A, {"entity_id": p["id"]})[1]["entries"]
upd = next(x for x in au if x["action"] == "project.update" and "approved_request" in x["details"])
assert upd["actor_name"] == "System Administrator" and upd["details"]["requested_by"] == mgr["id"]
assert {"approval.request", "approval.reject", "approval.cancel", "approval.approve"} <= {x["action"] for x in au}; print("PASS full approval trail in the audit log")
# admin's own changes also need someone else
r4 = call(proj, "PATCH", P, {"manager_id": admin2["id"], "reason": "Checker takes over"}, A)[1]["pending_approval"]
check("admin cannot approve own project-manager change", call(proj, "POST", f"/api/projects/approvals/{r4['id']}/approve", {}, A), 403)
check("second admin approves it", call(proj, "POST", f"/api/projects/approvals/{r4['id']}/approve", {}, A2), 200)
assert call(proj, "GET", P, None, A)[1]["project"]["manager_id"] == admin2["id"]; print("PASS project manager changed after approval")
# changes that became invalid are not applied
r5 = call(users, "PATCH", f"/api/users/{emp['id']}", {"role": "manager", "reason": "Promotion"}, A)[1]["pending_approval"]
call(users, "POST", f"/api/users/{emp['id']}/archive", None, A)
check("stale request cannot be applied", call(users, "POST", f"/api/users/approvals/{r5['id']}/approve", {}, A2), 409)
assert call(users, "GET", "/api/users/approvals", None, A2, {"status": "pending"})[1]["requests"][0]["id"] == r5["id"]; print("PASS failed approval leaves the request pending")
check("then it can be rejected", call(users, "POST", f"/api/users/approvals/{r5['id']}/reject", {"note": "Person left"}, A2), 200)
print("ALL APPROVAL TESTS PASSED")
