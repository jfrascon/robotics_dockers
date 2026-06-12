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

if [ ! -f "${_ENTRYPOINT_DIR}/99-uid-gid-adapt.sh" ]; then
    echo "Error: ${_ENTRYPOINT_DIR}/99-uid-gid-adapt.sh not found. The image was not built correctly." >&2
    exit 1
fi

# Validate naming convention for all files in entrypoint.d/.
# Every file must be NN-name.sh or NN-name.txt (NN = exactly two digits).
for _f in "${_ENTRYPOINT_DIR}"/*; do
    [ ! -e "${_f}" ] && continue
    _basename="$(basename "${_f}")"
    if [[ ! "${_basename}" =~ ^[0-9]{2}-.+\.(sh|txt)$ ]]; then
        echo "Error: '${_basename}' in ${_ENTRYPOINT_DIR} does not follow the naming convention NN-name.sh|txt (NN must be exactly two digits, e.g. 01, 50, 99)" >&2
        exit 1
    fi
done

declare -a _PARTS=( "${_ENTRYPOINT_DIR}"/*@(.txt|.sh) )
shopt -u nullglob extglob

for _file in "${_PARTS[@]}"; do
    case "${_file}" in
        *.txt) cat "${_file}" ;;
        *.sh)  source "${_file}" ;;
    esac
done
