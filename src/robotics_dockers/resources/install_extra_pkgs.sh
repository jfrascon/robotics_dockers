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

    log error "${error_message} (exit code: ${exit_code})"
    exit "${exit_code}"
}

usage() {
    cat <<EOF
Usage:
  ${script_name} TARGET_USER TARGET_USER_HOME [--help]

Positional arguments:
  TARGET_USER       Target system user name
  TARGET_USER_HOME  Home directory for the target user

Options:
  --help          Show this help and exit

The following files in /tmp/context/.resources/extra.d/ are processed if present:
  apt_packages.sh   Executed as root. Add repos, keys and apt packages here.
  requirements.txt  pip install -r as TARGET_USER. Standard pip format supported.
  rust_packages.txt Rust packages. One crate per line (binary) or 'source: crate --flags' (compiled).
                    Rust toolchain is installed only if the file contains active (non-comment) lines.
EOF
}

script="${BASH_SOURCE:-${0}}"
script_name="$(basename "${script}")"

if [ "$(id --user)" -ne 0 ]; then
    handle_error 1 "root user must be active to run the script '${script_name}'"
fi

for arg in "$@"; do
    case "$arg" in
    --help | -h)
        usage
        exit 0
        ;;
    esac
done

TARGET_USER="${1:-}"
TARGET_USER_HOME="${2:-}"

[ -z "${TARGET_USER}" ] && handle_error 1 "User not provided"
[ -z "${TARGET_USER_HOME}" ] && handle_error 1 "User home directory not provided"

EXTRA_D="/tmp/context/.resources/extra.d"

#-----------------------------------------------------------------------------------------------------------------------
# apt_packages.sh
#-----------------------------------------------------------------------------------------------------------------------
apt_script="${EXTRA_D}/apt_packages.sh"

if [ -f "${apt_script}" ]; then
    log info "Running '${apt_script}'"
    bash "${apt_script}" || handle_error 1 "apt_packages.sh failed"

    log info "Cleaning up apt cache"
    apt-get autoremove --purge -y
    apt-get clean
    rm -rf /var/lib/apt/lists/* 1>/dev/null 2>&1
else
    log info "No '${apt_script}' found, skipping"
fi

#-----------------------------------------------------------------------------------------------------------------------
# requirements.txt
#-----------------------------------------------------------------------------------------------------------------------
requirements_file="${EXTRA_D}/requirements.txt"

if [ -f "${requirements_file}" ]; then
    log info "Running pip install -r '${requirements_file}' for user '${TARGET_USER}'"

    pip_args=(--no-cache-dir --disable-pip-version-check --user)

    if python3 -m pip install --help | grep --quiet 'break-system-packages'; then
        pip_args+=("--break-system-packages")
    fi

    sudo -H -u "${TARGET_USER}" env PATH="${TARGET_USER_HOME}/.local/bin:${PATH}" \
        python3 -m pip install "${pip_args[@]}" -r "${requirements_file}" ||
        handle_error 1 "Failed to install Python packages from '${requirements_file}'"
else
    log info "No '${requirements_file}' found, skipping"
fi

#-----------------------------------------------------------------------------------------------------------------------
# rust_packages.txt
#-----------------------------------------------------------------------------------------------------------------------
rust_packages_file="${EXTRA_D}/rust_packages.txt"

if [ -f "${rust_packages_file}" ]; then
    mapfile -t rust_packages < <(grep -v '^\s*#' "${rust_packages_file}" | grep -v '^\s*$')

    if [ ${#rust_packages[@]} -eq 0 ]; then
        log info "No active Rust packages in '${rust_packages_file}', skipping Rust toolchain installation"
    else
        log info "Found ${#rust_packages[@]} Rust package(s) — installing Rust toolchain for user '${TARGET_USER}'"

        # Install rustup and stable toolchain as TARGET_USER.
        sudo -H -u "${TARGET_USER}" bash -c \
            'curl --proto '"'"'=https'"'"' --tlsv1.2 -sSf https://sh.rustup.rs | bash -s -- -y --no-modify-path --default-toolchain stable' ||
            handle_error 1 "Failed to install Rust toolchain for user '${TARGET_USER}'"

        # Bootstrap cargo-binstall using its official binary installer (no compilation needed).
        sudo -H -u "${TARGET_USER}" env HOME="${TARGET_USER_HOME}" bash -c \
            'curl -L --proto '"'"'=https'"'"' --tlsv1.2 -sSf https://raw.githubusercontent.com/cargo-bins/cargo-binstall/main/install-from-binstall-release.sh | bash' ||
            handle_error 1 "Failed to install cargo-binstall for user '${TARGET_USER}'"

        # Update stable toolchain.
        sudo -H -u "${TARGET_USER}" env HOME="${TARGET_USER_HOME}" \
            "${TARGET_USER_HOME}/.cargo/bin/rustup" update stable ||
            handle_error 1 "Failed to update Rust stable toolchain"

        # Install each package.
        for entry in "${rust_packages[@]}"; do
            if [[ ${entry} == source:* ]]; then
                crate_args="${entry#source:}"
                crate_args="${crate_args#"${crate_args%%[![:space:]]*}"}"
                log info "Installing Rust package from source: cargo install ${crate_args}"
                # shellcheck disable=SC2086
                sudo -H -u "${TARGET_USER}" env HOME="${TARGET_USER_HOME}" PATH="${TARGET_USER_HOME}/.cargo/bin:${PATH}" \
                    cargo install ${crate_args} ||
                    handle_error 1 "Failed to install Rust package (source): ${crate_args}"
            else
                log info "Installing Rust package (binary): cargo binstall ${entry}"
                # shellcheck disable=SC2086
                sudo -H -u "${TARGET_USER}" env HOME="${TARGET_USER_HOME}" PATH="${TARGET_USER_HOME}/.cargo/bin:${PATH}" \
                    cargo binstall --no-confirm ${entry} ||
                    handle_error 1 "Failed to install Rust package (binary): ${entry}"
            fi
        done
    fi
else
    log info "No '${rust_packages_file}' found, skipping"
fi
