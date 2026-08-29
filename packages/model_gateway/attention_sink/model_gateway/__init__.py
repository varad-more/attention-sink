"""Configuration and adapters for model access.

Holds the runtime-mode decision that keeps simulated output out of canonical
results, and the local fixture adapter that decision gates.
"""

from attention_sink.model_gateway.fixtures import (
    FixtureFile,
    FixtureModelGateway,
    FixtureNotFoundError,
)
from attention_sink.model_gateway.settings import (
    ConfigurationError,
    ModelConfig,
    RuntimeMode,
    RuntimeSettings,
)

__all__ = [
    "ConfigurationError",
    "FixtureFile",
    "FixtureModelGateway",
    "FixtureNotFoundError",
    "ModelConfig",
    "RuntimeMode",
    "RuntimeSettings",
]
