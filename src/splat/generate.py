"""Deterministic generator for the synthetic company "Kestrel".

Kestrel is a fictional 40-person B2B invoicing company. The generator writes one
JSON file describing its goals, work, decisions, assumptions, metrics, and the
documents, threads and tickets those facts come from, plus a year of ordinary
chatter that has nothing to do with any decision.

Stories are planted on purpose and everything else is seeded noise, so the same
seed always produces the same company. True effects used to synthesise metric
readings are never exported: outcomes have to be recovered from the readings,
the same way they would be with real data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from splat import exports

SEED = 7
AS_OF = date(2026, 10, 12)
FIRST_WEEK = date(2025, 10, 13)

FIRST_NAMES = [
    "Aarav", "Meera", "Rohan", "Priya", "Kabir", "Ananya", "Vikram", "Isha", "Arjun", "Diya",
    "Samir", "Neha", "Karan", "Tara", "Nikhil", "Zoya", "Dev", "Riya", "Omar", "Lena",
    "Jonas", "Maya", "Felix", "Sara", "Theo", "Nora", "Ivan", "Elif", "Hugo", "Asha",
    "Ravi", "Mira", "Leo", "Ines", "Yusuf", "Chloe", "Arun", "Pooja", "Marco", "Hana",
]
LAST_NAMES = [
    "Sharma", "Iyer", "Mehta", "Rao", "Kapoor", "Nair", "Gupta", "Das", "Bose", "Khan",
    "Silva", "Novak", "Berg", "Moreau", "Rossi", "Weber", "Costa", "Ito", "Park", "Lind",
]

# Uneven, like a real company.
TEAMS = {"platform": ("Platform", 11), "payments": ("Payments", 9), "growth": ("Growth", 8),
         "mobile": ("Mobile", 7), "data": ("Data", 5)}


def week(x: date) -> date:
    return x - timedelta(days=x.weekday())


def d(s: str) -> date:
    return date.fromisoformat(s)


@dataclass
class Company:
    rng: random.Random
    teams: list[dict] = field(default_factory=list)
    people: list[dict] = field(default_factory=list)
    memberships: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    goals: list[dict] = field(default_factory=list)
    initiatives: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    capabilities: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    readings: list[dict] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)
    assumptions: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    team: dict[str, list[str]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    _source_seq: int = 0

    def source(self, kind: str, title: str, at: date, author: str, text: str, mentions=(),
               messages: list[tuple[str, str]] | None = None) -> str:
        """Record a document or Slack thread. Slack threads keep their individual messages
        so they can be exported in Slack's own format."""
        self._source_seq += 1
        sid = f"SRC-{self._source_seq:04d}"
        if kind == "slack" and messages is None:
            messages = [(author, text)]
        self.sources.append({"id": sid, "kind": kind, "title": title, "at": at.isoformat(),
                             "author": author, "text": text, "mentions": [m for m in mentions if m],
                             "channel": title.split(":")[0].lstrip("#") if kind == "slack" else None,
                             "messages": messages})
        return sid

    def lead(self, t: str) -> str:
        return self.team[t][0]

    def member(self, t: str, k: int) -> str:
        return self.team[t][k % len(self.team[t])]

    def first(self, pid: str) -> str:
        return self.names[pid].split()[0]


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def build_people(c: Company) -> None:
    surnames = c.rng.sample(LAST_NAMES * 2, len(FIRST_NAMES))
    i = 0
    for tid, (name, size) in TEAMS.items():
        c.teams.append({"id": tid, "name": name})
        c.team[tid] = []
        for _ in range(size):
            full = f"{FIRST_NAMES[i]} {surnames[i]}"
            pid = f"P{i + 1:02d}"
            first, last = full.lower().split()
            c.people.append({"id": pid, "name": full, "email": f"{first}.{last}@kestrel.example"})
            c.names[pid] = full
            c.team[tid].append(pid)
            c.memberships.append({"person": pid, "team": tid, "since": FIRST_WEEK.isoformat(), "until": None})
            i += 1


def leave(c: Company, person: str, on: str) -> None:
    for m in c.memberships:
        if m["person"] == person and m["until"] is None:
            m["until"] = on


def move(c: Company, person: str, to: str, on: str) -> None:
    leave(c, person, on)
    c.memberships.append({"person": person, "team": to, "since": on, "until": None})


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

METRICS = [
    # id, name, unit, better, baseline, weekly noise (fraction)
    ("api_p95_latency_ms", "API p95 latency", "ms", "lower", 420.0, 0.016),
    ("monthly_churn_pct", "Monthly churn", "%", "lower", 3.1, 0.018),
    ("weekly_signups", "Self-serve signups per week", "signups", "higher", 180.0, 0.022),
    ("invoice_delivery_rate", "Invoice delivery success", "%", "higher", 97.8, 0.0012),
    ("support_tickets_weekly", "Support tickets per week", "tickets", "lower", 260.0, 0.022),
    ("eu_signups_weekly", "EU signups per week", "signups", "higher", 6.0, 0.05),
    ("infra_cost_usd_monthly", "Infrastructure cost per month", "USD", "lower", 48000.0, 0.012),
    ("days_to_paid", "Average days until an invoice is paid", "days", "lower", 21.0, 0.015),
    ("deploys_weekly", "Production deploys per week", "deploys", "higher", 14.0, 0.04),
    ("mobile_crash_free_pct", "Crash-free mobile sessions", "%", "higher", 98.6, 0.0008),
    ("activation_rate_pct", "New accounts sending an invoice in 7 days", "%", "higher", 41.0, 0.02),
]
METRIC_NAME = {m[0]: m[1] for m in METRICS}


def build_readings(c: Company, effects: list[tuple[str, date, float]]) -> None:
    """Weekly readings: baseline, seeded noise, and step effects that ramp in over two weeks."""
    weeks, w = [], FIRST_WEEK
    while w <= AS_OF:
        weeks.append(w)
        w += timedelta(days=7)
    for mid, _, _, _, base, noise in METRICS:
        for w in weeks:
            factor = 1.0
            for metric, start, change in effects:
                if metric == mid and w >= week(start):
                    factor *= 1 + change * min(1.0, ((w - week(start)).days / 7 + 1) / 2)
            c.readings.append({"metric": mid, "at": w.isoformat(),
                               "value": round(base * factor * (1 + c.rng.gauss(0, noise)), 3)})


