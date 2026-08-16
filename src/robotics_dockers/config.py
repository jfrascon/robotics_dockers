from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from pathlib import Path

from robotics_dockers.errors import (
    InvalidDockerImageNameError,
    InvalidImageIdentityError,
    InvalidRosdepPackagesDirError,
    InvalidRosDistroError,
)

ROS_DISTROS: dict[str, str] = {'humble': '22.04', 'jazzy': '24.04'}

DEFAULT_META_TITLE = 'Docker image with ROS 2'
DEFAULT_META_DESC = 'Docker image for development and testing'
MIN_USER_GROUP_ID = 1000
MAX_USER_GROUP_ID = 4294967294
LOCAL_ACCOUNT_NAME_PATTERN = re.compile(r'[a-z_][a-z0-9_-]{0,31}')


@dataclass(frozen=True)
class DockerContextConfig:
    """User input expressed with concise names local to the Python API.

    Templates publish the identity as ``ROBOTICS_DOCKERS_*`` environment
    variables. That prefix belongs to the image's public environment contract;
    repeating it on every Python attribute would not add information here.
    """

    ros_distro: str
    img_id: str
    user: str
    user_id: int | str
    primary_group_id: int | str
    primary_group: str | None = None
    output_dir: Path | str | None = None
    base_img: str | None = None
    use_host_nvidia_driver: bool = False
    meta_title: str = DEFAULT_META_TITLE
    meta_desc: str = DEFAULT_META_DESC
    meta_authors: str | None = None
    rosdep_packages_dir: Path | str | None = None


@dataclass(frozen=True)
class DockerContextResult:
    context_dir: Path
    generated_files: tuple[Path, ...]
    # Expose the exact normalized configuration used to render the context.
    # Callers can print IDs, defaulted names and paths without resolving and
    # validating the original input a second time.
    resolved_config: ResolvedDockerContextConfig


@dataclass(frozen=True)
class ResolvedDockerContextConfig:
    ros_distro: str
    img_id: str
    user: str
    user_id: int
    user_home: str
    primary_group: str
    primary_group_id: int
    output_dir: Path | None
    base_img: str
    use_host_nvidia_driver: bool
    meta_title: str
    meta_desc: str
    meta_authors: str
    rosdep_packages_dir: str | None
    rosdep_packages_dir_mode: str


def get_ros_distros_help() -> str:
    lines = ['Available ROS distros:']

    # Sort by Ubuntu version, then distro name, for consistent help output.
    sorted_distros = sorted(ROS_DISTROS.items(), key=lambda item: (item[1], item[0]))

    for key, value in sorted_distros:
        lines.append(f'    {key:<6}: ROS 2, Ubuntu {value}.')

    return '\n'.join(lines)


def resolve_config(config: DockerContextConfig) -> ResolvedDockerContextConfig:
    user = config.user.strip()
    primary_group = config.primary_group.strip() if config.primary_group is not None else user
    user_id = _normalize_local_id(config.user_id, 'UID')
    primary_group_id = _normalize_local_id(config.primary_group_id, 'primary GID')
    ros_distro = config.ros_distro.strip().lower()
    img_id = config.img_id.strip()
    base_img = config.base_img.strip() if config.base_img is not None else ''
    output_dir = Path(config.output_dir).expanduser().resolve() if config.output_dir is not None else None
    meta_authors = config.meta_authors if config.meta_authors is not None else getpass.getuser()
    rosdep_packages_dir = str(config.rosdep_packages_dir).strip() if config.rosdep_packages_dir is not None else None

    if ros_distro not in ROS_DISTROS:
        raise InvalidRosDistroError(f"Invalid ROS distro '{ros_distro}'. Allowed:\n{get_ros_distros_help()}")

    if not base_img:
        base_img = f'ubuntu:{ROS_DISTROS[ros_distro]}'

    if not is_valid_docker_image_name(base_img):
        raise InvalidDockerImageNameError(f"Invalid Docker base image name: '{base_img}'")

    if not is_valid_docker_image_name(img_id):
        raise InvalidDockerImageNameError(f"Invalid Docker image name: '{img_id}'")

    _validate_local_account_name(user, 'user')
    _validate_local_account_name(primary_group, 'primary group')

    if rosdep_packages_dir == '':
        raise InvalidRosdepPackagesDirError('rosdep_packages_dir must be a non-empty path when provided.')

    rosdep_packages_dir_mode = _resolve_rosdep_packages_dir_mode(rosdep_packages_dir)

    return ResolvedDockerContextConfig(
        ros_distro=ros_distro,
        img_id=img_id,
        user=user,
        user_id=user_id,
        user_home=f'/home/{user}',
        primary_group=primary_group,
        primary_group_id=primary_group_id,
        output_dir=output_dir,
        base_img=base_img,
        use_host_nvidia_driver=config.use_host_nvidia_driver,
        meta_title=config.meta_title,
        meta_desc=config.meta_desc,
        meta_authors=meta_authors,
        rosdep_packages_dir=rosdep_packages_dir,
        rosdep_packages_dir_mode=rosdep_packages_dir_mode,
    )


