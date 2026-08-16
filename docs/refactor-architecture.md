# Architecture

This document describes the current build-time identity architecture. It is a design contract, not a migration guide: generated contexts from the former runtime UID/GID adaptation model are unsupported and must be regenerated.

## Design goals

The implementation separates five responsibilities:

1. The Python package validates reusable context-generation input.
2. The generated `build.py` validates a local development identity and passes it to the Dockerfile.
3. Editable extension directories customize apt, Python, Rust and the user environment.
4. A small non-root entrypoint decides whether to load the configured development environment.
5. An optional derivative-image adapter changes only numeric identity when rebuilding the original image is impractical.

No normal container start modifies `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/gshadow` or home ownership. This removes root privilege dropping, runtime traversal of bind mounts and duplicate identity mechanisms from the normal path.

## Python package

### Public entry points

`pyproject.toml` publishes:

```toml
robotics-dockers = "robotics_dockers.cli:main"
```

`src/robotics_dockers/__main__.py` provides the equivalent module form:

```bash
python -m robotics_dockers new ros-distro img-id ...
```

`src/robotics_dockers/__init__.py` exports the stable Python API:

```python
from robotics_dockers import DockerContextConfig, DockerContextResult, generate_docker_context
```

### Configuration flow

`cli.py` owns argument parsing and presentation only. The `new` command accepts `ros-distro` and `img-id`, followed by optional long options. It constructs `DockerContextConfig`; the generator calls `resolve_config()` once before creating any output.

`resolve_config()`:

- validates the ROS distribution and image names;
- identifies whether the ROS package path comes from `build.py`, a context-relative path or a host path.

The returned `ResolvedDockerContextConfig` contains the complete shared build definition. `DockerContextResult.resolved_config` exposes the same object used for rendering, so the CLI and Python callers do not validate the configuration again merely to print a summary.

### Generation

`generator.py` maps each output path to one packaged source, optional Jinja context and executable flag. It uses `importlib.resources`, so generation works from a source checkout and an installed wheel.

Docker image names are validated here, before rendering. The generated `build.py` passes the already accepted tag to Docker and lets Docker report command-level errors; it does not carry a second copy of the image-name parser.

The generator produces empty extension directories deliberately. Their existence is part of the Dockerfile contract even when the user has no customization.

An explicit output directory must be absent or empty. The generator validates that condition before installing its first item, preventing a repeated generation command from partially overwriting user customizations.

## Generated context

```text
Dockerfile
Dockerfile_update_user
build.py
compose_files/
└── docker-compose.yaml
robotics_dockers_user_env.py
env_files/
└── .gitkeep
.resources/
├── configure_image_user.sh
├── configure_sudo.sh
├── entrypoint_user.sh
├── install_base_system.sh
├── install_extra_apt.sh
├── install_user_extras.sh
├── update_image_user.sh
├── user_preparation.d/
│   ├── 01-delete-ubuntu-user.sh.example
│   └── 02-reuse-ubuntu-user.sh.example
└── extra.d/
    ├── apt/
    │   ├── keyrings.d/
    │   ├── sources.d/
    │   └── packages.txt
    ├── env.d/
    ├── python/
    │   ├── requirements.txt
    │   └── install.d/
    └── rust/
        └── install.sh.example
```

There is no root entrypoint or runtime identity hook directory.

## Docker build sequence

The generated Dockerfile uses BuildKit bind mounts for inputs consumed by each phase. Mounted input changes participate in BuildKit cache invalidation and are not copied into the image layer. Keeping mounts phase-specific also prevents an apt customization from invalidating an unrelated Python phase.

`build.py` accepts `user-name user-id group-id [--group GROUP_NAME]`. It validates and normalizes the account, passes the five `ROBOTICS_DOCKERS_USER*` values as Docker build arguments, and requires the completed image metadata to equal those values. It never creates a Compose environment file. Identity selection occurs at build time so the generated and committed context remains independent of one developer's host IDs.

The phase order is:

1. Install the base system and the shared `install_pkgs` helper.
2. Install Mesa when host NVIDIA mode is disabled.
3. Install GitHub CLI as a general system tool.
4. Install ROS and rosdep configuration.
5. Apply editable apt repository and package configuration.
6. Publish identity environment variables and labels.
7. Run ordered account-preparation hooks and create or validate the exact user/group.
8. Run rosdep and colcon setup now that the home exists.
9. Install root-owned runtime commands and user-owned environment files individually.
10. Switch to the real development user and run Python/Rust extensions.
11. Return to root only to install and validate the named sudoers rule.
12. Set the final `USER`, `WORKDIR`, entrypoint and command.

