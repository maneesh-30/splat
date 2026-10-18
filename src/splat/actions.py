"""Actions: the agent proposes one fix per finding, tries it on a sandbox copy of
the graph, and changes the real graph only after a person approves it. After
applying, it re-runs the check that found the problem to verify it is gone.

Every action is a fixed, reviewed write query. The language model never writes
to the graph.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from falkordb import FalkorDB

from splat.db import Settings, copy_graph
from splat.tools import Tools


@dataclass
class Action:
    id: str
    kind: str
    title: str
    why: str
    target: str
    cypher: str
    params: dict


# --- checks: does the problem an action addresses still exist? -------------------


def check(tools: Tools, action: Action) -> dict:
    if action.kind == "review_decision":
        live = [d for a in tools.cascade() for d in a["decisions"] if d["id"] == action.target and d["still_live"]]
        return {"problem": bool(live),
                "detail": f"{action.target} is still live on a disproved assumption" if live
                else f"{action.target} is no longer live on a disproved assumption"}
    if action.kind == "pause_epic":
        under = [x for x in tools.cancelled_goal_work() if x["project"]["id"] == action.target]
        return {"problem": bool(under),
                "detail": f"{action.target} is still active under a cancelled goal" if under
                else f"{action.target} is no longer active under a cancelled goal"}
    if action.kind == "assign_owner":
        unowned = [m for m in tools.unowned_metrics() if m["id"] == action.target]
        return {"problem": bool(unowned),
                "detail": f"{action.target} has no current owner" if unowned
                else f"{action.target} has an owner"}
    if action.kind == "connect_teams":
        a, b = action.target.split("+")
        pair = [x for x in tools.duplicate_work() if {p["id"] for p in x["projects"]} == {a, b}]
        unaware = bool(pair) and not (pair[0]["linked"] or pair[0]["same_island"])
        return {"problem": unaware,
                "detail": f"{a} and {b} share no person, thread or conversation island" if unaware
                else f"{a} and {b} are now linked through a shared thread and conversation"}
    raise ValueError(f"unknown action kind {action.kind}")


# --- proposals, built from what the graph currently shows ------------------------


def propose(tools: Tools) -> list[Action]:
    as_of = tools.as_of.isoformat()
    actions: list[Action] = []

    for a in tools.cascade():
        for d in a["decisions"]:
            if d["still_live"]:
                actions.append(Action(
                    id=f"review-{d['id']}", kind="review_decision", target=d["id"],
                    title=f"Open a review of {d['id']} {d['title']}",
                    why=f"It rests on {a['id']} (\"{a['statement']}\"), disproved on {a['contradicted_at']} "
                        f"by {a['evidence']['id']}, and it is still live.",
                    cypher="""
MATCH (d:Decision {id: $decision}), (s:Source {id: $evidence})
OPTIONAL MATCH (owner:Person)-[:DECIDED]->(d)
MERGE (r:Review {id: $review})
SET r.title = $title, r.reason = $reason, r.status = 'open', r.created_at = $as_of
MERGE (r)-[:REVIEWS]->(d)
MERGE (r)-[:CITES]->(s)
FOREACH (o IN CASE WHEN owner IS NULL THEN [] ELSE [owner] END | MERGE (r)-[:ASSIGNED_TO]->(o))
SET d.status = 'under_review'
""",
                    params={"decision": d["id"], "evidence": a["evidence"]["id"], "review": f"REV-{d['id']}",
                            "title": f"Revisit {d['id']}: its assumption {a['id']} was disproved",
                            "reason": f"{a['id']} disproved on {a['contradicted_at']}", "as_of": as_of}))

    for x in tools.cancelled_goal_work():
        if x["project"]["status"] == "active":
            actions.append(Action(
                id=f"pause-{x['project']['id']}", kind="pause_epic", target=x["project"]["id"],
                title=f"Pause {x['project']['id']} {x['project']['title']}",
                why=f"Its goal {x['goal']['id']} \"{x['goal']['title']}\" was cancelled on "
                    f"{x['goal']['cancelled_at']}, yet it gained {x['tasks_after_cancel']} tickets since.",
                cypher="""
MATCH (p:Project {id: $epic})-[:PART_OF]->(i:Initiative)
SET p.status = 'paused', p.paused_at = $as_of, i.status = 'closed'
""",
                params={"epic": x["project"]["id"], "as_of": as_of}))

    for m in tools.unowned_metrics():
        owner = tools.suggest_metric_owner(m["id"])
        if owner:
            actions.append(Action(
                id=f"own-{m['id']}", kind="assign_owner", target=m["id"],
                title=f"Make {owner['name']} the owner of \"{m['name']}\"",
                why=(f"Its owner {m['former_owner']} left on {m['owner_left_on']}." if m["former_owner"]
                     else "Nobody has ever owned it.")
                    + f" {owner['name']} {owner['reason']}.",
                cypher="""
