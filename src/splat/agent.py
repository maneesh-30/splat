"""Question answering: route a plain-language question to one graph tool, then
turn its rows into an answer that cites sources and shows the path it followed.

With a language model configured, it picks the tool and its subject from the
question's meaning; otherwise, or if the model fails, deterministic keyword
matching does. Either way the answer itself is composed from tool rows only.
An optional model-written summary is checked against those rows, and any
sentence mentioning an id or number they do not contain is dropped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from splat import llm
from splat.tools import Tools, search_terms

VERDICT_LABEL = {
    "worked": "Worked",
    "partial": "Partly worked",
    "missed": "Missed",
    "ambiguous": "Ambiguous",
    "too_early": "Too early to judge",
    "not_shipped": "Not shipped",
    "no_success_metric": "Never defined success",
    "insufficient_data": "Not enough data",
    "unknown": "Not in the graph",
}


@dataclass
class Answer:
    tool: str
    title: str
    lines: list[str]
    data: object
    sources: list[str] = field(default_factory=list)
    path: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    ms: float = 0.0
    router: str = "keywords"
    summary: str | None = None
    summary_dropped: int = 0

    def text(self) -> str:
        return "\n".join([self.title, *self.lines])


# Order matters: the first matching route wins.
ROUTES = [
    ("load_bearing", r"load.?bearing|verify first|check first|riskiest|untested|most .*depend"),
    ("blast_radius", r"blast|what breaks|downstream|ripple|knock.?on|relies on|rely on|depends on a-|a-\d+ .*(wrong|false)"),
    ("cascade", r"assumption|wrong|disprove|false|still true|still hold"),
    ("duplicate_work", r"duplicat|same thing|already build|two teams|overlap"),
    ("cancelled_goal_work", r"cancel"),
    ("unowned_metrics", r"own(s|er|ed)?\b.*metric|metric.*own|nobody own|unowned"),
    ("goals_without_work", r"goal.*(no|without|nobody|idle).*work|neglect|idle goal"),
    ("orphaned_work", r"orphan|no goal|not tied|why are we|wast(e|ed|ing)|pointless|serve(s)? no"),
    ("scorecard", r"scorecard|all decisions|overall|how did our decisions|track record"),
    ("similar", r"tried|before|similar|propos|should we|thinking of|plan to"),
    ("verdict", r"work(ed)?\b|did .* (help|pay off|succeed)|result|outcome|impact|verdict"),
    ("why", r"\bwhy\b|what is .* for|which goal"),
]
ID_PATTERN = re.compile(r"\b(D-\d+|EP-\d+|KES-\d+|GH-\d+|I-\d+|A-\d+|G-\d+)\b", re.I)

# Words that describe the question rather than the thing being asked about.
GENERIC_WORDS = {"work", "worked", "working", "help", "helped", "decision", "result", "results", "outcome",
                 "impact", "succeed", "pay", "off", "project", "exist", "exists", "has", "have", "been"}
MIN_COVERAGE = 0.6


def route(question: str) -> str:
    q = question.lower()
    for tool, pattern in ROUTES:
        if re.search(pattern, q):
            return tool
    return "verdict"


# Tools that are about one specific item, which the model may name by id.
SUBJECT_TOOLS = {"verdict", "blast_radius", "why", "cascade"}


class Agent:
    _catalogue: str | None = None

    def __init__(self, tools: Tools, use_llm: bool = True):
        self.tools = tools
        self.use_llm = use_llm and llm.available()

    def ask(self, question: str) -> Answer:
        self.tools.reset_trace()
        tool, subject, router = None, None, "keywords"
        if self.use_llm:
            choice = llm.route(question, self.catalogue())
            if choice:
                (tool, subject), router = choice, "llm"
        tool = tool or route(question)
        argument = question
        if subject and tool in SUBJECT_TOOLS and ID_PATTERN.fullmatch(subject.strip()):
            argument = subject.strip().upper()
        answer = getattr(self, f"_{tool}")(argument)
        trace = self.tools.reset_trace()
        answer.trace, answer.ms, answer.router = trace.queries, trace.total_ms, router
        if self.use_llm and answer.data is not None:
            text = llm.summarize(question, answer.title, answer.lines)
            if text:
                evidence = " ".join([answer.title, *answer.lines, json.dumps(answer.data, default=str)])
                answer.summary, answer.summary_dropped = llm.verify(text, evidence)
                answer.summary = answer.summary or None
        return answer

    def catalogue(self) -> str:
        """The ids and names the model may refer to, read from the graph once."""
        if Agent._catalogue is None:
            rows = self.tools.catalogue()
            Agent._catalogue = "\n".join(f"{r['id']}: {r['text']}" for r in rows)
        return Agent._catalogue

    # -- helpers ------------------------------------------------------------------

    def _resolve(self, question: str, label: str) -> str | None:
        """An explicit id wins; otherwise full-text search the graph for the best match.

        A match must cover most of the question's specific words. One shared word is
        not enough: "the blockchain rewrite" must not resolve to "the Go rewrite".
        """
        m = ID_PATTERN.search(question)
        if m:
            return m.group(1).upper()
        hits = self.tools.find(label, question, limit=3)
        terms = [w for w in search_terms(question).split("|") if w and w not in GENERIC_WORDS]
        for hit in hits:
            title = {w[:4] for w in re.findall(r"[a-z0-9]+", hit["text"].lower())}
            covered = sum(1 for t in terms if t[:4] in title)
            if terms and covered / len(terms) >= MIN_COVERAGE:
                return hit["id"]
        return None

    @staticmethod
    def _not_found(tool: str, what: str) -> Answer:
        return Answer(tool, f"Not in the graph: no {what} matches that question.",
                      ["Nothing was guessed. Try naming the decision or project, or use its id."], None)

    # -- outcomes -------------------------------------------------------------------

    def _verdict(self, question: str) -> Answer:
        did = self._resolve(question, "Decision")
        if not did:
            return self._not_found("verdict", "decision")
        v = self.tools.verdict(did)
        if v["verdict"] == "unknown":
            return self._not_found("verdict", "decision")
        return self._verdict_answer(v)

    def _verdict_answer(self, v: dict) -> Answer:
        lines = [v["reason"]]
        if v["verdict"] in ("worked", "partial") and any(a["contradicted_at"] for a in v.get("assumptions", [])):
            lines.append("It moved the metric, but the reason it was made for was later disproved.")
        sources = [v["decided_in"]["id"]]
        path = [f"{v['id']} {v['title']}"]
        if v.get("expected"):
            e = v["expected"]
            path.append(f"-EXPECTS {e['change']:+.1%} by {e['judge_by']}-> {e['metric_name']}")
        if v.get("measured"):
            m = v["measured"]
            lines.append(f"Baseline {m['baseline']} {v['expected']['unit']} "
                         f"({m['baseline_window'][0]} to {m['baseline_window'][1]}), outcome {m['outcome']} {v['expected']['unit']} "
                         f"({m['outcome_window'][0]} to {m['outcome_window'][1]}).")
        for o in v.get("overlaps", []):
            lines.append(f"Overlap: {o['type']} {o['id']} ({o['title']}) at {o['at']}.")
            if o.get("source"):
                sources.append(o["source"])
        for a in v.get("assumptions", []):
            note = (f"disproved on {a['contradicted_at']} by {a['contradicted_by']['id']}"
                    if a["contradicted_at"] else "not disproved")
            lines.append(f"Rested on {a['id']}: {a['statement']} ({note}).")
            path.append(f"-RESTS_ON-> {a['id']}")
            if a["contradicted_by"]:
                sources.append(a["contradicted_by"]["id"])
        lines.append(f"Decided by {v['decider']} on {v['decided_at']}, recorded in {v['decided_in']['id']}.")
        return Answer("verdict", f"{VERDICT_LABEL[v['verdict']]}: {v['id']} {v['title']}", lines, v,
                      sources=sources, path=path)

    def _scorecard(self, question: str) -> Answer:
        s = self.tools.scorecard()
        lines = [f"{VERDICT_LABEL[k]}: {n}" for k, n in sorted(s["counts"].items(), key=lambda kv: -kv[1])]
        undefined = s["counts"].get("no_success_metric", 0)
        lines.append(f"{undefined} of {s['total']} decisions never said what success would look like.")
        return Answer("scorecard", f"Decision scorecard: {s['total']} decisions judged against their own promises",
                      lines, s)

    # -- assumptions ----------------------------------------------------------------

    def _cascade(self, question: str) -> Answer:
        aid = None
        m = ID_PATTERN.search(question)
        if m and m.group(1).upper().startswith("A-"):
            aid = m.group(1).upper()
        found = self.tools.cascade(aid)
        if not found:
            return Answer("cascade", "No disproved assumptions are recorded in the graph.", [], [])
        lines, sources, path = [], [], []
        for a in found:
            lines.append(f"{a['id']} \"{a['statement']}\" was disproved on {a['contradicted_at']} "
                         f"by {a['evidence']['id']} ({a['evidence']['title']}, {a['evidence']['author']}).")
            sources.append(a["evidence"]["id"])
            for d in a["decisions"]:
                flags = []
                if d["decided_after_evidence"]:
                    flags.append("decided AFTER the evidence")
                if d["still_live"]:
                    flags.append("STILL LIVE")
                if d["verdict"] in ("worked", "partial"):
                    flags.append("but not for the stated reason")
                lines.append(f"  {d['id']} {d['title']} ({d['owner']}): {VERDICT_LABEL[d['verdict']]}"
                             + (f"; {', '.join(flags)}" if flags else ""))
                path.append(f"{d['id']} -RESTS_ON-> {a['id']} -CONTRADICTED_BY-> {a['evidence']['id']}")
        return Answer("cascade", "Decisions resting on assumptions that turned out false", lines, found,
                      sources=sources, path=path)

    def _blast_radius(self, question: str) -> Answer:
        m = ID_PATTERN.search(question)
        aid = m.group(1).upper() if m and m.group(1).upper().startswith("A-") else None
        if not aid:
            hits = self.tools.find("Assumption", question, limit=1)
            aid = hits[0]["id"] if hits else None
        if not aid:
            return self._not_found("blast_radius", "assumption")
        b = self.tools.blast_radius(aid)
        if not b["items"]:
            return Answer("blast_radius", f"Nothing in the graph relies on {aid}.", [], b)
        lines = [f"{len(b['decisions'])} decisions, {len(b['epics'])} epics and {len(b['open_tasks'])} open tasks "
                 f"rely on it, up to {b['max_hops']} steps away."]
        for item in b["items"]:
            lines.append(f"  {'  ' * (item['hops'] - 1)}{item['kind']} {item['id']} {item['title']}"
                         + (f" ({item['status']})" if item["status"] else ""))
        if b["goals"]:
            lines.append("Goals at risk: " + "; ".join(b["goals"]) + ".")
        if b["metrics"]:
            lines.append("Metrics it was meant to move: " + ", ".join(b["metrics"]) + ".")
        if b["people"]:
            lines.append("People to tell: " + ", ".join(b["people"]) + ".")
        return Answer("blast_radius", f"What relies on {aid}", lines, b,
                      path=[f"(anything)-[:RELIES_ON*1..{b['max_hops']}]->({aid})"])

    def _load_bearing(self, question: str) -> Answer:
        ranked = self.tools.load_bearing()
        untested = [a for a in ranked if a["status"] == "untested"]
        lines = ["Untested assumptions, ranked by PageRank over everything that relies on them:"]
        for n, a in enumerate(untested[:5], start=1):
            lines.append(f"  {n}. {a['id']} \"{a['statement']}\": {a['dependents']} decisions, epics and "
                         f"open tasks rest on it (score {a['score']}).")
        disproved = [a for a in ranked if a["status"] == "disproved"]
        if disproved:
            lines.append("Already disproved: " + ", ".join(f"{a['id']} ({a['dependents']} dependents)"
                                                         for a in disproved) + ".")
        return Answer("load_bearing", "Assumptions to verify first", lines, ranked)

    def _similar(self, question: str) -> Answer:
        found = self.tools.similar(question)
        if not found:
            return self._not_found("similar", "past decision")
        lines = [f"{d['id']} {d['title']}: {VERDICT_LABEL[d['verdict']]}" for d in found]
        shared = {}
        for d in found:
            for a in d["assumptions"]:
                shared.setdefault((a["id"], a["statement"], a["contradicted_at"]), []).append(d["id"])
        for (aid, statement, contradicted), ids in shared.items():
            if len(ids) > 1 or contradicted:
                lines.append(f"Shared assumption {aid} \"{statement}\" under {', '.join(ids)}"
                             + (f"; disproved on {contradicted}." if contradicted else "."))
        return Answer("similar", "Past decisions like this one, and how they turned out", lines, found)

    # -- goals and work ---------------------------------------------------------------

    def _why(self, question: str) -> Answer:
        m = ID_PATTERN.search(question)
        node = m.group(1).upper() if m else (self._resolve(question, "Project"))
        if not node:
            return self._not_found("why", "task or project")
        w = self.tools.why(node)
        if not w["found"]:
            return self._not_found("why", "task or project")
        if not w["chains"]:
            partial = w.get("partial") or []
            lines = ["No path leads from it to any goal in the graph."]
            if partial:
                lines.append("The trail stops at " + partial[-1] + ", which serves no goal.")
            return Answer("why", f"{w['id']} {w['title']} serves no goal", lines, w,
                          path=[" -> ".join(partial)] if partial else [])
        lines, path = [], []
        for c in w["chains"]:
            path.append(" -> ".join(c["chain"]))
            status = c["goal_status"] + (f" since {c['goal_cancelled_at']}" if c["goal_cancelled_at"] else "")
            lines.append(f"Goal status: {status}.")
        return Answer("why", f"Why {w['id']} {w['title']} exists", lines, w, path=path)

    def _orphaned_work(self, question: str) -> Answer:
        o = self.tools.orphaned_work()
        lines = [f"Project {p['id']} {p['title']} ({p['team']}): "
                 + ("reaches only " + ", ".join(p["reaches"]) if p["reaches"] else "no path to any goal")
                 for p in o["projects"]]
        lines += [f"Task {t['id']} {t['title']} ({t['assignee']}): not part of any project" for t in o["tasks"]]
        return Answer("orphaned_work", f"{len(o['projects'])} active projects and {len(o['tasks'])} open tasks "
                      "serve no active goal", lines, o)

    def _goals_without_work(self, question: str) -> Answer:
        g = self.tools.goals_without_work()
        lines = [f"{x['id']} {x['title']} (owner {x['owner']}). Past work: {', '.join(x['past_work']) or 'none'}"
                 for x in g]
        return Answer("goals_without_work", f"{len(g)} active goals have no active work", lines, g)

    def _cancelled_goal_work(self, question: str) -> Answer:
        c = self.tools.cancelled_goal_work()
        lines = [f"{x['goal']['id']} {x['goal']['title']} was cancelled on {x['goal']['cancelled_at']}, but "
                 f"{x['initiative']['id']} is still {x['initiative']['status']} and {x['project']['id']} "
                 f"{x['project']['title']} gained {x['tasks_after_cancel']} tasks after the cancellation."
                 for x in c]
        return Answer("cancelled_goal_work", "Work still running under cancelled goals", lines, c,
                      path=[f"{x['project']['id']} -PART_OF-> {x['initiative']['id']} -SERVES-> {x['goal']['id']}"
                            for x in c])

    def _unowned_metrics(self, question: str) -> Answer:
        u = self.tools.unowned_metrics()
        lines = [f"{x['name']}: " + (f"owner {x['former_owner']} left on {x['owner_left_on']}"
                                     if x["former_owner"] else "nobody has ever owned it")
                 + (f"; tracks {', '.join(x['goals'])}" if x["goals"] else "") for x in u]
        return Answer("unowned_metrics", f"{len(u)} metrics have no current owner", lines, u)

    def _duplicate_work(self, question: str) -> Answer:
        d = self.tools.duplicate_work()
        lines = []
        for x in d:
            a, b = x["projects"]
            link = ("linked through " + ", ".join(x["shared_people"] + x["shared_threads"])
                    if x["linked"] else "no shared person and no thread mentions both")
            lines.append(f"{a['team']} ({a['id']} {a['title']}) and {b['team']} ({b['id']} {b['title']}) both "
                         f"build \"{x['capability']}\": {link}.")
            if not x["same_island"]:
                groups = " and ".join("{" + ", ".join(i["teams"]) + "}" for i in x["islands"])
                lines.append(f"The two teams are in separate conversation islands ({groups}): no chain of people "
                             "who have talked to each other connects them.")
        return Answer("duplicate_work", f"{len(d)} pairs of teams are building the same thing", lines, d)
