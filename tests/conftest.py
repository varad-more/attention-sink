"""Shared pytest configuration.

Test markers are derived from the directory a test lives in rather than declared by
hand, so `make test-unit` and the markers can never drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from attention_sink.model_gateway import GatewaySettings, ModelGateway, build_gateway
from attention_sink.pilot import PilotEngine, ProtocolBundle, build_run, load_bundle

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


# --------------------------------------------------------------------- pilot

REPO_ROOT = _TESTS_ROOT.parent
PILOT_ROOT = REPO_ROOT / "experiments" / "pilot"
FIXED_CLOCK_TIME = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    """A clock that never moves, so two runs of one cycle hash identically."""
    return FIXED_CLOCK_TIME


@pytest.fixture(scope="session")
def pilot_bundle() -> ProtocolBundle:
    """The committed pilot protocol, loaded once for the whole session."""
    return load_bundle(PILOT_ROOT)


@pytest.fixture
def pilot_gateway() -> ModelGateway:
    """A fixture-mode gateway, built from an empty environment.

    Empty rather than the real one, so a developer with AWS credentials exported
    runs the same tests as CI does.
    """
    return build_gateway(GatewaySettings.from_env(env={}))


@pytest.fixture
def pilot_engine(pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway) -> PilotEngine:
    """An initialised engine on the committed protocol, with a frozen clock."""
    engine = build_run(pilot_bundle, run_id="run_test_pilot", gateway=pilot_gateway)
    engine.clock = fixed_clock
    return engine
