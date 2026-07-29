#!/bin/sh
# Container entrypoint.
#
# Applies migrations, then execs the server. `exec` matters: without it the shell stays PID 1
# and swallows SIGTERM, so the orchestrator's graceful stop becomes a 30-second kill and
# in-flight scanning sockets are severed rather than closed.
set -eu

if [ "${FIGION_RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "applying migrations..."
    # Single-replica-safe only. With several replicas starting at once, run migrations as a
    # separate job and set FIGION_RUN_MIGRATIONS=0 here — concurrent `alembic upgrade` runs
    # race on the version table.
    alembic upgrade head
fi

exec "$@"
