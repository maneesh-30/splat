"""Graph schema: indexes and the constraints FalkorDB enforces on every write."""

from falkordb import FalkorDB
from falkordb.graph import Graph

NODE_LABELS = [
    "Team", "Person", "Goal", "Initiative", "Project", "Capability", "Task",
    "Metric", "Reading", "Incident", "Assumption", "Decision", "Source",
]

# Relationships that state a fact must say where the fact came from. With these
# constraints a write that omits the source is rejected by the database itself.
MANDATORY_EDGE_PROPERTIES = {
    "RESTS_ON": ["source_id"],
    "EXPECTS": ["change", "judge_by", "source_id"],
    "AFFECTED": ["source_id"],
    "CONTRADICTED_BY": ["at"],
}

FULLTEXT = {
    "Decision": ["title"],
    "Assumption": ["statement"],
    "Project": ["title"],
}


def _constraint(db: FalkorDB, graph: str, *args: str) -> None:
    try:
        db.connection.execute_command("GRAPH.CONSTRAINT", "CREATE", graph, *args)
    except Exception as exc:  # re-running on an existing graph is fine
        if "already exists" not in str(exc).lower():
            raise


def apply(db: FalkorDB, graph: Graph) -> None:
    for label in NODE_LABELS:
        graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
        _constraint(db, graph.name, "UNIQUE", "NODE", label, "PROPERTIES", "1", "id")
    for rel, props in MANDATORY_EDGE_PROPERTIES.items():
        _constraint(db, graph.name, "MANDATORY", "RELATIONSHIP", rel, "PROPERTIES", str(len(props)), *props)
    for label, props in FULLTEXT.items():
        graph.query(f"CALL db.idx.fulltext.createNodeIndex('{label}', {', '.join(repr(p) for p in props)})")
