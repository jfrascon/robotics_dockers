#!/usr/bin/env bash
set -e

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

#-----------------------------------------------------------------------------------------------------------------------
# Start execution of the script
#-----------------------------------------------------------------------------------------------------------------------

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script is run by root when building the Docker image.
[ "$(id --user)" -ne 0 ] && handle_error 1 "root user must be active to run the script '${script_name}'"

log info "Using system repositories to install Mesa packages"
apt-get update

log info "Resolving candidate versions for Mesa packages"

packages=(
    libgl1
    libgl1-mesa-dri
    mesa-utils
    x11-xserver-utils
)

for pkg in "${packages[@]}"; do
    candidate=$(apt-cache policy "${pkg}" | grep Candidate | awk '{print $2}')
    installed=$(dpkg-query -W -f='${Version}' "${pkg}" 2>/dev/null || echo "none")

    log info "Package: ${pkg}"
    log info "    Installed: ${installed}"
    log info "    Candidate: ${candidate}"
done

install_pkgs "${packages[@]}"
