#!/usr/bin/env bash
# Grant the configured development account explicit passwordless sudo access.
#
# The account is deliberately not added to Ubuntu's sudo group. A named rule is
# easier to inspect and avoids combining the project policy with the distribution
# policy, which normally expects a usable password.

handle_error() {
    local exit_code="${1:-1}"
    shift || true
    printf 'Error: %s (exit code: %s)\n' "${*:-Unknown error}" "${exit_code}" >&2
    exit "${exit_code}"
}

if [ "$#" -ne 1 ]; then
    handle_error 2 "Usage: configure_sudo.sh USER"
fi

development_user="${1}"

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is configuring sudo"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "Sudo configuration must run as UID 0; found UID '${executing_user_id}'"
fi

getent passwd "${development_user}" >/dev/null 2>&1 ||
    handle_error 1 "User '${development_user}' does not exist"

temporary_sudoers="$(mktemp)" || handle_error 1 "Could not create a temporary sudoers file"

trap 'rm -f -- "${temporary_sudoers}"' EXIT

printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "${development_user}" >"${temporary_sudoers}" ||
    handle_error 1 "Could not prepare the sudo rule for '${development_user}'"

visudo --check --file "${temporary_sudoers}" >/dev/null 2>&1 ||
    handle_error 1 "The generated sudo rule is invalid"

install --owner root --group root --mode 0440 "${temporary_sudoers}" "/etc/sudoers.d/robotics-dockers-${development_user}" ||
    handle_error 1 "Could not install the sudo rule for '${development_user}'"
