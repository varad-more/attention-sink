"""Freezing the protocol, and the four ways a canonical launch is refused.

Freezing is the point where the pilot stops being editable, so every test here is
about a refusal. The four the brief names -- an edited file, a different model, a
different budget, a different prompt hash -- are each proved separately, because a
single "the hashes differ" refusal would not tell an operator which one they changed.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from attention_sink.model_gateway import GatewaySettings, ModelConfig, build_gateway
from attention_sink.pilot import (
    PilotRunConfiguration,
    ProtocolError,
    ProtocolStatus,
    load_bundle,
    model_specs,
)
from attention_sink.pilot.cli import (
    _prompt_hashes,
    canonical_launch_mismatches,
    counter_identity,
    read_canonical_manifest,
    require_canonical_launch,
    write_canonical_manifest,
)
from attention_sink.pilot.protocol import (
    CANONICAL_MANIFEST_DIGEST_PATH,
    CANONICAL_MANIFEST_PATH,
    promote_documents,
    return_to_draft,
    rewrite_block,
    rewrite_scalars,
)
from tests.conftest import PILOT_ROOT

NOW = datetime(2026, 8, 30, tzinfo=UTC)

BEDROCK_ENV = {
    "MODEL_MODE": "bedrock",
    "AWS_REGION": "us-east-1",
    "WRITER_MODEL_ID": "amazon.nova-micro-v1:0",
    "AUDITOR_MODEL_ID": "amazon.nova-micro-v1:0",
    "JUDGE_MODEL_ID": "amazon.nova-micro-v1:0",
    "SUMMARY_MODEL_ID": "amazon.nova-micro-v1:0",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
    "TOKEN_COUNT_SOURCE": "converse",
}
"""The deployment the committed protocol was calibrated against.

