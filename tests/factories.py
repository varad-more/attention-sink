"""Builders and Hypothesis strategies shared by the Phase 2 test suites.

Kept in one place so that every policy is exercised against states built the same
way. A per-file builder would let two suites drift into testing subtly different
worlds, and the whole point of the experiment is that the arms see identical input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hypothesis import strategies as st

from attention_sink.domain import (
    ArmId,
    CompressingMemoryPolicy,
    CycleContext,
    HeuristicTokenCounter,
    Memory,
    MemoryKind,
    MemoryPolicy,
    MemoryState,
    PolicyDecision,
    TokenBudget,
)

RUN_ID = "run_test"
RUN_SEED = "seed-0123456789abcdef"
PROTOCOL_VERSION = "2026.08-test"
PROMPT_VERSION = "prompts-v1"
STIMULUS_ID = "stim_000"
COUNTER = HeuristicTokenCounter()

CURRENT_CYCLE = 20
"""The cycle every generated state is rebalanced in.

Fixed and well above the birth cycles the strategies produce, so a generated case
exercises eviction rather than accidentally protecting everything as newly born.
"""


@dataclass(frozen=True)
class MemorySpec:
    """A memory to build, described only by what a policy can see."""

    tokens: int = 10
    cycle: int = 0
    kind: MemoryKind = MemoryKind.GENERATED
    pinned: bool = False
    citation_count: int = 0
    last_cited: int | None = None
    score: float = 0.0
    parents: tuple[str, ...] = field(default=())


def budget(max_tokens: int) -> TokenBudget:
    return TokenBudget(max_active_tokens=max_tokens, counter_version=COUNTER.version)


def context(
    arm: ArmId,
    *,
    cycle: int = CURRENT_CYCLE,
    seed: str = RUN_SEED,
    run_id: str = RUN_ID,
) -> CycleContext:
    return CycleContext(
        run_id=run_id,
        arm_id=arm,
        cycle=cycle,
        stimulus_id=STIMULUS_ID,
        protocol_version=PROTOCOL_VERSION,
        prompt_version=PROMPT_VERSION,
        run_random_seed=seed,
    )


def build_state(arm: ArmId, specs: list[MemorySpec], *, run_id: str = RUN_ID) -> MemoryState:
    """Build a memory state holding one active memory per spec, in order."""
    state = MemoryState(run_id=run_id, arm_id=arm)
    for index, spec in enumerate(specs):
        memory = state.mint(
            text=f"memory {index} of {arm.value}",
            token_count=spec.tokens,
            memory_kind=spec.kind,
            cycle=spec.cycle,
            source_stimulus_id=f"stim_{spec.cycle:03d}",
            parent_memory_ids=spec.parents,
            pinned=spec.pinned,
        )
        changes: dict[str, object] = {}
        if spec.citation_count:
            changes["citation_count"] = spec.citation_count
        if spec.last_cited is not None:
            changes["last_verified_citation_cycle"] = spec.last_cited
        if spec.score:
            changes["discounted_citation_score"] = spec.score
        if changes:
            memory = memory.evolve(**changes)
        state = state.admit([memory])
    return state


def uniform_state(arm: ArmId, *, count: int, tokens: int = 10) -> MemoryState:
    """A state of ``count`` interchangeable memories, one born per cycle."""
    return build_state(arm, [MemorySpec(tokens=tokens, cycle=i) for i in range(count)])


def summary_for(
    state: MemoryState, plan_sources: tuple[str, ...], *, cycle: int, tokens: int = 5
) -> Memory:
    """Mint the summary a compression plan asked for."""
    return state.mint(
        text="a compressed account of what came before",
        token_count=tokens,
        memory_kind=MemoryKind.SUMMARY,
        cycle=cycle,
        parent_memory_ids=plan_sources,
    )


# --------------------------------------------------------------------- strategies

_kinds = st.sampled_from([MemoryKind.SEED, MemoryKind.GENERATED, MemoryKind.EXTERNAL])


@st.composite
def memory_specs(draw: st.DrawFn, *, min_size: int = 0, max_size: int = 12) -> list[MemorySpec]:
    """Generate a plausible active set, including never-cited and pinned memories.

    Birth cycles stay strictly below :data:`CURRENT_CYCLE` so that every generated
    memory is eligible for eviction; the current cycle's protection is covered by
    dedicated unit tests where it can be asserted precisely rather than sampled.
    """
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    specs: list[MemorySpec] = []
    for _ in range(size):
        cycle = draw(st.integers(min_value=0, max_value=CURRENT_CYCLE - 1))
        cited = draw(st.booleans())
        specs.append(
            MemorySpec(
                tokens=draw(st.integers(min_value=1, max_value=40)),
                cycle=cycle,
                kind=draw(_kinds),
                pinned=draw(st.booleans()) if draw(st.integers(0, 4)) == 0 else False,
                citation_count=draw(st.integers(min_value=1, max_value=9)) if cited else 0,
                last_cited=draw(st.integers(min_value=cycle, max_value=CURRENT_CYCLE - 1))
                if cited
                else None,
                score=draw(
                    st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)
                ),
            )
        )
    return specs


@st.composite
def states_and_budgets(draw: st.DrawFn, arm: ArmId) -> tuple[MemoryState, TokenBudget]:
    """A generated state paired with a budget that may or may not be reachable."""
    specs = draw(memory_specs())
    state = build_state(arm, specs)
    total = max(state.active_tokens, 1)
    return state, budget(draw(st.integers(min_value=1, max_value=total + 10)))


def resolve(
    policy: MemoryPolicy,
    state: MemoryState,
    token_budget: TokenBudget,
    cycle_context: CycleContext,
) -> tuple[PolicyDecision, MemoryState]:
    """Drive a policy to a final decision, supplying summaries when it asks for them.

    Every arm but the summarising one reaches a final decision in a single call.
    This wrapper lets the shared contract tests treat all six identically instead of
    special-casing the two-stage arm in every assertion.
    """
    working = state
    decision = policy.rebalance(working, token_budget, cycle_context)
    while (plan := decision.compression_plan) is not None:
        assert isinstance(policy, CompressingMemoryPolicy), (
            f"{policy.arm_id.value} requested a compression but cannot finalise one"
        )
        if decision.retirements:
            working = working.apply(decision)
        summary = summary_for(
            working,
            plan.source_memory_ids,
            cycle=cycle_context.cycle,
            tokens=min(plan.summary_target_token_limit, 5),
        )
        decision = policy.finalize_compression(working, token_budget, cycle_context, plan, summary)
    return decision, working
