#!/usr/bin/env python3
"""
Fill a deployed ACME Tracker with realistic demo data, through its public API.

Every record goes through the real endpoints, so every business rule, permission check and
audit entry applies exactly as if someone had clicked through the app. Uses only the Python
standard library, so it runs anywhere Python 3 is installed.

Usage (from the repo root):
    python3 bin/seed-cloud.py https://<your-cloudfront-domain>
    python3 bin/seed-cloud.py http://localhost:3001          # local dev through the proxy

It asks for:
    - an admin username and password (used to create the demo people)
    - a password to give every demo account (they are created with temporary passwords,
      which this script replaces, exactly as a new user must on first sign-in)

Creates 2 managers, 6 employees and a second admin, 4 projects in different states
(healthy, behind and over budget, just started, completed and archived), milestones,
tasks, team assignments, about 10 weeks of logged time, other costs, and one budget
increase waiting for approval. Safe to run once; it stops if the demo people already exist.
"""
import getpass
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

if len(sys.argv) != 2 or not sys.argv[1].startswith("http"):
    raise SystemExit(__doc__)
BASE = sys.argv[1].rstrip("/")
DOMAIN = "demo.acme.com"


class Failed(Exception):
    pass


def call(method, path, body=None, token=None, qs=None, quiet=False):
    """One API request. Retries while the database wakes up (503/504), like the frontend does."""
    url = f"{BASE}/api{path}"
    if qs:
        url += "?" + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in qs.items())
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=40) as res:
                text = res.read().decode()
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            if e.code in (502, 503, 504) and attempt < 5:
                time.sleep(3 + attempt * 2)
                continue
            try:
                msg = json.loads(text).get("error") or text
            except ValueError:
                msg = text
            if quiet:
                raise Failed(msg)
            raise SystemExit(f"\n{method} {path} failed ({e.code}): {msg}")
        except urllib.error.URLError as e:
            if attempt < 5:
                time.sleep(3 + attempt * 2)
                continue
            raise SystemExit(f"\nCould not reach {BASE}: {e.reason}")


def step(msg):
    print(f"  {msg}", flush=True)


# ---------- Sign in ----------

print(f"Seeding demo data into {BASE}")
admin_user = input("Admin username [admin]: ").strip() or "admin"
admin_pass = getpass.getpass("Admin password: ")
demo_pass = getpass.getpass("Password to give every demo account (at least 8 characters): ")
if len(demo_pass) < 8:
    raise SystemExit("Pick a password of at least 8 characters.")

print("Waking the database and signing in (this can take up to 20 seconds)...")
admin = call("POST", "/auth/login", {"username": admin_user, "password": admin_pass})["token"]
if call("GET", "/auth/me", token=admin)["user"].get("role") != "admin":
    raise SystemExit("That account is not an admin.")
if call("GET", "/users", token=admin, qs={"q": DOMAIN})["users"]:
    raise SystemExit(f"Demo people (@{DOMAIN}) already exist here. Nothing to do.")

today = date.today()
iso = lambda d: d.isoformat()
day = lambda n: today + timedelta(days=n)
accounts = []

# ---------- People ----------

print("Creating people...")


def person(name, role, title, pay, manager=None):
    email = name.lower().replace(" ", ".") + "@" + DOMAIN
    d = call("POST", "/users", {"name": name, "email": email, "role": role, "job_title": title,
                                "pay_rate": pay, "manager_id": manager and manager["id"]}, admin)
    temp = d["temporary_password"]
    tok = call("POST", "/auth/login", {"username": d["user"]["username"], "password": temp})["token"]
    call("POST", "/auth/change-password", {"current_password": temp, "new_password": demo_pass}, tok)
    tok = call("POST", "/auth/login", {"username": d["user"]["username"], "password": demo_pass})["token"]
    accounts.append((name, role, title, d["user"]["username"], d["user"]["employee_number"]))
    step(f"{name} ({role})")
    return {**d["user"], "token": tok}


maria = person("Maria Alvarez", "manager", "Engineering Manager", 85)
devon = person("Devon Price", "manager", "Operations Manager", 80)
anika = person("Anika Shah", "employee", "Frontend Engineer", 55, maria)
ben = person("Ben Okafor", "employee", "Backend Engineer", 58, maria)
cara = person("Cara Lindqvist", "employee", "QA Analyst", 45, maria)
eli = person("Eli Nakamura", "employee", "Data Analyst", 50, devon)
fatima = person("Fatima Rahman", "employee", "Business Analyst", 48, devon)
gus = person("Gus Moreau", "employee", "Cloud Engineer", 62, devon)

