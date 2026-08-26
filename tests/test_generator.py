import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from robotics_dockers import DockerContextConfig, generate_docker_context
from robotics_dockers.config import ROS_DISTROS, resolve_config
from robotics_dockers.errors import (
    InvalidDockerImageNameError,
    InvalidOutputDirectoryError,
    InvalidRosdepPackagesDirError,
    InvalidRosDistroError,
)


def _config(output_dir: Path | None = None, **overrides: object) -> DockerContextConfig:
    values: dict[str, object] = {'ros_distro': 'jazzy', 'img_id': 'local/ros-test:latest', 'output_dir': output_dir}
    values.update(overrides)
    return DockerContextConfig(**values)  # type: ignore[arg-type]


def test_generate_docker_context_creates_new_contract_only(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))

    expected = (
        'Dockerfile',
        'Dockerfile_update_user',
        'build.py',
        'compose_files/.gitkeep',
        'robotics_dockers_user_env.py',
        'env_files/.gitkeep',
        '.resources/configure_image_user.sh',
        '.resources/configure_sudo.sh',
        '.resources/deduplicate_path',
        '.resources/entrypoint_user.sh',
        '.resources/update_image_user.sh',
        '.resources/extra.d/apt/packages.txt',
        '.resources/extra.d/python/requirements.txt',
        '.resources/extra.d/rust/install.sh.example',
        '.resources/user_preparation.d/01-delete-ubuntu-user.sh.example',
        '.resources/user_preparation.d/02-reuse-ubuntu-user.sh.example',
    )
    for relative_path in expected:
        assert result.context_dir.joinpath(relative_path).exists(), relative_path

    assert result.context_dir.joinpath('.resources/deduplicate_path').stat().st_mode & 0o111 == 0o111

    assert not result.context_dir.joinpath('compose_files/docker-compose.yaml').exists()

    assert not result.context_dir.joinpath('.resources/entrypoint_root.sh').exists()
    assert not result.context_dir.joinpath('.resources/entrypoint_root.d').exists()
    generated_text = '\n'.join(
        path.read_text(errors='ignore') for path in result.context_dir.rglob('*') if path.is_file()
    )
    for obsolete_value in ('IMAGE_MAIN_USER', 'HOST_UID', 'HOST_UPGID', 'RESOURCES_CHECKSUM'):
        assert obsolete_value not in generated_text