def _validate_local_account_name(value: str, label: str) -> None:
    """Validate the deliberately narrow local account-name contract used by generated images."""
    if not LOCAL_ACCOUNT_NAME_PATTERN.fullmatch(value):
        raise InvalidImageIdentityError(
            f"Invalid {label} '{value}'. It must start with a lowercase letter or '_', contain only lowercase "
            "letters, digits, '-' or '_', and contain at most 32 characters."
        )


def _normalize_local_id(value: int | str, label: str) -> int:
    """Return a canonical decimal UID/GID without relying on shell octal parsing rules."""
    if isinstance(value, bool):
        raise InvalidImageIdentityError(f'Invalid {label} {value!r}: a decimal integer is required.')

    text = str(value).strip()
    if not re.fullmatch(r'[0-9]+', text):
        raise InvalidImageIdentityError(f"Invalid {label} '{text}': only decimal digits are allowed.")

    normalized = int(text, 10)
    if not MIN_USER_GROUP_ID <= normalized <= MAX_USER_GROUP_ID:
        raise InvalidImageIdentityError(
            f'Invalid {label} {normalized}: expected a value between {MIN_USER_GROUP_ID} and {MAX_USER_GROUP_ID}.'
        )

    return normalized


def is_valid_docker_image_name(name: str) -> bool:
    """
    Validate the Docker image-reference subset accepted by this generator.

    Format:
        [HOST[:PORT_NUMBER]/]PATH[:TAG]

    Digests and IPv6 registry literals are intentionally outside this small CLI
    contract. The tag rule follows Docker's first-character and 128-character
    limits so values accepted here do not fail later in ``docker build --tag``.
    """

    # Registry labels cannot begin or end with '-'. Keeping that rule here
    # prevents a malformed registry from surviving until docker build.
    host_label = r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?'
    host_and_port_prefix = rf'((?:{host_label})(?:\.{host_label})*(?::[0-9]+)?/)?'
    path_separator = r'(?:\.|_{1,2}|-+)'
    path_component = rf'[a-z0-9]+(?:{path_separator}[a-z0-9]+)*'
    path_re = rf'{path_component}(/{path_component})*'
    tag_re = r'(:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127})?'
    full_re = re.compile(rf'^{host_and_port_prefix}{path_re}{tag_re}$')

    return bool(full_re.match(name))


def _resolve_rosdep_packages_dir_mode(rosdep_packages_dir: str | None) -> str:
    if rosdep_packages_dir is None:
        return 'cli'

    # fixed_host_path is intentionally not named fixed_absolute: paths beginning with '~'
    # are not absolute before expanduser(), but they are still host paths independent of context_dir.
    if Path(rosdep_packages_dir).is_absolute() or rosdep_packages_dir.startswith('~'):
        return 'fixed_host_path'

    return 'fixed_relative'
