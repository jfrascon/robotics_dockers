#!/usr/bin/env bash
# Create or verify the exact local development identity stored in the image.
#
# The script intentionally refuses to repair conflicting users or groups. A
# project-specific preparation hook can resolve a known base-image account before
# this script runs. Refusing unknown collisions prevents an automatic rename or
# deletion from changing unrelated files and accounts.
#
# Exactly three initial local-account states are supported:
#   * The requested user and primary group are both absent and both IDs are free.
#   * The user is absent, while the primary group already has the requested name
#     and GID; the group is reused and the user is created.
#   * The user, primary group, home and shell already match every requested value;
#     the existing identity is reused.
# Conflicting names, numeric IDs, homes and shells in /etc/passwd or /etc/group
# are rejected before the first account change. shadow-utils validates and
# updates the corresponding protected records. Existing home content is never
# traversed recursively.

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

if [ "$#" -ne 5 ]; then
    handle_error 2 "Usage: configure_image_user.sh USER UID PRIMARY_GROUP PRIMARY_GID HOME"
fi

development_user="${1}"
development_user_id="${2}"
development_primary_group="${3}"
development_primary_group_id="${4}"
development_user_home="${5}"
development_user_shell="/bin/bash"
maximum_user_group_id=4294967294

# validate_account_name VALUE LABEL
#
# VALUE is the account name being checked. LABEL is used only to make an error
# identify whether that value represents a user or a primary group.
validate_account_name() {
    local value="${1}"
    local label="${2}"
    if [[ ! ${value} =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        handle_error 2 "${label} '${value}' must start with a lowercase letter or underscore, contain only lowercase letters, digits, hyphens or underscores, and contain at most 32 characters"
    fi
}

# validate_numeric_id VALUE LABEL
validate_numeric_id() {
    local value="${1}"
    local label="${2}"
    if [[ ! ${value} =~ ^[1-9][0-9]*$ ]]; then
        handle_error 2 "${label} '${value}' must use canonical decimal notation"
    fi
    # Reject values that are too long before the arithmetic comparison so Bash
    # never has to interpret an integer outside the supported range.
    if [[ ${value} =~ ^[0-9]{11,}$ ]]; then
        handle_error 2 "${label} '${value}' exceeds maximum '${maximum_user_group_id}'"
    fi

    if [ "${value}" -gt "${maximum_user_group_id}" ]; then
        handle_error 2 "${label} '${value}' exceeds maximum '${maximum_user_group_id}'"
    fi
    if [ "${value}" -lt 1000 ]; then
        handle_error 2 "${label} '${value}' must be at least 1000"
    fi
}

# validate_optional_directory PATH LABEL
#
# An absent path is valid because this script can create it later. An existing
# path must be a real directory: files cannot be converted into directories, and
# following a symbolic link could change ownership outside the configured home.
validate_optional_directory() {
    local path="${1}"
    local label="${2}"

    if [ -L "${path}" ]; then
        handle_error 1 "${label} '${path}' must not be a symbolic link"
    fi

    if [ -e "${path}" ]; then
        if [ ! -d "${path}" ]; then
            handle_error 1 "${label} '${path}' exists but is not a directory"
        fi
    fi
}

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing this script"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "User configuration must run as UID 0; found UID '${executing_user_id}'"
fi

# -----------------------------------------------------------------------------
# Validate the requested identity before reading or changing local accounts.
# -----------------------------------------------------------------------------
# The generator performs the same validation before rendering. Repeating the
# small checks here makes this helper safe when a derived Dockerfile calls it
# directly instead of going through the Python CLI.
validate_account_name "${development_user}" "User name"
validate_account_name "${development_primary_group}" "Primary group name"
validate_numeric_id "${development_user_id}" "UID"
validate_numeric_id "${development_primary_group_id}" "Primary GID"
if [ "${development_user_home}" != "/home/${development_user}" ]; then
    handle_error 2 "Home '${development_user_home}' must be exactly '/home/${development_user}'"
fi

user_directory_relative_paths=(
    ".cache"
    ".config"
    ".env.d"
    ".local"
    ".local/bin"
    ".local/lib"
    ".local/share"
    ".local/state"
    ".local/state/robotics-dockers"
    ".ros"
    ".ros/logs"
    ".ros/tests"
)

user_directories=()
for relative_path in "${user_directory_relative_paths[@]}"; do
    user_directories+=("${development_user_home}/${relative_path}")
done

# -----------------------------------------------------------------------------
# Read only the local user and group records relevant to this identity.
# -----------------------------------------------------------------------------
# Direct reads of /etc/passwd and /etc/group are intentional. The identity is
# stored in the image's local account files, so an account provided by LDAP or
# another external name service must not be mistaken for a reusable local one.

user_name_count="$(awk -F: -v name="${development_user}" '$1 == name { count++ } END { print count + 0 }' /etc/passwd)" ||
    handle_error 1 "Could not inspect user name '${development_user}' in /etc/passwd"

user_id_count="$(awk -F: -v id="${development_user_id}" '$3 == id { count++ } END { print count + 0 }' /etc/passwd)" ||
    handle_error 1 "Could not inspect UID '${development_user_id}' in /etc/passwd"

group_name_count="$(awk -F: -v name="${development_primary_group}" '$1 == name { count++ } END { print count + 0 }' /etc/group)" ||
    handle_error 1 "Could not inspect group name '${development_primary_group}' in /etc/group"

group_id_count="$(awk -F: -v id="${development_primary_group_id}" '$3 == id { count++ } END { print count + 0 }' /etc/group)" ||
    handle_error 1 "Could not inspect GID '${development_primary_group_id}' in /etc/group"

if [ "${user_name_count}" -gt 1 ]; then
    handle_error 1 "User name '${development_user}' appears '${user_name_count}' times in /etc/passwd; zero or one entry is required"
fi
if [ "${user_id_count}" -gt 1 ]; then
    handle_error 1 "UID '${development_user_id}' appears '${user_id_count}' times in /etc/passwd; zero or one entry is required"
fi
if [ "${group_name_count}" -gt 1 ]; then
    handle_error 1 "Group name '${development_primary_group}' appears '${group_name_count}' times in /etc/group; zero or one entry is required"
fi
if [ "${group_id_count}" -gt 1 ]; then
    handle_error 1 "GID '${development_primary_group_id}' appears '${group_id_count}' times in /etc/group; zero or one entry is required"
fi

# dialout and video are optional base-image groups. Only their presence is
# needed here. usermod is responsible for validating and updating the account
# files when it adds the development user to these groups.
supplementary_groups=()
for supplementary_group in dialout video; do
    supplementary_group_count="$(awk -F: -v name="${supplementary_group}" '$1 == name { count++ } END { print count + 0 }' /etc/group)" ||
        handle_error 1 "Could not inspect supplementary group '${supplementary_group}' in /etc/group"

    # A primary group already grants access through the process primary GID and
    # does not need a duplicate member-list entry as a supplementary group.
    if [ "${supplementary_group_count}" -gt 0 ]; then
        if [ "${supplementary_group}" != "${development_primary_group}" ]; then
            supplementary_groups+=("${supplementary_group}")
        fi
    fi
done

if [ "${group_name_count}" -eq 1 ]; then
    existing_group_id="$(awk -F: -v name="${development_primary_group}" '$1 == name { print $3 }' /etc/group)" ||
        handle_error 1 "Could not read group '${development_primary_group}'"

    if [ "${existing_group_id}" != "${development_primary_group_id}" ]; then
        handle_error 1 "Group '${development_primary_group}' has GID '${existing_group_id}'; expected '${development_primary_group_id}'. Resolve the base-image collision in user_preparation.d"
    fi
elif [ "${group_id_count}" -eq 1 ]; then
    existing_group_name="$(awk -F: -v id="${development_primary_group_id}" '$3 == id { print $1 }' /etc/group)" ||
        handle_error 1 "Could not read the group with GID '${development_primary_group_id}'"

    handle_error 1 "GID '${development_primary_group_id}' belongs to group '${existing_group_name}'; expected the free name '${development_primary_group}'. Resolve the base-image collision in user_preparation.d"
fi

if [ "${user_name_count}" -eq 1 ]; then
    if [ "${group_name_count}" -ne 1 ]; then
        handle_error 1 "User '${development_user}' exists, but its expected primary group '${development_primary_group}' does not. Resolve the incomplete base-image identity in user_preparation.d"
    fi

    existing_user_entry="$(awk -F: -v name="${development_user}" '$1 == name { print }' /etc/passwd)" ||
        handle_error 1 "Could not read user '${development_user}'"

    IFS=: read -r _ _ existing_id existing_group_id _ existing_home existing_shell <<<"${existing_user_entry}"

    if [ "${existing_id}" != "${development_user_id}" ]; then
        handle_error 1 "User '${development_user}' has UID '${existing_id}'; expected '${development_user_id}'. Resolve the base-image collision in user_preparation.d"
    fi

    if [ "${existing_group_id}" != "${development_primary_group_id}" ]; then
        handle_error 1 "User '${development_user}' has primary GID '${existing_group_id}'; expected '${development_primary_group_id}'. Resolve the base-image collision in user_preparation.d"
    fi

    if [ "${existing_home}" != "${development_user_home}" ]; then
        handle_error 1 "User '${development_user}' has home '${existing_home}'; expected '${development_user_home}'. Resolve the base-image collision in user_preparation.d"
    fi

    if [ "${existing_shell}" != "${development_user_shell}" ]; then
        handle_error 1 "User '${development_user}' has shell '${existing_shell}'; expected '${development_user_shell}'. Resolve the base-image collision in user_preparation.d"
    fi

    if [ -L "${development_user_home}" ]; then
        handle_error 1 "Existing user '${development_user}' has a symbolic link at home path '${development_user_home}'"
    fi

    if [ ! -d "${development_user_home}" ]; then
        handle_error 1 "Existing user '${development_user}' does not have a directory at home path '${development_user_home}'"
    fi

    home_owner="$(stat --format '%u:%g' -- "${development_user_home}")" ||
        handle_error 1 "Could not read ownership of '${development_user_home}'"

    if [ "${home_owner}" != "${development_user_id}:${development_primary_group_id}" ]; then
        handle_error 1 "Home '${development_user_home}' belongs to '${home_owner}'; expected '${development_user_id}:${development_primary_group_id}'"
    fi
elif [ "${user_id_count}" -eq 1 ]; then
    existing_user_name="$(awk -F: -v id="${development_user_id}" '$3 == id { print $1 }' /etc/passwd)" ||
        handle_error 1 "Could not read the user with UID '${development_user_id}'"

    handle_error 1 "UID '${development_user_id}' belongs to user '${existing_user_name}'; expected the free name '${development_user}'. Resolve the base-image collision in user_preparation.d"
elif [ -L "${development_user_home}" ]; then
    handle_error 1 "Home path '${development_user_home}' is a symbolic link but user '${development_user}' does not exist; refusing to adopt its target"
elif [ -e "${development_user_home}" ]; then
    handle_error 1 "Home path '${development_user_home}' already exists but user '${development_user}' does not; refusing to adopt unknown content"
fi

# These paths may already contain base-image configuration. Reject files and
# symbolic links before account creation; both would make later directory setup
# fail or operate through an unexpected target. Existing real directories are
# allowed and keep their current permission mode.
for user_directory in "${user_directories[@]}"; do
    validate_optional_directory "${user_directory}" "User path"
done

# useradd --create-home copies /etc/skel into a new home. Check the known paths
# there before groupadd or useradd so a conflicting skeleton file or link cannot
# appear only after account creation has started.
if [ "${user_name_count}" -eq 0 ]; then
    if [ -d /etc/skel ]; then
        for relative_path in "${user_directory_relative_paths[@]}"; do
            validate_optional_directory "/etc/skel/${relative_path}" "Skeleton path"
        done
    fi
fi

# -----------------------------------------------------------------------------
# Requested identity and filesystem checks are complete. Mutation begins.
# -----------------------------------------------------------------------------
# groupadd, useradd and usermod are separate operations, not one transaction. A
# failure after one command succeeds can leave a partial result when this helper
# is called directly. During a Docker build, Docker rejects that failed layer;
# the helper does not attempt a risky automatic rollback of account files.
if [ "${group_name_count}" -eq 0 ]; then
    log info "Creating primary group '${development_primary_group}' with GID '${development_primary_group_id}'"
    groupadd --gid "${development_primary_group_id}" "${development_primary_group}" ||
        handle_error 1 "Could not create primary group '${development_primary_group}'"
else
    log info "Reusing primary group '${development_primary_group}' with GID '${development_primary_group_id}'"
fi

if [ "${user_name_count}" -eq 0 ]; then
    log info "Creating user '${development_user}' with UID '${development_user_id}' and primary GID '${development_primary_group_id}'"
    useradd --uid "${development_user_id}" --gid "${development_primary_group_id}" --create-home \
        --home-dir "${development_user_home}" --shell "${development_user_shell}" "${development_user}" ||
        handle_error 1 "Could not create user '${development_user}'"
else
    log info "Reusing exact existing user '${development_user}'"
fi

# Apply the password lock and all optional memberships in one account-database
# operation. --append preserves every supplementary group inherited from an
# exact existing user.
usermod_arguments=(--lock)
# ${#supplementary_groups[@]} returns the number of supplementary groups that
# must be added to the development user.
if [ "${#supplementary_groups[@]}" -gt 0 ]; then
    # Bash joins an array expansion with the first character of IFS. Limit the
    # comma separator to this command substitution so the script's IFS does not
    # change and usermod receives the comma-separated syntax required by --groups.
    supplementary_group_list="$(
        IFS=,
        printf '%s' "${supplementary_groups[*]}"
    )"
    usermod_arguments+=(--append --groups "${supplementary_group_list}")
fi

usermod "${usermod_arguments[@]}" "${development_user}" ||
    handle_error 1 "Could not lock '${development_user}' or configure its supplementary groups"

# New directories receive mode 0755. Existing directories keep their mode, but
# their own directory entry receives the configured owner. No contained file or
# subdirectory is traversed by this non-recursive chown.
for user_directory in "${user_directories[@]}"; do
    if [ -d "${user_directory}" ]; then
        chown "${development_user_id}:${development_primary_group_id}" "${user_directory}" ||
            handle_error 1 "Could not set ownership of existing user directory '${user_directory}'"
    else
        install --directory --owner "${development_user_id}" --group "${development_primary_group_id}" --mode 0755 "${user_directory}" ||
            handle_error 1 "Could not create user directory '${user_directory}'"
    fi
done

# -----------------------------------------------------------------------------
# Verify the three final properties used by the generated image.
# -----------------------------------------------------------------------------
# shadow-utils has already reported success. These checks verify the public
# identity consumed at runtime without reimplementing validation of the complete
# passwd, shadow, group and gshadow databases.
final_user_entry="$(awk -F: -v name="${development_user}" '$1 == name { print }' /etc/passwd)" ||
    handle_error 1 "Could not read final user '${development_user}'"

if [ -z "${final_user_entry}" ]; then
    handle_error 1 "Final user '${development_user}' is missing from /etc/passwd"
fi

IFS=: read -r _ _ final_user_id final_group_id _ final_home final_shell <<<"${final_user_entry}"

if [ "${final_user_id}" != "${development_user_id}" ]; then
    handle_error 1 "Final user UID '${final_user_id}' differs from requested UID '${development_user_id}'"
fi

if [ "${final_group_id}" != "${development_primary_group_id}" ]; then
    handle_error 1 "Final user primary GID '${final_group_id}' differs from requested GID '${development_primary_group_id}'"
fi

if [ "${final_home}" != "${development_user_home}" ]; then
    handle_error 1 "Final user home '${final_home}' differs from requested home '${development_user_home}'"
fi

if [ "${final_shell}" != "${development_user_shell}" ]; then
    handle_error 1 "Final user shell '${final_shell}' differs from requested shell '${development_user_shell}'"
fi

final_group_entry="$(awk -F: -v name="${development_primary_group}" '$1 == name { print }' /etc/group)" ||
    handle_error 1 "Could not read final group '${development_primary_group}'"

if [ -z "${final_group_entry}" ]; then
    handle_error 1 "Final primary group '${development_primary_group}' is missing from /etc/group"
fi

IFS=: read -r _ _ final_primary_group_id _ <<<"${final_group_entry}"

if [ "${final_primary_group_id}" != "${development_primary_group_id}" ]; then
    handle_error 1 "Final group '${development_primary_group}' has GID '${final_primary_group_id}'; expected '${development_primary_group_id}'"
fi

if [ -L "${development_user_home}" ]; then
    handle_error 1 "Final home '${development_user_home}' must not be a symbolic link"
fi

if [ ! -d "${development_user_home}" ]; then
    handle_error 1 "Final home '${development_user_home}' does not exist or is not a directory"
fi

final_home_owner="$(stat --format '%u:%g' -- "${development_user_home}")" ||
    handle_error 1 "Could not read final ownership of home '${development_user_home}'"

if [ "${final_home_owner}" != "${development_user_id}:${development_primary_group_id}" ]; then
    handle_error 1 "Final home '${development_user_home}' belongs to '${final_home_owner}'; expected '${development_user_id}:${development_primary_group_id}'"
fi

log info "Development identity ready: ${development_user_id}:${development_primary_group_id} (${development_user}:${development_primary_group})"
