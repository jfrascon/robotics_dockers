import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from robotics_dockers import DockerContextConfig, generate_docker_context
from robotics_dockers.config import ROS_DISTROS, resolve_config
from robotics_dockers.errors import (
    InvalidDockerImageNameError,
    InvalidImageIdentityError,
    InvalidOutputDirectoryError,
    InvalidRosdepPackagesDirError,
    InvalidRosDistroError,
)


def _config(output_dir: Path | None = None, **overrides: object) -> DockerContextConfig:
    values: dict[str, object] = {
        'ros_distro': 'jazzy',
        'img_id': 'local/ros-test:latest',
        'user': 'developer',
        'user_id': 1000,
        'primary_group_id': 1001,
        'output_dir': output_dir,
    }
    values.update(overrides)
    return DockerContextConfig(**values)  # type: ignore[arg-type]


def test_generate_docker_context_creates_new_contract_only(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))

    # Generation resolves and validates the input once, then returns that exact
    # normalized configuration for callers that need to present it.
    assert result.resolved_config.user == 'developer'
    assert result.resolved_config.primary_group == 'developer'
    assert result.resolved_config.user_id == 1000
    assert result.resolved_config.primary_group_id == 1001

    expected = (
        'Dockerfile',
        'Dockerfile.update-user',
        'build.py',
        'docker-compose-dev.yaml',
        '.resources/configure_image_user.sh',
        '.resources/configure_sudo.sh',
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

    assert not result.context_dir.joinpath('.resources/entrypoint_root.sh').exists()
    assert not result.context_dir.joinpath('.resources/entrypoint_root.d').exists()
    generated_text = '\n'.join(
        path.read_text(errors='ignore') for path in result.context_dir.rglob('*') if path.is_file()
    )
    for obsolete_value in ('IMAGE_MAIN_USER', 'HOST_UID', 'HOST_UPGID', 'RESOURCES_CHECKSUM'):
        assert obsolete_value not in generated_text


def test_generated_identity_compose_and_metadata_are_concrete(tmp_path: Path) -> None:
    result = generate_docker_context(
        _config(
            tmp_path,
            primary_group='robotics',
            meta_title='Custom image',
            meta_desc='Custom description',
            meta_authors='Custom Author',
        )
    )
    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    adapter = result.context_dir.joinpath('Dockerfile.update-user').read_text()
    compose = result.context_dir.joinpath('docker-compose-dev.yaml').read_text()

    assert 'ROBOTICS_DOCKERS_USER="developer"' in dockerfile
    assert 'ROBOTICS_DOCKERS_USER_ID="1000"' in dockerfile
    assert 'ROBOTICS_DOCKERS_USER_HOME="/home/developer"' in dockerfile
    assert 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP="robotics"' in dockerfile
    assert 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID="1001"' in dockerfile
    assert 'io.github.jfrascon.robotics-dockers.user.name="developer"' in dockerfile
    assert 'org.opencontainers.image.title="Custom image"' in dockerfile
    assert 'USER "${ROBOTICS_DOCKERS_USER}"' in dockerfile
    assert 'WORKDIR "${ROBOTICS_DOCKERS_USER_HOME}"' in dockerfile
    assert 'USER "developer"' in adapter
    assert 'WORKDIR "/home/developer"' in adapter
    assert '"${NEW_UID}" "${NEW_GID}" "developer" "/home/developer"' in adapter

    assert 'user:' not in compose
    assert '/run/user/1000:mode=700,uid=1000,gid=1001' in compose
    assert 'XDG_RUNTIME_DIR: "/run/user/1000"' in compose
    assert 'CONTAINER_ROS_WORKSPACE: "/workspace"' in compose
    assert '${HOST_ROS_WORKSPACE:' in compose
    assert '#- ~/datasets:/datasets' in compose
    assert '# cap_add:' in compose
    assert '#   - NET_ADMIN' in compose
    assert '${HOST_UID' not in compose
    assert '${HOST_UPGID' not in compose


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
with open(os.environ['DOCKER_ARGS_FILE'], 'w', encoding='utf-8') as output:
    json.dump(sys.argv[1:], output)
"""
    )
    fake_docker.chmod(0o755)
    environment = os.environ.copy()
    environment['DOCKER_ARGS_FILE'] = str(args_file)
    environment['PATH'] = f'{fake_bin_dir}:{environment["PATH"]}'

    default_build = subprocess.run(
        [str(result.context_dir / 'build.py')], cwd=result.context_dir, env=environment, text=True, capture_output=True
    )
    assert default_build.returncode == 0, default_build.stdout + default_build.stderr
    default_args = json.loads(args_file.read_text())
    assert '--no-cache' not in default_args
    assert '--pull' not in default_args
    assert '--build-arg' not in default_args
    labels = [default_args[index + 1] for index, value in enumerate(default_args) if value == '--label']
    assert len(labels) == 1
    assert labels[0].startswith('org.opencontainers.image.created=')

    no_cache_build = subprocess.run(
        [str(result.context_dir / 'build.py'), '--no-cache', '--pull'],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert no_cache_build.returncode == 0
    no_cache_args = json.loads(args_file.read_text())
    assert '--no-cache' in no_cache_args
    assert '--pull' in no_cache_args


def test_generated_build_script_propagates_launch_failure(tmp_path: Path) -> None:
    result = generate_docker_context(_config(tmp_path))
    environment = os.environ.copy()
    environment['PATH'] = str(tmp_path / 'missing-bin')

    completed = subprocess.run(
        [sys.executable, str(result.context_dir / 'build.py')],
        cwd=result.context_dir,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 1


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
        ({'user': 'InvalidUser'}, InvalidImageIdentityError),
        ({'primary_group': 'bad.group'}, InvalidImageIdentityError),
        ({'user_id': 999}, InvalidImageIdentityError),
        ({'primary_group_id': 4294967295}, InvalidImageIdentityError),
        ({'rosdep_packages_dir': ' '}, InvalidRosdepPackagesDirError),
    ],
)
def test_resolve_config_rejects_invalid_values(overrides: dict[str, object], error_type: type[Exception]) -> None:
    with pytest.raises(error_type):
        resolve_config(_config(**overrides))


def test_resolve_config_normalizes_ids_and_defaults_group() -> None:
    resolved = resolve_config(_config(user_id='00001000', primary_group_id='00001001'))

    assert resolved.user_id == 1000
    assert resolved.primary_group_id == 1001
    assert resolved.primary_group == 'developer'
    assert resolved.user_home == '/home/developer'