def test_generated_identity_sources_are_general_and_metadata_is_concrete(tmp_path: Path) -> None:
    result = generate_docker_context(
        _config(
            tmp_path,
            add_compose_file=True,
            meta_title='Custom image',
            meta_desc='Custom description',
            meta_authors='Custom Author',
        )
    )
    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    adapter = result.context_dir.joinpath('Dockerfile_update_user').read_text()
    compose = result.context_dir.joinpath('compose_files/docker-compose.yaml').read_text()
    compose_config = yaml.safe_load(compose)
    service = next(iter(compose_config['services'].values()))
    x11_socket_mount = next(
        mount for mount in service['volumes'] if isinstance(mount, dict) and mount.get('source') == '/tmp/.X11-unix'
    )
    usb_bus_mount = next(
        mount for mount in service['volumes'] if isinstance(mount, dict) and mount.get('source') == '/dev/bus/usb'
    )

    assert 'ARG ROBOTICS_DOCKERS_USER_ID' in dockerfile
    assert 'ROBOTICS_DOCKERS_USER_ID="${ROBOTICS_DOCKERS_USER_ID}"' in dockerfile
    assert 'io.github.jfrascon.robotics-dockers.user.name="${ROBOTICS_DOCKERS_USER}"' in dockerfile
    assert 'org.opencontainers.image.title="Custom image"' in dockerfile
    assert 'USER "${ROBOTICS_DOCKERS_USER}"' in dockerfile
    assert 'WORKDIR "${ROBOTICS_DOCKERS_USER_HOME}"' in dockerfile
    assert 'USER "${ROBOTICS_DOCKERS_USER}"' in adapter
    assert 'WORKDIR "${ROBOTICS_DOCKERS_USER_HOME}"' in adapter
    assert '"${NEW_UID}" "${NEW_GID}" "${ROBOTICS_DOCKERS_USER}" "${ROBOTICS_DOCKERS_USER_HOME}"' in adapter

    assert 'user:' not in compose
    assert '${IMAGE_USER_ID:?' in compose
    assert '${IMAGE_USER_PRIMARY_GROUP_ID:?' in compose
    assert 'working_dir:' not in compose
    assert 'CONTAINER_ROS_WORKSPACE: "/workspace"' in compose
    assert '#   source: "${HOST_WORKSPACE:' in compose
    assert '#- ~/datasets:/datasets' in compose
    assert '# cap_add:' in compose
    assert '#   - NET_ADMIN' in compose
    assert '# security_opt:' in compose
    assert '#   - seccomp=unconfined' in compose
    assert '#   - apparmor=unconfined' in compose
    assert '# ulimits:' in compose
    assert '#   rtprio:' in compose
    assert '#   memlock:' in compose
    assert service['stop_grace_period'] == '30s'
    for capability in ('SYS_ADMIN', 'SYS_PTRACE', 'PERFMON', 'SYS_NICE', 'IPC_LOCK', 'NET_ADMIN'):
        assert f'#   - {capability}' in compose
    for default_capability in ('SYS_CHROOT', 'SETUID', 'SETGID', 'NET_RAW'):
        assert f'#   - {default_capability}' not in compose
    assert '#     source: "${SSH_AUTH_SOCK:' in compose
    assert '#     target: /ssh-agent' in compose
    assert '#     read_only: true' in compose
    assert '${HOST_UID' not in compose
    assert '${HOST_UPGID' not in compose
    assert x11_socket_mount == {
        'type': 'bind',
        'source': '/tmp/.X11-unix',
        'target': '/tmp/.X11-unix',
        'read_only': True,
        'bind': {'create_host_path': False},
    }
    assert usb_bus_mount == {
        'type': 'bind',
        'source': '/dev/bus/usb',
        'target': '/dev/bus/usb',
        'bind': {'create_host_path': False},
    }
    assert service['devices'] == ['/dev/dri:/dev/dri:rw']
    assert service['device_cgroup_rules'] == ['c 189:* rw']
    assert '#   source: /dev/input' in compose
    assert '# - "c 13:* r"' in compose
    for concrete_identity in ('"developer"', '"/home/developer"', '"1000"', '"1001"'):
        assert concrete_identity not in dockerfile
        assert concrete_identity not in adapter
        assert concrete_identity not in compose


def test_generated_build_script_uses_cache_by_default_and_only_adds_created_label(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))
    build_script = result.context_dir.joinpath('build.py').read_text()

    # Image-name validation belongs to config.py during generation. The real
    # docker build command remains responsible for validating its own argv.
    assert 'is_valid_docker_img_name' not in build_script
    assert 'except OSError as error:' in build_script
    assert 'except Exception' not in build_script

    args_file = tmp_path / 'docker_args.json'
    fake_bin_dir = tmp_path / 'fake-bin'
    fake_bin_dir.mkdir()
    fake_docker = fake_bin_dir / 'docker'
    fake_docker.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ['DOCKER_ARGS_FILE'], 'a', encoding='utf-8') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['image', 'inspect']:
    with open(os.environ['DOCKER_ARGS_FILE'], encoding='utf-8') as recorded:
        build_args = json.loads(recorded.readline())
    image_environment = [
        build_args[index + 1] for index, value in enumerate(build_args) if value == '--build-arg'
    ]
    if os.environ.get('DOCKER_IDENTITY_MISMATCH'):
        image_environment[0] = 'ROBOTICS_DOCKERS_USER=another_user'
        image_environment[2] = 'ROBOTICS_DOCKERS_USER_HOME=/home/another_user'
    print(json.dumps([{'Config': {'Env': image_environment}}]))
