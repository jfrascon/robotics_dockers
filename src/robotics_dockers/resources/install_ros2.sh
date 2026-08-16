#!/usr/bin/env bash

# install_ros2.sh
#
# Installs ROS 2 and related packages into the image.
# Must run as root.
#
# Usage: install_ros2.sh <ros_distro>
#   ros_distro: e.g. humble, jazzy

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

# sanitize <version_codename>
#
# Removes legacy ROS 2 deb lines from apt sources.
# Needed because on 2025-06-01 the key/repo management moved to ros2-apt-source.
# Ref: https://discourse.ros.org/t/ros-signing-key-migration-guide/43937
sanitize() {
    local version_codename="${1}"
    local url="http://packages.ros.org/ros2/ubuntu"
    local ros_deb_pattern="^deb.*${url}[[:space:]]+${version_codename}[[:space:]]+main"

    local grep_exit_code
    local matched_files

    matched_files="$(grep \
        --recursive \
        --include='*.list' \
        --files-with-matches \
        --extended-regexp \
        "${ros_deb_pattern}" \
        /etc/apt/)"

    grep_exit_code="${?}"

    # grep uses exit code 1 to report that no line matched. That is the normal
    # result for a clean Ubuntu base image and leaves matched_files empty. Exit
    # code 2 reports a real search or file-reading error.
    if [ "${grep_exit_code}" -gt 1 ]; then
        handle_error "${grep_exit_code}" "Could not inspect apt sources for legacy ROS entries"
    fi

    # Nothing to sanitize, exit early to avoid a spurious empty-string iteration.
    if [ -z "${matched_files}" ]; then
        return 0
    fi

    while IFS= read -r matched_file; do
        # A .list file may contain repositories unrelated to ROS. Remove only
        # the matching ROS lines so sanitizing a base image never removes
        # another vendor's apt configuration from the same file.
        log info "ROS deb line found in '${matched_file}', removing matching lines"
        sed --in-place --regexp-extended "\#${ros_deb_pattern}#d" "${matched_file}" ||
            handle_error 1 "Could not remove the legacy ROS source from '${matched_file}'"

        # Keep /etc/apt/sources.list even when it contains no active entries.
        # Files below sources.list.d are removed only when no text remains.
        # Preserve comments because they may explain local apt configuration.
        if [ "${matched_file}" != "/etc/apt/sources.list" ]; then
            if ! grep --quiet --extended-regexp '[^[:space:]]' "${matched_file}"; then
                log info "Removing '${matched_file}' because it is empty after ROS cleanup"
                rm --force "${matched_file}" ||
                    handle_error 1 "Could not remove empty legacy ROS source file '${matched_file}'"
            fi
        fi
    done <<<"${matched_files}"

    # Do not delete key files referenced by removed lines. Another apt source
    # may share that key, while an unreferenced file in /etc/apt/keyrings is not
    # trusted automatically and is harmless.
}

# --------------------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------------------

# ${BASH_SOURCE:-${0}} uses the current Bash source filename and falls back to
# $0 when BASH_SOURCE is unavailable.
script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing '${script_name}'"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "Script '${script_name}' must run as UID 0; found UID '${executing_user_id}'"
fi

ROS_DISTRO="${1}"
if [ -z "${ROS_DISTRO}" ]; then
    handle_error 1 "No ROS_DISTRO provided. Usage: ${script_name} <ros_distro>"
fi

# shellcheck disable=SC1091
. /etc/os-release || handle_error 1 "Could not load operating-system metadata from '/etc/os-release'"

# shellcheck disable=SC2153
version_codename="${VERSION_CODENAME}"
if [ -z "${version_codename}" ]; then
    handle_error 1 "VERSION_CODENAME is empty in '/etc/os-release'"
fi

# --------------------------------------------------------------------------------------------------
# Check for existing ROS installation.
# --------------------------------------------------------------------------------------------------
installed_package_records="$(dpkg-query --show --showformat='${db:Status-Abbrev} ${binary:Package}\n')" ||
    handle_error 1 "Could not inspect installed packages"

ros_distro_installed="$(awk '
    $1 == "ii" && $2 ~ /^ros-[a-z]+-ros-core(:[^[:space:]]+)?$/ {
        distro = $2
        sub(/^ros-/, "", distro)
        sub(/-ros-core(:.*)?$/, "", distro)
        printf "%s%s", separator, distro
        separator = " "
    }
    END { print "" }
' <<<"${installed_package_records}")" || handle_error 1 "Could not identify an installed ROS distribution"

num_ros_distros="$(awk '{ print NF }' <<<"${ros_distro_installed}")" ||
    handle_error 1 "Could not count installed ROS distributions"

if [ "${num_ros_distros}" -gt 1 ]; then
    handle_error 1 "More than one ROS distro is installed: ${ros_distro_installed}"