# ---------------------------------------------------------------------------
# Goals, initiatives, projects
# ---------------------------------------------------------------------------

def build_plan(c: Company) -> None:
    owners = {
        "api_p95_latency_ms": c.lead("platform"),
        "monthly_churn_pct": c.lead("growth"),
        "weekly_signups": c.member("growth", 1),
        "invoice_delivery_rate": c.lead("payments"),
        "support_tickets_weekly": c.member("payments", 2),
        "eu_signups_weekly": c.member("growth", 3),  # this owner leaves in May
        "infra_cost_usd_monthly": None,  # nobody owns infrastructure cost
        "days_to_paid": c.member("payments", 1),
        "deploys_weekly": c.member("platform", 1),
        "mobile_crash_free_pct": c.lead("mobile"),
        "activation_rate_pct": c.member("growth", 2),
    }
    for mid, name, unit, better, _, _ in METRICS:
        c.metrics.append({"id": mid, "name": name, "unit": unit, "better": better, "owner": owners[mid]})

    goals = [
        ("G-1", "Cut API p95 latency below 300 ms", "active", c.lead("platform"), "api_p95_latency_ms", None),
        ("G-2", "Reduce monthly churn to 2%", "active", c.lead("growth"), "monthly_churn_pct", None),
        ("G-3", "Grow self-serve signups by 40%", "active", c.member("growth", 1), "weekly_signups", None),
        ("G-4", "Launch Kestrel in the EU", "cancelled", c.member("growth", 3), "eu_signups_weekly", "2026-07-01"),
        ("G-5", "Reach 99.9% invoice delivery", "active", c.lead("payments"), "invoice_delivery_rate", None),
        ("G-6", "Get customers paid within 14 days", "active", c.member("payments", 1), "days_to_paid", None),
        ("G-7", "Ship to production twice as often", "active", c.member("platform", 1), "deploys_weekly", None),
    ]
    for gid, title, status, owner, metric, cancelled in goals:
        c.goals.append({"id": gid, "title": title, "status": status, "owner": owner,
                        "metric": metric, "cancelled_at": cancelled})

    c.initiatives += [
        {"id": "I-1", "title": "Latency programme", "goal": "G-1", "status": "active"},
        {"id": "I-2", "title": "Retention programme", "goal": "G-2", "status": "active"},
        {"id": "I-3", "title": "Growth loops", "goal": "G-3", "status": "active"},
        {"id": "I-4", "title": "EU expansion", "goal": "G-4", "status": "active"},  # goal cancelled, still active
        {"id": "I-5", "title": "Delivery reliability", "goal": "G-5", "status": "active"},
        {"id": "I-6", "title": "Faster payments", "goal": "G-6", "status": "active"},
        {"id": "I-7", "title": "Release engineering", "goal": "G-7", "status": "active"},
        {"id": "I-8", "title": "Mobile quality", "goal": None, "status": "active"},  # serves no goal
    ]
    c.capabilities += [
        {"id": "CAP-retry", "name": "Retry and backoff for outbound calls"},
        {"id": "CAP-cache", "name": "Read caching"},
        {"id": "CAP-reporting", "name": "Revenue reporting"},
        {"id": "CAP-theme", "name": "UI theming"},
    ]
    projects = [
        # id, title, team, initiative, status, capability
        ("EP-1", "Invoice read cache", "platform", "I-1", "done", "CAP-cache"),
        ("EP-2", "Async PDF rendering", "platform", "I-1", "done", None),
        ("EP-3", "Precomputed dashboard aggregates", "data", "I-1", "active", "CAP-cache"),
        ("EP-4", "Annual plan pricing", "growth", "I-2", "done", None),
        ("EP-5", "Guided onboarding checklist", "mobile", "I-2", "done", None),
        ("EP-6", "Signup flow redesign", "growth", "I-3", "done", None),
        ("EP-7", "Referral programme", "growth", "I-3", "active", None),
        ("EP-8", "EU data residency", "platform", "I-4", "active", None),
        ("EP-9", "Database connection pool tuning", "platform", "I-1", "done", None),
        ("EP-10", "Email delivery provider switch", "payments", "I-5", "done", None),
        ("EP-11", "Admin panel theme refresh", "mobile", None, "active", "CAP-theme"),
        ("EP-12", "Customer profile cache", "payments", "I-1", "done", "CAP-cache"),
        ("EP-13", "Webhook retry library", "payments", "I-5", "active", "CAP-retry"),
        ("EP-14", "Shared retry and backoff toolkit", "platform", "I-1", "active", "CAP-retry"),
        ("EP-15", "Revenue dashboard v2", "data", None, "active", "CAP-reporting"),
        ("EP-16", "Invoice renderer rewrite in Go", "platform", "I-1", "active", None),
        ("EP-17", "One-click card payments", "payments", "I-6", "done", None),
        ("EP-18", "Automatic payment reminders", "payments", "I-6", "done", None),
        ("EP-19", "Merge queue for CI", "platform", "I-7", "done", None),
        ("EP-20", "Feature flags for every release", "platform", "I-7", "active", None),
        ("EP-21", "Kotlin Multiplatform Android app", "mobile", "I-8", "done", None),
        ("EP-22", "Offline invoices on mobile", "mobile", "I-8", "active", None),
        ("EP-23", "Two-plan pricing page", "growth", "I-3", "active", None),
        ("EP-24", "Invoice template gallery", "growth", "I-3", "done", None),
        ("EP-25", "Weekly invoice statements", "payments", "I-5", "done", None),
        ("EP-26", "German localisation", "growth", "I-4", "done", None),
        ("EP-27", "Columnar analytics warehouse", "data", None, "done", None),
        ("EP-28", "Admin two-factor authentication", "platform", None, "done", None),
        ("EP-29", "Payment links in reminder emails", "payments", "I-6", "active", None),
    ]
    # Epics that cannot work without another epic.
    depends_on = {"EP-3": ["EP-1"], "EP-12": ["EP-1"], "EP-15": ["EP-3"], "EP-16": ["EP-2"],
                  "EP-20": ["EP-19"], "EP-22": ["EP-21"], "EP-24": ["EP-6"], "EP-26": ["EP-8"],
                  "EP-29": ["EP-18"]}
    for pid, title, t, ini, status, cap in projects:
        c.projects.append({"id": pid, "title": title, "team": t, "initiative": ini, "status": status,
                           "capability": cap, "owner": c.lead(t), "depends_on": depends_on.get(pid, [])})


