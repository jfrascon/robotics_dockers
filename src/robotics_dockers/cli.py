from __future__ import annotations

import argparse
import sys

from robotics_dockers.config import (
    DEFAULT_META_DESC,
    DEFAULT_META_TITLE,
    DockerContextConfig,
    DockerContextResult,
    ResolvedDockerContextConfig,
    get_ros_distros_help,
    resolve_config,
)
from robotics_dockers.errors import RoboticsDockersError
from robotics_dockers.generator import generate_docker_context


def main(argv: list[str] | None = None) -> int:
    parser = _create_parser()
    args = parser.parse_args(argv)

    if args.command == 'create':
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
    create_parser = subparsers.add_parser(
        'create',
        help='generate a ROS 2 Docker build context',
        description='Generate a Docker build context for a ROS 2 development image.',
        allow_abbrev=False,
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(prog, max_help_position=38),
    )
    create_parser.add_argument('image_main_user', type=str, help='User to run containers for the resulting image')
    create_parser.add_argument('ros_distro', type=str, help=f'ROS distro.\n{get_ros_distros_help()}')
    create_parser.add_argument('img_id', type=str, help='Image ID for the resulting Docker image')
    create_parser.add_argument(
        '-b', '--base-img', type=str, default=None, help='Base image. Default: ubuntu:X.Y, matched to the ROS distro.'
    )
    create_parser.add_argument('--use-host-nvidia-driver', action='store_true', help="Use host's NVIDIA driver")
    create_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help=(
            'Output directory for the generated context. If not specified, a temporary directory under /tmp is created.'
        ),
    )
    create_parser.add_argument(
        '--meta-title', type=str, default=DEFAULT_META_TITLE, help='Title to include in the image metadata'
    )
    create_parser.add_argument(
        '--meta-desc', type=str, default=DEFAULT_META_DESC, help='Description to include in the image metadata'
    )
    create_parser.add_argument('--meta-authors', type=str, default=None, help='Authors of the image')

    return parser


def _run_create(args: argparse.Namespace) -> int:
    config = DockerContextConfig(
        image_main_user=args.image_main_user,
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
        resolved_config = resolve_config(config)
        result = generate_docker_context(config)
    except RoboticsDockersError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    _print_create_summary(result, resolved_config)
    return 0


def _print_create_summary(result: DockerContextResult, config: ResolvedDockerContextConfig) -> None:
    resource_files = [path for path in result.generated_files if '.resources' in path.parts]

    print('Created ROS 2 Docker build context.')
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
    print('  - docker-compose-dev.yaml')
    print(f'  - .resources/ ({len(resource_files)} support files)')
    print()
    print('Next steps:')
    print('  1. Review optional customizations in .resources/extra.d/')
    print('  2. Build the image:')
    print(f'     cd {result.context_dir}')
    print('     python3 build.py')


if __name__ == '__main__':
    raise SystemExit(main())
