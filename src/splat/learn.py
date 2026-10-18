"""Keeping the graph current: read a new message or note, work out what it changes,
try that on a sandbox copy of the graph, and write it only after a person approves.

What a message changes is extracted into fixed fields (epics it mentions,
assumptions it contradicts or supports, each with the words that show it). Every
id is checked against the graph, and every quote must appear in the message;
anything else is dropped. The write itself is a fixed query, never model-written.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from falkordb import FalkorDB

from splat import llm
from splat.db import Settings, copy_graph
from splat.tools import Tools, search_terms

NEGATION = re.compile(r"\b(not|no longer|isn't|aren't|wasn't|weren't|doesn't|didn't|only|never|wrong|false|"
                      r"disproved?|no better|worse|rather than|instead)\b", re.I)

# Words too common to count as shared evidence between a message and an assumption.
FILLER = {"down", "more", "most", "less", "lot", "lots", "much", "many", "very", "really", "just", "also", "still",
          "since", "than", "then", "now", "like", "get", "got", "one", "two", "three"}

PROPOSALS: dict[str, "Proposal"] = {}


@dataclass
class Finding:
    id: str
    statement: str
    quote: str


@dataclass
class Proposal:
    id: str
    text: str
    author: str
    channel: str
    at: str
    source_id: str
    mentions: list[dict] = field(default_factory=list)
    contradicts: list[Finding] = field(default_factory=list)
    supports: list[Finding] = field(default_factory=list)
    read_by: str = "keywords"


def _catalogue(tools: Tools) -> tuple[dict, dict]:
    rows = tools.catalogue()
    assumptions = {r["id"]: r["text"] for r in rows if r["id"].startswith("A-")}
    epics = {r["id"]: r["text"] for r in rows if r["id"].startswith("EP-")}
    return assumptions, epics


def _keyword_read(text: str, tools: Tools, assumptions: dict, epics: dict):
    low = text.lower()
    mentions = [eid for eid, title in epics.items() if title.lower() in low]
    contradicts, supports = [], []
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+", text.strip()) if s]

    def stems(s: str) -> set[str]:
        return {w[:4] for w in search_terms(s).split("|") if w and w not in FILLER}

    for hit in tools.find("Assumption", text, limit=5):
        statement = assumptions.get(hit["id"], hit["text"])
        wanted = stems(statement)
        # Evidence is a sentence sharing at least two meaningful words with the assumption.
        evidence = [s for s in sentences if len(wanted & stems(s)) >= 2 and len(wanted & stems(s)) / len(wanted) >= 0.3]
        if not evidence:
            continue
        negated = [s for s in evidence if NEGATION.search(s)]
        if negated:
            contradicts.append(Finding(hit["id"], statement, negated[0]))
        else:
            supports.append(Finding(hit["id"], statement, evidence[0]))
    return mentions, contradicts, supports


def _model_read(text: str, assumptions: dict, epics: dict):
    system = (
        "You read one new company message and report what it says about recorded assumptions and epics. "
        "Reply with JSON: {\"mentions\": [epic ids], \"contradicts\": [{\"id\": assumption id, \"quote\": exact words "
        "from the message}], \"supports\": [same shape]}. Only use ids from the lists. A quote must be copied exactly "
        "from the message. Report an assumption only if the message gives evidence about it; otherwise leave it out.\n"
        "Assumptions:\n" + "\n".join(f"{k}: {v}" for k, v in assumptions.items())
        + "\nEpics:\n" + "\n".join(f"{k}: {v}" for k, v in epics.items()))
    try:
        out = llm._chat(system, text)
    except Exception as exc:
        llm._log("reading a message", exc)
        return None

    def findings(items):
        kept = []
        for item in items or []:
            aid, quote = item.get("id"), (item.get("quote") or "").strip()
            # Keep only real assumptions, backed by words that really are in the message.
            if aid in assumptions and quote and quote.lower() in text.lower():
                kept.append(Finding(aid, assumptions[aid], quote))
        return kept

    mentions = [e for e in out.get("mentions") or [] if e in epics]
    return mentions, findings(out.get("contradicts")), findings(out.get("supports"))


def read(tools: Tools, text: str, author: str, channel: str, use_llm: bool = True) -> Proposal:
    assumptions, epics = _catalogue(tools)
    result, read_by = None, "keywords"
    if use_llm and llm.available():
        result = _model_read(text, assumptions, epics)
        read_by = "llm" if result else read_by
    mentions, contradicts, supports = result or _keyword_read(text, tools, assumptions, epics)
    at = tools.as_of.isoformat()
    pid = f"learn-{int(time.time() * 1000)}"
    channel = re.sub(r"[^a-z0-9-]", "", channel.lower().lstrip("#")) or "general"
    proposal = Proposal(id=pid, text=text.strip(), author=author, channel=channel, at=at,
                        source_id=f"slack/{channel}/{at}/new-{pid[-6:]}",
                        mentions=[{"id": e, "title": epics[e]} for e in dict.fromkeys(mentions)],
                        contradicts=contradicts, supports=supports, read_by=read_by)
    PROPOSALS[pid] = proposal
    return proposal


WRITE = """
MERGE (s:Source {id: $source})
SET s.kind = 'slack', s.title = $title, s.at = $at, s.text = $text
WITH s
OPTIONAL MATCH (p:Person {id: $author})
FOREACH (x IN CASE WHEN p IS NULL THEN [] ELSE [p] END | MERGE (x)-[:AUTHORED]->(s) MERGE (x)-[:POSTED_IN]->(s))
WITH s
UNWIND (CASE WHEN size($mentions) = 0 THEN [null] ELSE $mentions END) AS eid
OPTIONAL MATCH (e:Project {id: eid})
FOREACH (x IN CASE WHEN e IS NULL THEN [] ELSE [e] END | MERGE (s)-[:MENTIONS]->(x))
WITH DISTINCT s
UNWIND (CASE WHEN size($contradicts) = 0 THEN [null] ELSE $contradicts END) AS aid
OPTIONAL MATCH (a:Assumption {id: aid})
FOREACH (x IN CASE WHEN a IS NULL THEN [] ELSE [a] END | MERGE (x)-[:CONTRADICTED_BY {at: $at}]->(s))
WITH DISTINCT s
UNWIND (CASE WHEN size($supports) = 0 THEN [null] ELSE $supports END) AS aid
OPTIONAL MATCH (a:Assumption {id: aid})
FOREACH (x IN CASE WHEN a IS NULL THEN [] ELSE [a] END | MERGE (x)-[:SUPPORTED_BY]->(s))
RETURN s.id
"""


def _params(p: Proposal) -> dict:
    return {"source": p.source_id, "title": f"#{p.channel}: {p.text[:60]}", "at": p.at, "text": p.text,
            "author": p.author, "mentions": [m["id"] for m in p.mentions],
            "contradicts": [f.id for f in p.contradicts], "supports": [f.id for f in p.supports]}


def _effect(tools: Tools, p: Proposal) -> dict:
    """What the graph says about the affected assumptions: decisions now flagged."""
    flagged = []
    for f in p.contradicts:
        for a in tools.cascade(f.id):
            for d in a["decisions"]:
                flagged.append({**d, "assumption": f.id})
    reach = {f.id: len(tools.blast_radius(f.id)["items"]) for f in p.contradicts}
    disproved = {a["id"] for a in tools.cascade()}
    return {"disproved": sorted(disproved), "flagged": flagged, "reach": reach}


def dry_run(db: FalkorDB, settings: Settings, p: Proposal) -> dict:
    live = db.select_graph(settings.graph)
    before = _effect(Tools(live), p)
    sandbox_name = f"{settings.graph}__sandbox__{p.id}"
    if sandbox_name in db.list_graphs():
        db.select_graph(sandbox_name).delete()
    copy_graph(db, settings.graph, sandbox_name)
    sandbox = db.select_graph(sandbox_name)
    try:
        sandbox.query(WRITE, _params(p))
        after = _effect(Tools(sandbox), p)
    finally:
        sandbox.delete()
    return {"proposal": asdict(p), "before": before, "after": after}


def apply(db: FalkorDB, settings: Settings, p: Proposal) -> dict:
    live = db.select_graph(settings.graph)
    before = _effect(Tools(live), p)
    live.query(WRITE, _params(p))
    db.connection.save()
    after = _effect(Tools(live), p)
    PROPOSALS.pop(p.id, None)
    stored = live.ro_query("MATCH (s:Source {id: $id}) RETURN count(s)", {"id": p.source_id}).result_set[0][0]
    return {"proposal": asdict(p), "before": before, "after": after, "verified": stored == 1}