# ---------------------------------------------------------------------------
# Assumptions and decisions
# ---------------------------------------------------------------------------

ASSUMPTIONS = [
    ("A-1", "Invoice API traffic is read-heavy, so caching reads cuts latency"),
    ("A-2", "PDF rendering is the largest share of API latency"),
    ("A-3", "Most failed invoice deliveries are bounces caused by the email provider"),
    ("A-4", "Price is the main reason customers churn"),
    ("A-5", "Customers churn because they never finish account setup"),
    ("A-6", "A shorter signup form converts more visitors"),
    ("A-7", "EU customers require their data to be stored in the EU"),
    ("A-8", "Most support tickets come from failed webhook deliveries"),
    ("A-9", "Connection pool exhaustion causes the latency spikes"),
    ("A-10", "Customers pay late because paying is inconvenient"),
    ("A-11", "Customers pay late because they forget"),
    ("A-12", "Flaky tests are what slows down merging"),
    ("A-13", "Customers are annoyed by receiving many small invoices"),
    ("A-14", "EU prospects stall because the product is only in English"),
    ("A-15", "The old Android codebase causes most mobile crashes"),
    ("A-16", "New accounts stall because they do not know how to design an invoice"),
    ("A-17", "Analytics queries drive most of the infrastructure bill"),
]

# Evidence that came in later. (assumption, date, author team, member index, kind, title, text)
CONTRADICTIONS = [
    ("A-1", "2026-03-16", "data", 1, "slack", "#data-insights: invoice API traffic mix",
     "Ran the numbers on invoice API traffic since the bulk import launch in January. Writes are now 58% "
     "of requests, up from 31% last year. It is not read-heavy any more, so caching reads will not move p95 "
     "much. Full breakdown in the notebook; happy to walk anyone through it."),
    ("A-4", "2026-09-21", "growth", 2, "doc", "Churn exit survey, Q3 summary",
     "Exit survey, 212 responses from accounts that churned between June and August. Price is the top "
     "reason for only 14% of them. Missing integrations (41%) and setup friction (27%) rank well above it. "
     "Recommendation: stop treating churn as a pricing problem."),
    ("A-12", "2026-08-24", "platform", 4, "doc", "Where merge time actually goes",
     "Measured 1,140 merged pull requests since March. Flaky test reruns account for 9% of time-to-merge. "
     "Waiting for a first review accounts for 61%. The merge queue helped because it batches merges, not "
     "because it fixed flakiness."),
    ("A-17", "2026-07-06", "data", 2, "slack", "#data-insights: what the cloud bill is made of",
     "Broke down June's bill by service. Analytics is 11% of it. Storage for invoice PDFs is 38% and "
     "egress is 22%. Moving analytics will not bend the curve."),
]
SUPPORTS = [
    ("A-2", "2025-11-17", "platform", 2, "doc", "Latency profile of the invoice API",
     "Profiled the invoice API for a week. Synchronous PDF rendering is 61% of p95 latency; "
     "database time is 18%."),
    ("A-3", "2026-04-20", "payments", 1, "slack", "#payments: delivery failure breakdown",
     "Of last month's failed invoice deliveries, 83% are bounces on the provider side, mostly throttling."),
    ("A-10", "2025-12-08", "payments", 3, "doc", "Why invoices get paid late: 30 customer calls",
     "Thirty calls with finance teams. Twenty-two said paying by bank transfer is the slow part; "
     "only four said they forget."),
]


