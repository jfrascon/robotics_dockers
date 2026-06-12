#!/usr/bin/env bash

# Gather and execute parts from /etc/entrypoint.d/ in alphabetical (numeric prefix) order.
# - .sh  files are sourced so they share this process and can set variables used below.
# - .txt files are printed to stdout (useful for banners or license notices).
# /etc/entrypoint.d/ is system-level (not user-specific) so it is accessible regardless
# of which user the container starts as (typically root for UID/GID adaptation).
shopt -s nullglob extglob
_ENTRYPOINT_DIR="/etc/entrypoint.d"

if [ ! -d "${_ENTRYPOINT_DIR}" ]; then
    echo "Error: ${_ENTRYPOINT_DIR} not found. The image was not built correctly." >&2
    exit 1
fi

if [ ! -f "${_ENTRYPOINT_DIR}/00-checks.sh" ] || [ ! -f "${_ENTRYPOINT_DIR}/99-uid-gid-adapt.sh" ]; then
    echo "Error: ${_ENTRYPOINT_DIR} is missing required scripts (00-checks.sh and/or 99-uid-gid-adapt.sh)." >&2
    exit 1
fi

declare -a _PARTS=( "${_ENTRYPOINT_DIR}"/*@(.txt|.sh) )
shopt -u nullglob extglob

for _file in "${_PARTS[@]}"; do
    case "${_file}" in
        *.txt) cat "${_file}" ;;
        *.sh)  source "${_file}" ;;
    esac
done
