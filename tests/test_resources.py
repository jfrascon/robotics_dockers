import os
import subprocess
from importlib import resources
from pathlib import Path

import pytest

from robotics_dockers.config import DockerContextConfig, resolve_config
from robotics_dockers.generator import _create_items_to_install

BASH_RESOURCE_FILES = (
    'bashrc_user',
    'colcon_mixin_metadata.sh',
    'configure_image_user.sh',
    'configure_sudo.sh',
    'entrypoint_user.sh',
    'env.rc',
    'extra.d/rust/install.sh.example',
    'install_base_system.sh',
    'install_extra_apt.sh',
    'install_gh.sh',
    'install_mesa_packages.sh',
    'install_pkgs',
    'install_ros2.sh',
    'install_user_extras.sh',
    'ros.rc',
    'ros2build',
    'rosdep_init_update_install.sh',
    'skip_rosdep_keys',
    'update_image_user.sh',
    'user_preparation.d/01-delete-ubuntu-user.sh.example',
    'user_preparation.d/02-reuse-ubuntu-user.sh.example',
)

FAILING_COLCON_REPOSITORY_COMMANDS = ('mixin list', 'mixin remove default', 'metadata list', 'metadata remove default')


def _resolved_config():
    return resolve_config(DockerContextConfig(ros_distro='jazzy', img_id='local/ros-test:latest'))


def test_all_referenced_resources_are_packaged() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    for spec in _create_items_to_install(_resolved_config()).values():
        source_name = spec[0]
        if source_name is not None:
            assert package_resources.joinpath(str(source_name)).exists(), source_name


def test_bash_resources_parse_successfully() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    for script_name in BASH_RESOURCE_FILES:
        script = package_resources.joinpath(script_name)
        result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, f'{script_name}: {result.stderr}'


def test_removed_runtime_identity_resources_are_absent() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    obsolete_resources = (
        'entrypoint_root.sh.j2',
        'install_extra_pkgs.sh',
        'extra.d/apt_packages.sh',
        'extra.d/requirements.txt',
        'extra.d/rust_packages.txt',
    )
    for relative_path in obsolete_resources:
        assert not package_resources.joinpath(relative_path).exists(), relative_path


