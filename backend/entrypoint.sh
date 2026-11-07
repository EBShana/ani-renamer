#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" appuser 2>/dev/null || true
    usermod -o -u "$PUID" appuser 2>/dev/null || true
    chown -R appuser:appuser /config 2>/dev/null || true
    exec gosu appuser "$@"
fi

exec "$@"
