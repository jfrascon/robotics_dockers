#!/usr/bin/env bash
# Install declarative apt customizations supplied in extra.d/apt.
#
# Repository configuration uses apt's standard keyring and deb822 formats.
# This script validates the complete input set before copying project files.
# Apt remains responsible for parsing sources and verifying repository
# signatures; package installation itself is not an all-or-nothing transaction.

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
    handle_error 2 "Usage: install_extra_apt.sh EXTRA_APT_DIRECTORY"
fi

extra_apt_directory="${1}"

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is installing extra apt resources"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "Extra apt installation must run as UID 0; found UID '${executing_user_id}'"
fi

if [ ! -d "${extra_apt_directory}" ]; then
    handle_error 1 "Extra apt directory '${extra_apt_directory}' does not exist"
fi

keyrings_directory="${extra_apt_directory}/keyrings.d"
sources_directory="${extra_apt_directory}/sources.d"
packages_file="${extra_apt_directory}/packages.txt"

# Validate the input shape before copying files so unsupported project entries
# produce a direct error instead of being silently ignored.
for required_directory in "${keyrings_directory}" "${sources_directory}"; do
    if [ -L "${required_directory}" ]; then
        handle_error 1 "Required directory '${required_directory}' must not be a symbolic link"
    fi

    if [ ! -d "${required_directory}" ]; then
        handle_error 1 "Required directory '${required_directory}' does not exist or is not a directory"
    fi
done

if [ -L "${packages_file}" ]; then
    handle_error 1 "Package list '${packages_file}' must not be a symbolic link"
fi

if [ ! -f "${packages_file}" ]; then
    handle_error 1 "Package list '${packages_file}' does not exist or is not a regular file"
fi

# Only regular files directly below the documented directories are meaningful.
# Reject directories, links and unknown extensions instead of silently ignoring
# them and producing an image that lacks an apparently configured repository.
unexpected_keyring="$(find "${keyrings_directory}" -mindepth 1 -maxdepth 1 \
    ! \( -type f \( -name '*.asc' -o -name '*.gpg' \) \) -print -quit)" ||
    handle_error 1 "Could not inspect apt keyrings in '${keyrings_directory}'"

if [ -n "${unexpected_keyring}" ]; then
    handle_error 1 "Unsupported keyring entry '${unexpected_keyring}'; only top-level regular .asc and .gpg files are accepted"
fi

unexpected_source="$(find "${sources_directory}" -mindepth 1 -maxdepth 1 \
    ! \( -type f -name '*.sources' \) -print -quit)" ||
    handle_error 1 "Could not inspect apt sources in '${sources_directory}'"

if [ -n "${unexpected_source}" ]; then
    handle_error 1 "Unsupported apt source entry '${unexpected_source}'; only top-level regular deb822 .sources files are accepted"
fi

mapfile -d '' -t keyring_files < <(
    find "${keyrings_directory}" -maxdepth 1 -type f \( -name '*.asc' -o -name '*.gpg' \) -print0 | LC_ALL=C sort -z
)

mapfile -d '' -t source_files < <(
    find "${sources_directory}" -maxdepth 1 -type f -name '*.sources' -print0 | LC_ALL=C sort -z
)

install --directory --owner root --group root --mode 0755 /etc/apt/keyrings /etc/apt/sources.list.d ||
    handle_error 1 "Could not prepare apt keyring and source directories"

# Reject inherited destinations instead of overwriting base-image repository
# configuration. The check and copy stay together so each file has one readable
# path through the script. If a later file collides, Docker discards the failed
# RUN layer, including files copied earlier by this loop.
for source_file in "${keyring_files[@]}"; do
    # ${source_file##*/} removes everything through the final slash and leaves
    # only the source filename.
    destination="/etc/apt/keyrings/${source_file##*/}"
    # -e is false for a dangling symbolic link. Test -L separately so install
    # cannot follow an inherited link whose target happens not to exist yet.
    if [ -L "${destination}" ]; then
        handle_error 1 "Refusing inherited symbolic link '${destination}' at the apt keyring destination"
    fi

    if [ -e "${destination}" ]; then
        handle_error 1 "Refusing to overwrite inherited apt keyring '${destination}'"
    fi

    install --owner root --group root --mode 0644 "${source_file}" "${destination}" ||
        handle_error 1 "Could not install apt keyring '${source_file}'"
done

for source_file in "${source_files[@]}"; do
    # ${source_file##*/} removes everything through the final slash and leaves
    # only the source filename.
    destination="/etc/apt/sources.list.d/${source_file##*/}"
    if [ -L "${destination}" ]; then
        handle_error 1 "Refusing inherited symbolic link '${destination}' at the apt source destination"
    fi

    if [ -e "${destination}" ]; then
        handle_error 1 "Refusing to overwrite inherited apt source '${destination}'"
    fi

    install --owner root --group root --mode 0644 "${source_file}" "${destination}" ||
        handle_error 1 "Could not install apt source '${source_file}'"
done

packages=()
# The second condition processes a final non-empty line even when the file does
# not end with a newline. This is one read-loop rule, not two error conditions.
while IFS= read -r package_line || [ -n "${package_line}" ]; do
    # The inner %% expansion isolates all leading whitespace; the outer #
    # expansion removes that prefix from the line.
    package_line="${package_line#"${package_line%%[![:space:]]*}"}"
    # The inner ## expansion isolates all trailing whitespace; the outer %
    # expansion removes that suffix from the line.
    package_line="${package_line%"${package_line##*[![:space:]]}"}"

    if [ -z "${package_line}" ]; then
        continue
    fi

    # Comment lines can document the editable package list.
    if [[ ${package_line} == \#* ]]; then
        continue
    fi

    # Reject any line that contains whitespace, which is not a valid apt package
    # name.
    if [[ ${package_line} == *[[:space:]]* ]]; then
        handle_error 1 "Each active line in '${packages_file}' must contain one apt package specification; found '${package_line}'"
    fi

    # Reject any line that starts with a dash, which is not a valid apt package
    # name and is interpreted as an apt command option.
    if [[ ${package_line} == -* ]]; then
        handle_error 1 "Package specification '${package_line}' must not start with '-'; apt command options are not accepted in packages.txt"
    fi

    packages+=("${package_line}")
done <"${packages_file}"

# Updating when a source was added validates its standard deb822 syntax and
# signature configuration even when packages.txt is empty.
apt_update_required=false
# ${#source_files[@]} returns the number of elements in the source-file array.
if [ "${#source_files[@]}" -gt 0 ]; then
    apt_update_required=true
fi

# ${#packages[@]} returns the number of requested package specifications.
if [ "${#packages[@]}" -gt 0 ]; then
    apt_update_required=true
fi

if [ "${apt_update_required}" = true ]; then
    apt-get update --quiet --quiet ||
        handle_error 1 "apt rejected an extra source or could not update package indexes"
fi

# ${#packages[@]} returns the number of requested package specifications.
if [ "${#packages[@]}" -gt 0 ]; then
    install_pkgs "${packages[@]}" ||
        handle_error 1 "Could not install the complete extra apt package set"
else
    log info "No extra apt packages requested"
fi

apt-get clean >/dev/null ||
    handle_error 1 "Could not clean the apt cache after extra package installation"

find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + ||
    handle_error 1 "Could not remove apt package indexes after extra package installation"
