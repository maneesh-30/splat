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
    }
    trace = t.reset_trace()
    data["queries"], data["ms"] = len(trace.queries), trace.total_ms
    return data


@app.get("/api/source/{source_id}")
def source(source_id: str) -> dict:
    found = tools().source(source_id)
    if not found:
        raise HTTPException(404, "No such source in the graph.")
    return found


def run() -> None:
    import os

    import uvicorn
    uvicorn.run("splat.web:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
