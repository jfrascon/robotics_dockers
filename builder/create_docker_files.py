#!/usr/bin/env python3

import argparse
import getpass
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

if __name__ == "__main__":
    ROS_DISTROS: dict[str, str] = {
        "humble": "2:22.04",
        "jazzy": "2:24.04",
    }

    def create_items_to_install(
        base_img: str,
        image_main_user: str,
        ros_distro: str,
        ros_version: str,
        img_id_to_build: str,
        use_host_nvidia_driver: bool,
    ) -> dict[str, list[str | dict[str, str] | bool]]:
        # Items to use.
        # Source is relative to base_dir, destination relative to context_path)
        # (src_name, dst_name, is_executable)
        #
        # Files that go to the root of the output directory:
        items_to_install = {
            "Dockerfile": [
                "Dockerfile.j2",
                {
                    "base_img": base_img,
                    "image_main_user": image_main_user,
                    "image_main_user_home": f"/home/{image_main_user}",
                    "ros_distro": ros_distro,
                    "ros_version": ros_version,
                },
                False,
            ],
            "build.py": [
                "build.j2",
                {
                    "base_img": base_img,
                    "img_id": img_id_to_build,
                    "image_main_user": image_main_user,
                    "ros_distro": ros_distro,
                    "ros_version": ros_version,
                },
                True,
            ],
            "docker-compose-dev.yaml": [
                "docker-compose.j2",
                {
                    "service": f"{img_id_to_build.replace(':', '_').replace('/', '_')}_cont",
                    "img_id": img_id_to_build,
                    "img_workspace_dir": f"/home/{image_main_user}/workspace",
                    "img_datasets_dir": f"/home/{image_main_user}/datasets",
                    "img_ssh_dir": f"/home/{image_main_user}/.ssh",
                    "img_gitconfig_file": f"/home/{image_main_user}/.gitconfig",
                    "use_host_nvidia_driver": use_host_nvidia_driver,
                },
                False,
            ],
            # Files that go to .resources/
            ".resources/bash_aliases.user": ["bash_aliases.user", True],
            ".resources/deduplicate_path": ["deduplicate_path", True],
            ".resources/install_base_system.sh": ["install_base_system.sh", True],
            ".resources/install_extra_pkgs.sh": ["install_extra_pkgs.sh", True],
            ".resources/install_ros.sh": ["install_ros2.sh", True],
            ".resources/rosbuild": ["ros2build", True],
            ".resources/rosdep_init_update_install.sh": ["rosdep_init_update_install.sh", True],
            # extra.d/ templates
            ".resources/extra.d/apt_packages.sh": ["extra.d/apt_packages.sh", True],
            ".resources/extra.d/requirements.txt": ["extra.d/requirements.txt", False],
            ".resources/extra.d/rust_packages.txt": ["extra.d/rust_packages.txt", False],
        }

        items_to_install[".resources/colcon_mixin_metadata.sh"] = [
            "colcon_mixin_metadata.sh",
            True,
        ]
        items_to_install[".resources/skip_rosdep_keys"] = [
            "skip_rosdep_keys",
            True,
        ]

        items_to_install[".resources/entrypoint.sh"] = ["entrypoint.sh", True]
        items_to_install[".resources/entrypoint.d/00-checks.sh"] = [
            "entrypoint.d/00-checks.sh",
            True,
        ]
        items_to_install[".resources/entrypoint.d/99-uid-gid-adapt.sh"] = [
            "entrypoint.d/99-uid-gid-adapt.sh",
            True,
        ]
        if use_host_nvidia_driver:
            items_to_install[".resources/entrypoint.d/98-gpu-driver-check.sh"] = [
                "entrypoint.d/98-gpu-driver-check.sh",
                True,
            ]

        # ROS2 bashrc is plain bash — no Jinja2 variables.
        items_to_install[".resources/bashrc.user"] = [
            "bashrc.user.ros2",
            True,
        ]

        # If not using the host NVIDIA driver, provide Mesa packages script as
        # extra.d/apt_packages.sh so the user can enable/extend it before building.
        if not use_host_nvidia_driver:
            items_to_install[".resources/extra.d/apt_packages.sh"] = [
                "examples/install_default_mesa_packages.sh",
                True,
            ]

        return items_to_install

    def get_ros_distros_str_for_help() -> str:
        lines = ["Available ROS distros:"]

        # Sort by ROS version, then Ubuntu version, then distro name for consistent help output
        sorted_distros = sorted(
            ROS_DISTROS.items(),
            key=lambda item: (
                int(item[1].split(":")[0]),
                item[1].split(":")[1],
                item[0],
            ),
        )

        for key, value in sorted_distros:
            ros_version, ubuntu_version = value.split(":")
            lines.append(f"    {key:<6}: ros{ros_version}, ubuntu {ubuntu_version}.")

        return "\n".join(lines)

    def img_exists_locally(img: str) -> bool:
        cmd = ["docker", "image", "inspect", img]
        # capture=True -> stdout y stderr redireted to PIPE (they are not shown in the terminal)
        # check=False  -> it does not throw exception if the image does not exist
        result = run_command(cmd, capture=True, check=False)
        return result.returncode == 0

    def install_items(
        items_to_install: dict[str, list[str | dict[str, str] | bool]],
        context_dir: Path,
    ) -> None:
        # Copy the items to use to the context directory.
        for key in sorted(items_to_install.keys()):
            dst_path = context_dir.joinpath(key)

            lst = items_to_install[key]

            # If the lst[0] is None, it means that the key, that can be a file or a directory, must
            # be created, not copied from a resource.
            src_path = None

            if lst[0] is not None:
                src_path = root_path.joinpath(lst[0])

                if not src_path.exists():
                    print(f"Required resource '{str(src_path)}' does not exist.")
                    sys.exit(1)

            # len = 1 -> directory
            #    src_path is None -> create an empty directory
            #    src_path is not None -> copy the directory recursively
            # len = 2 -> file with permissions
            #    src_path is None -> create an empty file with permissions
            #    src_path is not None -> copy the file with permissions
            # len = 3 -> file with Jinja2 rendering and permissions
            #    src_path is None -> raise an exception, not allowed
            #    src_path is not None -> copy the file with Jinja2 rendering and permissions
            if len(lst) == 1:
                print(f"Creating directory '{dst_path.name}'")

                if src_path is not None:
                    if not src_path.is_dir():
                        print(
                            f"Required resource '{str(src_path)}' is not a directory."
                        )
                        sys.exit(1)

                    if not dst_path.parent.exists():
                        dst_path.parent.mkdir(parents=True)

                    shutil.copytree(src_path, dst_path, copy_function=shutil.copy2)
                    dst_path.chmod(0o775)
                else:
                    dst_path.mkdir(parents=True)
            elif len(lst) == 2:
                print(f"Creating file '{dst_path.name}'")

                if src_path is not None:
                    if not src_path.is_file():
                        print(f"Required resource '{str(src_path)}' is not a file.")
                        sys.exit(1)

                    if not dst_path.parent.exists():
                        dst_path.parent.mkdir(parents=True)

                    shutil.copy2(src_path, dst_path)
                else:
                    dst_path.touch()

                if lst[1]:
                    dst_path.chmod(0o775)
                else:
                    dst_path.chmod(0o664)
            elif len(lst) == 3:
                print(f"Creating file '{dst_path.name}'")

                if src_path is None:
                    print(
                        f"Relative source path can't be empty for element '{str(dst_path)}'."
                    )
                    sys.exit(1)

                if not src_path.is_file():
                    print(f"Required resource '{str(src_path)}' is not a file.")
                    sys.exit(1)

                context = lst[1]

                if context is None:
                    print(
                        f"Context for Jinja2 rendering can't be None for element '{str(dst_path)}'."
                    )

                if not isinstance(context, dict):
                    print(
                        f"Context for Jinja2 rendering must be a dictionary for element '{str(dst_path)}'."
                    )

                if not dst_path.parent.exists():
                    dst_path.parent.mkdir(parents=True)

                jinja2_env = Environment(
                    loader=FileSystemLoader(src_path.parent),
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
                jinja2_template = jinja2_env.get_template(src_path.name)
                rendered_text = jinja2_template.render(context)

                with dst_path.open("w") as f:
                    f.write(rendered_text)

                if lst[2]:
                    dst_path.chmod(0o775)
                else:
                    dst_path.chmod(0o664)

    def is_valid_docker_img_name(name: str) -> bool:
        """
        Validate a Docker image name according to Docker's official naming rules.

        Format:
            [HOST[:PORT_NUMBER]/]PATH[:TAG]

        See: https://docs.docker.com/get-started/docker-concepts/building-images/build-tag-and-publish-an-image/#tagging-images
        """

        # Optional registry prefix: host (lower‑case letters, digits, dots, dashes)
        # with optional :PORT, followed by a slash.
        host_and_port_prefix = r"([a-z0-9.-]+(:[0-9]+)?/)?"

        # A separator inside a path component can be:
        #   • a single dot
        #   • one or two underscores
        #   • one or more dashes
        path_separator = r"(?:\.|_{1,2}|-+)"

        # A path component must start and end with an alphanumeric character,
        # separators are allowed only between alphanumerics.
        path_component = rf"[a-z0-9]+(?:{path_separator}[a-z0-9]+)*"

        # PATH = one or more components separated by '/'
        path_re = rf"{path_component}(/{path_component})*"

        # Optional TAG: colon + allowed characters (letters, digits, '_', '.', '-')
        tag_re = r"(:[a-zA-Z0-9_.-]+)?"

        # Full regex combining all parts
        full_re = re.compile(rf"^{host_and_port_prefix}{path_re}{tag_re}$")

        return bool(full_re.match(name))

    def run_command(
        cmd: list[str],
        capture: bool = False,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd, check=check, text=True, capture_output=capture, cwd=cwd
        )

    # --------------------------------------------------------------------------------------------------
    # Main execution block
    # --------------------------------------------------------------------------------------------------

    script_name = Path(__file__).name
    base_dir = Path(__file__).parent.resolve()

    parser = argparse.ArgumentParser(
        description="Builds a Docker image with and active user and ROS",
        allow_abbrev=False,  # Disable prefix matching
        add_help=False,  # Add custom help message
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(
            prog, max_help_position=38
        ),
    )

    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit",
    )

    parser.add_argument(
        "-b",
        "--base-img",
        type=str,
        help="Base image. Default: ubuntu:X.Y, matched to the ROS distro.",
    )

    parser.add_argument(
        "image_main_user",
        type=str,
        help="User to run containers for the resulting Docker image",
    )

    parser.add_argument(
        "ros_distro", type=str, help=f"ROS distro.\n{get_ros_distros_str_for_help()}"
    )

    parser.add_argument(
        "img_id", type=str, help="Image ID for the resulting Docker image."
    )

    parser.add_argument(
        "--use-host-nvidia-driver", action="store_true", help="Use host's NVIDIA driver"
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Output directory for the generated context. "
            "If not specified, a temporary directory under /tmp is created."
        ),
    )

    parser.add_argument(
        "--meta-title",
        type=str,
        default="Docker image with ROS2-humble",
        help='Title to include in the image\'s metadata (e.g "App")',
    )

    parser.add_argument(
        "--meta-desc",
        type=str,
        default="Docker image for development and testing",
        help="Description to include in the image's metadata",
    )

    parser.add_argument(
        "--meta-authors",
        type=str,
        default=getpass.getuser(),
        help="Authors of the image",
    )

    args = parser.parse_args()
    base_img = args.base_img.strip() if args.base_img is not None else ""
    image_main_user = args.image_main_user.strip()  # Required, so won't be empty
    ros_distro = args.ros_distro.lower()  # Required, so won't be empty
    img_id_to_build = args.img_id.strip()  # Required, so won't be empty

    if ros_distro not in ROS_DISTROS:
        print(
            f"Error: Invalid ROS distro '{ros_distro}'. Allowed: {get_ros_distros_str_for_help()}"
        )
        sys.exit(1)

    ros_version, ubuntu_version = ROS_DISTROS[ros_distro].split(":")

    if not base_img:
        base_img = f"ubuntu:{ubuntu_version}"

        if not is_valid_docker_img_name(
            base_img
        ):  # Should be valid by construction, but just in case
            print(
                f"Error: Default base image '{base_img}' is invalid.", file=sys.stderr
            )
            sys.exit(1)

        print(
            f"No base image specified, defaulting to '{base_img}' for 'ROS{ros_version}-{ros_distro}'"
        )
    elif not is_valid_docker_img_name(base_img):
        print(f"Error: Invalid Docker base image name: '{base_img}'", file=sys.stderr)
        sys.exit(1)

    if not is_valid_docker_img_name(img_id_to_build):
        print(f"Error: Invalid Docker image name: '{img_id_to_build}'", file=sys.stderr)
        sys.exit(1)

    if not image_main_user or " " in image_main_user:
        print(
            f"Error: Invalid user '{image_main_user}'. No whitepaces allowed",
            file=sys.stderr,
        )
        sys.exit(1)

    root_path = Path(__file__).expanduser().resolve().parent

    # with tempfile.TemporaryDirectory(prefix="context_", dir="/tmp") as tmp_dir:
    if args.output:
        context_dir = Path(args.output).expanduser().resolve()
        context_dir.mkdir(parents=True, exist_ok=True)
    else:
        context_dir = Path(tempfile.mkdtemp(prefix="context_", dir="/tmp"))

    print(f"Context directory: {context_dir}")

    install_items(
        create_items_to_install(
            base_img,
            image_main_user,
            ros_distro,
            ros_version,
            img_id_to_build,
            args.use_host_nvidia_driver,
        ),
        context_dir,
    )
