"""Canonical serialisation, and the digests taken over it.

A snapshot hash is only worth recording if two processes that hold the same value
produce the same hash. Python's default JSON output does not give that: key order
follows insertion, separators carry whitespace, and a float can render differently
between builds. Everything hashed in this package therefore goes through
:func:`canonical_json` first.

The rules are fixed and deliberately boring: keys sorted, no insignificant
whitespace, Unicode kept as Unicode rather than escaped, and Pydantic's JSON mode so
that a timestamp is an ISO string and an enum is its value.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

__all__ = ["canonical_digest", "canonical_json", "canonical_payload"]


def canonical_payload(value: Any) -> Any:
    """Reduce one value ``json`` cannot serialise to plain JSON types.

    Used as :func:`json.dumps`'s ``default`` hook, so it runs at any depth rather than
    only on the top-level object: a mapping of models, a model holding models, and a
    bare model all reduce the same way. Dumping in JSON mode is what turns a
    ``datetime`` into a fixed ISO string and a ``StrEnum`` into its value.

    Raises:
        TypeError: The value is not a Pydantic model and ``json`` could not encode it,
            which means something reached a digest that has no canonical form.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    msg = f"{type(value).__name__} has no canonical JSON form"
    raise TypeError(msg)


def canonical_json(value: Any) -> str:
    """Serialise ``value`` to the one byte sequence this project hashes.

    Sorted keys, no insignificant whitespace, and literal Unicode. Two equal values
    always produce identical output, on any machine and any Python build.
    """
    return json.dumps(
        value,
        default=canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_digest(value: Any) -> str:
    """Return the ``sha256:`` digest of ``value``'s canonical serialisation.

    Prefixed with the algorithm, matching ``attention_sink.domain.content_hash``, so
    a reader never has to guess which function produced a stored digest.
    """
    payload = canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
