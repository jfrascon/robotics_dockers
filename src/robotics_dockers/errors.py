class RoboticsDockersError(Exception):
    """Base class for errors raised by robotics-dockers."""


class InvalidDockerImageNameError(RoboticsDockersError):
    """Raised when a Docker image name does not match Docker naming rules."""


class InvalidImageUserError(RoboticsDockersError):
    """Raised when the requested image user is not a valid Linux user name."""


class InvalidRosDistroError(RoboticsDockersError):
    """Raised when the requested ROS distribution is not supported."""


class InvalidRosdepPackagesDirError(RoboticsDockersError):
    """Raised when the fixed rosdep packages directory configuration is invalid."""


class MissingResourceError(RoboticsDockersError):
    """Raised when a packaged template or support file cannot be found."""
