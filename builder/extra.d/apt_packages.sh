#!/usr/bin/env bash
# Extra apt packages and repositories for this Docker image.
#
# This script runs as root after ROS is installed, so ros-* packages are supported.
#
# Examples:
#   apt-get update
#   apt-get install -y --no-install-recommends libopencv-dev ros-jazzy-moveit
#
#   # Add a third-party repository before installing:
#   curl -sSL https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add -
#   add-apt-repository "deb http://apt.llvm.org/noble/ llvm-toolchain-noble-18 main"
#   apt-get update
#   apt-get install -y clang-18
