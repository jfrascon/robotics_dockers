#!/usr/bin/env bash

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

usage() {
    cat <<EOF
Usage:
  ${script_name} TARGET_USER TARGET_USER_HOME [--help]

Positional arguments:
  TARGET_USER       Target system user name
  TARGET_USER_HOME  Home directory for the target user

Options:
  --help          Show this help and exit
EOF
}

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

# This script must be run by root.
if [ "$(id --user)" -ne 0 ]; then
    handle_error 1 "root user must be active to run the script '${script_name}'"
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

TARGET_USER="${1:-}"
TARGET_USER_HOME="${2:-}"

[ -z "${TARGET_USER}" ] && handle_error 1 "User not provided"
[ -z "${TARGET_USER_HOME}" ] && handle_error 1 "User home directory not provided"

target_user_shell="/bin/bash"

# Update the package list and upgrade all packages to their latest versions.
apt-get update --yes --quiet --quiet || handle_error 1 "apt-get update failed"

# Install the apt-utils package first, to avoid warnings when installing packages if this package
# is not installed previously.
install_pkgs apt-utils || exit 1

# Install the package that allow us to add repositories.
install_pkgs python3-software-properties software-properties-common || exit 1

# Now add-apt-repository is available, and we can add the universe repository that contains many
# of the packages we need. Next, the index is updated, and the system is upgraded to ensure all packages are up to date.
add-apt-repository --yes universe || handle_error 1 "Adding universe repository failed"

# Upgrade the system to ensure all packages are up to date, now that apt-utils is installed.
apt-get dist-upgrade --yes --no-install-recommends || handle_error 1 "Upgrade of the system failed"

#-----------------------------------------------------------------------------------------------------------------------
# Unminimize the system
#-----------------------------------------------------------------------------------------------------------------------

# In a development environment, man pages are useful for understanding commands and their options, so we install them.
# Up to Ubuntu:22.04, the command 'unminimize' was already included in the Ubuntu base image provided by Docker Hub.
# Starting with Ubuntu:24.04, the 'unminimize' command is no longer included by default. However, it is available in the
# system repositories and must be installed before it can be used.

# Check if the command exists; if not, install it if available in apt sources.
# if ! command -v unminimize &>/dev/null; then
#     if apt-cache policy unminimize | grep --quiet 'Candidate:'; then
#         install_pkgs unminimize || {
#             log "Installation of package 'unminimize' failed" >&2
#             exit 1
#         }
#     else
#         log "Warning: Package 'unminimize' is missing in apt sources! Skipping installation"
#     fi
# fi

# Unminimize the system if the command unminimize is available.
# command -v unminimize &>/dev/null && {
#     log "Unminimizing the system"
#     echo y | unminimize || {
#         log "Unminimize command failed" >&2
#         exit 1
#     }
# }

# Install core packages.
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
    gosu
    htop
    iproute2
    iputils-ping
    jq
    lcov
    less
    libcppunit-dev
    libtool-bin
    lldb
    lsb-release
    nano
    net-tools
    openssh-client
    procps
    python3-dev
    python3-numpy
    python3-pip
    python3-pytest
    python3-setuptools
    rsync
    sed
    sudo
    tree
    valgrind
    vim
    wget
)

install_pkgs "${packages[@]}" || exit 1

update-alternatives --install /usr/bin/python python /usr/bin/python3 100

# Set the system timezone to UTC to ensure consistent timekeeping across environments.
# Handle timezone configuration explicitly and separately from the main package installation to avoid tzdata's
# interactive prompts. Even with DEBIAN_FRONTEND=noninteractive, tzdata might still try to launch its dialog if the
# timezone config files are missing or improperly set.
# The /etc/timezone file and the /etc/localtime symlink must be created before installing tzdata.
# The file /etc/localtime must point to a valid file under /usr/share/zoneinfo/, which is provided by the tzdata
# package.

log info "Configuring UTC time"
echo "Etc/UTC" >/etc/timezone
ln --symbolic --force "/usr/share/zoneinfo/Etc/UTC" /etc/localtime

if ! dpkg --status tzdata >/dev/null 2>&1; then
    TZ=Etc/UTC DEBIAN_FRONTEND=noninteractive install_pkgs tzdata || exit 1
fi

dpkg-reconfigure --frontend noninteractive tzdata
export TZ=Etc/UTC # In case any command in this script after this line needs it.

log info "Configuring locales to en_US.UTF-8"
# Install the locales package to support UTF-8 encoding.
install_pkgs locales || exit 1

tmp="$(mktemp)"
printf 'en_US.UTF-8 UTF-8\n' >"${tmp}"
install --owner=root --group=root --mode=0644 "${tmp}" /etc/locale.gen || \
    handle_error 1 "Failed to install /etc/locale.gen"
rm -f "${tmp}"

locale-gen en_US.UTF-8 || handle_error 1 "locale-gen failed"

update-locale LANG=en_US.UTF-8
export LANG=en_US.UTF-8 # In case any command in this script after this line needs it.

