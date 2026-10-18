"""FalkorDB connection helpers."""

import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from falkordb import FalkorDB
from falkordb.graph import Graph

load_dotenv()


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("FALKORDB_HOST", "localhost")
    port: int = int(os.getenv("FALKORDB_PORT", "6379"))
    graph: str = os.getenv("FALKORDB_GRAPH", "kestrel")


class DatabaseUnavailable(RuntimeError):
    """Raised when FalkorDB cannot be reached, so callers can say so plainly."""


def connect(settings: Settings | None = None) -> FalkorDB:
    settings = settings or Settings()
    try:
        db = FalkorDB(host=settings.host, port=settings.port)
        db.connection.ping()
    except Exception as exc:
        raise DatabaseUnavailable(
            f"FalkorDB is not reachable at {settings.host}:{settings.port}. "
            "Start it with `docker compose up -d`."
        ) from exc
    return db


def copy_graph(db: FalkorDB, source: str, target: str, attempts: int = 20) -> None:
    """GRAPH.COPY forks the server, and only one fork may run at a time, so a
    periodic background save can make it fail. Wait for the save and retry."""
    for attempt in range(attempts):
        if db.connection.info("persistence").get("rdb_bgsave_in_progress"):
            time.sleep(0.25)
            continue
        try:
            db.select_graph(source).copy(target)
            return
        except Exception as exc:
            if "fork" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(0.25)
    raise RuntimeError(f"could not copy graph {source} to {target}: the server stayed busy saving")


def get_graph(settings: Settings | None = None) -> Graph:
    settings = settings or Settings()
    return connect(settings).select_graph(settings.graph)


def health_check(settings: Settings | None = None) -> str:
    """Confirm FalkorDB answers and report whether the project graph exists yet."""
    settings = settings or Settings()
    db = connect(settings)
    if settings.graph not in db.list_graphs():
        return f"FalkorDB OK at {settings.host}:{settings.port}; graph '{settings.graph}' not loaded yet"
    nodes = db.select_graph(settings.graph).ro_query("MATCH (n) RETURN count(n)").result_set[0][0]
    return f"FalkorDB OK at {settings.host}:{settings.port}; graph '{settings.graph}' has {nodes} nodes"
