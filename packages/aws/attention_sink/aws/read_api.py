"""The read-API Lambda: the local read API, unchanged, behind API Gateway.

The application is :func:`~attention_sink.api.app.build_app` -- the same routes, the
same schemas, the same filters that keep prepared cycles, future stimuli, and prompt
text out of a response. Mangum translates one HTTP API event into one ASGI scope and
does nothing else. A second implementation of the read surface would be a second place
for a leak to appear, and the two would agree right up until they did not.

Read-only remains a property of the application rather than a rule of the deployment.
No mutating verb is registered, ``tests/unit/test_store_and_routes.py`` asserts the
route table contains only ``GET``, and the CDK assertions check that the deployed
routes match. Three checks, because the cost of a public endpoint that could advance
the experiment is not one anybody gets to discover later.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mangum import Mangum

from attention_sink.api.app import build_app
from attention_sink.aws.composition import build_runtime

__all__ = ["SERVICE_NAME", "handler"]

SERVICE_NAME = "read-api"


def _asgi() -> Any:
    """The ASGI adapter, built once per execution environment.

    Built lazily rather than at import, so a cold start that fails on configuration
    fails inside the handler with a logged reason instead of during module import,
    where the only trace is an ``Unable to import module`` line.
    """
    runtime = build_runtime(SERVICE_NAME)
    app = build_app(runtime.repository, allowed_origins=runtime.settings.allowed_origins)
    # A Lambda handler thread has no event loop, and Mangum reaches for one with
    # `asyncio.get_event_loop()`, which Python 3.12 deprecates for exactly that use
    # and 3.14 removes. Installing one here -- once per execution environment, on the
    # thread that will serve every request -- is the fix rather than the suppression.
    asyncio.set_event_loop(asyncio.new_event_loop())
    # Lifespan off: there is nothing to start up or shut down, and Mangum's lifespan
    # support would run a startup event on every cold start for no work at all.
    return Mangum(app, lifespan="off")


_HANDLER: Any = None


def handler(event: Any, context: Any = None) -> Any:
    """Serve one HTTP API request from committed data.

    Args:
        event: An API Gateway HTTP API v2 payload.
        context: The Lambda context, passed through to the application.

    Returns:
        An API Gateway HTTP API v2 response.
    """
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = _asgi()
    return _HANDLER(event, context)
