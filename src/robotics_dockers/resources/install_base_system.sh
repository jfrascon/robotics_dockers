#!/usr/bin/env bash
# Install operating-system tools shared by every generated development image.
#
# User creation deliberately does not belong here. Keeping this script independent
# from the requested account lets Docker reuse this expensive layer when only the
# development UID, GID or name changes.

log() {
    local level="${1:-info}"
    shift || true
    printf '[%s] [%s] %s\n' "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" "${level}" "$*"
}

handle_error() {
    local exit_code="${1:-1}"
    shift || true
    log error "${*:-Unknown error} (exit code: ${exit_code})" >&2
    exit "${exit_code}"
}

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing this script"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "This script must run as UID 0; found UID '${executing_user_id}'"
fi

apt-get update --quiet --quiet || handle_error 1 "apt-get update failed"

install_pkgs apt-utils python3-software-properties software-properties-common ||
    handle_error 1 "Could not install the tools required to enable Ubuntu repositories"

add-apt-repository --yes universe || handle_error 1 "Could not enable the Ubuntu universe repository"

# This project deliberately keeps a full distribution upgrade. Docker can reuse
# this layer from cache even when remote apt repositories change. Use build.py
# --no-cache when the upgrade must run again, and --pull when the base image
# itself must also be refreshed.
apt-get dist-upgrade --yes --no-install-recommends || handle_error 1 "The distribution upgrade failed"

packages=(
    apt-rdepends
    automake
    bash-completion
    build-essential
    ca-certificates
    clang
    clang-format
    clang-tidy
    cmake
    cppcheck
    curl
    gawk
    gdb
    git
    gnupg
    htop
    iproute2
    iputils-ping
    jq
    lcov
    less
    libcppunit-dev
    libtool-bin
    lldb
    locales
    lsb-release
    nano
    net-tools
    openssh-client
    passwd
    procps
    python3-dev
    python3-numpy
    python3-pip
    python3-pytest
    python3-setuptools
    ripgrep
    rsync
    sed
    shellcheck
    shfmt
    sudo
    tree
    tzdata
    util-linux
    valgrind
    vim
    wget
)
install_pkgs "${packages[@]}" || handle_error 1 "Could not install the base development packages"

update-alternatives --install /usr/bin/python python /usr/bin/python3 100 ||
    handle_error 1 "Could not configure /usr/bin/python"

log info "Configuring the system timezone as UTC"
printf 'Etc/UTC\n' >/etc/timezone || handle_error 1 "Could not write /etc/timezone"

ln --symbolic --force /usr/share/zoneinfo/Etc/UTC /etc/localtime ||
    handle_error 1 "Could not configure /etc/localtime"

DEBIAN_FRONTEND=noninteractive dpkg-reconfigure tzdata || handle_error 1 "Could not configure tzdata"

log info "Configuring the en_US.UTF-8 locale"
locale_file="$(mktemp)" || handle_error 1 "Could not create a temporary locale file"

trap 'rm -f -- "${locale_file}"' EXIT

printf 'en_US.UTF-8 UTF-8\n' >"${locale_file}" || handle_error 1 "Could not prepare the locale definition"

install --owner root --group root --mode 0644 "${locale_file}" /etc/locale.gen ||
    handle_error 1 "Could not install /etc/locale.gen"

locale-gen en_US.UTF-8 || handle_error 1 "locale-gen failed"

update-locale LANG=en_US.UTF-8 || handle_error 1 "update-locale failed"

# Removing apt's download and index data in this same build step keeps it out of
# the resulting layer. autoremove is intentionally not used: it can remove a
# dependency that a later project customization expects to remain installed.
apt-get clean >/dev/null || handle_error 1 "Could not clean the apt cache"

find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Could not remove apt package indexes"
