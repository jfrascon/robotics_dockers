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

#-----------------------------------------------------------------------------------------------------------------------
# Start execution of the script
#-----------------------------------------------------------------------------------------------------------------------

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

log info "Using system repositories to install Mesa packages"
apt-get update || handle_error 1 "Failed to update apt repositories before installing Mesa packages"

packages=(
    libgl1
    libgl1-mesa-dri
    mesa-utils
    x11-xserver-utils
)

install_pkgs "${packages[@]}" || handle_error 1 "Failed to install Mesa packages"

apt-get clean >/dev/null || handle_error 1 "Failed to clean the apt cache after Mesa installation"
find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Failed to remove apt package indexes after Mesa installation"
