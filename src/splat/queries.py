"""Every Cypher query the product runs. The LLM never writes Cypher; it can only
choose one of the tools built on these fixed, parameterised, read-only queries."""

AS_OF = "MATCH (c:Company) RETURN c.as_of LIMIT 1"

SOURCE = """
MATCH (s:Source {id: $id})
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(s)
RETURN s.id, s.kind, s.title, s.at, s.text, p.name
"""

FIND = """
CALL db.idx.fulltext.queryNodes($label, $text) YIELD node, score
RETURN node.id AS id, coalesce(node.title, node.statement) AS text, score
ORDER BY score DESC LIMIT $limit
"""

# --- Outcomes ----------------------------------------------------------------

DECISION = """
MATCH (d:Decision {id: $id})
OPTIONAL MATCH (p:Person)-[:DECIDED]->(d)
OPTIONAL MATCH (d)-[:DECIDED_IN]->(doc:Source)
OPTIONAL MATCH (d)-[:FOR]->(proj:Project)
OPTIONAL MATCH (d)-[e:EXPECTS]->(m:Metric)
RETURN d.id, d.title, d.status, d.decided_at, d.shipped_at, p.name, doc.id, doc.title,
       proj.id, proj.title, m.id, m.name, m.unit, m.better, e.change, e.judge_by, e.source_id
"""

DECISION_ASSUMPTIONS = """
MATCH (d:Decision {id: $id})-[r:RESTS_ON]->(a:Assumption)
OPTIONAL MATCH (a)-[c:CONTRADICTED_BY]->(s:Source)
RETURN a.id, a.statement, r.source_id, c.at, s.id, s.title
"""

READINGS = """
MATCH (:Metric {id: $metric})-[:HAS_READING]->(r:Reading)
WHERE r.at >= $from AND r.at <= $to
RETURN r.at, r.value ORDER BY r.at
"""

# Other changes that hit the same metric inside the judging window. If any exist,
# the decision's result cannot be attributed to it alone.
OVERLAPPING_DECISIONS = """
MATCH (d:Decision {id: $id})-[:EXPECTS]->(m:Metric)<-[:EXPECTS]-(other:Decision)
WHERE other <> d AND other.shipped_at IS NOT NULL
  AND other.shipped_at > $from AND other.shipped_at <= $to
RETURN other.id, other.title, other.shipped_at
"""

OVERLAPPING_INCIDENTS = """
MATCH (i:Incident)-[a:AFFECTED]->(:Metric {id: $metric})
WHERE i.at <= $to AND i.until >= $from
OPTIONAL MATCH (i)-[:REPORTED_IN]->(s:Source)
RETURN i.id, i.title, i.at, i.until, s.id
"""

ALL_DECISIONS = "MATCH (d:Decision) RETURN d.id ORDER BY d.id"

# --- Assumptions ---------------------------------------------------------------

# A disproved assumption, every decision that rests on it, and whether each
# decision was made before or after the evidence against it appeared.
ASSUMPTION_CASCADE = """
MATCH (a:Assumption)-[c:CONTRADICTED_BY]->(s:Source)
WHERE $id IS NULL OR a.id = $id
MATCH (d:Decision)-[:RESTS_ON]->(a)
OPTIONAL MATCH (author:Person)-[:AUTHORED]->(s)
OPTIONAL MATCH (owner:Person)-[:DECIDED]->(d)
RETURN a.id, a.statement, c.at, s.id, s.title, author.name,
       d.id, d.title, d.status, d.decided_at, d.shipped_at, owner.name
ORDER BY a.id, d.decided_at
"""

SHARED_ASSUMPTIONS = """
MATCH (d:Decision)-[:RESTS_ON]->(a:Assumption)
WITH a, collect(d.id) AS decisions
WHERE size(decisions) > 1
OPTIONAL MATCH (a)-[c:CONTRADICTED_BY]->(:Source)
RETURN a.id, a.statement, decisions, c.at
"""

SIMILAR_DECISIONS = """
CALL db.idx.fulltext.queryNodes('Decision', $text) YIELD node, score
RETURN node.id AS id, node.title AS title, score ORDER BY score DESC LIMIT 5
UNION
CALL db.idx.fulltext.queryNodes('Assumption', $text) YIELD node, score
MATCH (d:Decision)-[:RESTS_ON]->(node)
RETURN d.id AS id, d.title AS title, score ORDER BY score DESC LIMIT 5
"""

# Everything that relies on an assumption, at any depth: decisions resting on it,
# decisions built on those, the epics they created, epics depending on those epics,
# and the open work inside them. One variable-length pattern, however deep it goes.
BLAST_RADIUS = """
MATCH p = (n)-[:RELIES_ON*1..]->(:Assumption {id: $id})
WITH n, min(length(p)) AS hops
RETURN labels(n)[0], n.id, coalesce(n.title, n.id), n.status, hops
ORDER BY hops, n.id
"""