"""
    )
    fake_docker.chmod(0o755)
    environment = os.environ.copy()
    environment['DOCKER_ARGS_FILE'] = str(args_file)
    environment['PATH'] = f'{fake_bin_dir}:{environment["PATH"]}'

    default_build = subprocess.run(
        [str(result.context_dir / 'build.py'), 'developer', '01000', '01001'],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert default_build.returncode == 0, default_build.stdout + default_build.stderr
    default_calls = [json.loads(line) for line in args_file.read_text().splitlines()]
    default_args = default_calls[0]
    assert '--no-cache' not in default_args
    assert '--pull' not in default_args
    build_arguments = [default_args[index + 1] for index, value in enumerate(default_args) if value == '--build-arg']
    assert build_arguments == [
        'ROBOTICS_DOCKERS_USER=developer',
        'ROBOTICS_DOCKERS_USER_ID=1000',
        'ROBOTICS_DOCKERS_USER_HOME=/home/developer',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=developer',
        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=1001',
    ]
    labels = [default_args[index + 1] for index, value in enumerate(default_args) if value == '--label']
    assert len(labels) == 1
    assert labels[0].startswith('org.opencontainers.image.created=')
    assert default_calls[1] == ['image', 'inspect', 'local/ros-test:latest']
    assert not tmp_path.joinpath('.env').exists()

    args_file.write_text('')
    no_cache_build = subprocess.run(
        [
            str(result.context_dir / 'build.py'),
            'developer',
            '1000',
            '1001',
            '--group',
            'robotics',
            '--no-cache',
            '--pull',
        ],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert no_cache_build.returncode == 0
    no_cache_args = json.loads(args_file.read_text().splitlines()[0])
    assert '--no-cache' in no_cache_args
    assert '--pull' in no_cache_args
    assert 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=robotics' in no_cache_args

    args_file.write_text('')
    environment['DOCKER_IDENTITY_MISMATCH'] = '1'
    mismatched_build = subprocess.run(
        [str(result.context_dir / 'build.py'), 'developer', '1000', '1001'],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert mismatched_build.returncode == 1
    assert 'the build requested developer:developer' in mismatched_build.stderr
    assert not tmp_path.joinpath('.env').exists()


def test_optional_standalone_compose_keeps_workspace_mount_commented(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path, add_compose_file=True))
    compose = result.context_dir.joinpath('compose_files/docker-compose.yaml').read_text()
    compose_config = yaml.safe_load(compose)
    service = next(iter(compose_config['services'].values()))

    assert not any(
        isinstance(mount, dict) and mount.get('source', '').startswith('${HOST_WORKSPACE:')
        for mount in service['volumes']
    )
    assert '#   source: "${HOST_WORKSPACE:' in compose


def test_generated_build_script_propagates_launch_failure(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))
    environment = os.environ.copy()
    environment['PATH'] = str(tmp_path / 'missing-bin')

    completed = subprocess.run(
        [sys.executable, str(result.context_dir / 'build.py'), 'developer', '1000', '1000'],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 1


def test_generated_build_script_does_not_hide_unexpected_exceptions(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))
    fake_bin_dir = tmp_path / 'fake-bin'
    fake_bin_dir.mkdir()
    fake_docker = fake_bin_dir / 'docker'
    fake_docker.write_text('#!/bin/sh\nexit 0\n')
    fake_docker.chmod(0o755)

    # Replace the generated helper with a small test double. The build succeeds,
    # but inspecting its identity raises an unexpected programming error. That
    # error must retain its traceback instead of becoming a successful exit.
    result.context_dir.joinpath('robotics_dockers_user_env.py').write_text(
        """class UserEnvError(Exception):
    pass


class ImageUserInfo:
    def __init__(self, **values):
        self.__dict__.update(values)


def validate_account_name(value, label):
    return value


def validate_numeric_id(value, label):
    return int(value)


def inspect_image_user(image):
    raise RuntimeError('deliberate unexpected inspection failure')
