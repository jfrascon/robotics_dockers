from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from robotics_dockers import DockerContextConfig, generate_docker_context

DOCKER_IMAGE = os.environ.get('ROBOTICS_DOCKERS_ENTRYPOINT_TEST_IMAGE', 'ubuntu:24.04')
IMAGE_USER = 'imageuser'
IMAGE_GROUP = 'imagegroup'
OLD_UID = 21001
OLD_GID = 22001
AUX_GID = 22501
NEW_UID = 23001
NEW_GID = 24001
DOCKER_GROUP_GID = 25001
MAX_USER_GROUP_ID = 4294967294


def _docker_unavailable_reason() -> str | None:
    if shutil.which('docker') is None:
        return 'Docker CLI is not installed'

    try:
        daemon = subprocess.run(['docker', 'info'], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f'Docker daemon check failed: {error}'

    if daemon.returncode != 0:
        return 'Docker daemon is unavailable to the test process'

    image = subprocess.run(
        ['docker', 'image', 'inspect', DOCKER_IMAGE], capture_output=True, text=True, timeout=15, check=False
    )
    if image.returncode != 0:
        return f'local Docker image {DOCKER_IMAGE!r} is unavailable; tests never pull it automatically'

    return None


DOCKER_UNAVAILABLE_REASON = _docker_unavailable_reason()
pytestmark = pytest.mark.skipif(DOCKER_UNAVAILABLE_REASON is not None, reason=DOCKER_UNAVAILABLE_REASON or '')


@pytest.fixture(scope='module')
def rendered_entrypoint(tmp_path_factory: pytest.TempPathFactory) -> Path:
    context_dir = tmp_path_factory.mktemp('entrypoint_root_context')
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user=IMAGE_USER,
            ros_distro='jazzy',
            img_id='local/entrypoint-root-test:latest',
            output_dir=context_dir,
        )
    )
    entrypoint = result.context_dir.joinpath('.resources', 'entrypoint_root.sh')

    syntax = subprocess.run(['bash', '-n', str(entrypoint)], capture_output=True, text=True, check=False)
    assert syntax.returncode == 0, syntax.stdout + syntax.stderr
    return entrypoint


