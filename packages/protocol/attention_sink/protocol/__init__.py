"""Versioning and identity of the experimental protocol.

Depends on nothing but the standard library so that every backend service, however
thin, can stamp its output without pulling in a dependency graph.
"""

from attention_sink.protocol.version import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    VersionInfo,
    current_version,
)

__all__ = [
    "PROTOCOL_VERSION",
    "SCHEMA_VERSION",
    "VersionInfo",
    "current_version",
]
