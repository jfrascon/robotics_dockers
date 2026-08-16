from __future__ import annotations

import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader

from robotics_dockers.config import (
    DockerContextConfig,
    DockerContextResult,
    ResolvedDockerContextConfig,
    resolve_config,
)
from robotics_dockers.errors import InvalidOutputDirectoryError, MissingResourceError

ResourceSpec = list[str | dict[str, Any] | bool | None]


def generate_docker_context(config: DockerContextConfig) -> DockerContextResult:
    resolved_config = resolve_config(config)
    context_dir = _resolve_context_dir(resolved_config.output_dir)
    generated_files = _install_items(_create_items_to_install(resolved_config), context_dir)
    return DockerContextResult(
        context_dir=context_dir, generated_files=tuple(generated_files), resolved_config=resolved_config
    )


def _create_items_to_install(config: ResolvedDockerContextConfig) -> dict[str, ResourceSpec]:
    if config.ros_distro == 'humble':
        ros_discovery_env_name = 'ROS_LOCALHOST_ONLY'
        ros_discovery_env_value = '1'
    else:
        ros_discovery_env_name = 'ROS_AUTOMATIC_DISCOVERY_RANGE'
        ros_discovery_env_value = 'LOCALHOST'

    items_to_install: dict[str, ResourceSpec] = {
        'Dockerfile': [
            'Dockerfile.j2',
            {
                'base_img': config.base_img,
                'ros_distro': config.ros_distro,
                'ros_discovery_env_name': ros_discovery_env_name,
                'ros_discovery_env_value': ros_discovery_env_value,
                'use_host_nvidia_driver': config.use_host_nvidia_driver,
                'meta_title': config.meta_title,
                'meta_desc': config.meta_desc,
                'meta_authors': config.meta_authors,
            },
            False,
        ],
        'build.py': [
            'build.py.j2',
            {
                'base_img': config.base_img,
                'img_id': config.img_id,
                'ros_distro': config.ros_distro,
                'rosdep_packages_dir': config.rosdep_packages_dir,
                'rosdep_packages_dir_mode': config.rosdep_packages_dir_mode,
            },
            True,
        ],
        'compose_files/docker-compose.yaml': [
            'docker-compose.yaml.j2',
            {
                'service': f'{config.img_id.replace(":", "_").replace("/", "_")}_cont',
                'img_id': config.img_id,
                # Workspaces are deliberately outside the home so mounting them
                # cannot hide the environment files installed in the home.
                'img_workspace_dir': '/workspace',
                'img_datasets_dir': '/datasets',
                'use_host_nvidia_driver': config.use_host_nvidia_driver,
                'enable_workspace_mount': config.enable_workspace_mount,
            },
            False,
        ],
        '.resources/bash_aliases_user': ['bash_aliases_user', True],
        '.resources/configure_image_user.sh': ['configure_image_user.sh', True],
        '.resources/configure_sudo.sh': ['configure_sudo.sh', True],
        '.resources/entrypoint_user.sh': ['entrypoint_user.sh', True],
        '.resources/env.rc': ['env.rc', True],
        '.resources/install_pkgs': ['install_pkgs', True],
        '.resources/install_base_system.sh': ['install_base_system.sh', True],
        '.resources/install_extra_apt.sh': ['install_extra_apt.sh', True],
        '.resources/install_user_extras.sh': ['install_user_extras.sh', True],
        '.resources/install_gh.sh': ['install_gh.sh', True],
        '.resources/install_ros.sh': ['install_ros2.sh', True],
        '.resources/ros.rc': ['ros.rc', True],
        '.resources/rosbuild': ['ros2build', True],
        '.resources/rosdep_init_update_install.sh': ['rosdep_init_update_install.sh', True],
        '.resources/extra.d/apt/keyrings.d': [None],
        '.resources/extra.d/apt/sources.d': [None],
        '.resources/extra.d/apt/packages.txt': ['extra.d/apt/packages.txt', False],
        '.resources/extra.d/env.d': [None],
        '.resources/extra.d/python/install.d': [None],
        '.resources/extra.d/python/requirements.txt': ['extra.d/python/requirements.txt', False],
        '.resources/extra.d/rust/install.sh.example': ['extra.d/rust/install.sh.example', False],
        '.resources/user_preparation.d/01-delete-ubuntu-user.sh.example': [
            'user_preparation.d/01-delete-ubuntu-user.sh.example',
            False,
        ],
        '.resources/user_preparation.d/02-reuse-ubuntu-user.sh.example': [
            'user_preparation.d/02-reuse-ubuntu-user.sh.example',
            False,
        ],
        '.resources/update_image_user.sh': ['update_image_user.sh', True],
        'env_files/.gitkeep': [None, False],
        'robotics_dockers_user_env.py': ['robotics_dockers_user_env.py', True],
        'Dockerfile_update_user': ['Dockerfile_update_user', False],
    }

    items_to_install['.resources/colcon_mixin_metadata.sh'] = ['colcon_mixin_metadata.sh', True]
    items_to_install['.resources/skip_rosdep_keys'] = ['skip_rosdep_keys', True]
    items_to_install['.resources/rosdep_skip_keys.txt'] = ['rosdep_skip_keys.txt', False]
    if not config.use_host_nvidia_driver:
        items_to_install['.resources/install_mesa_packages.sh'] = ['install_mesa_packages.sh', True]

    items_to_install['.resources/bashrc_user'] = ['bashrc_user', True]

    return items_to_install


