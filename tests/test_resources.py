import subprocess
from importlib import resources

from robotics_dockers.config import DockerContextConfig, resolve_config
from robotics_dockers.generator import _create_items_to_install

BASH_RESOURCE_FILES = (
    'check_entrypoint_d',
    'colcon_mixin_metadata.sh',
    'deduplicate_path',
    'entrypoint.sh',
    'entrypoint.d/98-nvidia-gpu-driver-check.sh',
    'entrypoint.d/99-uid-gid-adapt.sh',
    'install_base_system.sh',
    'install_extra_pkgs.sh',
    'install_mesa_packages.sh',
    'install_pkgs',
    'install_ros2.sh',
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
