"""Optional language model, used for two things only:

1. Understanding a question in any wording and choosing one of the fixed graph
   tools (and what it is about). It never writes a query.
2. Rewriting a finished answer as a short paragraph. Every id and number in that
   paragraph is checked against the rows the graph returned; a sentence that
   mentions anything else is dropped.

Without GROQ_API_KEY, or if the model fails, the keyword router and the plain
answer are used, so the product never depends on the model being up.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv(".env")

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TIMEOUT = 15

TOOLS = {
    "verdict": "Did a specific decision work? Needs the decision as subject.",
    "scorecard": "How did all decisions do overall?",
    "cascade": "Which assumptions turned out false, and which decisions rested on them?",
    "blast_radius": "What relies on a specific assumption / what breaks if it is wrong? Needs the assumption as subject.",
    "load_bearing": "Which untested assumptions should be verified first?",
    "similar": "Has something like a proposed idea been tried before, and how did it go?",
    "why": "Why does a specific epic, ticket or initiative exist / which goal does it serve? Needs it as subject.",
    "orphaned_work": "Which work serves no live goal?",
    "goals_without_work": "Which goals have nobody working on them?",
    "cancelled_goal_work": "What work continues under cancelled goals?",
    "unowned_metrics": "Which metrics have no owner?",
    "duplicate_work": "Are teams building the same thing?",
    "who_knows": "Who knows about a topic / who should I ask about something? Subject: null (the topic is read from the question).",
    "who_owns": "Who owns or is responsible for a specific epic, metric, goal or decision? Needs it as subject.",
    "team_work": "What is a team working on / doing? Subject: the team name in lower case (platform, payments, growth, mobile, data).",
    "changes": "What changed recently in the company?",
}


def available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def _chat(system: str, user: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "reasoning_effort": "low",
    }).encode()
    headers = {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}", "Content-Type": "application/json",
               "User-Agent": "splat"}
    for attempt in range(2):
        try:
            request = urllib.request.Request(URL, data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=TIMEOUT) as res:
                return json.loads(json.load(res)["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as exc:
            # The free tier limits tokens per minute; wait briefly once, then give up.
            if exc.code == 429 and attempt == 0:
                time.sleep(min(float(exc.headers.get("retry-after") or 2), 4))
                continue
            raise
    raise RuntimeError("unreachable")


def _log(what: str, exc: Exception) -> None:
    print(f"[llm] {what} failed, falling back: {exc}", file=sys.stderr)


def route(question: str, catalogue: str) -> tuple[str, str | None] | None:
    """Pick a tool and its subject. Returns None when the model is unsure or fails."""
    system = (
        "You route questions about a company's decisions, assumptions and work to exactly one tool. "
        "You never answer the question yourself. Reply with JSON: "
        '{"tool": "<tool name>", "subject": "<id from the catalogue, or null>"}.\n'
        "Tools:\n" + "\n".join(f"- {name}: {desc}" for name, desc in TOOLS.items())
        + "\nUse an id from the catalogue as subject only when the question clearly refers to that item. "
        "If no item matches, use null; do not guess.\n\nCatalogue:\n" + catalogue)
    try:
        out = _chat(system, question)
    except Exception as exc:
        _log("routing", exc)
        return None
    tool = out.get("tool")
    if tool not in TOOLS:
        return None
    subject = out.get("subject")
    return tool, subject if isinstance(subject, str) and subject.strip() else None


def summarize(question: str, title: str, lines: list[str]) -> str | None:
    system = (
        "Rewrite a graph query result as a short, plain-English answer of two to four sentences. "
        "Use only facts present in the result. Keep ids such as D-101 or A-1 exactly as written. "
        "Do not add numbers, names, causes or advice that are not in the result. "
        'Reply with JSON: {"answer": "<text>"}.')
    # The answer's own lines carry its facts; sending the raw rows too would only
    # burn the free tier's tokens-per-minute budget. Verification still uses them.
    result = "\n".join([title, *lines])[:5000]
    try:
        return _chat(system, f"Question: {question}\n\nResult:\n{result}").get("answer")
    except Exception as exc:
        _log("summary", exc)
        return None


ID = re.compile(r"\b(?:D|A|EP|G|I|KES|GH|INC)-\d+\b")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def verify(text: str, evidence: str) -> tuple[str, int]:
    """Keep only sentences whose ids and numbers all appear in the evidence."""
    known_numbers = set(NUMBER.findall(evidence))
    # Percentages are stored as fractions (-0.1) and shown as -10.0%, so accept both forms.
    for n in list(known_numbers):
        try:
            value = float(n)
        except ValueError:
            continue
        for scaled in (value * 100, value / 100):
            known_numbers.add(f"{scaled:g}")
            known_numbers.add(f"{scaled:.1f}")
    kept, dropped = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        ids_ok = all(i in evidence for i in ID.findall(sentence))
        numbers = [n for n in NUMBER.findall(re.sub(ID, "", sentence)) if not re.fullmatch(r"20\d\d", n)]
        numbers_ok = all(n in known_numbers or n.rstrip("0").rstrip(".") in known_numbers for n in numbers)
        if ids_ok and numbers_ok:
            kept.append(sentence)
        else:
            dropped += 1
    return " ".join(kept), dropped
