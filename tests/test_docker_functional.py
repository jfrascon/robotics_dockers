from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

import pytest

from robotics_dockers import DockerContextConfig, generate_docker_context
from robotics_dockers.config import ROS_DISTROS

ROS_UBUNTU_PAIRS = tuple(ROS_DISTROS.items())
UBUNTU_IMAGES = tuple(dict.fromkeys(f'ubuntu:{ubuntu_version}' for ubuntu_version in ROS_DISTROS.values()))
ENSURED_DOCKER_IMAGES: set[str] = set()

pytestmark = pytest.mark.docker


# Both Ubuntu 22.04 and 24.04 are normalized to the identity expected by the
# user-preparation examples. Ubuntu 24.04 already supplies this account, while
# older minimal images may not. The test must exercise the same example input
# on every supported base instead of depending on Canonical's initial accounts.
ENSURE_UBUNTU_TEST_ACCOUNT = r"""
if getent passwd ubuntu >/dev/null 2>&1; then
    [ "$(getent passwd ubuntu | cut -d: -f1,3,4,6,7)" = \
        "ubuntu:1000:1000:/home/ubuntu:/bin/bash" ] || exit 1
    getent group ubuntu | grep -q '^ubuntu:x:1000:' || exit 1
    [ -d /home/ubuntu ] || exit 1
else
    ! getent passwd 1000 >/dev/null 2>&1 || exit 1
    ! getent group 1000 >/dev/null 2>&1 || exit 1
    [ ! -e /home/ubuntu ] || exit 1
    groupadd --gid 1000 ubuntu || exit 1
    useradd --uid 1000 --gid 1000 --comment Ubuntu --home-dir /home/ubuntu \
        --create-home --shell /bin/bash ubuntu || exit 1
fi
"""


