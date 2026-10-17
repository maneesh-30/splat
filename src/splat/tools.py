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
              "have", "want", "considering", "idea", "anyone", "someone", "already", "something"}


def search_terms(text: str) -> str:
    """Full-text query that matches any meaningful word (the index default is all words)."""
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2]
    return "|".join(dict.fromkeys(words))


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
                if r[8] == "active" or r[9] > 0]

    def unowned_metrics(self) -> list[dict]:
        return [{"id": r[0], "name": r[1], "former_owner": r[2], "owner_left_on": r[3], "goals": r[4]}
                for r in self._q("unowned_metrics", Q.UNOWNED_METRICS)]

    def duplicate_work(self) -> list[dict]:
        out = []
        for a, at, ateam, b, bt, bteam, capability in self._q("duplicate_candidates", Q.DUPLICATE_CANDIDATES):
            shared, threads = self._q("project_link", Q.PROJECT_LINK, a=a, b=b)[0]
            out.append({"capability": capability,
                        "projects": [{"id": a, "title": at, "team": ateam}, {"id": b, "title": bt, "team": bteam}],
                        "linked": bool(shared or threads), "shared_people": shared, "shared_threads": threads})
        return out
