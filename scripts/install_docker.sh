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

log info "Installing Docker"

sudo apt-get update || handle_error 1 "Failed to update apt repositories"
sudo apt-get upgrade --yes || handle_error 1 "Failed to upgrade installed apt packages"
sudo apt-get install --yes --quiet --no-install-recommends curl gpg ||
    handle_error 1 "Failed to install Docker installation dependencies"

gpg_dir="/etc/apt/keyrings"
gpg_file="${gpg_dir}/docker.gpg"

sudo mkdir -p "${gpg_dir}" || handle_error 1 "Failed to create '${gpg_dir}'"

# Download and install the Docker GPG key
log info "Installing Docker GPG key to '${gpg_file}'"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
    gpg --dearmor --output - |
    sudo tee "${gpg_file}" >/dev/null || handle_error 1 "Failed to install Docker GPG key"

# Set correct permissions
sudo chmod 644 "${gpg_file}" || handle_error 1 "Failed to set permissions on '${gpg_file}'"

# Get relevant environment variables, including VERSION_CODENAME.
# shellcheck disable=SC1091
. /etc/os-release || handle_error 1 "Failed to read '/etc/os-release'"

url="https://download.docker.com/linux/ubuntu"
deb_pattern="^deb.*${url}[[:space:]]+${VERSION_CODENAME}[[:space:]]+stable"
arch="$(dpkg --print-architecture)" || handle_error 1 "Failed to detect Debian architecture"
deb_line="deb [arch=${arch} signed-by=${gpg_file}] ${url} ${VERSION_CODENAME} stable"
list_file="/etc/apt/sources.list.d/docker.list"

# If the Docker deb line exists in /etc/apt/sources.list (the main file), remove it from there.
# Lines in sources.list.d/ files are handled by overwriting docker.list below.
if grep -qE "${deb_pattern}" /etc/apt/sources.list 2>/dev/null; then
    log info "Docker deb line found in '/etc/apt/sources.list', removing it"
    sudo sed -i -E "\#${deb_pattern}#d" /etc/apt/sources.list ||
        handle_error 1 "Failed to remove Docker deb line from '/etc/apt/sources.list'"
fi

# Write (or overwrite) the canonical docker.list entry.
log info "Writing Docker deb line to '${list_file}'"
echo "${deb_line}" | sudo tee "${list_file}" >/dev/null ||
    handle_error 1 "Failed to write Docker apt source to '${list_file}'"

# Install Docker packages
log info "Installing Docker packages"
sudo apt-get update || handle_error 1 "Failed to update apt repositories after adding Docker source"
sudo apt-get install --yes --quiet --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin ||
    handle_error 1 "Failed to install Docker packages"

# Manage Docker group and permissions
if ! getent group docker >/dev/null; then
    sudo groupadd docker || handle_error 1 "Failed to create docker group"
fi

# Add the current user to the docker group to allow running Docker without sudo.
# This change will take effect after logging out and logging back in.
sudo usermod -aG docker "${USER}" || handle_error 1 "Failed to add user '${USER}' to docker group"

# On Debian and Ubuntu, the Docker service is configured to start on boot by default.
# To automatically start Docker and Containerd on boot for other distros, use the commands below:
# sudo systemctl enable docker.service
# sudo systemctl enable containerd.service
# To disable this behavior, use disable instead.
# sudo systemctl disable docker.service
# sudo systemctl disable containerd.service

log info "Running test container"
if sudo docker run --rm hello-world; then
    log info "Docker was installed and is working correctly"
else
    handle_error 1 "Docker run failed. Try logging out and logging in again, or restarting your computer"
fi

# Configuration advice
cat <<'EOF'
*******************************************************************************************************
* By default configuration files for Docker are stored in the path "${HOME}/.docker".                 *
* It is possible to change the location of Docker configuration files from "${HOME}/.docker"          *
* to "${HOME}/.config/docker". You can achieve this by using the DOCKER_CONFIG environment variable.  *
* Here's how you can do it:                                                                           *
*                                                                                                     *
* 1. Create the path "${HOME}/.config/docker" if it doesn't exist:                                    *
*    mkdir -p "${HOME}/.config/docker"                                                                *
*                                                                                                     *
* 2. Set the DOCKER_CONFIG environment variable to point to the new location. You can do this by      *
*    adding the line                                                                                  *
*    export DOCKER_CONFIG="${HOME}/.config/docker"                                                    *
*    either to your shell rc file or profile file (e.g for bash ${HOME}/.bashrc or                    *
*    "${HOME}/.profile")                                                                              *
*                                                                                                     *
* 3. LOG OUT AND LOG BACK IN OR RESTART YOUR COMPUTER IF YOU PREFER TO APPLY THE CHANGES.             *
*******************************************************************************************************
EOF
