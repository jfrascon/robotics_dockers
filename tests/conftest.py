from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    test_options = parser.getgroup('robotics-dockers')
    test_options.addoption(
        '--full-build-distro',
        metavar='ROS_DISTRO',
        default=None,
        help=(
            'build one complete generated image for the named ROS distribution; '
            'this expensive release check never runs unless this option is given'
        ),
    )
