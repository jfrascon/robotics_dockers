from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from robotics_dockers.resources.robotics_dockers_user_env import (
    ImageUserInfo,
    UserEnvError,
    inspect_image_user,
    update_user_env,
    validate_account_name,
    validate_numeric_id,
)


def _user_info(user_id: int = 1000, primary_group_id: int = 1001) -> ImageUserInfo:
    return ImageUserInfo(
        user='developer',
        user_id=user_id,
        user_home='/home/developer',
        primary_group='robotics',
        primary_group_id=primary_group_id,
    )


def test_identity_input_validation_normalizes_decimal_ids() -> None:
    assert validate_account_name(' developer ', 'user') == 'developer'
    assert validate_numeric_id('00001000', 'UID') == 1000

    with pytest.raises(UserEnvError, match='only decimal digits'):
        validate_numeric_id('10x', 'UID')
    with pytest.raises(UserEnvError, match='between 1000 and 4294967294'):
        validate_numeric_id('999', 'UID')
    with pytest.raises(UserEnvError, match='Invalid user'):
        validate_account_name('Bad.User', 'user')


def test_inspect_image_user_reads_the_published_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    docker_output = json.dumps(
        [
            {
                'Config': {
                    'Env': [
                        'PATH=/usr/bin',
                        'ROBOTICS_DOCKERS_USER=developer',
                        'ROBOTICS_DOCKERS_USER_ID=01000',
                        'ROBOTICS_DOCKERS_USER_HOME=/home/developer',
                        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP=robotics',
                        'ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID=01001',
                    ]
                }
            }
        ]
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=['docker'], returncode=0, stdout=docker_output, stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    assert inspect_image_user('local/image:latest') == _user_info()


def test_inspect_image_user_rejects_an_image_without_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=['docker'], returncode=0, stdout='[{"Config":{"Env":[]}}]', stderr='')

    monkeypatch.setattr(subprocess, 'run', fake_run)

    with pytest.raises(UserEnvError, match='does not provide the required identity'):
        inspect_image_user('legacy/image:latest')


def test_update_user_env_creates_versionable_identity_file(tmp_path: Path) -> None:
    env_file = tmp_path / 'env_files/production.env'
    update_user_env(env_file, _user_info())

    contents = env_file.read_text()
    assert 'IMAGE_USER=developer' in contents
    assert 'IMAGE_USER_ID=1000' in contents
    assert 'IMAGE_USER_HOME=/home/developer' in contents
    assert 'IMAGE_USER_PRIMARY_GROUP=robotics' in contents
    assert 'IMAGE_USER_PRIMARY_GROUP_ID=1001' in contents
    assert 'HOST_WORKSPACE' not in contents
    assert 'DISPLAY' not in contents
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o664


def test_update_user_env_preserves_host_values_comments_and_mode(tmp_path: Path) -> None:
    env_file = tmp_path / 'production.env'
    env_file.write_text(
        '# Keep this local comment.\n'
        'IMAGE_USER=developer\n'
        'IMAGE_USER_ID=1000\n'
        'IMAGE_USER_HOME=/home/developer\n'
        'IMAGE_USER_PRIMARY_GROUP=robotics\n'
        'IMAGE_USER_PRIMARY_GROUP_ID=1001\n'
        'HOST_WORKSPACE=/srv/robot/workspace\n'
        'DISPLAY=:1\n'
    )
    env_file.chmod(0o640)

    update_user_env(env_file, _user_info(user_id=2000, primary_group_id=2001))

    assert env_file.read_text() == (
        '# Keep this local comment.\n'
        'IMAGE_USER=developer\n'
        'IMAGE_USER_ID=2000\n'
        'IMAGE_USER_HOME=/home/developer\n'
        'IMAGE_USER_PRIMARY_GROUP=robotics\n'
        'IMAGE_USER_PRIMARY_GROUP_ID=2001\n'
        'HOST_WORKSPACE=/srv/robot/workspace\n'
        'DISPLAY=:1\n'
    )
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o640


def test_update_user_env_rejects_duplicate_managed_values(tmp_path: Path) -> None:
    env_file = tmp_path / '.env'
    env_file.write_text('IMAGE_USER_ID=1000\nIMAGE_USER_ID=2000\n')

    with pytest.raises(UserEnvError, match='more than once'):
        update_user_env(env_file, _user_info())

    assert env_file.read_text() == 'IMAGE_USER_ID=1000\nIMAGE_USER_ID=2000\n'
