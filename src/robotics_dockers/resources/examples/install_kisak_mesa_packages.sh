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

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script is run by root when building the Docker image.
[ "$(id --user)" -ne 0 ] && handle_error 1 "root user must be active to run the script '${script_name}'"

log info "Adding Kisak PPA to install Mesa packages"
# From 'https://launchpad.net/~kisak/+archive/ubuntu/kisak-mesa':
# If you value stability over support, https://launchpad.net/~kisak/+archive/ubuntu/turtle/ is available as an
# alternative.
# ppa:kisak/kisak-mesa for support.
# ppa:kisak/turtle for stability.
add-apt-repository -y ppa:kisak/turtle
apt-get update

log info "Resolving candidate versions for Mesa packages"

packages=(
    mesa-utils
    mesa-vulkan-drivers
    libgl1-mesa-dri
    libgl1-mesa-glx
    libegl1-mesa
)

log info "Installing Mesa packages from Kisak PPA"
install_pkgs "${packages[@]}" || handle_error 1 "Installation of Mesa packages failed"