System package cleanup remains in the same `RUN` phase that populated apt indexes. There is no `apt autoremove`, because automatically deciding that an inherited package is unused is inappropriate in a reusable development image.

## Account preparation contract

Preparation hooks run before the project identity exists. The Dockerfile rejects symbolic links, selects only regular top-level `*.sh` files and orders them with `LC_ALL=C`. Examples remain inert because their names do not end in `.sh`.

`configure_image_user.sh` repeats critical name, ID and home validation even though `build.py` already performed it. This makes a direct `docker build` fail safely.

Before its first account change, it counts local entries by:

- requested user name;
- requested UID;
- requested primary group name;
- requested primary GID.

The helper reads only the public local records relevant to the requested identity. `shadow-utils` validates and updates `/etc/shadow` and `/etc/gshadow`; reimplementing those checks in the project would duplicate the account-management commands without making them transactional.

Creation is permitted when the requested user name and UID are free and the primary group is either free or already matches the requested name and GID. Reusing an existing user additionally requires the exact primary group to exist: an orphaned primary GID is rejected rather than repaired implicitly. Existing records must agree on name, UID, primary GID, a real `/home/<user>` directory, `/bin/bash` and the requested home owner. Every other state is a collision delegated to an explicit preparation hook.

Before mutation, the helper detects whether the local `dialout` and `video` groups exist and rejects files or symbolic links at the known home-directory paths. For a new user it performs the same path check against `/etc/skel`, because `useradd --create-home` copies that content. After creation or reuse, the helper locks the password, adds the available supplementary groups, creates missing known directories, preserves the modes of existing directories and changes only their directory-entry owners. A small final check verifies the public user, primary group and home owner used by the generated image.

The separate `configure_sudo.sh` creates one nominal `NOPASSWD` rule, validates it with `visudo`, installs it as root:root mode 0440 and does not add the account to the `sudo` group.

## Extension contracts

### Apt

Only top-level regular `.asc`, `.gpg` and deb822 `.sources` files are accepted. Nested directories and links are rejected instead of being silently ignored. Files are never interpreted as root shell scripts. Each destination is checked before it is copied, and Docker discards the complete RUN layer if a later destination collides. Apt remains responsible for source parsing and signature verification through `apt-get update`.

`install_pkgs` sends the complete package request to one real apt call. A preliminary simulation is intentionally omitted because the real command performs the same resolution. Docker discards the build layer if apt fails; apt itself is not a rollback-capable transaction manager.

### Python and Rust

`install_user_extras.sh` must execute as a non-root UID with a real `HOME`. It installs pip requirements into `$HOME/.local`, executes ordered Python hooks and then runs only `rust/install.sh` when that exact regular file exists.

The project sudoers rule does not exist during this phase. This sequencing prevents accidental reliance on project-provided sudo but cannot neutralize privilege policy inherited from a base image.

Rust has no project-specific manifest. The user-owned script is the standard escape hatch because rustup/toolchain/application policy cannot be inferred safely across arbitrary base images.

### Runtime environment

Root installs `.env.rc`, `.ros.rc` and generated environment hooks with the final numeric owner. `.env.rc` is idempotent and sets its loaded marker only after all sources succeed.

Home-dependent ROS variables belong to `.ros.rc`, not global Docker `ENV`. Alternate runtime users therefore do not inherit paths into the development home.

## Runtime state machine

The image starts as the development user. The root-owned entrypoint compares the executing numeric identity with the build metadata:

```text
executing UID:GID == configured UID:GID
├── yes: export textual identity, validate XDG/NVIDIA, source .env.rc,
│        write user log, exec command
└── no:  exec command directly
```

The decision uses only the UID and primary GID. Supplementary groups from Compose `group_add` are orthogonal.

No `XDG_RUNTIME_DIR` is created by the entrypoint. Compose creates `/run/user/<uid>` as a private tmpfs; the entrypoint validates it when the variable is present. Direct Docker users may omit the variable.

Overriding Docker `ENTRYPOINT` bypasses this state machine. `docker exec` also does not rerun it. Overriding only `USER`, its primary group or `WORKDIR` has the independent behavior described in the README.