# The RELIES_ON links among everything hit, so the blast radius can be drawn as a tree.
BLAST_EDGES = """
MATCH (n)-[:RELIES_ON*1..]->(a:Assumption {id: $id})
WITH collect(DISTINCT n) + collect(DISTINCT a) AS hit
UNWIND hit AS child
MATCH (child)-[:RELIES_ON]->(parent)
WHERE parent IN hit
RETURN DISTINCT child.id, parent.id
"""

BLAST_REACH = """
MATCH (n)-[:RELIES_ON*1..]->(:Assumption {id: $id})
WITH collect(DISTINCT n) AS hit
UNWIND hit AS n
OPTIONAL MATCH (n)-[:PART_OF]->(:Initiative)-[:SERVES]->(g:Goal)
OPTIONAL MATCH (n)-[:ASSIGNED_TO]->(worker:Person)
OPTIONAL MATCH (decider:Person)-[:DECIDED]->(n)
OPTIONAL MATCH (n)-[:EXPECTS]->(m:Metric)
RETURN collect(DISTINCT g.id + ': ' + g.title + ' (' + g.status + ')'),
       collect(DISTINCT worker.name), collect(DISTINCT decider.name), collect(DISTINCT m.name)
"""

# PageRank over RELIES_ON: an assumption scores high when much of the company's
# work rests on it, directly or through other decisions and epics.
LOAD_BEARING = """
CALL algo.pageRank(NULL, 'RELIES_ON') YIELD node, score
WITH node, score WHERE node:Assumption
OPTIONAL MATCH (node)-[c:CONTRADICTED_BY]->()
OPTIONAL MATCH (node)-[s:SUPPORTED_BY]->()
OPTIONAL MATCH (x)-[:RELIES_ON*1..]->(node)
RETURN node.id, node.statement, score, count(DISTINCT c) > 0, count(DISTINCT s) > 0, count(DISTINCT x)
ORDER BY score DESC
"""

# Who talks to whom, from people posting in the same threads, split into
# connected groups. People in different groups have no chain of conversation at all.
ISLANDS = """
CALL algo.WCC({nodeLabels: ['Person'], relationshipTypes: ['TALKS_WITH']}) YIELD node, componentId
OPTIONAL MATCH (node)-[m:MEMBER_OF]->(t:Team) WHERE m.until IS NULL
RETURN node.id, node.name, componentId, t.id
"""

# --- Drawing the graph ----------------------------------------------------------

# Relationships worth drawing. Readings, thread participation and derived
# relationships are left out so pictures show meaning, not plumbing.
DRAWN = ["RESTS_ON", "CONTRADICTED_BY", "SUPPORTED_BY", "EXPECTS", "FOR", "DECIDED", "DECIDED_IN",
         "BUILDS_ON", "DEPENDS_ON", "PART_OF", "SERVES", "TRACKED_BY", "SUPERSEDES", "AFFECTED",
         "REPORTED_IN", "BUILDS", "OWNS", "ASSIGNED_TO"]

# When drawing an answer, follow only what the answer's own nodes point to: a
# decision's promise, assumption, record and the decision it builds on, an
# assumption's evidence, an epic's initiative and goal. Following links in both
# directions pulls in everything else that touches a shared metric or goal.
EXPAND = ["EXPECTS", "RESTS_ON", "CONTRADICTED_BY", "SUPPORTED_BY", "DECIDED_IN", "BUILDS_ON",
          "AFFECTED", "REPORTED_IN", "PART_OF", "SERVES", "BUILDS", "SUPERSEDES"]

GRAPH_NEIGHBOURS = """
MATCH (n) WHERE n.id IN $ids
OPTIONAL MATCH (n)-[r]->(m) WHERE type(r) IN $types AND NOT m:Task AND NOT m:Reading AND NOT m:Person
RETURN collect(DISTINCT m.id)
"""

GRAPH_NODES = """
MATCH (n) WHERE n.id IN $ids
OPTIONAL MATCH (n)-[c:CONTRADICTED_BY]->()
OPTIONAL MATCH (n)-[s:SUPPORTED_BY]->()
RETURN n.id, labels(n)[0], coalesce(n.title, n.statement, n.name, n.id),
       CASE WHEN count(s) > 0 THEN 'supported' ELSE n.status END, count(c) > 0
"""

GRAPH_EDGES = """
MATCH (a)-[r]->(b)
WHERE a.id IN $ids AND b.id IN $ids AND type(r) IN $types
RETURN DISTINCT a.id, type(r), b.id
"""

# The company map: how goals, initiatives, epics, decisions, assumptions and
# metrics connect. About a hundred nodes, small enough to read.
MAP_LABELS = ["Goal", "Initiative", "Project", "Decision", "Assumption", "Metric", "Capability"]
MAP_IDS = "MATCH (n) WHERE labels(n)[0] IN $labels RETURN collect(n.id)"

# --- Goals and work ---------------------------------------------------------------

WHY = """
MATCH (start {id: $id})
WHERE start:Task OR start:Project OR start:Initiative OR start:Decision
OPTIONAL MATCH path = (start)-[:FOR|PART_OF|SERVES*1..4]->(g:Goal)
RETURN start.id, coalesce(start.title, start.id), labels(start)[0],
       [n IN nodes(path) | n.id + ': ' + coalesce(n.title, n.statement, '')] AS chain,
       g.status, g.cancelled_at
"""