#-----------------------------------------------------------------------------------------------------------------------
# Create the requested user
#-----------------------------------------------------------------------------------------------------------------------
# Starting with Ubuntu 24.04, a default non-root user named 'ubuntu' exists with UID 1000 and primary group 'ubuntu'
# with GID 1000.
# Reference: https://bugs.launchpad.net/cloud-images/+bug/2005129

[ "${TARGET_USER}" = root ] && handle_error 1 "TARGET_USER cannot be root"

# If 'TARGET_USER' does not exist, it will be created with the specified shell and home
# directory.
# If 'TARGET_USER' already exists, its shell and home directory will be updated to match the
# specified ones:
# - Shell: '/bin/bash'
# - Home directory: '${TARGET_USER_HOME}'
if ! getent passwd "${TARGET_USER}" >/dev/null 2>&1; then
    # Create the user with the specified home directory and shell. Home is created physically.
    # when no option --home-dir is specified, the home directory is created in /home/<username>.
    useradd --create-home --home-dir "${TARGET_USER_HOME}" --shell "${target_user_shell}" "${TARGET_USER}" || \
        handle_error 1 "Failed to create user '${TARGET_USER}'!"

    target_user_entry="$(getent passwd "${TARGET_USER}")"
    target_user_id="$(echo "${target_user_entry}" | cut -d: -f3)"
    target_user_pri_group_id="$(echo "${target_user_entry}" | cut -d: -f4)"
    target_user_pri_group="$(getent group "${target_user_pri_group_id}" | cut -d: -f1)"

    log info "Created user '${TARGET_USER}' (UID '${target_user_id}') with primary group '${target_user_pri_group}' (GID '${target_user_pri_group_id}')"
else
    # If the user already exists, check if the shell match the requested ones.
    target_user_entry="$(getent passwd "${TARGET_USER}")"
    target_user_id="$(echo "${target_user_entry}" | cut -d: -f3)"
    target_user_pri_group_id="$(echo "${target_user_entry}" | cut -d: -f4)"
    target_user_pri_group="$(getent group "${target_user_pri_group_id}" | cut -d: -f1)"
    current_shell="$(echo "${target_user_entry}" | cut -d: -f7)"

    log info "User '${TARGET_USER}' (UID '${target_user_id}') with primary group '${target_user_pri_group}' (GID '${target_user_pri_group_id}') already exists, verifying properties"

    if [ "${current_shell}" != "${target_user_shell}" ]; then
        log info "Updating shell of user '${TARGET_USER}' (UID '${target_user_id}') from '${current_shell}' to '${target_user_shell}'"
        usermod --shell "${target_user_shell}" "${TARGET_USER}" || \
            handle_error 1 "Failed to set shell of user '${TARGET_USER}' (UID '${target_user_id}') to '${target_user_shell}'!"
    fi

    # Check if the home directory exists.
    current_home="$(echo "${target_user_entry}" | cut -d: -f6)"

    if [ -z "${current_home}" ]; then
        usermod --home "${TARGET_USER_HOME}" "${TARGET_USER}" || \
            handle_error 1 "Failed to set home directory of user '${TARGET_USER}' (UID '${target_user_id}') to '${TARGET_USER_HOME}'!"
    elif [ "${current_home}" != "${TARGET_USER_HOME}" ]; then
        log info "Updating home directory of user '${TARGET_USER}' (UID '${target_user_id}') from '${current_home}' to '${TARGET_USER_HOME}'"
        #--move-home: Move the content of the user's home directory to the new location
        usermod --home "${TARGET_USER_HOME}" --move-home "${TARGET_USER}" || \
            handle_error 1 "Failed to set home directory of user '${TARGET_USER}' (UID '${target_user_id}') to '${TARGET_USER_HOME}'!"
    fi
fi

# Ensure user is member of secondary groups dialout, sudo and video.
# dialout group is used to access serial ports (devices like /dev/ttyusb<x>).
# video group is used to access video devices (like /dev/video<x>, /dev/dri/card<x>).
for group in dialout sudo video; do
    group_entry="$(getent group "${group}")"

    if [ -z "${group_entry}" ]; then
        log warning "Group '${group}' does not exist!"
    # Check if the user is not already a member of the group.
    elif ! id -nG "${TARGET_USER}" | grep --quiet --word-regexp "${group}"; then
        group_id="$(echo "${group_entry}" | cut -d: -f3)"
        log info "Adding user '${TARGET_USER}' (UID '${target_user_id}') to group '${group}' (GID '${group_id}')"
        usermod --append --groups "${group}" "${TARGET_USER}" || \
            handle_error 1 "Failed to add user '${TARGET_USER}' (UID '${target_user_id}') to group '${group}' (GID '${group_id}')!"
    else
        group_id="$(echo "${group_entry}" | cut -d: -f3)"
        log info "User '${TARGET_USER}' (UID '${target_user_id}') is already a member of group '${group}' (GID '${group_id}')"
    fi
done

