"""The frozen parameters of one pilot run, resolved from the protocol files.

Everything here is decided before cycle 1 and never changes. Two runs whose
configurations differ in any field below are different experiments, so this record is
serialised verbatim into the run manifest and hashed with it.

It is deliberately a *derivation* rather than a second source of truth: every value
comes from a protocol file, from the gateway that was actually built, or from the
version identity of the process. Nothing is defaulted here that a protocol could have
declared, because a parameter with a compiled-in default is a parameter nobody
recorded.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain import (
    ArmId,
    HeavyHitterConfig,
    MemoryId,
    PinnedOriginConfig,
    PolicyConfiguration,
    RunId,
    SummarizationConfig,
    TokenBudget,
    UtcTimestamp,
    Version,
    make_memory_id,
)
from attention_sink.pilot.protocol import CitationMode, ModelCallLimits, ProtocolBundle

__all__ = [
    "EXACT_TOKEN_COUNT_SOURCES",
    "ModelSpec",
    "PilotRunConfiguration",
    "RunKind",
]

EXACT_TOKEN_COUNT_SOURCES: frozenset[str] = frozenset({"bedrock_count_tokens"})
"""Counter sources that measure text the way the model that reads it does.

A canonical run must be denominated in one of these. Everything else is an
approximation, which is fine to *record* and never fine to present as the model's own
count (ADR-011, amended by ADR-012)."""


class RunKind(StrEnum):
    """What a run's output is allowed to be presented as.

    Carried on the configuration and on every snapshot rather than derived at read
    time. A reader holding one snapshot must be able to tell what it is without
    finding the manifest it came from.
    """

    LOCAL_FIXTURE = "local_fixture"
    """Fixture models, local approximate token budget, local filesystem.

    Validates application behaviour and nothing else. Never scientific evidence
    about the configured production model."""

    AWS_STAGING = "aws_staging"
    """Real models against a non-canonical protocol. Reserved for Phase 7."""

    AWS_CANONICAL = "aws_canonical"
    """The registered experiment. Requires a frozen protocol and real models."""

    @property
    def simulated_expected(self) -> bool:
        """Whether this kind of run is supposed to be driven by fixtures."""
        return self is RunKind.LOCAL_FIXTURE

    @property
    def is_canonical(self) -> bool:
        """Whether this run's output may ever be presented as a result."""
        return self is RunKind.AWS_CANONICAL