def _docker_reason() -> str | None:
    if shutil.which('docker') is None:
        return 'Docker CLI is not installed'
    try:
        daemon = subprocess.run(['docker', 'info'], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return f'Docker check failed: {error}'
    if daemon.returncode != 0:
        return 'Docker daemon is unavailable to the test process'
    return None


DOCKER_REASON = _docker_reason()


def _ensure_docker_image(image: str) -> None:
    """Reuse a local public image, pulling its exact tag only when absent."""
    if image in ENSURED_DOCKER_IMAGES:
        return

    if DOCKER_REASON is not None:
        pytest.skip(DOCKER_REASON)

    try:
        inspection = subprocess.run(
            ['docker', 'image', 'inspect', image], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.fail(f"Could not inspect Docker image '{image}': {error}")

    if inspection.returncode == 0:
        ENSURED_DOCKER_IMAGES.add(image)
        return

    # Pull only when the exact tag is absent. A registry or network failure is
    # a failed functional-test environment, not a silently skipped test.
    try:
        pull = subprocess.run(['docker', 'pull', image], capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.fail(f"Could not pull Docker image '{image}': {error}")

    if pull.returncode != 0:
        pytest.fail(f"Could not pull Docker image '{image}':\n{pull.stdout}{pull.stderr}")

    ENSURED_DOCKER_IMAGES.add(image)


@pytest.fixture(scope='module', params=UBUNTU_IMAGES, ids=lambda image: image)
def ubuntu_image(request: pytest.FixtureRequest) -> str:
    image = str(request.param)
    _ensure_docker_image(image)
    return image


@pytest.fixture(scope='module', params=ROS_UBUNTU_PAIRS, ids=lambda pair: f'{pair[0]}-ubuntu-{pair[1]}')
def ros_ubuntu_pair(request: pytest.FixtureRequest) -> tuple[str, str]:
    ros_distro, ubuntu_version = request.param
    return str(ros_distro), str(ubuntu_version)


@pytest.fixture(scope='module')
def ros_core_case(ros_ubuntu_pair: tuple[str, str]) -> tuple[str, str, str]:
    ros_distro, ubuntu_version = ros_ubuntu_pair
    image = f'ros:{ros_distro}-ros-core'
    _ensure_docker_image(image)
    return ros_distro, ubuntu_version, image


@pytest.fixture(scope='module')
def entrypoint_test_image(ubuntu_image: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    # Build the smallest environment needed by the real generated entrypoint.
    # The image uses only the current supported Ubuntu and local generated
    # files. Once the base is available, this build neither installs packages
    # nor accesses the network.
    ubuntu_version = ubuntu_image.rsplit(':', maxsplit=1)[1]
    ros_distro = next(
        distro for distro, configured_ubuntu_version in ROS_UBUNTU_PAIRS if configured_ubuntu_version == ubuntu_version
    )
    context = tmp_path_factory.mktemp(f'rendered-entrypoint-{ubuntu_version}')
    result = generate_docker_context(
        DockerContextConfig(ros_distro=ros_distro, img_id='local/entrypoint-functional-test:latest', output_dir=context)
    )
    dockerfile = result.context_dir / 'Dockerfile.entrypoint-test'
    dockerfile.write_text(
        f"""FROM {ubuntu_image}
RUN groupadd --gid 1001 jfr \\
    && useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash jfr
COPY .resources/entrypoint_user.sh /usr/local/bin/entrypoint
COPY .resources/env.rc /home/jfr/.env.rc
COPY .resources/ros.rc /home/jfr/.ros.rc
RUN chmod 0755 /usr/local/bin/entrypoint \\
    && chown 1001:1001 /home/jfr/.env.rc /home/jfr/.ros.rc
USER jfr
WORKDIR /home/jfr
ENTRYPOINT [\"/usr/local/bin/entrypoint\"]
CMD [\"bash\"]
"""
    )

    image_tag = f'robotics-dockers-entrypoint-test:{os.getpid()}-{ubuntu_version}'
    build = subprocess.run(
        ['docker', 'build', '--file', str(dockerfile), '--tag', image_tag, str(result.context_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if build.returncode != 0:
        pytest.fail(build.stdout + build.stderr)

    try:
        yield image_tag
    finally:
        subprocess.run(
            ['docker', 'image', 'rm', '--force', image_tag], capture_output=True, text=True, timeout=30, check=False
        )


def _run(image: str, script: str, *, mounts: tuple[tuple[Path, str], ...] = (), args: tuple[str, ...] = ()):
    command = ['docker', 'run', '--rm', '--user', 'root']
    for source, target in mounts:
        command.extend(['--mount', f'type=bind,source={source},target={target},readonly'])
    command.extend(['--entrypoint', 'bash', image, '-lc', script, 'bash', *args])
    return subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)


def test_supported_ubuntu_base_contract(ubuntu_image: str) -> None:
    expected_version = ubuntu_image.rsplit(':', maxsplit=1)[1]
    completed = _run(
        ubuntu_image,
        f"""
. /etc/os-release || exit 1
[ "${{VERSION_ID}}" = "{expected_version}" ] || exit 1
for required_command in \
    awk bash cut find getent groupadd groupdel install passwd runuser sed sha256sum \
    stat useradd userdel usermod groupmod; do
    command -v "${{required_command}}" >/dev/null 2>&1 || exit 1
done
printf 'VERSION=%s ACCOUNT=%s\n' \
    "${{VERSION_ID}}" "$(getent passwd ubuntu 2>/dev/null || echo absent)"
""",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f'VERSION={expected_version}' in completed.stdout


def test_ros_source_sanitizer_accepts_a_clean_apt_configuration(ubuntu_image: str, tmp_path: Path) -> None:
    """No legacy ROS source is a successful no-op, not a grep failure."""
    install_ros = resources.files('robotics_dockers.resources').joinpath('install_ros2.sh').read_text()
    entry_point_marker = '\n# Entry point\n'
    assert entry_point_marker in install_ros
    function_definitions = install_ros.split(entry_point_marker, maxsplit=1)[0]
    sanitizer_test = tmp_path / 'sanitize-clean-apt.sh'
    sanitizer_test.write_text(f'{function_definitions}\nsanitize clean-test-codename\n')

    completed = _run(
        ubuntu_image, 'bash /tmp/sanitize-clean-apt.sh', mounts=((sanitizer_test, '/tmp/sanitize-clean-apt.sh'),)
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_ros_source_sanitizer_preserves_unrelated_sources_and_keys(ubuntu_image: str, tmp_path: Path) -> None:
    """Removing a legacy ROS line must not remove neighboring sources or their key file."""
    install_ros = resources.files('robotics_dockers.resources').joinpath('install_ros2.sh').read_text()
    entry_point_marker = '\n# Entry point\n'
    assert entry_point_marker in install_ros
    function_definitions = install_ros.split(entry_point_marker, maxsplit=1)[0]
    sanitizer_test = tmp_path / 'sanitize-mixed-apt.sh'
    sanitizer_test.write_text(
        f"""{function_definitions}
mkdir -p /etc/apt/keyrings /etc/apt/sources.list.d || exit 1
legacy_key=/etc/apt/keyrings/legacy-ros-test.gpg
mixed_sources=/etc/apt/sources.list.d/robotics-dockers-mixed-test.list
commented_sources=/etc/apt/sources.list.d/robotics-dockers-commented-test.list
: >"${{legacy_key}}" || exit 1
cat >"${{mixed_sources}}" <<'EOF'
# Keep this project comment.
deb [signed-by=/etc/apt/keyrings/legacy-ros-test.gpg] http://packages.ros.org/ros2/ubuntu clean-test-codename main
deb [signed-by=/etc/apt/keyrings/vendor-test.gpg] https://vendor.example/ubuntu clean-test-codename main
EOF
cat >"${{commented_sources}}" <<'EOF'
# Keep this explanation even when no apt source remains below it.
deb http://packages.ros.org/ros2/ubuntu clean-test-codename main
EOF
sanitize clean-test-codename || exit 1
grep --quiet 'vendor.example' "${{mixed_sources}}" || exit 1
if grep --quiet 'packages.ros.org' "${{mixed_sources}}"; then exit 1; fi
[ -f "${{commented_sources}}" ] || exit 1
grep --quiet 'Keep this explanation' "${{commented_sources}}" || exit 1
if grep --quiet 'packages.ros.org' "${{commented_sources}}"; then exit 1; fi
[ -f "${{legacy_key}}" ] || exit 1
"""
    )

    completed = _run(
        ubuntu_image, 'bash /tmp/sanitize-mixed-apt.sh', mounts=((sanitizer_test, '/tmp/sanitize-mixed-apt.sh'),)
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_configure_image_user_creates_exact_locked_identity(ubuntu_image: str) -> None:
    configure = Path(str(resources.files('robotics_dockers.resources').joinpath('configure_image_user.sh')))
    completed = _run(
        ubuntu_image,
        """
bash /tmp/configure developer 21001 robotics 22001 /home/developer || exit 1
getent passwd developer
getent group robotics
passwd --status developer
id developer
awk -F: '$1 == "dialout" {
    count = split($4, members, ",")
    for (member_index = 1; member_index <= count; member_index++) {
        if (members[member_index] == "developer") found = 1
    }
} END { print "DIALOUT_GSHADOW=" (found ? "yes" : "no") }' /etc/gshadow || exit 1
awk -F: '$1 == "video" {
    count = split($4, members, ",")
    for (member_index = 1; member_index <= count; member_index++) {
        if (members[member_index] == "developer") found = 1
    }
} END { print "VIDEO_GSHADOW=" (found ? "yes" : "no") }' /etc/gshadow || exit 1
stat --format 'HOME=%u:%g:%a' /home/developer
""",
        mounts=((configure, '/tmp/configure'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'developer:x:21001:22001::/home/developer:/bin/bash' in completed.stdout
    assert 'robotics:x:22001:' in completed.stdout
    assert 'developer L ' in completed.stdout
    assert 'HOME=21001:22001:750' in completed.stdout
    assert 'dialout' in completed.stdout
    assert 'video' in completed.stdout
    assert 'DIALOUT_GSHADOW=yes' in completed.stdout
    assert 'VIDEO_GSHADOW=yes' in completed.stdout


def test_configure_image_user_reuses_an_exact_existing_identity(ubuntu_image: str) -> None:
    configure = Path(str(resources.files('robotics_dockers.resources').joinpath('configure_image_user.sh')))
    completed = _run(
        ubuntu_image,
        """
groupadd --gid 22001 robotics
useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer
touch /home/developer/preserved
chown 21001:22001 /home/developer/preserved
mkdir /home/developer/.config
chown root:root /home/developer/.config
chmod 0700 /home/developer/.config
bash /tmp/configure developer 21001 robotics 22001 /home/developer || exit 1
getent passwd developer
getent group robotics
stat --format 'PRESERVED=%u:%g' /home/developer/preserved
stat --format 'CONFIG=%u:%g:%a' /home/developer/.config
""",
        mounts=((configure, '/tmp/configure'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'Reusing primary group' in completed.stdout
    assert 'Reusing exact existing user' in completed.stdout
    assert 'developer:x:21001:22001::/home/developer:/bin/bash' in completed.stdout
    assert 'PRESERVED=21001:22001' in completed.stdout
    assert 'CONFIG=21001:22001:700' in completed.stdout


@pytest.mark.parametrize(
    'collision_setup',
    [
        'groupadd --gid 22001 robotics; useradd --uid 21002 --gid 22001 developer',
        'groupadd --gid 22001 robotics; useradd --uid 21001 --gid 22001 otheruser',
        'groupadd --gid 22002 robotics',
        'groupadd --gid 22001 othergroup',
        'mkdir /home/developer',
        'groupadd --gid 22001 robotics; useradd --uid 21001 --gid 22001 --home-dir /srv/developer developer',
        'groupadd --gid 22001 robotics; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --shell /bin/sh developer',
        'groupadd --gid 22001 robotics; mkdir /home/developer; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --no-create-home --shell /bin/bash developer',
        'groupadd --gid 22001 robotics; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer; '
        'echo "duplicate:x:21001:22001::/home/duplicate:/bin/bash" >>/etc/passwd',
        'groupadd --gid 22001 robotics; echo "duplicate:x:22001:" >>/etc/group',
        'groupadd --gid 22001 robotics; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer; '
        'echo "developer:x:21002:22001::/home/developer-two:/bin/bash" >>/etc/passwd',
        'groupadd --gid 22001 robotics; echo "robotics:x:22002:" >>/etc/group',
        'groupadd --gid 22001 robotics; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer; '
        "sed -i '/^robotics:/d' /etc/group; sed -i '/^robotics:/d' /etc/gshadow",
        'groupadd --gid 22001 robotics; mkdir -p /srv/developer; chown 21001:22001 /srv/developer; '
        'ln -s /srv/developer /home/developer; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --no-create-home --shell /bin/bash developer',
        'groupadd --gid 22001 robotics; '
        'useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer; '
        'mkdir -p /srv/developer-config; ln -s /srv/developer-config /home/developer/.config',
        'ln -s /tmp /etc/skel/.config',
    ],
)
def test_configure_image_user_rejects_collisions_before_mutation(ubuntu_image: str, collision_setup: str) -> None:
    configure = Path(str(resources.files('robotics_dockers.resources').joinpath('configure_image_user.sh')))
    completed = _run(
        ubuntu_image,
        f"""
{collision_setup}
before="$(sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow)"
bash /tmp/configure developer 21001 robotics 22001 /home/developer
rc=$?
after="$(sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow)"
printf 'RC=%s\\nUNCHANGED=%s\\n' "$rc" "$([ "$before" = "$after" ] && echo yes || echo no)"
exit 0
""",
        mounts=((configure, '/tmp/configure'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'RC=0' not in completed.stdout
    assert 'UNCHANGED=yes' in completed.stdout


@pytest.mark.parametrize(
    'example_name,verification',
    [
        (
            '01-delete-ubuntu-user.sh.example',
            '! getent passwd ubuntu && ! getent group ubuntu && [ ! -e /home/ubuntu ]',
        ),
        (
            '02-reuse-ubuntu-user.sh.example',
            '[ "$(getent passwd developer | cut -d: -f1,3,4,6,7)" = '
            '"developer:1000:1000:/home/developer:/bin/bash" ] '
            '&& getent group developer | grep -q "^developer:x:1000:" && [ -d /home/developer ]',
        ),
    ],
)
def test_ubuntu_user_preparation_examples(ubuntu_image: str, example_name: str, verification: str) -> None:
    example = Path(str(resources.files('robotics_dockers.resources').joinpath('user_preparation.d', example_name)))
    completed = _run(
        ubuntu_image,
        f"""
{ENSURE_UBUNTU_TEST_ACCOUNT}
export ROBOTICS_DOCKERS_USER=developer ROBOTICS_DOCKERS_USER_ID=1000
export ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1000
export ROBOTICS_DOCKERS_USER_HOME=/home/developer
bash /tmp/example || exit 1
{verification}
""",
        mounts=((example, '/tmp/example'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_reuse_ubuntu_example_validates_known_identity_before_mutation(ubuntu_image: str) -> None:
    example = Path(
        str(
            resources.files('robotics_dockers.resources').joinpath(
                'user_preparation.d', '02-reuse-ubuntu-user.sh.example'
            )
        )
    )
    completed = _run(
        ubuntu_image,
        f"""
{ENSURE_UBUNTU_TEST_ACCOUNT}
usermod --shell /bin/sh ubuntu
before="$(sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow)"
export ROBOTICS_DOCKERS_USER=developer ROBOTICS_DOCKERS_USER_ID=1000
export ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1000
export ROBOTICS_DOCKERS_USER_HOME=/home/developer
bash /tmp/example
rc=$?
after="$(sha256sum /etc/passwd /etc/group /etc/shadow /etc/gshadow)"
printf 'RC=%s\nUNCHANGED=%s\n' "$rc" "$([ "$before" = "$after" ] && echo yes || echo no)"
exit 0
""",
        mounts=((example, '/tmp/example'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'RC=0' not in completed.stdout
    assert 'UNCHANGED=yes' in completed.stdout


def test_adapter_changes_owners_selectively_with_one_usermod_call(ubuntu_image: str) -> None:
    adapter = Path(str(resources.files('robotics_dockers.resources').joinpath('update_image_user.sh')))
    completed = _run(
        ubuntu_image,
        """
groupadd --gid 22001 developer
groupadd --gid 22501 auxiliary
useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer
touch /home/developer/old_old /home/developer/root_old /home/developer/old_aux /home/developer/root_root
chown 21001:22001 /home/developer/old_old
chown 0:22001 /home/developer/root_old
chown 21001:22501 /home/developer/old_aux
chown 0:0 /home/developer/root_root
mkdir -p /usr/local/sbin
printf '%s\n' '#!/bin/bash' 'echo "$*" >>/tmp/usermod.calls' 'exec /usr/sbin/usermod "$@"' >/usr/local/sbin/usermod
chmod 0755 /usr/local/sbin/usermod
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ROBOTICS_DOCKERS_USER=developer ROBOTICS_DOCKERS_USER_ID=21001
export ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=22001
export ROBOTICS_DOCKERS_USER_HOME=/home/developer
bash /tmp/adapter 23001 24001 developer /home/developer || exit 1
printf 'CALLS=%s\n' "$(wc -l </tmp/usermod.calls)"
getent passwd developer
getent group developer
getent group rd_old_22001
for file in old_old root_old old_aux root_root; do stat --format "$file=%u:%g" "/home/developer/$file"; done
""",
        mounts=((adapter, '/tmp/adapter'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'CALLS=1' in completed.stdout
    assert 'developer:x:23001:24001::/home/developer:/bin/bash' in completed.stdout
    assert 'developer:x:24001:' in completed.stdout
    assert 'rd_old_22001:x:22001:' in completed.stdout
    assert 'old_old=23001:24001' in completed.stdout
    assert 'root_old=0:24001' in completed.stdout
    assert 'old_aux=23001:22501' in completed.stdout
    assert 'root_root=0:0' in completed.stdout


def test_adapter_reuses_existing_target_gid_and_preserves_other_users(ubuntu_image: str) -> None:
    adapter = Path(str(resources.files('robotics_dockers.resources').joinpath('update_image_user.sh')))
    completed = _run(
        ubuntu_image,
        """
groupadd --gid 22001 developer
groupadd --gid 24001 targetgroup
useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer
useradd --uid 21002 --gid 22001 --home-dir /home/old-peer --no-create-home --shell /bin/bash old-peer
useradd --uid 21003 --gid 24001 --home-dir /home/target-peer --no-create-home --shell /bin/bash target-peer
touch /outside-old-group && chown 0:22001 /outside-old-group
export ROBOTICS_DOCKERS_USER=developer ROBOTICS_DOCKERS_USER_ID=21001
export ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=22001
export ROBOTICS_DOCKERS_USER_HOME=/home/developer
bash /tmp/adapter 21001 24001 developer /home/developer || exit 1
getent passwd developer
getent passwd old-peer
getent passwd target-peer
getent group developer
getent group rd_old_22001
stat --format 'OUTSIDE=%u:%g' /outside-old-group
""",
        mounts=((adapter, '/tmp/adapter'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'developer:x:21001:24001::/home/developer:/bin/bash' in completed.stdout
    assert 'old-peer:x:21002:22001::/home/old-peer:/bin/bash' in completed.stdout
    assert 'target-peer:x:21003:24001::/home/target-peer:/bin/bash' in completed.stdout
    assert 'developer:x:24001:' in completed.stdout
    assert 'rd_old_22001:x:22001:' in completed.stdout
    assert 'OUTSIDE=0:22001' in completed.stdout


def test_adapter_rejects_external_uid_ownership_before_group_changes(ubuntu_image: str) -> None:
    adapter = Path(str(resources.files('robotics_dockers.resources').joinpath('update_image_user.sh')))
    completed = _run(
        ubuntu_image,
        """
groupadd --gid 22001 developer
useradd --uid 21001 --gid 22001 --home-dir /home/developer --create-home --shell /bin/bash developer
touch /external-owned && chown 21001:22001 /external-owned
export ROBOTICS_DOCKERS_USER=developer ROBOTICS_DOCKERS_USER_ID=21001
export ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=22001
export ROBOTICS_DOCKERS_USER_HOME=/home/developer
bash /tmp/adapter 23001 24001 developer /home/developer
rc=$?
printf 'RC=%s\n' "$rc"
getent passwd developer
getent group developer
exit 0
""",
        mounts=((adapter, '/tmp/adapter'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'RC=0' not in completed.stdout
    assert 'developer:x:21001:22001::/home/developer:/bin/bash' in completed.stdout
    assert 'developer:x:22001:' in completed.stdout
    assert 'outside the home and mailbox' in completed.stderr


def test_generated_adapter_dockerfile_updates_image_contract(ubuntu_image: str, tmp_path: Path) -> None:
    ubuntu_version = ubuntu_image.rsplit(':', maxsplit=1)[1]
    ros_distro = next(
        distro for distro, configured_ubuntu_version in ROS_UBUNTU_PAIRS if configured_ubuntu_version == ubuntu_version
    )
    context = tmp_path / 'context'
    generate_docker_context(
        DockerContextConfig(ros_distro=ros_distro, img_id='local/adapter-functional-source:latest', output_dir=context)
    )
    context.joinpath('Dockerfile.adapter-test-base').write_text(
        f"""FROM {ubuntu_image}
RUN groupadd --gid 22001 robotics \\
    && useradd --uid 21001 --gid 22001 --create-home --shell /bin/bash developer \\
    && touch /home/developer/owned \\
    && chown 21001:22001 /home/developer/owned
ENV ROBOTICS_DOCKERS_USER="developer" \\
    ROBOTICS_DOCKERS_USER_ID="21001" \\
    ROBOTICS_DOCKERS_USER_HOME="/home/developer" \\
    ROBOTICS_DOCKERS_USER_PRIMARY_GROUP="robotics" \\
    ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID="22001"
LABEL io.github.jfrascon.robotics-dockers.user.name="developer" \\
    io.github.jfrascon.robotics-dockers.user.id="21001" \\
    io.github.jfrascon.robotics-dockers.user.home="/home/developer" \\
    io.github.jfrascon.robotics-dockers.user.primary-group.name="robotics" \\
    io.github.jfrascon.robotics-dockers.user.primary-group.id="22001"
USER developer
WORKDIR /home/developer
CMD ["bash"]
"""
    )
    unique = f'{os.getpid()}-{ubuntu_version}'
    base_tag = f'robotics-dockers-adapter-test:base-{unique}'
    adapted_tag = f'robotics-dockers-adapter-test:adapted-{unique}'
    try:
        base_build = subprocess.run(
            [
                'docker',
                'build',
                '--file',
                str(context / 'Dockerfile.adapter-test-base'),
                '--tag',
                base_tag,
                str(context),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert base_build.returncode == 0, base_build.stdout + base_build.stderr

        adapted_build = subprocess.run(
            [
                'docker',
                'build',
                '--file',
                str(context / 'Dockerfile_update_user'),
                '--build-arg',
                f'BASE_IMAGE={base_tag}',
                '--build-arg',
                'NEW_UID=23001',
                '--build-arg',
                'NEW_GID=24001',
                '--tag',
                adapted_tag,
                str(context),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert adapted_build.returncode == 0, adapted_build.stdout + adapted_build.stderr

        inspection = subprocess.run(
            ['docker', 'image', 'inspect', adapted_tag], capture_output=True, text=True, timeout=30, check=False
        )
        assert inspection.returncode == 0, inspection.stderr
        config = json.loads(inspection.stdout)[0]['Config']
        assert config['User'] == 'developer'
        assert config['WorkingDir'] == '/home/developer'
        assert 'ROBOTICS_DOCKERS_USER_ID=23001' in config['Env']
        assert 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=24001' in config['Env']
        assert config['Labels']['io.github.jfrascon.robotics-dockers.user.id'] == '23001'

        runtime = subprocess.run(
            [
                'docker',
                'run',
                '--rm',
                '--entrypoint',
                'bash',
                adapted_tag,
                '-lc',
                'printf \'%s:%s \' "$(id -u)" "$(id -g)"; stat --format \'%u:%g\' /home/developer/owned',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert runtime.returncode == 0, runtime.stdout + runtime.stderr
        assert runtime.stdout.strip() == '23001:24001 23001:24001'
    finally:
        subprocess.run(
            ['docker', 'image', 'rm', '--force', adapted_tag, base_tag],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def test_named_sudo_rule_is_user_specific_and_does_not_require_sudo_group(ubuntu_image: str) -> None:
    configure_sudo = Path(str(resources.files('robotics_dockers.resources').joinpath('configure_sudo.sh')))
    completed = _run(
        ubuntu_image,
        """
groupadd --gid 22001 testdevelopers
useradd --uid 21001 --gid 22001 --create-home --shell /bin/bash testdeveloper
mkdir -p /etc/sudoers.d /usr/local/sbin
printf '%s\n' '#!/bin/bash' \
    '[ "$1" = "--check" ] && [ "$2" = "--file" ]' \
    'grep -qxF "testdeveloper ALL=(ALL:ALL) NOPASSWD: ALL" "$3"' \
    >/usr/local/sbin/visudo
chmod 0755 /usr/local/sbin/visudo
bash /tmp/configure-sudo testdeveloper || exit 1
id -nG testdeveloper
cat /etc/sudoers.d/robotics-dockers-testdeveloper
stat --format 'RULE=%u:%g:%a' /etc/sudoers.d/robotics-dockers-testdeveloper
""",
        mounts=((configure_sudo, '/tmp/configure-sudo'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'testdevelopers' in completed.stdout
    assert 'sudo' not in completed.stdout.splitlines()[0].split()
    assert 'testdeveloper ALL=(ALL:ALL) NOPASSWD: ALL' in completed.stdout
    assert 'RULE=0:0:440' in completed.stdout


def test_install_pkgs_propagates_failure_from_its_single_apt_call(ubuntu_image: str) -> None:
    install_pkgs = Path(str(resources.files('robotics_dockers.resources').joinpath('install_pkgs')))
    completed = _run(
        ubuntu_image,
        """
mkdir -p /usr/local/bin
printf '%s\n' '#!/bin/bash' 'printf "%s\\n" "$*" >>/tmp/apt.calls' \
    'exit 1' >/usr/local/bin/apt-get
chmod 0755 /usr/local/bin/apt-get
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
bash /tmp/install-pkgs package-one package-two
rc=$?
printf 'RC=%s CALLS=%s\n' "$rc" "$(wc -l </tmp/apt.calls)"
cat /tmp/apt.calls
exit 0
""",
        mounts=((install_pkgs, '/tmp/install-pkgs'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'RC=0' not in completed.stdout
    assert 'CALLS=1' in completed.stdout
    assert 'package-one package-two' in completed.stdout


def test_extra_apt_refuses_to_overwrite_an_inherited_destination(ubuntu_image: str, tmp_path: Path) -> None:
    installer = Path(str(resources.files('robotics_dockers.resources').joinpath('install_extra_apt.sh')))
    extra_apt = tmp_path / 'apt'
    (extra_apt / 'keyrings.d').mkdir(parents=True)
    (extra_apt / 'sources.d').mkdir()
    (extra_apt / 'packages.txt').write_text('# No packages are needed for this collision test.\n')
    (extra_apt / 'keyrings.d/first.gpg').write_text('first\n')
    (extra_apt / 'keyrings.d/z-existing.gpg').write_text('collision\n')
    tmp_path.chmod(0o755)
    for directory in (extra_apt, extra_apt / 'keyrings.d', extra_apt / 'sources.d'):
        directory.chmod(0o755)
    for file_path in extra_apt.rglob('*'):
        if file_path.is_file():
            file_path.chmod(0o644)

    completed = _run(
        ubuntu_image,
        """
mkdir -p /etc/apt/keyrings
ln -s /tmp/nonexistent-inherited-keyring /etc/apt/keyrings/z-existing.gpg
bash /tmp/install-extra-apt /tmp/extra-apt
rc=$?
printf 'RC=%s FIRST_COPIED=%s TARGET=%s\n' \
    "$rc" \
    "$([ -e /etc/apt/keyrings/first.gpg ] && echo yes || echo no)" \
    "$(readlink /etc/apt/keyrings/z-existing.gpg)"
exit 0
""",
        mounts=((installer, '/tmp/install-extra-apt'), (extra_apt, '/tmp/extra-apt')),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'RC=0' not in completed.stdout
    # This test invokes the helper directly, so the earlier copy remains visible.
    # In the generated Dockerfile both operations belong to one RUN layer and a
    # failure discards that whole layer.
    assert 'FIRST_COPIED=yes' in completed.stdout
    assert 'TARGET=/tmp/nonexistent-inherited-keyring' in completed.stdout
    assert 'Refusing inherited symbolic link' in completed.stderr


def test_extra_apt_rejects_a_symbolic_link_inside_the_mounted_input(ubuntu_image: str, tmp_path: Path) -> None:
    installer = Path(str(resources.files('robotics_dockers.resources').joinpath('install_extra_apt.sh')))
    extra_apt = tmp_path / 'apt'
    extra_apt.mkdir()
    (extra_apt / 'keyrings.d').symlink_to('/etc/apt/keyrings')
    (extra_apt / 'sources.d').mkdir()
    (extra_apt / 'packages.txt').write_text('# No packages requested.\n')

    completed = _run(
        ubuntu_image,
        'bash /tmp/install-extra-apt /tmp/extra-apt',
        mounts=((installer, '/tmp/install-extra-apt'), (extra_apt, '/tmp/extra-apt')),
    )

    assert completed.returncode != 0
    assert "Required directory '/tmp/extra-apt/keyrings.d' must not be a symbolic link" in completed.stderr


def test_extra_apt_rejects_nested_or_unsupported_entries(ubuntu_image: str, tmp_path: Path) -> None:
    installer = Path(str(resources.files('robotics_dockers.resources').joinpath('install_extra_apt.sh')))
    extra_apt = tmp_path / 'apt'
    (extra_apt / 'keyrings.d/nested').mkdir(parents=True)
    (extra_apt / 'sources.d').mkdir()
    (extra_apt / 'packages.txt').write_text('# No packages are needed for this validation test.\n')
    tmp_path.chmod(0o755)
    for directory in (extra_apt, extra_apt / 'keyrings.d', extra_apt / 'keyrings.d/nested', extra_apt / 'sources.d'):
        directory.chmod(0o755)
    (extra_apt / 'packages.txt').chmod(0o644)

    completed = _run(
        ubuntu_image,
        """
bash /tmp/install-extra-apt /tmp/extra-apt
""",
        mounts=((installer, '/tmp/install-extra-apt'), (extra_apt, '/tmp/extra-apt')),
    )

    assert completed.returncode != 0
    assert 'only top-level regular .asc and .gpg files are accepted' in completed.stderr


def test_python_and_rust_user_hooks_run_in_sorted_order(ubuntu_image: str, tmp_path: Path) -> None:
    installer = Path(str(resources.files('robotics_dockers.resources').joinpath('install_user_extras.sh')))
    extras = tmp_path / 'extra'
    (extras / 'python/install.d').mkdir(parents=True)
    (extras / 'rust').mkdir()
    (extras / 'python/requirements.txt').write_text('# Empty functional-test requirements.\n')
    (extras / 'python/install.d/20-second.sh').write_text('printf "python-second\\n" >>/tmp/hooks-order\n')
    (extras / 'python/install.d/10-first.sh').write_text('printf "python-first\\n" >>/tmp/hooks-order\n')
    (extras / 'rust/install.sh').write_text('printf "rust\\n" >>/tmp/hooks-order\n')
    tmp_path.chmod(0o755)
    for directory in (extras, extras / 'python', extras / 'python/install.d', extras / 'rust'):
        directory.chmod(0o755)
    for file_path in extras.rglob('*'):
        if file_path.is_file():
            file_path.chmod(0o644)

    completed = _run(
        ubuntu_image,
        """
groupadd --gid 1001 jfr
useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash jfr
printf '%s\n' '#!/bin/bash' \
    'if [ "$*" = "-m pip install --help" ]; then echo --break-system-packages; fi' \
    'exit 0' >/usr/bin/python3
chmod 0755 /usr/bin/python3
runuser --user jfr -- env HOME=/home/jfr PATH=/usr/local/bin:/usr/bin:/bin \
    bash /tmp/installer /tmp/extra || exit 1
cat /tmp/hooks-order
""",
        mounts=((installer, '/tmp/installer'), (extras, '/tmp/extra')),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.splitlines()[-3:] == ['python-first', 'python-second', 'rust']


@pytest.mark.parametrize(
    'docker_user,configured_gid,expect_loaded',
    [('jfr', '1001', True), ('1001:0', '1001', False), ('root', '1001', False)],
)
def test_rendered_entrypoint_on_ubuntu_based_test_image(
    entrypoint_test_image: str, docker_user: str, configured_gid: str, expect_loaded: bool
) -> None:
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        docker_user,
        '--env',
        'ROBOTICS_DOCKERS_USER=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_ID=1001',
        '--env',
        'ROBOTICS_DOCKERS_USER_HOME=/home/jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=' + configured_gid,
        '--env',
        'USE_HOST_NVIDIA_DRIVER=false',
        '--group-add',
        '12345',
        '--entrypoint',
        '/usr/local/bin/entrypoint',
        entrypoint_test_image,
        'bash',
        '-c',
        'printf "IDENTITY=%s:%s LOADED=%s GROUPS=%s\\n" '
        '"$(id -u)" "$(id -g)" "${ROBOTICS_DOCKERS_ENV_LOADED:-}" "$(id -G)"',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert ('LOADED=1' in completed.stdout) is expect_loaded
    assert '12345' in completed.stdout


def test_entrypoint_validates_compose_xdg_runtime_directory(entrypoint_test_image: str) -> None:
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        'jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_ID=1001',
        '--env',
        'ROBOTICS_DOCKERS_USER_HOME=/home/jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1001',
        '--env',
        'XDG_RUNTIME_DIR=/run/user/1001',
        '--tmpfs',
        '/run/user/1001:rw,uid=1001,gid=1001,mode=700',
        '--entrypoint',
        '/usr/local/bin/entrypoint',
        entrypoint_test_image,
        'true',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'is valid' in completed.stdout


def test_entrypoint_rejects_invalid_xdg_runtime_mode(entrypoint_test_image: str) -> None:
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        'jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_ID=1001',
        '--env',
        'ROBOTICS_DOCKERS_USER_HOME=/home/jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1001',
        '--env',
        'XDG_RUNTIME_DIR=/run/user/1001',
        '--tmpfs',
        '/run/user/1001:rw,uid=1001,gid=1001,mode=755',
        '--entrypoint',
        '/usr/local/bin/entrypoint',
        entrypoint_test_image,
        'true',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    assert completed.returncode != 0
    assert "expected '700'" in completed.stderr


def test_entrypoint_reports_missing_nvidia_runtime_access(entrypoint_test_image: str) -> None:
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        'jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_ID=1001',
        '--env',
        'ROBOTICS_DOCKERS_USER_HOME=/home/jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1001',
        '--env',
        'USE_HOST_NVIDIA_DRIVER=true',
        '--entrypoint',
        '/usr/local/bin/entrypoint',
        entrypoint_test_image,
        'true',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    assert completed.returncode != 0
    assert 'expects the host NVIDIA driver' in completed.stderr


def test_entrypoint_does_not_change_a_mount_below_the_home(entrypoint_test_image: str, tmp_path: Path) -> None:
    mounted_directory = tmp_path / 'mounted'
    mounted_directory.mkdir()
    mounted_file = mounted_directory / 'host-owned'
    mounted_file.write_text('preserve ownership\n')
    tmp_path.chmod(0o755)
    mounted_directory.chmod(0o755)
    mounted_file.chmod(0o644)
    owner_before = (mounted_file.stat().st_uid, mounted_file.stat().st_gid)
    command = [
        'docker',
        'run',
        '--rm',
        '--user',
        'jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_ID=1001',
        '--env',
        'ROBOTICS_DOCKERS_USER_HOME=/home/jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=jfr',
        '--env',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1001',
        '--mount',
        f'type=bind,source={mounted_directory},target=/home/jfr/mounted',
        '--entrypoint',
        '/usr/local/bin/entrypoint',
        entrypoint_test_image,
        'test',
        '-r',
        '/home/jfr/mounted/host-owned',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (mounted_file.stat().st_uid, mounted_file.stat().st_gid) == owner_before


@pytest.mark.ros_image
def test_ros_core_image_matches_supported_ros_ubuntu_pair(ros_core_case: tuple[str, str, str]) -> None:
    ros_distro, expected_ubuntu_version, ros_image = ros_core_case
    ros_environment = Path(str(resources.files('robotics_dockers.resources').joinpath('ros.rc')))
    completed = _run(
        ros_image,
        f"""
. /etc/os-release || exit 1
[ "${{VERSION_ID}}" = "{expected_ubuntu_version}" ] || exit 1
[ -s /opt/ros/{ros_distro}/setup.bash ] || exit 1
mkdir -p /tmp/robotics-dockers-ros-home
export HOME=/tmp/robotics-dockers-ros-home
unset ROS_DISTRO CONTAINER_ROS_WORKSPACE
. /tmp/ros.rc || exit 1
printf 'DISTRO=%s VERSION=%s AMENT_PREFIX_PATH=%s\n' \
    "${{ROS_DISTRO}}" "${{VERSION_ID}}" "${{AMENT_PREFIX_PATH:-}}"
""",
        mounts=((ros_environment, '/tmp/ros.rc'),),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f'DISTRO={ros_distro}' in completed.stdout
    assert f'VERSION={expected_ubuntu_version}' in completed.stdout
    assert f'/opt/ros/{ros_distro}' in completed.stdout


@pytest.mark.full_build
def test_complete_generated_image_builds_for_selected_pair(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    # A complete build can download and install several gigabytes. It is not
    # enough to select the full_build marker: the caller must also name the one
    # ROS distribution to build. This prevents an ordinary pytest invocation,
    # and even a broad Docker test invocation, from starting this work.
    selected_ros_distro = request.config.getoption('--full-build-distro')
    if selected_ros_distro is None:
        pytest.skip('a complete build requires the explicit --full-build-distro option')

    if selected_ros_distro not in ROS_DISTROS:
        supported_distros = ', '.join(ROS_DISTROS)
        pytest.fail(f"Unknown ROS distribution '{selected_ros_distro}'; choose one of: {supported_distros}")

    ros_distro = selected_ros_distro
    ubuntu_version = ROS_DISTROS[ros_distro]
    ubuntu_image = f'ubuntu:{ubuntu_version}'
    _ensure_docker_image(ubuntu_image)

    image_tag = f'robotics-dockers-full-build:{ros_distro}-{os.getpid()}'
    context = tmp_path / f'full-build-{ros_distro}'
    result = generate_docker_context(DockerContextConfig(ros_distro=ros_distro, img_id=image_tag, output_dir=context))

    try:
        build = subprocess.run(
            [
                sys.executable,
                str(result.context_dir / 'build.py'),
                'developer',
                '21001',
                '22001',
                '--group',
                'robotics',
            ],
            cwd=result.context_dir,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        assert build.returncode == 0, build.stdout + build.stderr

        runtime = subprocess.run(
            ['docker', 'run', '--rm', image_tag, 'bash', '-lc', 'printf "%s:%s\n" "$(id -u)" "$(id -g)"'],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert runtime.returncode == 0, runtime.stdout + runtime.stderr
        assert runtime.stdout.rstrip().endswith('21001:22001')
    finally:
        subprocess.run(
            ['docker', 'image', 'rm', '--force', image_tag], capture_output=True, text=True, timeout=60, check=False
        )
