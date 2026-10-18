"""The graph tools. Each one runs fixed read-only Cypher and returns structured
results plus a trace (queries run, timings) so every answer can show its work."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean

from falkordb.graph import Graph

from splat import queries as Q

TIMEOUT_MS = 3000
BASELINE_DAYS = 28
OUTCOME_DAYS = 27
WORKED_SHARE = 0.8   # reached at least 80% of the expected change
PARTIAL_SHARE = 0.3  # moved the right way, but less than 80%
SIMILAR_MIN_SHARE = 0.4  # drop matches far weaker than the best one


STOPWORDS = {"a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "is", "it", "we", "our",
              "did", "does", "do", "be", "by", "with", "this", "that", "what", "why", "how", "should",
              # words that frame a question rather than name the thing asked about
              "plan", "plans", "planning", "thinking", "tried", "try", "before", "similar", "been", "has",
              "have", "want", "considering", "idea", "anyone", "someone", "already", "something",
              "who", "knows", "know", "most", "about", "expert", "experts", "ask", "talk", "owns", "own",
              "responsible", "team", "working", "doing", "can", "could", "would", "need"}


def search_terms(text: str) -> str:
    """Full-text query that matches any meaningful word (the index default is all words)."""
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2]
    return "|".join(dict.fromkeys(words))


ROW_FILES = [("EP-", "planning/epics.csv"), ("KES-", "jira/issues.csv"), ("GH-", "github/pull_requests.csv"),
             ("G-", "planning/goals.csv"), ("I-", "planning/initiatives.csv"), ("A-", "assumptions/register.csv"),
             ("INC-", "incidents")]


def row_source(node_id: str | None) -> str | None:
    """Where a structured fact lives in the exports, e.g. planning/epics.csv#EP-11."""
    if not node_id:
        return None
    for prefix, path in ROW_FILES:
        if node_id.startswith(prefix):
            return f"{path}#{node_id}"
    if "@" in node_id:
        return f"org/people.csv#{node_id}"
    return f"metrics/metrics.csv#{node_id}"


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in items:
        key = (item["kind"], item["id"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


@dataclass
class Trace:
    queries: list[dict] = field(default_factory=list)

    @property
    def total_ms(self) -> float:
        return round(sum(q["ms"] for q in self.queries), 2)


class Tools:
    def __init__(self, graph: Graph):
        self.graph = graph
        self.trace = Trace()
        self._as_of: date | None = None

    # -- plumbing ---------------------------------------------------------------

    def _q(self, name: str, cypher: str, **params) -> list[list]:
        start = time.perf_counter()
        rows = self.graph.ro_query(cypher, params, timeout=TIMEOUT_MS).result_set
        ms = (time.perf_counter() - start) * 1000
        self.trace.queries.append({"name": name, "cypher": " ".join(cypher.split()),
                                   "params": params, "rows": len(rows), "ms": round(ms, 2)})
        return rows

    def reset_trace(self) -> Trace:
        trace, self.trace = self.trace, Trace()
        return trace

    @property
    def as_of(self) -> date:
        if self._as_of is None:
            self._as_of = date.fromisoformat(self._q("as_of", Q.AS_OF)[0][0])
        return self._as_of

    # -- lookup -----------------------------------------------------------------

    def find(self, label: str, text: str, limit: int = 5) -> list[dict]:
        clean = search_terms(text)
        if not clean:
            return []
        rows = self._q("find", Q.FIND, label=label, text=clean, limit=limit)
        return [{"id": r[0], "text": r[1], "score": round(r[2], 3)} for r in rows]

    def catalogue(self) -> list[dict]:
        rows = self._q("catalogue", Q.CATALOGUE)
        return [{"id": r[0], "text": r[1]} for r in rows]

    def source(self, source_id: str) -> dict | None:
        rows = self._q("source", Q.SOURCE, id=source_id)
        if not rows:
            return None
        sid, kind, title, at, text, author = rows[0]
        return {"id": sid, "kind": kind, "title": title, "at": at, "text": text, "author": author}

    # -- outcomes -----------------------------------------------------------------

    def _mean(self, metric: str, start: date, end: date) -> tuple[float | None, int]:
        rows = self._q("readings", Q.READINGS, metric=metric, **{"from": start.isoformat(), "to": end.isoformat()})
        values = [r[1] for r in rows]
        return (mean(values) if values else None), len(values)

    def verdict(self, decision_id: str) -> dict:
        rows = self._q("decision", Q.DECISION, id=decision_id)
        if not rows:
            return {"id": decision_id, "verdict": "unknown", "reason": "No such decision in the graph."}
        (did, title, status, decided, shipped, decider, doc_id, doc_title, proj_id, proj_title,
         metric, metric_name, unit, better, change, judge_by, exp_source) = rows[0]

        out = {
            "id": did, "title": title, "status": status, "decided_at": decided, "shipped_at": shipped,
            "decider": decider, "decided_in": {"id": doc_id, "title": doc_title},
            "project": proj_id and {"id": proj_id, "title": proj_title},
            "assumptions": [
                {"id": a[0], "statement": a[1], "source": a[2],
                 "contradicted_at": a[3], "contradicted_by": a[4] and {"id": a[4], "title": a[5]}}
                for a in self._q("assumptions", Q.DECISION_ASSUMPTIONS, id=decision_id)
            ],
        }
        if metric is None:
            return {**out, "verdict": "no_success_metric",
                    "reason": "The decision never said what result it expected, so it cannot be judged."}
        out["expected"] = {"metric": metric, "metric_name": metric_name, "unit": unit, "better": better,
                           "change": change, "judge_by": judge_by, "source": exp_source}
        if not shipped:
            late = date.fromisoformat(judge_by) < self.as_of
            return {**out, "verdict": "not_shipped",
                    "reason": "It has not shipped" + (f", and its judge-by date {judge_by} has passed." if late else ".")}

        ship = date.fromisoformat(shipped)
        due = date.fromisoformat(judge_by)
        window_start = ship - timedelta(days=BASELINE_DAYS)
        if due > self.as_of:
            return {**out, "verdict": "too_early",
                    "reason": f"Its expected result is due {judge_by}; the data runs to {self.as_of}."}

        baseline, nb = self._mean(metric, window_start, ship - timedelta(days=1))
        outcome, no = self._mean(metric, due - timedelta(days=OUTCOME_DAYS), due)
        if baseline is None or outcome is None:
            return {**out, "verdict": "insufficient_data", "reason": "Not enough readings around the window."}
        actual = (outcome - baseline) / baseline
        out["measured"] = {"baseline": round(baseline, 2), "outcome": round(outcome, 2),
                           "change": round(actual, 4), "baseline_readings": nb, "outcome_readings": no,
                           "baseline_window": [window_start.isoformat(), (ship - timedelta(days=1)).isoformat()],
                           "outcome_window": [(due - timedelta(days=OUTCOME_DAYS)).isoformat(), judge_by]}

        window = {"from": window_start.isoformat(), "to": judge_by}
        overlaps = [{"type": "decision", "id": r[0], "title": r[1], "at": r[2]}
                    for r in self._q("overlapping_decisions", Q.OVERLAPPING_DECISIONS, id=decision_id, **window)]
        overlaps += [{"type": "incident", "id": r[0], "title": r[1], "at": r[2], "until": r[3], "source": r[4]}
                     for r in self._q("overlapping_incidents", Q.OVERLAPPING_INCIDENTS, metric=metric, **window)]
        out["overlaps"] = overlaps

        share = actual / change if change else 0
        out["achieved_share"] = round(share, 2)
        if overlaps:
            names = ", ".join(f"{o['id']} ({o['title']})" for o in overlaps)
            return {**out, "verdict": "ambiguous",
                    "reason": f"{metric_name} moved {actual:+.1%} against an expected {change:+.1%}, "
                              f"but {names} also hit the same metric in the window."}
        if share >= WORKED_SHARE:
            verdict = "worked"
        elif share >= PARTIAL_SHARE:
            verdict = "partial"
        else:
            verdict = "missed"
        return {**out, "verdict": verdict,
                "reason": f"{metric_name} moved {actual:+.1%} against an expected {change:+.1%} "
                          f"({share:.0%} of the target), with no other change on that metric in the window."}

    def scorecard(self) -> dict:
        results = [self.verdict(r[0]) for r in self._q("all_decisions", Q.ALL_DECISIONS)]
        counts: dict[str, int] = {}
        for r in results:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        return {"total": len(results), "counts": counts,
                "decisions": [{k: r.get(k) for k in ("id", "title", "verdict", "reason")} for r in results]}

    # -- assumptions --------------------------------------------------------------

    def cascade(self, assumption_id: str | None = None) -> list[dict]:
        rows = self._q("assumption_cascade", Q.ASSUMPTION_CASCADE, id=assumption_id)
        found: dict[str, dict] = {}
        for (aid, statement, contradicted_at, sid, stitle, author,
             did, dtitle, dstatus, decided, shipped, owner) in rows:
            a = found.setdefault(aid, {"id": aid, "statement": statement, "contradicted_at": contradicted_at,
                                       "evidence": {"id": sid, "title": stitle, "author": author},
                                       "decisions": []})
            judged = self.verdict(did)["verdict"]
            a["decisions"].append({
                "id": did, "title": dtitle, "owner": owner, "decided_at": decided, "shipped_at": shipped,
                "verdict": judged,
                "decided_after_evidence": decided > contradicted_at,
                "still_live": dstatus == "active" and judged in ("too_early", "not_shipped", "no_success_metric"),
            })
        return list(found.values())

    def shared_assumptions(self) -> list[dict]:
        """Assumptions several decisions rely on, with how those decisions turned out."""
        out = []
        for aid, statement, decisions, contradicted_at in self._q("shared_assumptions", Q.SHARED_ASSUMPTIONS):
            verdicts = {d: self.verdict(d)["verdict"] for d in decisions}
            missed = [d for d, v in verdicts.items() if v == "missed"]
            out.append({"id": aid, "statement": statement, "contradicted_at": contradicted_at,
                        "decisions": verdicts,
                        "suspect": len(missed) >= 2 or (contradicted_at is not None)})
        return out

    def blast_radius(self, assumption_id: str) -> dict:
        """Everything that relies on an assumption, at any depth."""
        rows = self._q("blast_radius", Q.BLAST_RADIUS, id=assumption_id)
        hit = [{"kind": r[0], "id": r[1], "title": r[2], "status": r[3], "hops": r[4]} for r in rows]
        goals, workers, deciders, metrics = (self._q("blast_reach", Q.BLAST_REACH, id=assumption_id)[0]
                                             if hit else ([], [], [], []))
        edges = [{"child": r[0], "parent": r[1]}
                 for r in self._q("blast_edges", Q.BLAST_EDGES, id=assumption_id)] if hit else []
        return {"assumption": assumption_id, "items": hit, "edges": edges,
                "decisions": [h for h in hit if h["kind"] == "Decision"],
                "epics": [h for h in hit if h["kind"] == "Project"],
                "open_tasks": [h for h in hit if h["kind"] == "Task"],
                "max_hops": max((h["hops"] for h in hit), default=0),
                "goals": goals, "people": sorted(set(workers) | set(deciders)), "metrics": metrics}

    def load_bearing(self) -> list[dict]:
        """Assumptions ranked by PageRank over what relies on them."""
        out = []
        for aid, statement, score, disproved, supported, dependents in self._q("load_bearing", Q.LOAD_BEARING):
            status = "disproved" if disproved else "supported" if supported else "untested"
            out.append({"id": aid, "statement": statement, "score": round(score, 4),
                        "status": status, "dependents": dependents})
        return out

    def islands(self) -> dict:
        """Groups of people connected by any chain of conversation."""
        groups: dict[int, dict] = {}
        for pid, name, component, team in self._q("islands", Q.ISLANDS):
            g = groups.setdefault(component, {"people": [], "teams": set()})
            g["people"].append(name)
            if team:
                g["teams"].add(team)
        team_islands: dict[str, set[int]] = {}
        for component, g in groups.items():
            for team in g["teams"]:
                team_islands.setdefault(team, set()).add(component)
        return {"groups": {k: {"people": v["people"], "teams": sorted(v["teams"])} for k, v in groups.items()},
                "team_islands": team_islands}

    def subgraph(self, ids: list[str], expand: bool = True) -> dict:
        """Nodes and relationships for drawing: the given nodes, optionally with their
        direct neighbours, and every drawable relationship among them."""
        ids = list(dict.fromkeys(i for i in ids if i))
        if expand and ids:
            ids += self._q("graph_neighbours", Q.GRAPH_NEIGHBOURS, ids=ids, types=Q.EXPAND)[0][0]
            ids = list(dict.fromkeys(ids))
        return self._draw(ids)

    def company_map(self) -> dict:
        return self._draw(self._q("map_ids", Q.MAP_IDS, labels=Q.MAP_LABELS)[0][0])

    def _draw(self, ids: list[str]) -> dict:
        nodes = [{"id": r[0], "label": r[1], "title": r[2], "status": r[3], "disproved": r[4]}
                 for r in self._q("graph_nodes", Q.GRAPH_NODES, ids=ids)]
        edges = [{"source": r[0], "type": r[1], "target": r[2]}
                 for r in self._q("graph_edges", Q.GRAPH_EDGES, ids=ids, types=Q.DRAWN)]
        return {"nodes": nodes, "edges": edges}

    def similar(self, text: str) -> list[dict]:
        clean = search_terms(text)
        rows = self._q("similar_decisions", Q.SIMILAR_DECISIONS, text=clean) if clean else []
        seen, out = set(), []
        top = max((r[2] for r in rows), default=0)
        for did, title, score in sorted(rows, key=lambda r: -r[2]):
            if did in seen or score < SIMILAR_MIN_SHARE * top:
                continue
            seen.add(did)
            v = self.verdict(did)
            out.append({"id": did, "title": title, "verdict": v["verdict"], "reason": v.get("reason"),
                        "assumptions": v.get("assumptions", [])})
        return out

    # -- goals and work -------------------------------------------------------------

    def why(self, node_id: str) -> dict:
        rows = self._q("why", Q.WHY, id=node_id)
        if not rows:
            return {"id": node_id, "found": False}
        chains = [{"chain": r[3], "goal_status": r[4], "goal_cancelled_at": r[5]} for r in rows if r[3]]
        out = {"id": rows[0][0], "title": rows[0][1], "kind": rows[0][2], "found": True, "chains": chains}
        if not chains:
            partial = self._q("why_partial", Q.WHY_PARTIAL, id=node_id)
            out["partial"] = partial[0][0] if partial else []
        return out

    def orphaned_work(self) -> dict:
        projects = [{"id": r[0], "title": r[1], "team": r[2], "reaches": r[3]}
                    for r in self._q("orphaned_projects", Q.ORPHANED_PROJECTS)]
        tasks = [{"id": r[0], "title": r[1], "assignee": r[2], "created_at": r[3]}
                 for r in self._q("orphaned_tasks", Q.ORPHANED_TASKS)]
        return {"projects": projects, "tasks": tasks}

    def goals_without_work(self) -> list[dict]:
        return [{"id": r[0], "title": r[1], "owner": r[2], "past_work": r[3]}
                for r in self._q("goals_without_work", Q.GOALS_WITHOUT_WORK)]

    def cancelled_goal_work(self) -> list[dict]:
        return [{"goal": {"id": r[0], "title": r[1], "cancelled_at": r[2]},
                 "initiative": {"id": r[3], "title": r[4], "status": r[5]},
                 "project": {"id": r[6], "title": r[7], "status": r[8]}, "tasks_after_cancel": r[9]}
                for r in self._q("cancelled_goal_work", Q.CANCELLED_GOAL_WORK)
                if r[8] == "active"]

    def unowned_metrics(self) -> list[dict]:
        return [{"id": r[0], "name": r[1], "former_owner": r[2], "owner_left_on": r[3], "goals": r[4]}
                for r in self._q("unowned_metrics", Q.UNOWNED_METRICS)]

    def duplicate_work(self) -> list[dict]:
        out = []
        islands = None
        for a, at, ateam, b, bt, bteam, capability in self._q("duplicate_candidates", Q.DUPLICATE_CANDIDATES):
            shared, threads = self._q("project_link", Q.PROJECT_LINK, a=a, b=b)[0]
            islands = islands or self.islands()
            team_id = {t["name"]: t["id"] for t in self._teams()}
            ia = islands["team_islands"].get(team_id[ateam], set())
            ib = islands["team_islands"].get(team_id[bteam], set())
            out.append({"capability": capability,
                        "projects": [{"id": a, "title": at, "team": ateam}, {"id": b, "title": bt, "team": bteam}],
                        "linked": bool(shared or threads), "shared_people": shared, "shared_threads": threads,
                        "same_island": bool(ia & ib),
                        "islands": [{"teams": islands["groups"][k]["teams"],
                                     "people": len(islands["groups"][k]["people"])}
                                    for k in sorted(ia | ib) if len(islands["groups"][k]["people"]) > 1]})
        return out

    # -- people: who knows, who owns, what a team does ------------------------------

    def _people(self) -> dict[str, dict]:
        return {r[0]: {"id": r[0], "name": r[1], "team": r[2], "left_on": r[3] if not r[2] else None}
                for r in self._q("people", Q.PEOPLE)}

    def who_knows(self, text: str, limit: int = 5) -> dict:
        """People ranked by evidence that they know a topic: decisions they made, tickets
        they worked, threads and documents they wrote or replied in."""
        terms = search_terms(text)
        if not terms:
            return {"topic": text, "people": [], "former": []}
        weights = {"decided": 4, "ticket": 2, "authored": 2, "replied": 1, "epic": 1}
        score: dict[str, float] = {}
        evidence: dict[str, list[dict]] = {}

        def add(pid, kind, item, source, title):
            if not pid:
                return
            score[pid] = score.get(pid, 0) + weights[kind]
            evidence.setdefault(pid, []).append({"kind": kind, "id": item, "title": title, "source": source})

        for sid, title, at, _, author, posters in self._q("knows_sources", Q.KNOWS_SOURCES, text=terms):
            add(author, "authored", sid, sid, title)
            for p in posters:
                if p != author:
                    add(p, "replied", sid, sid, title)
        for tid, title, at, _, pid in self._q("knows_tasks", Q.KNOWS_TASKS, text=terms):
            add(pid, "ticket", tid, row_source(tid), title)
        for did, title, at, _, pid, doc in self._q("knows_decisions", Q.KNOWS_DECISIONS, text=terms):
            add(pid, "decided", did, doc, title)
        for eid, title, _, pid, n in self._q("knows_projects", Q.KNOWS_PROJECTS, text=terms):
            for _ in range(min(int(n), 3)):
                add(pid, "epic", eid, row_source(eid), title)

        people = self._people()
        ranked = sorted(score, key=lambda p: -score[p])
        out, former = [], []
        for pid in ranked:
            person = {**people.get(pid, {"id": pid, "name": pid, "team": None, "left_on": None}),
                      "score": score[pid], "evidence": _dedupe(evidence[pid])}
            (out if person["team"] else former).append(person)
        return {"topic": text, "people": out[:limit], "former": former[:3]}

    def who_owns(self, node_id: str) -> dict:
        """Who is responsible for an epic, metric, goal or decision."""
        if node_id.startswith("EP-"):
            rows = self._q("owners_of_project", Q.OWNERS_OF_PROJECT, id=node_id)
            if not rows:
                return {"found": False}
            (eid, title, status, team, members, deciders, ini, ini_title, gid, gtitle, gstatus, gowner) = rows[0]
            return {"found": True, "kind": "Epic", "id": eid, "title": title, "status": status, "team": team,
                    "members": members, "deciders": [d for d in deciders if d[0]],
                    "initiative": ini and f"{ini}: {ini_title}", "goal": gid and f"{gid}: {gtitle} ({gstatus})",
                    "goal_owner": gowner, "sources": [row_source(eid)] + ([row_source(gid)] if gid else [])}
        if node_id.startswith("G-"):
            rows = self._q("owners_of_goal", Q.OWNERS_OF_GOAL, id=node_id)
            if not rows:
                return {"found": False}
            gid, title, status, owner, team, initiatives = rows[0]
            return {"found": True, "kind": "Goal", "id": gid, "title": title, "status": status, "owner": owner,
                    "owner_team": team, "initiatives": initiatives, "sources": [row_source(gid)]}
        if node_id.startswith("D-"):
            rows = self._q("owners_of_decision", Q.OWNERS_OF_DECISION, id=node_id)
            if not rows:
                return {"found": False}
            did, title, decider, team, doc = rows[0]
            return {"found": True, "kind": "Decision", "id": did, "title": title, "owner": decider,
                    "owner_team": team, "sources": [doc] if doc else []}
        rows = self._q("owners_of_metric", Q.OWNERS_OF_METRIC, id=node_id)
        if not rows:
            return {"found": False}
        mid, name, owners, goals = rows[0]
        return {"found": True, "kind": "Metric", "id": mid, "title": name,
                "owners": [{"name": o, "current": bool(t)} for o, t in owners if o], "goals": goals,
                "sources": [row_source(mid)]}

    def team_work(self, team_id: str) -> dict:
        rows = self._q("team_work", Q.TEAM_WORK, team=team_id)
        if not rows or rows[0][0] is None:
            return {"found": False}
        name, members, epics = rows[0]
        decisions = [{"id": r[0], "title": r[1], "decided_at": r[2], "status": r[3], "source": r[4]}
                     for r in self._q("team_decisions", Q.TEAM_DECISIONS, team=team_id)]
        return {"found": True, "team": name, "members": members,
                "epics": [{"id": e[0], "title": e[1], "open_tasks": e[2]} for e in epics if e],
                "decisions": decisions}

    def changes(self, days: int = 90) -> dict:
        since = (self.as_of - timedelta(days=days)).isoformat()
        rows = self._q("changes", Q.CHANGES, since=since)
        items = sorted(({"kind": r[0], "at": r[1], "id": r[2], "what": r[3], "source": r[4]} for r in rows),
                       key=lambda x: x["at"], reverse=True)
        return {"since": since, "until": self.as_of.isoformat(), "items": items}

    def record(self, row_id: str) -> dict | None:
        """The graph record behind a structured source such as planning/epics.csv#EP-11."""
        path, _, node_id = row_id.partition("#")
        rows = self._q("record", Q.RECORD, id=node_id)
        if not rows:
            return None
        label, props = rows[0]
        text = "\n".join(f"{k}: {v}" for k, v in sorted(props.items()) if k not in ("embedding",))
        return {"id": row_id, "kind": "record", "title": f"{node_id} in {path}", "at": None, "author": None,
                "text": text}

    def suggest_metric_owner(self, metric_id: str) -> dict | None:
        deciders, goal_owners = self._q("metric_owner_candidates", Q.METRIC_OWNER_CANDIDATES, metric=metric_id)[0]
        for pid, name, did in deciders:
            return {"id": pid, "name": name, "reason": f"decided {did}, which promised to move it"}
        for pid, name, gid in goal_owners:
            return {"id": pid, "name": name, "reason": f"owns goal {gid}, which it measures"}
        return None

    def project_owners(self, project_ids: list[str]) -> list[dict]:
        rows = {r[0]: {"id": r[1], "name": r[2]} for r in self._q("project_owners", Q.PROJECT_OWNERS, ids=project_ids)}
        return [rows[p] for p in project_ids if p in rows]

    def _teams(self) -> list[dict]:
        return [{"id": r[0], "name": r[1]} for r in self._q("teams", "MATCH (t:Team) RETURN t.id, t.name")]