class ModelSpec(BaseModel):
    """One model role as the run actually resolved it.

    ``simulated`` travels with the identifier rather than being inferred from it. A
    reader of a manifest must never have to recognise a fixture model by name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    model_id: str = Field(min_length=1, max_length=256)
    region: str = Field(min_length=1, max_length=64)
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    max_output_tokens: int = Field(gt=0)
    simulated: bool


class PilotRunConfiguration(BaseModel):
    """The complete, hashable definition of one pilot run."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1

    # ------------------------------------------------------------- provenance
    run_id: RunId
    run_kind: RunKind
    """What this run's output may be presented as.

    A ``LOCAL_FIXTURE`` run additionally requires a simulated gateway, and an
    ``AWS_CANONICAL`` run a real one; :meth:`require_run_kind_consistent` is where
    that is enforced, before a cycle can spend anything.
    """

    created_at: UtcTimestamp
    app_version: str = Field(min_length=1)
    git_commit: str | None = None

    # ---------------------------------------------------------------- protocol
    protocol_version: Version
    seed_world_version: Version
    stimulus_deck_version: Version
    truth_ledger_version: Version
    interview_version: Version
    protocol_content_hashes: dict[str, str]
    """Digest per protocol file, keyed by repo-relative path."""

    # --------------------------------------------------------------- run shape
    maximum_cycles: int = Field(gt=0)
    checkpoint_cycles: tuple[int, ...] = Field(min_length=1)
    arms: tuple[ArmId, ...] = Field(min_length=1)

    # ----------------------------------------------------------------- budget
    memory_budget_tokens: int = Field(gt=0)
    counter_version: Version
    token_count_source: str = Field(min_length=1, max_length=64)
    """What produced the counts the budget is denominated in.

    ``local_fixture_heuristic`` marks a PROVISIONAL_LOCAL_APPROXIMATION: exact for
    what it measures, and not the production model's tokenisation. Anything not in
    :data:`EXACT_TOKEN_COUNT_SOURCES` is an approximation, and a canonical run may
    not be denominated in one -- see :meth:`require_run_kind_consistent`."""

    # ---------------------------------------------------------------- models
    writer_model: ModelSpec
    embedding_model: ModelSpec
    writer_prompt_version: Version
    summary_prompt_version: Version
    prompt_set_digest: str = Field(min_length=1)

    # ---------------------------------------------------------------- policies
    fifo_policy_version: Version
    lru_policy_version: Version
    heavy_hitter_policy_version: Version
    heavy_hitter_citation_decay: float = Field(ge=0.0, le=1.0)
    heavy_hitter_recency_reserve: int = Field(ge=0)
    pinned_origin_policy_version: Version
    pinned_origin_seed_memory_id: str = Field(min_length=1)
    pinned_origin_memory_id: MemoryId
    """The arm-scoped identifier the pinned-origin arm protects.

    Derived rather than declared. The protocol names a *seed*; the same seed is a
    different memory identifier in every arm, and only one arm's mechanism reads it.
    """

    seeded_random_policy_version: Version
    random_seed: str = Field(min_length=8, max_length=256)
    dreamer_policy_version: Version
    dreamer_target_summary_tokens: int = Field(gt=0)
    dreamer_safety_margin_tokens: int = Field(ge=0)
    dreamer_min_sources: int = Field(ge=2)
    dreamer_fallback_rule: Literal["fifo", "refuse"]

    # ---------------------------------------------------------------- spending
    citation_mode: CitationMode
    model_call_limits: ModelCallLimits
    max_parallel_model_calls: int = Field(gt=0, le=32)

    @model_validator(mode="after")
    def _check_configuration(self) -> Self:
        if len(set(self.arms)) != len(self.arms):
            msg = "a run cannot configure the same arm twice"
            raise ValueError(msg)
        return self

    # ------------------------------------------------------------ derivations

    @property
    def budget(self) -> TokenBudget:
        """The active-memory ceiling every arm operates under, and its counter."""
        return TokenBudget(
            max_active_tokens=self.memory_budget_tokens,
            counter_version=self.counter_version,
        )

    @property
    def policy_configuration(self) -> PolicyConfiguration:
        """The domain's policy parameters, as this protocol declares them.

        The Dreamer's parameters land in ``SummarizationConfig``: the summarising arm
        *is* the Dreamer, and the compression plan it emits is what a Dreamer call is
        written against. There is no second mechanism.
        """
        return PolicyConfiguration(
            heavy_hitter=HeavyHitterConfig(recency_reserve=self.heavy_hitter_recency_reserve),
            pinned_origin=PinnedOriginConfig(pinned_memory_id=self.pinned_origin_memory_id),
            summarization=SummarizationConfig(
                summary_target_token_limit=self.dreamer_target_summary_tokens,
                safety_margin_tokens=self.dreamer_safety_margin_tokens,
                min_sources=self.dreamer_min_sources,
                fifo_fallback_enabled=self.dreamer_fallback_rule == "fifo",
            ),
        )

    @property
    def canonical(self) -> bool:
        """Whether this run's output may ever be presented as a result."""
        return self.run_kind.is_canonical

    @property
    def simulated(self) -> bool:
        """Whether the models behind this run fabricate their responses."""
        return self.writer_model.simulated or self.embedding_model.simulated

    def is_checkpoint(self, cycle: int) -> bool:
        """Whether ``cycle`` is one of the fixed interview checkpoints."""
        return cycle in self.checkpoint_cycles

    def require_run_kind_consistent(self) -> None:
        """Refuse a run whose declared kind disagrees with the gateway it holds.

        Both directions matter. A canonical run driven by fixtures would serve
        fabrications as results; a local run driven by real models would spend
        against a provider during a phase that declared it would not.

        Raises:
            ValueError: The run kind and the models disagree, or a canonical run is
                denominated in an approximate token count.
        """
        if self.canonical and self.simulated:
            msg = (
                f"run {self.run_id} is marked {self.run_kind.value} but its models are "
                f"simulated; a fabricated generation must never be served as a result"
            )
            raise ValueError(msg)
        if self.canonical and self.token_count_source not in EXACT_TOKEN_COUNT_SOURCES:
            msg = (
                f"run {self.run_id} is marked {self.run_kind.value} but its budget is "
                f"denominated in {self.token_count_source!r}, which is an "
                f"approximation; a canonical run is counted with the model's own "
                f"tokeniser or not at all"
            )
            raise ValueError(msg)
        if self.run_kind.simulated_expected and not self.simulated:
            msg = (
                f"run {self.run_id} is marked {self.run_kind.value} but its models are "
                f"real; a local-first phase must not invoke a provider"
            )
            raise ValueError(msg)

    # -------------------------------------------------------------- resolution

    @classmethod
    def from_bundle(
        cls,
        bundle: ProtocolBundle,
        *,
        run_id: str,
        created_at: UtcTimestamp,
        writer_model: ModelSpec,
        embedding_model: ModelSpec,
        prompt_set_digest: str,
        app_version: str,
        git_commit: str | None = None,
        run_kind: RunKind = RunKind.LOCAL_FIXTURE,
        token_count_source: str | None = None,
    ) -> PilotRunConfiguration:
        """Derive a run configuration from validated protocol files.

        ``token_count_source`` overrides what the protocol declares, for a deployment
        that counted with something else. Recorded rather than assumed: a manifest
        that named the protocol's intended counter while the run used another would
        be the one place a reader could not check.

        Raises:
            ValueError: The protocol has not been calibrated, so there is no budget
                and no counter to denominate one in.
        """
        protocol = bundle.protocol
        if protocol.memory_budget_tokens is None or protocol.counter_version is None:
            msg = (
                f"protocol {protocol.protocol_version} has no calibrated budget; "
                f"run `make pilot-calibrate` before configuring a run"
            )
            raise ValueError(msg)

        policies = protocol.policies
        pinned_seed = policies.pinned_origin.pinned_seed_memory_id
        position = next(
            memory.initial_position
            for memory in bundle.seed_world.memories
            if memory.memory_id == pinned_seed
        )
        return cls(
            run_id=run_id,
            run_kind=run_kind,
            created_at=created_at,
            app_version=app_version,
            git_commit=git_commit,
            protocol_version=protocol.protocol_version,
            seed_world_version=protocol.seed_world_version,
            stimulus_deck_version=protocol.stimulus_deck_version,
            truth_ledger_version=protocol.truth_ledger_version,
            interview_version=protocol.interview_version,
            protocol_content_hashes=dict(bundle.digests),
            maximum_cycles=protocol.maximum_cycles,
            checkpoint_cycles=protocol.checkpoint_cycles,
            arms=protocol.arms,
            memory_budget_tokens=protocol.memory_budget_tokens,
            counter_version=protocol.counter_version,
            token_count_source=token_count_source or protocol.token_count_source,
            writer_model=writer_model,
            embedding_model=embedding_model,
            writer_prompt_version=protocol.writer_prompt_version,
            summary_prompt_version=protocol.summary_prompt_version,
            prompt_set_digest=prompt_set_digest,
            fifo_policy_version=policies.fifo.version,
            lru_policy_version=policies.lru.version,
            heavy_hitter_policy_version=policies.heavy_hitter.version,
            heavy_hitter_citation_decay=policies.heavy_hitter.citation_decay,
            heavy_hitter_recency_reserve=policies.heavy_hitter.recency_reserve,
            pinned_origin_policy_version=policies.pinned_origin.version,
            pinned_origin_seed_memory_id=pinned_seed,
            # Seeds are admitted in `initial_position` order, so position k takes
            # arm-local creation slot k-1 in every arm. Only the pinned-origin arm's
            # identifier is resolved: handing the other five a pin would make them
            # differ from each other in something other than mechanism.
            pinned_origin_memory_id=make_memory_id(ArmId.ARM_SINK, position - 1),
            seeded_random_policy_version=policies.seeded_random.version,
            random_seed=policies.seeded_random.random_seed,
            dreamer_policy_version=policies.dreamer.version,
            dreamer_target_summary_tokens=policies.dreamer.target_summary_tokens,
            dreamer_safety_margin_tokens=policies.dreamer.safety_margin_tokens,
            dreamer_min_sources=policies.dreamer.min_sources,
            dreamer_fallback_rule=policies.dreamer.fallback_rule,
            citation_mode=protocol.citation_mode,
            model_call_limits=protocol.model_call_limits,
            max_parallel_model_calls=protocol.max_parallel_model_calls,
        )
