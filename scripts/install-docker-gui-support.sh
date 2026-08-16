#!/usr/bin/env bash

set -o pipefail

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

# ---------------------------------------------------------------------------
# install_docker_gui_support.sh
#
# Installs the host-side support required to display X11 applications from
# Docker containers in a Wayland desktop session through XWayland.
#
# The installer:
#
#   1. Validates that it is running as a regular user in a Wayland session.
#   2. Installs the xauth and xwayland host packages when required.
#   3. Installs /usr/local/bin/set-xauth-cookies.sh. This helper reads the
#      current XWayland authentication records and generates:
#
#        ${XDG_RUNTIME_DIR}/docker-xwayland.xauth
#
#   4. Installs and enables the set-xauth-cookies.service systemd user unit.
#   5. Starts the service immediately and validates the generated file.
#
# The generated Xauthority file can later be mounted read-only into Docker
# containers. Docker Compose configuration is intentionally outside the scope
# of this host installer.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Validate the installer execution context
# ---------------------------------------------------------------------------

if [ "${EUID}" -eq 0 ]; then
    handle_error 1 "Run this installer as the desktop user, not as root"
fi

if ! command -v sudo >/dev/null 2>&1; then
    handle_error 1 "sudo is not installed"
fi

if ! command -v systemctl >/dev/null 2>&1; then
    handle_error 1 "systemctl is not installed"
fi

if [ "${XDG_SESSION_TYPE}" != "wayland" ]; then
    handle_error 1 "This installer must be run from a Wayland session"
fi

if [ -z "${WAYLAND_DISPLAY}" ]; then
    handle_error 1 "WAYLAND_DISPLAY is not set"
fi

if [ -z "${DISPLAY}" ]; then
    handle_error 1 "DISPLAY is not set; XWayland is unavailable"
fi

# A normal systemd desktop session uses /run/user/<UID>. Some ways of
# launching this installer do not preserve the exported XDG_RUNTIME_DIR even
# though the standard runtime directory already exists. Derive only the
# missing variable; never replace a non-empty value supplied by the session.
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    desktop_user_id="$(id --user)" ||
        handle_error 1 "Failed to obtain the desktop user ID"

    XDG_RUNTIME_DIR="/run/user/${desktop_user_id}"
fi

if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "User runtime directory does not exist: ${XDG_RUNTIME_DIR}"
fi

# systemctl --user import-environment reads exported process variables. Export
# the derived value so the installed user service receives the same directory.
export XDG_RUNTIME_DIR

if [ -z "${XAUTHORITY}" ]; then
    handle_error 1 "XAUTHORITY is not set"
fi

if [ ! -r "${XAUTHORITY}" ]; then
    handle_error 1 "XAUTHORITY is not readable: ${XAUTHORITY}"
fi

# ---------------------------------------------------------------------------
# Acquire sudo credentials once before changing system files
# ---------------------------------------------------------------------------

log info "Requesting administrative privileges"

sudo -v || handle_error 1 "Failed to acquire administrative privileges"

# ---------------------------------------------------------------------------
# Install host dependencies
# ---------------------------------------------------------------------------

required_packages=(
    xauth
    xwayland
)
missing_packages=()

for package in "${required_packages[@]}"; do
    if dpkg -s "${package}" >/dev/null 2>&1; then
        log info "Package '${package}' already installed. Skipping."
    else
        missing_packages+=("${package}")
    fi
done

# ${#missing_packages[@]} returns the number of missing package names.
if [ "${#missing_packages[@]}" -gt 0 ]; then
    log info "Updating package indexes"

    sudo apt-get update || handle_error 1 "Failed to update package indexes"

    # ${missing_packages[*]} joins the array elements with spaces for display.
    log info "Installing packages: ${missing_packages[*]}"

    sudo apt-get install --yes --no-install-recommends "${missing_packages[@]}" ||
        handle_error 1 "Failed to install required packages"
