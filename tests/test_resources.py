import os
import subprocess
from importlib import resources

from robotics_dockers.config import DockerContextConfig, resolve_config
from robotics_dockers.generator import _create_items_to_install

BASH_RESOURCE_FILES = (
    'bashrc_user',
    'colcon_mixin_metadata.sh',
    'deduplicate_path',
    'entrypoint_root.sh.j2',
    'entrypoint_user.sh',
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
    assert 'ensure_xdg_user_dir XDG_CACHE_HOME "${XDG_CACHE_HOME}"' in env_rc
    assert 'ensure_default_user_dir "${HOME}/.local/bin"' in env_rc
    assert 'chmod 755 "${dir}"' in env_rc
    assert 'Warning: ${name} points outside HOME' in env_rc
    assert '[[ ":${PATH}:" != *":${HOME}/.local/bin:"* ]]' in env_rc
    assert '[ -f "${HOME}/.ros.rc" ] && . "${HOME}/.ros.rc"' in env_rc


def test_env_rc_creates_custom_xdg_dirs_inside_home(tmp_path) -> None:
    package_resources = resources.files('robotics_dockers.resources')
    env_rc = package_resources.joinpath('env.rc')
    tmp_path.joinpath('.ros.rc').write_text('return 0\n')
    env = os.environ.copy()
    env.pop('ROBOTICS_DOCKERS_ENV_LOADED', None)
    env.update(
        {
            'HOME': str(tmp_path),
            'XDG_CACHE_HOME': str(tmp_path / 'custom-cache'),
            'XDG_CONFIG_HOME': str(tmp_path / 'custom-config'),
            'XDG_DATA_HOME': str(tmp_path / 'custom-data'),
            'XDG_STATE_HOME': str(tmp_path / 'custom-state'),
        }
    )

    result = subprocess.run(
        [
            'bash',
            '-c',
            '. "$1" && test -d "$XDG_CACHE_HOME" && test -d "$XDG_CONFIG_HOME" '
            '&& test -d "$XDG_DATA_HOME" && test -d "$XDG_STATE_HOME" '
            '&& [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]',
            'bash',
            str(env_rc),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_entrypoint_user_sources_env_rc_then_execs_command() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_user = package_resources.joinpath('entrypoint_user.sh').read_text()

    assert 'if [ ! -f "${HOME}/.env.rc" ]; then' in entrypoint_user
    assert "Error: required environment file '${HOME}/.env.rc' not found" in entrypoint_user
    assert '. "${HOME}/.env.rc" || {' in entrypoint_user
    assert "Error: failed to load required environment file '${HOME}/.env.rc'" in entrypoint_user
    assert 'exec "$@"' in entrypoint_user


def test_root_entrypoint_normalizes_decimal_ids_without_arithmetic_overflow() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_root = package_resources.joinpath('entrypoint_root.sh.j2').read_text()

    uid_format_check = '[[ ${HOST_UID} =~ ^[0-9]+$ ]]'
    uid_normalization = 'HOST_UID="$(normalize_decimal_id "${HOST_UID}")"'
    uid_maximum_check = '[ "${HOST_UID}" -gt "${MAX_USER_GROUP_ID}" ]'
    uid_minimum_check = '[ "${HOST_UID}" -lt 1000 ]'
    gid_format_check = '[[ ${HOST_UPGID} =~ ^[0-9]+$ ]]'
    gid_normalization = 'HOST_UPGID="$(normalize_decimal_id "${HOST_UPGID}")"'
    gid_maximum_check = '[ "${HOST_UPGID}" -gt "${MAX_USER_GROUP_ID}" ]'
    gid_minimum_check = '[ "${HOST_UPGID}" -lt 1000 ]'

    assert entrypoint_root.index(uid_format_check) < entrypoint_root.index(uid_normalization)
    assert entrypoint_root.index(uid_normalization) < entrypoint_root.index(uid_maximum_check)
    assert entrypoint_root.index(uid_maximum_check) < entrypoint_root.index(uid_minimum_check)
    assert entrypoint_root.index(gid_format_check) < entrypoint_root.index(gid_normalization)
    assert entrypoint_root.index(gid_normalization) < entrypoint_root.index(gid_maximum_check)
    assert entrypoint_root.index(gid_maximum_check) < entrypoint_root.index(gid_minimum_check)
    assert 'MAX_USER_GROUP_ID=4294967294' in entrypoint_root
    assert '10#' not in entrypoint_root
    assert 'greater than or equal to 1000' in entrypoint_root
    assert '-le 1000' not in entrypoint_root


def test_root_entrypoint_requires_canonical_decimal_ids_in_passwd() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_root = package_resources.joinpath('entrypoint_root.sh.j2').read_text()

    assert '[[ ${image_main_user_id} =~ ^[1-9][0-9]*$ ]]' in entrypoint_root
    assert '[[ ${image_main_user_pri_group_id} =~ ^[1-9][0-9]*$ ]]' in entrypoint_root
    assert 'image_main_user_id="$((10#${image_main_user_id}))"' not in entrypoint_root
    assert 'image_main_user_pri_group_id="$((10#${image_main_user_pri_group_id}))"' not in entrypoint_root


def test_root_entrypoint_log_is_root_owned_and_world_readable() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_root = package_resources.joinpath('entrypoint_root.sh.j2').read_text()

    assert 'install --directory --mode 755 --owner 0 --group 0 "${LOG_DIR}"' in entrypoint_root
    assert 'rm --force -- "${LOG_FILE}"' in entrypoint_root
    assert 'install --mode 644 --owner 0 --group 0 /dev/null "${LOG_FILE}"' in entrypoint_root
    assert '[ -L "${LOG_DIR}" ]' in entrypoint_root


def test_root_entrypoint_reads_only_the_required_executing_identity() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_root = package_resources.joinpath('entrypoint_root.sh.j2').read_text()

    assert 'entrypoint_user_id="$(id --user 2>/dev/null)"' in entrypoint_root
    assert 'entrypoint_primary_group_id="$(id --group 2>/dev/null)"' in entrypoint_root
    assert 'entrypoint_user_entry' not in entrypoint_root
    assert 'entrypoint_user_name' not in entrypoint_root
    assert 'entrypoint_user_primary_group_id' not in entrypoint_root
    assert 'entrypoint_user_primary_group_name' not in entrypoint_root


def test_root_entrypoint_documents_and_uses_single_usermod_strategy() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    entrypoint_root = package_resources.joinpath('entrypoint_root.sh.j2').read_text()

    assert '# Decision matrix' in entrypoint_root
    assert 'groupmod --gid because that command would change the primary GID' in entrypoint_root
    assert '# How usermod changes the account and home ownership' in entrypoint_root
    assert '# Mounts below the home' in entrypoint_root
    assert '# Failure limits' in entrypoint_root
    assert 'generate_preserved_group_name()' in entrypoint_root
    assert 'candidate="rd_old_${old_group_id}"' in entrypoint_root
    assert 'if ! usermod "${usermod_options[@]}" "${IMAGE_MAIN_USER}"; then' in entrypoint_root
    assert 'exec setpriv "${setpriv_options[@]}" env' in entrypoint_root
    assert 'exec gosu ' not in entrypoint_root
    assert 'groupmod --gid "' not in entrypoint_root
    assert 'chown_home_without_crossing_mounts' not in entrypoint_root
    assert 'generate_unique_name' not in entrypoint_root
    assert 'find_free_id' not in entrypoint_root


def test_base_system_installs_setpriv_provider_without_gosu() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    install_base_system = package_resources.joinpath('install_base_system.sh').read_text()

    assert '    util-linux\n' in install_base_system
    assert '    gosu\n' not in install_base_system


def test_ros_rc_sets_up_ros_shell_environment() -> None:
    package_resources = resources.files('robotics_dockers.resources')
    ros_rc = package_resources.joinpath('ros.rc').read_text()

    assert 'source_base_ros_setup()' in ros_rc
    assert 'CONTAINER_ROS_WORKSPACE is not set; falling back to base ROS setup' in ros_rc
    assert '${CONTAINER_ROS_WORKSPACE}/install/setup.bash' in ros_rc
    assert '/usr/share/colcon_argcomplete/hook/colcon-argcomplete.bash' in ros_rc
