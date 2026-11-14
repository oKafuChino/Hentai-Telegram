#!/bin/sh
set -e

PUID=${APP_UID:-1000}
PGID=${APP_GID:-1000}

# If running as root, adjust user/group and fix directory permissions
if [ "$(id -u)" = "0" ]; then
    if [ "$(id -u app 2>/dev/null)" != "$PUID" ]; then
        usermod -o -u "$PUID" app 2>/dev/null || true
    fi
    if [ "$(id -g app 2>/dev/null)" != "$PGID" ]; then
        groupmod -o -g "$PGID" app 2>/dev/null || true
    fi

    mkdir -p /app-data /storage
    chown -R app:app /app-data /storage 2>/dev/null || true
    chmod -R u+rwX /app-data /storage 2>/dev/null || true

    if [ "$1" = "run" ] || [ "$1" = "check-config" ] || [ "$1" = "check-channel" ] || [ "$1" = "check-storage" ] || [ "$1" = "check-source" ] || [ "$1" = "health" ] || [ "$1" = "backup" ] || [ "${1#-}" != "$1" ]; then
        set -- python -m hentai_telegram.main "$@"
    fi
    exec gosu app "$@"
else
    if [ "$1" = "run" ] || [ "$1" = "check-config" ] || [ "$1" = "check-channel" ] || [ "$1" = "check-storage" ] || [ "$1" = "check-source" ] || [ "$1" = "health" ] || [ "$1" = "backup" ] || [ "${1#-}" != "$1" ]; then
        set -- python -m hentai_telegram.main "$@"
    fi
    exec "$@"
fi
