"""Load a company JSON export into FalkorDB.

Every write is an idempotent UNWIND ... MERGE in batches. The data is loaded into a
staging graph first and only copied over the live graph once the import finished,
so the product never reads a half-imported graph.
"""

from __future__ import annotations

import sys

from falkordb.graph import Graph

from splat import exports, schema
from splat.db import Settings, connect

BATCH = 300


def _run(graph: Graph, query: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), BATCH):
        graph.query(query, {"rows": rows[i:i + BATCH]})


NODES = {
    "Team": ("teams", "SET n.name = row.name"),
    "Person": ("people", "SET n.name = row.name, n.email = row.email"),
    "Goal": ("goals", "SET n.title = row.title, n.status = row.status, n.cancelled_at = row.cancelled_at"),
    "Initiative": ("initiatives", "SET n.title = row.title, n.status = row.status"),
    "Project": ("projects", "SET n.title = row.title, n.status = row.status"),
    "Capability": ("capabilities", "SET n.name = row.name"),
    "Task": ("tasks", "SET n.kind = row.kind, n.title = row.title, n.created_at = row.created_at, "
                      "n.closed_at = row.closed_at"),
    "Metric": ("metrics", "SET n.name = row.name, n.unit = row.unit, n.better = row.better"),
    "Incident": ("incidents", "SET n.title = row.title, n.at = row.at, n.until = row.until"),
    "Assumption": ("assumptions", "SET n.statement = row.statement"),
    "Decision": ("decisions", "SET n.title = row.title, n.decided_at = row.decided_at, "
                              "n.shipped_at = row.shipped_at, n.status = row.status"),
    "Source": ("sources", "SET n.kind = row.kind, n.title = row.title, n.at = row.at, n.text = row.text"),
}

