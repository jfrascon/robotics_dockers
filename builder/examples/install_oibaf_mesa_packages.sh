#!/usr/bin/env bash
set -e

log() {
  local type="${1:-info}"
  local message="${2:-}"
  local timestamp
  local color=''
  local endcolor=''

  timestamp="$(date --utc '+%Y-%m-%dT%H:%M:%SZ')"

  if [ -t 1 ]; then
    endcolor='\\033[0m'

    case "${type}" in
    info)    color='\\033[38;5;79m' ;;
    success) color='\\033[1;32m'    ;;
    warning) color='\\033[1;33m'    ;;
    error)   color='\\033[1;31m'    ;;
    debug)   color='\\033[1;34m'    ;;
    *)       color='\\033[1;34m'    ;;
    esac
  fi

  printf '%b[%s] [%s] %s%b\\n' \\
    "${color}" \\
    "${timestamp}" \\
    "${type}" \\
    "${message}" \\
    "${endcolor}"
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

log info "Adding Oibaf PPA to install Mesa packages"
add-apt-repository -y ppa:oibaf/graphics-drivers
apt-get update

log info "Resolving candidate versions for Mesa packages"

packages=(
    mesa-utils
    mesa-vulkan-drivers
    libgl1-mesa-dri
    libgl1-mesa-glx
    libegl1-mesa
)

for pkg in "${packages[@]}"; do
    candidate=$(apt-cache policy "${pkg}" | grep Candidate | awk '{print $2}')
    installed=$(dpkg-query -W -f='${Version}' "${pkg}" 2>/dev/null || echo "none")

    log info "Package: ${pkg}"
    log info "    Installed: ${installed}"
    log info "    Candidate: ${candidate}"
done

log info "Installing Mesa packages from Oibaf PPA"
install_pkgs "${packages[@]}" || {
    log info "Installation of Mesa packages failed"
    exit 1
}
