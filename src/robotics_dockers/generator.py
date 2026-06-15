from __future__ import annotations

import shutil
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
from robotics_dockers.errors import MissingResourceError

ResourceSpec = list[str | dict[str, Any] | bool]


def generate_docker_context(config: DockerContextConfig) -> DockerContextResult:
    resolved_config = resolve_config(config)
    context_dir = _resolve_context_dir(resolved_config.output_dir)
    generated_files = _install_items(_create_items_to_install(resolved_config), context_dir)
    return DockerContextResult(context_dir=context_dir, generated_files=tuple(generated_files))


def _create_items_to_install(config: ResolvedDockerContextConfig) -> dict[str, ResourceSpec]:
    image_main_user_home = f'/home/{config.image_main_user}'

    items_to_install: dict[str, ResourceSpec] = {
        'Dockerfile': [
            'Dockerfile.j2',
            {
                'base_img': config.base_img,
                'image_main_user': config.image_main_user,
                'image_main_user_home': image_main_user_home,
                'ros_distro': config.ros_distro,
                'use_host_nvidia_driver': config.use_host_nvidia_driver,
                'meta_title': config.meta_title,
                'meta_desc': config.meta_desc,
                'meta_authors': config.meta_authors,
            },
            False,
        ],
        'build.py': [
            'build.j2',
            {
                'base_img': config.base_img,
                'img_id': config.img_id,
                'image_main_user': config.image_main_user,
                'ros_distro': config.ros_distro,
                'rosdep_packages_dir': config.rosdep_packages_dir,
            },
            True,
        ],
        'docker-compose-dev.yaml': [
            'docker-compose.j2',
            {
                'service': f'{config.img_id.replace(":", "_").replace("/", "_")}_cont',
                'img_id': config.img_id,
                'image_main_user': config.image_main_user,
                'image_main_user_home': image_main_user_home,
                'img_workspace_dir': f'{image_main_user_home}/workspace',
                'img_datasets_dir': f'{image_main_user_home}/datasets',
                'img_ssh_dir': f'{image_main_user_home}/.ssh',
                'img_gitconfig_file': f'{image_main_user_home}/.gitconfig',
                'use_host_nvidia_driver': config.use_host_nvidia_driver,
            },
            False,
        ],
        '.resources/bash_aliases.user': ['bash_aliases.user', True],
        '.resources/deduplicate_path': ['deduplicate_path', True],
        '.resources/install_base_system.sh': ['install_base_system.sh', True],
        '.resources/install_extra_pkgs.sh': ['install_extra_pkgs.sh', True],
        '.resources/install_ros.sh': ['install_ros2.sh', True],
        '.resources/check_entrypoint_d': ['check_entrypoint_d', True],
        '.resources/rosbuild': ['ros2build', True],
        '.resources/rosdep_init_update_install.sh': ['rosdep_init_update_install.sh', True],
        '.resources/extra.d/apt_packages.sh': ['extra.d/apt_packages.sh', True],
        '.resources/extra.d/requirements.txt': ['extra.d/requirements.txt', False],
        '.resources/extra.d/rust_packages.txt': ['extra.d/rust_packages.txt', False],
    }

    items_to_install['.resources/colcon_mixin_metadata.sh'] = ['colcon_mixin_metadata.sh', True]
    items_to_install['.resources/skip_rosdep_keys'] = ['skip_rosdep_keys', True]
    items_to_install['.resources/entrypoint.sh'] = ['entrypoint.sh', True]
    items_to_install['.resources/entrypoint.d/99-uid-gid-adapt.sh'] = ['entrypoint.d/99-uid-gid-adapt.sh', True]

    if config.use_host_nvidia_driver:
        items_to_install['.resources/entrypoint.d/98-nvidia-gpu-driver-check.sh'] = [
            'entrypoint.d/98-nvidia-gpu-driver-check.sh',
            True,
        ]

    items_to_install['.resources/bashrc.user'] = ['bashrc.user', True]

    if not config.use_host_nvidia_driver:
        items_to_install['.resources/extra.d/apt_packages.sh'] = ['examples/install_default_mesa_packages.sh', True]

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

    if not destination_path.parent.exists():
        destination_path.parent.mkdir(parents=True)

    _copy_resource_directory(source_path, destination_path)
    destination_path.chmod(0o775)


def _create_file(source_path: resources.abc.Traversable | None, destination_path: Path, executable: bool) -> None:
    if not destination_path.parent.exists():
        destination_path.parent.mkdir(parents=True)

    if source_path is None:
        destination_path.touch()
    else:
        if not source_path.is_file():
            raise MissingResourceError(f"Required resource '{source_path}' is not a file.")
        destination_path.write_bytes(source_path.read_bytes())

    destination_path.chmod(0o775 if executable else 0o664)


def _copy_resource_directory(source_path: resources.abc.Traversable, destination_path: Path) -> None:
    if destination_path.exists():
        shutil.rmtree(destination_path)

    destination_path.mkdir(parents=True)

    for child in source_path.iterdir():
        child_destination = destination_path / child.name
        if child.is_dir():
            _copy_resource_directory(child, child_destination)
        else:
            child_destination.write_bytes(child.read_bytes())


def _render_template(template_name: str, destination_path: Path, context: dict[str, Any], executable: bool) -> None:
    if not destination_path.parent.exists():
        destination_path.parent.mkdir(parents=True)

    environment = Environment(
        loader=PackageLoader('robotics_dockers', 'resources'), trim_blocks=True, lstrip_blocks=True
    )
    rendered_text = environment.get_template(template_name).render(context)
    destination_path.write_text(rendered_text)
    destination_path.chmod(0o775 if executable else 0o664)


def _resolve_context_dir(output_dir: Path | None) -> Path:
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    return Path(tempfile.mkdtemp(prefix='robotics_dockers_', dir='/tmp'))


def _resource_path(relative_path: str) -> resources.abc.Traversable:
    resource_path = resources.files('robotics_dockers.resources').joinpath(relative_path)

    if not resource_path.exists():
        raise MissingResourceError(f"Required resource '{relative_path}' does not exist.")

    return resource_path
