#!/bin/sh
set -e

mkdir -p /app/artifacts
# Avoid scanning and mutating every bind-mounted artifact on each start.
chown appuser:appuser /app /app/artifacts /app/rag_database.db 2>/dev/null || true

exec gosu appuser "$@"
