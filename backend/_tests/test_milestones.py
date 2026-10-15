"""
End-to-end tests for the milestones service, run directly against PostgreSQL.

Usage (from the repo root, with local PostgreSQL running):
    POSTGRES_NAME=test_db python3 backend/_tests/test_milestones.py

WARNING: creates test users and projects. Use a scratch database (test_db).
"""
import os, sys, json, uuid, importlib.util
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
auth = load("a", os.path.join(BACKEND, "auth", "function.py")); users = load("u", os.path.join(BACKEND, "users", "function.py"))
proj = load("p", os.path.join(BACKEND, "projects", "function.py")); mil = load("m", os.path.join(BACKEND, "milestones", "function.py"))
def call(mod, method, path, body=None, token=None, qs=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {}, "queryStringParameters": qs}
    if token: ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None: ev["body"] = json.dumps(body)
    r = mod.handler(ev); return r["statusCode"], json.loads(r["body"])
def check(label, got, want):
    ok = got[0] == want; print(("PASS" if ok else "FAIL"), label, got[0], "" if ok else got[1]); assert ok; return got[1]
tag = uuid.uuid4().hex[:6]
A = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": "Admin123!"})[1]["token"]
mk = lambda n, r, mgr=None: call(users, "POST", "/api/users", {"name": n, "email": f"{n.lower()}.{tag}@t.com", "role": r, "manager_id": mgr, "pay_rate": 50}, A)[1]
def first_login(username, temp_password, ip=None):
    """Sign in with a temporary password and set a real one, as every new user must."""
    token = call(auth, "POST", "/api/auth/login", {"username": username, "password": temp_password}, **({"ip": ip} if ip else {}))[1]["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": temp_password, "new_password": "Passw0rd!"}, token)
    return token
lg = lambda u: first_login(u["user"]["username"], u["temporary_password"])
m = mk("Mgr", "manager"); e1 = mk("Ann", "employee", m["user"]["id"]); e2 = mk("Ben", "employee", m["user"]["id"]); out = mk("Out", "employee", m["user"]["id"])
M, E1, E2, O = lg(m), lg(e1), lg(e2), lg(out)
p = call(proj, "POST", "/api/projects", {"name": f"Plan {tag}", "start_date": "2026-01-01", "end_date": "2026-12-31"}, M)[1]["project"]
for e in (e1, e2):
    call(proj, "POST", f"/api/projects/{p['id']}/members", {"user_id": e["user"]["id"], "hours_per_week": 20, "assigned_from": "2026-01-01"}, M)
