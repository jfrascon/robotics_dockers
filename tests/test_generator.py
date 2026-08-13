import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from robotics_dockers import DockerContextConfig, generate_docker_context
from robotics_dockers.config import resolve_config
from robotics_dockers.errors import (
    InvalidDockerImageNameError,
    InvalidImageUserError,
    InvalidRosdepPackagesDirError,
    InvalidRosDistroError,
)


def _compute_directory_checksum(path: Path) -> str:
    digest = hashlib.sha256()

    for file_path in sorted(item for item in path.rglob('*') if item.is_file()):
        relative_path = file_path.relative_to(path).as_posix()
        digest.update(relative_path.encode())
        digest.update(b'\0')
        digest.update(file_path.read_bytes())
        digest.update(b'\0')

    return digest.hexdigest()


def test_generate_docker_context_creates_expected_files(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    assert result.context_dir == tmp_path.resolve()
    assert tmp_path.joinpath('Dockerfile').is_file()
    assert tmp_path.joinpath('build.py').is_file()
    assert tmp_path.joinpath('docker-compose-dev.yaml').is_file()
    assert tmp_path.joinpath('.resources', 'rosdep_skip_keys.txt').is_file()
    assert tmp_path.joinpath('.resources', 'entrypoint_root.sh').is_file()
    assert tmp_path.joinpath('.resources', 'entrypoint_root.d').is_dir()


def test_generate_docker_context_requires_non_empty_runtime_ids(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    compose = result.context_dir.joinpath('docker-compose-dev.yaml').read_text()

    assert '${HOST_UID:?HOST_UID must be set}' in compose
    assert '${HOST_UPGID:?HOST_UPGID must be set}' in compose
    assert '${HOST_ROS_WORKSPACE:?Set HOST_ROS_WORKSPACE to the path of your ROS workspace on the host}' in compose
    assert '${HOST_XAUTHORITY_FILE:?HOST_XAUTHORITY_FILE must be set}' in compose
    assert '${RENDER_GID:?RENDER_GID must be set}' in compose
    assert 'source: "${HOST_XAUTHORITY_FILE:?HOST_XAUTHORITY_FILE must be set}"' in compose
    assert (
        '- ${HOST_ROS_WORKSPACE:?Set HOST_ROS_WORKSPACE to the path of your ROS workspace on the host}'
        ':/home/developer/workspace' in compose
    )
    assert 'target: "/run/user/${HOST_UID:?HOST_UID must be set}/docker-xwayland.xauth"' in compose
    assert 'XDG_RUNTIME_DIR: "/run/user/${HOST_UID:?HOST_UID must be set}"' in compose
    assert 'XAUTHORITY: "/run/user/${HOST_UID:?HOST_UID must be set}/docker-xwayland.xauth"' in compose
    assert 'CONTAINER_ROS_WORKSPACE: "/home/developer/workspace"' in compose
    assert (
        '- /run/user/${HOST_UID:?HOST_UID must be set}:mode=700,'
        'uid=${HOST_UID:?HOST_UID must be set},gid=${HOST_UPGID:?HOST_UPGID must be set}' in compose
    )
    assert '${HOST_UID?HOST_UID must be set}' not in compose
    assert '${HOST_UPGID?HOST_UPGID must be set}' not in compose
    assert '${HOST_XAUTHORITY_FILE?HOST_XAUTHORITY_FILE must be set}' not in compose
    assert '${RENDER_GID?RENDER_GID must be set}' not in compose


def test_generate_docker_context_uses_named_temporary_output_dir() -> None:
    result = generate_docker_context(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest'))

    try:
        assert result.context_dir.parent == Path('/tmp')
        assert result.context_dir.name.startswith('robotics_dockers_')
    finally:
        shutil.rmtree(result.context_dir)


def test_generate_docker_context_uses_ubuntu_default_for_ros_distro(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    assert 'FROM ubuntu:24.04' in dockerfile


def test_generate_docker_context_uses_rosdep_skip_keys_file(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    assert '.resources/rosdep_skip_keys.txt' in dockerfile
    assert 'skip_rosdep_keys /tmp/context/.resources/rosdep_skip_keys.txt' in dockerfile


def test_generated_build_script_passes_resources_checksum_to_docker(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    args_file = tmp_path / 'docker_args.json'
    fake_bin_dir = tmp_path / 'fake-bin'
    fake_bin_dir.mkdir()
    fake_docker = fake_bin_dir / 'docker'
    fake_docker.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ['DOCKER_ARGS_FILE'], 'w', encoding='utf-8') as args_file:
    json.dump(sys.argv[1:], args_file)
"""
    )
    fake_docker.chmod(0o775)

    expected_checksum = _compute_directory_checksum(result.context_dir / '.resources')
    env = os.environ.copy()
    env['DOCKER_ARGS_FILE'] = str(args_file)
    env['PATH'] = f'{fake_bin_dir}:{env["PATH"]}'

    completed_process = subprocess.run(
        [str(result.context_dir / 'build.py'), '--cache'],
        cwd=result.context_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed_process.returncode == 0, completed_process.stdout + completed_process.stderr
    docker_args = json.loads(args_file.read_text())
    build_arg_index = docker_args.index('--build-arg')
    assert docker_args[build_arg_index + 1] == f'RESOURCES_CHECKSUM={expected_checksum}'
    assert f'Resources checksum: {expected_checksum}' in completed_process.stdout


def test_generate_docker_context_keeps_apt_packages_template_comments(tmp_path: Path) -> None:
    generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    apt_packages = tmp_path.joinpath('.resources', 'extra.d', 'apt_packages.sh').read_text()
    assert 'The command `install_pkgs` is available in this script.' in apt_packages


def test_generate_docker_context_installs_mesa_only_without_nvidia(tmp_path: Path) -> None:
    no_nvidia_dir = tmp_path / 'no_nvidia'
    nvidia_dir = tmp_path / 'nvidia'

    generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=no_nvidia_dir,
            use_host_nvidia_driver=False,
        )
    )
    generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=nvidia_dir,
            use_host_nvidia_driver=True,
        )
    )

    no_nvidia_dockerfile = no_nvidia_dir.joinpath('Dockerfile').read_text()
    nvidia_dockerfile = nvidia_dir.joinpath('Dockerfile').read_text()

    assert 'install_mesa_packages.sh' in no_nvidia_dockerfile
    assert 'install_mesa_packages.sh' not in nvidia_dockerfile


def test_generate_docker_context_sets_ros_discovery_environment_by_distro(tmp_path: Path) -> None:
    humble_dir = tmp_path / 'humble'
    jazzy_dir = tmp_path / 'jazzy'

    generate_docker_context(DockerContextConfig('developer', 'humble', 'local/ros-humble:latest', humble_dir))
    generate_docker_context(DockerContextConfig('developer', 'jazzy', 'local/ros-jazzy:latest', jazzy_dir))

    humble_dockerfile = humble_dir.joinpath('Dockerfile').read_text()
    jazzy_dockerfile = jazzy_dir.joinpath('Dockerfile').read_text()

    assert 'ROS_LOCALHOST_ONLY="1"' in humble_dockerfile
    assert 'ROS_AUTOMATIC_DISCOVERY_RANGE=' not in humble_dockerfile
    assert 'ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST"' in jazzy_dockerfile
    assert 'ROS_LOCALHOST_ONLY=' not in jazzy_dockerfile


def test_generate_docker_context_uses_nvidia_check_only_when_requested(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path,
            use_host_nvidia_driver=True,
        )
    )

    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    entrypoint = result.context_dir.joinpath('.resources', 'entrypoint_root.sh').read_text()

    assert 'USE_HOST_NVIDIA_DRIVER="true"' in dockerfile
    assert 'nvidia_gpu_driver_check()' in entrypoint


def test_generate_docker_context_sets_xdg_environment_in_entrypoint(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer', ros_distro='jazzy', img_id='local/ros-test:latest', output_dir=tmp_path
        )
    )

    entrypoint = result.context_dir.joinpath('.resources', 'entrypoint_root.sh').read_text()

    assert 'default_xdg_runtime_dir="/run/user/${HOST_UID}"' in entrypoint
    assert 'xdg_runtime_dir="${XDG_RUNTIME_DIR:-${default_xdg_runtime_dir}}"' in entrypoint
    assert 'XDG_RUNTIME_DIR="${xdg_runtime_dir}"' in entrypoint
    assert 'XDG_CACHE_HOME=' not in entrypoint
    assert 'XDG_CONFIG_HOME=' not in entrypoint
    assert 'XDG_DATA_HOME=' not in entrypoint
    assert 'XDG_STATE_HOME=' not in entrypoint
    assert '"${image_main_user_home}/.entrypoint.sh"' in entrypoint


def test_generate_docker_context_uses_configured_image_metadata(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path,
            meta_title='Custom ROS 2 image',
            meta_desc='Custom description',
            meta_authors='Custom Author',
        )
    )

    dockerfile = result.context_dir.joinpath('Dockerfile').read_text()
    assert 'org.opencontainers.image.title="Custom ROS 2 image"' in dockerfile
    assert 'org.opencontainers.image.description="Custom description"' in dockerfile
    assert 'org.opencontainers.image.authors="Custom Author"' in dockerfile


def test_generate_docker_context_exposes_rosdep_packages_dir_option_by_default(tmp_path: Path) -> None:
    result = generate_docker_context(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest', tmp_path))

    build_script = result.context_dir.joinpath('build.py').read_text()
    assert "'--pkgs-dir'" in build_script
    assert 'if args.pkgs_dir:' in build_script
    assert build_script.endswith('\n')
    resolved_config = resolve_config(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest'))
    assert resolved_config.rosdep_packages_dir_mode == 'cli'


def test_generate_docker_context_can_use_fixed_rosdep_packages_dir(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path,
            rosdep_packages_dir='../src',
        )
    )

    build_script = result.context_dir.joinpath('build.py').read_text()
    assert "'--pkgs-dir'" not in build_script
    assert 'configured_pkgs_dir' not in build_script
    assert 'is_absolute()' not in build_script
    assert "pkgs_dir = context_dir.joinpath('../src').resolve()" in build_script
    assert 'args.pkgs_dir' not in build_script
    assert (
        resolve_config(
            DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest', rosdep_packages_dir='../src')
        ).rosdep_packages_dir_mode
        == 'fixed_relative'
    )


def test_generate_docker_context_can_use_fixed_host_rosdep_packages_dir(tmp_path: Path) -> None:
    fixed_pkgs_dir = tmp_path / 'src'
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path / 'docker',
            rosdep_packages_dir=fixed_pkgs_dir,
        )
    )

    build_script = result.context_dir.joinpath('build.py').read_text()
    assert "'--pkgs-dir'" not in build_script
    assert 'configured_pkgs_dir' not in build_script
    assert f"pkgs_dir = Path('{fixed_pkgs_dir}').expanduser().resolve()" in build_script
    assert 'args.pkgs_dir' not in build_script
    assert (
        resolve_config(
            DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest', rosdep_packages_dir=fixed_pkgs_dir)
        ).rosdep_packages_dir_mode
        == 'fixed_host_path'
    )


def test_generate_docker_context_treats_user_relative_rosdep_packages_dir_as_host_path(tmp_path: Path) -> None:
    result = generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path,
            rosdep_packages_dir='~/workspace/src',
        )
    )

    build_script = result.context_dir.joinpath('build.py').read_text()
    assert "pkgs_dir = Path('~/workspace/src').expanduser().resolve()" in build_script
    assert 'args.pkgs_dir' not in build_script
    assert (
        resolve_config(
            DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest', rosdep_packages_dir='~/workspace/src')
        ).rosdep_packages_dir_mode
        == 'fixed_host_path'
    )


def test_resolve_config_rejects_invalid_ros_distro() -> None:
    with pytest.raises(InvalidRosDistroError):
        resolve_config(DockerContextConfig('developer', 'invalid', 'local/ros-test:latest'))


def test_resolve_config_rejects_invalid_image_name() -> None:
    with pytest.raises(InvalidDockerImageNameError):
        resolve_config(DockerContextConfig('developer', 'jazzy', 'Invalid/Image:latest'))


def test_resolve_config_rejects_invalid_user() -> None:
    with pytest.raises(InvalidImageUserError):
        resolve_config(DockerContextConfig('InvalidUser', 'jazzy', 'local/ros-test:latest'))


def test_resolve_config_rejects_empty_fixed_rosdep_packages_dir() -> None:
    with pytest.raises(InvalidRosdepPackagesDirError):
        resolve_config(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest', rosdep_packages_dir=' '))
