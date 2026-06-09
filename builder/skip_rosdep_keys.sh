#!/usr/bin/env bash

log() {
  local type="${1:-info}"
  local message="${2:-}"
  printf '[%s] [%s] %s\n' \
    "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" \
    "${type}" \
    "${message}"
}

handle_error() {
  local exit_code="${1:-1}"
  local error_message="${2:-Unknown error}"

  log error "${error_message} (exit code: ${exit_code})"
  exit "${exit_code}"
}

usage() {
    cat <<EOF
Usage:
  ${script_name} KEYS_FILE

Positional arguments:
  KEYS_FILE  Path to a file containing rosdep keys to ignore, one per line.
             Empty lines and lines starting with '#' are ignored.

Options:
  --help           Show this help and exit
EOF
}

# Trim leading and trailing whitespace from a string.
trim() {
    local value="${1}"

    # Remove leading whitespace
    value="${value#"${value%%[![:space:]]*}"}"
    # Remove trailing whitespace
    value="${value%"${value##*[![:space:]]}"}"

    # Return the trimmed value
    printf '%s' "${value}"
}

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script writes under /etc/ros, so it must run as root.
[ "$(id --user)" -ne 0 ] && handle_error 1 "root user must be active to run the script '${script_name}'"

# Process options, and if --help is present, show usage and exit. The remaining arguments are
# expected to be the keys CSV.
for arg in "$@"; do
    case "${arg}" in
    --help | -h)
        usage
        exit 0
        ;;
    esac
done

# Check that there is exactly one argument, which is expected to be the path to the keys file.
[ "$#" -gt 1 ] && handle_error 1 "Too many arguments"

# The first argument is expected to be the path to the keys file.
keys_file="${1:-}"

[ -z "${keys_file}" ] && handle_error 1 "No keys file provided"

[ ! -f "${keys_file}" ] && handle_error 1 "Keys file '${keys_file}' not found"

# Read keys from the file, one per line. Empty lines and comments are skipped.
normalized_keys=()
while IFS= read -r raw_key || [ -n "${raw_key}" ]; do
    # Skip empty lines and comments.
    [[ -z "${raw_key}" || "${raw_key}" =~ ^# ]] && continue

    trimmed_key="$(trim "${raw_key}")"
    [ -z "${trimmed_key}" ] && continue

    normalized_keys+=("${trimmed_key}: {ubuntu: []}")
done <"${keys_file}"

# If there are no normalized keys, log a message and exit with a zero status, since there is
# nothing to do.
[ "${#normalized_keys[@]}" -eq 0 ] && {
    log info "No rosdep keys found in '${keys_file}'. Nothing to do."
    exit 0
}

# Keys to ignore must be written in a yaml file, and then that file (absolute path) must be
# inserted in a file under the directory `/etc/ros/rosdep/sources.list.d`.
# Files under the directory `/etc/ros/rosdep/sources.list.d` listing yaml files that contain rosdep
# keys to ignored are called index files.
# The command `rosdep` will read the index files in the directory `/etc/ros/rosdep/sources.list.d`
# to find the yaml files that contain rosdep keys to ignore, and then read those yaml files to get
# the rosdep keys to ignore.
# With this mechanism, those keys are always ignored by rosdep in this system.

# It is a design decision of this script to use a single yaml file to store the all rosdep keys to
# ignore, and a single index file to point to that yaml file.
# This means that if the script is run multiple times with different keys, the new keys will be
# added to the same yaml file, and the same index file will point to that yaml file.
# It is also a design decision to store the file with the keys to ignore under the path
# `/etc/ros/rosdep`, since there is no requirement for that file to be there.
# However, the index file must be stored under the directory `/etc/ros/rosdep/sources.list.d`, since
# that is where rosdep expects to find the index files.
rosdep_ignored_keys_file="/etc/ros/rosdep/rosdep_ignored_keys.yaml"
etc_rosdep_sources_dir="/etc/ros/rosdep/sources.list.d"
rosdep_ignored_keys_index_file="${etc_rosdep_sources_dir}/00-rosdep-ignored-keys-index.list"

# Be sure paths and files exist before trying to write to them.
mkdir --verbose --parent "${etc_rosdep_sources_dir}"
touch "${rosdep_ignored_keys_file}"

# For each normalized key, check if it already exists in the yaml file. If it does not exist, add it
# to the yaml file. This allows the script to be idempotent, since running it multiple times with
# the same keys will not result in duplicate entries in the yaml file.
for normalized_key in "${normalized_keys[@]}"; do
    if grep --quiet --line-regexp --fixed-strings \
        "${normalized_key}" "${rosdep_ignored_keys_file}"; then
        log info "Rosdep key '${normalized_key}' already registered in '${rosdep_ignored_keys_file}'"
    else
        printf '%s\n' "${normalized_key}" >>"${rosdep_ignored_keys_file}"
        log info "Added rosdep key '${normalized_key}' to '${rosdep_ignored_keys_file}'"
    fi
done

# Only add the yaml file to the rosdep index when the yaml file actually contains ignored keys.
# If the yaml file is already listed in the index file, do not add it again.
# This allows the script to be idempotent, since running it multiple times with the same keys will
# not result in duplicate entries in the index file.
if [ -s "${rosdep_ignored_keys_file}" ]; then
    touch "${rosdep_ignored_keys_index_file}"
    index_line="yaml file://${rosdep_ignored_keys_file}"

    if grep --quiet --line-regexp --fixed-strings \
        "${index_line}" "${rosdep_ignored_keys_index_file}"; then
        log info "Index entry '${index_line}' already exists in '${rosdep_ignored_keys_index_file}'"
    else
        printf '%s\n' "${index_line}" >>"${rosdep_ignored_keys_index_file}"
        log info "Added index entry '${index_line}' to '${rosdep_ignored_keys_index_file}'"
    fi
fi
