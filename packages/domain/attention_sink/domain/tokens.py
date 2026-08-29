"""Active-memory token accounting.

The budget is denominated in *budget tokens*, an explicit, versioned experimental
unit -- not in any model vendor's tokenisation. See ``docs/adr/0001-token-accounting.md``
for why. What matters for validity is that the same counter version is applied to
every arm in a run, and that the version is recorded in the run manifest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["HeuristicTokenCounter", "TokenCounter"]


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

    Formula (``heuristic-v1``): split on ASCII whitespace; charge each resulting
    word ``ceil(len(word) / 4)`` tokens with a floor of 1; charge a minimum of 1
    token for any non-empty text and 0 for text that is empty or all whitespace.

    Invariants relied on elsewhere: the count is a pure function of the string, is
    monotone non-decreasing under concatenation, and is identical on every machine
    and Python build. That is sufficient for a budget that must be applied
    identically across arms.
    """

    version: str = "heuristic-v1"

    def count(self, text: str) -> int:
        """Return the budget-token cost of ``text``."""
        return sum(max(1, math.ceil(len(word) / 4)) for word in text.split())
