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
from splat.tools import Tools, row_source, search_terms

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
TEAMS = {"platform", "payments", "growth", "mobile", "data"}

ROUTES = [
    ("team_work", r"(what|which).*\b(platform|payments|growth|mobile|data)\b.*(working|doing|up to|busy)|team.*working on"),
    ("who_knows", r"who (knows|understands)|expert|who (should|can|do) (i|we) (ask|talk)|who to (ask|talk)|knows the most"),
    ("changes", r"what changed|what's new|what is new|recent changes|changed recently|lately"),
    ("load_bearing", r"load.?bearing|verify first|check first|riskiest|untested|most .*depend"),
    ("blast_radius", r"blast|what breaks|downstream|ripple|knock.?on|relies on|rely on|depends on a-|a-\d+ .*(wrong|false)"),
    ("cascade", r"assumption|wrong|disprove|false|still true|still hold"),
    ("duplicate_work", r"duplicat|same thing|already build|two teams|overlap"),
    ("cancelled_goal_work", r"cancel"),
    ("unowned_metrics", r"own(s|er|ed)?\b.*metrics|metrics.*own|nobody own|unowned"),
    ("who_owns", r"who owns|owner of|who is responsible|responsible for|accountable for|whose"),
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
                 "impact", "succeed", "pay", "off", "project", "exist", "exists", "has", "have", "been",
                 "goal", "metric", "epic", "initiative"}
MIN_COVERAGE = 0.6


def route(question: str) -> str:
    q = question.lower()
    for tool, pattern in ROUTES:
        if re.search(pattern, q):
            return tool
    return "verdict"