def decision_specs() -> list[dict]:
    """Every decision: when, why, what it promised, and (hidden) what really happened."""
    return [
        dict(id="D-101", title="Cache invoice reads in Redis", team="platform", project="EP-1",
             decided="2026-02-02", shipped="2026-02-23", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.30, "2026-04-20"), effect=-0.03,
             context="p95 latency on the invoice list has crept up to over 400 ms and is the top complaint from "
                     "large accounts.",
             alternatives=["Add read replicas", "Paginate the invoice list more aggressively"],
             risk="cache invalidation bugs could show stale invoice totals"),
        dict(id="D-102", title="Cache customer profile lookups", team="payments", project="EP-12",
             decided="2026-04-06", shipped="2026-04-27", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.20, "2026-06-22"), effect=-0.02,
             context="Customer profile lookups run on almost every invoice request.",
             alternatives=["Denormalise profile fields onto invoices", "Do nothing until the Go rewrite"],
             risk="profiles change rarely but address changes must show immediately"),
        dict(id="D-103", title="Precompute dashboard aggregates", team="data", project="EP-3",
             decided="2026-09-14", shipped="2026-10-05", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.15, "2026-12-07"), effect=-0.01,
             context="Dashboard requests are slow and share the invoice API.",
             alternatives=["Move dashboards to the warehouse", "Cache the dashboard endpoint"],
             risk="aggregates may lag behind real-time invoice changes"),
        dict(id="D-104", title="Move PDF rendering to an async queue", team="platform", project="EP-2",
             decided="2025-12-01", shipped="2025-12-22", assumptions=["A-2"],
             expects=("api_p95_latency_ms", -0.25, "2026-01-26"), effect=-0.28,
             context="Every invoice save renders a PDF synchronously before responding.",
             alternatives=["Buy a faster PDF library", "Render PDFs on the client"],
             risk="users may download a PDF before it is ready"),
        dict(id="D-105", title="Switch email delivery provider", team="payments", project="EP-10",
             decided="2026-05-04", shipped="2026-05-25", assumptions=["A-3"],
             expects=("invoice_delivery_rate", 0.015, "2026-07-20"), effect=0.016,
             context="About 2% of invoice emails never arrive, and customers notice.",
             alternatives=["Negotiate higher limits with the current provider", "Send from two providers"],
             risk="sender reputation resets on a new provider"),
        dict(id="D-106", title="Offer a 20% annual plan discount", team="growth", project="EP-4",
             decided="2026-06-01", shipped="2026-06-15", assumptions=["A-4"],
             expects=("monthly_churn_pct", -0.15, "2026-08-31"), effect=-0.06,
             context="Monthly churn is 3.1%, well above the 2% goal.",
             alternatives=["Lower the entry plan price", "Add a pause option instead of cancel"],
             risk="discounting trains customers to wait for deals"),
        dict(id="D-107", title="Launch a guided onboarding checklist", team="mobile", project="EP-5",
             decided="2026-06-08", shipped="2026-06-29", assumptions=["A-5"],
             expects=("monthly_churn_pct", -0.10, "2026-09-07"), effect=-0.14,
             context="Accounts that never connect a bank account churn at three times the average rate.",
             alternatives=["Onboarding calls for every new account", "In-app tooltips only"],
             risk="a checklist can feel like homework"),
        dict(id="D-108", title="Ship dark mode in the mobile app", team="mobile", project=None,
             decided="2026-03-02", shipped="2026-04-06", assumptions=[], expects=None, effect=None,
             context="Dark mode is the most upvoted request on the mobile feedback board.",
             alternatives=["Follow the system theme only"], risk="every screen needs a second review"),
        dict(id="D-109", title="Launch a referral programme", team="growth", project="EP-7",
             decided="2026-09-07", shipped="2026-09-28", assumptions=[],
             expects=("weekly_signups", 0.15, "2026-12-14"), effect=0.03,
             context="Word of mouth already drives a quarter of signups.",
             alternatives=["Partner with accounting firms", "Paid search"], risk="referral fraud"),
        dict(id="D-110", title="Rewrite the invoice renderer in Go", team="platform", project="EP-16",
             decided="2026-07-06", shipped=None, assumptions=["A-2"], expects=None, effect=None,
             context="The renderer is the slowest service and hard to change.",
             alternatives=["Optimise the existing renderer", "Use a hosted PDF service"],
             risk="a rewrite stalls feature work for a quarter"),
        dict(id="D-111", title="Store EU customer data in Frankfurt", team="platform", project="EP-8",
             decided="2026-01-12", shipped=None, assumptions=["A-7"],
             expects=("eu_signups_weekly", 1.0, "2026-09-28"), effect=None,
             context="Three EU prospects asked where their data would live.",
             alternatives=["Sign standard contractual clauses only", "Wait for a larger EU pipeline"],
             risk="running a second region doubles on-call load"),
        dict(id="D-112", title="Retry failed webhooks with exponential backoff", team="payments",
             project="EP-13", decided="2026-03-09", shipped="2026-03-30", assumptions=["A-8"],
             expects=("support_tickets_weekly", -0.20, "2026-05-25"), effect=-0.22,
             context="Support sees a wave of 'payment not recorded' tickets after every partner outage.",
             alternatives=["Ask partners to poll instead", "Add a manual replay button"],
             risk="retries could hammer a partner that is already down"),
        dict(id="D-113", title="Tune database connection pools", team="platform", project="EP-9",
             decided="2026-07-27", shipped="2026-08-10", assumptions=["A-9"],
             expects=("api_p95_latency_ms", -0.10, "2026-09-21"), effect=-0.08,
             context="Latency spikes line up with connection pool saturation alerts.",
             alternatives=["Add a connection proxy", "Scale the database up"],
             risk="larger pools can overload the database"),
        dict(id="D-114", title="Shorten the signup form to three fields", team="growth", project="EP-6",
             decided="2026-01-19", shipped="2026-02-09", assumptions=["A-6"],
             expects=("weekly_signups", 0.20, "2026-04-06"), effect=0.09,
             context="Half of visitors who start the signup form abandon it.",
             alternatives=["Sign in with Google only", "Remove the company size question"],
             risk="sales loses qualifying data"),
        dict(id="D-115", title="Send a weekly invoice digest email", team="payments", project=None,
             decided="2025-11-03", shipped="2025-11-17", assumptions=[], expects=None, effect=None,
             status="superseded", context="Customers want a summary of unpaid invoices.",
             alternatives=["A dashboard widget instead"], risk="more email"),
        dict(id="D-116", title="Make the invoice digest daily and opt-in", team="payments", project=None,
             decided="2026-02-16", shipped="2026-03-02", assumptions=[], expects=None, effect=None,
             supersedes="D-115", context="The weekly digest has a 4% open rate and drew complaints.",
             alternatives=["Kill the digest entirely"], risk="few people opt in"),
        dict(id="D-117", title="Add one-click card payments to invoices", team="payments", project="EP-17",
             decided="2025-12-15", shipped="2026-01-12", assumptions=["A-10"],
             expects=("days_to_paid", -0.20, "2026-03-09"), effect=-0.24,
             context="Invoices take 21 days to get paid on average; bank transfer is the only option.",
             alternatives=["Partner with a payments aggregator", "Offer early-payment discounts"],
             risk="card fees eat into small invoices"),
        dict(id="D-118", title="Send automatic payment reminders", team="payments", project="EP-18",
             decided="2026-06-01", shipped="2026-06-22", assumptions=["A-11"],
             expects=("days_to_paid", -0.10, "2026-08-17"), effect=-0.025,
             context="Customers ask for a way to nudge late payers without writing emails themselves.",
             alternatives=["Let customers schedule their own reminders"], risk="reminders feel pushy"),
        dict(id="D-119", title="Adopt a merge queue for CI", team="platform", project="EP-19",
             decided="2026-02-23", shipped="2026-03-16", assumptions=["A-12"],
             expects=("deploys_weekly", 0.30, "2026-05-11"), effect=0.36,
             context="Pull requests sit for days and main breaks twice a week.",
             alternatives=["Quarantine flaky tests", "Split the monorepo"],
             risk="a slow queue blocks everyone"),
        dict(id="D-120", title="Put every release behind a feature flag", team="platform", project="EP-20",
             decided="2026-06-29", shipped="2026-07-13", assumptions=["A-12"],
             expects=("deploys_weekly", 0.15, "2026-09-07"), effect=0.07,
             context="Releases are batched because half-finished features cannot ship.",
             alternatives=["Release branches per feature"], risk="stale flags pile up"),
        dict(id="D-121", title="Rebuild the Android app with Kotlin Multiplatform", team="mobile",
             project="EP-21", decided="2025-11-24", shipped="2026-03-30", assumptions=["A-15"],
             expects=("mobile_crash_free_pct", 0.006, "2026-05-25"), effect=0.0065,
             context="Android crash-free sessions trail iOS by more than a point.",
             alternatives=["Fix the top twenty crashes in place"], risk="a long rewrite with no visible wins"),
        dict(id="D-122", title="Support offline invoices on mobile", team="mobile", project="EP-22",
             decided="2026-08-10", shipped=None, assumptions=[], expects=None, effect=None,
             context="Tradespeople create invoices on site with no signal.",
             alternatives=["Save drafts locally only"], risk="sync conflicts"),
        dict(id="D-123", title="Simplify the pricing page to two plans", team="growth", project="EP-23",
             decided="2026-09-07", shipped="2026-09-21", assumptions=[],
             expects=("activation_rate_pct", 0.10, "2026-11-30"), effect=0.02,
             context="Four plans confuse new visitors, according to session recordings.",
             alternatives=["Add a plan picker quiz"], risk="upsell paths get shorter"),
        dict(id="D-124", title="Add an invoice template gallery", team="growth", project="EP-24",
             decided="2026-02-23", shipped="2026-03-16", assumptions=["A-16"],
             expects=("activation_rate_pct", 0.08, "2026-05-11"), effect=0.105,
             context="New accounts open the invoice editor and leave without saving.",
             alternatives=["A design wizard", "Import invoices from a spreadsheet"],
             risk="templates look generic"),
        dict(id="D-125", title="Require two-factor authentication for admins", team="platform", project="EP-28",
             decided="2026-04-13", shipped="2026-05-04", assumptions=[], expects=None, effect=None,
             context="A customer's admin account was phished in March.",
             alternatives=["Make it optional with a banner"], risk="support load from locked-out admins"),
        dict(id="D-126", title="Retire the annual plan discount", team="growth", project="EP-4",
             decided="2026-10-05", shipped=None, assumptions=[], expects=None, effect=None,
             supersedes="D-106", context="The exit survey says price is not why customers leave.",
             alternatives=["Keep the discount for existing annual customers"], risk="annual signups dip"),
        dict(id="D-127", title="Batch small invoices into weekly statements", team="payments", project="EP-25",
             decided="2026-07-06", shipped="2026-07-27", assumptions=["A-13"],
             expects=("support_tickets_weekly", -0.10, "2026-09-21"), effect=0.02,
             context="Customers with many small invoices write in asking for one statement.",
             alternatives=["Let customers choose per client"], risk="statements confuse accounting exports"),
        dict(id="D-128", title="Translate the app into German", team="growth", project="EP-26",
             decided="2026-02-09", shipped="2026-04-13", assumptions=["A-14"],
             expects=("eu_signups_weekly", 0.50, "2026-06-08"), effect=0.08,
             context="German prospects make up most of the EU pipeline.",
             alternatives=["Hire a German-speaking salesperson first"], risk="support in German"),
        dict(id="D-129", title="Move analytics to a columnar warehouse", team="data", project="EP-27",
             decided="2026-05-11", shipped="2026-06-01", assumptions=["A-17"],
             expects=("infra_cost_usd_monthly", -0.15, "2026-07-27"), effect=0.02,
             context="The infrastructure bill grew 12% in March.",
             alternatives=["Reserved instances", "Archive old invoice PDFs"],
             risk="analysts must learn a new tool"),
        dict(id="D-130", title="Create a second on-call rotation", team="platform", project=None,
             decided="2026-08-31", shipped="2026-09-14", assumptions=[], expects=None, effect=None,
             context="Platform engineers were paged 41 times in August.",
             alternatives=["Hire an SRE first"], risk="thin rotations burn people out faster"),
    ]


