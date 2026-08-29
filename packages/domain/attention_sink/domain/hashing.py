"""Deterministic digests used for integrity, replay, and seeded selection.

Everything here is a pure function of its arguments and stable across processes,
machines, and Python builds. Python's built-in ``hash`` is salted per process and
must never appear anywhere a value is stored, compared across runs, or replayed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

__all__ = [
    "content_hash",
    "selection_digest",
    "state_hash",
]

_FIELD_SEPARATOR = "|"


def _join(fields: Iterable[str]) -> str:
    """Join fields into an unambiguously decodable digest payload.

    Each field is length-prefixed. A bare separator would make the payload injective
    only for as long as no field could contain that separator -- true today, because
    ``IDENTIFIER_PATTERN`` excludes it, but true because of an invariant enforced in
    another module. Length-prefixing makes the digest injective on its own terms, so
    a future identifier format cannot silently introduce a collision.
    """
    return _FIELD_SEPARATOR.join(f"{len(field)}:{field}" for field in fields)


def content_hash(text: str) -> str:
    """Return the stable content digest of a memory's text.

    Prefixed with the algorithm so a future migration can store both without
    guessing which record used which.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def state_hash(memory_ids: Iterable[str]) -> str:
    """Return a digest of an active set, in the order given.

    Order is preserved rather than sorted: the sequence in which memories are
    presented to the writer is part of the state, so two arms holding the same
    memories in a different order are not in the same state.
    """
    payload = _join(memory_ids)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def selection_digest(
    *,
    run_random_seed: str,
    arm_id: str,
    cycle: int,
    decision_index: int,
    candidate_memory_ids: Iterable[str],
) -> str:
    """Derive the digest that seeds one pseudo-random eviction choice.

    The payload is ``run_random_seed``, ``arm_id``, ``cycle``, ``decision_index``,
    then the candidate identifiers in ascending order, each length-prefixed and joined
    by ``|``. Sorting the candidates means the
    draw depends on *which* memories were eligible and not on the order the caller
    happened to enumerate them in, so the choice replays from stored state alone.

    Returns:
        Hex-encoded SHA-256 digest, without an algorithm prefix, because it is
        consumed as an integer seed rather than compared as a content digest.
    """
    fields = (
        run_random_seed,
        arm_id,
        str(cycle),
        str(decision_index),
        *sorted(candidate_memory_ids),
    )
    return hashlib.sha256(_join(fields).encode("utf-8")).hexdigest()
