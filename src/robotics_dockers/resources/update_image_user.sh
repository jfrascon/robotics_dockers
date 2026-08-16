#!/usr/bin/env bash
# Change only the numeric identity of an existing robotics-dockers account.
#
# shadow-utils changes ownership inside the user's home when usermod changes UID
# or primary GID. That traversal can create a large image layer. The script
# rejects identity collisions before modifying account databases, keeps the old
# numeric group available under another name, and invokes usermod at most once.
# shadow-utils validates the account files it changes. It is not transactional:
# a reported mutation failure requires inspection of the intermediate image and
# is not rolled back automatically.

MAX_USER_GROUP_ID=4294967294

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

normalize_decimal_id() {
    local value="${1}"
    # The inner %% expansion isolates all leading zeroes; the outer # expansion
    # removes that prefix. An input containing only zeroes becomes empty here.
    value="${value#"${value%%[!0]*}"}"
    if [ -z "${value}" ]; then
        value=0
    fi

    printf '%s\n' "${value}"
}

validate_decimal_id() {
    local variable_name="${1}"
    local raw_value="${2}"
    local normalized
    if [[ ! ${raw_value} =~ ^[0-9]+$ ]]; then
        handle_error 2 "${variable_name} must contain decimal digits; found '${raw_value}'"
    fi

    normalized="$(normalize_decimal_id "${raw_value}")"
    if [ "${raw_value}" != "${normalized}" ]; then
        handle_error 2 "${variable_name} must use canonical decimal form; use '${normalized}' instead of '${raw_value}'"
    fi

    if [[ ${normalized} =~ ^[0-9]{11,}$ ]]; then
        handle_error 2 "${variable_name} must not exceed '${MAX_USER_GROUP_ID}'; found '${normalized}'"
    fi

    if [ "${normalized}" -gt "${MAX_USER_GROUP_ID}" ]; then
        handle_error 2 "${variable_name} must not exceed '${MAX_USER_GROUP_ID}'; found '${normalized}'"
    fi
    if [ "${normalized}" -lt 1000 ]; then
        handle_error 2 "${variable_name} must be at least 1000; found '${normalized}'"
    fi

    printf '%s\n' "${normalized}"
}

generate_preserved_group_name() {
    local old_group_id="${1}"
    local candidate="rd_old_${old_group_id}"
    local suffix=0
    while awk -F: -v name="${candidate}" '$1 == name { found=1 } END { exit !found }' /etc/group; do
        suffix=$((suffix + 1))
        candidate="rd_old_${old_group_id}_${suffix}"
    done
    printf '%s\n' "${candidate}"
}

if [ "$#" -ne 4 ]; then
    handle_error 2 "Usage: update_image_user.sh NEW_UID NEW_GID EXPECTED_USER EXPECTED_HOME"
fi

executing_user_id="$(id --user 2>/dev/null)" ||
    handle_error 1 "Could not determine which UID is executing the image-user adapter"

if [ "${executing_user_id}" -ne 0 ]; then
    handle_error 1 "The image-user adapter must run as UID 0; found UID '${executing_user_id}'"
fi

new_user_id="$(validate_decimal_id NEW_UID "${1}")" || exit $?
new_primary_group_id="$(validate_decimal_id NEW_GID "${2}")" || exit $?

expected_user="${3}"
expected_home="${4}"

for required_variable in \
    ROBOTICS_DOCKERS_USER \
    ROBOTICS_DOCKERS_USER_ID \
    ROBOTICS_DOCKERS_USER_HOME \
    ROBOTICS_DOCKERS_USER_PRIMARY_GROUP \
    ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID; do
    # ${!required_variable:-} reads the variable whose name is stored in
    # required_variable and produces an empty value when that variable is unset.
    if [ -z "${!required_variable:-}" ]; then
        handle_error 1 "Base image does not provide required metadata '${required_variable}'; legacy images are not supported"
    fi
done

if [ "${ROBOTICS_DOCKERS_USER}" != "${expected_user}" ]; then
    handle_error 1 "Base image user '${ROBOTICS_DOCKERS_USER}' differs from adapter user '${expected_user}'; use the adapter generated for that image project"
fi

if [ "${ROBOTICS_DOCKERS_USER_HOME}" != "${expected_home}" ]; then
    handle_error 1 "Base image home '${ROBOTICS_DOCKERS_USER_HOME}' differs from adapter home '${expected_home}'; use the adapter generated for that image project"
fi

old_user_id="$(validate_decimal_id ROBOTICS_DOCKERS_USER_ID "${ROBOTICS_DOCKERS_USER_ID}")" || exit $?
old_primary_group_id="$(validate_decimal_id ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID}")" || exit $?

user_name_count="$(awk -F: -v name="${ROBOTICS_DOCKERS_USER}" '$1 == name { count++ } END { print count + 0 }' /etc/passwd)" ||
    handle_error 1 "Could not inspect source user"

old_user_id_count="$(awk -F: -v id="${old_user_id}" '$3 == id { count++ } END { print count + 0 }' /etc/passwd)" ||
    handle_error 1 "Could not inspect source UID"

