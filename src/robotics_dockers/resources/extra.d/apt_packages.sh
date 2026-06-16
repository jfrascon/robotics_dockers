#!/usr/bin/env bash
# Extra apt packages and repositories for this Docker image.
#
# This script runs as root after ROS is installed, so ros-${ROS_DISTRO}* packages are supported.
#
# The command `install_pkgs` is available in this script. It checks which requested
# packages are already installed, which packages are installable, and which packages
# cannot be installed from the currently configured apt repositories.
#
# If at least one requested package is installable, `install_pkgs` installs the
# installable packages and only warns about the ones that cannot be installed.
# If none of the requested packages can be installed, it returns an error.
#
# Examples:
#   install_pkgs libopencv-dev "ros-${ROS_DISTRO}-plotjuggler-ros"
#
#   url="https://packages.osrfoundation.org"
#   remote_gpg_key="${url}/gazebo.gpg"
#   local_gpg_key="/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg"
#
#   curl --fail --location --show-error --silent "${remote_gpg_key}" --output "${local_gpg_key}" || exit 1
#
#   . /etc/os-release
#
#   echo "deb [arch=$(dpkg --print-architecture) signed-by=${local_gpg_key}] ${url}/gazebo/ubuntu-stable ${UBUNTU_CODENAME} main" |
#       tee /etc/apt/sources.list.d/gazebo-stable.list >/dev/null
#
#   apt-get update --quiet --quiet
#
#   install_pkgs "ros-${ROS_DISTRO}-ros-gz"
#
# Prefer rosdep for ROS packages that are real dependencies of the project.
# This script is intended for general tools and repositories, such as PlotJuggler
# or Gazebo, that are useful in the image but are not necessarily declared by a
# package.xml in your workspace.
#
# For example, if a project package needs twist_mux, prefer declaring it in that
# package.xml and building the image with build.py --pkgs-dir <workspace-src>.
# rosdep can then install the correct apt package from the declared dependency.
#
# This requires the project packages and their dependencies to be known before
# the image is built. In real projects that is not always true, so regenerating
# the image as new packages and dependencies appear is expected.
