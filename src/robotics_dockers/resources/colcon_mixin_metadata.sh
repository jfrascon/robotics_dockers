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

    log error "${error_message} (exit code: ${exit_code})" >&2
    exit "${exit_code}"
}

usage() {
    cat <<EOF
Usage:
  ${script_name} TARGET_USER [--help]

Positional arguments:
  TARGET_USER     Target system user name

Options:
  --help          Show this help and exit
EOF
}

# ${BASH_SOURCE:-${0}} uses the current Bash source filename and falls back to
# $0 when BASH_SOURCE is unavailable.
script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script is run by root when building the Docker image.
executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing '${script_name}'"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "Script '${script_name}' must run as UID 0; found UID '${executing_user_id}'"
fi

# Pre-scan: show help if present in any position.
for arg in "$@"; do
    case "$arg" in
    --help | -h)
        usage
        exit 0
        ;;
    esac
done

if [ "$#" -lt 1 ]; then
    handle_error 1 "Missing required positional argument TARGET_USER"
fi

TARGET_USER="${1:-}"
shift

if [ "$#" -gt 0 ]; then
    log warning "unexpected extra arguments: $*"
fi

if [ -z "${TARGET_USER}" ]; then
    handle_error 1 "TARGET_USER is empty"
fi

target_user_entry="$(getent passwd "${TARGET_USER}")" ||
    handle_error 1 "Could not query user '${TARGET_USER}'"

IFS=: read -r _ _ target_user_id target_user_pri_group_id _ target_user_home _ <<<"${target_user_entry}"

if [ -z "${target_user_id}" ]; then
    handle_error 1 "User record for '${TARGET_USER}' has an empty UID field"
fi

if [ -z "${target_user_pri_group_id}" ]; then
    handle_error 1 "User record for '${TARGET_USER}' has an empty primary GID field"
fi

if [ -z "${target_user_home}" ]; then
    handle_error 1 "User record for '${TARGET_USER}' has an empty home field"
fi

root_home="/root"
root_ros_home="/root/.ros"
root_colcon_home="/root/.colcon"

# Make sure both paths above exist.
mkdir --parent --verbose "${root_ros_home}" ||
    handle_error 1 "Could not create root ROS home '${root_ros_home}'"

mkdir --parent --verbose "${root_colcon_home}" ||
    handle_error 1 "Could not create root colcon home '${root_colcon_home}'"

# Download the colcon mixin and metadata repositories.
log info "Installing colcon mixin and metadata for ROS 2"
log info "Ownership of colcon databases will be fixed later "

# Removing a missing repository and failing to remove an existing repository are
# different states. Listing first lets the script ignore only the expected
# "default is absent" case without hiding permissions, parsing or filesystem
# errors reported by `colcon mixin remove`.
mixin_repositories="$(HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin list)" ||
    handle_error 1 "Could not inspect configured colcon mixin repositories"

default_mixin_repository_exists=false
while IFS= read -r mixin_repository; do
    if [[ ${mixin_repository} == default:* ]]; then
        default_mixin_repository_exists=true
        break
    fi
done <<<"${mixin_repositories}"

if [ "${default_mixin_repository_exists}" = true ]; then
    HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin remove default ||
        handle_error 1 "Could not remove the existing default colcon mixin repository"
else
    log info "No existing default colcon mixin needed removal"
fi

HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin add default https://raw.githubusercontent.com/colcon/colcon-mixin-repository/master/index.yaml || handle_error 1 "colcon mixin add failed"
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin update default || handle_error 1 "colcon mixin update failed"

# Metadata repositories use the same contract as mixin repositories: absence is
# harmless, but failure while inspecting or removing an existing entry is not.
metadata_repositories="$(HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata list)" ||
    handle_error 1 "Could not inspect configured colcon metadata repositories"

default_metadata_repository_exists=false
while IFS= read -r metadata_repository; do
    if [[ ${metadata_repository} == default:* ]]; then
        default_metadata_repository_exists=true
        break
    fi
done <<<"${metadata_repositories}"

if [ "${default_metadata_repository_exists}" = true ]; then
    HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata remove default ||
        handle_error 1 "Could not remove the existing default colcon metadata repository"
else
    log info "No existing default colcon metadata needed removal"
fi

HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata add default https://raw.githubusercontent.com/colcon/colcon-metadata-repository/master/index.yaml || handle_error 1 "colcon metadata add failed"
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata update default || handle_error 1 "colcon metadata update failed"

if [ "${TARGET_USER}" = "root" ]; then
    log info "TARGET_USER is 'root', no need to move colcon databases"
    exit 0
fi

# Replace only ~/.colcon, the directory owned by this setup phase. Other files
# in an existing development home are never removed or recursively traversed.
target_user_colcon_home="${target_user_home}/.colcon"
if [ -d "${target_user_colcon_home}" ]; then
    rm -rf "${target_user_colcon_home}" &>/dev/null ||
        handle_error 1 "Could not remove previous colcon metadata '${target_user_colcon_home}'"
fi

log info "Moving COLCON_HOME from '${root_colcon_home}' to '${target_user_colcon_home}'"
mv --verbose "${root_colcon_home}" "${target_user_colcon_home}" ||
    handle_error 1 "Could not move generated colcon metadata into '${target_user_colcon_home}'"

chown --recursive "${target_user_id}:${target_user_pri_group_id}" "${target_user_colcon_home}" ||
    handle_error 1 "Could not set ownership of generated colcon metadata '${target_user_colcon_home}'"
