#!/usr/bin/env python
"""Advance the calibrated protocol to AWS_CALIBRATED, then to FROZEN.

Two steps, and the separation matters. ``aws-calibrated`` says the budget was derived
against the model that will read the memories; ``frozen`` says nothing may change
again. A protocol goes through the first before the second so that a calibration can
be reviewed, and re-derived, without having already been declared immutable.

Lives outside the pilot package because a canonical manifest has to record the metric
versions and the Graveyard Echo threshold, and the pilot may not import the analysis
package (see the import boundary test). A composition-level script may import both.

    python scripts/freeze_canonical_protocol.py --status aws_calibrated
    python scripts/freeze_canonical_protocol.py --status frozen

Neither step calls a model or needs a credential.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from attention_sink.analysis import METRIC_VERSION
from attention_sink.analysis.metrics import ECHO_THRESHOLD, IDENTITY_QUESTION_IDS
from attention_sink.model_gateway import EVALUATION_CALCULATION_VERSION, GatewaySettings
from attention_sink.pilot import load_bundle
from attention_sink.pilot.cli import (
    _prompt_hashes,
    write_canonical_manifest,
)
from attention_sink.pilot.protocol import (
    DEFAULT_PROTOCOL_ROOT,
    ProtocolError,
    ProtocolStatus,
    promote_documents,
    write_manifest,
)

PROMOTABLE = (ProtocolStatus.AWS_CALIBRATED, ProtocolStatus.FROZEN)


def analysis_constants() -> dict[str, object]:
    """The metric definitions a frozen protocol pins.

    A metric version that changed under a frozen protocol would silently redefine
    what the run's numbers mean, which is exactly what freezing is meant to prevent.
    """
    return {
        "metric_version": METRIC_VERSION,
        "graveyard_echo_threshold": ECHO_THRESHOLD,
        "evaluation_calculation_version": EVALUATION_CALCULATION_VERSION,
        "identity_question_ids": list(IDENTITY_QUESTION_IDS),
    }


def main() -> int:
    """Promote the protocol and write both manifests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument(
        "--status",
        default=ProtocolStatus.FROZEN.value,
        choices=[status.value for status in PROMOTABLE],
    )
    args = parser.parse_args()
    status = ProtocolStatus(args.status)

    bundle = load_bundle(args.root)
    try:
        written = promote_documents(bundle, status)
    except ProtocolError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    for name in written:
        print(f"{name} -> {status.value}")
    if not written:
        print(f"already {status.value}; nothing to write")

    reloaded = load_bundle(args.root)
    prompt_hashes = _prompt_hashes(reloaded)
    print(f"wrote {write_manifest(reloaded, prompt_hashes=prompt_hashes)}")

    # The canonical manifest is written at both steps so that a reviewer can read the
    # complete definition before deciding to freeze it, rather than only afterwards.
    path, digest_path, digest = write_canonical_manifest(
        reloaded,
        prompt_hashes=prompt_hashes,
        # Built from the environment, so the manifest records the models the run will
        # really use. In fixture mode this writes nulls, which is why a canonical run
        # is refused against a manifest whose models are null.
        settings=GatewaySettings.from_env(),
        analysis=analysis_constants(),
    )
    print(f"wrote {path}")
    print(f"wrote {digest_path}")
    print(f"  sha256 {digest}")
    print(f"  status {status.value}  frozen={reloaded.is_frozen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
