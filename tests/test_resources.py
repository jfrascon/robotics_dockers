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
