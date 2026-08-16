class RoboticsDockersError(Exception):
    """Base class for errors raised by robotics-dockers."""


class InvalidDockerImageNameError(RoboticsDockersError):
    """Raised when a Docker image name does not match Docker naming rules."""


class InvalidRosDistroError(RoboticsDockersError):
    """Raised when the requested ROS distribution is not supported."""


class InvalidRosdepPackagesDirError(RoboticsDockersError):
    """Raised when the fixed rosdep packages directory configuration is invalid."""


class InvalidOutputDirectoryError(RoboticsDockersError):
    """Raised when generation would overwrite an existing file or directory content."""


class MissingResourceError(RoboticsDockersError):
    """Raised when a packaged template or support file cannot be found."""
