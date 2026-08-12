"""Small in-process write coordination for the project's SQLite runtime."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from django.db import connection  # type: ignore[import-untyped]

_SQLITE_WRITE_LOCK = RLock()


@contextmanager
def database_write_guard() -> Iterator[None]:
    """Serialize SQLite write phases while leaving fetch work concurrent."""
    if connection.vendor != "sqlite":
        yield
        return
    with _SQLITE_WRITE_LOCK:
        yield