## Existing-image adapter

`Dockerfile_update_user` inherits a generated image and invokes `update_image_user.sh` as root. The helper requires the new environment metadata and refuses unknown/legacy source images. User name, group name and home are inherited from that metadata, so the reusable adapter does not embed a generation-time account.

Its validation stage checks:

- canonical source and target IDs;
- unique source records in `/etc/passwd` and `/etc/group`;
- exact source home owner;
- target UID and GID collisions;
- old-UID-owned files outside the home and mailbox.

When an existing group supplies the target GID, the helper requires that GID to identify exactly one public local group. `groupmod` remains responsible for validating and synchronizing `/etc/group` and `/etc/gshadow` during the rename.

When the primary GID changes, the old group is renamed to `rd_old_<gid>[_n]` without changing its numeric GID. A free target group is created, or the one group at the target GID is renamed to the expected primary group name. The helper then calls `usermod` zero or one time with the changed UID/GID options together.

`usermod` independently transforms matching old UID and old primary GID ownership inside the home. Files with a different UID or auxiliary GID retain that component. Files outside the home are never modified.

The helper receives the inherited source user name and home and verifies them before mutation. The derivative Dockerfile can therefore restore the validated textual `USER` and `WORKDIR` explicitly without redundant user-supplied build arguments.

Account files and a recursive ownership traversal are not an atomic operation. The helper validates predictable failures and checks postconditions, but does not roll back a partial failure. The derivative layer can be large.

## Compose boundaries

Compose contains no literal developer UID/GID and does not override `user:`. It requires `ROBOTICS_DOCKERS_USER_ID` and `ROBOTICS_DOCKERS_USER_PRIMARY_GROUP_ID` during interpolation. Compose definitions belong below `compose_files/`, while runtime-machine configuration belongs in a deliberately selected file below `env_files/`, not in the image build command. The standalone generator comments the `/workspace` bind mount by default because it does not know a project path; callers such as `ros_project_generator` can enable it explicitly. `/workspace` and `/datasets` keep editable host mounts away from the installed home environment.

`robotics_dockers_user_env.py` is the single image-inspection implementation used by `build.py` and available as a command. Without `--output` it prints the five validated identity variables. With `--output`, it atomically creates or updates all five variables and preserves unrelated settings, comments and permissions. The user supplies machine-specific paths and chooses the environment file explicitly with `docker compose --env-file`.

`group_add` supplies render-device access as a supplementary GID. NVIDIA device requests are rendered separately. `NET_ADMIN` remains commented because normal ROS networking does not require permission to reconfigure host/container interfaces.

Rootless Docker and daemon user-namespace remapping are outside this identity contract because they apply an additional host/container numeric translation.

## Tests

The suite is divided by responsibility:

- `test_cli.py`: required generation positionals and obsolete-option rejection;
- `test_generator.py`: rendered generic context, build-time identity arguments, absence of build-time environment-file writes, cache flags and configuration modes;
- `test_user_env.py`: image metadata parsing and non-destructive maintenance of deliberately selected environment files;
- `test_resources.py`: packaged resources, shell syntax, environment order and structural build contracts;
- `test_docker_functional.py`: real account collisions, Ubuntu preparation examples, sudoers, apt input validation, user hooks, adapter ownership and the generated entrypoint on an ephemeral Ubuntu-based test image.

The Docker matrix is derived from `ROS_DISTROS`, which is the same mapping used by generation. General functional scenarios run once per unique supported Ubuntu version. Entry-point tests build a small temporary image from each Ubuntu base and generated project files; those derived builds perform no package installation and make no further network request.

Each supported ROS–Ubuntu pair also runs against the smallest public `ros:<distro>-ros-core` image. The test verifies the image's Ubuntu version, the expected setup file and the real `.ros.rc` loading path. All public images are inspected first and pulled only when their exact tag is absent. Pull failures fail the suite; only a missing Docker CLI or unavailable daemon causes a skip. No private image is a test prerequisite.

The `docker`, `ros_image` and `full_build` pytest markers separate increasing costs. A `full_build` creates one complete generated development image and requires an explicit distribution, for example `pytest -m full_build --full-build-distro humble`. Without that option it is skipped, including when the whole pytest suite runs. One invocation never builds every supported pair, because each complete build can consume several gigabytes.
