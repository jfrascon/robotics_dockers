import subprocess
from importlib import resources

from robotics_dockers.config import DockerContextConfig, resolve_config
from robotics_dockers.generator import _create_items_to_install

BASH_RESOURCE_FILES = (
    'bashrc_user',
    'colcon_mixin_metadata.sh',
    'deduplicate_path',
    'entrypoint_user.sh',
    'entrypoint.sh.j2',
    'env.rc',
    'install_base_system.sh',
    'install_extra_pkgs.sh',
    'install_gh.sh',
    'install_mesa_packages.sh',
    'install_pkgs',
    'install_ros2.sh',
    'ros.rc',
    'ros2build',
    'rosdep_init_update_install.sh',
    'skip_rosdep_keys',
)

TEXT_RESOURCE_FILES = ('rosdep_skip_keys.txt',)


def test_all_referenced_resources_are_packaged() -> None:
    resolved_config = resolve_config(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest'))
    package_resources = resources.files('robotics_dockers.resources')

    for spec in _create_items_to_install(resolved_config).values():
        source_name = spec[0]
        if source_name is not None:
            assert package_resources.joinpath(str(source_name)).exists(), source_name


def test_text_resources_are_packaged() -> None:
    package_resources = resources.files('robotics_dockers.resources')

    for resource_name in TEXT_RESOURCE_FILES:
        assert package_resources.joinpath(resource_name).is_file(), resource_name


def test_bash_resources_parse_successfully() -> None:
    package_resources = resources.files('robotics_dockers.resources')

    for script_name in BASH_RESOURCE_FILES:
        script = package_resources.joinpath(script_name)
        result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True, check=False)

        assert result.returncode == 0, f'{script_name}: {result.stderr}'


def test_colcon_setup_recreates_default_repositories_silently() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    script = package_resources.joinpath('colcon_mixin_metadata.sh').read_text()

    assert 'colcon mixin remove default >/dev/null 2>&1 || true' in script
    assert 'colcon mixin add default' in script
    assert 'colcon metadata remove default >/dev/null 2>&1 || true' in script
    assert 'colcon metadata add default' in script


def test_rosdep_setup_preserves_existing_default_sources() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    script = package_resources.joinpath('rosdep_init_update_install.sh').read_text()

    assert 'rm --verbose --force "${rosdep_sources_dir}/20-default.list"' not in script
    assert 'rosdep_default_sources="${rosdep_sources_dir}/20-default.list"' in script
    assert 'if [ -f "${rosdep_default_sources}" ]; then' in script
    assert 'rosdep init || handle_error 1 "rosdep init failed"' in script


def test_bashrc_user_does_not_set_xdg_environment() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    bashrc_user = package_resources.joinpath('bashrc_user').read_text()

    assert 'XDG_CACHE_HOME' not in bashrc_user
    assert 'XDG_CONFIG_HOME' not in bashrc_user
    assert 'XDG_DATA_HOME' not in bashrc_user
    assert 'XDG_STATE_HOME' not in bashrc_user
    assert 'XDG_RUNTIME_DIR' not in bashrc_user


def test_bashrc_user_sources_env_rc() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    bashrc_user = package_resources.joinpath('bashrc_user').read_text()

    assert '[ -f "${HOME}/.env.rc" ] && . "${HOME}/.env.rc"' in bashrc_user
    assert 'reload_envrc()' in bashrc_user
    assert 'unset ROBOTICS_DOCKERS_ENV_LOADED' in bashrc_user
    assert '[ -f "${HOME}/.bash_aliases_user" ] && . "${HOME}/.bash_aliases_user"' in bashrc_user


def test_env_rc_sources_ros_rc_once() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    env_rc = package_resources.joinpath('env.rc').read_text()

    assert 'must be sourced, not executed' in env_rc
    assert 'ROBOTICS_DOCKERS_ENV_LOADED' in env_rc
    assert 'export ROBOTICS_DOCKERS_ENV_LOADED=1' in env_rc
    assert 'export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"' in env_rc
    assert 'ensure_default_user_dir "${HOME}/.cache"' in env_rc
    assert 'chmod 755 "${dir}"' in env_rc
    assert 'warn_if_xdg_outside_home XDG_CACHE_HOME "${XDG_CACHE_HOME}"' in env_rc
    assert '[[ ":${PATH}:" != *":${HOME}/.local/bin:"* ]]' in env_rc
    assert '[ -f "${HOME}/.ros.rc" ] && . "${HOME}/.ros.rc"' in env_rc


def test_entrypoint_user_sources_env_rc_then_execs_command() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_user = package_resources.joinpath('entrypoint_user.sh').read_text()

    assert '[ -f "${HOME}/.env.rc" ] && . "${HOME}/.env.rc" || exit 1' in entrypoint_user
    assert 'exec "$@"' in entrypoint_user


def test_ros_rc_sets_up_ros_shell_environment() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    ros_rc = package_resources.joinpath('ros.rc').read_text()

    assert 'source_base_ros_setup()' in ros_rc
    assert 'CONTAINER_ROS_WORKSPACE is not set; falling back to base ROS setup' in ros_rc
    assert '${CONTAINER_ROS_WORKSPACE}/install/setup.bash' in ros_rc
    assert '/usr/share/colcon_argcomplete/hook/colcon-argcomplete.bash' in ros_rc
