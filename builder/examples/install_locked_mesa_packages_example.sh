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

log info "Using system repositories to install locked versions of Mesa packages"
apt-get update

# Puedes editar las versiones concretas según las que hayan funcionado bien para tu caso de uso.
# Estas versiones son solo ejemplos y deben ser revisadas antes de usar.
packages=(
    mesa-utils=8.4.0-1ubuntu1
    mesa-vulkan-drivers:amd64=23.2.1-1ubuntu3.1~22.04.3
    libgl1-mesa-dri:amd64=23.2.1-1ubuntu3.1~22.04.3
    libgl1-mesa-glx:amd64=23.0.4-0ubuntu1~22.04.1
    libegl1-mesa:amd64=23.0.4-0ubuntu1~22.04.1
)

log info "Candidate versions will be forced:"

for entry in "${packages[@]}"; do
    pkg="${entry%%=*}"
    ver="${entry##*=}"

    log info "Package: ${pkg}"
    log info "    Version: ${ver}"
done

install_pkgs "${packages[@]}" || handle_error 1 "Installation of Mesa packages failed"
