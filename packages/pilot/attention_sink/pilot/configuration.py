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

__all__ = ["ModelSpec", "PilotRunConfiguration"]


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
    canonical: bool
    """Whether this run's output may ever be presented as a result.

    False for every fixture run. A canonical run additionally requires a frozen,
    undrifted protocol and a non-simulated gateway; :meth:`require_canonical_ready`
    is where that is enforced.
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
    max_cycles: int = Field(gt=0)
    checkpoint_cycles: tuple[int, ...] = Field(min_length=1)
    arms: tuple[ArmId, ...] = Field(min_length=1)

    # ----------------------------------------------------------------- budget
    memory_budget_tokens: int = Field(gt=0)
    counter_version: Version

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
    def simulated(self) -> bool:
        """Whether the models behind this run fabricate their responses."""
        return self.writer_model.simulated or self.embedding_model.simulated

    def is_checkpoint(self, cycle: int) -> bool:
        """Whether ``cycle`` is one of the fixed interview checkpoints."""
        return cycle in self.checkpoint_cycles

    def require_canonical_ready(self) -> None:
        """Refuse to treat a simulated run as canonical.

        Raises:
            ValueError: The run is marked canonical but its models are fixtures.
        """
        if self.canonical and self.simulated:
            msg = (
                f"run {self.run_id} is marked canonical but its models are simulated; "
                f"a fabricated generation must never be served as a result"
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
        canonical: bool = False,
    ) -> PilotRunConfiguration:
        """Derive a run configuration from validated protocol files.

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
            canonical=canonical,
            created_at=created_at,
            app_version=app_version,
            git_commit=git_commit,
            protocol_version=protocol.protocol_version,
            seed_world_version=protocol.seed_world_version,
            stimulus_deck_version=protocol.stimulus_deck_version,
            truth_ledger_version=protocol.truth_ledger_version,
            interview_version=protocol.interview_version,
            protocol_content_hashes=dict(bundle.digests),
            max_cycles=protocol.max_cycles,
            checkpoint_cycles=protocol.checkpoint_cycles,
            arms=protocol.arms,
            memory_budget_tokens=protocol.memory_budget_tokens,
            counter_version=protocol.counter_version,
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
