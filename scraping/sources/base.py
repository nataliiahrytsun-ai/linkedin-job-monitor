"""Minimal source-neutral contract for vacancy adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

type SourceRecord = dict[str, object]


class SourceCompany(Protocol):
    """Company attributes available to every source adapter."""

    source: str
    source_jobs_url: str | None


@dataclass(frozen=True, slots=True)
class SourceBatch:
    """Ordered source records and the requests used to obtain them."""

    records: tuple[SourceRecord, ...]
    requests_made: int

    def __post_init__(self) -> None:
        if type(self.requests_made) is not int or self.requests_made < 0:
            raise ValueError(
                "requests_made must be a non-negative int, excluding bool"
            )


class SourceError(Exception):
    """Safe adapter failure with the request count known at failure time."""

    def __init__(self, message: str, *, requests_made: int = 0) -> None:
        if type(requests_made) is not int or requests_made < 0:
            raise ValueError(
                "requests_made must be a non-negative int, excluding bool"
            )
        super().__init__(message)
        self.requests_made = requests_made


class SourceAdapter(Protocol):
    """Load one source batch for a persisted company."""

    def fetch(self, *, company: SourceCompany) -> SourceBatch: ...
