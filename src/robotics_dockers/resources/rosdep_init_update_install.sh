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
  ${script_name} ROS_DISTRO TARGET_USER [--pkgs-dir DIR --help]

Positional arguments:
  ROS_DISTRO      Target ROS 2 distribution (e.g., humble, jazzy)
  TARGET_USER     Target system user name

Options:
  --pkgs-dir DIR  Directory containing packages
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

# Normalize arguments with GNU getopt.
# -o ''     -> no short options
# -l ...    -> long options; ":" ⇒ option requires a value
# --        -> end of getopt's own flags; after this, pass script args to parse
# "$@"      -> forward all original args verbatim (keeps spaces/quotes)
# getopt    -> normalizes: reorders options first, splits values, appends a final "--"
# on error  -> exits non-zero; we show usage and exit 2
if ! PARSED=$(getopt -o '' -l pkgs-dir:,help -- "$@"); then
    usage
    exit 1
fi

# Replace $@ with the normalized list; eval preserves quoting from getopt’s output
eval set -- "${PARSED}"

# After eval set -- ... we get:
# --pkgs-dir dir -- ROS_DISTRO TARGET_USER
# --pkgs-dir may or may not be present
# -- is the end of options marker

pkgs_dir="" # optional

while true; do
    case "${1:-}" in
    --pkgs-dir)
        pkgs_dir="${2}"
        shift 2
        ;;
    --help)
        usage
        exit 0
        ;;
    --)
        shift
        break
        ;; # end of options, positionals follow
    *)
        usage
        exit 2
        ;;
    esac
done

if [ "$#" -lt 2 ]; then
    handle_error 1 "Missing required positionals: ROS_DISTRO and TARGET_USER"
fi

ROS_DISTRO="${1}"
TARGET_USER="${2}"
shift 2

if [ "$#" -gt 0 ]; then
    log warning "unexpected extra arguments: $*"
fi

if [ -z "${ROS_DISTRO}" ]; then
    handle_error 2 "ROS_DISTRO is empty"
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

if [ -n "${pkgs_dir}" ]; then
    if [ ! -d "${pkgs_dir}" ]; then
        log warning "pkgs_dir '${pkgs_dir}' does not exist, ignoring it"
        pkgs_dir=""
    fi
fi

log info "Initializing rosdep"

rosdep_sources_dir="/etc/ros/rosdep/sources.list.d"
rosdep_default_sources="${rosdep_sources_dir}/20-default.list"

# The ROS distribution selects this build-time path.
# shellcheck disable=SC1090
. /opt/ros/"${ROS_DISTRO}"/setup.bash || handle_error 1 "Sourcing ROS setup.bash failed"

if [ -f "${rosdep_default_sources}" ]; then
    log info "rosdep default sources already exist, skipping rosdep init"
else
    rosdep init || handle_error 1 "rosdep init failed"
fi

log info "Executing rosdep update as root. Ignore the warning about running as root"
log info "rosdep database ownership will be fixed later"

# If the command 'rosdep update' is run as root, the rosdep database is located at
# /root/.ros/rosdep.
# Ref: rosdep --help
root_home="/root"
root_ros_home="${root_home}/.ros"
# Make sure the ROS home directory exists.
mkdir --parent --verbose "${root_ros_home}" ||
    handle_error 1 "Could not create root ROS home '${root_ros_home}'"

HOME="${root_home}" ROS_HOME="${root_ros_home}" rosdep update --rosdistro "${ROS_DISTRO}" || handle_error 1 "rosdep update failed"

# At this point if pkg_dir is set, it is a valid directory.
if [ -n "${pkgs_dir}" ]; then
    log info "Installing dependencies with rosdep for packages located at '${pkgs_dir}'"

    # Update cache to ensure the latest package information is available.
    apt-get update --quiet --quiet || handle_error 1 "apt-get update failed"

    HOME="${root_home}" ROS_HOME="${root_ros_home}" rosdep install -r -y --rosdistro "${ROS_DISTRO}" --from-paths "${pkgs_dir}" --ignore-src ||
        handle_error 1 "rosdep install failed"
fi

if [ "${TARGET_USER}" = "root" ]; then
    log info "TARGET_USER is 'root', no need to move the rosdep databases"
    exit 0
fi

# Move the generated rosdep database into the configured user's ROS directory.
# Ownership is changed only on the directory and database created by this
# script. Other pre-existing content below ~/.ros is deliberately not traversed.
target_user_ros_home="${target_user_home}/.ros"

if [ ! -d "${target_user_ros_home}" ]; then
    mkdir --verbose --parent "${target_user_ros_home}" ||
        handle_error 1 "Could not create target ROS home '${target_user_ros_home}'"
elif [ -d "${target_user_ros_home}/rosdep" ]; then
    # Remove any existing rosdep database in the user home directory.
    rm -rf "${target_user_ros_home}/rosdep" &>/dev/null ||
        handle_error 1 "Could not remove the previous rosdep database from '${target_user_ros_home}'"
fi

log info "Moving '${root_ros_home}/rosdep' to '${target_user_ros_home}/rosdep'"
mv --verbose "${root_ros_home}/rosdep" "${target_user_ros_home}/rosdep" ||
    handle_error 1 "Could not move the generated rosdep database into '${target_user_ros_home}'"

chown "${target_user_id}:${target_user_pri_group_id}" "${target_user_ros_home}" ||
    handle_error 1 "Could not set ownership of target ROS home '${target_user_ros_home}'"

chown --recursive "${target_user_id}:${target_user_pri_group_id}" "${target_user_ros_home}/rosdep" ||
    handle_error 1 "Could not set ownership of the generated rosdep database"

log info "Removing installation residues from apt cache"
apt-get clean >/dev/null || handle_error 1 "Failed to clean the apt cache"
find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Failed to remove apt package indexes"