def _run_entrypoint_case(
    rendered_entrypoint: Path,
    *,
    host_uid: int | str,
    host_gid: int | str,
    extra_setup: str = '',
    extra_environment: tuple[str, ...] = (),
    tmpfs_targets: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    # The usermod wrapper records how many times the real Ubuntu command is
    # called and with which arguments. The user entrypoint also records the
    # identity that setpriv gives to the final process.
    setup_script = f"""
set -u

groupadd --gid {OLD_GID} {IMAGE_GROUP}
groupadd --gid {AUX_GID} auxiliary
useradd --uid {OLD_UID} --gid {OLD_GID} --home-dir /home/{IMAGE_USER} --no-create-home --shell /bin/bash {IMAGE_USER}
useradd --uid 21002 --gid {OLD_GID} --home-dir /home/peer --no-create-home --shell /bin/bash peer
usermod --append --groups {AUX_GID} {IMAGE_USER}
install --directory --mode 755 --owner {OLD_UID} --group {OLD_GID} /home/{IMAGE_USER}

printf '%s\n' '#!/bin/bash' 'id -u >/tmp/final.uid' 'id -g >/tmp/final.gid' \
    'id -G >/tmp/final.groups' 'exec "$@"' >/home/{IMAGE_USER}/.entrypoint.sh
chmod 755 /home/{IMAGE_USER}/.entrypoint.sh
chown {OLD_UID}:{OLD_GID} /home/{IMAGE_USER}/.entrypoint.sh
printf '%s\n' '# Test environment file.' >/home/{IMAGE_USER}/.env.rc
chmod 644 /home/{IMAGE_USER}/.env.rc
chown {OLD_UID}:{OLD_GID} /home/{IMAGE_USER}/.env.rc

touch /home/{IMAGE_USER}/owned_old /home/{IMAGE_USER}/root_old_gid \
    /home/{IMAGE_USER}/old_uid_aux_gid /home/{IMAGE_USER}/root_root
chown {OLD_UID}:{OLD_GID} /home/{IMAGE_USER}/owned_old
chown 0:{OLD_GID} /home/{IMAGE_USER}/root_old_gid
chown {OLD_UID}:{AUX_GID} /home/{IMAGE_USER}/old_uid_aux_gid
chown 0:0 /home/{IMAGE_USER}/root_root

install --directory --mode 755 /usr/local/sbin /usr/local/bin
printf '%s\n' '#!/bin/bash' 'printf "%s\\n" "$*" >>/tmp/usermod.calls' \
    'exec /usr/sbin/usermod "$@"' >/usr/local/sbin/usermod
chmod 755 /usr/local/sbin/usermod

{extra_setup}

set +e
/usr/local/bin/entrypoint-under-test /bin/true
entrypoint_rc=$?
set -e

printf 'STATE entrypoint_rc=%s\n' "${{entrypoint_rc}}"
awk -F: '
    $1 == "{IMAGE_USER}" || $1 == "peer" || $1 ~ /^conflict/ || $1 == "duplicateuser" {{
        print "STATE passwd=" $0
    }}
' /etc/passwd
awk -F: '
    $1 == "{IMAGE_GROUP}" || $1 ~ /^rd_old_/ || $1 ~ /^target/ || $1 ~ /^duplicate/ || $1 == "otherold" {{
        print "STATE group=" $0
    }}
' /etc/group
awk -F: '$1 == "{IMAGE_GROUP}" || $1 ~ /^rd_old_/ || $1 ~ /^target/ {{ print "STATE gshadow=" $0 }}' \
    /etc/gshadow

for state_file in owned_old root_old_gid old_uid_aux_gid root_root mounted/nested_file; do
    if [ -e "/home/{IMAGE_USER}/${{state_file}}" ]; then
        stat --format "STATE file=${{state_file}}:%u:%g" "/home/{IMAGE_USER}/${{state_file}}"
    fi
done

stat --format 'STATE home=%u:%g' /home/{IMAGE_USER}
stat --format 'STATE log_mode=%u:%g:%a' /run/robotics-dockers/entrypoint.log
sed 's/^/STATE log=/' /run/robotics-dockers/entrypoint.log

if [ -L /run/robotics-dockers/entrypoint.log ]; then
    printf 'STATE log_symlink=yes\n'
else
    printf 'STATE log_symlink=no\n'
fi

if [ -f /tmp/protected-log-target ]; then
    printf 'STATE protected_log_target=%s\n' "$(cat /tmp/protected-log-target)"
fi

if [ -f /tmp/final.uid ]; then
    printf 'STATE final_uid=%s\n' "$(cat /tmp/final.uid)"
    printf 'STATE final_gid=%s\n' "$(cat /tmp/final.gid)"
    printf 'STATE final_groups=%s\n' "$(cat /tmp/final.groups)"
fi

if [ -f /tmp/usermod.calls ]; then
    printf 'STATE usermod_count=%s\n' "$(wc -l </tmp/usermod.calls)"
    sed 's/^/STATE usermod_call=/' /tmp/usermod.calls
else
    printf 'STATE usermod_count=0\n'
fi

set +e
grpck --read-only >/tmp/grpck.output 2>&1
grpck_rc=$?
set -e
printf 'STATE grpck_rc=%s\n' "${{grpck_rc}}"

exit "${{entrypoint_rc}}"
"""

    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        'root',
        '--env',
        f'IMAGE_MAIN_USER={IMAGE_USER}',
        '--env',
        f'HOST_UID={host_uid}',
        '--env',
        f'HOST_UPGID={host_gid}',
    ]
    for environment_value in extra_environment:
        command.extend(['--env', environment_value])
    command.extend(
        [
            '--group-add',
            str(DOCKER_GROUP_GID),
            '--mount',
            f'type=bind,source={rendered_entrypoint},target=/usr/local/bin/entrypoint-under-test,readonly',
            '--entrypoint',
            'bash',
        ]
    )
    for target in tmpfs_targets:
        command.extend(['--tmpfs', target])
    command.extend([DOCKER_IMAGE, '-lc', setup_script])

    return subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)


def _state_values(result: subprocess.CompletedProcess[str], key: str) -> list[str]:
    prefix = f'STATE {key}='
    return [line.removeprefix(prefix) for line in result.stdout.splitlines() if line.startswith(prefix)]