# ---------- Helpers ----------


def project(owner, name, desc, start, end, hours, budget, team):
    """Create a project with its starting team in one request, like the New project form."""
    body = {"name": name, "description": desc, "start_date": iso(start), "end_date": iso(end),
            "budget_hours": hours, "planned_budget": budget,
            "members": [{"user_id": who["id"], "hours_per_week": h, "assigned_from": iso(start)} for who, h in team]}
    return call("POST", "/projects", body, owner["token"])["project"]


def milestone(owner, proj, name, desc, start, end, people=(), tasks=()):
    """Create a milestone with its people and tasks in one request, like the Add milestone form.
    tasks: (title, person or None) pairs."""
    body = {"project_id": proj["id"], "name": name, "description": desc, "start_date": iso(start), "end_date": iso(end),
            "member_ids": [p["id"] for p in people],
            "tasks": [{"title": t, "assigned_to": who and who["id"]} for t, who in tasks]}
    return call("POST", "/milestones", body, owner["token"])["milestone"]


def act(owner, ms, action, body=None):
    return call("POST", f"/milestones/{ms['id']}/{action}", body or {}, owner["token"])["milestone"]


def check_off(who, ms, title):
    t = next(t for t in ms["tasks"] if t["title"] == title)
    call("PATCH", f"/milestones/tasks/{t['id']}", {"completed": True}, who["token"])


logged = 0


def log(who, proj, ms, days_ago, hours, note):
    """Log time on a weekday in the past. Skips weekends, like a normal work week."""
    global logged
    d = day(-days_ago)
    if d.weekday() >= 5:
        return
    call("POST", "/budgets/time", {"project_id": proj["id"], "milestone_id": ms and ms["id"],
                                   "work_date": iso(d), "hours": hours, "note": note}, who["token"])
    logged += 1
    if logged % 25 == 0:
        step(f"{logged} time entries logged...")


def cost(owner, proj, category, amount, desc):
    call("POST", "/budgets/costs", {"project_id": proj["id"], "category": category, "amount": amount,
                                    "description": desc}, owner["token"])


# ---------- 1. Customer Portal Rebuild: healthy, second milestone in progress ----------

print("Project 1: Customer Portal Rebuild (on track)...")
portal = project(maria, "Customer Portal Rebuild", "Replace the legacy account portal with a responsive React app.",
                 day(-56), day(70), 900, 60000, [(anika, 24), (ben, 24), (cara, 16)])
p1 = milestone(maria, portal, "Discovery and design", "Interviews, wireframes, and an approved design system.",
               day(-56), day(-29), [anika, ben],
               [("Run customer interviews", anika), ("Wireframe account pages", anika), ("Document API gaps", ben)])
p2 = milestone(maria, portal, "Core build", "Sign-in, account overview, and billing pages working end to end.",
               day(-29), day(21))
milestone(maria, portal, "Testing and launch", "Accessibility review, load tests, and production cutover.", day(21), day(70))
p1 = act(maria, p1, "start")
check_off(anika, p1, "Run customer interviews"); check_off(anika, p1, "Wireframe account pages"); check_off(ben, p1, "Document API gaps")
for n in range(29, 57):
    log(anika, portal, p1, n, 4, "Design work")
    log(ben, portal, p1, n, 3, "API discovery")
act(maria, p1, "complete")
for who in (anika, ben, cara):
    call("POST", f"/milestones/{p2['id']}/members", {"user_id": who["id"]}, maria["token"])
for title, who in (("Build sign-in flow", anika), ("Account overview page", anika), ("Billing API endpoints", ben),
                   ("Write regression test plan", cara), ("Update style guide", None)):
    call("POST", f"/milestones/{p2['id']}/tasks", {"title": title, "assigned_to": who and who["id"]}, maria["token"])
p2 = act(maria, p2, "start")
check_off(anika, p2, "Build sign-in flow"); check_off(anika, p2, "Account overview page")
check_off(ben, p2, "Billing API endpoints")
for n in range(1, 29):
    log(anika, portal, p2, n, 5, "Frontend build")
    log(ben, portal, p2, n, 5, "Backend build")
    log(cara, portal, p2, n, 2, "Test planning")
