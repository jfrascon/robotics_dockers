import shutil
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
    assert tmp_path.joinpath('.resources', 'entrypoint.sh').is_file()
    assert tmp_path.joinpath('.resources', 'entrypoint.d', '99-uid-gid-adapt.sh').is_file()


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


def test_generate_docker_context_uses_nvidia_check_only_when_requested(tmp_path: Path) -> None:
    generate_docker_context(
        DockerContextConfig(
            image_main_user='developer',
            ros_distro='jazzy',
            img_id='local/ros-test:latest',
            output_dir=tmp_path,
            use_host_nvidia_driver=True,
        )
    )

    assert tmp_path.joinpath('.resources', 'entrypoint.d', '98-nvidia-gpu-driver-check.sh').is_file()


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
    assert '"--pkgs-dir"' in build_script
    assert 'if args.pkgs_dir:' in build_script
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
    assert '"--pkgs-dir"' not in build_script
    assert 'configured_pkgs_dir' not in build_script
    assert 'is_absolute()' not in build_script
    assert 'pkgs_dir = context_dir.joinpath("../src").resolve()' in build_script
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
    assert '"--pkgs-dir"' not in build_script
    assert 'configured_pkgs_dir' not in build_script
    assert f'pkgs_dir = Path("{fixed_pkgs_dir}").expanduser().resolve()' in build_script
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
    assert 'pkgs_dir = Path("~/workspace/src").expanduser().resolve()' in build_script
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