P = "/api/milestones"
check("employee cannot create milestone", call(mil, "POST", P, {"project_id": p["id"], "name": "x", "start_date": "2026-01-01", "end_date": "2026-01-31"}, E1), 403)
check("outside project dates", call(mil, "POST", P, {"project_id": p["id"], "name": "x", "start_date": "2025-12-01", "end_date": "2026-01-31"}, M), 400)
m1 = check("create M1", call(mil, "POST", P, {"project_id": p["id"], "name": "Design", "start_date": "2026-01-01", "end_date": "2026-03-31"}, M), 201)["milestone"]
check("M2 overlapping M1 rejected", call(mil, "POST", P, {"project_id": p["id"], "name": "Build", "start_date": "2026-03-15", "end_date": "2026-06-30"}, M), 409)
m2 = check("create M2 after M1", call(mil, "POST", P, {"project_id": p["id"], "name": "Build", "start_date": "2026-04-01", "end_date": "2026-06-30"}, M), 201)["milestone"]
m3 = check("create M3", call(mil, "POST", P, {"project_id": p["id"], "name": "Launch", "start_date": "2026-07-01", "end_date": "2026-08-31"}, M), 201)["milestone"]
assert (m1["position"], m2["position"], m3["position"]) == (1, 2, 3)
check("extend M1 into M2 rejected", call(mil, "PATCH", f"{P}/{m1['id']}", {"end_date": "2026-04-15"}, M), 409)
s, b = call(mil, "GET", P, None, E1, {"project_id": p["id"]}); check("employee lists milestones", (s, b), 200)
print("    can_start:", [x["can_start"] for x in b["milestones"]], "blocked:", [x["blocked_by_previous"] for x in b["milestones"]])
check("outsider cannot list", call(mil, "GET", P, None, O, {"project_id": p["id"]}), 404)
check("cannot start M2 before M1", call(mil, "POST", f"{P}/{m2['id']}/start", None, M), 409)
check("non-project-member cannot join milestone", call(mil, "POST", f"{P}/{m1['id']}/members", {"user_id": out["user"]["id"]}, M), 400)
check("Ann joins M1", call(mil, "POST", f"{P}/{m1['id']}/members", {"user_id": e1["user"]["id"]}, M), 201)
check("Ben joins M1 (many people, one milestone)", call(mil, "POST", f"{P}/{m1['id']}/members", {"user_id": e2["user"]["id"]}, M), 201)
r = check("Ann cannot also join M2", call(mil, "POST", f"{P}/{m2['id']}/members", {"user_id": e1["user"]["id"]}, M), 409); print("   ", r["error"])
check("task assigned to non-member rejected", call(mil, "POST", f"{P}/{m1['id']}/tasks", {"title": "x", "assigned_to": out["user"]["id"]}, M), 400)
d = check("add task for Ann", call(mil, "POST", f"{P}/{m1['id']}/tasks", {"title": "Wireframes", "assigned_to": e1["user"]["id"]}, M), 201)["milestone"]
d = check("add unassigned task", call(mil, "POST", f"{P}/{m1['id']}/tasks", {"title": "Style guide"}, M), 201)["milestone"]
t_ann = next(t for t in d["tasks"] if t["title"] == "Wireframes"); t_open = next(t for t in d["tasks"] if t["title"] == "Style guide")
check("employee cannot rename a task", call(mil, "PATCH", f"{P}/tasks/{t_open['id']}", {"title": "x"}, E1), 403)
r = check("manager renames and reassigns a task", call(mil, "PATCH", f"{P}/tasks/{t_open['id']}", {"title": "Style guide v2", "assigned_to": e2["user"]["id"]}, M), 200)
tt = next(t for t in r["milestone"]["tasks"] if t["id"] == t_open["id"]); assert tt["title"] == "Style guide v2" and tt["assigned_to_name"] == "Ben"; print("PASS task renamed and reassigned")
check("task cannot go to someone off the milestone", call(mil, "PATCH", f"{P}/tasks/{t_open['id']}", {"assigned_to": out["user"]["id"]}, M), 400)
check("task back to anyone on the milestone", call(mil, "PATCH", f"{P}/tasks/{t_open['id']}", {"assigned_to": None, "title": "Style guide"}, M), 200)
check("cannot check off before milestone starts", call(mil, "PATCH", f"{P}/tasks/{t_ann['id']}", {"completed": True}, E1), 409)
check("start M1", call(mil, "POST", f"{P}/{m1['id']}/start", None, M), 200)
mine = check("Ann's open milestones", call(mil, "GET", f"{P}/mine", None, E1), 200)
print("    Ann sees:", [(x["name"], [t["title"] for t in x["tasks"]]) for x in mine["milestones"]])
assert len(mine["milestones"]) == 1 and len(mine["milestones"][0]["tasks"]) == 2
check("Ben cannot check off Ann's task", call(mil, "PATCH", f"{P}/tasks/{t_ann['id']}", {"completed": True}, E2), 403)
check("Ann checks off her task", call(mil, "PATCH", f"{P}/tasks/{t_ann['id']}", {"completed": True}, E1), 200)
check("employee cannot rename task", call(mil, "PATCH", f"{P}/tasks/{t_ann['id']}", {"title": "x"}, E1), 403)
check("employee cannot complete milestone", call(mil, "POST", f"{P}/{m1['id']}/complete", None, E1), 403)
check("complete blocked by open task", call(mil, "POST", f"{P}/{m1['id']}/complete", None, M), 409)
check("Ben checks off unassigned task", call(mil, "PATCH", f"{P}/tasks/{t_open['id']}", {"completed": True}, E2), 200)
d = check("manager completes M1", call(mil, "POST", f"{P}/{m1['id']}/complete", None, M), 200)["milestone"]
print("    M1 progress:", d["progress_pct"], "%")
mine = check("Ann's open milestones (none after M1 completes)", call(mil, "GET", f"{P}/mine", None, E1), 200)
assert mine["milestones"] == []
check("Ann can now join M2", call(mil, "POST", f"{P}/{m2['id']}/members", {"user_id": e1["user"]["id"]}, M), 201)
check("start M2", call(mil, "POST", f"{P}/{m2['id']}/start", None, M), 200)
check("cannot reopen M1 once M2 started", call(mil, "POST", f"{P}/{m1['id']}/reopen", None, M), 409)
check("cannot change completed M1 dates", call(mil, "PATCH", f"{P}/{m1['id']}", {"end_date": "2026-03-30"}, M), 409)
check("add task to M2", call(mil, "POST", f"{P}/{m2['id']}/tasks", {"title": "API"}, M), 201)
check("force-complete M2 with open task", call(mil, "POST", f"{P}/{m2['id']}/complete", {"force": True}, M), 200)
check("cannot delete started milestone", call(mil, "DELETE", f"{P}/{m2['id']}", None, M), 409)
m4 = check("create M4", call(mil, "POST", P, {"project_id": p["id"], "name": "Retro", "start_date": "2026-09-01", "end_date": "2026-09-30"}, M), 201)["milestone"]
m5 = check("M5 starting on M4's last day allowed", call(mil, "POST", P, {"project_id": p["id"], "name": "Wrap", "start_date": "2026-09-30", "end_date": "2026-09-30"}, M), 201)["milestone"]
m6 = check("M6 on the same single day allowed", call(mil, "POST", P, {"project_id": p["id"], "name": "Sign-off", "start_date": "2026-09-30", "end_date": "2026-09-30"}, M), 201)["milestone"]
check("M7 starting before M6 ends rejected", call(mil, "POST", P, {"project_id": p["id"], "name": "x", "start_date": "2026-09-29", "end_date": "2026-10-02"}, M), 409)
check("delete same-day M6", call(mil, "DELETE", f"{P}/{m6['id']}", None, M), 200)
check("delete same-day M5", call(mil, "DELETE", f"{P}/{m5['id']}", None, M), 200)
check("delete pending M3", call(mil, "DELETE", f"{P}/{m3['id']}", None, M), 200)
s, b = call(mil, "GET", P, None, M, {"project_id": p["id"]})
print("    positions after delete:", [(x["position"], x["name"]) for x in b["milestones"]]); assert [x["position"] for x in b["milestones"]] == [1, 2, 3]
check("project cannot complete with open M4", call(proj, "POST", f"/api/projects/{p['id']}/complete", None, M), 409)
check("start M4", call(mil, "POST", f"{P}/{m4['id']}/start", None, M), 200)
check("complete M4", call(mil, "POST", f"{P}/{m4['id']}/complete", None, M), 200)
check("project completes once all milestones done", call(proj, "POST", f"/api/projects/{p['id']}/complete", None, M), 200)
check("completed project milestones locked", call(mil, "POST", P, {"project_id": p["id"], "name": "x", "start_date": "2026-10-01", "end_date": "2026-10-31"}, A), 409)
# People and tasks sent with a new milestone
q = call(proj, "POST", "/api/projects", {"name": f"Kit {tag}", "start_date": "2026-01-01", "end_date": "2026-12-31",
         "members": [{"user_id": e1["user"]["id"], "hours_per_week": 5, "assigned_from": "2026-01-01"}]}, M)[1]["project"]
