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

    log error "${error_message} (exit code: ${exit_code})"
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

if [ -z "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "XDG_RUNTIME_DIR is not set"
fi

if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "XDG_RUNTIME_DIR does not exist: ${XDG_RUNTIME_DIR}"
fi

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

if ! sudo -v; then
    handle_error 1 "Failed to acquire administrative privileges"
fi

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

if [ "${#missing_packages[@]}" -gt 0 ]; then
    log info "Updating package indexes"

    if ! sudo apt-get update; then
        handle_error 1 "Failed to update package indexes"
    fi

    log info "Installing packages: ${missing_packages[*]}"

    if ! sudo apt-get install --yes --no-install-recommends "${missing_packages[@]}"; then
        handle_error 1 "Failed to install required packages"
    fi
fi

if ! command -v xauth >/dev/null 2>&1; then
    handle_error 1 "xauth is unavailable after package installation"
fi

# ---------------------------------------------------------------------------
# Install /usr/local/bin/set-xauth-cookies.sh
# ---------------------------------------------------------------------------

script_name="set-xauth-cookies.sh"
qualified_script="/usr/local/bin/${script_name}"
log info "Installing '${qualified_script}'"

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
# Validate required commands
# ---------------------------------------------------------------------------

if ! command -v xauth >/dev/null 2>&1; then
    handle_error 1 "xauth is not installed"
fi

if ! command -v sed >/dev/null 2>&1; then
    handle_error 1 "sed is not installed"
fi

if ! command -v sort >/dev/null 2>&1; then
    handle_error 1 "sort is not installed"
fi

if ! command -v mktemp >/dev/null 2>&1; then
    handle_error 1 "mktemp is not installed"
fi

# ---------------------------------------------------------------------------
# Validate the graphical session environment
# ---------------------------------------------------------------------------

if [ -z "${WAYLAND_DISPLAY}" ]; then
    handle_error 1 "WAYLAND_DISPLAY is not set"
fi

if [ -z "${DISPLAY}" ]; then
    handle_error 1 "DISPLAY is not set; XWayland is unavailable"
fi

if [ -z "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "XDG_RUNTIME_DIR is not set"
fi

if [ ! -d "${XDG_RUNTIME_DIR}" ]; then
    handle_error 1 "XDG_RUNTIME_DIR does not exist: ${XDG_RUNTIME_DIR}"
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

if ! temporary_file="$(mktemp "${XDG_RUNTIME_DIR}/docker-xwayland.xauth.XXXXXX")"; then
    handle_error 1 "Failed to create a temporary Xauthority file"
fi

# ---------------------------------------------------------------------------
# Remove the temporary file if the script exits before completing
# ---------------------------------------------------------------------------

cleanup() {
    if [ -n "${temporary_file}" ] && [ -f "${temporary_file}" ]; then
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

if ! xauth_records="$(
    xauth -f "${XAUTHORITY}" nlist "${DISPLAY}" 2>/dev/null |
        sed -e 's/^..../ffff/' |
        sort -u
)"; then
    handle_error 1 \
        "Failed to read authentication records for DISPLAY=${DISPLAY}"
fi

if [ -z "${xauth_records}" ]; then
    handle_error 1 \
        "No authentication records were found for DISPLAY=${DISPLAY}"
fi

# ---------------------------------------------------------------------------
# Import the records into the temporary Xauthority file
# ---------------------------------------------------------------------------

if ! printf '%s\n' "${xauth_records}" |
    xauth -f "${temporary_file}" nmerge - 2>/dev/null; then
    handle_error 1 "Failed to create the Docker Xauthority file"
fi

if [ ! -s "${temporary_file}" ]; then
    handle_error 1 "The generated Xauthority file is empty"
fi

# ---------------------------------------------------------------------------
# Set file permissions
#
# XDG_RUNTIME_DIR remains private to the host user. The file itself is made
# readable so a container process can read it when its numeric user ID differs
# from the host user ID.
# ---------------------------------------------------------------------------

if ! chmod 0644 "${temporary_file}"; then
    handle_error 1 \
        "Failed to set permissions on the temporary Xauthority file"
fi

# ---------------------------------------------------------------------------
# Atomically replace the previous Docker Xauthority file
# ---------------------------------------------------------------------------

if ! mv -f "${temporary_file}" "${xauth_file}"; then
    handle_error 1 "Failed to install ${xauth_file}"
fi

trap - EXIT

# ---------------------------------------------------------------------------
# Validate the generated file without printing authentication credentials
# ---------------------------------------------------------------------------

if [ ! -s "${xauth_file}" ]; then
    handle_error 1 "The installed Xauthority file is empty: ${xauth_file}"
fi

if ! xauth_entries="$(xauth -f "${xauth_file}" nlist 2>/dev/null)"; then
    handle_error 1 "Failed to validate ${xauth_file}"
fi

if [ -z "${xauth_entries}" ]; then
    handle_error 1 "No authentication records were written to ${xauth_file}"
fi

printf 'Created %s for DISPLAY=%s\n' "${xauth_file}" "${DISPLAY}"
SCRIPT_EOF
    handle_error 1 "Failed to write '${qualified_script}'"
fi

if ! sudo chmod 0755 "${qualified_script}"; then
    handle_error 1 "Failed to make '${qualified_script}' executable"
fi

# ---------------------------------------------------------------------------
# Install the systemd user service
# ---------------------------------------------------------------------------

service_name="set-xauth-cookies"
systemd_user_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
service_file="${systemd_user_dir}/${service_name}.service"

log info "Installing systemd user service '${service_file}'"

if ! mkdir -p "${systemd_user_dir}"; then
    handle_error 1 "Failed to create '${systemd_user_dir}'"
fi

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

if ! chmod 0644 "${service_file}"; then
    handle_error 1 "Failed to set permissions on '${service_file}'"
fi

# ---------------------------------------------------------------------------
# Import the current session environment and start the service
# ---------------------------------------------------------------------------

log info "Importing graphical session variables into the systemd user manager"

if ! systemctl --user import-environment \
    DISPLAY \
    WAYLAND_DISPLAY \
    XAUTHORITY \
    XDG_RUNTIME_DIR; then
    handle_error 1 "Failed to import the graphical session environment"
fi

if ! systemctl --user daemon-reload; then
    handle_error 1 "Failed to reload the systemd user manager"
fi

if ! systemctl --user enable "${service_name}.service"; then
    handle_error 1 "Failed to enable '${service_name}.service'"
fi

if ! systemctl --user restart "${service_name}.service"; then
    handle_error 1 "Failed to start '${service_name}.service'"
fi

# ---------------------------------------------------------------------------
# Validate the installation
# ---------------------------------------------------------------------------

if [ ! -x "${qualified_script}" ]; then
    handle_error 1 "Installed helper is not executable: ${qualified_script}"
fi

if ! systemctl --user is-enabled --quiet "${service_name}.service"; then
    handle_error 1 "The systemd user service is not enabled"
fi

if ! systemctl --user is-active --quiet "${service_name}.service"; then
    handle_error 1 "The systemd user service did not complete successfully"
fi

xauth_file="${XDG_RUNTIME_DIR}/docker-xwayland.xauth"

if [ ! -s "${xauth_file}" ]; then
    handle_error 1 "The Docker Xauthority file was not generated: ${xauth_file}"
fi

if ! xauth_entries="$(xauth -f "${xauth_file}" nlist 2>/dev/null)"; then
    handle_error 1 "Failed to validate the generated Xauthority file"
fi

if [ -z "${xauth_entries}" ]; then
    handle_error 1 "The generated Xauthority file contains no entries"
fi

log info "Docker GUI support for Wayland/XWayland was installed successfully."
log info "Service: ${service_name}.service"
log info "Xauthority file: ${xauth_file}"
log info "Done."
