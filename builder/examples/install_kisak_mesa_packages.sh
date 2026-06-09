#!/usr/bin/env bash
set -e

# Install only resolvable Debian packages.
install_pkgs() {
    local pkgs=("$@")
    local valid=()
    local bad=()
    local already=()
    local pkg
    local verb

    [ ${#pkgs[@]} -eq 0 ] && return 0

    for pkg in "${pkgs[@]}"; do
        # If it is already installed, skip it.
        if dpkg-query -W -f='${Status}\n' "$pkg" 2>/dev/null | grep -q '^install ok installed$'; then
            log info "Checking package '${pkg}': already installed"
            already+=("${pkg}")
            continue
        fi

        if apt-get --simulate --option=Dpkg::Use-Pty=0 --no-install-recommends install "${pkg}" >/dev/null 2>&1; then
            valid+=("${pkg}")
            verb="installable"
        else
            bad+=("${pkg}")
            verb="not installable"
        fi

        log info "Checking package '${pkg}': ${verb}"
    done

    # Every package is already installed, nothing to do.
    [ ${#already[@]} -eq ${#pkgs[@]} ] && return 0

    # Warn about packages that cannot be installed.
    [ ${#bad[@]} -gt 0 ] && log warning "Packages not installable: ${bad[*]}"

    # No valid packages to install.
    if [ ${#valid[@]} -eq 0 ]; then
        log warning "No installable packages"
        return 1
    fi

    apt-get install --yes --no-install-recommends "${valid[@]}" || {
        log error "Installation failed: ${valid[*]}"
        return 1
    }

    return 0
}

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
install_pkgs "${packages[@]}" || {
    log info "Installation of Mesa packages failed"
    exit 1
}
