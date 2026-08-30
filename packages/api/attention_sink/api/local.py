"""The ASGI entry point `make local-api` serves.

A factory rather than a module-level app, so nothing opens a database at import
time. `uvicorn --factory attention_sink.api.local:app` calls this once at startup;
importing the module does nothing at all.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from attention_sink.api.app import DEFAULT_ALLOWED_ORIGINS, build_app

__all__ = ["app"]

DATABASE_ENV = "AS_LOCAL_DATABASE"
"""Where the local database lives. Defaults to the path the make targets use."""

ORIGINS_ENV = "AS_LOCAL_ALLOWED_ORIGINS"
"""Comma-separated browser origins allowed to read this API. Defaults to the local
dev and preview ports the exhibition is served from."""


def app() -> FastAPI:
    """Build the read API over the local SQLite database.

    Raises:
        FileNotFoundError: There is no local database to serve.
    """
    from attention_sink.persistence import SqliteRepository

    path = Path(os.environ.get(DATABASE_ENV, ".pilot-local/pilot.sqlite3"))
    if not path.is_file():
        msg = f"no local database at {path}; run `make local-db-migrate` first"
        raise FileNotFoundError(msg)
    configured = os.environ.get(ORIGINS_ENV, "").strip()
    origins = (
        tuple(origin.strip() for origin in configured.split(",") if origin.strip())
        if configured
        else DEFAULT_ALLOWED_ORIGINS
    )
    return build_app(SqliteRepository(path), allowed_origins=origins)