# (query, row builder). Each builder turns the export into the rows its query expects.
EDGES = [
    ("MATCH (p:Person {id: row.person}), (t:Team {id: row.team}) "
     "MERGE (p)-[r:MEMBER_OF]->(t) SET r.since = row.since, r.until = row.until",
     lambda c: c["memberships"]),
    ("MATCH (p:Person {id: row.owner}), (g:Goal {id: row.id}) MERGE (p)-[:OWNS]->(g)",
     lambda c: c["goals"]),
    ("MATCH (g:Goal {id: row.id}), (m:Metric {id: row.metric}) MERGE (g)-[:TRACKED_BY]->(m)",
     lambda c: c["goals"]),
    ("MATCH (p:Person {id: row.owner}), (m:Metric {id: row.id}) MERGE (p)-[:OWNS]->(m)",
     lambda c: [m for m in c["metrics"] if m["owner"]]),
    ("MATCH (i:Initiative {id: row.id}), (g:Goal {id: row.goal}) MERGE (i)-[:SERVES]->(g)",
     lambda c: c["initiatives"]),
    ("MATCH (p:Project {id: row.id}), (i:Initiative {id: row.initiative}) MERGE (p)-[:PART_OF]->(i)",
     lambda c: [p for p in c["projects"] if p["initiative"]]),
    ("MATCH (t:Team {id: row.team}), (p:Project {id: row.id}) MERGE (t)-[:OWNS]->(p)",
     lambda c: c["projects"]),
    ("MATCH (p:Project {id: row.id}), (k:Capability {id: row.capability}) MERGE (p)-[:BUILDS]->(k)",
     lambda c: [p for p in c["projects"] if p["capability"]]),
    ("MATCH (t:Task {id: row.id}), (p:Project {id: row.project}) MERGE (t)-[:PART_OF]->(p)",
     lambda c: [t for t in c["tasks"] if t["project"]]),
    ("MATCH (t:Task {id: row.id}), (p:Person {id: row.assignee}) MERGE (t)-[:ASSIGNED_TO]->(p)",
     lambda c: c["tasks"]),
    ("MATCH (m:Metric {id: row.metric}) "
     "MERGE (r:Reading {id: row.metric + '@' + row.at}) SET r.at = row.at, r.value = row.value "
     "MERGE (m)-[:HAS_READING]->(r)",
     lambda c: c["readings"]),
    ("MATCH (i:Incident {id: row.id}), (m:Metric {id: row.metric}) "
     "MERGE (i)-[r:AFFECTED {source_id: row.source}]->(m)",
     lambda c: c["incidents"]),
    ("MATCH (i:Incident {id: row.id}), (s:Source {id: row.source}) MERGE (i)-[:REPORTED_IN]->(s)",
     lambda c: c["incidents"]),
    ("MATCH (p:Person {id: row.author}), (s:Source {id: row.id}) MERGE (p)-[:AUTHORED]->(s)",
     lambda c: c["sources"]),
    ("MATCH (s:Source {id: row.source}), (p:Project {id: row.project}) MERGE (s)-[:MENTIONS]->(p)",
     lambda c: [{"source": s["id"], "project": p} for s in c["sources"] for p in s["mentions"]]),
    ("MATCH (a:Assumption {id: row.id}), (s:Source {id: row.contradicted_by}) "
     "MERGE (a)-[r:CONTRADICTED_BY {at: row.contradicted_at}]->(s)",
     lambda c: [a for a in c["assumptions"] if a.get("contradicted_by")]),
    ("MATCH (a:Assumption {id: row.id}), (s:Source {id: row.supported_by}) MERGE (a)-[:SUPPORTED_BY]->(s)",
     lambda c: [a for a in c["assumptions"] if a.get("supported_by")]),
    ("MATCH (p:Person {id: row.decider}), (d:Decision {id: row.id}) MERGE (p)-[:DECIDED]->(d)",
     lambda c: c["decisions"]),
    ("MATCH (d:Decision {id: row.id}), (p:Project {id: row.project}) MERGE (d)-[:FOR]->(p)",
     lambda c: [d for d in c["decisions"] if d["project"]]),
    ("MATCH (d:Decision {id: row.id}), (s:Source {id: row.decided_in}) MERGE (d)-[:DECIDED_IN]->(s)",
     lambda c: c["decisions"]),
    ("MATCH (d:Decision {id: row.id}), (s:Source {id: row.discussed_in}) MERGE (d)-[:DISCUSSED_IN]->(s)",
     lambda c: c["decisions"]),
    ("MATCH (d:Decision {id: row.id}), (a:Assumption {id: row.assumption}) "
     "MERGE (d)-[:RESTS_ON {source_id: row.source}]->(a)",
     lambda c: [{"id": d["id"], "assumption": a, "source": d["decided_in"]}
                for d in c["decisions"] for a in d["assumptions"]]),
    ("MATCH (d:Decision {id: row.id}), (m:Metric {id: row.metric}) "
     "MERGE (d)-[:EXPECTS {change: row.change, judge_by: row.judge_by, source_id: row.source}]->(m)",
     lambda c: [{"id": d["id"], **d["expects"]} for d in c["decisions"] if d["expects"]]),
    ("MATCH (d:Decision {id: row.id}), (old:Decision {id: row.supersedes}) MERGE (d)-[:SUPERSEDES]->(old)",
     lambda c: [d for d in c["decisions"] if d["supersedes"]]),
]


def load(company: dict, graph: Graph) -> None:
    # The export's as-of date is "today" for every time-based judgement.
    graph.query("MERGE (c:Company {id: $id}) SET c.name = $name, c.as_of = $as_of",
                {"id": company["company"].lower(), "name": company["company"], "as_of": company["as_of"]})
    for label, (key, set_clause) in NODES.items():
        _run(graph, f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) {set_clause}", company[key])
    for query, rows in EDGES:
        _run(graph, f"UNWIND $rows AS row {query}", rows(company))


def main(path: str = "data/kestrel") -> None:
    settings = Settings()
    company = exports.read(path)
    db = connect(settings)
    staging_name = f"{settings.graph}__loading"
    if staging_name in db.list_graphs():
        db.select_graph(staging_name).delete()

    staging = db.select_graph(staging_name)
    schema.apply(db, staging)
    load(company, staging)

    # Swap the finished import in only now, so readers never see a partial graph.
    if settings.graph in db.list_graphs():
        db.select_graph(settings.graph).delete()
    staging.copy(settings.graph)
    staging.delete()

    live = db.select_graph(settings.graph)
    nodes = live.ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = live.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    print(f"Loaded {company['company']} into graph '{settings.graph}': {nodes} nodes, {edges} relationships")


if __name__ == "__main__":
    main(*sys.argv[1:])
