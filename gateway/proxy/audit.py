"""The audit hook point: one event per request, counts and hashes only (ADR 0019).

`safeguard serve` plugs in `gateway.audit.AuditLogSink`, the hash-chained log
(ADR 0021); `NullAuditSink` is for `--no-audit` and for tests. An event has no
field that can hold request or response text.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from gateway.engine.mask import Mapping as PlaceholderMapping
from gateway.engine.placeholders import CANONICAL


@dataclass(frozen=True)
class AuditEvent:
    route: str
    outcome: str  # forwarded, rejected, upstream_error, masked, session_deleted, ...
    status_code: int
    started_at: datetime
    duration_ms: int
    provider: str | None = None
    upstream_host: str | None = None
    model: str | None = None
    stream: bool = False
    entity_counts: Mapping[str, int] = field(default_factory=dict)  # type -> distinct values
    # The /mask session a chat request was masked in (ADR 0025). The audit log keeps
    # only its sha256 (ADR 0021 point 3); it is never forwarded or logged.
    session_id: str | None = None
    unresolved: int = 0
    request_sha256: str | None = None  # of the exact bytes sent upstream
    response_sha256: str | None = None  # of the exact bytes received from upstream
    bytes_out: int = 0  # length of the exact bytes sent upstream
    upstream_status: int | None = None  # the upstream's HTTP status; None when none came back
    local: bool = False  # the upstream runs on this machine (Ollama)


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...


@runtime_checkable
class AuditReader(Protocol):
    """A sink that can also show its log, for `GET /audit` (metadata only)."""

    def recent(self, limit: int, before_id: int | None) -> list[dict[str, object]]: ...

    def verification(self) -> dict[str, object]: ...


class NullAuditSink:
    """Records nothing: `safeguard serve --no-audit`, and tests."""

    def record(self, event: AuditEvent) -> None:
        pass


def entity_counts(mapping: PlaceholderMapping) -> dict[str, int]:
    """Distinct masked values per entity type, read from the placeholders only."""
    counts: Counter[str] = Counter()
    for placeholder in mapping.to_dict():
        match = CANONICAL.fullmatch(placeholder)
        if match is not None:
            counts[match.group("type")] += 1
    return dict(counts)


def entity_counts_present(texts: Iterable[str], mapping: PlaceholderMapping) -> dict[str, int]:
    """Distinct placeholders of `mapping` that appear in `texts`, per type (ADR 0025).

    For a chat request masked in a session: the session holds more than the request
    sent, and the audit row should count what left the machine.
    """
    present = {
        match.group(): match.group("type")
        for text in texts
        for match in CANONICAL.finditer(text)
        if match.group() in mapping
    }
    return dict(Counter(present.values()))
