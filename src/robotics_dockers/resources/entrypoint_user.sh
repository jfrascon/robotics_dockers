#!/usr/bin/env bash
# Load the development environment only for the exact identity built into the image.
#
# Docker can override USER, the primary group, WORKDIR or ENTRYPOINT independently.
# Treating every other UID:GID as an intentional override keeps the image useful for
# root administration and for accounts inherited from a base image without applying
# the development user's HOME or ROS configuration to those processes.

LOG_FILE=""

log() {
    local level="${1:-info}"
    shift || true
    local line
    line="[$(date --utc '+%Y-%m-%dT%H:%M:%SZ')] [${level}] $*"
    printf '%s\n' "${line}"
    if [ -n "${LOG_FILE}" ]; then
        printf '%s\n' "${line}" >>"${LOG_FILE}"
    fi
}

handle_error() {
    local exit_code="${1:-1}"
    shift || true
    log error "${*:-Unknown error} (exit code: ${exit_code})" >&2
    exit "${exit_code}"
}

nvidia_gpu_driver_check() {
    local libcuda_from_cache=""
    local libcuda_from_path=""
    local library_path
    local library_paths=()
    local nvidia_device=""
    local nvidia_device_path

    if command -v ldconfig >/dev/null 2>&1; then
        if ! libcuda_from_cache="$(ldconfig -p 2>/dev/null | grep 'libcuda.so.1')"; then
            libcuda_from_cache=""
        fi
    fi

    if [ -n "${LD_LIBRARY_PATH:-}" ]; then
        IFS=: read -ra library_paths <<<"${LD_LIBRARY_PATH}"
        for library_path in "${library_paths[@]}"; do
            if [ -z "${library_path}" ]; then
                continue
            fi

            if [ -e "${library_path}/libcuda.so.1" ]; then
                if [[ ${library_path} != *compat* ]]; then
                    libcuda_from_path="${library_path}/libcuda.so.1"
                    break
                fi
            fi
        done
    fi

    if [ -z "${libcuda_from_cache}" ]; then
        if [ -z "${libcuda_from_path}" ]; then
            handle_error 1 "The image expects the host NVIDIA driver, but libcuda.so.1 was not detected. Start the container with NVIDIA Container Toolkit GPU access"
        fi
    fi

    for nvidia_device_path in /dev/nvidiactl /dev/dxg /dev/nvgpu; do
        if [ -e "${nvidia_device_path}" ]; then
            nvidia_device="${nvidia_device_path}"
            break
        fi
    done

    if [ -z "${nvidia_device}" ]; then
        handle_error 1 "The image expects the host NVIDIA driver, but no supported NVIDIA device was detected. Start the container with NVIDIA Container Toolkit GPU access"
    fi

    log info "NVIDIA driver libraries and device '${nvidia_device}' are available"
}

for required_variable in \
    ROBOTICS_DOCKERS_USER \
    ROBOTICS_DOCKERS_USER_ID \
    ROBOTICS_DOCKERS_USER_HOME \
    ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID; do
    # ${!required_variable:-} reads the variable whose name is stored in
    # required_variable and produces an empty value when that variable is unset.
    if [ -z "${!required_variable:-}" ]; then
        handle_error 1 "Required image variable '${required_variable}' is empty"
    fi
done

if [ "$#" -eq 0 ]; then
    handle_error 2 "No command was provided to the container entrypoint"
fi

executing_user_id="$(id --user 2>/dev/null)" || handle_error 1 "Could not determine the executing UID"

executing_user_primary_group_id="$(id --group 2>/dev/null)" ||
    handle_error 1 "Could not determine the executing primary GID"

if [ "${executing_user_id}" != "${ROBOTICS_DOCKERS_USER_ID}" ]; then
    log info "Executing the requested command without the robotics-dockers development environment because UID '${executing_user_id}' differs from configured UID '${ROBOTICS_DOCKERS_USER_ID}'"
    exec "$@"