cost(maria, portal, "software", 2400, "Design tool licenses")
cost(maria, portal, "infrastructure", 1800, "Staging environment")

# ---------- 2. Data Warehouse Migration: behind schedule and over budget ----------

print("Project 2: Data Warehouse Migration (at risk)...")
dw = project(devon, "Data Warehouse Migration", "Move reporting from on-premise SQL Server to a cloud warehouse.",
             day(-90), day(10), 1200, 70000, [(eli, 30), (gus, 32), (fatima, 6)])
d1 = milestone(devon, dw, "Source inventory", "Catalogue every report, table, and owner.", day(-90), day(-60), [eli],
               [("List every report and owner", eli)])
d2 = milestone(devon, dw, "Pipeline build", "Nightly loads for finance and sales data.", day(-60), day(-5))
milestone(devon, dw, "Cutover", "Switch dashboards to the new warehouse and retire the old server.", day(-5), day(10))
d1 = act(devon, d1, "start"); check_off(eli, d1, "List every report and owner"); act(devon, d1, "complete")
for who in (eli, gus):
    call("POST", f"/milestones/{d2['id']}/members", {"user_id": who["id"]}, devon["token"])
for title, who in (("Finance nightly load", gus), ("Sales nightly load", gus), ("Reconcile row counts", eli)):
    call("POST", f"/milestones/{d2['id']}/tasks", {"title": title, "assigned_to": who["id"]}, devon["token"])
d2 = act(devon, d2, "start"); check_off(gus, d2, "Finance nightly load")
for n in range(1, 85):
    log(eli, dw, d2 if n <= 60 else None, n, 6, "Pipeline work")
    log(gus, dw, d2 if n <= 60 else None, n, 6, "Infrastructure")
cost(devon, dw, "infrastructure", 9500, "Warehouse compute")
cost(devon, dw, "contractor", 6000, "Migration consultant")

# ---------- 3. Employee Onboarding Revamp: just started ----------

print("Project 3: Employee Onboarding Revamp (just started)...")
onb = project(devon, "Employee Onboarding Revamp", "Shorten new-hire setup from two weeks to three days.",
              day(-7), day(120), 300, 18000, [(fatima, 20), (eli, 8)])
o1 = milestone(devon, onb, "Current process map", "Interview HR and IT, and map every handoff.", day(-7), day(20),
               [fatima], [("Interview HR", fatima), ("Interview IT", fatima), ("Draw the current flow", None)])
milestone(devon, onb, "Pilot", "Run the new checklist with five hires.", day(20), day(80))
o1 = act(devon, o1, "start"); check_off(fatima, o1, "Interview HR")
for n in range(1, 6):
    log(fatima, onb, o1, n, 3, "HR interviews")

# ---------- 4. Office Wi-Fi Upgrade: completed and archived ----------

print("Project 4: Office Wi-Fi Upgrade (completed, archived)...")
wifi = project(maria, "Office Wi-Fi Upgrade", "Replace access points on floors 2 to 4.",
               day(-120), day(-30), 160, 12000, [(cara, 4)])
w1 = milestone(maria, wifi, "Install and verify", "Survey, install, and sign off coverage.", day(-120), day(-30),
               [cara], [("Coverage survey", cara)])
w1 = act(maria, w1, "start"); check_off(cara, w1, "Coverage survey"); act(maria, w1, "complete")
call("POST", f"/projects/{wifi['id']}/complete", None, maria["token"])

# ---------- 5. Maker-checker ----------

print("Approvals: second admin and a pending budget increase...")
priya = person("Priya Raman", "admin", "Compliance Officer", 90)
call("PATCH", f"/projects/{dw['id']}", {"planned_budget": 85000,
     "reason": "Consultant extended six weeks to finish the sales pipeline"}, devon["token"])

# ---------- Done ----------

print(f"\nDone. {logged} time entries logged. Every demo account uses the password you chose.\n")
print(f"{'Name':<17} {'Role':<9} {'Title':<21} {'Username':<12} Employee ID")
for name, role, title, username, number in accounts:
    print(f"{name:<17} {role:<9} {title:<21} {username:<12} {number}")
print("\nTry: Maria (manager) for the portfolio, Anika (employee) for My work and My time,")
print("Priya (second admin) to approve the pending budget increase, then the Audit log.")
