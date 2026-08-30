"""Local transactional persistence for the pilot.

The SQLite adapter that satisfies
:class:`~attention_sink.pilot.repositories.PilotRepository` in Phases 5-6. Phase 7
adds a DynamoDB adapter beside it and changes nothing above the adapter line.

Nothing outside this package imports :mod:`sqlite3`.
"""

from attention_sink.persistence.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    current_version,
)
from attention_sink.persistence.sqlite import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_LOCK_TTL_SECONDS,
    SqliteRepository,
)

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "DEFAULT_LOCK_TTL_SECONDS",
    "MIGRATIONS",
    "Migration",
    "SqliteRepository",
    "apply_migrations",
    "current_version",
]
