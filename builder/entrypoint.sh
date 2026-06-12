#!/usr/bin/env bash

# Gather and execute parts from /etc/entrypoint.d/ in alphabetical (numeric prefix) order.
# - .sh  files are sourced so they share this process and can set variables used below.
# - .txt files are printed to stdout (useful for banners or license notices).
# /etc/entrypoint.d/ is system-level (not user-specific) so it is accessible regardless
# of which user the container starts as (typically root for UID/GID adaptation).

_ENTRYPOINT_DIR="/etc/entrypoint.d"
_CHECK_SCRIPT="/usr/local/bin/check_entrypoint_d"

# ---------------------------------------------------------------------------
# Precondition checks (fail early).
#
# This image requires:
#   1. The container must start as root (UID 0).
#   2. HOST_UID and HOST_UPGID must exist, be non-empty integers > 1000.
#
# These are the contract this image imposes on the caller. Any other
# combination is a misconfiguration and must fail with a clear message.
# ---------------------------------------------------------------------------

# validate_id_var <var_name> <var_value>
# Checks that a UID/GID variable is not undefined, not empty, is an integer, and is > 1000.
validate_id_var() {
    local name="${1}"
    local value="${2}"

    if [ -z "${value}" ]; then
        echo "Error: ${name} is undefined or empty. It must be an integer greater than 1000." >&2
        exit 1
    fi

    if ! [[ "${value}" =~ ^[0-9]+$ ]]; then
        echo "Error: ${name} is not an integer (got: '${value}'). It must be an integer greater than 1000." >&2
        exit 1
    fi

    if [ "${value}" -le 1000 ]; then
        echo "Error: ${name} must be greater than 1000 (got: '${value}')." >&2
        exit 1
    fi
}

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this image must be started as root. Current UID: $(id -u)." >&2
    exit 1
fi

validate_id_var "HOST_UID"   "${HOST_UID}"
validate_id_var "HOST_UPGID" "${HOST_UPGID}"

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