Settings only. Nothing here builds a client or makes a call: `build_gateway` is given
an invoker, so the models are recorded and never invoked.
"""


@pytest.fixture
def calibrated(tmp_path: Path) -> Path:
    """A writable copy of the committed protocol, at LOCAL_VALIDATED.

    The committed protocol is exactly calibrated, which is what makes it promotable.
    Returning it to draft and re-validating puts it one step below frozen without
    changing a single measured number.
    """
    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    # Removed rather than left in place: every test here either writes its own or
    # asserts on its absence, and a copy of the committed one would answer both.
    (root / CANONICAL_MANIFEST_PATH).unlink(missing_ok=True)
    (root / CANONICAL_MANIFEST_DIGEST_PATH).unlink(missing_ok=True)
    return_to_draft(load_bundle(root))
    promote_documents(load_bundle(root))
    return root


@pytest.fixture
def approximate(calibrated: Path) -> Path:
    """The same protocol, with the budget denominated in the local heuristic."""
    bundle = load_bundle(calibrated)
    return_to_draft(bundle)
    rewrite_scalars(
        calibrated / "protocol.yaml",
        {
            "token_count_source": "local_fixture_heuristic",
            "counter_version": "heuristic-v1",
            "calibration_writer_model_id": "null",
            "calibration_region": "null",
            "calibrated_at": "null",
        },
    )
    rewrite_block(calibrated / "protocol.yaml", "calibration_input_hashes", {})
    promote_documents(load_bundle(calibrated))
    return calibrated


def settings() -> GatewaySettings:
    return GatewaySettings.from_env(env=BEDROCK_ENV)


def gateway(env: dict[str, str] | None = None):
    """A gateway that records the configured models and can invoke none of them."""
    from tests.doubles import ScriptedInvoker

    return build_gateway(
        GatewaySettings.from_env(env=env or BEDROCK_ENV), invoker=ScriptedInvoker()
    )


def frozen(root: Path) -> dict[str, Any]:
    """Freeze the protocol at ``root`` and return its canonical manifest."""
    promote_documents(load_bundle(root), ProtocolStatus.FROZEN)
    bundle = load_bundle(root)
    write_canonical_manifest(
        bundle,
        prompt_hashes=_prompt_hashes(bundle),
        settings=settings(),
        analysis={"metric_version": "metric-v1"},
    )
    return read_canonical_manifest(root)


def configuration(root: Path, **overrides: object) -> PilotRunConfiguration:
    """The run configuration the frozen protocol at ``root`` implies."""
    bundle = load_bundle(root)
    built = gateway()
    writer, embedding = model_specs(built)
    counter_version, source = counter_identity(built)
    derived: dict[str, Any] = {
        "run_id": "run_aws_canonical",
        "created_at": NOW,
        "writer_model": writer,
        "embedding_model": embedding,
        "prompt_set_digest": built.prompts.prompt_set_digest(bundle.protocol.writer_prompt_version),
        "app_version": "0.1.0",
        "counter_version": counter_version,
        "token_count_source": source,
    }
    return PilotRunConfiguration.from_bundle(bundle, **(derived | overrides))


# ------------------------------------------------------------------- promotion


def test_a_locally_calibrated_protocol_refuses_to_be_frozen(approximate: Path):
    """A budget in heuristic tokens would make the canonical run measure the heuristic."""
    with pytest.raises(ProtocolError, match="local_fixture_heuristic"):
        promote_documents(load_bundle(approximate), ProtocolStatus.FROZEN)


def test_a_locally_calibrated_protocol_refuses_the_calibrated_status_too(approximate: Path):
    with pytest.raises(ProtocolError, match="aws_calibrated"):
        promote_documents(load_bundle(approximate), ProtocolStatus.AWS_CALIBRATED)


def test_an_exactly_calibrated_protocol_advances_to_calibrated_then_frozen(calibrated: Path):
    promote_documents(load_bundle(calibrated), ProtocolStatus.AWS_CALIBRATED)
    assert not load_bundle(calibrated).is_frozen

    promote_documents(load_bundle(calibrated), ProtocolStatus.FROZEN)
    bundle = load_bundle(calibrated)
    assert bundle.is_frozen
    assert bundle.drifted() == ()
    bundle.require_runnable(canonical=True)


def test_a_frozen_protocol_still_reports_drift_when_it_is_edited(calibrated: Path):
    """A comment would not count -- the digest covers the parsed document, not the file."""
    promote_documents(load_bundle(calibrated), ProtocolStatus.FROZEN)
    path = calibrated / "stimuli.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("title:", "title: edited ", 1), encoding="utf-8"
    )
    bundle = load_bundle(calibrated)
    assert bundle.drifted() == ("stimuli.yaml",)
    with pytest.raises(ProtocolError, match="were modified after validation"):
        bundle.require_runnable(canonical=True)


def test_a_status_no_document_can_hold_is_refused(calibrated: Path):
    with pytest.raises(ProtocolError, match="not a status"):
        promote_documents(load_bundle(calibrated), ProtocolStatus.RETIRED)


# ------------------------------------------------------- the canonical manifest


def test_the_canonical_manifest_records_the_whole_experiment(calibrated: Path):
    manifest = frozen(calibrated)

    assert manifest["models"]["writer_model_id"] == "amazon.nova-micro-v1:0"
    assert manifest["models"]["region"] == "us-east-1"
    assert manifest["budget"]["token_count_source"] == "bedrock_converse_usage"  # noqa: S105
    assert (
        manifest["budget"]["memory_budget_tokens"]
        == load_bundle(calibrated).protocol.memory_budget_tokens
    )
    assert manifest["run_shape"]["maximum_cycles"] == 24
    assert manifest["run_shape"]["checkpoint_cycles"] == [0, 12, 24]
    assert len(manifest["run_shape"]["arms"]) == 6
    assert manifest["policies"]["seeded_random"]["random_seed"]
    assert manifest["policies"]["dreamer"]["min_sources"] >= 2
    assert manifest["analysis"]["metric_version"] == "metric-v1"
    assert manifest["prompts"]["hashes"]["prompt_set"].startswith("sha256:")


def test_the_digest_file_verifies_without_this_repository(calibrated: Path):
    """`sha256sum -c` has to agree, or the freeze cannot be checked independently."""
    import hashlib

    frozen(calibrated)
    rendered = (calibrated / CANONICAL_MANIFEST_PATH).read_bytes()
    recorded = (calibrated / CANONICAL_MANIFEST_DIGEST_PATH).read_text(encoding="utf-8")
    digest, name = recorded.split()
    assert name == CANONICAL_MANIFEST_PATH
    assert digest == hashlib.sha256(rendered).hexdigest()


def test_an_edited_canonical_manifest_is_refused(calibrated: Path):
    frozen(calibrated)
    path = calibrated / CANONICAL_MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["budget"]["memory_budget_tokens"] = 9999
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(ProtocolError, match="does not match"):
        read_canonical_manifest(calibrated)


def test_a_manifest_with_a_repaired_digest_file_is_still_refused(calibrated: Path):
    """Editing both files is caught by the manifest's own recorded content hash."""
    import hashlib

    frozen(calibrated)
    path = calibrated / CANONICAL_MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["budget"]["memory_budget_tokens"] = 9999
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    path.write_text(rendered, encoding="utf-8")
    (calibrated / CANONICAL_MANIFEST_DIGEST_PATH).write_text(
        f"{hashlib.sha256(rendered.encode()).hexdigest()}  {CANONICAL_MANIFEST_PATH}\n",
        encoding="utf-8",
    )

    with pytest.raises(ProtocolError, match="has been edited"):
        read_canonical_manifest(calibrated)