# Set password for the non-root user.
# The non-root user can run commands with sudo without a password.
# INTENTIONAL: the password is set to the username for convenience in development images.
# This is acceptable because these images are for local development only and not for production.
log info "Setting password for user '${TARGET_USER}' (UID '${target_user_id}') to '${TARGET_USER}'"
password="${TARGET_USER}"

echo "${TARGET_USER}:${password}" | chpasswd || \
    handle_error 1 "Failed to set password for '${TARGET_USER}' (UID '${target_user_id}')"

# The following block is disabled and is left here for reference.
# It is not recommended to configure passwordless sudo in a Docker image, as it can lead to
# security issues.

# Configure passwordless sudo.
# log info "Configuring passwordless sudo for user '${TARGET_USER}' (UID '${target_user_id}')"
# sudoers_file="/etc/sudoers.d/${TARGET_USER}"
# tmp_sudoers="$(mktemp)"
# echo "${TARGET_USER} ALL=(ALL) NOPASSWD:ALL" >"${tmp_sudoers}"

# If the temporary sudoers file is not valid, it will be removed and the script will exit with an
# error.
# Otherwise, the temporary sudoers file will be installed in the sudoers directory with the correct
# permissions, and the temporary file will be removed.
# if ! visudo --check --file "${tmp_sudoers}" >/dev/null 2>&1; then
#     log "Error: Invalid sudoers content, aborting" >&2
#     rm -f "${tmp_sudoers}"
#     exit 1
# fi
#
# install --owner=root --group=root --mode=0440 "${tmp_sudoers}" "${sudoers_file}"
# rm -f "${tmp_sudoers}"

# Create basic folders for configuration and binaries.
dirs_to_create=(
    "${TARGET_USER_HOME}/.config"
    "${TARGET_USER_HOME}/.local/bin"
    "${TARGET_USER_HOME}/.local/lib"
    "${TARGET_USER_HOME}/.local/share"
)

for dir in "${dirs_to_create[@]}"; do
    if [ ! -d "${dir}" ]; then
        log info "Creating directory '${dir}'"
        install --directory --mode 755 --owner "${TARGET_USER}" --group "${target_user_pri_group}" "${dir}"
    else
        log info "Directory '${dir}' already exists"
    fi
done

# Create the .bashrc file if it does not exist.
if [ ! -s "${TARGET_USER_HOME}/.bashrc" ]; then
    log info "File '${TARGET_USER_HOME}/.bashrc' does not exist. Copying file /etc/skel/.bashrc to '${TARGET_USER_HOME}/.bashrc'"
    # Copy the default .bashrc from /etc/skel to the user's home directory.
    # Ownership is fixed later in the script with `chown ...`, so we can copy the file as root.
    cp --verbose /etc/skel/.bashrc "${TARGET_USER_HOME}/.bashrc" || \
        handle_error 1 "Failed to copy /etc/skel/.bashrc to '${TARGET_USER_HOME}/.bashrc'"
fi

#-----------------------------------------------------------------------------------------------------------------------
# Install Python packages for the user that are commonly used for development
#-----------------------------------------------------------------------------------------------------------------------
python_packages=(argcomplete ruff cmake-format pre-commit jinja2 python-rapidjson)

log info "Installing Python packages for the user '${TARGET_USER}': ${python_packages[*]}"

pip_args=(--no-cache-dir --disable-pip-version-check)

# Install packages in the user's home directory.
pip_args+=(--user)

# The '--break-system-packages', described in PEP 668, was introduced in Python 3.11+ from Debian Bookworm and
# Ubuntu 24.04 (Noble Numbat), onwards. PEP 668 prevents installing packages with  'pip install --user' in
# system-managed environments. To work around this, the '--break-system-packages' flag is used to allow the
# installation of packages in user-managed environments.
# Ubuntu 22.04 (Jammy), and below, does NOT have this restriction, so 'pip install --user' should work fine.
if python3 -m pip install --help | grep --quiet 'break-system-packages'; then
    pip_args+=("--break-system-packages")
fi

# -H flag is used to set the HOME environment variable to the home directory of the target user.
# The HOME environment variable is used by pip to determine the location of the user's home directory.
# The --no-cache-dir flag is used to avoid caching the downloaded packages.
# The --user flag is used to install the packages in the user's home directory.
# To avoid warning messages when installing packages we set the environment variable PATH to include
# the user's local bin directory.

sudo -H -u "${TARGET_USER}" env PATH="${TARGET_USER_HOME}/.local/bin:${PATH}" \
    python3 -m pip install "${pip_args[@]}" "${python_packages[@]}" || \
    handle_error 1 "Failed to install Python packages for user '${TARGET_USER}'"

#-----------------------------------------------------------------------------------------------------------------------
# Cleanup
#-----------------------------------------------------------------------------------------------------------------------
log info "Removing installation residues from apt cache"
apt-get autoclean >/dev/null
apt-get autoremove --purge -y >/dev/null
apt-get clean >/dev/null
rm -rf /var/lib/apt/lists/* 1>/dev/null 2>&1