REPLIES = [
    "+1, this has bitten us twice this quarter.",
    "How sure are we this moves {metric}? Do we have numbers, or is this a hunch?",
    "Worried about {risk}. Can we roll it out to a few accounts first?",
    "Did we consider the other option, '{alt}'?",
    "We looked at '{alt}' last quarter. Slower to ship, and it does not fix the root cause.",
    "Happy to pair on this, I touched that code last month.",
    "Can we write down what success looks like before we start?",
    "Fine by me as long as we can turn it off quickly.",
    "Is this the most important thing for this quarter?",
    "Agree. Let's not over-design it.",
    "I'd want a dashboard for {metric} before and after, otherwise we will argue about it later.",
    "This feels like the right call. The current setup is held together with tape.",
    "Who owns this once it ships? I don't want it to become nobody's problem.",
    "Can we time-box it to two sprints and revisit?",
    "Not against it, but {risk} worries me more than people think.",
    "Is there a customer asking for this, or is it us?",
    "We tried something close to this two years ago. Different team, though, so maybe it lands differently now.",
    "Sounds good. Please loop in support before launch.",
    "Let's make sure the on-call runbook is updated with this.",
    "What happens if it doesn't work? Is there a clean rollback?",
    "Strong yes from me.",
    "I can take the first ticket.",
    "Have we checked how '{alt}' compares on cost?",
    "Can we get a rough estimate before committing?",
    "Agree with the direction. Small nit: let's name the flag something searchable.",
    "My gut says {metric} is driven by something else, but I don't have data either way.",
    "Ok if we keep the old path around for a month.",
    "Love it. This comes up in every customer call I'm on.",
    "Please write the decision down this time, we lost the reasoning on the last one.",
    "Let's do it. Worst case we learn something.",
]


