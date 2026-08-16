#!/usr/bin/env bash
# Install optional Python and Rust tooling as the real development user.
#
# The Dockerfile invokes this script before installing the project's NOPASSWD
# sudo rule. Project hooks therefore cannot acquire root privileges through the
# policy created by robotics-dockers. The image author still controls the base
# image and Dockerfile, so this ordering is a guard against mistakes rather than
# a security boundary against a hostile image author.

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

if [ "$#" -ne 1 ]; then
    handle_error 2 "Usage: install_user_extras.sh EXTRA_DIRECTORY"
fi

extra_directory="${1}"

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is installing user extras"

if [ "${executing_user_id}" -eq 0 ]; then
    handle_error 1 "User extras must not run as UID 0"
fi

if [ -z "${HOME:-}" ]; then
    handle_error 1 "HOME is empty"
fi

if [ ! -d "${HOME}" ]; then
    handle_error 1 "HOME '${HOME}' does not identify an existing development-user home"
fi

if [ ! -d "${extra_directory}" ]; then
    handle_error 1 "Extra directory '${extra_directory}' does not exist"
fi

export PYTHONUSERBASE="${HOME}/.local"
case ":${PATH}:" in
*:"${HOME}/.local/bin":*) ;;
*) export PATH="${HOME}/.local/bin:${PATH}" ;;
esac

requirements_file="${extra_directory}/python/requirements.txt"
if [ -L "${requirements_file}" ]; then
    handle_error 1 "Python requirements '${requirements_file}' must not be a symbolic link"
fi

if [ ! -f "${requirements_file}" ]; then
    handle_error 1 "Python requirements '${requirements_file}' does not exist or is not a regular file"
fi

pip_arguments=(--user --no-cache-dir --disable-pip-version-check)
if /usr/bin/python3 -m pip install --help 2>/dev/null | grep --quiet -- '--break-system-packages'; then
    pip_arguments+=(--break-system-packages)
fi

log info "Installing editable Python requirements from '${requirements_file}'"
/usr/bin/python3 -m pip install "${pip_arguments[@]}" --requirement "${requirements_file}" ||
    handle_error 1 "Could not install Python requirements from '${requirements_file}'"

python_hooks_directory="${extra_directory}/python/install.d"
if [ -L "${python_hooks_directory}" ]; then
    handle_error 1 "Python hook directory '${python_hooks_directory}' must not be a symbolic link"
fi

if [ ! -d "${python_hooks_directory}" ]; then
    handle_error 1 "Python hook directory '${python_hooks_directory}' does not exist or is not a directory"
fi

if [ ! -r "${python_hooks_directory}" ]; then
    handle_error 1 "Python hook directory '${python_hooks_directory}' is not readable"
fi

if [ ! -x "${python_hooks_directory}" ]; then
    handle_error 1 "Python hook directory '${python_hooks_directory}' is not searchable"
fi

if find "${python_hooks_directory}" -mindepth 1 -type l -print -quit | grep -q .; then
    handle_error 1 "Symbolic links are not allowed in '${python_hooks_directory}'"
fi

while IFS= read -r -d '' python_hook; do
    # ${python_hook##*/} removes the directory prefix and leaves the hook
    # filename for the human-readable build log.
    log info "Running Python customization hook '${python_hook##*/}'"
    bash "${python_hook}" || handle_error 1 "Python customization hook '${python_hook}' failed"
done < <(find "${python_hooks_directory}" -maxdepth 1 -type f -name '*.sh' -print0 | LC_ALL=C sort -z)

rust_install_script="${extra_directory}/rust/install.sh"
if [ -L "${rust_install_script}" ]; then
    handle_error 1 "Rust installer '${rust_install_script}' must not be a symbolic link"
elif [ -f "${rust_install_script}" ]; then
    log info "Running the project-selected Rust installer"
    bash "${rust_install_script}" || handle_error 1 "Rust installer '${rust_install_script}' failed"
else
    log info "No active Rust installer found; '${rust_install_script}' is optional"
fi
