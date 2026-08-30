"""The versioned protocol files, and the freeze that makes them canonical.

A protocol file is experimental apparatus in the same sense a prompt is: two runs
whose stimulus decks differ are different experiments, whatever their run identifiers
say. So each file declares its own version, carries a digest of its own content, and
moves through a lifecycle whose steps say exactly how much a run may claim.

``DRAFT`` may be edited freely and runs nothing. ``LOCAL_VALIDATED`` carries a digest
that must still match and may run locally against fixture models; its token budget is
a local approximation, so its results describe application behaviour and nothing
else. ``AWS_CALIBRATED`` has had that budget re-derived against the production
counter. ``FROZEN`` is the canonical protocol and may not be edited at all.
``RETIRED`` records a protocol that has been superseded and starts no new run,
without deleting what earlier runs executed.

The pilot reaches ``LOCAL_VALIDATED`` in Phase 4 and no further. Freezing a budget
denominated in local approximate tokens would make the canonical experiment a
measurement of the fixture counter (ADR-local-first-pilot, ADR-011).

The digest deliberately covers the *parsed content* rather than the file bytes.
Reindenting a YAML block or rewrapping a comment leaves a protocol identical in every
way an experiment can observe, and a digest that changed for those would train people
to re-freeze without reading. Changing a word of a stimulus does change it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from attention_sink.domain import ArmId, UtcTimestamp, Version, content_hash
from attention_sink.pilot.canonical import canonical_digest

__all__ = [
    "DEFAULT_PROTOCOL_ROOT",
    "DOCUMENT_PATHS",
    "EXPECTED_SEED_COUNT",
    "MANIFEST_PATH",
    "PREDICTIONS_PATH",
    "CitationMode",
    "DreamerSpec",
    "HeavyHitterSpec",
    "InterviewProtocol",
    "InterviewQuestionSpec",
    "ModelCallLimits",
    "PilotProtocol",
    "PinnedOriginSpec",
    "PolicySpecs",
    "ProtocolBundle",
    "ProtocolError",
    "ProtocolStatus",
    "Reliability",
    "SeedMemorySpec",
    "SeedWorld",
    "SeededRandomSpec",
    "StimulusDeck",
    "StimulusSpec",
    "TruthFact",
    "TruthLedger",
    "build_manifest",
    "document_digest",
    "load_bundle",
    "manifest_drift",
    "promote_documents",
    "read_document",
    "read_manifest",
    "return_to_draft",
    "rewrite_scalars",
    "write_manifest",
]

DEFAULT_PROTOCOL_ROOT = Path("experiment/pilot")
"""Where the protocol files live, relative to the repository root."""

EXPECTED_SEED_COUNT = 12
"""Station Kestrel's seed world is twelve cards. A thirteenth is a protocol change."""

_FACT_ID = r"^F[0-9]{2,3}$"
_SEED_ID = r"^seed_[0-9]{2,3}$"
_STIMULUS_ID = r"^stim_[0-9]{3,4}$"
_QUESTION_ID = r"^[a-z][a-z0-9_]{0,63}$"

