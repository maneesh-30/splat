import argparse
import sys

from splat.db import DatabaseUnavailable, get_graph, health_check


def main() -> None:
    # Windows consoles default to a legacy code page; answers can contain characters it lacks.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="splat", description="Ask the company graph.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health", help="check the FalkorDB connection (default)")
    sub.add_parser("generate", help="regenerate the synthetic company data")
    sub.add_parser("load", help="load the company data into FalkorDB")
    sub.add_parser("serve", help="run the web app on http://localhost:8000")
    ask = sub.add_parser("ask", help="ask a question")
    ask.add_argument("question", nargs="+")
    ask.add_argument("--trace", action="store_true", help="print every query that ran")
    args = parser.parse_args()

    try:
        if args.command == "generate":
            from splat.generate import main as generate
            generate()
        elif args.command == "load":
            from splat.ingest import main as load
            load()
        elif args.command == "serve":
            from splat.web import run
            run()
        elif args.command == "ask":
            from splat.agent import Agent
            from splat.tools import Tools
            answer = Agent(Tools(get_graph())).ask(" ".join(args.question))
            if answer.summary:
                print(f"Summary (checked against the graph): {answer.summary}\n")
            print(answer.text())
            for p in answer.path:
                print(f"  path: {p}")
            if answer.sources:
                print(f"  sources: {', '.join(dict.fromkeys(answer.sources))}")
            print(f"  {len(answer.trace)} graph queries via {answer.tool} (routed by {answer.router}), {answer.ms} ms")
            if args.trace:
                for q in answer.trace:
                    print(f"    [{q['ms']} ms, {q['rows']} rows] {q['cypher']}")
        else:
            print(health_check())
    except DatabaseUnavailable as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
