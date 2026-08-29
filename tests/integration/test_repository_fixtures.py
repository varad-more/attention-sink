"""The fixtures committed to this repository load and validate as written.

Crosses the filesystem boundary against the real ``datasets/fixtures`` tree rather
than a temporary directory, so a malformed committed fixture fails here rather than
in someone's first local run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from attention_sink.model_gateway import FixtureModelGateway, RuntimeMode, RuntimeSettings

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "datasets" / "fixtures" / "model_responses"


def _tasks() -> list[str]:
    return sorted(path.stem for path in FIXTURE_ROOT.glob("*.json"))


def test_fixture_directory_is_present():
    assert FIXTURE_ROOT.is_dir()
    assert _tasks(), "local mode has no fixtures to serve"


@pytest.mark.parametrize("task", _tasks())
def test_every_committed_fixture_validates(task: str):
    gateway = FixtureModelGateway(RuntimeSettings(mode=RuntimeMode.LOCAL), FIXTURE_ROOT)

    fixture = gateway.load(task)

    assert fixture.task == task
    assert fixture.simulated is True
    assert fixture.responses
