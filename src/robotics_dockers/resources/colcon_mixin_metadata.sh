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
  ${script_name} TARGET_USER [--help]

Positional arguments:
  TARGET_USER     Target system user name

Options:
  --help          Show this help and exit
EOF
}

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script is run by root when building the Docker image.
[ "$(id --user)" -ne 0 ] && handle_error 1 "root user must be active to run the script '${script_name}'"

# Pre-scan: show help if present in any position.
for arg in "$@"; do
    case "$arg" in
    --help | -h)
        usage
        exit 0
        ;;
    esac
done

[ "$#" -lt 1 ] && handle_error 1 "Missing required positional argument TARGET_USER"

TARGET_USER="${1:-}"
shift

[ "$#" -gt 0 ] && log warning "unexpected extra arguments: $*"

[ -z "${TARGET_USER}" ] && handle_error 1 "TARGET_USER is empty"

target_user_entry="$(getent passwd "${TARGET_USER}")"

[ -z "${target_user_entry}" ] && handle_error 1 "User '${TARGET_USER}' does not exist"

target_user_home="$(echo "${target_user_entry}" | cut -d: -f6)"

[ -z "${target_user_home}" ] && handle_error 1 "Home directory for user '${TARGET_USER}' could not be determined"

root_home="/root"

[ ! -d "${root_home}" ] && handle_error 1 "Root home directory '${root_home}' does not exist"

root_ros_home="/root/.ros"
root_colcon_home="/root/.colcon"

# Make sure both paths above exist.
mkdir --parent --verbose "${root_ros_home}"
mkdir --parent --verbose "${root_colcon_home}"

# Download the colcon mixin and metadata repositories.
log info "Installing colcon mixin and metadata for ROS 2"
log info "Ownership of colcon databases will be fixed later "

HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin remove default >/dev/null 2>&1 || true
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin add default https://raw.githubusercontent.com/colcon/colcon-mixin-repository/master/index.yaml || handle_error 1 "colcon mixin add failed"
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon mixin update default || handle_error 1 "colcon mixin update failed"
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata remove default >/dev/null 2>&1 || true
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata add default https://raw.githubusercontent.com/colcon/colcon-metadata-repository/master/index.yaml || handle_error 1 "colcon metadata add failed"
HOME="${root_home}" ROS_HOME="${root_ros_home}" colcon metadata update default || handle_error 1 "colcon metadata update failed"

[ "${TARGET_USER}" = "root" ] && {
    log info "TARGET_USER is 'root', no need to move colcon databases"
    exit 0
}

# Move the colcon home directory to the user home directory, if the TARGET_USER is non root.
target_user_id="$(echo "${target_user_entry}" | cut -d: -f3)"
target_user_pri_group_id="$(echo "${target_user_entry}" | cut -d: -f4)"
target_user_home="$(echo "${target_user_entry}" | cut -d: -f6)"
target_user_colcon_home="${target_user_home}/.colcon"
[ -d "${target_user_colcon_home}" ] && rm -rf "${target_user_colcon_home}" &>/dev/null

log info "Moving COLCON_HOME from '${root_colcon_home}' to '${target_user_colcon_home}'"
mv --verbose "${root_colcon_home}" "${target_user_colcon_home}"

chown --recursive "${target_user_id}:${target_user_pri_group_id}" "${target_user_colcon_home}"