NEIGHBOUR = {"platform": "data", "data": "platform", "mobile": "data", "payments": "growth", "growth": "payments"}
CROSS_TEAM_REPLIES = [
    "Chiming in from the next team over: this affects us too, please keep us posted.",
    "We depend on this path, so a heads-up before the rollout would help.",
    "Sharing context: we saw something similar on our side last month.",
    "Happy to review from our side.",
]

# Decisions that only make sense because an earlier decision was made.
BUILDS_ON = {
    "D-102": ["D-101"], "D-103": ["D-101"], "D-110": ["D-104"], "D-118": ["D-117"],
    "D-120": ["D-119"], "D-122": ["D-121"], "D-123": ["D-114"], "D-128": ["D-111"],
}


def build_decisions(c: Company) -> list[tuple[str, date, float]]:
    for aid, statement in ASSUMPTIONS:
        c.assumptions.append({"id": aid, "statement": statement})
    by_id = {a["id"]: a for a in c.assumptions}
    for aid, at, t, k, kind, title, text in CONTRADICTIONS:
        sid = c.source(kind, title, d(at), c.member(t, k), text)
        by_id[aid].update({"contradicted_by": sid, "contradicted_at": at})
    for aid, at, t, k, kind, title, text in SUPPORTS:
        by_id[aid]["supported_by"] = c.source(kind, title, d(at), c.member(t, k), text)

    effects: list[tuple[str, date, float]] = []
    for spec in decision_specs():
        decider, proposer = c.lead(spec["team"]), c.member(spec["team"], 1 + c.rng.randint(0, 3))
        metric_text = "the target metric"
        success = "No success metric was set."
        if spec["expects"]:
            metric, change, by = spec["expects"]
            metric_text = METRIC_NAME[metric].lower()
            success = (f"Success means {metric_text} changes by {change:+.1%} from its pre-launch level by {by}. "
                       f"Owner of the result: {c.names[decider]}.")
        reasons = "; ".join(by_id[a]["statement"] for a in spec["assumptions"]) or "not written down"

        # The proposal thread: a few people, some agreement, some pushback.
        project_title = next((p["title"] for p in c.projects if p["id"] == spec["project"]), None)
        opener = f"Proposal for the {project_title} work: " if project_title else "Proposal: "
        messages = [(proposer, f"{opener}{spec['title'].lower()}. {spec['context']}")]
        for reply in c.rng.sample(REPLIES, c.rng.randint(2, 4)):
            speaker = c.rng.choice([p for p in c.team[spec["team"]] if p != proposer])
            messages.append((speaker, reply.format(metric=metric_text, risk=spec["risk"],
                                                   alt=spec["alternatives"][0])))
        # Teams that work closely chime in on each other's proposals. Payments and
        # Platform never do, which is why they end up building the same thing.
        if c.rng.random() < 0.7:
            neighbour = c.rng.choice(c.team[NEIGHBOUR[spec["team"]]])
            messages.insert(len(messages) // 2 + 1, (neighbour, c.rng.choice(CROSS_TEAM_REPLIES)))
        messages.append((decider, "Going ahead. Decision record to follow."))
        thread = c.source("slack", f"#{spec['team']}: {spec['title'].lower()}",
                          d(spec["decided"]) - timedelta(days=c.rng.randint(3, 9)), proposer,
                          "\n".join(f"{c.first(who)}: {msg}" for who, msg in messages),
                          mentions=[spec["project"]], messages=messages)

        doc = c.source(
            "doc", f"Decision record {spec['id']}: {spec['title']}", d(spec["decided"]), decider,
            f"Context: {spec['context']}\n"
            f"Decision: {spec['title']}.\n"
            + (f"Project: {project_title}.\n" if project_title else "")
            + f"Why: {reasons}.\n"
            f"Options considered and not chosen: {'; '.join(spec['alternatives'])}.\n"
            f"Risks: {spec['risk']}.\n"
            f"{success}",
            mentions=[spec["project"]])

        c.decisions.append({
            "id": spec["id"], "title": spec["title"], "team": spec["team"], "decider": decider,
            "project": spec["project"], "decided_at": spec["decided"], "shipped_at": spec["shipped"],
            "status": spec.get("status", "active"), "supersedes": spec.get("supersedes"),
            "builds_on": BUILDS_ON.get(spec["id"], []),
            "assumptions": spec["assumptions"], "decided_in": doc, "discussed_in": thread,
            "expects": None if not spec["expects"] else {
                "metric": spec["expects"][0], "change": spec["expects"][1],
                "judge_by": spec["expects"][2], "source": doc},
        })
        if spec["effect"] is not None and spec["shipped"]:
            effects.append((spec["expects"][0], d(spec["shipped"]), spec["effect"]))

    # A reversal marks the decision it replaces as superseded.
    for dec in c.decisions:
        if dec["supersedes"]:
            next(x for x in c.decisions if x["id"] == dec["supersedes"])["status"] = "superseded"

    # Follow-ups people posted after results came in.
    c.source("slack", "#platform: async PDF results", d("2026-02-02"), c.lead("platform"),
             "Async PDF rendering has been live for six weeks. p95 on invoice saves is down by about a "
             "quarter and the timeout alerts have stopped.", mentions=["EP-2"])
    c.source("slack", "#platform: invoice cache, eight weeks in", d("2026-04-27"), c.member("platform", 2),
             "Cache hit rate on invoice reads is 94%, but p95 barely moved. Most slow requests are writes. "
             "Not sure the cache was the right lever.", mentions=["EP-1"])
    return effects


# ---------------------------------------------------------------------------
# Tasks and everyday noise
# ---------------------------------------------------------------------------

TASKS = {
    "EP-1": ["Add Redis cluster to staging", "Cache GET /invoices responses", "Invalidate cache on invoice update",
             "Handle cache stampede on the invoice list", "Add cache hit rate panel", "Load test cached endpoints"],
    "EP-2": ["Move PDF rendering onto the job queue", "Show 'PDF generating' state in the UI",
             "Retry failed renders", "Alert on queue depth", "Remove synchronous render path"],
    "EP-3": ["Design aggregate tables", "Nightly aggregate job", "Serve dashboard tiles from aggregates",
             "Backfill twelve months of aggregates", "Compare aggregates against live totals"],
    "EP-4": ["Annual plan in billing system", "Discount banner on upgrade page", "Proration for mid-cycle switch",
             "Annual plan invoices and receipts"],
    "EP-5": ["Checklist component", "Track checklist completion events", "Bank connection step",
             "First invoice step", "Email nudge for unfinished checklists"],
    "EP-6": ["Cut signup form to three fields", "Move company details to onboarding", "A/B test old vs new form",
             "Fix password strength meter on mobile"],
    "EP-7": ["Referral codes", "Referral reward ledger", "Referral landing page", "Fraud checks for self-referrals",
             "Share sheet on mobile"],
    "EP-8": ["Provision Frankfurt region", "Replicate customer tables to EU", "Route EU accounts by region",
             "EU backups and restore drill", "Data processing addendum for EU customers",
             "Region switch in admin tools", "EU status page"],
    "EP-9": ["Measure pool saturation", "Raise pool size on API nodes", "Add pool wait time metric",
             "Tune idle connection timeout"],
    "EP-10": ["Set up new provider domains", "Warm up sending IPs", "Dual-send during migration",
              "Bounce webhook handling", "Remove old provider"],
    "EP-11": ["Theme tokens for admin panel", "Dark palette for admin tables", "Admin typography refresh",
              "Update admin icons"],
    "EP-12": ["Cache customer profiles", "Invalidate profile cache on edit", "Profile cache metrics"],
    "EP-13": ["Retry queue for outbound webhooks", "Exponential backoff with jitter", "Dead letter queue",
              "Replay webhooks from admin", "Webhook retry dashboard"],
    "EP-14": ["Generic retry helper", "Backoff policies config", "Circuit breaker for outbound HTTP",
              "Retry toolkit docs", "Adopt toolkit in email sender"],
    "EP-15": ["Revenue by plan chart", "MRR movement table", "Export revenue to CSV", "Revenue dashboard permissions"],
    "EP-16": ["Go renderer skeleton", "Port invoice layout to Go templates", "Font embedding in Go renderer",
              "Golden file tests for PDFs", "Shadow traffic to Go renderer"],
    "EP-17": ["Card payment button on invoices", "Payment processor integration", "Card fee settings",
              "Mark invoice paid on successful charge", "Refunds for card payments"],
    "EP-18": ["Reminder schedule settings", "Reminder email template", "Stop reminders when paid",
              "Reminder opt-out link"],
    "EP-19": ["Enable merge queue on main", "Parallelise test shards", "Queue status in chat", "Flaky test report"],
    "EP-20": ["Feature flag service", "Flag SDK for backend", "Flag SDK for web", "Stale flag report",
              "Flag audit log"],
    "EP-21": ["Shared invoice model module", "Kotlin networking layer", "Port invoice list screen",
              "Port invoice editor", "Crash reporting for new app", "Staged rollout to Android users"],
    "EP-22": ["Local invoice storage", "Sync queue for offline edits", "Conflict resolution rules",
              "Offline indicator", "Offline PDF preview"],
    "EP-23": ["Two-plan pricing page design", "Plan comparison table", "Migrate legacy plan names",
              "Pricing page experiment"],
    "EP-24": ["Template gallery UI", "Ten starter templates", "Template preview", "Track template usage"],
    "EP-25": ["Weekly statement generator", "Statement email", "Statement PDF layout", "Opt-in setting"],
    "EP-26": ["Extract UI strings", "German translations", "German invoice templates", "Date and number formats"],
    "EP-27": ["Warehouse cluster setup", "Daily export of invoice events", "Port finance queries",
              "Retire old analytics replica"],
    "EP-28": ["TOTP enrolment for admins", "Recovery codes", "Enforce 2FA at login", "Admin 2FA support runbook"],
    "EP-29": ["Payment link in reminder email", "Track link clicks", "Link expiry rules"],
}

STRAY_TASKS = [
    ("Migrate CI runners to ARM", "platform"), ("Rebuild the internal status page", "data"),
    ("Prototype a Slack bot for invoice lookups", "growth"), ("Investigate slow admin search", "mobile"),
]

CHATTER = [
    ("standup", "Yesterday: reviewed PRs and fixed a flaky test. Today: pairing on {project}. No blockers."),
    ("standup", "Out this afternoon for a dentist appointment, back tomorrow."),
    ("standup", "Still chasing the {project} bug from Friday; will post findings."),
    ("standup", "Shipped the small copy fix. Moving to {project} next."),
    ("bug", "Customer reports invoice totals rounding differently in CSV export. Repro steps in the ticket."),
    ("bug", "Login page throws a 500 when the email has a trailing space."),
    ("bug", "{project}: staging deploy failed on a migration lock, retried and it passed."),
    ("release", "Release notes: small fixes to {project}, faster search in the admin panel."),
    ("random", "Lunch order for Thursday is in the sheet, last call at noon."),
    ("random", "Reminder that the office wifi is down for maintenance on Saturday."),
    ("random", "Book club this month: The Phoenix Project. Bring snacks."),
    ("question", "Does anyone know who owns the invoice numbering logic? Need to change the prefix format."),
    ("question", "Where do we keep the runbook for the PDF queue?"),
    ("question", "Is there a reason we pin the old version of the date library?"),
    ("review", "Left comments on the {project} PR, mostly naming. Approve after those."),
    ("standup", "Yesterday: on-call handover, quiet night. Today: writing tests for {project}."),
    ("standup", "Blocked on a review for {project}, anyone free after lunch?"),
    ("standup", "Spent the morning in interviews. Afternoon on {project}."),
    ("standup", "Wrapping up the {project} migration script, should merge today."),
    ("standup", "Sick today, will check messages later."),
    ("standup", "Demo prep for Friday, then back to {project}."),
    ("bug", "Duplicate reminder emails for two customers this morning. Looking into it."),
    ("bug", "The invoice PDF cuts off long line items on page breaks."),
    ("bug", "Timezone bug: invoices dated in UTC show the previous day for customers in the Americas."),
    ("bug", "Search returns archived customers. Low priority but confusing."),
    ("bug", "{project}: flaky integration test again, quarantined it for now."),
    ("release", "Shipped: {project} behind a flag for internal accounts."),
    ("release", "Hotfix out for the CSV export encoding issue."),
    ("random", "Coffee machine on floor two is fixed, rejoice."),
    ("random", "Happy birthday to everyone born in this month! Cake at 4."),
    ("random", "Anyone up for badminton on Thursday evening?"),
    ("random", "Reminder: expense reports are due on the 25th."),
    ("random", "The all-hands recording is up in the shared drive."),
    ("random", "Who left a laptop charger in meeting room B?"),
    ("question", "Is staging down for anyone else?"),
    ("question", "What's the process for getting read access to production logs?"),
    ("question", "Who can approve a new SaaS tool purchase?"),
    ("question", "Do we have a style guide for error messages?"),
    ("question", "Is the {project} work still happening this quarter?"),
    ("review", "Approved the {project} change, nice cleanup."),
    ("review", "Requested changes on {project}: needs a migration rollback."),
    ("review", "Can someone with more context review the {project} PR? It touches billing."),
]


def build_tasks(c: Company) -> None:
    seq = 0
    decision_of = {x["project"]: x for x in c.decisions if x["project"]}
    for p in c.projects:
        dec = decision_of.get(p["id"])
        start = d(dec["decided_at"]) if dec else d("2026-05-04")
        titles = TASKS[p["id"]]
        for k, title in enumerate(titles):
            seq += 1
            created = start + timedelta(days=c.rng.randint(0, 35) + 4 * k)
            if p["id"] == "EP-8":
                # EU work keeps going after the EU goal was cancelled.
                created = d("2026-07-01") + timedelta(days=c.rng.randint(5, 90))
            closed = created + timedelta(days=c.rng.randint(2, 18))
            still_open = p["status"] == "active" and k >= len(titles) - 2
            c.tasks.append({
                "id": f"T-{seq:03d}", "kind": "pr" if c.rng.random() < 0.45 else "ticket", "title": title,
                "project": p["id"], "assignee": c.rng.choice(c.team[p["team"]]),
                "created_at": min(created, AS_OF).isoformat(),
                "closed_at": None if still_open or closed > AS_OF else closed.isoformat(),
            })
    for title, t in STRAY_TASKS:
        seq += 1
        c.tasks.append({"id": f"T-{seq:03d}", "kind": "ticket", "title": title, "project": None,
                        "assignee": c.rng.choice(c.team[t]),
                        "created_at": (d("2026-08-03") + timedelta(days=c.rng.randint(0, 50))).isoformat(),
                        "closed_at": None})


def build_chatter(c: Company, n: int = 180) -> None:
    """Everyday messages that relate to no decision. They only mention projects of
    the author's own team, like real standups do."""
    projects_of = {t: [p for p in c.projects if p["team"] == t] for t in TEAMS}
    span = (AS_OF - FIRST_WEEK).days
    for _ in range(n):
        t = c.rng.choice(list(TEAMS))
        author = c.rng.choice(c.team[t])
        kind, template = c.rng.choice(CHATTER)
        project = c.rng.choice(projects_of[t])
        text = template.format(project=project["title"].lower())
        channel = {"standup": f"#{t}-standup", "random": "#random", "question": "#engineering"}.get(kind, f"#{t}")
        c.source("slack", f"{channel}: {text[:48].rstrip()}", FIRST_WEEK + timedelta(days=c.rng.randint(0, span)),
                 author, text, mentions=[project["id"]] if "{project}" in template else [])


# ---------------------------------------------------------------------------

def build(seed: int = SEED) -> Company:
    c = Company(rng=random.Random(seed))
    build_people(c)
    build_plan(c)

    # People changes. None of them bridges Payments and Platform, so the two
    # retry projects stay genuinely unaware of each other.
    leave(c, c.member("growth", 3), "2026-05-29")      # EU metric and goal owner
    leave(c, c.member("platform", 9), "2026-08-14")
    move(c, c.member("growth", 5), "payments", "2026-04-06")
    move(c, c.member("mobile", 5), "data", "2026-06-15")

    effects = build_decisions(c)

    # An incident lifts latency right after the pool tuning shipped.
    inc = c.source("doc", "Incident INC-42: degraded storage volume", d("2026-08-17"), c.member("platform", 3),
                   "Timeline: 08:40 p95 latency alerts. 09:10 traced to a degraded storage volume under the primary "
                   "database. Mitigated by failing over to a replica on 2026-09-07 after the vendor confirmed the "
                   "volume fault. Latency stayed elevated for three weeks.")
    c.incidents.append({"id": "INC-42", "title": "Degraded storage volume", "at": "2026-08-17",
                        "until": "2026-09-07", "metric": "api_p95_latency_ms", "source": inc})
    effects += [("api_p95_latency_ms", d("2026-08-17"), 0.09), ("api_p95_latency_ms", d("2026-09-07"), -0.083)]

    # Background drift nobody decided.
    effects += [("infra_cost_usd_monthly", d("2026-03-02"), 0.12), ("weekly_signups", d("2026-05-04"), 0.04)]

    build_readings(c, effects)
    build_tasks(c)
    build_chatter(c)
    return c


def to_json(c: Company) -> dict:
    return {
        "company": "Kestrel", "as_of": AS_OF.isoformat(), "seed": SEED,
        "teams": c.teams, "people": c.people, "memberships": c.memberships,
        "goals": c.goals, "initiatives": c.initiatives, "projects": c.projects,
        "capabilities": c.capabilities, "tasks": c.tasks, "metrics": c.metrics,
        "readings": c.readings, "incidents": c.incidents, "assumptions": c.assumptions,
        "decisions": c.decisions, "sources": c.sources,
    }


def main(out: str = "data/kestrel") -> None:
    company = build()
    root = exports.write(to_json(company), out)
    files = sum(1 for p in Path(root).rglob("*") if p.is_file())
    print(f"Wrote {files} export files to {root}: {len(company.decisions)} decisions, {len(company.tasks)} "
          f"tickets and pull requests, {len(company.readings)} metric readings, {len(company.sources)} "
          "Slack threads and documents")


if __name__ == "__main__":
    main()
