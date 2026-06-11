#!/usr/bin/env bash

# install_ros2.sh
#
# Installs ROS2 and related packages into the image.
# Must run as root.
#
# Usage: install_ros2.sh <ros_distro>
#   ros_distro: e.g. humble, jazzy

# --------------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------------

# install_pkgs <pkg>...
#
# Installs only the apt packages that are not yet installed and are resolvable.
# Already-installed packages are skipped. Unresolvable packages are warned about but do not abort.
install_pkgs() {
    local pkgs=("$@")
    local valid=()
    local bad=()
    local already=()
    local pkg
    local verb

    [ ${#pkgs[@]} -eq 0 ] && return 0

    for pkg in "${pkgs[@]}"; do
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

    if [ ${#already[@]} -eq ${#pkgs[@]} ]; then
        log info "All requested packages are already installed"
        return 0
    fi

    [ ${#bad[@]} -gt 0 ] && log warning "Packages not installable: ${bad[*]}"

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

# remove_gpg_key_file <sources_file> <deb_pattern>
#
# Removes any GPG key file referenced by signed-by= in lines matching deb_pattern.
remove_gpg_key_file() {
    local file="${1}"
    local deb_pattern="${2}"

    grep --extended-regexp "${deb_pattern}" "${file}" |
        grep --only-matching --perl-regexp 'signed-by=\K[^] ]+' |
        while IFS= read -r signed_by_path; do
            if [ -f "${signed_by_path}" ]; then
                log info "Removing GPG key file '${signed_by_path}'"
                rm --force "${signed_by_path}"
            fi
        done
}

# sanitize <version_codename>
#
# Removes legacy ROS2 deb lines (and their GPG keys) from apt sources.
# Needed because on 2025-06-01 the key/repo management moved to ros2-apt-source.
# Ref: https://discourse.ros.org/t/ros-signing-key-migration-guide/43937
sanitize() {
    local version_codename="${1}"
    local url="http://packages.ros.org/ros2/ubuntu"
    local ros_deb_pattern="^deb.*${url}[[:space:]]+${version_codename}[[:space:]]+main"

    local matched_files
    matched_files="$(find /etc/apt/ -type f -name '*.list' \
        -exec grep --files-with-matches --extended-regexp "${ros_deb_pattern}" {} + 2>/dev/null)"

    while IFS= read -r matched_file; do
        if [ "${matched_file}" = "/etc/apt/sources.list" ]; then
            remove_gpg_key_file "${matched_file}" "${ros_deb_pattern}"
            log info "ROS deb line found in '${matched_file}', removing matching lines"
            sed --in-place --regexp-extended "\#${ros_deb_pattern}#d" "${matched_file}"
        else
            remove_gpg_key_file "${matched_file}" "${ros_deb_pattern}"
            log info "ROS deb line found in '${matched_file}', removing file"
            rm --force "${matched_file}"
        fi
    done <<< "${matched_files}"
}

# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

[ "$(id --user)" -ne 0 ] && handle_error 1 "root user must be active to run '${script_name}'"

ROS_DISTRO="${1}"
[ -z "${ROS_DISTRO}" ] && handle_error 1 "No ROS_DISTRO provided. Usage: ${script_name} <ros_distro>"

version_codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"

# --------------------------------------------------------------------------------------------------
# Check for existing ROS installation.
# --------------------------------------------------------------------------------------------------
ros_distro_installed="$(dpkg --list | \
    sed -nE 's/^ii\s+ros-([a-z]+)-ros-core.*$/\1/p' | \
    tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

num_ros_distros="$(echo "${ros_distro_installed}" | wc -w)"

[ "${num_ros_distros}" -gt 1 ] && \
    handle_error 1 "More than one ROS distro is installed: ${ros_distro_installed}"

[ "${num_ros_distros}" -eq 1 ] && [ "${ROS_DISTRO}" != "${ros_distro_installed}" ] && \
    handle_error 1 "Found ROS '${ros_distro_installed}' installed, but '${ROS_DISTRO}' was requested"

# --------------------------------------------------------------------------------------------------
# ROS packages to install.
# For a list of all packages in each variant (ros_core, ros_base, perception, simulation, desktop,
# desktop_full), see https://github.com/ros2/variants.
# desktop and desktop_full contain tutorials/examples not needed for a development image.
# Simulation (Gazebo) packages are intentionally excluded — add them via extra.d/ if needed.
# --------------------------------------------------------------------------------------------------
packages=(
  libasio-dev
  python3-colcon-alias
  python3-colcon-clean
  python3-colcon-common-extensions
  python3-colcon-hardware-acceleration
  python3-colcon-mixin
  python3-colcon-ros-distro
  python3-colcon-ros-domain-id-coordinator
  python3-mypy
  python3-mypy-extensions
  python3-rosdep
  python3-vcstool
  ros-${ROS_DISTRO}-ros-base
  ros-${ROS_DISTRO}-rmw-cyclonedds-cpp
  ros-${ROS_DISTRO}-rmw-fastrtps-cpp
  ros-${ROS_DISTRO}-rmw-fastrtps-dynamic-cpp
  ros-${ROS_DISTRO}-rqt
  ros-${ROS_DISTRO}-rqt-common-plugins
  ros-${ROS_DISTRO}-rqt-tf-tree
  ros-${ROS_DISTRO}-rviz2
  ros-${ROS_DISTRO}-xacro
  ros-dev-tools
)

# --------------------------------------------------------------------------------------------------
# Remove legacy apt sources and install ROS2.
# --------------------------------------------------------------------------------------------------
sanitize "${version_codename}"

apt-get update --yes --quiet --quiet || handle_error 1 "apt-get update failed"
install_pkgs apt-utils || exit 1
install_pkgs python3-software-properties software-properties-common || exit 1
add-apt-repository --yes universe || handle_error 1 "Adding universe repository failed"
install_pkgs curl gpg || exit 1

ros_list_file=""
ros_apt_source_package="ros2-apt-source"

if ! dpkg --status "${ros_apt_source_package}" >/dev/null 2>&1; then
    gpg_dir="/etc/apt/keyrings"
    gpg_file="${gpg_dir}/ros.gpg"

    if [ ! -d "${gpg_dir}" ]; then
        log info "Creating directory '${gpg_dir}'"
        mkdir --verbose --parent "${gpg_dir}" || handle_error 1 "Failed to create '${gpg_dir}'"
    fi

    log info "Adding ROS GPG key to '${gpg_file}'"
    curl --fail --silent --show-error --location \
        https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc | \
        gpg --dearmor --output "${gpg_file}" || \
        handle_error 1 "Downloading or dearmoring the ROS2 GPG key failed"

    chmod 644 "${gpg_file}" || handle_error 1 "Failed to set permissions on '${gpg_file}'"

    url="http://packages.ros.org/ros2/ubuntu"
    ros_deb_line="deb [arch=$(dpkg --print-architecture) signed-by=${gpg_file}] ${url} ${version_codename} main"
    ros_list_file="/etc/apt/sources.list.d/ros.list"

    log info "Adding ROS2 deb line to '${ros_list_file}'"
    echo "${ros_deb_line}" | tee "${ros_list_file}" >/dev/null || \
        handle_error 1 "Failed to write ROS2 deb line to '${ros_list_file}'"

    packages+=("${ros_apt_source_package}")
fi

apt-get update --yes --quiet --quiet || handle_error 1 "apt-get update failed"
install_pkgs "${packages[@]}" || exit 1

# If we created a temporary list file, remove it and its GPG key now that ros2-apt-source
# manages repository and key going forward.
if [ -n "${ros_list_file}" ]; then
    rm -f "${ros_list_file}"
    rm -f "${gpg_file}"
fi

log info "Removing installation residues from apt cache"
apt-get autoclean  || handle_error 1 "Autoclean failed"
apt-get autoremove --purge -y || handle_error 1 "Autoremove failed"
apt-get clean      || handle_error 1 "Clean failed"
rm -rf /var/lib/apt/lists/*