MATCH (p:Person {id: $person}), (m:Metric {id: $metric})
MERGE (p)-[r:OWNS]->(m)
SET r.since = $as_of
""",
                params={"person": owner["id"], "metric": m["id"], "as_of": as_of}))

    for x in tools.duplicate_work():
        if not (x["linked"] or x["same_island"]):
            a, b = x["projects"]
            owners = tools.project_owners([a["id"], b["id"]])
            if len(owners) < 2:
                continue
            note_id = f"actions/introduce-{a['id']}-{b['id']}"
            actions.append(Action(
                id=f"connect-{a['id']}-{b['id']}", kind="connect_teams", target=f"{a['id']}+{b['id']}",
                title=f"Introduce {owners[0]['name']} ({a['team']}) and {owners[1]['name']} ({b['team']})",
                why=f"Both teams are building \"{x['capability']}\" ({a['id']} and {b['id']}) with no shared "
                    "person, thread or chain of conversation between them.",
                cypher="""
MATCH (pa:Project {id: $a}), (pb:Project {id: $b}), (oa:Person {id: $owner_a}), (ob:Person {id: $owner_b})
MERGE (s:Source {id: $note})
SET s.kind = 'slack', s.title = $title, s.at = $as_of, s.text = $text
MERGE (s)-[:MENTIONS]->(pa)
MERGE (s)-[:MENTIONS]->(pb)
MERGE (oa)-[:AUTHORED]->(s)
MERGE (oa)-[:POSTED_IN]->(s)
MERGE (ob)-[:POSTED_IN]->(s)
MERGE (oa)-[:TALKS_WITH]->(ob)
MERGE (ob)-[:TALKS_WITH]->(oa)
""",
                params={"a": a["id"], "b": b["id"], "owner_a": owners[0]["id"], "owner_b": owners[1]["id"],
                        "note": note_id, "as_of": as_of,
                        "title": f"Introduction: {a['title']} and {b['title']}",
                        "text": f"{owners[0]['name'].split()[0]}: Hi {owners[1]['name'].split()[0]}, our teams are "
                                f"both building {x['capability'].lower()} ({a['title']} and {b['title']}). "
                                "Worth comparing notes before we go further?"}))
    return actions


# --- dry run and apply --------------------------------------------------------------


def find(tools: Tools, action_id: str) -> Action | None:
    return next((a for a in propose(tools) if a.id == action_id), None)


def dry_run(db: FalkorDB, settings: Settings, action: Action) -> dict:
    """Apply the action to a throwaway copy of the graph and compare the check."""
    live = db.select_graph(settings.graph)
    before = check(Tools(live), action)
    sandbox_name = f"{settings.graph}__sandbox__{action.id}"
    if sandbox_name in db.list_graphs():
        db.select_graph(sandbox_name).delete()
    start = time.perf_counter()
    copy_graph(db, settings.graph, sandbox_name)
    copy_ms = round((time.perf_counter() - start) * 1000, 2)
    sandbox = db.select_graph(sandbox_name)
    try:
        stats = sandbox.query(action.cypher, action.params)
        after = check(Tools(sandbox), action)
    finally:
        sandbox.delete()
    return {"action": asdict(action), "before": before, "after": after, "copy_ms": copy_ms,
            "sandbox": sandbox_name, "writes": _stats(stats)}


def apply(db: FalkorDB, settings: Settings, action: Action) -> dict:
    """Apply an approved action to the real graph, record it, and verify the result."""
    live = db.select_graph(settings.graph)
    before = check(Tools(live), action)
    stats = live.query(action.cypher, action.params)
    live.query(
        "MERGE (x:Action {id: $id}) SET x.kind = $kind, x.title = $title, x.why = $why, "
        "x.applied_at = $at, x.approved = true",
        {"id": action.id, "kind": action.kind, "title": action.title, "why": action.why,
         "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    db.connection.save()
    after = check(Tools(live), action)
    return {"action": asdict(action), "before": before, "after": after, "writes": _stats(stats),
            "verified": before["problem"] and not after["problem"]}


def _stats(result) -> dict:
    return {"nodes_created": result.nodes_created, "relationships_created": result.relationships_created,
            "properties_set": result.properties_set}
