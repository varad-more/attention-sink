"""The local read API.

Wraps the same read services a Lambda handler will wrap in Phase 7. Read-only by
construction: no mutating route is registered, and `tests/integration/test_api.py`
asserts the route table contains only GET.
"""

from attention_sink.api.app import build_app, registered_methods, route_paths
from attention_sink.api.local import app
from attention_sink.api.schemas import (
    ApiEnvelope,
    ArmSummary,
    CycleView,
    GraveyardView,
    InterviewView,
    MemoryView,
    Page,
    RunSummary,
)

__all__ = [
    "ApiEnvelope",
    "ArmSummary",
    "CycleView",
    "GraveyardView",
    "InterviewView",
    "MemoryView",
    "Page",
    "RunSummary",
    "app",
    "build_app",
    "registered_methods",
    "route_paths",
]