fi

if [ "${executing_user_primary_group_id}" != "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID}" ]; then
    log info "Executing the requested command without the robotics-dockers development environment because primary GID '${executing_user_primary_group_id}' differs from configured primary GID '${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID}'"
    exec "$@"
fi

# configure_image_user.sh established the local account during the image build.
# At runtime only the executing numeric identity matters: Docker may add
# supplementary groups, but that does not change the UID or primary GID checked
# above. Re-reading /etc/passwd and /etc/group here would repeat the build check
# on every container start without making the runtime decision safer.
export HOME="${ROBOTICS_DOCKERS_USER_HOME}"
export USER="${ROBOTICS_DOCKERS_USER}"
export LOGNAME="${ROBOTICS_DOCKERS_USER}"
export SHELL="/bin/bash"

log_directory="${HOME}/.local/state/robotics-dockers"
mkdir --parents -- "${log_directory}" || handle_error 1 "Could not create entrypoint log directory '${log_directory}'"

chmod 0755 -- "${log_directory}" || handle_error 1 "Could not set mode 0755 on '${log_directory}'"

LOG_FILE="${log_directory}/entrypoint.log"

: >"${LOG_FILE}" || handle_error 1 "Could not create entrypoint log '${LOG_FILE}'"

chmod 0644 -- "${LOG_FILE}" || handle_error 1 "Could not set mode 0644 on '${LOG_FILE}'"

log info "Loading the development environment for '${ROBOTICS_DOCKERS_USER}' (${ROBOTICS_DOCKERS_USER_ID}:${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID})"

if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
    expected_xdg_runtime_directory="/run/user/${ROBOTICS_DOCKERS_USER_ID}"
    if [ "${XDG_RUNTIME_DIR}" != "${expected_xdg_runtime_directory}" ]; then
        handle_error 1 "XDG_RUNTIME_DIR is '${XDG_RUNTIME_DIR}'; expected '${expected_xdg_runtime_directory}'"
    fi

    if [ -L "${XDG_RUNTIME_DIR}" ]; then
        handle_error 1 "XDG_RUNTIME_DIR '${XDG_RUNTIME_DIR}' must not be a symbolic link"
    fi

    if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
        handle_error 1 "XDG_RUNTIME_DIR '${XDG_RUNTIME_DIR}' must already exist as a directory"
    fi

    xdg_owner="$(stat --format '%u:%g' -- "${XDG_RUNTIME_DIR}")" ||
        handle_error 1 "Could not read XDG_RUNTIME_DIR ownership"

    if [ "${xdg_owner}" != "${ROBOTICS_DOCKERS_USER_ID}:${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID}" ]; then
        handle_error 1 "XDG_RUNTIME_DIR belongs to '${xdg_owner}'; expected '${ROBOTICS_DOCKERS_USER_ID}:${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID}'"
    fi

    xdg_mode="$(stat --format '%a' -- "${XDG_RUNTIME_DIR}")" ||
        handle_error 1 "Could not read XDG_RUNTIME_DIR mode"

    if [ "${xdg_mode}" != "700" ]; then
        handle_error 1 "XDG_RUNTIME_DIR mode is '${xdg_mode}'; expected '700'"
    fi

    log info "XDG_RUNTIME_DIR '${XDG_RUNTIME_DIR}' is valid"
else
    log info "XDG_RUNTIME_DIR is not defined; continuing because the requested command may not require it"
fi

if [ "${USE_HOST_NVIDIA_DRIVER:-false}" = "true" ]; then
    nvidia_gpu_driver_check
fi

if [ ! -f "${HOME}/.env.rc" ]; then
    handle_error 1 "Required environment file '${HOME}/.env.rc' does not exist"
fi

# shellcheck disable=SC1091
. "${HOME}/.env.rc" || handle_error 1 "Could not load required environment file '${HOME}/.env.rc'"

log info "Development environment loaded; executing requested command"
exec "$@"
