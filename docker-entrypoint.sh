#!/bin/sh
set -e

# Start FalkorDB inside the container. The data is reloaded on every start, so
# nothing needs to persist between restarts.
redis-server --loadmodule /var/lib/falkordb/bin/falkordb.so \
  --save "" --appendonly no --dir /tmp --daemonize yes

until redis-cli ping >/dev/null 2>&1; do sleep 0.2; done

cd /app
uv run --frozen --no-sync splat load
exec uv run --frozen --no-sync splat serve