# How far a piece of work gets toward a goal when it never reaches one.
WHY_PARTIAL = """
MATCH path = (start {id: $id})-[:FOR|PART_OF*1..3]->(end)
WHERE NOT (end)-[:PART_OF|SERVES]->()
WITH path ORDER BY length(path) DESC LIMIT 1
RETURN [n IN nodes(path) | n.id + ': ' + coalesce(n.title, '')]
"""

# Active work with no path to any active goal.
ORPHANED_PROJECTS = """
MATCH (p:Project) WHERE p.status = 'active'
OPTIONAL MATCH (team:Team)-[:OWNS]->(p)
OPTIONAL MATCH (p)-[:PART_OF]->(:Initiative)-[:SERVES]->(g:Goal)
WITH p, team, collect(g) AS goals
WHERE size([g IN goals WHERE g.status = 'active']) = 0
RETURN p.id, p.title, team.name, [g IN goals | g.id + ' (' + g.status + ')'] AS reached
"""

ORPHANED_TASKS = """
MATCH (t:Task) WHERE t.closed_at IS NULL AND NOT (t)-[:PART_OF]->(:Project)
OPTIONAL MATCH (t)-[:ASSIGNED_TO]->(p:Person)
RETURN t.id, t.title, p.name, t.created_at
"""

GOALS_WITHOUT_WORK = """
MATCH (g:Goal) WHERE g.status = 'active'
OPTIONAL MATCH (owner:Person)-[:OWNS]->(g)
OPTIONAL MATCH (g)<-[:SERVES]-(:Initiative)<-[:PART_OF]-(p:Project)
WITH g, owner, collect(DISTINCT p) AS projects
WHERE size([p IN projects WHERE p.status = 'active']) = 0
RETURN g.id, g.title, owner.name, [p IN projects | p.title + ' (' + p.status + ')'] AS past_work
"""

CANCELLED_GOAL_WORK = """
MATCH (g:Goal {status: 'cancelled'})<-[:SERVES]-(i:Initiative)<-[:PART_OF]-(p:Project)
OPTIONAL MATCH (t:Task)-[:PART_OF]->(p) WHERE t.created_at > g.cancelled_at
RETURN g.id, g.title, g.cancelled_at, i.id, i.title, i.status, p.id, p.title, p.status,
       count(t) AS tasks_after_cancel
"""

# A metric is unowned if nobody owns it, or if its owner no longer belongs to any team.
UNOWNED_METRICS = """
MATCH (m:Metric)
OPTIONAL MATCH (o:Person)-[:OWNS]->(m)
OPTIONAL MATCH (o)-[mem:MEMBER_OF]->(:Team) WHERE mem.until IS NULL
OPTIONAL MATCH (o)-[left:MEMBER_OF]->(:Team) WHERE left.until IS NOT NULL
OPTIONAL MATCH (g:Goal)-[:TRACKED_BY]->(m)
WITH m, o, count(mem) AS current, max(left.until) AS left_on, collect(g.id + ' (' + g.status + ')') AS goals
WHERE o IS NULL OR current = 0
RETURN m.id, m.name, o.name, left_on, goals
"""

# Two active projects in different teams building the same capability.
DUPLICATE_CANDIDATES = """
MATCH (ta:Team)-[:OWNS]->(a:Project)-[:BUILDS]->(k:Capability)<-[:BUILDS]-(b:Project)<-[:OWNS]-(tb:Team)
WHERE a.id < b.id AND ta <> tb AND a.status = 'active' AND b.status = 'active'
RETURN a.id, a.title, ta.name, b.id, b.title, tb.name, k.name
"""

# Everyone connected to a project: people assigned its tasks, people who wrote a
# thread or document mentioning it, and current members of the team that owns it.
_PROJECT_PEOPLE = """
MATCH (p:Project {{id: ${param}}})
OPTIONAL MATCH (p)<-[:PART_OF]-(:Task)-[:ASSIGNED_TO]->(assignee:Person)
OPTIONAL MATCH (p)<-[:MENTIONS]-(:Source)<-[:AUTHORED]-(author:Person)
OPTIONAL MATCH (p)<-[:OWNS]-(:Team)<-[m:MEMBER_OF]-(member:Person) WHERE m.until IS NULL
WITH collect(DISTINCT assignee) + collect(DISTINCT author) + collect(DISTINCT member) AS {name}
"""

# Is there any working link between two projects? A shared person, or one thread
# that mentions both. No link means the two teams show no sign of knowing.
PROJECT_LINK = (
    _PROJECT_PEOPLE.format(param="a", name="people_a")
    + _PROJECT_PEOPLE.format(param="b", name="people_b").replace("WITH ", "WITH people_a, ", 1)
    + """
WITH [x IN people_a WHERE x IN people_b | x.name] AS shared
OPTIONAL MATCH (:Project {id: $a})<-[:MENTIONS]-(s:Source)-[:MENTIONS]->(:Project {id: $b})
RETURN shared, collect(s.id)
"""
)
