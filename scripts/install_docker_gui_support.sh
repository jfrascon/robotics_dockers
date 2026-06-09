#!/usr/bin/env bash

set -euo pipefail

log() {
    local type="${1:-info}"
    local message="${2:-}"
    printf '[%s] [%s] %s\n' \
        "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" \
        "${type}" \
        "${message}"
}

# ---------------------------------------------------------------------------
# install_docker_gui_support.sh
#
# Installs GUI support for Docker containers by:
#
#   1. Installing xwayland (if available).
#   2. Installing /usr/local/bin/set_xauth_cookies.sh — a script that generates
#      ${XDG_RUNTIME_DIR}/cookies.xauth with the X11 authentication tokens required by
#      Docker containers to access the host display.
#   3. Installing a systemd user service (set-xauth-cookies.service) that
#      runs the script automatically when a graphical session starts, via
#      graphical-session.target.
#   4. Running the script immediately so that GUI support is available without
#      requiring a re-login.
#
# The systemd user service approach replaces the legacy XDG autostart
# (.desktop) mechanism. It is more robust, universal across desktop
# environments, and allows manual control:
#
#   systemctl --user restart set-xauth-cookies   # force cookie regeneration
#   systemctl --user status  set-xauth-cookies   # check status
#   journalctl --user -u     set-xauth-cookies   # view logs
#
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Validate dependencies
# ---------------------------------------------------------------------------

if ! command -v xauth &>/dev/null; then
    log error "xauth is not installed. Please install it before running this script."
    exit 1
fi

# ---------------------------------------------------------------------------
# Install xwayland
# ---------------------------------------------------------------------------

package="xwayland"

if dpkg -s "${package}" &>/dev/null; then
    log info "Package '${package}' already installed. Skipping."
elif apt-cache policy "${package}" 2>/dev/null | grep --quiet 'Candidate:'; then
    sudo apt-get install --yes --no-install-recommends "${package}"
else
    log warning "Package '${package}' is missing in apt sources. Skipping."
fi

# ---------------------------------------------------------------------------
# Install /usr/local/bin/set_xauth_cookies.sh
# ---------------------------------------------------------------------------

script="set_xauth_cookies.sh"
qualified_script="/usr/local/bin/${script}"

log info "Installing script '${qualified_script}'"

sudo tee "${qualified_script}" >/dev/null <<'EOF'
#!/usr/bin/env bash

set -euo pipefail

xauth_file="${XDG_RUNTIME_DIR}/cookies.xauth"
xauth_log_file="${XDG_RUNTIME_DIR}/xauth_cookies.log"

set_empty_xauth_file() {
    touch "${xauth_file}"
    chmod a+r "${xauth_file}"
}

# Remove any existing X11 cookie file to ensure a clean state.
[ -e "${xauth_file}" ] && rm -f "${xauth_file}"

echo "Generating cookies at '$(date)' for DISPLAY=${DISPLAY:-unset} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset}" > "${xauth_log_file}"

# Under Wayland, X11 cookies are not used. If DISPLAY is unset, no graphical
# session is available. In both cases create an empty file for compatibility.
if [ -n "${WAYLAND_DISPLAY:-}" ] || [ -z "${DISPLAY:-}" ]; then
    set_empty_xauth_file
    exit 0
fi

# Generate cookies for the current display.
echo "Processing DISPLAY=${DISPLAY}" >> "${xauth_log_file}"
xauth_list="$(xauth nlist "${DISPLAY}" | sed -e 's/^..../ffff/')"

if [ -n "${xauth_list}" ]; then
    echo "${xauth_list}" | xauth -f "${xauth_file}" nmerge - 2>/dev/null || true
fi

if [ -s "${xauth_file}" ]; then
    # Ensure the cookie file is readable by Docker containers.
    chmod a+r "${xauth_file}"
else
    # No X11 cookies found; create an empty file to avoid mount errors.
    set_empty_xauth_file
fi
EOF

sudo chmod a+x "${qualified_script}"

# ---------------------------------------------------------------------------
# Install the systemd user service
# ---------------------------------------------------------------------------

service_name="set-xauth-cookies"
systemd_user_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
service_file="${systemd_user_dir}/${service_name}.service"

log info "Installing systemd user service '${service_file}'"

mkdir -p "${systemd_user_dir}"

cat >"${service_file}" <<EOF
[Unit]
Description=Generate XAuth cookies for Docker GUI access
Documentation=https://wiki.archlinux.org/title/Docker#Run_graphical_programs_inside_a_container
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/test -n "${XDG_RUNTIME_DIR}"
ExecStart=${qualified_script}

[Install]
WantedBy=graphical-session.target
EOF

chmod 644 "${service_file}"

systemctl --user daemon-reload
systemctl --user enable "${service_name}.service"

log info "Service '${service_name}' enabled. It will run automatically on every graphical session start."
log info "Useful commands:"
log info "  systemctl --user restart ${service_name}   # force cookie regeneration"
log info "  systemctl --user status  ${service_name}   # check status"
log info "  journalctl --user -u     ${service_name}   # view logs"

# ---------------------------------------------------------------------------
# Run immediately — no re-login required
# ---------------------------------------------------------------------------

log info "Executing '${qualified_script}'"
"${qualified_script}"

log info "Done."
