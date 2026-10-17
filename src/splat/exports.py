"""Company data as the files real tools export, and the reader that imports them.

    org/              teams.csv, people.csv, memberships.csv   (HR / org chart)
    planning/         goals.csv, initiatives.csv, epics.csv, capabilities.csv
    jira/issues.csv   tickets, keyed KES-123
    github/pull_requests.csv
    metrics/          metrics.csv, readings.csv                 (dashboard export)
    slack/            users.json, channels.json, <channel>/<date>.json  (Slack export format)
    docs/decisions/   one Markdown decision record per decision, with front matter
    docs/notes/       research notes and analyses
    incidents/        one Markdown incident report each
    assumptions/register.csv

The importer reads only these files, so a real company could point it at its own
exports. Every source keeps a stable id derived from where it lives, for example
docs/decisions/D-101.md or slack/platform/2026-01-27/1, so answers cite a real file.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Small file helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows([["" if v is None else v for v in r] for r in rows])


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]


def _write_markdown(path: Path, meta: dict, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(value) + "]"
        lines.append(f"{key}: {value}")
    lines += ["---", "", f"# {title}", "", body.strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_markdown(path: Path) -> tuple[dict, str, str]:
    text = path.read_text(encoding="utf-8")
    _, front, rest = text.split("---", 2)
    meta = {}
    for line in front.strip().splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value or None
    body = rest.strip()
    title = body.splitlines()[0].lstrip("# ").strip()
    return meta, title, "\n".join(body.splitlines()[1:]).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _epoch(day: str, minutes: int) -> str:
    dt = datetime.fromisoformat(day).replace(hour=9, tzinfo=timezone.utc)
    return f"{dt.timestamp() + minutes * 60:.6f}"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write(company: dict, root: str | Path) -> Path:
    """Write the generator's in-memory company as tool exports under root."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "export.json").write_text(json.dumps(
        {"company": company["company"], "as_of": company["as_of"]}, indent=1), encoding="utf-8")
    email = {p["id"]: p["email"] for p in company["people"]}
    names = {p["id"]: p["name"] for p in company["people"]}

    # Organisation and planning.
    _write_csv(root / "org/teams.csv", ["id", "name"], [[t["id"], t["name"]] for t in company["teams"]])
    _write_csv(root / "org/people.csv", ["email", "name"], [[p["email"], p["name"]] for p in company["people"]])
    _write_csv(root / "org/memberships.csv", ["email", "team", "since", "until"],
               [[email[m["person"]], m["team"], m["since"], m["until"]] for m in company["memberships"]])
    _write_csv(root / "planning/goals.csv", ["id", "title", "status", "owner", "metric", "cancelled_at"],
               [[g["id"], g["title"], g["status"], email[g["owner"]], g["metric"], g["cancelled_at"]]
                for g in company["goals"]])
    _write_csv(root / "planning/initiatives.csv", ["id", "title", "goal", "status"],
               [[i["id"], i["title"], i["goal"], i["status"]] for i in company["initiatives"]])
    _write_csv(root / "planning/epics.csv", ["id", "title", "team", "initiative", "status", "capability"],
               [[p["id"], p["title"], p["team"], p["initiative"], p["status"], p["capability"]]
                for p in company["projects"]])
    _write_csv(root / "planning/capabilities.csv", ["id", "name"],
               [[k["id"], k["name"]] for k in company["capabilities"]])

    # Tickets live in Jira, pull requests in GitHub.
    jira, github = [], []
    for n, t in enumerate(company["tasks"]):
        row = [t["title"], t["project"], email[t["assignee"]], t["created_at"], t["closed_at"]]
        if t["kind"] == "pr":
            github.append([f"GH-{400 + n}", *row])
        else:
            jira.append([f"KES-{100 + n}", "Bug" if "fix" in t["title"].lower() else "Task", *row])
    _write_csv(root / "jira/issues.csv", ["key", "type", "summary", "epic", "assignee", "created", "resolved"], jira)
    _write_csv(root / "github/pull_requests.csv", ["id", "title", "epic", "author", "created", "merged"], github)

    # Dashboards.
    _write_csv(root / "metrics/metrics.csv", ["id", "name", "unit", "better", "owner"],
               [[m["id"], m["name"], m["unit"], m["better"], m["owner"] and email[m["owner"]]]
                for m in company["metrics"]])
    _write_csv(root / "metrics/readings.csv", ["metric", "week", "value"],
               [[r["metric"], r["at"], r["value"]] for r in company["readings"]])

    # Slack, in Slack's export format. Each thread is a parent message with replies.
    exported_id: dict[str, str] = {}
    users = {pid: f"U{n + 1:03d}" for n, pid in enumerate(email)}
    by_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for s in sorted((s for s in company["sources"] if s["kind"] == "slack"), key=lambda s: (s["at"], s["id"])):
        bucket = by_day[(s["channel"], s["at"])]
        position = len({m["thread_ts"] for m in bucket if "thread_ts" in m}) + 1
        exported_id[s["id"]] = f"slack/{s['channel']}/{s['at']}/{position}"
        parent_ts = _epoch(s["at"], 10 * position)
        for k, (who, text) in enumerate(s["messages"]):
            msg = {"type": "message", "user": users[who], "user_name": names[who], "text": text,
                   "ts": parent_ts if k == 0 else _epoch(s["at"], 10 * position + k), "thread_ts": parent_ts}
            if k == 0:
                msg["reply_count"] = len(s["messages"]) - 1
            bucket.append(msg)
    channels = sorted({ch for ch, _ in by_day})
    for (channel, day), messages in by_day.items():
        path = root / "slack" / channel / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(messages, indent=1), encoding="utf-8")
    (root / "slack/channels.json").write_text(json.dumps(
        [{"id": f"C{n + 1:03d}", "name": ch} for n, ch in enumerate(channels)], indent=1), encoding="utf-8")
    (root / "slack/users.json").write_text(json.dumps(
        [{"id": users[pid], "name": email[pid].split("@")[0], "real_name": names[pid],
          "profile": {"email": email[pid], "real_name": names[pid]}} for pid in users], indent=1), encoding="utf-8")

    # Documents: decision records, the incident report, research notes.
    decision_doc = {x["decided_in"]: x for x in company["decisions"]}
    incident_doc = {i["source"]: i for i in company["incidents"]}
    for s in company["sources"]:
        if s["kind"] != "doc":
            continue
        if s["id"] in decision_doc:
            exported_id[s["id"]] = f"docs/decisions/{decision_doc[s['id']]['id']}.md"
        elif s["id"] in incident_doc:
            exported_id[s["id"]] = f"incidents/{incident_doc[s['id']]['id']}.md"
        else:
            exported_id[s["id"]] = f"docs/notes/{s['at']}-{_slug(s['title'])}.md"

    for x in company["decisions"]:
        e = x["expects"] or {}
        doc = next(s for s in company["sources"] if s["id"] == x["decided_in"])
        _write_markdown(root / exported_id[x["decided_in"]], {
            "id": x["id"], "team": x["team"], "decider": email[x["decider"]], "decided": x["decided_at"],
            "shipped": x["shipped_at"], "status": x["status"], "epic": x["project"],
            "supersedes": x["supersedes"], "assumptions": x["assumptions"] or None,
            "expects_metric": e.get("metric"), "expects_change": e.get("change"), "judge_by": e.get("judge_by"),
            "discussed_in": exported_id[x["discussed_in"]],
        }, x["title"], doc["text"])
    for i in company["incidents"]:
        doc = next(s for s in company["sources"] if s["id"] == i["source"])
        _write_markdown(root / exported_id[i["source"]], {
            "id": i["id"], "author": email[doc["author"]], "started": i["at"], "resolved": i["until"],
            "metric": i["metric"]}, i["title"], doc["text"])
    for s in company["sources"]:
        if s["kind"] == "doc" and exported_id[s["id"]].startswith("docs/notes/"):
            _write_markdown(root / exported_id[s["id"]], {"author": email[s["author"]], "date": s["at"]},
                            s["title"], s["text"])

    # The assumption register links each assumption to the evidence for or against it.
    register = []
    for a in company["assumptions"]:
        if a.get("contradicted_by"):
            register.append([a["id"], a["statement"], "disproved", exported_id[a["contradicted_by"]],
                             a["contradicted_at"]])
        elif a.get("supported_by"):
            register.append([a["id"], a["statement"], "supported", exported_id[a["supported_by"]], None])
        else:
            register.append([a["id"], a["statement"], "open", None, None])
    _write_csv(root / "assumptions/register.csv", ["id", "statement", "status", "evidence", "changed_on"], register)
    return root


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def read(root: str | Path, name: str = "Kestrel", as_of: str | None = None) -> dict:
    """Import tool exports into the structure the graph loader expects."""
    root = Path(root)
    meta = json.loads((root / "export.json").read_text(encoding="utf-8")) if (root / "export.json").exists() else {}
    epics = _read_csv(root / "planning/epics.csv")
    epic_titles = [(e["id"], e["title"].lower()) for e in epics]

    def mentions(text: str) -> list[str]:
        low = text.lower()
        return [eid for eid, title in epic_titles if title in low]

    sources: list[dict] = []

    # Slack threads.
    users = {u["id"]: u["profile"]["email"] for u in json.loads((root / "slack/users.json").read_text("utf-8"))}
    user_names = {u["id"]: u["real_name"] for u in json.loads((root / "slack/users.json").read_text("utf-8"))}
    for day_file in sorted((root / "slack").glob("*/*.json")):
        channel, day = day_file.parent.name, day_file.stem
        threads: dict[str, list[dict]] = defaultdict(list)
        for msg in json.loads(day_file.read_text("utf-8")):
            threads[msg.get("thread_ts", msg["ts"])].append(msg)
        for position, ts in enumerate(sorted(threads), start=1):
            msgs = sorted(threads[ts], key=lambda m: float(m["ts"]))
            text = "\n".join(f"{user_names[m['user']].split()[0]}: {m['text']}" for m in msgs)
            sources.append({"id": f"slack/{channel}/{day}/{position}", "kind": "slack",
                            "title": f"#{channel}: {msgs[0]['text'][:60]}", "at": day,
                            "author": users[msgs[0]["user"]], "text": text, "mentions": mentions(text)})

    # Markdown documents.
    decisions, incidents = [], []
    for path in sorted(root.glob("docs/decisions/*.md")):
        m, title, body = _read_markdown(path)
        sid = path.relative_to(root).as_posix()
        sources.append({"id": sid, "kind": "doc", "title": f"Decision record {m['id']}: {title}",
                        "at": m["decided"], "author": m["decider"], "text": body, "mentions": mentions(body)})
        decisions.append({
            "id": m["id"], "title": title, "team": m["team"], "decider": m["decider"], "project": m.get("epic"),
            "decided_at": m["decided"], "shipped_at": m.get("shipped"), "status": m.get("status") or "active",
            "supersedes": m.get("supersedes"), "assumptions": m.get("assumptions") or [],
            "decided_in": sid, "discussed_in": m["discussed_in"],
            "expects": None if not m.get("expects_metric") else {
                "metric": m["expects_metric"], "change": float(m["expects_change"]),
                "judge_by": m["judge_by"], "source": sid},
        })
    for path in sorted(root.glob("incidents/*.md")):
        m, title, body = _read_markdown(path)
        sid = path.relative_to(root).as_posix()
        sources.append({"id": sid, "kind": "doc", "title": f"Incident {m['id']}: {title}", "at": m["started"],
                        "author": m["author"], "text": body, "mentions": mentions(body)})
        incidents.append({"id": m["id"], "title": title, "at": m["started"], "until": m["resolved"],
                          "metric": m["metric"], "source": sid})
    for path in sorted(root.glob("docs/notes/*.md")):
        m, title, body = _read_markdown(path)
        sources.append({"id": path.relative_to(root).as_posix(), "kind": "doc", "title": title, "at": m["date"],
                        "author": m["author"], "text": body, "mentions": mentions(body)})

    assumptions = []
    for a in _read_csv(root / "assumptions/register.csv"):
        row = {"id": a["id"], "statement": a["statement"]}
        if a["status"] == "disproved":
            row.update({"contradicted_by": a["evidence"], "contradicted_at": a["changed_on"]})
        elif a["status"] == "supported":
            row["supported_by"] = a["evidence"]
        assumptions.append(row)

    tasks = [{"id": t["key"], "kind": "ticket", "title": t["summary"], "project": t["epic"],
              "assignee": t["assignee"], "created_at": t["created"], "closed_at": t["resolved"]}
             for t in _read_csv(root / "jira/issues.csv")]
    tasks += [{"id": t["id"], "kind": "pr", "title": t["title"], "project": t["epic"],
               "assignee": t["author"], "created_at": t["created"], "closed_at": t["merged"]}
              for t in _read_csv(root / "github/pull_requests.csv")]

    return {
        "company": meta.get("company", name), "as_of": meta.get("as_of", as_of),
        "teams": _read_csv(root / "org/teams.csv"),
        "people": [{"id": p["email"], "name": p["name"], "email": p["email"]} for p in _read_csv(root / "org/people.csv")],
        "memberships": [{"person": m["email"], "team": m["team"], "since": m["since"], "until": m["until"]}
                        for m in _read_csv(root / "org/memberships.csv")],
        "goals": _read_csv(root / "planning/goals.csv"),
        "initiatives": _read_csv(root / "planning/initiatives.csv"),
        "projects": [{**e, "owner": None} for e in epics],
        "capabilities": _read_csv(root / "planning/capabilities.csv"),
        "tasks": tasks,
        "metrics": _read_csv(root / "metrics/metrics.csv"),
        "readings": [{"metric": r["metric"], "at": r["week"], "value": float(r["value"])}
                     for r in _read_csv(root / "metrics/readings.csv")],
        "incidents": incidents, "assumptions": assumptions, "decisions": decisions, "sources": sources,
    }