fi

# ---------------------------------------------------------------------------
# Install /usr/local/bin/set-xauth-cookies.sh
# ---------------------------------------------------------------------------

script_name="set-xauth-cookies.sh"
qualified_script="/usr/local/bin/${script_name}"
log info "Installing '${qualified_script}'"

# The current shell supplies the heredoc as standard input. sudo gives tee the
# permission needed to write under /usr/local/bin. tee also copies its input to
# standard output, which is discarded because only the installed file is needed.
if ! sudo tee "${qualified_script}" >/dev/null <<'SCRIPT_EOF'; then
#!/usr/bin/env bash

set -o pipefail

# ---------------------------------------------------------------------------
# set-xauth-cookies.sh
#
# Generates an Xauthority file that Docker containers can use to connect to
# XWayland from the current Wayland desktop session.
#
# Generated file:
#
#   ${XDG_RUNTIME_DIR}/docker-xwayland.xauth
#
# The authentication cookie is never printed to standard output or logs.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

handle_error() {
    local exit_code="${1:-1}"
    local error_message="${2:-Unknown error}"

    printf 'set-xauth-cookies.sh: %s\n' "${error_message}" >&2
    exit "${exit_code}"
}

# ---------------------------------------------------------------------------
# Validate the graphical session environment
# ---------------------------------------------------------------------------

if [ -z "${WAYLAND_DISPLAY}" ]; then
    handle_error 1 "WAYLAND_DISPLAY is not set"
fi

if [ -z "${DISPLAY}" ]; then
    handle_error 1 "DISPLAY is not set; XWayland is unavailable"
fi

# systemd normally provides XDG_RUNTIME_DIR to user services. Derive the
# standard path as a fallback so this helper also works when the user manager
# did not receive that variable from the graphical session.
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    desktop_user_id="$(id --user)" ||
        handle_error 1 "Failed to obtain the desktop user ID"

    XDG_RUNTIME_DIR="/run/user/${desktop_user_id}"
fi

if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "User runtime directory does not exist: ${XDG_RUNTIME_DIR}"
fi

if [ -z "${XAUTHORITY}" ]; then
    handle_error 1 "XAUTHORITY is not set"
fi

if [ ! -r "${XAUTHORITY}" ]; then
    handle_error 1 "XAUTHORITY is not readable: ${XAUTHORITY}"
fi

# ---------------------------------------------------------------------------
# Define the destination and temporary files
# ---------------------------------------------------------------------------

xauth_file="${XDG_RUNTIME_DIR}/docker-xwayland.xauth"

temporary_file="$(mktemp "${XDG_RUNTIME_DIR}/docker-xwayland.xauth.XXXXXX")" ||
    handle_error 1 "Failed to create a temporary Xauthority file"

# ---------------------------------------------------------------------------
# Remove the temporary file if the script exits before completing
# ---------------------------------------------------------------------------

cleanup() {
    if [ -f "${temporary_file}" ]; then
        rm -f "${temporary_file}"
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Read and prepare the authentication records
#
# xauth nlist returns the authentication entries in numeric form.
#
# Replacing the first four characters with "ffff" changes the address family
# to FamilyWild. This permits the cookie to work when the container hostname
# differs from the host hostname.
#
# Mutter may expose multiple records that become identical after this
# conversion, so sort -u removes duplicates.
# ---------------------------------------------------------------------------

xauth_records="$(
    xauth -f "${XAUTHORITY}" nlist "${DISPLAY}" 2>/dev/null |
        sed -e 's/^..../ffff/' |
        sort -u
)" || handle_error 1 "Failed to read authentication records for DISPLAY=${DISPLAY}"

if [ -z "${xauth_records}" ]; then
    handle_error 1 \
        "No authentication records were found for DISPLAY=${DISPLAY}"
fi

# ---------------------------------------------------------------------------
# Import the records into the temporary Xauthority file
# ---------------------------------------------------------------------------

