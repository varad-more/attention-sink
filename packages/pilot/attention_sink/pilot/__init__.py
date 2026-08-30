"""The pilot: one locally executable Attention Sink experiment.

Six arms, twelve identical seed memories, twenty-four shared stimuli, one fixed
active-memory budget, and exactly one difference between the arms. Everything here is
an application service over the packages that already exist: the domain kernel decides
what a memory is, the policy package decides what is forgotten, the model gateway is
the only thing that talks to a model, and this package decides the order in which they
take their turns.

Pilot V1 records immutable snapshots rather than an event stream, and runs in one
process rather than as a distributed workflow. See ``docs/adr/ADR-008-pilot-snapshot-
architecture.md`` for what that defers and what it does not change.
"""

from attention_sink.pilot.budget import ModelCallBudget, ModelCallBudgetExceeded, ModelUsage
from attention_sink.pilot.canonical import canonical_digest, canonical_json
from attention_sink.pilot.cli import build_run, calibrate, main, model_specs, run_cycles
from attention_sink.pilot.configuration import ModelSpec, PilotRunConfiguration
from attention_sink.pilot.engine import (
    ArmGeneration,
    ArmResult,
    CheckpointRecord,
    CycleSequenceError,
    PilotEngine,
    RebalanceOutcome,
    StagedCycle,
    validate_claims,
)
from attention_sink.pilot.export import ExportResult, checksum_lines, export_run
from attention_sink.pilot.protocol import (
    DEFAULT_PROTOCOL_ROOT,
    EXPECTED_SEED_COUNT,
    CitationMode,
    InterviewProtocol,
    PilotProtocol,
    ProtocolBundle,
    ProtocolError,
    ProtocolStatus,
    SeedWorld,
    StimulusDeck,
    TruthLedger,
    document_digest,
    freeze_documents,
    load_bundle,
)
from attention_sink.pilot.snapshots import (
    CLAIMED_VALIDATOR_VERSION,
    ArmCycleSnapshot,
    MemoryStatistic,
    RejectedClaim,
    RetiredMemoryRecord,
    RunSnapshot,
    RunStatus,
    StimulusRecord,
)

__all__ = [
    "CLAIMED_VALIDATOR_VERSION",
    "DEFAULT_PROTOCOL_ROOT",
    "EXPECTED_SEED_COUNT",
    "ArmCycleSnapshot",
    "ArmGeneration",
    "ArmResult",
    "CheckpointRecord",
    "CitationMode",
    "CycleSequenceError",
    "ExportResult",
    "InterviewProtocol",
    "MemoryStatistic",
    "ModelCallBudget",
    "ModelCallBudgetExceeded",
    "ModelSpec",
    "ModelUsage",
    "PilotEngine",
    "PilotProtocol",
    "PilotRunConfiguration",
    "ProtocolBundle",
    "ProtocolError",
    "ProtocolStatus",
    "RebalanceOutcome",
    "RejectedClaim",
    "RetiredMemoryRecord",
    "RunSnapshot",
    "RunStatus",
    "SeedWorld",
    "StagedCycle",
    "StimulusDeck",
    "StimulusRecord",
    "TruthLedger",
    "build_run",
    "calibrate",
    "canonical_digest",
    "canonical_json",
    "checksum_lines",
    "document_digest",
    "export_run",
    "freeze_documents",
    "load_bundle",
    "main",
    "model_specs",
    "run_cycles",
    "validate_claims",
]
