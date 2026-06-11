# builder

Generate a ready-to-use Docker image with ROS 2 and a configured development user.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Quick start](#quick-start)
3. [create_docker_files.py reference](#create_docker_filespy-reference)
4. [build.py reference](#buildpy-reference)
5. [Customizing the output](#customizing-the-output)
6. [Startup scripts (entrypoint.d)](#startup-scripts-entrypointd)
7. [NVIDIA GPU support](#nvidia-gpu-support)
8. [rosbuild — colcon build wrapper](#rosbuild--colcon-build-wrapper)
9. [Running the container](#running-the-container)
10. [Examples](#examples)

---

## Prerequisites

- Docker Engine
- Python 3.10+
- Python packages: `jinja2` (`pip install jinja2`)

If you intend to use an NVIDIA GPU:
- NVIDIA driver installed on the host (`nvidia-smi` works)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

---

## Quick start

```bash
# Invocation:
# python3 create_docker_files.py <user> <ros_distro> <image_id> --output ~/my_docker

# See help:
python3 create_docker_files.py -h

# Create the Dockerfile and docker-compose files:
python3 create_docker_files.py jfrascon jazzy myorg/ros2-jazzy:latest --output ~/my_docker

# Customize extra packages (Optional)
# Edit files under ~/my_docker/.resources/extra.d/
echo 'apt-get install -y --no-install-recommends ffmpeg' >> ~/my_docker/.resources/extra.d/apt_packages.sh
echo 'ruff==0.15.14' >> ~/my_docker/.resources/extra.d/requirements.txt
echo 'fd-find' >> ~/my_docker/.resources/extra.d/rust_packages.txt

# Example 1: Build the image.
cd ~/my_docker
python3 build.py

# Example 2: Build with ROS package dependencies resolved via rosdep:
python3 build.py --pkgs-dir /path/to/your/workspace/src

# Example 3: Save the build log to a file if you want to keep a record of the build output.
python3 build.py 2>&1 | tee /tmp/my_build.log
```

---

## create_docker_files.py reference

```
usage: create_docker_files.py [-h] [-b BASE_IMG]
                               [--use-host-nvidia-driver] [--output OUTPUT]
                               image_main_user ros_distro img_id
```

| Argument | Description |
|---|---|
| `image_main_user` | Username for the development user inside the container |
| `ros_distro` | ROS distro: `humble`, `jazzy` |
| `img_id` | Docker image name and tag, e.g. `myorg/ros2-jazzy:latest` |
| `-b BASE_IMG` | Base Docker image. Default: `ubuntu:X.Y` matched to the ROS distro |
| `--use-host-nvidia-driver` | Enable NVIDIA GPU access via the host driver |
| `--output DIR` | Directory where the output is written. Default: a temporary directory under `/tmp` |

**Available ROS distros:**
- `humble` - ROS 2, Ubuntu 22.04
- `jazzy` - ROS 2, Ubuntu 24.04

**Custom base image:**

You can pass any Docker image as the base, for example a CUDA image:
```bash
python3 create_docker_files.py jfrascon jazzy myorg/ros2-jazzy:latest \
    -b nvidia/cuda:12.5.0-devel-ubuntu24.04 \
    --use-host-nvidia-driver \
    --output ~/my_docker
```

---

## build.py reference

```
usage: build.py [-h] [-c] [-p] [--pkgs-dir DIR] [--meta-title ...] ...
```

| Argument | Description |
|---|---|
| `-c`, `--cache` | Reuse cached Docker layers |
| `-p`, `--pull` | Pull the latest base image before building |
| `--pkgs-dir DIR` | Path to the ROS packages directory on the host (e.g. `~/workspace/src`). If provided, rosdep installs the dependencies of those packages into the image |

**Save the build output to a log file:**
```bash
python3 build.py 2>&1 | tee /tmp/my_build.log
```
`build.py` writes directly to your terminal. Pipe through `tee` if you want to keep a log file.

---

## Customizing the output

After running `create_docker_files.py`, the output directory contains a
`.resources/extra.d/` folder with three files you can edit before building:

### `extra.d/apt_packages.sh`

Shell script executed as root after ROS is installed. Add apt packages,
third-party repositories or any other system-level setup here.

The helper `skip_rosdep_keys` is available at `/usr/local/bin/skip_rosdep_keys`
and can be called from this script to register additional rosdep keys that
should be ignored (useful for packages not available in the standard Ubuntu/ROS2
repositories):

```bash
#!/usr/bin/env bash
apt-get update
apt-get install -y --no-install-recommends libopencv-dev ros-jazzy-moveit

# Adding a third-party repository:
curl -sSL https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add -
add-apt-repository "deb http://apt.llvm.org/noble/ llvm-toolchain-noble-18 main"
apt-get update && apt-get install -y clang-18

# Ignore a custom rosdep key not available in standard repositories:
skip_rosdep_keys my_private_package another_unavailable_key
```

> If you generated without `--use-host-nvidia-driver`, this file already
> contains the default Mesa packages script. Edit it as needed.

### `extra.d/requirements.txt`

Standard pip requirements file. Installed as `--user` for the container user.

```
numpy
torch==2.3.0
--index-url https://download.pytorch.org/whl/cu121
torchvision
git+https://github.com/user/repo.git@main
```

### `extra.d/rust_packages.txt`

One Rust crate per line. Two modes:

```
# Binary mode (fast): downloads a pre-built binary
ripgrep
fd-find

# Source mode (slow): compiles from source, use when features are needed
source: broot --features clipboard
```

If the file contains only comments or blank lines, the Rust toolchain is
**not** installed.

---

## Startup scripts (entrypoint.d)

When the container starts, the custom entrypoint runs all scripts found in
`~/.entrypoint.d/` in alphabetical order. Scripts with a `.sh` extension are
**sourced** (they run in the same process and can set environment variables
used by later scripts). Scripts with a `.txt` extension are printed to stdout.

Every file must follow the naming convention `NN-name.sh` or `NN-name.txt`,
where `NN` is **exactly two digits** (e.g. `01`, `50`, `99`). Files that do
not match this pattern cause the container to abort at startup.

Two scripts are always included and are mandatory:

| Script | Purpose |
|---|---|
| `00-checks.sh` | Validates the naming convention of all files in `entrypoint.d/` and checks that `99-uid-gid-adapt.sh` is present. Runs first. |
| `99-uid-gid-adapt.sh` | Remaps the internal user UID/GID to match `HOST_UID`/`HOST_UPGID` and performs the final `exec` that starts the user session. Runs last. |

When `--use-host-nvidia-driver` is passed, an additional script is included:

| Script | Purpose |
|---|---|
| `98-gpu-driver-check.sh` | Warns at startup if the NVIDIA driver is not visible in the container (e.g. `--gpus all` was omitted or the NVIDIA Container Toolkit is not installed). Based on the [upstream NVIDIA script](https://gitlab.com/nvidia/container-images/cuda/-/blob/master/entrypoint.d/50-gpu-driver-check.sh). |

### Adding your own startup scripts

You can add custom scripts to `.resources/entrypoint.d/` **before** running
`build.py`. They will be copied into the image and executed at every container
startup.

```bash
# Example: print a banner at startup
cat > .resources/entrypoint.d/10-banner.txt <<'EOF'
Welcome to my ROS 2 development container!
EOF

# Example: set custom environment variables at startup
cat > .resources/entrypoint.d/20-env.sh <<'EOF'
export MY_VAR=hello
EOF
```

Rules to follow:
- Filename must be `NN-name.sh` or `NN-name.txt` with exactly two digits.
- Do not use `00` (reserved for checks) or `99` (reserved for UID/GID
  adaptation).
- If `--use-host-nvidia-driver` was used, do not use `98` either.
- `.sh` scripts are sourced — they run in the entrypoint process. Keep them
  fast and side-effect-free (no `exit`, no long-running commands).

### Using a base image that has its own entrypoint

This project always sets its own entrypoint (`/usr/local/bin/entrypoint.sh`),
which overrides any entrypoint defined by the base image. If the base image
you chose performs initialization logic that you want to preserve (common with
NVIDIA images, for example), do not rely on the base entrypoint being called
automatically. Instead:

1. Find the relevant script(s) in the base image entrypoint (e.g. inspect the
   image with `docker run --rm <base_img> cat /path/to/entrypoint_script.sh`).
2. Copy or adapt that logic into a new `.sh` file in `.resources/entrypoint.d/`
   using an appropriate numeric prefix (e.g. `10-nvidia-env.sh`).
3. Run `build.py` as usual — the script will be picked up automatically.

To inspect what entrypoint a base image defines:

```bash
# Show the entrypoint declared in the image metadata:
docker inspect <base_img> --format '{{.Config.Entrypoint}}'

# Read the contents of that script:
docker run --rm --entrypoint cat <base_img> /path/to/entrypoint.sh
```

---

## NVIDIA GPU support

Pass `--use-host-nvidia-driver` when generating. This configures the
docker-compose file to use `deploy.resources` with the NVIDIA driver.

You also need to provide the GID of the render device so all processes
(including those started by VS Code) can access `/dev/dri/renderD*`:

```bash
# Find the GID on your machine:
stat -c %g /dev/dri/renderD128

# Add it to a .env file next to docker-compose-dev.yaml:
echo "RENDER_GID=$(stat -c %g /dev/dri/renderD128)" >> .env
```

---

## rosbuild — colcon build wrapper

`rosbuild` is installed at `/usr/local/bin/rosbuild` and wraps `colcon build`
with sensible defaults enabled out of the box:

| Default behaviour | colcon equivalent |
|---|---|
| `--merge-install` | merges all install spaces into a single `install/` directory |
| `--symlink-install` | symlinks Python files and other resources instead of copying |
| `--mixin release` | enables release-mode compiler flags via colcon mixins |
| `--mixin compile-commands` | generates `compile_commands.json` for IDEs/clangd |
| `--parallel-workers N` | uses half the available CPU cores (rounded up) |
| `-Wall -Wextra -Wpedantic ...` | injects common C++ warning flags via `CMAKE_CXX_FLAGS` |

So instead of:
```bash
colcon build --merge-install --symlink-install --mixin release --mixin compile-commands
```

You just run:
```bash
rosbuild
```

Flags to opt out of the defaults:

| Flag | Effect |
|---|---|
| `--no-merge-install` | disables `--merge-install` |
| `--no-symlink-install` | disables `--symlink-install` |

Any other `colcon build` argument is passed through unchanged:
```bash
# Build only specific packages in debug mode:
rosbuild --packages-select my_pkg --no-symlink-install --mixin debug
```

---

## Running the container

The output directory contains a `docker-compose-dev.yaml`. Copy it next to
your workspace and create a `.env` file with the required variables:

```bash
# .env
HOST_UID=1000          # your UID: id -u
HOST_UPGID=1000        # your primary GID: id -g
WORKSPACE=/home/user/my_workspace   # path to your workspace on the host
RENDER_GID=992         # required if --use-host-nvidia-driver was used: stat -c %g /dev/dri/renderD128
```

Then:
```bash
docker compose -f docker-compose-dev.yaml up
```

The container starts as root, remaps the internal user to your `HOST_UID`/`HOST_UPGID`,
and then drops to the development user. Files created inside the container will
be owned by you on the host.

---

## Examples

The `examples/` directory contains reference scripts for installing specific
Mesa driver variants (default, Kisak PPA, Oibaf PPA, locked versions).
These are not used automatically - copy the relevant parts into your
`extra.d/apt_packages.sh`.
