"""Deterministic explanations for policy decisions.

Every sentence a reader sees about *why* a memory was forgotten is assembled here,
from templates, out of values the policy already recorded. No model is asked to
narrate a decision it did not make: an explanation that was generated rather than
derived would be a plausible story about the mechanism instead of a description of
it, and the whole point of the experiment is that the mechanism is knowable.
"""

from __future__ import annotations

from collections.abc import Sequence

from attention_sink.domain.enums import ArmId, PolicyDecisionCode

__all__ = ["render_explanation"]

_TEMPLATES: dict[PolicyDecisionCode, str] = {
    PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET: (
        "held all {kept} memories: {tokens_before} tokens already fit the {budget}-token budget"
    ),
    PolicyDecisionCode.EVICTED_OLDEST: (
        "evicted {retired} of {eligible} eligible memories, oldest first, to bring "
        "{tokens_before} tokens down to {tokens_after} within the {budget}-token budget"
    ),
    PolicyDecisionCode.EVICTED_LEAST_RECENTLY_CITED: (
        "evicted {retired} of {eligible} eligible memories, least recently verified-cited "
        "first, to bring {tokens_before} tokens down to {tokens_after} within the "
        "{budget}-token budget"
    ),
    PolicyDecisionCode.EVICTED_LOWEST_RETENTION_DENSITY: (
        "evicted {retired} of {eligible} eligible memories, lowest citation weight per "
        "token first, to bring {tokens_before} tokens down to {tokens_after} within the "
        "{budget}-token budget"
    ),
    PolicyDecisionCode.HEAVY_HITTER_RESERVE_BROKEN: (
        "evicted {retired} of {eligible} eligible memories by lowest citation weight per "
        "token, invading the recency reserve because the unreserved memories alone could "
        "not free enough space, bringing {tokens_before} tokens down to {tokens_after} "
        "within the {budget}-token budget"
    ),
    PolicyDecisionCode.EVICTED_OUTSIDE_WINDOW: (
        "evicted {retired} of {eligible} unpinned memories, oldest first, to bring "
        "{tokens_before} tokens down to {tokens_after} within the {budget}-token budget; "
        "pinned memories were not eligible"
    ),
    PolicyDecisionCode.EVICTED_RANDOM: (
        "evicted {retired} of {eligible} eligible memories drawn pseudo-randomly from the "
        "recorded run seed, bringing {tokens_before} tokens down to {tokens_after} within "
        "the {budget}-token budget"
    ),
    PolicyDecisionCode.EVICTED_STATELESS: (
        "evicted all {retired} memories carried in from earlier cycles, leaving only this "
        "cycle's admission at {tokens_after} tokens against the {budget}-token budget"
    ),
    PolicyDecisionCode.RETAINED_ALL: (
        "retained all {kept} memories at {tokens_after} tokens; this reference arm never "
        "evicts and its {budget}-token budget must hold the whole run"
    ),
    PolicyDecisionCode.COMPRESSION_PLANNED: (
        "requested a summary of {sources} memories capped at {summary_limit} tokens; "
        "{tokens_before} tokens exceed the {budget}-token budget and compressing those "
        "sources frees {freed}"
    ),
    PolicyDecisionCode.COMPRESSION_COMMITTED: (
        "replaced {sources} memories with one summary costing {summary_tokens} tokens, "
        "bringing {tokens_before} tokens down to {tokens_after} within the {budget}-token "
        "budget; the summary records every source it compressed"
    ),
    PolicyDecisionCode.SUMMARY_FALLBACK_FIFO: (
        "no legal compression could reach the budget, so the configured fallback evicted "
        "{retired} of {eligible} eligible memories oldest first, bringing {tokens_before} "
        "tokens down to {tokens_after} within the {budget}-token budget"
    ),
}


def render_explanation(
    *,
    arm_id: ArmId,
    cycle: int,
    code: PolicyDecisionCode,
    budget_tokens: int,
    tokens_before: int,
    tokens_after: int,
    kept_memory_ids: Sequence[str] = (),
    retired_memory_ids: Sequence[str] = (),
    eligible_count: int = 0,
    compression_sources: int = 0,
    summary_limit: int = 0,
    summary_tokens: int = 0,
    tokens_freed: int = 0,
) -> str:
    """Render the human-readable account of one policy decision.

    Pure and total: the same arguments always produce the same sentence, and every
    number in it comes from the decision it describes.
    """
    body = _TEMPLATES[code].format(
        kept=len(kept_memory_ids),
        retired=len(retired_memory_ids),
        eligible=eligible_count,
        budget=budget_tokens,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        sources=compression_sources,
        summary_limit=summary_limit,
        summary_tokens=summary_tokens,
        freed=tokens_freed,
    )
    detail = f"; retired {', '.join(retired_memory_ids)}" if retired_memory_ids else ""
    return f"{arm_id.value} cycle {cycle}: {body}{detail}."
