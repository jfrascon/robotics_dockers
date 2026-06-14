# Refactor architecture

This document explains the new package layout introduced by the refactor.

## Visual diagram

Open `docs/refactor-architecture.drawio` with draw.io or diagrams.net. The diagram shows the two public entry points, the internal generation flow, the packaged resources, and the generated Docker context.

## Public entry points

### `pyproject.toml`

This file now describes `robotics-dockers` as an installable Python package. It declares the package metadata, the runtime dependency on `jinja2`, the optional development dependencies, the Hatchling build backend, and the console command:

```toml
robotics-dockers = "robotics_dockers.cli:main"
```

That entry point is what makes this command available after installation:

```bash
robotics-dockers create ...
```

### `src/robotics_dockers/cli.py`

This file owns the command-line interface. It uses `argparse`, defines the `create` subcommand, converts CLI arguments into `DockerContextConfig`, calls `generate_docker_context`, prints the generated context directory, and converts known package errors into user-readable CLI errors.

It intentionally does not contain Docker generation logic. Its job is only to translate terminal input into the Python API.

### `src/robotics_dockers/__main__.py`

This file allows the package to be executed as a Python module:

```bash
python -m robotics_dockers create ...
```

It delegates directly to `robotics_dockers.cli.main()`.

### `src/robotics_dockers/__init__.py`

This file defines the small public Python API exported by the package:

```python
from robotics_dockers import DockerContextConfig, DockerContextResult, generate_docker_context
```

Other projects, such as `ros_project_creator`, should import from here instead of importing internal modules directly.

## Core package

### `src/robotics_dockers/config.py`

This file defines the data model and validation rules for a Docker context generation request.

It contains:

- `DockerContextConfig`: the user-facing configuration object.
- `DockerContextResult`: the result returned after generation.
- `ResolvedDockerContextConfig`: the internal normalized form used by the generator.
- `ROS_DISTROS`: the mapping between ROS 2 distributions and Ubuntu base versions.
- validation for Docker image names, ROS distributions, and Linux user names.

This separation matters because both the CLI and future Python callers need the same defaults and validation rules.

### `src/robotics_dockers/generator.py`

This file contains the actual generation logic.

It takes a `DockerContextConfig`, resolves and validates it, creates the output directory, copies packaged resources, renders Jinja templates, sets executable permissions where needed, and returns a `DockerContextResult`.

The main function is:

```python
generate_docker_context(config)
```

This is the function that `ros_project_creator` should eventually call instead of copying Docker templates or shelling out to a script.

### `src/robotics_dockers/errors.py`

This file defines package-specific exceptions:

- `RoboticsDockersError`
- `InvalidDockerImageNameError`
- `InvalidImageUserError`
- `InvalidRosDistroError`
- `MissingResourceError`

The CLI catches `RoboticsDockersError` and prints clean error messages. Python callers can catch the same base class or the more specific subclasses.

## Packaged resources

### `src/robotics_dockers/resources/`

This directory contains the Docker build context templates and support scripts that used to live under `builder/`.

Important files include:

- `Dockerfile.j2`: template for the generated Dockerfile.
- `build.j2`: template for the generated `build.py`.
- `docker-compose.j2`: template for the generated `docker-compose-dev.yaml`.
- `entrypoint.sh`: container entrypoint script.
- `entrypoint.d/`: startup scripts executed by the entrypoint.
- `install_base_system.sh`: base system dependency installation.
- `install_extra_pkgs.sh`: installs user-customizable extra packages.
- `install_ros2.sh`: ROS 2 installation.
- `rosdep_init_update_install.sh`: rosdep setup and package dependency installation.
- `ros2build`: wrapper around `colcon build`.
- `extra.d/`: files users can edit after generation to add apt, Python, or Rust packages.
- `examples/`: reference scripts for Mesa driver variants.

The generator reads these resources through `importlib.resources`, so they work both from a source checkout and from an installed wheel.

## Generated output

Running:

```bash
robotics-dockers create developer jazzy local/ros:latest --output ./docker
```

creates a Docker context like:

```text
docker/
  Dockerfile
  build.py
  docker-compose-dev.yaml
  .resources/
    entrypoint.sh
    entrypoint.d/
    extra.d/
    install_base_system.sh
    install_extra_pkgs.sh
    install_ros.sh
    rosbuild
    rosdep_init_update_install.sh
    ...
```

The generated output intentionally keeps the same high-level behavior as the old script-based tool. The refactor changes how the tool is packaged and called, not what kind of Docker context it produces.

## Tests

### `tests/test_generator.py`

These tests cover the Python API. They verify that `generate_docker_context` creates the expected files, applies ROS distro defaults, includes NVIDIA-specific resources only when requested, and rejects invalid input.

### `tests/test_cli.py`

This test covers the module CLI:

```bash
python -m robotics_dockers create ...
```

It verifies that the command exits successfully and creates the main Docker context files.

### `tests/test_resources.py`

This test verifies that every resource referenced by the generator exists inside the package. It protects against accidentally renaming or omitting a template or support script during future refactors.

## Documentation and license

### `README.md`

The README now documents the installable package workflow, the `robotics-dockers create` CLI, and the Python API. It no longer presents `builder/create_docker_files.py` as the public interface.

### `LICENSE`

The project now includes an MIT license so it is ready for a future public release.
