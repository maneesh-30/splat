"""Deterministic generator for the synthetic company "Kestrel".

Kestrel is a fictional 40-person B2B invoicing company. The generator writes one
JSON file describing its goals, work, decisions, assumptions, metrics and the
documents and threads those facts come from.

Every story in the data is planted on purpose, and the rest is seeded noise, so the
same seed always produces the same company. True effects used to synthesise metric
readings are never exported: the product has to recover outcomes from the readings,
the same way it would with real data.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

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

TEAMS = {
    "platform": "Platform",
    "payments": "Payments",
    "growth": "Growth",
    "mobile": "Mobile",
    "data": "Data",
}


def week(d: date) -> date:
    """Snap a date to the Monday of its week."""
    return d - timedelta(days=d.weekday())


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
    _source_seq: int = 0

    def source(self, kind: str, title: str, at: date, author: str, text: str, mentions=()) -> str:
        self._source_seq += 1
        sid = f"SRC-{self._source_seq:04d}"
        self.sources.append({
            "id": sid, "kind": kind, "title": title, "at": at.isoformat(),
            "author": author, "text": text, "mentions": list(mentions),
        })
        return sid


# ---------------------------------------------------------------------------
# People and teams
# ---------------------------------------------------------------------------

def build_people(c: Company) -> dict[str, list[str]]:
    for tid, name in TEAMS.items():
        c.teams.append({"id": tid, "name": name})

    names = [f"{f} {l}" for f, l in zip(FIRST_NAMES, c.rng.sample(LAST_NAMES * 2, len(FIRST_NAMES)))]
    by_team: dict[str, list[str]] = {t: [] for t in TEAMS}
    team_ids = list(TEAMS)
    for i, full in enumerate(names):
        pid = f"P{i + 1:02d}"
        handle = full.split()[0].lower()
        c.people.append({"id": pid, "name": full, "email": f"{handle}.{full.split()[1].lower()}@kestrel.example"})
        team = team_ids[i % len(team_ids)]
        by_team[team].append(pid)
        c.memberships.append({"person": pid, "team": team, "since": FIRST_WEEK.isoformat(), "until": None})
    return by_team


def leave(c: Company, person: str, on: str) -> None:
    for m in c.memberships:
        if m["person"] == person and m["until"] is None:
            m["until"] = on


# ---------------------------------------------------------------------------
# Metrics and readings
# ---------------------------------------------------------------------------

METRICS = [
    # id, name, unit, better, baseline, weekly noise as a fraction
    ("api_p95_latency_ms", "API p95 latency", "ms", "lower", 420.0, 0.012),
    ("monthly_churn_pct", "Monthly churn", "%", "lower", 3.1, 0.015),
    ("weekly_signups", "Self-serve signups per week", "signups", "higher", 180.0, 0.02),
    ("invoice_delivery_rate", "Invoice delivery success", "%", "higher", 97.8, 0.001),
    ("support_tickets_weekly", "Support tickets per week", "tickets", "lower", 260.0, 0.02),
    ("eu_signups_weekly", "EU signups per week", "signups", "higher", 6.0, 0.05),
    ("infra_cost_usd_monthly", "Infrastructure cost per month", "USD", "lower", 48000.0, 0.01),
]


def build_readings(c: Company, effects: list[tuple[str, date, float]]) -> None:
    """Synthesize weekly readings: baseline, seeded noise, and planted step effects.

    Each effect is (metric, start_date, relative_change) and ramps in over two weeks.
    """
    weeks = []
    w = FIRST_WEEK
    while w <= AS_OF:
        weeks.append(w)
        w += timedelta(days=7)

    for mid, name, unit, better, base, noise in METRICS:
        for w in weeks:
            factor = 1.0
            for metric, start, change in effects:
                if metric != mid or w < week(start):
                    continue
                ramp = min(1.0, ((w - week(start)).days / 7 + 1) / 2)
                factor *= 1 + change * ramp
            value = base * factor * (1 + c.rng.gauss(0, noise))
            c.readings.append({"metric": mid, "at": w.isoformat(), "value": round(value, 3)})


# ---------------------------------------------------------------------------
# The planted company story
# ---------------------------------------------------------------------------

def build(seed: int = SEED) -> Company:
    c = Company(rng=random.Random(seed))
    team = build_people(c)

    def lead(t: str) -> str:
        return team[t][0]

    def member(t: str, k: int) -> str:
        return team[t][k % len(team[t])]

    # People who leave during the year. The EU metric owner leaving is planted.
    leave(c, member("growth", 3), "2026-05-29")
    leave(c, member("platform", 5), "2026-08-14")

    # --- Metrics and their owners ---------------------------------------------
    owners = {
        "api_p95_latency_ms": lead("platform"),
        "monthly_churn_pct": lead("growth"),
        "weekly_signups": member("growth", 1),
        "invoice_delivery_rate": lead("payments"),
        "support_tickets_weekly": member("payments", 2),
        "eu_signups_weekly": member("growth", 3),  # owner left on 2026-05-29
        "infra_cost_usd_monthly": None,  # planted: nobody owns this metric
    }
    for mid, name, unit, better, base, _ in METRICS:
        c.metrics.append({"id": mid, "name": name, "unit": unit, "better": better, "owner": owners[mid]})

    # --- Goals and initiatives ------------------------------------------------
    c.goals += [
        {"id": "G-1", "title": "Cut API p95 latency below 300 ms", "status": "active",
         "owner": lead("platform"), "metric": "api_p95_latency_ms", "cancelled_at": None},
        {"id": "G-2", "title": "Reduce monthly churn to 2%", "status": "active",
         "owner": lead("growth"), "metric": "monthly_churn_pct", "cancelled_at": None},
        {"id": "G-3", "title": "Grow self-serve signups by 40%", "status": "active",
         "owner": member("growth", 1), "metric": "weekly_signups", "cancelled_at": None},
        {"id": "G-4", "title": "Launch Kestrel in the EU", "status": "cancelled",
         "owner": member("growth", 3), "metric": "eu_signups_weekly", "cancelled_at": "2026-07-01"},
        {"id": "G-5", "title": "Reach 99.9% invoice delivery", "status": "active",
         "owner": lead("payments"), "metric": "invoice_delivery_rate", "cancelled_at": None},
    ]
    c.initiatives += [
        {"id": "I-1", "title": "Latency programme", "goal": "G-1", "status": "active"},
        {"id": "I-2", "title": "Retention programme", "goal": "G-2", "status": "active"},
        {"id": "I-3", "title": "Growth loops", "goal": "G-3", "status": "active"},
        {"id": "I-4", "title": "EU expansion", "goal": "G-4", "status": "active"},  # goal cancelled, still active
        {"id": "I-5", "title": "Delivery reliability", "goal": "G-5", "status": "active"},
    ]

    # --- Capabilities and projects -------------------------------------------
    c.capabilities += [
        {"id": "CAP-retry", "name": "Retry and backoff for outbound calls"},
        {"id": "CAP-cache", "name": "Read caching"},
        {"id": "CAP-reporting", "name": "Revenue reporting"},
        {"id": "CAP-theme", "name": "UI theming"},
    ]
    projects = [
        # id, title, team, initiative, status, capability
        ("PR-1", "Invoice read cache", "platform", "I-1", "done", "CAP-cache"),
        ("PR-2", "Async PDF rendering", "platform", "I-1", "done", None),
        ("PR-3", "Precomputed dashboard aggregates", "data", "I-1", "active", "CAP-cache"),
        ("PR-4", "Annual plan pricing", "growth", "I-2", "done", None),
        ("PR-5", "Guided onboarding checklist", "mobile", "I-2", "done", None),
        ("PR-6", "Signup flow redesign", "growth", "I-3", "done", None),
        ("PR-7", "Referral programme", "growth", "I-3", "active", None),
        ("PR-8", "EU data residency", "platform", "I-4", "active", None),
        ("PR-9", "Database connection pool tuning", "platform", "I-1", "done", None),
        ("PR-10", "Email delivery provider switch", "payments", "I-5", "done", None),
        ("PR-11", "Admin panel theme refresh", "mobile", None, "active", "CAP-theme"),  # orphan
        ("PR-12", "Customer profile cache", "payments", "I-1", "done", "CAP-cache"),
        ("PR-13", "Webhook retry library", "payments", "I-5", "active", "CAP-retry"),  # duplicate pair
        ("PR-14", "Shared retry and backoff toolkit", "platform", "I-1", "active", "CAP-retry"),
        ("PR-15", "Revenue dashboard v2", "data", None, "active", "CAP-reporting"),  # orphan
        ("PR-16", "Invoice renderer rewrite in Go", "platform", "I-1", "active", None),
    ]
    for pid, title, t, ini, status, cap in projects:
        c.projects.append({"id": pid, "title": title, "team": t, "initiative": ini,
                           "status": status, "capability": cap, "owner": lead(t)})

    # --- Assumptions ---------------------------------------------------------
    c.assumptions += [
        {"id": "A-1", "statement": "Invoice API traffic is read-heavy, so caching reads cuts latency"},
        {"id": "A-2", "statement": "PDF rendering is the largest share of API latency"},
        {"id": "A-3", "statement": "Most failed invoice deliveries are bounces caused by the email provider"},
        {"id": "A-4", "statement": "Price is the main reason customers churn"},
        {"id": "A-5", "statement": "Customers churn because they never finish account setup"},
        {"id": "A-6", "statement": "A shorter signup form converts more visitors"},
        {"id": "A-7", "statement": "EU customers require data stored in the EU"},
        {"id": "A-8", "statement": "Most support tickets come from failed webhook deliveries"},
        {"id": "A-9", "statement": "Connection pool exhaustion causes the latency spikes"},
    ]
    contradictions = {
        # A-1 is disproved after the first cache shipped and before the second was decided.
        "A-1": ("2026-03-16", member("data", 1), "slack", "#data-insights: invoice API traffic mix",
                "Ran the numbers on invoice API traffic since the bulk import launch. Writes are now 58% "
                "of requests, so it is no longer read-heavy. Caching reads will not move p95 much."),
        "A-4": ("2026-09-21", member("growth", 2), "doc", "Churn exit survey, Q3 summary",
                "Exit survey, 212 responses. Price is the top reason for only 14% of churned accounts; "
                "missing integrations (41%) and setup friction (27%) rank higher."),
    }
    supports = {
        "A-2": ("2025-11-17", member("platform", 2), "doc", "Latency profile of the invoice API",
                "Profiling shows synchronous PDF rendering accounts for 61% of p95 latency."),
        "A-3": ("2026-04-20", member("payments", 1), "slack", "#payments: delivery failure breakdown",
                "Of last month's failed deliveries, 83% are provider-side bounces."),
    }
    for aid, (at, author, kind, title, text) in contradictions.items():
        sid = c.source(kind, title, d(at), author, text)
        a = next(x for x in c.assumptions if x["id"] == aid)
        a.update({"contradicted_by": sid, "contradicted_at": at})
    for aid, (at, author, kind, title, text) in supports.items():
        sid = c.source(kind, title, d(at), author, text)
        a = next(x for x in c.assumptions if x["id"] == aid)
        a.update({"supported_by": sid})

    # --- Decisions -------------------------------------------------------------
    # expects: (metric, relative change, judge-by date). effect: hidden true change.
    decisions = [
        dict(id="D-101", title="Cache invoice reads in Redis", team="platform", project="PR-1",
             decided="2026-02-02", shipped="2026-02-23", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.30, "2026-04-20"), effect=-0.03),
        dict(id="D-102", title="Cache customer profile lookups", team="payments", project="PR-12",
             decided="2026-04-06", shipped="2026-04-27", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.20, "2026-06-22"), effect=-0.02),
        dict(id="D-103", title="Precompute dashboard aggregates", team="data", project="PR-3",
             decided="2026-09-14", shipped="2026-10-05", assumptions=["A-1"],
             expects=("api_p95_latency_ms", -0.15, "2026-12-07"), effect=-0.01),
        dict(id="D-104", title="Move PDF rendering to an async queue", team="platform", project="PR-2",
             decided="2025-12-01", shipped="2025-12-22", assumptions=["A-2"],
             expects=("api_p95_latency_ms", -0.25, "2026-01-26"), effect=-0.28),
        dict(id="D-105", title="Switch email delivery provider", team="payments", project="PR-10",
             decided="2026-05-04", shipped="2026-05-25", assumptions=["A-3"],
             expects=("invoice_delivery_rate", 0.015, "2026-07-20"), effect=0.016),
        dict(id="D-106", title="Offer a 20% annual plan discount", team="growth", project="PR-4",
             decided="2026-06-01", shipped="2026-06-15", assumptions=["A-4"],
             expects=("monthly_churn_pct", -0.15, "2026-08-31"), effect=-0.06),
        dict(id="D-107", title="Launch a guided onboarding checklist", team="mobile", project="PR-5",
             decided="2026-06-08", shipped="2026-06-29", assumptions=["A-5"],
             expects=("monthly_churn_pct", -0.10, "2026-09-07"), effect=-0.14),
        dict(id="D-108", title="Ship dark mode in the mobile app", team="mobile", project=None,
             decided="2026-03-02", shipped="2026-04-06", assumptions=[], expects=None, effect=None),
        dict(id="D-109", title="Launch a referral programme", team="growth", project="PR-7",
             decided="2026-09-07", shipped="2026-09-28", assumptions=[],
             expects=("weekly_signups", 0.15, "2026-12-14"), effect=0.03),
        dict(id="D-110", title="Rewrite the invoice renderer in Go", team="platform", project="PR-16",
             decided="2026-07-06", shipped=None, assumptions=["A-2"], expects=None, effect=None),
        dict(id="D-111", title="Store EU customer data in Frankfurt", team="platform", project="PR-8",
             decided="2026-01-12", shipped=None, assumptions=["A-7"],
             expects=("eu_signups_weekly", 1.0, "2026-09-28"), effect=None),
        dict(id="D-112", title="Retry failed webhooks with exponential backoff", team="payments",
             project="PR-13", decided="2026-03-09", shipped="2026-03-30", assumptions=["A-8"],
             expects=("support_tickets_weekly", -0.20, "2026-05-25"), effect=-0.22),
        dict(id="D-113", title="Tune database connection pools", team="platform", project="PR-9",
             decided="2026-07-27", shipped="2026-08-10", assumptions=["A-9"],
             expects=("api_p95_latency_ms", -0.10, "2026-09-21"), effect=-0.08),
        dict(id="D-114", title="Shorten the signup form to three fields", team="growth", project="PR-6",
             decided="2026-01-19", shipped="2026-02-09", assumptions=["A-6"],
             expects=("weekly_signups", 0.20, "2026-04-06"), effect=0.09),
        dict(id="D-115", title="Send a weekly invoice digest email", team="payments", project=None,
             decided="2025-11-03", shipped="2025-11-17", assumptions=[], expects=None, effect=None,
             status="superseded"),
        dict(id="D-116", title="Make the invoice digest daily and opt-in", team="payments", project=None,
             decided="2026-02-16", shipped="2026-03-02", assumptions=[], expects=None, effect=None,
             supersedes="D-115"),
    ]

    effects: list[tuple[str, date, float]] = []
    for spec in decisions:
        decider = lead(spec["team"])
        doc_text = f"Decision: {spec['title']}."
        if spec["assumptions"]:
            reasons = "; ".join(next(a["statement"] for a in c.assumptions if a["id"] == aid)
                                for aid in spec["assumptions"])
            doc_text += f" We are doing this because: {reasons}."
        if spec["expects"]:
            metric, change, by = spec["expects"]
            mname = next(m[1] for m in METRICS if m[0] == metric)
            doc_text += (f" Success means {mname} changes by {change:+.0%} from its pre-launch level"
                         f" by {by}.")
        else:
            doc_text += " No success metric was set."
        doc = c.source("doc", f"Decision record {spec['id']}: {spec['title']}", d(spec["decided"]),
                       decider, doc_text, mentions=[spec["project"]] if spec["project"] else [])
        thread = c.source("slack", f"#{spec['team']}: {spec['title'].lower()}",
                          d(spec["decided"]) - timedelta(days=4), member(spec["team"], 1),
                          f"Proposal thread for '{spec['title']}'. Agreed to go ahead; decision record to follow.",
                          mentions=[spec["project"]] if spec["project"] else [])
        c.decisions.append({
            "id": spec["id"], "title": spec["title"], "team": spec["team"], "decider": decider,
            "project": spec["project"], "decided_at": spec["decided"], "shipped_at": spec["shipped"],
            "status": spec.get("status", "active"), "supersedes": spec.get("supersedes"),
            "assumptions": spec["assumptions"], "decided_in": doc, "discussed_in": thread,
            "expects": None if not spec["expects"] else {
                "metric": spec["expects"][0], "change": spec["expects"][1],
                "judge_by": spec["expects"][2], "source": doc},
        })
        if spec["effect"] is not None and spec["shipped"]:
            effects.append((spec["expects"][0], d(spec["shipped"]), spec["effect"]))

    # Planted confounder: an incident lifts latency right after the pool tuning shipped.
    inc_src = c.source("doc", "Incident INC-42: degraded storage volume", d("2026-08-17"),
                       member("platform", 3),
                       "A degraded storage volume raised API latency for three weeks until it was replaced.")
    c.incidents.append({"id": "INC-42", "title": "Degraded storage volume", "at": "2026-08-17",
                        "until": "2026-09-07", "metric": "api_p95_latency_ms", "source": inc_src})
    effects.append(("api_p95_latency_ms", d("2026-08-17"), 0.09))
    effects.append(("api_p95_latency_ms", d("2026-09-07"), -0.083))

    # Background drift so readings are never perfectly flat.
    effects.append(("infra_cost_usd_monthly", d("2026-03-02"), 0.12))
    effects.append(("weekly_signups", d("2026-05-04"), 0.04))

    build_readings(c, effects)
    build_tasks(c, team)
    return c


def build_tasks(c: Company, team: dict[str, list[str]]) -> None:
    """Tickets and pull requests under each project, plus a few with no project at all."""
    verbs = ["Implement", "Add tests for", "Instrument", "Document", "Fix edge case in", "Review", "Roll out"]
    seq = 0
    cancelled_eu = d("2026-07-01")
    for p in c.projects:
        decision = next((x for x in c.decisions if x["project"] == p["id"]), None)
        start = d(decision["decided_at"]) if decision else d("2026-05-04")
        n = c.rng.randint(5, 9)
        for k in range(n):
            seq += 1
            created = start + timedelta(days=c.rng.randint(0, 40))
            if p["id"] == "PR-8":
                # Planted: EU work keeps going after the EU goal was cancelled.
                created = cancelled_eu + timedelta(days=c.rng.randint(5, 90))
            closed = created + timedelta(days=c.rng.randint(2, 15))
            open_task = p["status"] == "active" and k >= n - 3
            c.tasks.append({
                "id": f"T-{seq:03d}", "kind": c.rng.choice(["ticket", "pr"]),
                "title": f"{c.rng.choice(verbs)} {p['title'].lower()}",
                "project": p["id"], "assignee": c.rng.choice(team[p["team"]]),
                "created_at": created.isoformat(),
                "closed_at": None if open_task or closed > AS_OF else closed.isoformat(),
            })
    # Planted: work tracked against no project at all.
    for title, t in [("Migrate CI runners to ARM", "platform"), ("Rebuild the internal status page", "data"),
                     ("Prototype a Slack bot for invoice lookups", "growth")]:
        seq += 1
        created = d("2026-08-03") + timedelta(days=c.rng.randint(0, 50))
        c.tasks.append({"id": f"T-{seq:03d}", "kind": "ticket", "title": title, "project": None,
                        "assignee": c.rng.choice(team[t]), "created_at": created.isoformat(),
                        "closed_at": None})


def to_json(c: Company) -> dict:
    return {
        "company": "Kestrel", "as_of": AS_OF.isoformat(), "seed": SEED,
        "teams": c.teams, "people": c.people, "memberships": c.memberships,
        "goals": c.goals, "initiatives": c.initiatives, "projects": c.projects,
        "capabilities": c.capabilities, "tasks": c.tasks, "metrics": c.metrics,
        "readings": c.readings, "incidents": c.incidents, "assumptions": c.assumptions,
        "decisions": c.decisions, "sources": c.sources,
    }


def main(out: str = "data/kestrel.json") -> None:
    company = build()
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_json(company), indent=1), encoding="utf-8")
    print(f"Wrote {path}: {len(company.decisions)} decisions, {len(company.tasks)} tasks, "
          f"{len(company.readings)} readings, {len(company.sources)} sources")


if __name__ == "__main__":
    main()
