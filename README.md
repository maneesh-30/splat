# splat

A company brain built on a FalkorDB knowledge graph.

> Work in progress. Sections below will be filled in as the project is built.

## Quick start

Requirements: Docker, Python 3.11+, [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d        # FalkorDB on :6379, Browser UI on :3000
cp .env.example .env
uv sync
uv run splat                # health check against FalkorDB
```

The FalkorDB image is pinned by digest in `docker-compose.yml`, so every run uses the same build.

## Graph data model

_To be documented._

## Cypher queries and graph algorithms

_To be documented._

## Data

_To be documented._

## AI assistance

_To be documented._
