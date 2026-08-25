import re
from pathlib import Path

import pytest

DDS_SYSCTL_DIRECTORY = Path(__file__).parents[1] / 'dds' / 'cyclonedds'
EXPECTED_DDS_SYSCTL_SETTINGS = {
    '10-cyclonedds.conf': {'net.core.rmem_max': 2_147_483_647},
    '10-ros2-cross-vendor-tuning.conf': {'net.ipv4.ipfrag_time': 3, 'net.ipv4.ipfrag_high_thresh': 134_217_728},
}
SYSCTL_ASSIGNMENT = re.compile(r'(?P<key>[a-z0-9_.]+)\s*=\s*(?P<value>[0-9]+)')


@pytest.mark.parametrize(('file_name', 'expected_settings'), EXPECTED_DDS_SYSCTL_SETTINGS.items())
def test_dds_sysctl_files_use_portable_assignments(file_name: str, expected_settings: dict[str, int]) -> None:
    settings: dict[str, int] = {}

    for line_number, line in enumerate(DDS_SYSCTL_DIRECTORY.joinpath(file_name).read_text().splitlines(), start=1):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith(('#', ';')):
            continue

        match = SYSCTL_ASSIGNMENT.fullmatch(stripped_line)
        assert match is not None, f'{file_name}:{line_number}: expected one numeric sysctl assignment'

        key = match.group('key')
        assert key not in settings, f'{file_name}:{line_number}: duplicate assignment for {key}'
        settings[key] = int(match.group('value'))

    assert settings == expected_settings


def test_all_dds_sysctl_files_are_covered_by_the_contract() -> None:
    actual_files = {path.name for path in DDS_SYSCTL_DIRECTORY.glob('10-*.conf')}
    assert actual_files == EXPECTED_DDS_SYSCTL_SETTINGS.keys()
