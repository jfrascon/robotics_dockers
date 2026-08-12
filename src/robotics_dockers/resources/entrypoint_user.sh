#!/usr/bin/env bash
# Prepares the final user environment and then executes the requested command.

# shellcheck disable=SC1091
[ -f "${HOME}/.env.rc" ] && . "${HOME}/.env.rc" || exit 1

exec "$@"
