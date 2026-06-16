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
#   install_pkgs "ros-${ROS_DISTRO}-plotjuggler-ros"
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