"""
    )
    environment = os.environ.copy()
    environment['PATH'] = f'{fake_bin_dir}:{environment["PATH"]}'

    completed = subprocess.run(
        [str(result.context_dir / 'build.py'), 'developer', '1000', '1000'],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    assert 'Traceback (most recent call last)' in completed.stderr
    assert 'RuntimeError: deliberate unexpected inspection failure' in completed.stderr


def test_generate_docker_context_uses_named_temporary_output_dir() -> None:
    result = generate_docker_context(_config())
    try:
        assert result.context_dir.parent == Path('/tmp')
        assert result.context_dir.name.startswith('robotics_dockers_')
    finally:
        shutil.rmtree(result.context_dir)


def test_generate_docker_context_refuses_to_overwrite_existing_content(tmp_path: Path) -> None:
    tmp_path.joinpath('keep.txt').write_text('user content\n')

    with pytest.raises(InvalidOutputDirectoryError):
        generate_docker_context(_config(tmp_path))

    assert tmp_path.joinpath('keep.txt').read_text() == 'user content\n'


@pytest.mark.parametrize('ros_distro,expected_ubuntu', ROS_DISTROS.items())
def test_generate_docker_context_sets_distro_defaults(tmp_path: Path, ros_distro: str, expected_ubuntu: str) -> None:
    result = generate_docker_context(_config(tmp_path, ros_distro=ros_distro))
    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()

    if ros_distro == 'humble':
        expected = 'ROS_LOCALHOST_ONLY="1"'
        unexpected = 'ROS_AUTOMATIC_DISCOVERY_RANGE='
    else:
        expected = 'ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"'
        unexpected = 'ROS_LOCALHOST_ONLY='

    assert f'FROM ubuntu:{expected_ubuntu}' in dockerfile
    assert expected in dockerfile
    assert unexpected not in dockerfile


def test_generate_docker_context_installs_mesa_only_without_nvidia(tmp_path: Path) -> None:
    no_nvidia = generate_docker_context(_config(tmp_path / 'mesa'))
    nvidia = generate_docker_context(_config(tmp_path / 'nvidia', use_host_nvidia_driver=True))

    assert no_nvidia.context_dir.joinpath('.resources/install_mesa_packages.sh').exists()
    assert not nvidia.context_dir.joinpath('.resources/install_mesa_packages.sh').exists()
    assert 'USE_HOST_NVIDIA_DRIVER="true"' in nvidia.context_dir.joinpath('Dockerfile').read_text()


@pytest.mark.parametrize(
    'configured,mode,rendered_fragment',
    [
        (None, 'cli', "'--pkgs-dir'"),
        ('../src', 'fixed_relative', 'context_dir.joinpath("../src").resolve()'),
        ('~/workspace/src', 'fixed_host_path', 'Path("~/workspace/src").expanduser().resolve()'),
    ],
)
def test_rosdep_package_directory_modes(
    tmp_path: Path, configured: str | None, mode: str, rendered_fragment: str
) -> None:
    config = _config(tmp_path, rosdep_packages_dir=configured)
    result = generate_docker_context(config)

    assert resolve_config(config).rosdep_packages_dir_mode == mode
    assert rendered_fragment in result.context_dir.joinpath('build.py').read_text()


def test_generated_python_escapes_configured_paths_and_metadata(tmp_path: Path) -> None:
    """User-provided text must remain data instead of becoming generated Python syntax."""
    result = generate_docker_context(
        _config(
            tmp_path,
            rosdep_packages_dir="directory'with\na-newline",
            meta_title='A "quoted" title\nwith a second line',
            meta_desc="Description with 'apostrophes'",
            meta_authors='First Author\nSecond Author',
        )
    )

    build_script = result.context_dir.joinpath('build.py').read_text()
    compile(build_script, str(result.context_dir.joinpath('build.py')), 'exec')

    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    assert 'org.opencontainers.image.title="A \\"quoted\\" title\\nwith a second line"' in dockerfile
    assert 'org.opencontainers.image.description="Description with \\u0027apostrophes\\u0027"' in dockerfile
    assert 'org.opencontainers.image.authors="First Author\\nSecond Author"' in dockerfile


@pytest.mark.parametrize(
    'overrides,error_type',
    [
        ({'ros_distro': 'invalid'}, InvalidRosDistroError),
        ({'img_id': 'Invalid/Image:latest'}, InvalidDockerImageNameError),
        ({'img_id': 'local/image:-invalid'}, InvalidDockerImageNameError),
        ({'img_id': 'local/image:.invalid'}, InvalidDockerImageNameError),
        ({'img_id': '-invalid.example/image:latest'}, InvalidDockerImageNameError),
        ({'img_id': 'invalid-.example/image:latest'}, InvalidDockerImageNameError),
        ({'img_id': f'local/image:{"a" * 129}'}, InvalidDockerImageNameError),
        ({'rosdep_packages_dir': ' '}, InvalidRosdepPackagesDirError),
    ],
)
def test_resolve_config_rejects_invalid_values(overrides: dict[str, object], error_type: type[Exception]) -> None:
    with pytest.raises(error_type):
        resolve_config(_config(**overrides))