def _assert_successful_identity(
    result: subprocess.CompletedProcess[str], *, host_uid: int, host_gid: int, expected_usermod_call: str | None
) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert _state_values(result, 'entrypoint_rc') == ['0']
    assert f'{IMAGE_USER}:x:{host_uid}:{host_gid}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{host_gid}:' in _state_values(result, 'group')
    assert _state_values(result, 'home') == [f'{host_uid}:{host_gid}']
    assert _state_values(result, 'log_mode') == ['0:0:644']
    assert f'owned_old:{host_uid}:{host_gid}' in _state_values(result, 'file')
    assert f'root_old_gid:0:{host_gid}' in _state_values(result, 'file')
    assert f'old_uid_aux_gid:{host_uid}:{AUX_GID}' in _state_values(result, 'file')
    assert 'root_root:0:0' in _state_values(result, 'file')
    assert _state_values(result, 'grpck_rc') == ['0']
    assert _state_values(result, 'final_uid') == [str(host_uid)]
    assert _state_values(result, 'final_gid') == [str(host_gid)]
    final_groups = set(_state_values(result, 'final_groups')[0].split())
    assert str(host_gid) in final_groups
    assert str(AUX_GID) in final_groups
    assert str(DOCKER_GROUP_GID) in final_groups
    assert '0' not in final_groups
    assert 'command not found' not in result.stderr

    if expected_usermod_call is None:
        assert _state_values(result, 'usermod_count') == ['0']
        assert _state_values(result, 'usermod_call') == []
    else:
        assert _state_values(result, 'usermod_count') == ['1']
        assert _state_values(result, 'usermod_call') == [expected_usermod_call]


@pytest.mark.parametrize(
    ('host_uid', 'host_gid', 'extra_setup', 'expected_usermod_call'),
    [
        pytest.param(OLD_UID, OLD_GID, '', None, id='case-1-no-change'),
        pytest.param(OLD_UID, NEW_GID, '', f'--gid {NEW_GID} {IMAGE_USER}', id='case-2-1-gid-free'),
        pytest.param(
            OLD_UID,
            NEW_GID,
            f'groupadd --gid {NEW_GID} targetgroup',
            f'--gid {NEW_GID} {IMAGE_USER}',
            id='case-2-2-gid-existing',
        ),
        pytest.param(NEW_UID, OLD_GID, '', f'--uid {NEW_UID} {IMAGE_USER}', id='case-3-1-uid-free'),
        pytest.param(
            NEW_UID, NEW_GID, '', f'--uid {NEW_UID} --gid {NEW_GID} {IMAGE_USER}', id='case-4-1-uid-and-gid-free'
        ),
        pytest.param(
            NEW_UID,
            NEW_GID,
            f'groupadd --gid {NEW_GID} targetgroup',
            f'--uid {NEW_UID} --gid {NEW_GID} {IMAGE_USER}',
            id='case-4-2-uid-free-gid-existing',
        ),
    ],
)
def test_successful_uid_gid_matrix(
    rendered_entrypoint: Path, host_uid: int, host_gid: int, extra_setup: str, expected_usermod_call: str | None
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=host_uid, host_gid=host_gid, extra_setup=extra_setup)

    _assert_successful_identity(
        result, host_uid=host_uid, host_gid=host_gid, expected_usermod_call=expected_usermod_call
    )

    groups = _state_values(result, 'group')
    passwd = _state_values(result, 'passwd')
    if host_gid == OLD_GID:
        assert not any(group.startswith('rd_old_') for group in groups)
    else:
        assert f'rd_old_{OLD_GID}:x:{OLD_GID}:' in groups
        assert f'peer:x:21002:{OLD_GID}::/home/peer:/bin/bash' in passwd
        assert f'rd_old_{OLD_GID}:!::' in _state_values(result, 'gshadow')
        assert f'{IMAGE_GROUP}:!::' in _state_values(result, 'gshadow')


@pytest.mark.parametrize(
    ('host_gid', 'extra_group_setup'),
    [
        pytest.param(OLD_GID, '', id='case-3-2-uid-occupied-gid-unchanged'),
        pytest.param(NEW_GID, '', id='case-4-3-uid-occupied-gid-free'),
        pytest.param(NEW_GID, f'groupadd --gid {NEW_GID} targetgroup', id='case-4-4-uid-occupied-gid-existing'),
    ],
)
def test_uid_collision_fails_before_group_changes(
    rendered_entrypoint: Path, host_gid: int, extra_group_setup: str
) -> None:
    extra_setup = f"""
{extra_group_setup}
useradd --uid {NEW_UID} --gid {OLD_GID} --home-dir /home/conflict --no-create-home conflict
"""
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=NEW_UID, host_gid=host_gid, extra_setup=extra_setup)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert 'UID/GID adaptation has not started' in result.stderr