printf '%s\n' "${xauth_records}" |
    xauth -f "${temporary_file}" nmerge - 2>/dev/null ||
    handle_error 1 "Failed to create the Docker Xauthority file"

# ---------------------------------------------------------------------------
# Set file permissions
#
# XDG_RUNTIME_DIR remains private to the host user. The file itself is made
# readable so a container process can read it when its numeric user ID differs
# from the host user ID.
# ---------------------------------------------------------------------------

chmod 0644 "${temporary_file}" ||
    handle_error 1 "Failed to set permissions on the temporary Xauthority file"

# ---------------------------------------------------------------------------
# Atomically replace the previous Docker Xauthority file
# ---------------------------------------------------------------------------

mv -f "${temporary_file}" "${xauth_file}" || handle_error 1 "Failed to install ${xauth_file}"

trap - EXIT

# ---------------------------------------------------------------------------
# Validate the generated file without printing authentication credentials
# ---------------------------------------------------------------------------

if [ ! -s "${xauth_file}" ]; then
    handle_error 1 "The installed Xauthority file is empty: ${xauth_file}"
fi

xauth_entries="$(xauth -f "${xauth_file}" nlist 2>/dev/null)" ||
    handle_error 1 "Failed to validate ${xauth_file}"

if [ -z "${xauth_entries}" ]; then
    handle_error 1 "No authentication records were written to ${xauth_file}"
fi

printf 'Created %s for DISPLAY=%s\n' "${xauth_file}" "${DISPLAY}"
SCRIPT_EOF
    handle_error 1 "Failed to write '${qualified_script}'"
fi

sudo chmod 0755 "${qualified_script}" || handle_error 1 "Failed to make '${qualified_script}' executable"

# ---------------------------------------------------------------------------
# Install the systemd user service
# ---------------------------------------------------------------------------

service_name="set-xauth-cookies"
# ${XDG_CONFIG_HOME:-${HOME}/.config} uses XDG_CONFIG_HOME when it is set and
# otherwise builds the standard per-user configuration path below HOME.
systemd_user_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
service_file="${systemd_user_dir}/${service_name}.service"

log info "Installing systemd user service '${service_file}'"

mkdir -p "${systemd_user_dir}" || handle_error 1 "Failed to create '${systemd_user_dir}'"

if ! cat >"${service_file}" <<'SERVICE_EOF'; then
[Unit]
Description=Generate XWayland authentication file for Docker containers
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/set-xauth-cookies.sh
RemainAfterExit=yes

[Install]
WantedBy=graphical-session.target
SERVICE_EOF
    handle_error 1 "Failed to write '${service_file}'"
fi

chmod 0644 "${service_file}" || handle_error 1 "Failed to set permissions on '${service_file}'"

# ---------------------------------------------------------------------------
# Import the current session environment and start the service
# ---------------------------------------------------------------------------

log info "Importing graphical session variables into the systemd user manager"

systemctl --user import-environment \
    DISPLAY \
    WAYLAND_DISPLAY \
    XAUTHORITY \
    XDG_RUNTIME_DIR || handle_error 1 "Failed to import the graphical session environment"

systemctl --user daemon-reload || handle_error 1 "Failed to reload the systemd user manager"

systemctl --user enable "${service_name}.service" ||
    handle_error 1 "Failed to enable '${service_name}.service'"

systemctl --user restart "${service_name}.service" ||
    handle_error 1 "Failed to start '${service_name}.service'"

# `enable` and `restart` already returned success. The helper invoked by the
# service also validates the generated Xauthority file before it exits, so the
# installer does not repeat those same checks here.
xauth_file="${XDG_RUNTIME_DIR}/docker-xwayland.xauth"

log info "Docker GUI support for Wayland/XWayland was installed successfully."
log info "Service: ${service_name}.service"
log info "Xauthority file: ${xauth_file}"
log info "Done."
