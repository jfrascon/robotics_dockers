import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('output_option', ['--output', '-o'])
def test_python_module_cli_creates_context(tmp_path: Path, output_option: str) -> None:
    completed_process = subprocess.run(
        [
            sys.executable,
            '-m',
            'robotics_dockers',
            'create',
            'developer',
            'jazzy',
            'local/ros-test:latest',
            output_option,
            str(tmp_path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed_process.returncode == 0, completed_process.stderr
    assert 'Created ROS 2 Docker files.' in completed_process.stdout
    assert f'Output directory: {tmp_path.resolve()}' in completed_process.stdout
    assert 'Image name:        local/ros-test:latest' in completed_process.stdout
    assert 'ROS distro:        jazzy' in completed_process.stdout
    assert 'Base image:        ubuntu:24.04' in completed_process.stdout
    assert 'Host NVIDIA:       disabled' in completed_process.stdout
    assert 'Generated:' in completed_process.stdout
    assert '- Dockerfile' in completed_process.stdout
    assert '- build.py' in completed_process.stdout
    assert '- docker-compose-dev.yaml' in completed_process.stdout
    assert 'Next steps:' in completed_process.stdout
    assert 'python3 build.py' in completed_process.stdout
    assert tmp_path.joinpath('Dockerfile').is_file()
    assert tmp_path.joinpath('build.py').is_file()
    assert tmp_path.joinpath('docker-compose-dev.yaml').is_file()