def test_preserved_group_name_gets_numeric_suffix_when_base_name_exists(rendered_entrypoint: Path) -> None:
    result = _run_entrypoint_case(
        rendered_entrypoint, host_uid=OLD_UID, host_gid=NEW_GID, extra_setup=f'groupadd --gid 25001 rd_old_{OLD_GID}'
    )

    _assert_successful_identity(
        result, host_uid=OLD_UID, host_gid=NEW_GID, expected_usermod_call=f'--gid {NEW_GID} {IMAGE_USER}'
    )
    assert f'rd_old_{OLD_GID}:x:25001:' in _state_values(result, 'group')
    assert f'rd_old_{OLD_GID}_1:x:{OLD_GID}:' in _state_values(result, 'group')


@pytest.mark.parametrize(
    ('extra_setup', 'expected_passwd_entry', 'expected_error'),
    [
        pytest.param(
            f"sed -i 's/^{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}:/{IMAGE_USER}:x:0{OLD_UID}:{OLD_GID}:/' /etc/passwd",
            f'{IMAGE_USER}:x:0{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash',
            'UID field',
            id='uid-leading-zero',
        ),
        pytest.param(
            f"sed -i 's/^{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}:/{IMAGE_USER}:x:{OLD_UID}:0{OLD_GID}:/' /etc/passwd",
            f'{IMAGE_USER}:x:{OLD_UID}:0{OLD_GID}::/home/{IMAGE_USER}:/bin/bash',
            'primary GID field',
            id='primary-gid-leading-zero',
        ),
    ],
)
def test_initial_validation_rejects_passwd_ids_with_leading_zeroes(
    rendered_entrypoint: Path, extra_setup: str, expected_passwd_entry: str, expected_error: str
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, extra_setup=extra_setup)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert expected_passwd_entry in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert expected_error in result.stderr
    assert 'without leading zeroes' in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


@pytest.mark.parametrize(
    ('host_uid', 'host_gid', 'expected_uid', 'expected_gid'),
    [
        pytest.param(f'000{NEW_UID}', f'000{NEW_GID}', NEW_UID, NEW_GID, id='leading-zeroes'),
        pytest.param(
            str(MAX_USER_GROUP_ID),
            str(MAX_USER_GROUP_ID),
            MAX_USER_GROUP_ID,
            MAX_USER_GROUP_ID,
            id='largest-supported-uid-gid',
        ),
    ],
)
def test_requested_ids_are_normalized_and_bounded_without_overflow(
    rendered_entrypoint: Path, host_uid: str, host_gid: str, expected_uid: int, expected_gid: int
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=host_uid, host_gid=host_gid)

    _assert_successful_identity(
        result,
        host_uid=expected_uid,
        host_gid=expected_gid,
        expected_usermod_call=f'--uid {expected_uid} --gid {expected_gid} {IMAGE_USER}',
    )


@pytest.mark.parametrize(
    ('host_uid', 'host_gid', 'expected_error'),
    [
        pytest.param(
            str(MAX_USER_GROUP_ID + 1), str(NEW_GID), 'HOST_UID must be less than or equal', id='uid-reserved'
        ),
        pytest.param(
            str(NEW_UID), str(MAX_USER_GROUP_ID + 1), 'HOST_UPGID must be less than or equal', id='gid-reserved'
        ),
        pytest.param('9' * 100, str(NEW_GID), 'HOST_UID must be less than or equal', id='uid-beyond-bash-range'),
        pytest.param(str(NEW_UID), '9' * 100, 'HOST_UPGID must be less than or equal', id='gid-beyond-bash-range'),
    ],
)
def test_requested_ids_outside_the_linux_range_fail_before_changes(
    rendered_entrypoint: Path, host_uid: str, host_gid: str, expected_error: str
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=host_uid, host_gid=host_gid)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert expected_error in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr
    assert 'value too great for base' not in result.stderr


