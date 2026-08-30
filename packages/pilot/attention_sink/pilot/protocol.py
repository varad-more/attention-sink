"""The versioned protocol files, and the freeze that makes them canonical.

A protocol file is experimental apparatus in the same sense a prompt is: two runs
whose stimulus decks differ are different experiments, whatever their run identifiers
say. So each file declares its own version, carries a digest of its own content, and
moves through exactly three states.

``DRAFT`` may be edited freely and may not be run canonically. ``FROZEN`` carries a
digest that must still match; a file edited after freezing is detected by recomputing
it. ``RETIRED`` records a protocol that has been superseded and must not start new
runs, without deleting what earlier runs executed.

The digest deliberately covers the *parsed content* rather than the file bytes.
Reindenting a YAML block or rewrapping a comment leaves a protocol identical in every
way an experiment can observe, and a digest that changed for those would train people
to re-freeze without reading. Changing a word of a stimulus does change it.
"""

from __future__ import annotations

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
    "EXPECTED_SEED_COUNT",
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
    "document_digest",
    "freeze_documents",
    "load_bundle",
    "read_document",
    "rewrite_scalars",
]

DEFAULT_PROTOCOL_ROOT = Path("experiments/pilot")
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
    """Lifecycle of one protocol artefact."""

    DRAFT = "draft"
    """Editable. Its digest is not yet meaningful and it cannot run canonically."""

    FROZEN = "frozen"
    """Digested and immutable. Any later edit is detected by recomputation."""

    RETIRED = "retired"
    """Superseded. Kept so earlier runs remain readable; starts no new run."""


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
    def is_frozen(self) -> bool:
        """Whether this document may take part in a canonical run."""
        return self.status is ProtocolStatus.FROZEN


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
    token_count: int | None = Field(default=None, ge=1)
    """Written by calibration, in the tokens of the counter the run is measured by."""

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
            memory.token_count is not None for memory in self.memories
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
        return sum(memory.token_count or 0 for memory in self.memories)


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

    max_cycles: int = Field(gt=0)
    checkpoint_cycles: tuple[int, ...] = Field(min_length=1)
    arms: tuple[ArmId, ...] = Field(min_length=1)

    memory_budget_tokens: int | None = Field(default=None, gt=0)
    counter_version: Version | None = None

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
        outside = [c for c in self.checkpoint_cycles if c < 0 or c > self.max_cycles]
        if outside:
            msg = f"checkpoint cycles outside 0..{self.max_cycles}: {outside}"
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
    def is_frozen(self) -> bool:
        """Whether every document is frozen and may run canonically."""
        return all(document.is_frozen for document in self.documents)

    def drifted(self) -> tuple[str, ...]:
        """Files whose stored digest no longer matches their content.

        Only frozen files are checked. A draft's digest is not a claim about anything
        yet, and reporting drift on one would make the signal useless.
        """
        return tuple(
            sorted(
                name
                for name, document in self.named_documents
                if document.is_frozen and document.content_hash != self.digests[name]
            )
        )

    def require_runnable(self) -> None:
        """Refuse a bundle that is not frozen, undrifted, and calibrated.

        Raises:
            ProtocolError: A document is still a draft or has been retired, a frozen
                file has been edited since, or the budget was never calibrated.
        """
        unfrozen = sorted(name for name, doc in self.named_documents if not doc.is_frozen)
        if unfrozen:
            msg = (
                f"refusing to run a canonical pilot from files that are not frozen: "
                f"{', '.join(unfrozen)}. Run `make pilot-freeze` first."
            )
            raise ProtocolError(msg)
        drifted = self.drifted()
        if drifted:
            msg = (
                f"protocol files were modified after freezing: {', '.join(drifted)}. "
                f"Re-freeze deliberately, or restore them."
            )
            raise ProtocolError(msg)
        if not self.protocol.is_calibrated:
            msg = "the active-memory budget has not been calibrated; run `make pilot-calibrate`"
            raise ProtocolError(msg)
        if not self.seed_world.is_calibrated:
            msg = "the seed world has no token counts; run `make pilot-calibrate`"
            raise ProtocolError(msg)


def _paths_for(protocol_version: str, world: str) -> tuple[str, ...]:
    """The five files a protocol version and its seed world resolve to, in order."""
    return (
        f"protocols/{protocol_version}.yaml",
        f"seed-worlds/{world}.yaml",
        f"stimulus-decks/{world}.yaml",
        f"truth-ledgers/{world}.yaml",
        f"interviews/{protocol_version}.yaml",
    )


def load_bundle(
    root: Path = DEFAULT_PROTOCOL_ROOT, *, protocol_version: str = "pilot-v1"
) -> ProtocolBundle:
    """Load, validate, and cross-check every protocol file under ``root``.

    Raises:
        ProtocolError: A file is missing or malformed, a declared version does not
            match the file that declares it, or two files disagree.
    """
    protocol_path = root / "protocols" / f"{protocol_version}.yaml"
    raw_protocol = read_document(protocol_path)
    protocol = _validate(PilotProtocol, raw_protocol, protocol_path)

    paths = _paths_for(protocol_version, protocol.seed_world_version)
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
    if len(bundle.stimulus_deck.stimuli) != protocol.max_cycles:
        problems.append(
            f"the deck holds {len(bundle.stimulus_deck.stimuli)} stimuli but the "
            f"protocol runs {protocol.max_cycles} cycles"
        )
    if protocol.max_cycles not in protocol.checkpoint_cycles:
        problems.append(
            f"the final cycle {protocol.max_cycles} is not a checkpoint; the "
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


def freeze_documents(bundle: ProtocolBundle) -> tuple[str, ...]:
    """Write each document's digest into itself and mark it frozen.

    The digest is computed over the content the file will have *after* the status
    change, so a frozen file's recorded digest matches what a later verification
    recomputes. Files already frozen and undrifted are left alone and not reported.

    Returns:
        The paths that were rewritten.

    Raises:
        ProtocolError: The protocol or the seed world has not been calibrated.
    """
    if not bundle.protocol.is_calibrated or not bundle.seed_world.is_calibrated:
        msg = (
            "refusing to freeze an uncalibrated protocol: the active-memory budget is "
            "still unset. Run `make pilot-calibrate` first."
        )
        raise ProtocolError(msg)

    written: list[str] = []
    frozen_status = ProtocolStatus.FROZEN.value
    for name in bundle.paths:
        path = bundle.root / name
        data = read_document(path)
        digest = document_digest({**data, "status": frozen_status})
        if data.get("status") == frozen_status and data.get("content_hash") == digest:
            continue
        rewrite_scalars(path, {"status": frozen_status, "content_hash": f"'{digest}'"})
        written.append(name)
    return tuple(written)
