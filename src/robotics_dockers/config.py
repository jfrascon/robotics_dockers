from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from pathlib import Path

from robotics_dockers.errors import InvalidDockerImageNameError, InvalidImageUserError, InvalidRosDistroError

ROS_DISTROS: dict[str, str] = {'humble': '22.04', 'jazzy': '24.04'}

DEFAULT_META_TITLE = 'Docker image with ROS 2'
DEFAULT_META_DESC = 'Docker image for development and testing'


@dataclass(frozen=True)
class DockerContextConfig:
    image_main_user: str
    ros_distro: str
    img_id: str
    output_dir: Path | str | None = None
    base_img: str | None = None
    use_host_nvidia_driver: bool = False
    meta_title: str = DEFAULT_META_TITLE
    meta_desc: str = DEFAULT_META_DESC
    meta_authors: str | None = None


@dataclass(frozen=True)
class DockerContextResult:
    context_dir: Path
    generated_files: tuple[Path, ...]


@dataclass(frozen=True)
class ResolvedDockerContextConfig:
    image_main_user: str
    ros_distro: str
    img_id: str
    output_dir: Path | None
    base_img: str
    use_host_nvidia_driver: bool
    meta_title: str
    meta_desc: str
    meta_authors: str


def get_ros_distros_help() -> str:
    lines = ['Available ROS distros:']

    # Sort by Ubuntu version, then distro name, for consistent help output.
    sorted_distros = sorted(ROS_DISTROS.items(), key=lambda item: (item[1], item[0]))

    for key, value in sorted_distros:
        lines.append(f'    {key:<6}: ROS 2, Ubuntu {value}.')

    return '\n'.join(lines)


def resolve_config(config: DockerContextConfig) -> ResolvedDockerContextConfig:
    image_main_user = config.image_main_user.strip()
    ros_distro = config.ros_distro.strip().lower()
    img_id = config.img_id.strip()
    base_img = config.base_img.strip() if config.base_img is not None else ''
    output_dir = Path(config.output_dir).expanduser().resolve() if config.output_dir is not None else None
    meta_authors = config.meta_authors if config.meta_authors is not None else getpass.getuser()

    if ros_distro not in ROS_DISTROS:
        raise InvalidRosDistroError(f"Invalid ROS distro '{ros_distro}'. Allowed:\n{get_ros_distros_help()}")

    if not base_img:
        base_img = f'ubuntu:{ROS_DISTROS[ros_distro]}'
    elif not is_valid_docker_image_name(base_img):
        raise InvalidDockerImageNameError(f"Invalid Docker base image name: '{base_img}'")

    if not is_valid_docker_image_name(base_img):
        raise InvalidDockerImageNameError(f"Default base image '{base_img}' is invalid.")

    if not is_valid_docker_image_name(img_id):
        raise InvalidDockerImageNameError(f"Invalid Docker image name: '{img_id}'")

    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', image_main_user):
        raise InvalidImageUserError(
            f"Invalid user '{image_main_user}'. Must be a valid Unix username: start with a lowercase "
            "letter or '_', followed by lowercase letters, digits, '-' or '_' (max 32 chars total)."
        )

    return ResolvedDockerContextConfig(
        image_main_user=image_main_user,
        ros_distro=ros_distro,
        img_id=img_id,
        output_dir=output_dir,
        base_img=base_img,
        use_host_nvidia_driver=config.use_host_nvidia_driver,
        meta_title=config.meta_title,
        meta_desc=config.meta_desc,
        meta_authors=meta_authors,
    )


def is_valid_docker_image_name(name: str) -> bool:
    """
    Validate a Docker image name according to Docker's official naming rules.

    Format:
        [HOST[:PORT_NUMBER]/]PATH[:TAG]
    """

    host_and_port_prefix = r'([a-z0-9.-]+(:[0-9]+)?/)?'
    path_separator = r'(?:\.|_{1,2}|-+)'
    path_component = rf'[a-z0-9]+(?:{path_separator}[a-z0-9]+)*'
    path_re = rf'{path_component}(/{path_component})*'
    tag_re = r'(:[a-zA-Z0-9_.-]+)?'
    full_re = re.compile(rf'^{host_and_port_prefix}{path_re}{tag_re}$')

    return bool(full_re.match(name))