@pytest.mark.parametrize(
    ('extra_setup', 'expected_error'),
    [
        pytest.param(
            (
                f'useradd --non-unique --uid {OLD_UID} --gid {OLD_GID} '
                '--home-dir /home/duplicate --no-create-home duplicateuser'
            ),
            f"UID '{OLD_UID}' is used by '2' local /etc/passwd entries",
            id='current-uid-duplicated',
        ),
        pytest.param(
            f"""useradd --uid {NEW_UID} --gid {OLD_GID} --home-dir /home/conflict1 --no-create-home conflict1
useradd --non-unique --uid {NEW_UID} --gid {OLD_GID} --home-dir /home/conflict2 --no-create-home conflict2""",
            f"Requested HOST_UID '{NEW_UID}' is already used by '2' local /etc/passwd entry or entries",
            id='requested-uid-duplicated',
        ),
        pytest.param(
            f'groupadd --non-unique --gid {OLD_GID} otherold',
            f"GID '{OLD_GID}', which is the primary GID of '{IMAGE_USER}', is used by '2'",
            id='current-primary-gid-ambiguous',
        ),
        pytest.param(
            f"""groupadd --gid {NEW_GID} targetgroup
groupadd --non-unique --gid {NEW_GID} targetduplicate""",
            f"Requested HOST_UPGID '{NEW_GID}' is shared by '2' local /etc/group entries",
            id='requested-primary-gid-ambiguous',
        ),
        pytest.param(
            f'chown 0:{OLD_GID} /home/{IMAGE_USER}',
            f"expected IMAGE_MAIN_USER_ID:IMAGE_MAIN_USER_PRIMARY_GID '{OLD_UID}:{OLD_GID}'",
            id='home-uid-wrong',
        ),
        pytest.param(
            f'chown {OLD_UID}:0 /home/{IMAGE_USER}',
            f"expected IMAGE_MAIN_USER_ID:IMAGE_MAIN_USER_PRIMARY_GID '{OLD_UID}:{OLD_GID}'",
            id='home-gid-wrong',
        ),
    ],
)
def test_initial_validation_rejects_duplicate_or_unsafe_local_state(
    rendered_entrypoint: Path, extra_setup: str, expected_error: str
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, extra_setup=extra_setup)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert expected_error in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


@pytest.mark.parametrize(
    ('extra_setup', 'expected_error'),
    [
        pytest.param(
            f"sed -i 's/^{IMAGE_GROUP}:x:{OLD_GID}:/{IMAGE_GROUP}:x:0{OLD_GID}:/' /etc/group",
            'must use canonical decimal value',
            id='image-primary-group-gid-leading-zero',
        ),
        pytest.param(
            (
                f'groupadd --gid {NEW_GID} targetgroup\n'
                f"sed -i 's/^targetgroup:x:{NEW_GID}:/targetgroup:x:0{NEW_GID}:/' /etc/group"
            ),
            'must use canonical decimal value',
            id='target-group-gid-leading-zero',
        ),
        pytest.param(
            f"groupadd --gid {NEW_GID} targetgroup\nprintf '%s\\n' 'targetgroup:x:26001:' >>/etc/group",
            "Target group name 'targetgroup' is used by '2'",
            id='target-group-name-duplicated',
        ),
        pytest.param(
            f'mv /home/{IMAGE_USER} /home/{IMAGE_USER}.real\nln -s /home/{IMAGE_USER}.real /home/{IMAGE_USER}',
            'must be a directory, not a symbolic link',
            id='home-is-symbolic-link',
        ),
        pytest.param(
            f'rm /home/{IMAGE_USER}/.env.rc', 'must be a readable regular file', id='required-environment-file-missing'
        ),
        pytest.param(
            f"printf '%s\\n' 'rd_old_{OLD_GID}:orphan-password:admin:member' >>/etc/gshadow",
            "Local files '/etc/group' and '/etc/gshadow' must contain the same group names",
            id='orphan-preserved-name-in-gshadow',
        ),
    ],
)
def test_initial_validation_rejects_additional_unsafe_states(
    rendered_entrypoint: Path, extra_setup: str, expected_error: str
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, extra_setup=extra_setup)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert expected_error in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


@pytest.mark.parametrize(
    ('extra_setup', 'expected_error'),
    [
        pytest.param(
            f'chown 0:0 /home/{IMAGE_USER}/.entrypoint.sh\nchmod 700 /home/{IMAGE_USER}/.entrypoint.sh',
            'must be executable by IMAGE_MAIN_USER',
            id='entrypoint-not-executable-by-image-user',
        ),
        pytest.param(
            f'chown 0:0 /home/{IMAGE_USER}/.env.rc\nchmod 600 /home/{IMAGE_USER}/.env.rc',
            'must be readable by IMAGE_MAIN_USER',
            id='environment-not-readable-by-image-user',
        ),
    ],
)
def test_initial_validation_checks_required_file_access_as_image_user(
    rendered_entrypoint: Path, extra_setup: str, expected_error: str
) -> None:
    result = _run_entrypoint_case(rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, extra_setup=extra_setup)

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert expected_error in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


