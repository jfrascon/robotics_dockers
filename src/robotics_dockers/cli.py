from __future__ import annotations

import argparse
import sys

from robotics_dockers.config import (
    DEFAULT_META_DESC,
    DEFAULT_META_TITLE,
    DockerContextConfig,
    DockerContextResult,
    get_ros_distros_help,
)
from robotics_dockers.errors import RoboticsDockersError
from robotics_dockers.generator import generate_docker_context


def main(argv: list[str] | None = None) -> int:
    parser = _create_parser()
    args = parser.parse_args(argv)

    if args.command == 'new':
        return _run_create(args)

    parser.print_help()
    return 1


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='robotics-dockers',
        description='Generate Docker build contexts for ROS 2 development images.',
        allow_abbrev=False,
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(prog, max_help_position=38),
    )

    subparsers = parser.add_subparsers(dest='command')
    new_parser = subparsers.add_parser(
        'new',
        help='generate a ROS 2 Docker build context',
        description='Generate a Docker build context for a ROS 2 development image.',
        allow_abbrev=False,
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(prog, max_help_position=38),
    )
    # Generation records only settings that may be shared in source control.
    # The local development identity is supplied later to the generated
    # build.py, immediately before Docker builds the image.
    new_parser.add_argument('ros_distro', metavar='ros-distro', type=str, help=f'ROS distro.\n{get_ros_distros_help()}')
    new_parser.add_argument('img_id', metavar='img-id', type=str, help='ID for the resulting Docker image')
    new_parser.add_argument(
        '--base-img', type=str, default=None, help='Base image. Default: ubuntu:X.Y, matched to the ROS distro.'
    )
    new_parser.add_argument(
        '--nvidia', action='store_true', dest='use_host_nvidia_driver', help="Use host's NVIDIA driver"
    )
    new_parser.add_argument(
        '--meta-title', type=str, default=DEFAULT_META_TITLE, help='Title to include in the image metadata'
    )
    new_parser.add_argument(
        '--meta-desc', type=str, default=DEFAULT_META_DESC, help='Description to include in the image metadata'
    )
    new_parser.add_argument('--meta-authors', type=str, default=None, help='Authors of the image')
    new_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help=(
            'Output directory for the generated context. If not specified, a temporary directory under /tmp is created.'
        ),
    )

    return parser


def _run_create(args: argparse.Namespace) -> int:
    config = DockerContextConfig(
        ros_distro=args.ros_distro,
        img_id=args.img_id,
        output_dir=args.output,
        base_img=args.base_img,
        use_host_nvidia_driver=args.use_host_nvidia_driver,
        meta_title=args.meta_title,
        meta_desc=args.meta_desc,
        meta_authors=args.meta_authors,
    )

    try:
        result = generate_docker_context(config)
    except RoboticsDockersError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    _print_create_summary(result)
    return 0


def _print_create_summary(result: DockerContextResult) -> None:
    config = result.resolved_config
    resource_files = [path for path in result.generated_files if '.resources' in path.parts]

    print('Created ROS 2 Docker files.')
    print()
    print(f'  Output directory: {result.context_dir}')
    print(f'  Image name:        {config.img_id}')
    print(f'  ROS distro:        {config.ros_distro}')
    print(f'  Base image:        {config.base_img}')
    print(f'  Host NVIDIA:       {"enabled" if config.use_host_nvidia_driver else "disabled"}')
    print()
    print('Generated:')
    print('  - Dockerfile')
    print('  - build.py')
    print('  - compose_files/docker-compose.yaml')
    print('  - robotics_dockers_user_env.py')
    print('  - env_files/')
    print(f'  - .resources/ ({len(resource_files)} support files)')
    print()
    print('Next steps:')
    print('  1. Review optional customizations in .resources/extra.d/')
    print('  2. Build the image:')
    print(f'     cd {result.context_dir}')
    print('     python3 build.py USER_NAME USER_ID GROUP_ID [--group GROUP_NAME]')


if __name__ == '__main__':
    raise SystemExit(main())