FactId = Annotated[str, StringConstraints(pattern=_FACT_ID)]
SeedId = Annotated[str, StringConstraints(pattern=_SEED_ID)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ProtocolError(ValueError):
    """A protocol file is missing, malformed, inconsistent, or has been altered."""


class ProtocolStatus(StrEnum):
    """Lifecycle of one protocol artefact, in the order it advances through it."""

    DRAFT = "draft"
    """Editable. Its digest is not yet meaningful and it runs nothing."""

    LOCAL_VALIDATED = "local_validated"
    """Digested, cross-checked, and runnable against fixture models only.

    The budget it carries is a local approximation. Returning a document here to
    ``DRAFT`` is the only way to edit it, and produces new digests."""

    AWS_CALIBRATED = "aws_calibrated"
    """The budget has been re-derived against the production token counter.

    Reserved for Phase 8. Nothing in Phases 4-6 writes this status."""

    FROZEN = "frozen"
    """Canonical and immutable. Any later edit is detected by recomputation.

    Reserved for Phase 8, after AWS calibration. A protocol frozen around a local
    approximate budget would be a canonical experiment about the fixture counter."""

    RETIRED = "retired"
    """Superseded. Kept so earlier runs remain readable; starts no new run."""

    @property
    def is_digested(self) -> bool:
        """Whether this status asserts that the recorded digest still matches."""
        return self in _DIGESTED_STATUSES

    @property
    def runs_locally(self) -> bool:
        """Whether a document in this status may take part in a fixture run."""
        return self in _LOCAL_RUNNABLE_STATUSES


_DIGESTED_STATUSES = frozenset(
    {ProtocolStatus.LOCAL_VALIDATED, ProtocolStatus.AWS_CALIBRATED, ProtocolStatus.FROZEN}
)
_LOCAL_RUNNABLE_STATUSES = frozenset(
    {ProtocolStatus.LOCAL_VALIDATED, ProtocolStatus.AWS_CALIBRATED, ProtocolStatus.FROZEN}
)


class Reliability(StrEnum):
    """How far a stimulus's content can be taken at face value."""

    RELIABLE = "reliable"
    DECEPTIVE = "deceptive"
    AMBIGUOUS = "ambiguous"
    NEUTRAL = "neutral"


class CitationMode(StrEnum):
    """How a writer's citation claims become the statistics a mechanism reads."""

    CLAIMED_VALIDATED = "claimed_validated"
    """Structural validation only: the label was offered, the memory is active, and
    duplicates are collapsed. No auditor call. The pilot's mode."""

    AUDITED = "audited"
    """A citation-auditor call per arm per cycle decides what counts. Reserved: the
    gateway implements it, the pilot engine does not spend it."""


class _Document(BaseModel):
    """Fields every machine-readable protocol file carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    protocol_version: Version
    status: ProtocolStatus
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    created_at: UtcTimestamp
    content_hash: str = ""
    """Digest of this document's content, excluding this field. Written by freeze."""

    @property
    def is_digested(self) -> bool:
        """Whether this document claims its recorded digest still matches."""
        return self.status.is_digested

    @property
    def is_frozen(self) -> bool:
        """Whether this document is the canonical, immutable version."""
        return self.status is ProtocolStatus.FROZEN

    @property
    def runs_locally(self) -> bool:
        """Whether this document may take part in a local fixture run."""
        return self.status.runs_locally


# ------------------------------------------------------------------- seed world


class SeedMemorySpec(BaseModel):
    """One seed memory card, and the metadata that never reaches a prompt.

    Only :attr:`text` is ever rendered into a request. ``fact_ids``, ``category``,
    ``importance``, and ``entities`` are scoring apparatus: a writer that could see
    which canonical fact a memory carries would be told which memories matter, and
    the arms would no longer differ only in what they were allowed to keep.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: SeedId
    text: Text
    fact_ids: tuple[FactId, ...] = Field(min_length=1)
    category: str = Field(min_length=1, max_length=64)
    importance: Literal["critical", "high", "medium", "low"]
    initial_position: int = Field(ge=1)
    entities: tuple[str, ...] = ()
    provisional_token_count: int | None = Field(default=None, ge=1)
    """Written by calibration, in the tokens of the counter the run is measured by.

    Named provisional because in Phases 4-6 that counter is the deterministic local
    heuristic, not the production model's tokeniser. The number is exact for what it
    measures and an approximation of what will eventually matter."""

    pinned_eligible: bool = False
    content_hash: str = ""

    @property
    def expected_content_hash(self) -> str:
        """The digest this card's text must carry."""
        return content_hash(self.text)


class SeedWorld(_Document):
    """The memories every arm begins with, identically."""

    seed_world_version: Version
    counter_version: Version | None = None
    memories: tuple[SeedMemorySpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_seed_set(self) -> Self:
        ids = [memory.memory_id for memory in self.memories]
        if len(set(ids)) != len(ids):
            msg = "seed world lists a memory identifier more than once"
            raise ValueError(msg)
        positions = [memory.initial_position for memory in self.memories]
        if positions != list(range(1, len(positions) + 1)):
            msg = f"seed initial_position must run 1..{len(positions)} in order, got {positions}"
            raise ValueError(msg)
        pinned = [m.memory_id for m in self.memories if m.pinned_eligible]
        if len(pinned) > 1:
            msg = f"at most one seed may be pinned-eligible, got {pinned}"
            raise ValueError(msg)
        return self

    @property
    def is_calibrated(self) -> bool:
        """Whether every card carries a token count and the counter that produced it."""
        return self.counter_version is not None and all(
            memory.provisional_token_count is not None for memory in self.memories
        )

    @property
    def total_tokens(self) -> int:
        """Cost of the whole seed set.

        Raises:
            ProtocolError: The seed world has not been calibrated.
        """
        if not self.is_calibrated:
            msg = f"seed world {self.seed_world_version} has not been calibrated"
            raise ProtocolError(msg)
        return sum(memory.provisional_token_count or 0 for memory in self.memories)


# --------------------------------------------------------------- stimulus deck


class StimulusSpec(BaseModel):
    """One cycle's event, identical for every arm.

    Only :attr:`text` reaches a writer. ``relevant_fact_ids``, ``pressure_type``,
    ``reliability``, and ``evaluator_notes`` say what the stimulus is *for*, and a
    writer told what an event is for would write to the expectation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stimulus_id: Annotated[str, StringConstraints(pattern=_STIMULUS_ID)]
    cycle: int = Field(ge=1)
    phase: str = Field(min_length=1, max_length=64)
    text: Text
    relevant_fact_ids: tuple[FactId, ...] = ()
    pressure_type: str = Field(min_length=1, max_length=64)
    reliability: Reliability
    evaluator_notes: str = Field(min_length=1, max_length=2000)
    content_hash: str = ""

    @property
    def expected_content_hash(self) -> str:
        """The digest this stimulus's text must carry."""
        return content_hash(self.text)


class StimulusDeck(_Document):
    """Every stimulus of the run, in the order the arms receive them."""

    stimulus_deck_version: Version
    stimuli: tuple[StimulusSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_deck(self) -> Self:
        cycles = [stimulus.cycle for stimulus in self.stimuli]
        if cycles != list(range(1, len(cycles) + 1)):
            msg = f"stimulus cycles must run 1..{len(cycles)} in order, got {cycles}"
            raise ValueError(msg)
        ids = [stimulus.stimulus_id for stimulus in self.stimuli]
        if len(set(ids)) != len(ids):
            msg = "stimulus deck lists an identifier more than once"
            raise ValueError(msg)
        return self

    def for_cycle(self, cycle: int) -> StimulusSpec:
        """Return the one stimulus every arm receives in ``cycle``.

        Raises:
            ProtocolError: The deck has no stimulus for that cycle.
        """
        found = next((s for s in self.stimuli if s.cycle == cycle), None)
        if found is None:
            msg = f"stimulus deck {self.stimulus_deck_version} has no cycle {cycle}"
            raise ProtocolError(msg)
        return found


# ----------------------------------------------------------------- truth ledger


class TruthFact(BaseModel):
    """One canonical fact, and the seed that carries it into the run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: FactId
    statement: Text
    seed_memory_id: SeedId
    category: str = Field(min_length=1, max_length=64)
    contradicted_in_phase: str | None = None

    answer_terms: tuple[str, ...] = ()
    """Terms an answer must contain, normalised, for this fact to count as recalled.

    Scoring apparatus, held here rather than on the question, because what makes an
    answer right is a property of the fact. Never rendered into a prompt: a writer
    told which words score would write those words."""

    accepted_variants: tuple[str, ...] = ()
    """Alternative surface forms that satisfy an ``answer_terms`` entry.

    Configured rather than inferred. A scorer that guessed at synonyms would be a
    second, unversioned judgement inside a metric that claims to be deterministic."""

    evaluator_fallback: bool = False
    """Whether an unmatched answer for this fact is ambiguous enough to ask a model.

    False for almost every fact. A name is recalled or it is not, and sending that to
    an evaluator would replace an exact answer with an opinion."""


class TruthLedger(_Document):
    """What is actually true, held apart from everything a model can see."""

    truth_ledger_version: Version
    facts: tuple[TruthFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_facts(self) -> Self:
        ids = [fact.fact_id for fact in self.facts]
        if len(set(ids)) != len(ids):
            msg = "truth ledger states a fact identifier more than once"
            raise ValueError(msg)
        return self

    @property
    def fact_ids(self) -> frozenset[str]:
        """Every canonical fact identifier this ledger defines."""
        return frozenset(fact.fact_id for fact in self.facts)

    @property
    def statements(self) -> tuple[str, ...]:
        """The canonical statements, for the evaluator and for the blindness test."""
        return tuple(fact.statement for fact in self.facts)


# --------------------------------------------------------------------- interview


class InterviewQuestionSpec(BaseModel):
    """One checkpoint question, with a stable identifier and its scoring role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: Annotated[str, StringConstraints(pattern=_QUESTION_ID)]
    text: Text
    factual_recall: bool
    """Whether this question contributes to the primary factual-recall score."""

    fact_ids: tuple[FactId, ...] = ()


class InterviewProtocol(_Document):
    """The fixed question set, asked identically at every checkpoint."""

    interview_version: Version
    questions: tuple[InterviewQuestionSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_questions(self) -> Self:
        ids = [question.question_id for question in self.questions]
        if len(set(ids)) != len(ids):
            msg = "interview protocol asks a question identifier more than once"
            raise ValueError(msg)
        if not any(question.factual_recall for question in self.questions):
            msg = "an interview with no factual-recall question scores nothing"
            raise ValueError(msg)
        return self

    @property
    def factual_recall_question_ids(self) -> tuple[str, ...]:
        """Questions whose answers move the primary score, in order."""
        return tuple(q.question_id for q in self.questions if q.factual_recall)


# ---------------------------------------------------------------- the protocol


class NamedPolicySpec(BaseModel):
    """A mechanism with no parameters beyond its version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version


class HeavyHitterSpec(BaseModel):
    """Parameters of the citation-weighted arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    citation_decay: float = Field(ge=0.0, le=1.0)
    recency_reserve: int = Field(ge=0)


class PinnedOriginSpec(BaseModel):
    """Which seed the pinned-origin arm may never retire.

    Named by *seed* identifier rather than by memory identifier, because a memory
    identifier is arm-scoped: the same seed is ``mem_arm_sink_000000`` in one arm and
    ``mem_arm_fifo_000000`` in another. The engine resolves it for the one arm whose
    mechanism reads it, which is what keeps the pin out of the other five.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    pinned_seed_memory_id: SeedId


class SeededRandomSpec(BaseModel):
    """The recorded entropy of the stochastic arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    random_seed: str = Field(min_length=8, max_length=256)


class DreamerSpec(BaseModel):
    """Parameters of the summarising arm: what a compression costs, and when it gives up."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Version
    target_summary_tokens: int = Field(gt=0)
    safety_margin_tokens: int = Field(ge=0)
    min_sources: int = Field(ge=2)
    fallback_rule: Literal["fifo", "refuse"]


class PolicySpecs(BaseModel):
    """Every arm's parameters, as the protocol declares them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fifo: NamedPolicySpec
    lru: NamedPolicySpec
    heavy_hitter: HeavyHitterSpec
    pinned_origin: PinnedOriginSpec
    seeded_random: SeededRandomSpec
    dreamer: DreamerSpec


class ModelCallLimits(BaseModel):
    """What one cycle, one checkpoint, and one run are allowed to spend.

    A ceiling rather than an estimate. The engine checks it *before* each call, so a
    protocol that would spend more than it declared stops instead of discovering the
    overspend in a bill.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    writer_calls_per_cycle: int = Field(ge=0)
    summary_calls_per_cycle: int = Field(ge=0)
    evaluator_calls_per_cycle: int = Field(ge=0)
    interview_calls_per_cycle: int = Field(ge=0)
    interview_calls_per_checkpoint: int = Field(ge=0)
    max_model_calls_per_run: int = Field(gt=0)


class PilotProtocol(_Document):
    """The complete definition of one pilot experiment."""

    seed_world_version: Version
    stimulus_deck_version: Version
    truth_ledger_version: Version
    interview_version: Version

    maximum_cycles: int = Field(gt=0)
    checkpoint_cycles: tuple[int, ...] = Field(min_length=1)
    arms: tuple[ArmId, ...] = Field(min_length=1)

    memory_budget_tokens: int | None = Field(default=None, gt=0)
    counter_version: Version | None = None
    token_count_source: str = Field(default="local_fixture_heuristic", min_length=1, max_length=64)
    """What produced the counts the budget is denominated in.

    Recorded rather than inferred, so a manifest never leaves a reader guessing
    whether a budget came from the local heuristic or from Bedrock ``CountTokens``."""

    writer_prompt_version: Version
    summary_prompt_version: Version

    policies: PolicySpecs
    citation_mode: CitationMode
    model_call_limits: ModelCallLimits
    max_parallel_model_calls: int = Field(gt=0, le=32)

    @model_validator(mode="after")
    def _check_protocol(self) -> Self:
        if len(set(self.arms)) != len(self.arms):
            msg = "a protocol cannot configure the same arm twice"
            raise ValueError(msg)
        outside = [c for c in self.checkpoint_cycles if c < 0 or c > self.maximum_cycles]
        if outside:
            msg = f"checkpoint cycles outside 0..{self.maximum_cycles}: {outside}"
            raise ValueError(msg)
        if sorted(self.checkpoint_cycles) != list(self.checkpoint_cycles):
            msg = f"checkpoint cycles must be ascending, got {list(self.checkpoint_cycles)}"
            raise ValueError(msg)
        if (self.memory_budget_tokens is None) != (self.counter_version is None):
            msg = "a budget and the counter that measures it must be set together"
            raise ValueError(msg)
        return self

    @property
    def is_calibrated(self) -> bool:
        """Whether the active-memory budget has been fixed against a counter."""
        return self.memory_budget_tokens is not None


# --------------------------------------------------------------------- loading


def read_document(path: Path) -> dict[str, Any]:
    """Parse one protocol file into a plain mapping.

    Raises:
        ProtocolError: The file is missing, is not YAML, or is not a mapping.
    """
    if not path.is_file():
        msg = f"protocol file does not exist: {path}"
        raise ProtocolError(msg)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path} is not valid YAML: {exc}"
        raise ProtocolError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a mapping at the top level"
        raise ProtocolError(msg)
    return loaded


def document_digest(data: Mapping[str, Any]) -> str:
    """Digest a protocol document's content, excluding its own digest field.

    Self-exclusion is not a nicety: a hash that covered the field it is written into
    could never be satisfied. Everything else is covered, ``status`` included, so
    promoting a frozen protocol to retired is itself a change that has to be
    re-digested rather than made silently.
    """
    return canonical_digest({k: v for k, v in data.items() if k != "content_hash"})


class ProtocolBundle(BaseModel):
    """All five machine-readable files, parsed, cross-checked, and digested.

    Loaded as a set rather than one at a time because most of what can be wrong with a
    protocol is a disagreement between two files: a stimulus that cites a fact the
    ledger does not define, a ledger fact whose seed does not exist, a deck of
    twenty-three cycles behind a protocol that promises twenty-four.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    paths: tuple[str, ...]
    """Repo-relative path per document, in :attr:`documents` order."""

    protocol: PilotProtocol
    seed_world: SeedWorld
    stimulus_deck: StimulusDeck
    truth_ledger: TruthLedger
    interview: InterviewProtocol
    digests: Mapping[str, str]
    """Recomputed digest per file, keyed by the path it was loaded from."""

    @property
    def documents(self) -> tuple[_Document, ...]:
        """Every loaded document, in a stable order."""
        return (
            self.protocol,
            self.seed_world,
            self.stimulus_deck,
            self.truth_ledger,
            self.interview,
        )

    @property
    def named_documents(self) -> tuple[tuple[str, _Document], ...]:
        """Every document paired with the path it was loaded from."""
        return tuple(zip(self.paths, self.documents, strict=True))

    @property
    def is_local_validated(self) -> bool:
        """Whether every document has been digested and may run locally."""
        return all(document.runs_locally for document in self.documents)

    @property
    def is_frozen(self) -> bool:
        """Whether every document is the canonical, immutable version."""
        return all(document.is_frozen for document in self.documents)

    def drifted(self) -> tuple[str, ...]:
        """Files whose stored digest no longer matches their content.

        Only digested files are checked. A draft's digest is not a claim about
        anything yet, and reporting drift on one would make the signal useless.
        """
        return tuple(
            sorted(
                name
                for name, document in self.named_documents
                if document.is_digested and document.content_hash != self.digests[name]
            )
        )

    def require_runnable(self, *, canonical: bool = False) -> None:
        """Refuse a bundle that is not validated, undrifted, and calibrated.

        A local fixture run needs ``LOCAL_VALIDATED`` or better. A canonical run
        needs ``FROZEN``, which nothing in Phases 4-6 produces -- so asking for one
        here is refused by the status check rather than by a comment.

        Raises:
            ProtocolError: A document is still a draft or has been retired, a
                digested file has been edited since, or the budget was never
                calibrated.
        """
        if canonical:
            unfrozen = sorted(name for name, doc in self.named_documents if not doc.is_frozen)
            if unfrozen:
                msg = (
                    f"refusing to run a canonical pilot from files that are not frozen: "
                    f"{', '.join(unfrozen)}. A protocol is frozen only after AWS token "
                    f"calibration in Phase 8."
                )
                raise ProtocolError(msg)
        unvalidated = sorted(name for name, doc in self.named_documents if not doc.runs_locally)
        if unvalidated:
            msg = (
                f"refusing to run a pilot from files that are not validated: "
                f"{', '.join(unvalidated)}. Run `make pilot-local-validate` first."
            )
            raise ProtocolError(msg)
        drifted = self.drifted()
        if drifted:
            msg = (
                f"protocol files were modified after validation: {', '.join(drifted)}. "
                f"Return them to draft and re-validate deliberately, or restore them."
            )
            raise ProtocolError(msg)
        if not self.protocol.is_calibrated:
            msg = "the active-memory budget has not been calibrated; run `make pilot-calibrate`"
            raise ProtocolError(msg)
        if not self.seed_world.is_calibrated:
            msg = "the seed world has no token counts; run `make pilot-calibrate`"
            raise ProtocolError(msg)


DOCUMENT_PATHS: tuple[str, ...] = (
    "protocol.yaml",
    "seed_memories.yaml",
    "stimuli.yaml",
    "truth_ledger.yaml",
    "interview_questions.yaml",
)
"""The five machine-readable files, in :attr:`ProtocolBundle.documents` order."""

MANIFEST_PATH = "manifest.json"
"""Where the digest of every protocol file is recorded, beside the files themselves."""

PREDICTIONS_PATH = "predictions.md"
"""Registered predictions. Prose, so it is manifested but not schema-validated."""


def load_bundle(
    root: Path = DEFAULT_PROTOCOL_ROOT, *, protocol_version: str = "pilot-v1"
) -> ProtocolBundle:
    """Load, validate, and cross-check every protocol file under ``root``.

    Raises:
        ProtocolError: A file is missing or malformed, a declared version does not
            match the file that declares it, or two files disagree.
    """
    paths = DOCUMENT_PATHS
    protocol_path = root / paths[0]
    protocol = _validate(PilotProtocol, read_document(protocol_path), protocol_path)
    if protocol.protocol_version != protocol_version:
        msg = (
            f"{protocol_path} declares protocol version {protocol.protocol_version!r}, "
            f"not the requested {protocol_version!r}"
        )
        raise ProtocolError(msg)

    raws = {name: read_document(root / name) for name in paths}
    bundle = ProtocolBundle(
        root=root,
        paths=paths,
        protocol=protocol,
        seed_world=_validate(SeedWorld, raws[paths[1]], root / paths[1]),
        stimulus_deck=_validate(StimulusDeck, raws[paths[2]], root / paths[2]),
        truth_ledger=_validate(TruthLedger, raws[paths[3]], root / paths[3]),
        interview=_validate(InterviewProtocol, raws[paths[4]], root / paths[4]),
        digests={name: document_digest(raws[name]) for name in paths},
    )
    _cross_check(bundle)
    return bundle


def _validate[M: BaseModel](model: type[M], data: Mapping[str, Any], path: Path) -> M:
    """Validate one document, naming the file in anything that goes wrong."""
    try:
        return model.model_validate(dict(data))
    except ValueError as exc:
        msg = f"{path} is not a valid {model.__name__}: {exc}"
        raise ProtocolError(msg) from exc


def _version_problems(bundle: ProtocolBundle) -> list[str]:
    """Every disagreement about which version a file is, or which protocol it serves."""
    protocol = bundle.protocol
    declared = (
        ("seed world", protocol.seed_world_version, bundle.seed_world.seed_world_version),
        (
            "stimulus deck",
            protocol.stimulus_deck_version,
            bundle.stimulus_deck.stimulus_deck_version,
        ),
        ("truth ledger", protocol.truth_ledger_version, bundle.truth_ledger.truth_ledger_version),
        ("interview", protocol.interview_version, bundle.interview.interview_version),
    )
    problems = [
        f"the protocol names {kind} {wanted!r} but that file declares {found!r}"
        for kind, wanted, found in declared
        if wanted != found
    ]
    problems += [
        f"{kind} declares protocol version {document.protocol_version!r}, not "
        f"{protocol.protocol_version!r}"
        for kind, document in bundle.named_documents[1:]
        if document.protocol_version != protocol.protocol_version
    ]
    return problems


def _reference_problems(bundle: ProtocolBundle) -> list[str]:
    """Every reference from one file into another that does not resolve."""
    seed_ids = {memory.memory_id for memory in bundle.seed_world.memories}
    fact_ids = bundle.truth_ledger.fact_ids
    problems = [
        f"truth ledger fact {fact.fact_id} names unknown seed {fact.seed_memory_id}"
        for fact in bundle.truth_ledger.facts
        if fact.seed_memory_id not in seed_ids
    ]
    named_facts: tuple[tuple[str, tuple[str, ...]], ...] = (
        *((f"seed {m.memory_id}", m.fact_ids) for m in bundle.seed_world.memories),
        *((f"stimulus {s.stimulus_id}", s.relevant_fact_ids) for s in bundle.stimulus_deck.stimuli),
        *((f"question {q.question_id}", q.fact_ids) for q in bundle.interview.questions),
    )
    problems += [
        f"{where} names unknown facts: {sorted(set(named) - fact_ids)}"
        for where, named in named_facts
        if set(named) - fact_ids
    ]

    pinned = bundle.protocol.policies.pinned_origin.pinned_seed_memory_id
    if pinned not in seed_ids:
        problems.append(f"the pinned-origin arm names unknown seed {pinned}")
    elif pinned not in {m.memory_id for m in bundle.seed_world.memories if m.pinned_eligible}:
        problems.append(f"seed {pinned} is pinned by the protocol but is not pinned-eligible")
    return problems


def _cross_check(bundle: ProtocolBundle) -> None:
    """Assert every claim one protocol file makes about another.

    Raises:
        ProtocolError: Two files disagree.
    """
    protocol = bundle.protocol
    problems = _version_problems(bundle) + _reference_problems(bundle)

    if len(bundle.seed_world.memories) != EXPECTED_SEED_COUNT:
        problems.append(
            f"the seed world holds {len(bundle.seed_world.memories)} memories; "
            f"Station Kestrel is defined as {EXPECTED_SEED_COUNT}"
        )
    if len(bundle.stimulus_deck.stimuli) != protocol.maximum_cycles:
        problems.append(
            f"the deck holds {len(bundle.stimulus_deck.stimuli)} stimuli but the "
            f"protocol runs {protocol.maximum_cycles} cycles"
        )
    if protocol.maximum_cycles not in protocol.checkpoint_cycles:
        problems.append(
            f"the final cycle {protocol.maximum_cycles} is not a checkpoint; the "
            f"autobiography would never be interviewed"
        )

    if problems:
        msg = "the protocol files disagree:\n  - " + "\n  - ".join(problems)
        raise ProtocolError(msg)


# -------------------------------------------------------------------- freezing


def rewrite_scalars(path: Path, fields: Mapping[str, str]) -> None:
    """Replace top-level scalar fields in a YAML file, leaving everything else alone.

    A read-parse-dump round trip would reflow every block, drop every comment, and
    make the diff of a freeze unreadable. The fields written here are all top-level
    scalars that the pilot commands themselves own, so a line-level substitution is
    both sufficient and reviewable.

    Raises:
        ProtocolError: A field to be written does not appear at the top level.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    remaining = dict(fields)
    for index, line in enumerate(lines):
        key = line.split(":", 1)[0]
        if line.startswith(f"{key}:") and key in remaining:
            ending = "\n" if line.endswith("\n") else ""
            lines[index] = f"{key}: {remaining.pop(key)}{ending}"
    if remaining:
        msg = f"{path} has no top-level {', '.join(sorted(remaining))} field to write"
        raise ProtocolError(msg)
    path.write_text("".join(lines), encoding="utf-8")


def promote_documents(
    bundle: ProtocolBundle, status: ProtocolStatus = ProtocolStatus.LOCAL_VALIDATED
) -> tuple[str, ...]:
    """Write each document's digest into itself and advance it to ``status``.

    The digest is computed over the content the file will have *after* the status
    change, so a validated file's recorded digest matches what a later verification
    recomputes. Files already at ``status`` and undrifted are left alone.

    Returns:
        The paths that were rewritten.

    Raises:
        ProtocolError: The protocol or the seed world has not been calibrated, or
            ``status`` is one this phase must not write.
    """
    if status not in _LOCAL_RUNNABLE_STATUSES:
        msg = f"{status.value} is not a status a document can be promoted to"
        raise ProtocolError(msg)
    if status is not ProtocolStatus.LOCAL_VALIDATED:
        msg = (
            f"refusing to write status {status.value}: a pilot protocol advances past "
            f"local validation only at AWS token calibration in Phase 8"
        )
        raise ProtocolError(msg)
    if not bundle.protocol.is_calibrated or not bundle.seed_world.is_calibrated:
        msg = (
            "refusing to validate an uncalibrated protocol: the active-memory budget "
            "is still unset. Run `make pilot-calibrate` first."
        )
        raise ProtocolError(msg)

    written: list[str] = []
    target = status.value
    for name in bundle.paths:
        path = bundle.root / name
        data = read_document(path)
        digest = document_digest({**data, "status": target})
        if data.get("status") == target and data.get("content_hash") == digest:
            continue
        rewrite_scalars(path, {"status": target, "content_hash": f"'{digest}'"})
        written.append(name)
    return tuple(written)


def return_to_draft(bundle: ProtocolBundle) -> tuple[str, ...]:
    """Return every document to ``DRAFT`` so it may be edited again.

    The only supported way to change a validated protocol. Editing one in place would
    leave a file whose recorded digest is a claim about content it no longer has,
    which is exactly the state drift detection exists to make loud.

    Returns:
        The paths that were rewritten.
    """
    written: list[str] = []
    draft = ProtocolStatus.DRAFT.value
    for name in bundle.paths:
        path = bundle.root / name
        if read_document(path).get("status") == draft:
            continue
        rewrite_scalars(path, {"status": draft, "content_hash": "''"})
        written.append(name)
    return tuple(written)


def build_manifest(bundle: ProtocolBundle, *, prompt_hashes: Mapping[str, str]) -> dict[str, Any]:
    """The digest of everything one run's protocol is made of, in one document.

    Covers the prose file as well as the five schema-validated ones, and the prompt
    templates besides. A reader holding a manifest and a run's snapshots can decide
    whether the two describe the same experiment without parsing either.
    """
    files = {name: bundle.digests[name] for name in bundle.paths}
    predictions = bundle.root / PREDICTIONS_PATH
    if predictions.is_file():
        files[PREDICTIONS_PATH] = content_hash(predictions.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "protocol_version": str(bundle.protocol.protocol_version),
        "status": bundle.protocol.status.value,
        "title": bundle.protocol.title,
        "description": bundle.protocol.description,
        "token_count_source": bundle.protocol.token_count_source,
        "memory_budget_tokens": bundle.protocol.memory_budget_tokens,
        "counter_version": (
            None
            if bundle.protocol.counter_version is None
            else str(bundle.protocol.counter_version)
        ),
        "files": dict(sorted(files.items())),
        "prompt_hashes": dict(sorted(prompt_hashes.items())),
    }


def write_manifest(bundle: ProtocolBundle, *, prompt_hashes: Mapping[str, str]) -> Path:
    """Write :func:`build_manifest` beside the protocol, canonically serialised.

    Returns:
        The path written.
    """
    manifest = build_manifest(bundle, prompt_hashes=prompt_hashes)
    manifest["content_hash"] = canonical_digest(manifest)
    path = bundle.root / MANIFEST_PATH
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_manifest(root: Path = DEFAULT_PROTOCOL_ROOT) -> dict[str, Any]:
    """Load the recorded manifest.

    Raises:
        ProtocolError: The manifest is missing or is not a JSON object.
    """
    path = root / MANIFEST_PATH
    if not path.is_file():
        msg = f"no protocol manifest at {path}; run `make pilot-local-validate`"
        raise ProtocolError(msg)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        msg = f"{path} must contain a JSON object"
        raise ProtocolError(msg)
    return loaded


def manifest_drift(bundle: ProtocolBundle, *, prompt_hashes: Mapping[str, str]) -> tuple[str, ...]:
    """Every file whose digest disagrees with the recorded manifest.

    Reported per file rather than as one manifest-level mismatch, because "the
    manifest does not match" tells nobody which stimulus was edited.

    Raises:
        ProtocolError: The manifest is missing or malformed.
    """
    recorded = read_manifest(bundle.root)
    expected = build_manifest(bundle, prompt_hashes=prompt_hashes)
    stored_files = recorded.get("files")
    if not isinstance(stored_files, dict):
        msg = f"{bundle.root / MANIFEST_PATH} records no file digests"
        raise ProtocolError(msg)
    problems = [
        name for name, digest in expected["files"].items() if stored_files.get(name) != digest
    ]
    problems += [
        f"{name} (not in manifest)" for name in stored_files if name not in expected["files"]
    ]
    if recorded.get("prompt_hashes") != expected["prompt_hashes"]:
        problems.append("prompt templates")
    return tuple(sorted(problems))