# Tools that are about one specific item, which the model may name by id.
SUBJECT_TOOLS = {"verdict", "blast_radius", "why", "cascade", "who_owns", "team_work"}


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
        elif subject and tool == "team_work" and subject.strip().lower() in TEAMS:
            argument = subject.strip().lower()
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
        return Answer("load_bearing", "Assumptions to verify first", lines, ranked,
                      sources=[row_source(a["id"]) for a in untested[:5]])

    def _who_knows(self, question: str) -> Answer:
        k = self.tools.who_knows(question)
        if not k["people"]:
            return self._not_found("who_knows", "thread, document, ticket or decision")
        names = {"decided": "decided", "ticket": "worked ticket", "authored": "wrote", "replied": "replied in",
                 "epic": "worked on epic"}
        lines, sources = [], []
        for n, p in enumerate(k["people"], start=1):
            proof = "; ".join(f"{names[e['kind']]} {e['id']}" for e in p["evidence"][:4])
            lines.append(f"{n}. {p['name']} ({p['team']}), evidence score {p['score']:g}: {proof}.")
            sources += [e["source"] for e in p["evidence"][:4] if e["source"]]
        for p in k["former"]:
            lines.append(f"Also knew it but left on {p['left_on']}: {p['name']} ({len(p['evidence'])} pieces of evidence).")
        return Answer("who_knows", "Who knows about this, ranked by evidence", lines, k, sources=sources,
                      path=["(Person)-[:DECIDED|AUTHORED|POSTED_IN|ASSIGNED_TO]-(matching decision, document, thread or ticket)"])

    def _who_owns(self, question: str) -> Answer:
        team = next((t for t in TEAMS if re.search(rf"\b{t}\b", question.lower())), None)
        node = None
        m = ID_PATTERN.search(question)
        if m and m.group(1).upper().split("-")[0] in ("EP", "G", "D"):
            node = m.group(1).upper()
        if not node:
            # Look first for the kind of thing the question names, e.g. "the churn goal".
            labels = ["Project", "Metric", "Goal", "Decision"]
            named = {"goal": "Goal", "metric": "Metric", "decision": "Decision", "epic": "Project", "project": "Project"}
            for word, label in named.items():
                if re.search(rf"\b{word}\b", question.lower()):
                    labels.insert(0, labels.pop(labels.index(label)))
            for label in labels:
                node = self._resolve(question, label)
                if node:
                    break
        if not node and team:
            return self._team_work(team)
        if not node:
            return self._not_found("who_owns", "epic, metric, goal or decision")
        o = self.tools.who_owns(node)
        if not o["found"]:
            return self._not_found("who_owns", "epic, metric, goal or decision")
        lines = []
        if o["kind"] == "Epic":
            lines.append(f"Owned by the {o['team']} team ({len(o['members'])} current members).")
            for name, did, dtitle in o["deciders"]:
                lines.append(f"Created by decision {did} ({dtitle}), decided by {name}.")
            if o["goal"]:
                lines.append(f"Serves {o['initiative']} toward goal {o['goal']}, owned by {o['goal_owner']}.")
            else:
                lines.append("It serves no goal.")
        elif o["kind"] == "Metric":
            current = [x["name"] for x in o["owners"] if x["current"]]
            gone = [x["name"] for x in o["owners"] if not x["current"]]
            lines.append(f"Owned by {', '.join(current)}." if current else "Nobody currently owns it.")
            if gone:
                lines.append(f"Former owner(s) who left: {', '.join(gone)}.")
            if o["goals"]:
                lines.append(f"Measures {', '.join(o['goals'])}.")
        else:
            team_note = f" ({o['owner_team']})" if o.get("owner_team") else " (no longer at the company)"
            lines.append(f"Owned by {o['owner']}{team_note}." if o.get("owner") else "No owner is recorded.")
            if o["kind"] == "Goal":
                lines.append(f"Status: {o['status']}. Pursued through {', '.join(o['initiatives']) or 'no initiative'}.")
        return Answer("who_owns", f"Who owns {o['id']} {o['title']}", lines, o, sources=o["sources"])

    def _team_work(self, question: str) -> Answer:
        team = question if question in TEAMS else next(
            (t for t in TEAMS if re.search(rf"\b{t}\b", question.lower())), None)
        if not team:
            return self._not_found("team_work", "team")
        w = self.tools.team_work(team)
        if not w["found"]:
            return self._not_found("team_work", "team")
        lines = [f"{len(w['members'])} current members: {', '.join(w['members'])}."]
        for e in w["epics"]:
            lines.append(f"Active epic {e['id']} {e['title']}: {len(e['open_tasks'])} open tickets.")
            lines += [f"  {t}" for t in e["open_tasks"][:4]]
        if not w["epics"]:
            lines.append("No active epics.")
        for d in w["decisions"][:4]:
            lines.append(f"Recent decision {d['id']} {d['title']} ({d['decided_at']}, {d['status']}).")
        sources = [row_source(e["id"]) for e in w["epics"]] + [d["source"] for d in w["decisions"][:4] if d["source"]]
        return Answer("team_work", f"What the {w['team']} team is working on", lines, w, sources=sources)

    def _changes(self, question: str) -> Answer:
        m = re.search(r"(\d+)\s*(day|week|month)", question.lower())
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)] if m else 90
        c = self.tools.changes(days)
        lines = [f"{x['at']}  {x['kind']}: {x['id']} {x['what']}" for x in c["items"]]
        return Answer("changes", f"What changed between {c['since']} and {c['until']}", lines or ["Nothing recorded."],
                      c, sources=[x["source"] for x in c["items"] if x["source"]])

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
        sources = [row_source(p["id"]) for p in o["projects"]] + [row_source(t["id"]) for t in o["tasks"]]
        return Answer("orphaned_work", f"{len(o['projects'])} active projects and {len(o['tasks'])} open tasks "
                      "serve no active goal", lines, o, sources=sources)

    def _goals_without_work(self, question: str) -> Answer:
        g = self.tools.goals_without_work()
        lines = [f"{x['id']} {x['title']} (owner {x['owner']}). Past work: {', '.join(x['past_work']) or 'none'}"
                 for x in g]
        return Answer("goals_without_work", f"{len(g)} active goals have no active work", lines, g,
                      sources=[row_source(x["id"]) for x in g])

    def _cancelled_goal_work(self, question: str) -> Answer:
        c = self.tools.cancelled_goal_work()
        lines = [f"{x['goal']['id']} {x['goal']['title']} was cancelled on {x['goal']['cancelled_at']}, but "
                 f"{x['initiative']['id']} is still {x['initiative']['status']} and {x['project']['id']} "
                 f"{x['project']['title']} gained {x['tasks_after_cancel']} tasks after the cancellation."
                 for x in c]
        return Answer("cancelled_goal_work", "Work still running under cancelled goals", lines, c,
                      sources=[s for x in c for s in (row_source(x["goal"]["id"]), row_source(x["project"]["id"]))],
                      path=[f"{x['project']['id']} -PART_OF-> {x['initiative']['id']} -SERVES-> {x['goal']['id']}"
                            for x in c])

    def _unowned_metrics(self, question: str) -> Answer:
        u = self.tools.unowned_metrics()
        lines = [f"{x['name']}: " + (f"owner {x['former_owner']} left on {x['owner_left_on']}"
                                     if x["former_owner"] else "nobody has ever owned it")
                 + (f"; tracks {', '.join(x['goals'])}" if x["goals"] else "") for x in u]
        return Answer("unowned_metrics", f"{len(u)} metrics have no current owner", lines, u,
                      sources=[row_source(x["id"]) for x in u])

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
        return Answer("duplicate_work", f"{len(d)} pairs of teams are building the same thing", lines, d,
                      sources=[row_source(p["id"]) for x in d for p in x["projects"]])
