"""The four local runner scripts, and the one piece of real logic they hold.

The scripts themselves are thin: three of them are a handful of lines over the pilot
package, and running them is what `make pilot-*` does. What is worth testing is the
calibration arithmetic, which is not a wrapper over anything, and the table renderer,
which exists specifically so that regenerating the calibration document cannot break
`make lint`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest
from scripts.calibrate_local_budget import (
    LABEL,
    estimated_candidate_tokens,
    markdown_table,
    serialized_block_tokens,
)

from attention_sink.model_gateway import ModelGateway
from attention_sink.pilot import ProtocolBundle

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_serialized_seed_block_costs_more_than_the_seed_texts(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """A request pays for the m1..mn labels and separators as well as the text."""
    block = serialized_block_tokens(pilot_bundle, pilot_gateway)
    assert block > pilot_bundle.seed_world.total_tokens


def test_the_candidate_estimate_sits_inside_the_range_of_seed_costs(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    counts = [pilot_gateway.token_counter.count(s.text) for s in pilot_bundle.seed_world.memories]
    assert min(counts) <= estimated_candidate_tokens(pilot_bundle, pilot_gateway) <= max(counts)


def test_the_provisional_label_is_the_one_the_documents_carry():
    assert LABEL == "PROVISIONAL_LOCAL_APPROXIMATION"
    generated = (REPO_ROOT / "docs/pilot/local-token-calibration.md").read_text(encoding="utf-8")
    assert LABEL in generated


def test_the_table_renderer_pads_every_column_to_its_widest_cell():
    """Prettier aligns Markdown tables. A ragged generator would fail lint on every run."""
    table = markdown_table(("seed", "tokens"), (("`seed_01`", "6"), ("`x`", "128")))
    assert table.splitlines() == [
        "| seed      | tokens |",
        "| --------- | ------ |",
        "| `seed_01` | 6      |",
        "| `x`       | 128    |",
    ]


def test_a_row_of_the_wrong_width_is_refused_rather_than_silently_padded():
    with pytest.raises(ValueError, match="argument 2 is shorter"):
        markdown_table(("a", "b"), (("only-one",),))


@pytest.mark.parametrize(
    "module_name",
    [
        "validate_local_protocol",
        "calibrate_local_budget",
        "run_local_fixture_cycle",
        "run_local_fixture_experiment",
        "run_local_scheduler",
        "verify_local_run",
        "local_cli",
    ],
)
def test_every_runner_parses_its_arguments_without_touching_a_network(
    module_name: str, capsys: pytest.CaptureFixture[str]
):
    """`--help` proves the module imports and its arguments parse. Nothing else runs.

    In-process rather than in a subprocess: a subprocess re-resolves the editable
    install, and a test that failed when the *environment* was wrong would say
    nothing about the script.
    """
    module = importlib.import_module(f"scripts.{module_name}")
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--help"]) if module_name == "local_cli" else _help(module)
    assert exit_info.value.code == 0
    assert "--root" in capsys.readouterr().out


def _help(module: object) -> None:
    """Invoke a script's argument parser with ``--help``."""
    with mock.patch.object(sys, "argv", ["script", "--help"]):
        module.main()  # type: ignore[attr-defined]
