"""
Fill a database with realistic demo data for presentations, through the real
service handlers (so every business rule is applied).

Usage (from the repo root):
    python3 backend/_tests/seed_demo.py                 # seeds the main local database
    POSTGRES_NAME=test_db python3 backend/_tests/seed_demo.py

Creates managers, employees, a second admin (approvals need two people),
projects with milestones, tasks, team assignments, logged time, other costs,
and one budget increase waiting for approval. Every demo account gets the password
Demo123! and the script prints the generated usernames.
Safe to run once per database; it stops if the demo data already exists.
"""
import os, sys, json, importlib.util
from datetime import date, timedelta

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for k, v in {"POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "postgres",
             "POSTGRES_PASS": "postgres123", "POSTGRES_NAME": "postgres"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(BACKEND, "auth"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BACKEND, name, "function.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auth, users, projects, milestones, budgets = (load(n) for n in ("auth", "users", "projects", "milestones", "budgets"))
DEMO_PASSWORD = "Demo123!"


def call(mod, method, path, body=None, token=None, qs=None):
    ev = {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": {},
          "queryStringParameters": qs}
    if token:
        ev["headers"]["Authorization"] = "Bearer " + token
    if body is not None:
        ev["body"] = json.dumps(body)
    res = mod.handler(ev)
    data = json.loads(res["body"])
    if res["statusCode"] >= 400:
        raise SystemExit(f"{method} {path} failed ({res['statusCode']}): {data.get('error')}")
    return data


admin = call(auth, "POST", "/api/auth/login", {"username": "admin", "password": os.getenv("ADMIN_PASSWORD", "Admin123!")})["token"]
existing = call(users, "GET", "/api/users", token=admin, qs={"q": "demo.acme"})["users"]
if existing:
    raise SystemExit("Demo data already exists in this database. Nothing to do.")

accounts = []


def person(name, role, title, pay, manager=None):
    email = name.lower().replace(" ", ".") + "@demo.acme.com"
    d = call(users, "POST", "/api/users", {"name": name, "email": email, "role": role, "job_title": title,
                                           "pay_rate": pay, "manager_id": manager and manager["id"]}, admin)
    tok = call(auth, "POST", "/api/auth/login", {"username": d["user"]["username"], "password": d["temporary_password"]})["token"]
    call(auth, "POST", "/api/auth/change-password", {"current_password": d["temporary_password"], "new_password": DEMO_PASSWORD}, tok)
    accounts.append((name, role, d["user"]["username"]))
    return {**d["user"], "token": tok}


today = date.today()
iso = lambda d: d.isoformat()
day = lambda n: today + timedelta(days=n)

maria = person("Maria Alvarez", "manager", "Engineering Manager", 85)
dev = person("Devon Price", "manager", "Operations Manager", 80)
ann = person("Anika Shah", "employee", "Frontend Engineer", 55, maria)
ben = person("Ben Okafor", "employee", "Backend Engineer", 58, maria)
cara = person("Cara Lindqvist", "employee", "QA Analyst", 45, maria)
eli = person("Eli Nakamura", "employee", "Data Analyst", 50, dev)
fay = person("Fatima Rahman", "employee", "Business Analyst", 48, dev)
gus = person("Gus Moreau", "employee", "Cloud Engineer", 62, dev)


def project(owner, name, desc, start, end, hours, budget):
    return call(projects, "POST", "/api/projects", {"name": name, "description": desc, "start_date": iso(start),
                                                    "end_date": iso(end), "budget_hours": hours,
                                                    "planned_budget": budget}, owner["token"])["project"]


def assign(owner, proj, who, hours, start):
    call(projects, "POST", f"/api/projects/{proj['id']}/members",
         {"user_id": who["id"], "hours_per_week": hours, "assigned_from": iso(start)}, owner["token"])


def milestone(owner, proj, name, desc, start, end):
    return call(milestones, "POST", "/api/milestones", {"project_id": proj["id"], "name": name, "description": desc,
                                                       "start_date": iso(start), "end_date": iso(end)}, owner["token"])["milestone"]


def ms_action(owner, ms, action, body=None):
    return call(milestones, "POST", f"/api/milestones/{ms['id']}/{action}", body or {}, owner["token"])["milestone"]


def task(owner, ms, title, who=None):
    d = call(milestones, "POST", f"/api/milestones/{ms['id']}/tasks", {"title": title, "assigned_to": who and who["id"]}, owner["token"])
    return next(t for t in d["milestone"]["tasks"] if t["title"] == title)


def check(who, t):
    call(milestones, "PATCH", f"/api/milestones/tasks/{t['id']}", {"completed": True}, who["token"])


def log(who, proj, ms, days_ago, hours, note):
    d = day(-days_ago)
    if d.weekday() >= 5:
        return
    call(budgets, "POST", "/api/budgets/time", {"project_id": proj["id"], "milestone_id": ms and ms["id"],
                                                "work_date": iso(d), "hours": hours, "note": note}, who["token"])


# 1. Customer portal: healthy, second milestone in progress
portal = project(maria, "Customer Portal Rebuild", "Replace the legacy account portal with a responsive React app.",
                 day(-56), day(70), 900, 60000)
for p_, h in ((ann, 24), (ben, 24), (cara, 16)):
    assign(maria, portal, p_, h, day(-56))
p1 = milestone(maria, portal, "Discovery and design", "Interviews, wireframes, and an approved design system.", day(-56), day(-29))
p2 = milestone(maria, portal, "Core build", "Sign-in, account overview, and billing pages working end to end.", day(-28), day(21))
p3 = milestone(maria, portal, "Testing and launch", "Accessibility review, load tests, and production cutover.", day(22), day(70))
for p_ in (ann, ben):
    call(milestones, "POST", f"/api/milestones/{p1['id']}/members", {"user_id": p_["id"]}, maria["token"])
ms_action(maria, p1, "start")
for t in (task(maria, p1, "Run customer interviews", ann), task(maria, p1, "Wireframe account pages", ann), task(maria, p1, "Document API gaps", ben)):
    check(ann if "API" not in t["title"] else ben, t)
for n in range(29, 57):
    log(ann, portal, p1, n, 4, "Design work")
    log(ben, portal, p1, n, 3, "API discovery")
ms_action(maria, p1, "complete")
for p_ in (ann, ben, cara):
    call(milestones, "POST", f"/api/milestones/{p2['id']}/members", {"user_id": p_["id"]}, maria["token"])
ms_action(maria, p2, "start")
t_signin = task(maria, p2, "Build sign-in flow", ann)
t_overview = task(maria, p2, "Account overview page", ann)
t_billing = task(maria, p2, "Billing API endpoints", ben)
task(maria, p2, "Write regression test plan", cara)
task(maria, p2, "Update style guide")
check(ann, t_signin); check(ben, t_billing)
for n in range(1, 29):
    log(ann, portal, p2, n, 5, "Frontend build")
    log(ben, portal, p2, n, 5, "Backend build")
    log(cara, portal, p2, n, 2, "Test planning")
call(budgets, "POST", "/api/budgets/costs", {"project_id": portal["id"], "category": "software", "amount": 2400, "description": "Design tool licenses"}, maria["token"])
call(budgets, "POST", "/api/budgets/costs", {"project_id": portal["id"], "category": "infrastructure", "amount": 1800, "description": "Staging environment"}, maria["token"])

# 2. Data warehouse: behind schedule and spending fast
dw = project(dev, "Data Warehouse Migration", "Move reporting from on-premise SQL Server to a cloud warehouse.",
             day(-90), day(10), 1200, 70000)
for p_, h in ((eli, 30), (gus, 32), (fay, 10)):
    assign(dev, dw, p_, h, day(-90))
d1 = milestone(dev, dw, "Source inventory", "Catalogue every report, table, and owner.", day(-90), day(-60))
d2 = milestone(dev, dw, "Pipeline build", "Nightly loads for finance and sales data.", day(-59), day(-5))
milestone(dev, dw, "Cutover", "Switch dashboards to the new warehouse and retire the old server.", day(-4), day(10))
call(milestones, "POST", f"/api/milestones/{d1['id']}/members", {"user_id": eli["id"]}, dev["token"])
ms_action(dev, d1, "start"); ms_action(dev, d1, "complete")
for p_ in (eli, gus):
    call(milestones, "POST", f"/api/milestones/{d2['id']}/members", {"user_id": p_["id"]}, dev["token"])
ms_action(dev, d2, "start")
check(gus, task(dev, d2, "Finance nightly load", gus))
task(dev, d2, "Sales nightly load", gus)
task(dev, d2, "Reconcile row counts", eli)
for n in range(1, 85):
    log(eli, dw, d2 if n < 60 else d1, n, 6, "Pipeline work")
    log(gus, dw, d2 if n < 60 else None, n, 6, "Infrastructure")
call(budgets, "POST", "/api/budgets/costs", {"project_id": dw["id"], "category": "infrastructure", "amount": 9500, "description": "Warehouse compute"}, dev["token"])
call(budgets, "POST", "/api/budgets/costs", {"project_id": dw["id"], "category": "contractor", "amount": 6000, "description": "Migration consultant"}, dev["token"])

# 3. Onboarding process: just starting
onb = project(dev, "Employee Onboarding Revamp", "Shorten new-hire setup from two weeks to three days.", day(-7), day(120), 300, 18000)
assign(dev, onb, fay, 20, day(-7))
assign(dev, onb, eli, 8, day(-7))
milestone(dev, onb, "Current process map", "Interview HR and IT, and map every handoff.", day(-7), day(20))
milestone(dev, onb, "Pilot", "Run the new checklist with five hires.", day(21), day(80))

# 4. A finished project in the archive
done = project(maria, "Office Wi-Fi Upgrade", "Replace access points on floors 2 to 4.", day(-120), day(-30), 160, 12000)
assign(maria, done, cara, 4, day(-120))
w1 = milestone(maria, done, "Install and verify", "Survey, install, and sign off coverage.", day(-120), day(-30))
ms_action(maria, w1, "start"); ms_action(maria, w1, "complete")
call(projects, "POST", f"/api/projects/{done['id']}/complete", None, maria["token"])

# 5. Maker-checker: a second admin to approve changes, and one budget increase waiting for review
priya = person("Priya Raman", "admin", "Compliance Officer", 90)
call(projects, "PATCH", f"/api/projects/{dw['id']}", {"planned_budget": 85000, "reason": "Consultant extended six weeks to finish the sales pipeline"}, dev["token"])

print("Demo data created. Every account below uses the password", DEMO_PASSWORD)
print(f"{'Name':<18} {'Role':<9} Username")
for name, role, username in accounts:
    print(f"{name:<18} {role:<9} {username}")
print("Admin: username admin, password Admin123! (unless you changed it)")
