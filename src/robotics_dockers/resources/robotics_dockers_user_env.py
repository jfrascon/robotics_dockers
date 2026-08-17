#!/usr/bin/env python3

"""Inspect a robotics-dockers image and optionally update a selected environment file."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MIN_USER_GROUP_ID = 1000
MAX_USER_GROUP_ID = 4294967294
LOCAL_ACCOUNT_NAME_PATTERN = re.compile(r'[a-z_][a-z0-9_-]{0,31}')

IMAGE_METADATA_USER_VARIABLE = 'ROBOTICS_DOCKERS_USER'
IMAGE_METADATA_USER_ID_VARIABLE = 'ROBOTICS_DOCKERS_USER_ID'
IMAGE_METADATA_USER_HOME_VARIABLE = 'ROBOTICS_DOCKERS_USER_HOME'
IMAGE_METADATA_PRIMARY_GROUP_VARIABLE = 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP'
IMAGE_METADATA_PRIMARY_GROUP_ID_VARIABLE = 'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID'

IMAGE_METADATA_VARIABLES = (
    IMAGE_METADATA_USER_VARIABLE,
    IMAGE_METADATA_USER_ID_VARIABLE,
    IMAGE_METADATA_USER_HOME_VARIABLE,
    IMAGE_METADATA_PRIMARY_GROUP_VARIABLE,
    IMAGE_METADATA_PRIMARY_GROUP_ID_VARIABLE,
)

COMPOSE_USER_VARIABLE = 'IMAGE_USER'
COMPOSE_USER_ID_VARIABLE = 'IMAGE_USER_ID'
COMPOSE_USER_HOME_VARIABLE = 'IMAGE_USER_HOME'
COMPOSE_PRIMARY_GROUP_VARIABLE = 'IMAGE_USER_PRIMARY_GROUP'
COMPOSE_PRIMARY_GROUP_ID_VARIABLE = 'IMAGE_USER_PRIMARY_GROUP_ID'

COMPOSE_IDENTITY_VARIABLES = (
    COMPOSE_USER_VARIABLE,
    COMPOSE_USER_ID_VARIABLE,
    COMPOSE_USER_HOME_VARIABLE,
    COMPOSE_PRIMARY_GROUP_VARIABLE,
    COMPOSE_PRIMARY_GROUP_ID_VARIABLE,
)


class UserEnvError(Exception):
    """Report an invalid request, image identity or local environment file."""


@dataclass(frozen=True)
class ImageUserInfo:
    """Validated development identity stored in a robotics-dockers image."""

    user: str
    user_id: int
    user_home: str
    primary_group: str
    primary_group_id: int

    def as_compose_environment(self) -> dict[str, str]:
        """Return generic variables that a Compose file can use with any image."""
        return {
            COMPOSE_USER_VARIABLE: self.user,
            COMPOSE_USER_ID_VARIABLE: str(self.user_id),
            COMPOSE_USER_HOME_VARIABLE: self.user_home,
            COMPOSE_PRIMARY_GROUP_VARIABLE: self.primary_group,
            COMPOSE_PRIMARY_GROUP_ID_VARIABLE: str(self.primary_group_id),
        }


def validate_account_name(value: str, label: str) -> str:
    """Return a local account name after applying the image account contract."""
    normalized = value.strip()
    if not LOCAL_ACCOUNT_NAME_PATTERN.fullmatch(normalized):
        raise UserEnvError(
            f"Invalid {label} '{value}'. It must start with a lowercase letter or '_', contain only lowercase "
            "letters, digits, '-' or '_', and contain at most 32 characters."
        )
    return normalized


def validate_numeric_id(value: int | str, label: str) -> int:
    """Return a canonical decimal UID or GID accepted by local Linux accounts."""
    if isinstance(value, bool):
        raise UserEnvError(f'Invalid {label} {value!r}: a decimal integer is required.')

    text = str(value).strip()
    if not re.fullmatch(r'[0-9]+', text):
        raise UserEnvError(f"Invalid {label} '{text}': only decimal digits are allowed.")

    normalized = int(text, 10)
    if not MIN_USER_GROUP_ID <= normalized <= MAX_USER_GROUP_ID:
        raise UserEnvError(
            f'Invalid {label} {normalized}: expected a value between {MIN_USER_GROUP_ID} and {MAX_USER_GROUP_ID}.'
        )
    return normalized


def inspect_image_user(image: str) -> ImageUserInfo:
    """Read and validate the public ROBOTICS_DOCKERS_* environment contract."""
    if not image.strip():
        raise UserEnvError('A non-empty Docker image name is required.')

    try:
        completed = subprocess.run(['docker', 'image', 'inspect', image], check=False, text=True, capture_output=True)
    except OSError as error:
        raise UserEnvError(f'Could not start Docker while inspecting image {image!r}: {error}') from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or 'Docker returned no diagnostic text.'
        raise UserEnvError(f'Could not inspect Docker image {image!r}: {detail}')

    try:
        inspected_images = json.loads(completed.stdout)
        image_environment = inspected_images[0]['Config']['Env']
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise UserEnvError(f'Docker returned malformed metadata for image {image!r}.') from error

    if not isinstance(image_environment, list):
        raise UserEnvError(f'Docker image {image!r} has no readable environment metadata.')

    environment: dict[str, str] = {}
    for entry in image_environment:
        if isinstance(entry, str) and '=' in entry:
            name, value = entry.split('=', 1)
            environment[name] = value

    missing_variables = [name for name in IMAGE_METADATA_VARIABLES if not environment.get(name)]
    if missing_variables:
        raise UserEnvError(
            f'Docker image {image!r} does not provide the required identity variable(s): '
            f'{", ".join(missing_variables)}. Build it with the current robotics-dockers contract.'
        )

    user = validate_account_name(environment[IMAGE_METADATA_USER_VARIABLE], 'image user name')
    primary_group = validate_account_name(
        environment[IMAGE_METADATA_PRIMARY_GROUP_VARIABLE], 'image primary group name'
    )
    user_id = validate_numeric_id(environment[IMAGE_METADATA_USER_ID_VARIABLE], 'image UID')
    primary_group_id = validate_numeric_id(environment[IMAGE_METADATA_PRIMARY_GROUP_ID_VARIABLE], 'image primary GID')
    user_home = environment[IMAGE_METADATA_USER_HOME_VARIABLE]
    expected_home = f'/home/{user}'
    if user_home != expected_home:
        raise UserEnvError(
            f'Docker image {image!r} stores user home {user_home!r}; expected {expected_home!r} for user {user!r}.'
        )

    return ImageUserInfo(
        user=user, user_id=user_id, user_home=user_home, primary_group=primary_group, primary_group_id=primary_group_id
    )


def update_user_env(output_path: Path, user_info: ImageUserInfo) -> None:
    """Update all image identity values while preserving machine-specific settings."""
    output_path = output_path.expanduser().resolve()
    if output_path.exists() and not output_path.is_file():
        raise UserEnvError(f"Environment output path '{output_path}' exists and is not a regular file.")

    output_mode = output_path.stat().st_mode & 0o777 if output_path.exists() else 0o664
    existing_lines = output_path.read_text().splitlines(keepends=True) if output_path.exists() else []
    managed_values = user_info.as_compose_environment()
    # Compose .env files use NAME=VALUE assignments. A colon belongs to YAML,
    # not to this file format, so it must not be treated as a managed entry.
    assignment_pattern = re.compile(rf'^\s*({"|".join(re.escape(name) for name in COMPOSE_IDENTITY_VARIABLES)})\s*=')
    occurrences = dict.fromkeys(COMPOSE_IDENTITY_VARIABLES, 0)
    updated_lines: list[str] = []

    for line in existing_lines:
        match = assignment_pattern.match(line)
        if match is None:
            updated_lines.append(line)
            continue

        name = match.group(1)
        occurrences[name] += 1
        if occurrences[name] > 1:
            raise UserEnvError(
                f"Environment file '{output_path}' defines managed variable '{name}' more than once. "
                'Remove the duplicate before updating the file.'
            )
        updated_lines.append(f'{name}={managed_values[name]}\n')

    missing_names = [name for name in COMPOSE_IDENTITY_VARIABLES if occurrences[name] == 0]
    if missing_names:
        managed_block = [
            '# Image identity read from the selected robotics-dockers image.\n',
            *(f'{name}={managed_values[name]}\n' for name in missing_names),
            '\n',
        ]
        updated_lines = managed_block + updated_lines

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=output_path.parent, prefix=f'.{output_path.name}.', delete=False
        ) as temporary_file:
            temporary_file.writelines(updated_lines)
            temporary_path = Path(temporary_file.name)
        # These files are intended to be reviewed and versioned with the
        # project. Preserve an existing mode; use the same group-writable mode
        # as the other generated project files when creating a new file.
        temporary_path.chmod(output_mode)
        os.replace(temporary_path, output_path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise UserEnvError(f"Could not update environment file '{output_path}': {error}") from error


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Read the development identity stored in a robotics-dockers image.', allow_abbrev=False
    )
    parser.add_argument('image', help='Docker image to inspect')
    parser.add_argument(
        '--output', type=Path, default=None, help='Create or update this environment file with the image identity'
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _create_parser().parse_args(argv)
    try:
        user_info = inspect_image_user(args.image)
        if args.output is not None:
            update_user_env(args.output, user_info)
            print(f"Updated '{args.output}' from Docker image '{args.image}'.")
        else:
            for name, value in user_info.as_compose_environment().items():
                print(f'{name}={value}')
    except UserEnvError as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
