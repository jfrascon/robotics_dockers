#!/usr/bin/env bash
# Prepares the final user environment and then executes the requested command.

if [ ! -f "${HOME}/.env.rc" ]; then
    echo "Error: required environment file '${HOME}/.env.rc' not found" >&2
    exit 1
fi

# shellcheck disable=SC1091
. "${HOME}/.env.rc" || {
    echo "Error: failed to load required environment file '${HOME}/.env.rc'" >&2
    exit 1
}

exec "$@"
