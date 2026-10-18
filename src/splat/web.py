"""HTTP API and the single-page UI."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from splat.agent import Agent
from splat.db import DatabaseUnavailable, Settings, connect, get_graph
from splat.tools import Tools

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Splat", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(DatabaseUnavailable)
def database_unavailable(_, exc: DatabaseUnavailable) -> JSONResponse:
    # Say plainly that the graph is unreachable instead of answering from nothing.
    return JSONResponse(status_code=503, content={"error": "database_unavailable", "detail": str(exc)})


def tools() -> Tools:
    return Tools(get_graph())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict:
    settings = Settings()
    db = connect(settings)
    loaded = settings.graph in db.list_graphs()
    return {"database": "ok", "graph": settings.graph, "loaded": loaded}


@app.get("/api/ask")
def ask(q: str = Query(..., min_length=2, max_length=300)) -> dict:
    return asdict(Agent(tools()).ask(q))


@app.get("/api/overview")
def overview() -> dict:
    t = tools()
    data = {
        "as_of": t.as_of.isoformat(),
        "scorecard": t.scorecard(),
        "cascade": t.cascade(),
        "orphaned": t.orphaned_work(),
        "goals_without_work": t.goals_without_work(),
        "cancelled_goal_work": t.cancelled_goal_work(),
        "unowned_metrics": t.unowned_metrics(),
        "duplicates": t.duplicate_work(),
        "load_bearing": t.load_bearing(),
    }
    trace = t.reset_trace()
    data["queries"], data["ms"] = len(trace.queries), trace.total_ms
    return data


@app.get("/api/source/{source_id:path}")
def source(source_id: str) -> dict:
    # Source ids are file paths such as docs/decisions/D-101.md, hence the :path converter.
    found = tools().source(source_id)
    if not found:
        raise HTTPException(404, "No such source in the graph.")
    return found


@app.get("/api/graph")
def graph(ids: str = Query(..., max_length=4000), expand: bool = True) -> dict:
    """The subgraph behind an answer: the given node ids, their neighbours, and the links among them."""
    t = tools()
    data = t.subgraph([i.strip() for i in ids.split(",")][:80], expand=expand)
    trace = t.reset_trace()
    return {**data, "queries": trace.queries, "ms": trace.total_ms}


@app.get("/api/map")
def company_map() -> dict:
    t = tools()
    data = t.company_map()
    trace = t.reset_trace()
    return {**data, "queries": trace.queries, "ms": trace.total_ms}


@app.get("/api/blast/{assumption_id}")
def blast(assumption_id: str) -> dict:
    t = tools()
    data = t.blast_radius(assumption_id.upper())
    trace = t.reset_trace()
    return {**data, "queries": trace.queries, "ms": trace.total_ms}


def run() -> None:
    import os

    import uvicorn
    uvicorn.run("splat.web:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