if [ "${user_name_count}" -ne 1 ]; then
    handle_error 1 "Source user '${ROBOTICS_DOCKERS_USER}' must identify exactly one local entry; found '${user_name_count}'"
fi

if [ "${old_user_id_count}" -ne 1 ]; then
    handle_error 1 "Source UID '${old_user_id}' must identify exactly one local entry; found '${old_user_id_count}'"
fi

user_entry="$(awk -F: -v name="${ROBOTICS_DOCKERS_USER}" '$1 == name { print }' /etc/passwd)" ||
    handle_error 1 "Could not read source user"

IFS=: read -r _ _ actual_user_id actual_group_id _ actual_home _ <<<"${user_entry}"
if [ "${actual_user_id}" != "${old_user_id}" ]; then
    handle_error 1 "Source user UID '${actual_user_id}' differs from image metadata UID '${old_user_id}'"
fi

if [ "${actual_group_id}" != "${old_primary_group_id}" ]; then
    handle_error 1 "Source user primary GID '${actual_group_id}' differs from image metadata GID '${old_primary_group_id}'"
fi

if [ "${actual_home}" != "${ROBOTICS_DOCKERS_USER_HOME}" ]; then
    handle_error 1 "Source user home '${actual_home}' differs from image metadata home '${ROBOTICS_DOCKERS_USER_HOME}'"
fi

group_name_count="$(awk -F: -v name="${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}" '$1 == name { count++ } END { print count + 0 }' /etc/group)" ||
    handle_error 1 "Could not inspect source primary group"

old_group_id_count="$(awk -F: -v id="${old_primary_group_id}" '$3 == id { count++ } END { print count + 0 }' /etc/group)" ||
    handle_error 1 "Could not inspect source primary GID"

if [ "${group_name_count}" -ne 1 ]; then
    handle_error 1 "Source primary group '${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}' must identify exactly one local group; found '${group_name_count}'"
fi

if [ "${old_group_id_count}" -ne 1 ]; then
    handle_error 1 "Source primary GID '${old_primary_group_id}' must identify exactly one local group; found '${old_group_id_count}'"
fi

actual_named_group_id="$(awk -F: -v name="${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}" '$1 == name { print $3 }' /etc/group)" ||
    handle_error 1 "Could not read source primary group"

if [ "${actual_named_group_id}" != "${old_primary_group_id}" ]; then
    handle_error 1 "Source primary group does not match image metadata"
fi

if [ -L "${ROBOTICS_DOCKERS_USER_HOME}" ]; then
    handle_error 1 "Source home '${ROBOTICS_DOCKERS_USER_HOME}' must not be a symbolic link"
fi

if [ ! -d "${ROBOTICS_DOCKERS_USER_HOME}" ]; then
    handle_error 1 "Source home '${ROBOTICS_DOCKERS_USER_HOME}' does not exist or is not a directory"
fi
home_owner="$(stat --format '%u:%g' -- "${ROBOTICS_DOCKERS_USER_HOME}")" ||
    handle_error 1 "Could not read source home ownership"

if [ "${home_owner}" != "${old_user_id}:${old_primary_group_id}" ]; then
    handle_error 1 "Source home belongs to '${home_owner}'; expected '${old_user_id}:${old_primary_group_id}'"
fi