def _install_items(items_to_install: dict[str, ResourceSpec], context_dir: Path) -> list[Path]:
    generated_files = []

    for destination, spec in sorted(items_to_install.items()):
        destination_path = context_dir.joinpath(destination)
        source_name = spec[0]
        source_path = _resource_path(str(source_name)) if source_name is not None else None

        if len(spec) == 1:
            _create_directory(source_path, destination_path)
        elif len(spec) == 2:
            _create_file(source_path, destination_path, bool(spec[1]))
        elif len(spec) == 3:
            context = spec[1]
            if not isinstance(context, dict):
                raise MissingResourceError(f"Jinja2 context for '{destination_path}' must be a dictionary.")
            _render_template(str(source_name), destination_path, context, bool(spec[2]))
        else:
            raise MissingResourceError(f"Invalid resource specification for '{destination_path}'.")

        generated_files.append(destination_path)

    return generated_files


def _create_directory(source_path: resources.abc.Traversable | None, destination_path: Path) -> None:
    if source_path is None:
        destination_path.mkdir(parents=True)
        return

    if not source_path.is_dir():
        raise MissingResourceError(f"Required resource '{source_path}' is not a directory.")

    destination_path.parent.mkdir(parents=True, exist_ok=True)

    _copy_resource_directory(source_path, destination_path)
    destination_path.chmod(0o775)


def _create_file(source_path: resources.abc.Traversable | None, destination_path: Path, executable: bool) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    if source_path is None:
        destination_path.touch()
    else:
        if not source_path.is_file():
            raise MissingResourceError(f"Required resource '{source_path}' is not a file.")
        destination_path.write_bytes(source_path.read_bytes())

    destination_path.chmod(0o775 if executable else 0o664)


def _copy_resource_directory(source_path: resources.abc.Traversable, destination_path: Path) -> None:
    # _resolve_context_dir already rejects non-empty output directories. Do not
    # add a second overwrite path here: an unexpected collision should fail
    # instead of deleting something that appeared after the initial check.
    destination_path.mkdir(parents=True)

    for child in source_path.iterdir():
        child_destination = destination_path / child.name
        if child.is_dir():
            _copy_resource_directory(child, child_destination)
        else:
            child_destination.write_bytes(child.read_bytes())


def _render_template(template_name: str, destination_path: Path, context: dict[str, Any], executable: bool) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    environment = Environment(
        loader=PackageLoader('robotics_dockers', 'resources'),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    rendered_text = environment.get_template(template_name).render(context)
    destination_path.write_text(rendered_text)
    destination_path.chmod(0o775 if executable else 0o664)


def _resolve_context_dir(output_dir: Path | None) -> Path:
    if output_dir is not None:
        if output_dir.exists():
            if not output_dir.is_dir():
                raise InvalidOutputDirectoryError(f"Output path '{output_dir}' exists and is not a directory.")
            if next(output_dir.iterdir(), None) is not None:
                raise InvalidOutputDirectoryError(
                    f"Output directory '{output_dir}' is not empty. Refusing to overwrite generated files or "
                    'user customizations.'
                )
        else:
            output_dir.mkdir(parents=True)
        return output_dir

    return Path(tempfile.mkdtemp(prefix='robotics_dockers_', dir='/tmp'))


def _resource_path(relative_path: str) -> resources.abc.Traversable:
    # _create_file and _create_directory perform the useful type-specific
    # validation. A separate exists() call here would inspect every resource
    # twice without producing a better decision.
    return resources.files('robotics_dockers.resources').joinpath(relative_path)
