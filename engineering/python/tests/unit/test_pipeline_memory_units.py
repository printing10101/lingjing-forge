"""Memory units with a B suffix must match the full unit before bytes."""

import pytest

from app.data.pipeline.config import _parse_memory_to_bytes

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("value", "expected"), [
    ("8GB", 8 * 1024**3),
    ("1.5 GB", int(1.5 * 1024**3)),
    ("512mb", 512 * 1024**2),
    ("2KB", 2048),
    ("128B", 128),
    ("1024", 1024),
    ("unknown", 8 * 1024**3),
])
def test_memory_units(value, expected):
    assert _parse_memory_to_bytes(value) == expected