fi

if [ "${num_ros_distros}" -eq 1 ]; then
    if [ "${ROS_DISTRO}" != "${ros_distro_installed}" ]; then
        handle_error 1 "Found ROS '${ros_distro_installed}' installed, but '${ROS_DISTRO}' was requested"
    fi
fi

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
    "ros-${ROS_DISTRO}-ros-base"
    "ros-${ROS_DISTRO}-rmw-cyclonedds-cpp"
    "ros-${ROS_DISTRO}-rmw-fastrtps-cpp"
    "ros-${ROS_DISTRO}-rmw-fastrtps-dynamic-cpp"
    "ros-${ROS_DISTRO}-rqt"
    "ros-${ROS_DISTRO}-rqt-common-plugins"
    "ros-${ROS_DISTRO}-rqt-tf-tree"
    "ros-${ROS_DISTRO}-rviz2"
    "ros-${ROS_DISTRO}-xacro"
    ros-dev-tools
)

# --------------------------------------------------------------------------------------------------
# Remove legacy apt sources and install ROS 2.
# --------------------------------------------------------------------------------------------------
sanitize "${version_codename}"

apt-get update --quiet --quiet || handle_error 1 "apt-get update failed"
install_pkgs apt-utils || handle_error 1 "Failed to install apt-utils"
install_pkgs python3-software-properties software-properties-common ||
    handle_error 1 "Failed to install add-apt-repository dependencies"
add-apt-repository --yes universe || handle_error 1 "Adding universe repository failed"
install_pkgs curl gpg || handle_error 1 "Failed to install ROS repository key dependencies"

ros_list_file=""
ros_apt_source_package="ros2-apt-source"

if ! dpkg --status "${ros_apt_source_package}" >/dev/null 2>&1; then
    gpg_dir="/etc/apt/keyrings"
    # Use a project-specific bootstrap filename. It cannot collide with a
    # legacy ROS key that sanitize deliberately leaves untouched.
    gpg_file="${gpg_dir}/robotics-dockers-ros-bootstrap.gpg"

    if [ -e "${gpg_file}" ]; then
        handle_error 1 "Temporary ROS key path '${gpg_file}' already exists"
    fi

    if [ ! -d "${gpg_dir}" ]; then
        log info "Creating directory '${gpg_dir}'"
        mkdir --verbose --parent "${gpg_dir}" || handle_error 1 "Failed to create '${gpg_dir}'"
    fi

    # Download and convert the key as two explicit operations. A pipeline would
    # report only the status of its last command unless pipefail were enabled,
    # which could hide a failed download behind a successful gpg invocation.
    downloaded_ros_key="$(mktemp)" || handle_error 1 "Could not create a temporary file for the ROS key"
    curl --fail --silent --show-error --location \
        --output "${downloaded_ros_key}" \
        https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc || {
        rm -f -- "${downloaded_ros_key}"
        handle_error 1 "Downloading the ROS 2 GPG key failed"
    }
    gpg --dearmor --output "${gpg_file}" "${downloaded_ros_key}" || {
        rm -f -- "${downloaded_ros_key}"
        handle_error 1 "Converting the ROS 2 GPG key failed"
    }
    rm -f -- "${downloaded_ros_key}" || handle_error 1 "Could not remove the temporary ROS key"

    chmod 644 "${gpg_file}" || handle_error 1 "Failed to set permissions on '${gpg_file}'"

    url="http://packages.ros.org/ros2/ubuntu"
    ros_deb_line="deb [arch=$(dpkg --print-architecture) signed-by=${gpg_file}] ${url} ${version_codename} main"
    ros_list_file="/etc/apt/sources.list.d/ros.list"

    log info "Adding ROS 2 deb line to '${ros_list_file}'"
    echo "${ros_deb_line}" | tee "${ros_list_file}" >/dev/null ||
        handle_error 1 "Failed to write ROS 2 deb line to '${ros_list_file}'"

    packages+=("${ros_apt_source_package}")
fi

apt-get update --quiet --quiet || handle_error 1 "apt-get update failed"
install_pkgs "${packages[@]}" || handle_error 1 "Failed to install ROS 2 packages"

# If we created a temporary list file, remove it and its GPG key now that ros2-apt-source
# manages repository and key going forward.
if [ -n "${ros_list_file}" ]; then
    rm -f "${ros_list_file}" ||
        handle_error 1 "Could not remove temporary ROS source '${ros_list_file}'"

    rm -f "${gpg_file}" ||
        handle_error 1 "Could not remove temporary ROS key '${gpg_file}'"
fi

log info "Removing installation residues from apt cache"
apt-get clean >/dev/null || handle_error 1 "Failed to clean the apt cache"
find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Failed to remove apt package indexes"
