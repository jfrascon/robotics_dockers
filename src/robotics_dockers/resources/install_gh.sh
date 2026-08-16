#!/usr/bin/env bash

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

# ${BASH_SOURCE:-${0}} uses the current Bash source filename and falls back to
# $0 when BASH_SOURCE is unavailable.
script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing '${script_name}'"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "Script '${script_name}' must run as UID 0; found UID '${executing_user_id}'"
fi

gpg_dir="/etc/apt/keyrings"
gpg_file="${gpg_dir}/githubcli-archive-keyring.gpg"
list_file="/etc/apt/sources.list.d/github-cli.list"
url="https://cli.github.com/packages"

log info "Installing GitHub CLI APT keyring to '${gpg_file}'"

mkdir --parent --mode 755 "${gpg_dir}" || handle_error 1 "Failed to create '${gpg_dir}'"

tmp_keyring="$(mktemp)" || handle_error 1 "Failed to create temporary keyring file"
trap 'rm -f "${tmp_keyring}"' EXIT

wget --no-verbose --output-document "${tmp_keyring}" "${url}/githubcli-archive-keyring.gpg" ||
    handle_error 1 "Failed to download GitHub CLI APT keyring"

install --owner root --group root --mode 644 "${tmp_keyring}" "${gpg_file}" ||
    handle_error 1 "Failed to install GitHub CLI APT keyring"

mkdir --parent --mode 755 "$(dirname "${list_file}")" ||
    handle_error 1 "Failed to create '$(dirname "${list_file}")'"

architecture="$(dpkg --print-architecture)" || handle_error 1 "Failed to detect Debian architecture"
deb_line="deb [arch=${architecture} signed-by=${gpg_file}] ${url} stable main"

log info "Writing GitHub CLI APT source to '${list_file}'"
echo "${deb_line}" >"${list_file}" || handle_error 1 "Failed to write '${list_file}'"

apt-get update --quiet --quiet || handle_error 1 "apt-get update failed after adding GitHub CLI source"
install_pkgs gh || handle_error 1 "Failed to install GitHub CLI"

apt-get clean >/dev/null || handle_error 1 "Failed to clean the apt cache after GitHub CLI installation"
find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Failed to remove apt package indexes after GitHub CLI installation"

log info "GitHub CLI installed successfully"
