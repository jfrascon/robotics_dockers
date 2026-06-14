from importlib import resources

from robotics_dockers.config import DockerContextConfig, resolve_config
from robotics_dockers.generator import _create_items_to_install


def test_all_referenced_resources_are_packaged() -> None:
    resolved_config = resolve_config(DockerContextConfig('developer', 'jazzy', 'local/ros-test:latest'))
    package_resources = resources.files('robotics_dockers.resources')

    for spec in _create_items_to_install(resolved_config).values():
        source_name = spec[0]
        if source_name is not None:
            assert package_resources.joinpath(str(source_name)).exists(), source_name


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