if [ "${new_user_id}" != "${old_user_id}" ]; then
    target_user_count="$(awk -F: -v id="${new_user_id}" '$3 == id { count++ } END { print count + 0 }' /etc/passwd)" ||
        handle_error 1 "Could not inspect requested UID '${new_user_id}'"

    if [ "${target_user_count}" -ne 0 ]; then
        handle_error 1 "Requested UID '${new_user_id}' is already assigned to a local user"
    fi

    # usermod changes the mailbox and home automatically, but it does not change
    # files elsewhere. Refusing such files prevents the new layer from leaving
    # numeric ownership with no matching user.
    mailbox="/var/mail/${ROBOTICS_DOCKERS_USER}"
    spool_mailbox="/var/spool/mail/${ROBOTICS_DOCKERS_USER}"
    external_owned_file="$(find / -xdev \
        \( -path "${ROBOTICS_DOCKERS_USER_HOME}" -o -path "${ROBOTICS_DOCKERS_USER_HOME}/*" \
        -o -path "${mailbox}" -o -path "${spool_mailbox}" \) -prune -o \
        -uid "${old_user_id}" -print -quit 2>/dev/null)" ||
        handle_error 1 "Could not scan the image filesystem for source UID '${old_user_id}'"

    if [ -n "${external_owned_file}" ]; then
        handle_error 1 "Source UID '${old_user_id}' owns '${external_owned_file}' outside the home and mailbox; fix that ownership before adapting the image"
    fi
fi

target_group_count=0
target_group_name=""
preserved_group_name=""
if [ "${new_primary_group_id}" != "${old_primary_group_id}" ]; then
    target_group_count="$(awk -F: -v id="${new_primary_group_id}" '$3 == id { count++ } END { print count + 0 }' /etc/group)" ||
        handle_error 1 "Could not inspect requested primary GID '${new_primary_group_id}'"

    if [ "${target_group_count}" -gt 1 ]; then
        handle_error 1 "Requested primary GID '${new_primary_group_id}' is shared by multiple local group names"
    fi

    if [ "${target_group_count}" -eq 1 ]; then
        target_group_name="$(awk -F: -v id="${new_primary_group_id}" '$3 == id { print $1 }' /etc/group)" ||
            handle_error 1 "Could not read group with requested GID '${new_primary_group_id}'"

        # The existing group will receive the configured primary-group name.
        # groupmod validates and updates /etc/group and /etc/gshadow together.
    fi

    preserved_group_name="$(generate_preserved_group_name "${old_primary_group_id}")" ||
        handle_error 1 "Could not choose a name that preserves old GID '${old_primary_group_id}'"
fi

if [ "${new_user_id}:${new_primary_group_id}" != "${old_user_id}:${old_primary_group_id}" ]; then
    log warning "Changing home ownership can add a layer approximately as large as the affected home content"
fi

log info "Validated identity change '${old_user_id}:${old_primary_group_id}' -> '${new_user_id}:${new_primary_group_id}'"

if [ "${new_primary_group_id}" != "${old_primary_group_id}" ]; then
    groupmod --new-name "${preserved_group_name}" "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}" ||
        handle_error 1 "Could not preserve old primary group as '${preserved_group_name}'. Account files may be partially modified; inspect the failed intermediate image"
    if [ "${target_group_count}" -eq 0 ]; then
        groupadd --gid "${new_primary_group_id}" "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}" ||
            handle_error 1 "Old group was preserved as '${preserved_group_name}', but the requested group could not be created. Inspect the failed intermediate image"
    else
        groupmod --new-name "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}" "${target_group_name}" ||
            handle_error 1 "Old group was preserved as '${preserved_group_name}', but target group '${target_group_name}' could not be renamed. Inspect the failed intermediate image"
    fi
fi

usermod_arguments=()
if [ "${new_user_id}" != "${old_user_id}" ]; then
    usermod_arguments+=(--uid "${new_user_id}")
fi

if [ "${new_primary_group_id}" != "${old_primary_group_id}" ]; then
    usermod_arguments+=(--gid "${new_primary_group_id}")
fi

# ${#usermod_arguments[@]} returns the number of options prepared for usermod.
if [ "${#usermod_arguments[@]}" -gt 0 ]; then
    usermod "${usermod_arguments[@]}" "${ROBOTICS_DOCKERS_USER}" ||
        handle_error 1 "usermod failed after group preparation. /etc/passwd or home ownership may be partly updated; inspect the failed intermediate image"
else
    log info "UID and primary GID already match; usermod and the home traversal were skipped"
fi

final_user_entry="$(getent passwd "${ROBOTICS_DOCKERS_USER}")" || handle_error 1 "Could not read final user entry"
IFS=: read -r _ _ final_user_id final_user_primary_group_id _ _ _ <<<"${final_user_entry}"

if [ "${final_user_id}" != "${new_user_id}" ]; then
    handle_error 1 "Final user has UID '${final_user_id}'; expected '${new_user_id}'"
fi

if [ "${final_user_primary_group_id}" != "${new_primary_group_id}" ]; then
    handle_error 1 "Final user has primary GID '${final_user_primary_group_id}'; expected '${new_primary_group_id}'"
fi

final_group_entry="$(getent group "${ROBOTICS_DOCKERS_USER_PRIMARY_GROUP}")" ||
    handle_error 1 "Could not read final primary group"
IFS=: read -r _ _ final_group_id _ <<<"${final_group_entry}"
if [ "${final_group_id}" != "${new_primary_group_id}" ]; then
    handle_error 1 "Final primary group has GID '${final_group_id}'; expected '${new_primary_group_id}'"
fi

final_home_owner="$(stat --format '%u:%g' -- "${ROBOTICS_DOCKERS_USER_HOME}")" || handle_error 1 "Could not read final home ownership"
if [ "${final_home_owner}" != "${new_user_id}:${new_primary_group_id}" ]; then
    handle_error 1 "Final home belongs to '${final_home_owner}'; expected '${new_user_id}:${new_primary_group_id}'"
fi

if [ -n "${preserved_group_name}" ]; then
    preserved_group_id="$(awk -F: -v name="${preserved_group_name}" '$1 == name { print $3 }' /etc/group)" ||
        handle_error 1 "Could not read preserved group '${preserved_group_name}'"
    if [ "${preserved_group_id}" != "${old_primary_group_id}" ]; then
        handle_error 1 "Preserved group '${preserved_group_name}' has GID '${preserved_group_id}'; expected '${old_primary_group_id}'"
    fi
fi

log info "Image user adaptation complete: ${ROBOTICS_DOCKERS_USER} now uses ${new_user_id}:${new_primary_group_id}"
