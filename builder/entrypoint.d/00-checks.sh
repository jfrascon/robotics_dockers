#!/usr/bin/env bash
# Validates all files in the entrypoint.d directory before any script runs.
# This script must be sourced (not executed) — it runs in the entrypoint process.

# Every file in entrypoint.d must follow the naming convention: NN-name.sh or NN-name.txt
# where NN is exactly two digits (00-99). Files with three or more digits are rejected.
_entrypoint_d_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

for _f in "${_entrypoint_d_dir}"/*; do
    [ -e "${_f}" ] || continue
    _basename="$(basename "${_f}")"

    if [[ ! "${_basename}" =~ ^[0-9]{2}-.+\.(sh|txt)$ ]]; then
        echo "Error: '${_basename}' in entrypoint.d does not follow the naming convention NN-name.sh|txt (NN must be exactly two digits, e.g. 01, 50, 99)" >&2
        exit 1
    fi
done

# 99-uid-gid-adapt.sh is mandatory — it contains the final exec that starts the user session.
if [ ! -f "${_entrypoint_d_dir}/99-uid-gid-adapt.sh" ]; then
    echo "Error: '${_entrypoint_d_dir}/99-uid-gid-adapt.sh' not found. This script is required." >&2
    exit 1
fi
