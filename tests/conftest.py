"""Shared pytest configuration.

Test markers are derived from the directory a test lives in rather than declared by
hand, so `make test-unit` and the markers can never drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_MARKER_BY_DIRECTORY = {
    "unit": "unit",
    "property": "property",
    "integration": "integration",
    "e2e": "e2e",
}
_TESTS_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply the marker implied by each test's top-level directory under tests/."""
    for item in items:
        try:
            relative = item.path.relative_to(_TESTS_ROOT)
        except ValueError:
            continue
        marker = _MARKER_BY_DIRECTORY.get(relative.parts[0] if relative.parts else "")
        if marker:
            item.add_marker(getattr(pytest.mark, marker))