def test_invalid_xdg_runtime_dir_fails_before_account_changes(rendered_entrypoint: Path) -> None:
    result = _run_entrypoint_case(
        rendered_entrypoint,
        host_uid=NEW_UID,
        host_gid=NEW_GID,
        extra_environment=('XDG_RUNTIME_DIR=/tmp/unexpected-runtime-dir',),
    )

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert "XDG_RUNTIME_DIR must be '/run/user/23001'" in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


def test_missing_requested_nvidia_driver_fails_before_account_changes(rendered_entrypoint: Path) -> None:
    result = _run_entrypoint_case(
        rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, extra_environment=('USE_HOST_NVIDIA_DRIVER=true',)
    )

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_USER}:x:{OLD_UID}:{OLD_GID}::/home/{IMAGE_USER}:/bin/bash' in _state_values(result, 'passwd')
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert 'NVIDIA driver validation failed' in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


def test_busy_image_uid_fails_before_group_preparation(rendered_entrypoint: Path) -> None:
    result = _run_entrypoint_case(
        rendered_entrypoint,
        host_uid=NEW_UID,
        host_gid=NEW_GID,
        extra_setup=f'setpriv --reuid {OLD_UID} --regid {OLD_GID} --clear-groups sleep 300 &',
    )

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert f'{IMAGE_GROUP}:x:{OLD_GID}:' in _state_values(result, 'group')
    assert not any(group.startswith('rd_old_') for group in _state_values(result, 'group'))
    assert 'owns one or more running processes' in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


def test_world_readable_log_does_not_copy_password_or_group_records(rendered_entrypoint: Path) -> None:
    secret = 'do-not-copy-this-password-field'
    result = _run_entrypoint_case(
        rendered_entrypoint,
        host_uid=NEW_UID,
        host_gid=NEW_GID,
        extra_setup=(
            f"sed -i 's/^{IMAGE_USER}:x:/{IMAGE_USER}:{secret}:/' /etc/passwd\nsed -i 's#:/bin/bash$#:#' /etc/passwd"
        ),
    )

    assert result.returncode != 0
    assert _state_values(result, 'log_mode') == ['0:0:644']
    assert secret not in '\n'.join(_state_values(result, 'log'))
    assert secret not in result.stderr
    assert 'shell field' in result.stderr


def test_log_initialization_does_not_follow_an_existing_symbolic_link(rendered_entrypoint: Path) -> None:
    protected_contents = 'must-not-be-truncated'
    result = _run_entrypoint_case(
        rendered_entrypoint,
        host_uid=OLD_UID,
        host_gid=OLD_GID,
        extra_setup=(
            'install --directory /run/robotics-dockers\n'
            f"printf '%s\\n' '{protected_contents}' >/tmp/protected-log-target\n"
            'ln -s /tmp/protected-log-target /run/robotics-dockers/entrypoint.log'
        ),
    )

    _assert_successful_identity(result, host_uid=OLD_UID, host_gid=OLD_GID, expected_usermod_call=None)
    assert _state_values(result, 'log_symlink') == ['no']
    assert _state_values(result, 'protected_log_target') == [protected_contents]


def test_complete_home_mount_is_rejected(rendered_entrypoint: Path) -> None:
    result = _run_entrypoint_case(
        rendered_entrypoint, host_uid=NEW_UID, host_gid=NEW_GID, tmpfs_targets=(f'/home/{IMAGE_USER}',)
    )

    assert result.returncode != 0
    assert _state_values(result, 'usermod_count') == ['0']
    assert 'is itself a mount target' in result.stderr
    assert 'UID/GID adaptation has not started' in result.stderr


def test_nested_mount_is_processed_like_other_home_content(rendered_entrypoint: Path) -> None:
    extra_setup = f"""
printf nested >/home/{IMAGE_USER}/mounted/nested_file
chown {OLD_UID}:{OLD_GID} /home/{IMAGE_USER}/mounted/nested_file
"""
    result = _run_entrypoint_case(
        rendered_entrypoint,
        host_uid=NEW_UID,
        host_gid=NEW_GID,
        extra_setup=extra_setup,
        tmpfs_targets=(f'/home/{IMAGE_USER}/mounted',),
    )

    _assert_successful_identity(
        result,
        host_uid=NEW_UID,
        host_gid=NEW_GID,
        expected_usermod_call=f'--uid {NEW_UID} --gid {NEW_GID} {IMAGE_USER}',
    )
    assert f'mounted/nested_file:{NEW_UID}:{NEW_GID}' in _state_values(result, 'file')
