"""
End-to-end tests for the backend services, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    python3 backend/_tests/test_users.py

WARNING: creates test users and projects in the target database.
Point it at a scratch database with the POSTGRES_* variables, for example:
    POSTGRES_NAME=test_db python3 backend/_tests/test_users.py
"""
import os, sys, json, importlib.util
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
import uuid
TAG = uuid.uuid4().hex[:8]  # unique emails so the suite can run repeatedly
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
auth = load("auth_fn", os.path.join(BACKEND, "auth", "function.py"))
users = load("users_fn", os.path.join(BACKEND, "users", "function.py"))
def call(mod, method, path, body=None, token=None, qs=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {}, "queryStringParameters": qs}
    if token: ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None: ev["body"] = json.dumps(body)
    r = mod.handler(ev); return r["statusCode"], json.loads(r["body"])
def check(label, got, want):
    ok = got[0] == want; print(("PASS" if ok else "FAIL"), label, got[0], "" if ok else got[1]); assert ok
s, b = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": "Admin123!"}); assert s == 200
A = b["token"]; admin_id = b["user"]["id"]
check("no token", call(users, "GET", "/api/users"), 401)
s, m = call(users, "POST", "/api/users", {"name": "Maria Manager", "email": f"maria@{TAG}.test", "role": "manager", "job_title": "PM", "pay_rate": 60}, A); check("create manager", (s, m), 201)
s, e1 = call(users, "POST", "/api/users", {"name": "Eli One", "email": f"eli@{TAG}.test", "role": "employee", "manager_id": m["user"]["id"], "pay_rate": 40}, A); check("create emp1", (s, e1), 201)
s, e2 = call(users, "POST", "/api/users", {"name": "Eve Two", "email": f"eve@{TAG}.test", "role": "employee", "manager_id": m["user"]["id"], "pay_rate": 42}, A); check("create emp2", (s, e2), 201)
s, o = call(users, "POST", "/api/users", {"name": "Other Mgr", "email": f"om@{TAG}.test", "role": "manager"}, A); check("create mgr2", (s, o), 201)
s, x = call(users, "POST", "/api/users", {"name": "Xavier", "email": f"x@{TAG}.test", "role": "employee", "manager_id": o["user"]["id"]}, A); check("create other-team emp", (s, x), 201)
check("dup email", call(users, "POST", "/api/users", {"name": "Dup", "email": f"ELI@{TAG}.test", "role": "employee"}, A), 409)
check("employee as manager rejected", call(users, "POST", "/api/users", {"name": "Bad", "email": f"bad@{TAG}.test", "role": "employee", "manager_id": e1["user"]["id"]}, A), 400)
print("  username sample:", e1["user"]["username"], "emp#", e1["user"]["employee_number"])
def first_login(username, temp_password, ip=None):
    """Sign in with a temporary password and set a real one, as every new user must."""
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password}, **({"ip": ip} if ip else {}))[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
temp_tok = call(auth, "POST", "/api/auth/login", {"username": e1["user"]["username"], "password": e1["temporary_password"]})[1]
assert temp_tok["user"]["must_change_password"] is True; print("PASS new user must change temporary password")
check("temporary password blocks other pages", call(users, "GET", "/api/users", None, temp_tok["token"]), 403)
check("temporary password still allows /me", call(auth, "GET", "/api/auth/me", None, temp_tok["token"]), 200)
check("new password must differ", call(auth, "POST", "/api/auth/change-password", {"current_password": e1["temporary_password"], "new_password": e1["temporary_password"]}, temp_tok["token"]), 400)
lg = lambda u, p: first_login(u, p)
M = lg(m["user"]["username"], m["temporary_password"]); E = lg(e1["user"]["username"], e1["temporary_password"])
s, b = call(users, "GET", "/api/users", None, E); names = sorted(u["name"] for u in b["users"])
print("  employee sees:", names, "| pay visible on teammate:", any("pay_rate" in u for u in b["users"] if u["name"] == "Eve Two")); assert names == ["Eli One", "Eve Two", "Maria Manager"]
s, b = call(users, "GET", "/api/users", None, M); print("  manager sees:", sorted(u["name"] for u in b["users"]))
check("employee cannot view other team", call(users, "GET", f"/api/users/{x['user']['id']}", None, E), 404)
check("employee edits own email", call(users, "PATCH", f"/api/users/{e1['user']['id']}", {"email": f"eli.one@{TAG}.test"}, E), 200)
check("employee cannot edit teammate", call(users, "PATCH", f"/api/users/{e2['user']['id']}", {"name": "Hacked"}, E), 403)
check("employee cannot change own title", call(users, "PATCH", f"/api/users/{e1['user']['id']}", {"job_title": "CEO"}, E), 403)
check("manager edits report name", call(users, "PATCH", f"/api/users/{e2['user']['id']}", {"name": "Eve Second"}, M), 200)
check("manager cannot reassign manager", call(users, "PATCH", f"/api/users/{e2['user']['id']}", {"manager_id": o["user"]["id"]}, M), 403)
check("manager cannot edit other team", call(users, "PATCH", f"/api/users/{x['user']['id']}", {"name": "Nope"}, M), 404)
check("admin reassigns manager", call(users, "PATCH", f"/api/users/{e2['user']['id']}", {"manager_id": o["user"]["id"]}, A), 200)
check("reporting loop blocked", call(users, "PATCH", f"/api/users/{m['user']['id']}", {"manager_id": m["user"]["id"]}, A), 400)
check("cannot archive manager w/ reports", call(users, "POST", f"/api/users/{o['user']['id']}/archive", None, A), 409)
check("archive employee", call(users, "POST", f"/api/users/{x['user']['id']}/archive", None, A), 200)
s, b = call(auth, "POST", "/api/auth/login", {"username": x["user"]["username"], "password": x["temporary_password"]}); check("archived cannot log in", (s, b), 403)
check("search", call(users, "GET", "/api/users", None, A, {"q": "eve"}), 200)
check("unarchive", call(users, "POST", f"/api/users/{x['user']['id']}/unarchive", None, A), 200)
rp_res = call(users, "POST", f"/api/users/{e1['user']['id']}/reset-password", None, A); check("reset password", rp_res, 200); rp = rp_res[1]
t2 = call(auth, "POST", "/api/auth/login", {"username": e1["user"]["username"], "password": rp["temporary_password"]})[1]
assert t2["user"]["must_change_password"] is True; print("PASS reset password forces a new change")
check("employee cannot create", call(users, "POST", "/api/users", {"name": "Z", "email": f"z@{TAG}.test", "role": "employee"}, E), 403)
check("admin cannot demote self", call(users, "PATCH", f"/api/users/{admin_id}", {"role": "employee"}, A), 400)
check("bad id", call(users, "GET", "/api/users/notauuid", None, A), 400)
print("ALL USERS TESTS PASSED")
check("mgr2 reports to maria", call(users, "PATCH", f"/api/users/{o['user']['id']}", {"manager_id": m["user"]["id"]}, A), 200)
check("true loop blocked", call(users, "PATCH", f"/api/users/{m['user']['id']}", {"manager_id": o["user"]["id"]}, A), 400)
print("LOOP TEST PASSED")