check("new milestone with non-team person refused", call(mil, "POST", P, {"project_id": q["id"], "name": "K1", "start_date": "2026-01-01", "end_date": "2026-01-31", "member_ids": [out["user"]["id"]]}, M), 400)
check("new milestone task for non-member refused", call(mil, "POST", P, {"project_id": q["id"], "name": "K1", "start_date": "2026-01-01", "end_date": "2026-01-31", "member_ids": [e1["user"]["id"]], "tasks": [{"title": "x", "assigned_to": e2["user"]["id"]}]}, M), 400)
s, b = call(mil, "GET", P, None, M, {"project_id": q["id"]}); assert b["milestones"] == []; print("PASS refused milestone creates leave nothing behind")
k1 = check("new milestone with people and tasks", call(mil, "POST", P, {"project_id": q["id"], "name": "K1", "start_date": "2026-01-01", "end_date": "2026-01-31", "member_ids": [e1["user"]["id"]], "tasks": [{"title": "Kickoff", "assigned_to": e1["user"]["id"]}, {"title": "Shared notes"}, "Plain string task"]}, M), 201)["milestone"]
assert [x["name"] for x in k1["members"]] == ["Ann"] and len(k1["tasks"]) == 3; print("PASS milestone created with 1 person and 3 tasks")
check("person on open K1 cannot be put on new K2", call(mil, "POST", P, {"project_id": q["id"], "name": "K2", "start_date": "2026-02-01", "end_date": "2026-02-28", "member_ids": [e1["user"]["id"]]}, M), 409)
# ---------- Dependencies, critical path and projected finish ----------
# A fresh project with a diamond plan: Design -> (Build API, Build UI in parallel) -> Launch.
pp = call(proj, "POST", "/api/projects", {"name": f"Diamond {tag}", "start_date": "2027-01-05", "end_date": "2027-04-30"}, M)[1]["project"]
mk_ms = lambda name, start, end, **extra: call(mil, "POST", P, {"project_id": pp["id"], "name": name, "start_date": start, "end_date": end, **extra}, M)
design = check("Design (no prerequisites)", mk_ms("Design", "2027-01-05", "2027-01-30"), 201)["milestone"]
api = check("Build API after Design", mk_ms("Build API", "2027-01-30", "2027-03-13"), 201)["milestone"]
assert [d["name"] for d in api["depends_on"]] == ["Design"]; print("PASS a new milestone depends on the previous one by default")
check("Build UI overlapping Build API refused while it depends on it", mk_ms("Build UI", "2027-01-30", "2027-02-27"), 409)
ui = check("Build UI in parallel, depending on Design only", mk_ms("Build UI", "2027-01-30", "2027-02-27", depends_on=[design["id"]]), 201)["milestone"]
check("cannot depend on a later milestone", mk_ms("Bad", "2027-03-13", "2027-03-20", depends_on=[design["id"], "00000000-0000-0000-0000-000000000000"]), 400)
launch = check("Launch after both builds", mk_ms("Launch", "2027-03-13", "2027-04-10", depends_on=[api["id"], ui["id"]]), 201)["milestone"]
check("Launch cannot start before API ends", call(mil, "PATCH", f"{P}/{launch['id']}", {"start_date": "2027-03-01"}, M), 409)
check("API cannot end after Launch starts", call(mil, "PATCH", f"{P}/{api['id']}", {"end_date": "2027-03-20"}, M), 409)
check("Build UI may end later now that nothing follows it tightly", call(mil, "PATCH", f"{P}/{ui['id']}", {"end_date": "2027-03-06"}, M), 200)
r = check("Build UI cannot start: Design not done", call(mil, "POST", f"{P}/{ui['id']}/start", None, M), 409); assert "Design" in r["error"]
lst = call(mil, "GET", P, None, M, {"project_id": pp["id"]})[1]["milestones"]
assert [x["name"] for x in lst if x["can_start"]] == ["Design"] and [d["name"] for d in next(x for x in lst if x["name"] == "Launch")["blocked_by"]] == ["Build API", "Build UI"]
print("PASS list shows what each milestone is waiting on")
sched = check("schedule", call(mil, "GET", f"{P}/schedule", None, M, {"project_id": pp["id"]}), 200)
crit = [x["name"] for x in sched["milestones"] if x["critical"]]
assert crit == ["Design", "Build API", "Launch"], crit; print("PASS critical path is Design > Build API > Launch (UI has slack):", crit)
ui_s = next(x for x in sched["milestones"] if x["name"] == "Build UI"); assert ui_s["slack_days"] == 7 and ui_s["finish_impact_per_day"] == 0
print("PASS Build UI has 7 days of slack (ends Mar 6, Launch starts Mar 13)")
assert sched["earliest_finish"] == "2027-04-10" and sched["delay_days"] == 0 and sched["critical_length_days"] == 96
sim = check("what if Build API slips 10 days", call(mil, "GET", f"{P}/schedule", None, M, {"project_id": pp["id"], "simulate": f"{api['id']}:10"}), 200)
assert sim["projected_finish"] == "2027-04-20" and sim["delay_days"] == 10; print("PASS a 10-day slip on the critical path moves the finish 10 days")
sim = call(mil, "GET", f"{P}/schedule", None, M, {"project_id": pp["id"], "simulate": f"{ui['id']}:5"})[1]
assert sim["delay_days"] == 0; print("PASS a 5-day slip on Build UI is absorbed by its slack")
sim = call(mil, "GET", f"{P}/schedule", None, M, {"project_id": pp["id"], "simulate": f"{ui['id']}:12"})[1]
assert sim["delay_days"] == 5; print("PASS a 12-day slip on Build UI overruns its 7 days of slack by 5")
check("simulate rejects junk", call(mil, "GET", f"{P}/schedule", None, M, {"project_id": pp["id"], "simulate": "nope"}), 400)
# Start, complete and reopen follow the dependency graph, not the list order.
call(mil, "POST", f"{P}/{design['id']}/start", None, M); check("complete Design", call(mil, "POST", f"{P}/{design['id']}/complete", {"force": True}, M), 200)
check("Build UI (#3) can start before Build API (#2): both only need Design", call(mil, "POST", f"{P}/{ui['id']}/start", None, M), 200)
check("Build API can start too, in parallel", call(mil, "POST", f"{P}/{api['id']}/start", None, M), 200)
check("Design cannot reopen: dependents have started", call(mil, "POST", f"{P}/{design['id']}/reopen", None, M), 409)
check("Launch cannot start: builds still open", call(mil, "POST", f"{P}/{launch['id']}/start", None, M), 409)
check("API cannot depend on UI, which comes later in the list", call(mil, "PATCH", f"{P}/{api['id']}", {"depends_on": [design["id"], ui["id"]]}, M), 400)
check("Launch may drop its UI dependency", call(mil, "PATCH", f"{P}/{launch['id']}", {"depends_on": [api["id"]]}, M), 200)
call(mil, "POST", f"{P}/{api['id']}/complete", {"force": True}, M); call(mil, "POST", f"{P}/{launch['id']}/start", None, M)
check("in-progress Launch cannot take on unfinished Build UI as a prerequisite", call(mil, "PATCH", f"{P}/{launch['id']}", {"depends_on": [api["id"], ui["id"]]}, M), 409)
au = call(users, "GET", "/api/users/audit", None, A, {"entity_id": launch["id"], "action": "milestone.update"})[1]["entries"][0]
assert au["details"]["changes"]["depends_on"] == ["Build API, Build UI", "Build API"]; print("PASS dependency change is audited:", au["details"]["changes"]["depends_on"])
tl = check("portfolio timeline", call(mil, "GET", f"{P}/timeline", None, M), 200)
assert any(x["name"].startswith("Diamond") and x["critical_path"] for x in tl["projects"]); print("PASS timeline lists projects with their critical path")
check("employee not on the project cannot read its schedule", call(mil, "GET", f"{P}/schedule", None, O, {"project_id": pp["id"]}), 404)
print("ALL MILESTONE TESTS PASSED")
