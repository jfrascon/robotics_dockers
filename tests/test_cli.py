import subprocess
import sys
from pathlib import Path

import pytest


def _new_command(output: Path) -> list[str]:
    return [sys.executable, '-m', 'robotics_dockers', 'new', 'jazzy', 'local/ros-test:latest', '--output', str(output)]


def test_python_module_cli_creates_context(tmp_path: Path) -> None:
    command = _new_command(tmp_path)
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    assert 'Created ROS 2 Docker files.' in completed.stdout
    assert f'Output directory: {tmp_path.resolve()}' in completed.stdout
    assert 'Host NVIDIA:       disabled' in completed.stdout
    assert 'compose_files/docker-compose.yaml' in completed.stdout
    assert tmp_path.joinpath('Dockerfile').is_file()
    assert tmp_path.joinpath('Dockerfile_update_user').is_file()
    assert not tmp_path.joinpath('Dockerfile.update-user').exists()
    assert tmp_path.joinpath('build.py').is_file()
    assert tmp_path.joinpath('compose_files/docker-compose.yaml').is_file()
    assert not tmp_path.joinpath('docker-compose.yaml').exists()
    assert tmp_path.joinpath('robotics_dockers_user_env.py').is_file()
    assert tmp_path.joinpath('env_files/.gitkeep').is_file()
    assert not tmp_path.joinpath('.gitignore').exists()


def test_python_module_cli_accepts_nvidia(tmp_path: Path) -> None:
    command = _new_command(tmp_path)
    command[command.index('--output') : command.index('--output')] = ['--nvidia']
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 0, completed.stderr
    assert 'Host NVIDIA:       enabled' in completed.stdout


def test_python_module_cli_help_contains_only_shared_required_values() -> None:
    completed = subprocess.run(
        [sys.executable, '-m', 'robotics_dockers', 'new', '--help'], check=False, text=True, capture_output=True
    )

    assert completed.returncode == 0, completed.stderr
    assert 'ros-distro img-id' in completed.stdout
    assert 'user-name' not in completed.stdout
    assert '--group' not in completed.stdout
    assert '--workspace-mount' not in completed.stdout


@pytest.mark.parametrize('provided,missing_name', [(['jazzy'], 'img-id'), ([], 'ros-distro')])
def test_python_module_cli_requires_complete_positionals(provided: list[str], missing_name: str) -> None:
    command = [sys.executable, '-m', 'robotics_dockers', 'new', *provided]
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 2
    assert missing_name in completed.stderr


@pytest.mark.parametrize(
    'obsolete_option',
    ['--image-main-user', '-u', '--user', '--uid', '--gid', '--group', '--workspace-mount', '-b', '-o'],
)
def test_python_module_cli_rejects_obsolete_option(tmp_path: Path, obsolete_option: str) -> None:
    command = _new_command(tmp_path) + [obsolete_option, 'legacy']
    completed = subprocess.run(command, check=False, text=True, capture_output=True)

    assert completed.returncode == 2
    assert 'unrecognized arguments' in completed.stderr