def test_python_requirements_start_with_supported_default_tools() -> None:
    requirement_lines = [
        line.strip()
        for line in resources.files('robotics_dockers.resources')
        .joinpath('extra.d/python/requirements.txt')
        .read_text()
        .splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert requirement_lines == [
        'argcomplete',
        'ruff',
        'cmake-format',
        'pre-commit',
        'jinja2',
        'python-rapidjson',
        'uv',
    ]


def test_env_rc_loads_paths_ros_and_ordered_hooks(tmp_path: Path) -> None:
    package_resources = resources.files('robotics_dockers.resources')
    env_rc = package_resources.joinpath('env.rc')
    tmp_path.joinpath('.local/bin').mkdir(parents=True)
    tmp_path.joinpath('.cargo/bin').mkdir(parents=True)
    tmp_path.joinpath('.env.d').mkdir()
    tmp_path.joinpath('.ros.rc').write_text('printf "ros\\n" >>"$HOME/load-order"\n')
    tmp_path.joinpath('.env.d/20-second.rc').write_text('printf "second\\n" >>"$HOME/load-order"\n')
    tmp_path.joinpath('.env.d/10-first.rc').write_text('printf "first\\n" >>"$HOME/load-order"\n')
    environment = os.environ.copy()
    environment.update({'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin'})
    environment.pop('ROBOTICS_DOCKERS_ENV_LOADED', None)

    completed = subprocess.run(
        [
            'bash',
            '-c',
            '. "$1" && printf "PATH=%s\\nMARKER=%s\\n" "$PATH" "$ROBOTICS_DOCKERS_ENV_LOADED"',
            'bash',
            str(env_rc),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert f'PATH={tmp_path}/.local/bin:{tmp_path}/.cargo/bin:/usr/bin:/bin' in completed.stdout
    assert 'MARKER=1' in completed.stdout
    assert tmp_path.joinpath('load-order').read_text().splitlines() == ['ros', 'first', 'second']


def test_env_rc_does_not_mark_failed_hook_as_loaded(tmp_path: Path) -> None:
    env_rc = resources.files('robotics_dockers.resources').joinpath('env.rc')
    tmp_path.joinpath('.env.d').mkdir()
    tmp_path.joinpath('.env.d/10-fail.rc').write_text('return 7\n')
    environment = os.environ.copy()
    environment.update({'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin'})

    completed = subprocess.run(
        [
            'bash',
            '-c',
            '. "$1"; rc=$?; printf "RC=%s MARKER=%s\\n" "$rc" "${ROBOTICS_DOCKERS_ENV_LOADED:-}"',
            'bash',
            str(env_rc),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert 'RC=1 MARKER=' in completed.stdout
    assert 'environment hook' in completed.stderr


def test_env_rc_preserves_existing_xdg_directory_mode(tmp_path: Path) -> None:
    env_rc = resources.files('robotics_dockers.resources').joinpath('env.rc')
    cache_directory = tmp_path / '.cache'
    cache_directory.mkdir()
    cache_directory.chmod(0o700)
    environment = os.environ.copy()
    environment.update({'HOME': str(tmp_path), 'PATH': '/usr/bin:/bin'})

    completed = subprocess.run(
        ['bash', '-c', '. "$1"', 'bash', str(env_rc)], env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert cache_directory.stat().st_mode & 0o777 == 0o700


def test_bash_alias_helper_does_not_change_shell_globbing_options(tmp_path: Path) -> None:
    aliases = resources.files('robotics_dockers.resources').joinpath('bash_aliases_user')
    completed = subprocess.run(
        [
            'bash',
            '-c',
            'cd "$1" || exit 1; shopt -u nullglob dotglob; . "$2"; l. >/dev/null; '
            'if shopt -q nullglob || shopt -q dotglob; then exit 9; fi',
            'bash',
            str(tmp_path),
            str(aliases),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize(
    'wrapper_arguments,expected_option,expected_value',
    [
        (
            ['--cmake-args', '-DPROJECT_OPTION=ON', '--event-handlers', 'console_direct+'],
            '--event-handlers',
            'console_direct+',
        ),
        (['--mixin', 'debug', '--executor', 'sequential'], '--executor', 'sequential'),
    ],
)
def test_ros2build_processes_option_after_variable_length_values(
    tmp_path: Path, wrapper_arguments: list[str], expected_option: str, expected_value: str
) -> None:
    """The option after --cmake-args or --mixin must not be skipped."""
    ros2build = resources.files('robotics_dockers.resources').joinpath('ros2build')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    captured_arguments = tmp_path / 'colcon-arguments'
    fake_colcon = fake_bin / 'colcon'
    fake_colcon.write_text('#!/bin/bash\nprintf "%s\\0" "$@" >"${COLCON_ARGUMENTS}"\n')
    fake_colcon.chmod(0o755)
    environment = os.environ.copy()
    environment['COLCON_ARGUMENTS'] = str(captured_arguments)
    environment['PATH'] = f'{fake_bin}:/usr/bin:/bin'

    completed = subprocess.run(
        ['bash', str(ros2build), *wrapper_arguments], env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    actual_arguments = captured_arguments.read_bytes().split(b'\0')[:-1]
    expected_pair = [expected_option.encode(), expected_value.encode()]
    pair_index = actual_arguments.index(expected_pair[0])
    assert actual_arguments[pair_index : pair_index + 2] == expected_pair


@pytest.mark.parametrize('repositories_present', [False, True])
def test_colcon_setup_removes_default_only_when_it_exists(tmp_path: Path, repositories_present: bool) -> None:
    script = resources.files('robotics_dockers.resources').joinpath('colcon_mixin_metadata.sh')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    calls_file = tmp_path / 'colcon-calls'

    fake_id = fake_bin / 'id'
    fake_id.write_text('#!/bin/bash\nprintf "0\\n"\n')
    fake_id.chmod(0o755)

    fake_mkdir = fake_bin / 'mkdir'
    fake_mkdir.write_text('#!/bin/bash\nexit 0\n')
    fake_mkdir.chmod(0o755)

    fake_colcon = fake_bin / 'colcon'
    fake_colcon.write_text(
        """#!/bin/bash
printf '%s\n' "$*" >>"${COLCON_CALLS}"
if [ "$*" = "${COLCON_FAIL_COMMAND:-}" ]; then
    exit 7
fi
case "$*" in
"mixin list" | "metadata list")
    if [ "${COLCON_REPOSITORIES_PRESENT}" = "true" ]; then
        printf 'default: https://example.invalid/index.yaml\n'
    fi
    ;;
esac
"""
    )
    fake_colcon.chmod(0o755)

    environment = os.environ.copy()
    environment['COLCON_CALLS'] = str(calls_file)
    environment['COLCON_REPOSITORIES_PRESENT'] = 'true' if repositories_present else 'false'
    environment['PATH'] = f'{fake_bin}:/usr/bin:/bin'

    completed = subprocess.run(
        ['bash', str(script), 'root'], env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = calls_file.read_text().splitlines()
    assert ('mixin remove default' in calls) is repositories_present
    assert ('metadata remove default' in calls) is repositories_present


@pytest.mark.parametrize('failing_command', FAILING_COLCON_REPOSITORY_COMMANDS)
def test_colcon_setup_does_not_hide_repository_errors(tmp_path: Path, failing_command: str) -> None:
    script = resources.files('robotics_dockers.resources').joinpath('colcon_mixin_metadata.sh')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    calls_file = tmp_path / 'colcon-calls'

    for command_name, command_body in {
        'id': '#!/bin/bash\nprintf "0\\n"\n',
        'mkdir': '#!/bin/bash\nexit 0\n',
        'colcon': """#!/bin/bash
printf '%s\n' "$*" >>"${COLCON_CALLS}"
if [ "$*" = "${COLCON_FAIL_COMMAND}" ]; then
    exit 7
fi
case "$*" in
"mixin list" | "metadata list") printf 'default: https://example.invalid/index.yaml\n' ;;
esac
""",
    }.items():
        fake_command = fake_bin / command_name
        fake_command.write_text(command_body)
        fake_command.chmod(0o755)

    environment = os.environ.copy()
    environment['COLCON_CALLS'] = str(calls_file)
    environment['COLCON_FAIL_COMMAND'] = failing_command
    environment['PATH'] = f'{fake_bin}:/usr/bin:/bin'

    completed = subprocess.run(
        ['bash', str(script), 'root'], env=environment, capture_output=True, text=True, check=False
    )

    assert completed.returncode != 0
    assert failing_command in calls_file.read_text().splitlines()


def test_dockerfile_uses_separate_buildkit_mount_phases_without_manual_checksums() -> None:
    dockerfile = resources.files('robotics_dockers.resources').joinpath('Dockerfile.j2').read_text()

    assert 'https://docs.docker.com/build/cache/invalidation/' in dockerfile
    assert dockerfile.count('RUN --mount=type=bind') >= 8
    assert 'RESOURCES_CHECKSUM' not in dockerfile
    assert 'groupmod --gid' not in dockerfile
    assert 'apt-get autoremove' not in dockerfile

    # GitHub CLI is a general system tool. Keep it with the base tooling, after
    # the optional graphics-driver phase and before the ROS-specific phase.
    mesa_phase = dockerfile.index('source=.resources/install_mesa_packages.sh')
    github_cli_phase = dockerfile.index('source=.resources/install_gh.sh')
    ros_phase = dockerfile.index('source=.resources/install_ros.sh')
    assert mesa_phase < github_cli_phase < ros_phase


def test_user_preparation_examples_are_disabled_and_ordered() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    preparation = package_resources.joinpath('user_preparation.d')
    names = sorted(path.name for path in preparation.iterdir())

    assert names == ['01-delete-ubuntu-user.sh.example', '02-reuse-ubuntu-user.sh.example']
    assert all(name.endswith('.example') for name in names)


def test_runtime_entrypoint_is_root_owned_by_dockerfile_but_runs_as_development_user() -> None:
    dockerfile = resources.files('robotics_dockers.resources').joinpath('Dockerfile.j2').read_text()
    entrypoint = resources.files('robotics_dockers.resources').joinpath('entrypoint_user.sh').read_text()

    assert (
        'install --owner root --group root --mode 0755 /tmp/entrypoint_user.sh /usr/local/bin/entrypoint.sh'
        in dockerfile
    )
    assert dockerfile.rstrip().endswith('CMD ["bash"]')
    assert 'USER "${ROBOTICS_DOCKERS_USER}"\nWORKDIR "${ROBOTICS_DOCKERS_USER_HOME}"' in dockerfile
    assert 'exec "$@"' in entrypoint
    assert 'setpriv' not in entrypoint
    assert 'gosu' not in entrypoint


def test_install_pkgs_uses_one_real_apt_installation() -> None:
    script = resources.files('robotics_dockers.resources').joinpath('install_pkgs').read_text()

    assert 'apt-get --simulate' not in script
    assert script.count('apt-get --yes') == 1
    assert 'for package' not in script


def test_ros_environment_values_dependent_on_home_live_in_ros_rc() -> None:
    dockerfile = resources.files('robotics_dockers.resources').joinpath('Dockerfile.j2').read_text()
    ros_rc = resources.files('robotics_dockers.resources').joinpath('ros.rc').read_text()

    for variable in ('ROS_HOME', 'ROS_LOG_DIR', 'ROS_TEST_RESULTS_DIR'):
        assert f'{variable}=' not in dockerfile
        assert f'export {variable}=' in ros_rc