def test_a_missing_canonical_manifest_names_the_command_that_writes_it(calibrated: Path):
    with pytest.raises(ProtocolError, match="make pilot-freeze"):
        read_canonical_manifest(calibrated)


# ------------------------------------------------------------ launch rejection


def test_the_run_the_protocol_was_frozen_as_launches(calibrated: Path):
    manifest = frozen(calibrated)

    assert canonical_launch_mismatches(configuration(calibrated), manifest) == ()
    require_canonical_launch(configuration(calibrated), manifest)


def test_a_different_model_identifier_is_refused(calibrated: Path):
    manifest = frozen(calibrated)
    other = gateway(BEDROCK_ENV | {"WRITER_MODEL_ID": "amazon.nova-lite-v1:0"})
    writer, _ = model_specs(other)

    with pytest.raises(ProtocolError, match="writer model"):
        require_canonical_launch(configuration(calibrated, writer_model=writer), manifest)


def test_a_different_token_budget_is_refused(calibrated: Path):
    manifest = frozen(calibrated)

    with pytest.raises(ProtocolError, match="memory budget"):
        require_canonical_launch(
            configuration(calibrated).model_copy(update={"memory_budget_tokens": 1024}), manifest
        )


def test_a_different_prompt_hash_is_refused(calibrated: Path):
    manifest = frozen(calibrated)

    with pytest.raises(ProtocolError, match="prompt set digest"):
        require_canonical_launch(
            configuration(calibrated, prompt_set_digest="sha256:" + "0" * 64), manifest
        )


def test_a_modified_protocol_file_is_refused(calibrated: Path):
    """The digests travel on the configuration too, so an edit is caught twice."""
    manifest = frozen(calibrated)
    edited = configuration(calibrated)
    hashes = dict(edited.protocol_content_hashes)
    hashes["stimuli.yaml"] = "sha256:" + "1" * 64

    with pytest.raises(ProtocolError, match="stimuli.yaml"):
        require_canonical_launch(
            edited.model_copy(update={"protocol_content_hashes": hashes}), manifest
        )


def test_every_difference_is_reported_rather_than_the_first(calibrated: Path):
    manifest = frozen(calibrated)
    wrong = configuration(calibrated, prompt_set_digest="sha256:" + "0" * 64).model_copy(
        update={"memory_budget_tokens": 1024, "random_seed": "a-different-seed"}
    )

    differences = canonical_launch_mismatches(wrong, manifest)

    assert len(differences) == 3
    assert any("memory budget" in line for line in differences)
    assert any("prompt set digest" in line for line in differences)
    assert any("random seed" in line for line in differences)


def test_a_manifest_from_a_fixture_deployment_records_no_models(calibrated: Path):
    """Nulls, so a canonical run against it is refused rather than launched blind."""
    promote_documents(load_bundle(calibrated), ProtocolStatus.FROZEN)
    bundle = load_bundle(calibrated)
    write_canonical_manifest(
        bundle,
        prompt_hashes=_prompt_hashes(bundle),
        settings=GatewaySettings.from_env(env={}),
        analysis={},
    )

    manifest = read_canonical_manifest(calibrated)
    assert manifest["models"]["writer_model_id"] is None
    with pytest.raises(ProtocolError, match="writer model"):
        require_canonical_launch(configuration(calibrated), manifest)


def test_the_configured_models_are_recorded_and_never_invoked():
    """Building the settings must not need a credential or reach a provider."""
    configured = settings().models
    assert isinstance(configured, ModelConfig)
    assert configured.writer_model_id == "amazon.nova-micro-v1:0"
    assert configured.region == "us-east-1"
