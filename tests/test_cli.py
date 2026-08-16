import subprocess
import sys
from pathlib import Path

import pytest


def _new_command(output: Path) -> list[str]:
    return [
        sys.executable,
        '-m',
        'robotics_dockers',
        'new',
        'developer',
        '01000',
        '01001',
        'jazzy',
        'local/ros-test:latest',
        '--output',
        str(output),
    ]


def test_python_module_cli_creates_context(tmp_path: Path) -> None:
    command = _new_command(tmp_path)
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    assert 'Created ROS 2 Docker files.' in completed.stdout
    assert f'Output directory: {tmp_path.resolve()}' in completed.stdout
    assert 'Development user:  developer (1000)' in completed.stdout
    assert 'Primary group:     developer (1001)' in completed.stdout
    assert 'Host NVIDIA:       disabled' in completed.stdout
    assert tmp_path.joinpath('Dockerfile').is_file()
    assert tmp_path.joinpath('Dockerfile.update-user').is_file()
    assert tmp_path.joinpath('build.py').is_file()
    assert tmp_path.joinpath('docker-compose-dev.yaml').is_file()


def test_python_module_cli_accepts_group_and_nvidia(tmp_path: Path) -> None:
    command = _new_command(tmp_path)
    command[command.index('--output') : command.index('--output')] = ['--group', 'robotics', '--nvidia']
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    assert 'Primary group:     robotics (1001)' in completed.stdout
    assert 'Host NVIDIA:       enabled' in completed.stdout


def test_python_module_cli_help_distinguishes_user_and_group_names() -> None:
    completed = subprocess.run(
        [sys.executable, '-m', 'robotics_dockers', 'new', '--help'], check=False, text=True, capture_output=True
    )

    assert completed.returncode == 0, completed.stderr
    assert 'user-name user-id group-id ros-distro img-id' in completed.stdout
    assert '--group GROUP_NAME' in completed.stdout


@pytest.mark.parametrize('missing_count,missing_name', [(1, 'img-id'), (2, 'ros-distro'), (3, 'group-id')])
def test_python_module_cli_requires_complete_positionals(tmp_path: Path, missing_count: int, missing_name: str) -> None:
    command = _new_command(tmp_path)
    del command[5 : 5 + missing_count]
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 2
    assert missing_name in completed.stderr


@pytest.mark.parametrize('obsolete_option', ['--image-main-user', '-u', '--user', '--uid', '--gid', '-b', '-o'])
def test_python_module_cli_rejects_obsolete_option(tmp_path: Path, obsolete_option: str) -> None:
    command = _new_command(tmp_path) + [obsolete_option, 'legacy']
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 2
    assert 'unrecognized arguments' in completed.stderr


@pytest.mark.parametrize('field,value', [('user-id', '999'), ('group-id', '4294967295'), ('user-id', '10x')])
def test_python_module_cli_rejects_invalid_identity_values(tmp_path: Path, field: str, value: str) -> None:
    command = _new_command(tmp_path)
    positional_index = {'user-id': 5, 'group-id': 6}[field]
    command[positional_index] = value
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 1
    assert 'Error:' in completed.stderr
