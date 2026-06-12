#!/usr/bin/env bash

# Gather and execute parts from /etc/entrypoint.d/ in alphabetical (numeric prefix) order.
# - .sh  files are sourced so they share this process and can set variables used below.
# - .txt files are printed to stdout (useful for banners or license notices).
# /etc/entrypoint.d/ is system-level (not user-specific) so it is accessible regardless
# of which user the container starts as (typically root for UID/GID adaptation).

_ENTRYPOINT_DIR="/etc/entrypoint.d"
_CHECK_SCRIPT="/usr/local/bin/check_entrypoint_d"

# Verify the validation script itself is present and executable before using it.
if [ ! -f "${_CHECK_SCRIPT}" ]; then
    echo "Error: ${_CHECK_SCRIPT} not found. The image was not built correctly." >&2
    exit 1
fi

if [ ! -x "${_CHECK_SCRIPT}" ]; then
    echo "Error: ${_CHECK_SCRIPT} is not executable. The image was not built correctly." >&2
    exit 1
fi

# Validate the entrypoint.d directory: existence, non-empty, required scripts, naming convention.
"${_CHECK_SCRIPT}" "${_ENTRYPOINT_DIR}" || exit 1

# nullglob: if a glob pattern matches no files, expand to nothing (empty) instead of
#           keeping the unexpanded pattern as a literal string in the result.
# extglob: enables extended glob syntax, needed for *@(.txt|.sh) used to collect scripts.
shopt -s nullglob extglob

# Collect all .sh and .txt files in alphabetical order.
declare -a _PARTS=( "${_ENTRYPOINT_DIR}"/*@(.txt|.sh) )

# nullglob and extglob are no longer needed after the glob expansion.
# Deactivating them avoids unintended side effects in the sourced scripts.
shopt -u nullglob extglob

for _file in "${_PARTS[@]}"; do
    case "${_file}" in
        *.txt) cat "${_file}" ;;
        *.sh)  source "${_file}" ;;
    esac
done
