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
#   install -d -m 0755 /etc/apt/keyrings
#   curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | \
#       gpg --dearmor -o /etc/apt/keyrings/llvm-snapshot.gpg
#   echo "deb [signed-by=/etc/apt/keyrings/llvm-snapshot.gpg] http://apt.llvm.org/noble/ llvm-toolchain-noble-18 main" \
#       > /etc/apt/sources.list.d/llvm-toolchain-noble-18.list
#   apt-get update
#   apt-get install -y clang-18
