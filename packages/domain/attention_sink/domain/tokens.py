"""Active-memory token accounting.

The budget is denominated in *budget tokens*: an explicit, versioned experimental
unit, not any model vendor's tokenisation. See ``docs/adr/008-budget-token-accounting.md``
for why. What matters for validity is that the same counter version is applied to
every arm in a run, and that the version is recorded in the run manifest.

All budget arithmetic in this package is integer arithmetic. Floats appear only in
citation scores, where a fraction is part of the mechanism; letting one into a
budget comparison would make "within budget" depend on rounding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.domain.identifiers import Version

__all__ = ["HeuristicTokenCounter", "TokenBudget", "TokenCounter"]


@runtime_checkable
class TokenCounter(Protocol):
    """Maps text to the budget-token cost of holding it in active memory."""

    @property
    def version(self) -> str:
        """Stable identifier recorded in the run manifest alongside every count."""

    def count(self, text: str) -> int:
        """Return the budget-token cost of ``text``. Zero only for blank text."""


@dataclass(frozen=True, slots=True)
class HeuristicTokenCounter:
    """Deterministic, dependency-free budget-token estimator.

    Formula (``heuristic-v1``): split on ASCII whitespace; charge each resulting word
    ``ceil(len(word) / 4)`` tokens with a floor of 1; charge 0 for text that is empty
    or all whitespace.

    Invariants relied on elsewhere: the count is a pure function of the string, is
    monotone non-decreasing under concatenation, and is identical on every machine
    and Python build. That is sufficient for a budget applied identically to arms
    that are being compared only against each other.
    """

    version: str = "heuristic-v1"

    def count(self, text: str) -> int:
        """Return the budget-token cost of ``text``."""
        return sum(max(1, math.ceil(len(word) / 4)) for word in text.split())


class TokenBudget(BaseModel):
    """The active-memory ceiling one arm operates under, and how it is measured.

    Identical for every arm in a canonical run. The counter version travels with the
    ceiling because a budget of 1000 means nothing without the counter that produced
    the numbers being compared against it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    max_active_tokens: int = Field(gt=0)
    counter_version: Version

    def is_satisfied_by(self, total_tokens: int) -> bool:
        """Whether an active set costing ``total_tokens`` fits this budget."""
        return total_tokens <= self.max_active_tokens
