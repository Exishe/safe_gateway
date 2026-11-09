"""The proxy's `AuditSink`, writing the hash-chained log (ADR 0019, ADR 0021 point 11).

The proxy calls `record(event)` once per request, from a worker thread, after the
response ends. Only events whose payload was handed to the upstream adapter become
rows: a request refused before forwarding, `/mask` and session deletes send nothing
anywhere, so they leave none.
"""

from collections.abc import Mapping
from typing import Self

from gateway.audit.db import default_audit_path
from gateway.audit.store import AuditLog
from gateway.audit.verify import VerifyResult
from gateway.engine.session_disk import KeyProvider
from gateway.proxy.audit import AuditEvent
from gateway.secrets import KeyringKeyProvider

NO_RESPONSE = 0  # ADR 0021 point 3: no status line came back (network error, timeout)


def _host(host: str) -> str:
    """The log stores IPv6 addresses in brackets, as in a URL (ADR 0021 point 3)."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


class AuditLogSink:
    def __init__(self, log: AuditLog) -> None:
        self._log = log

    @classmethod
    def open_default(cls, key_provider: KeyProvider | None = None) -> Self:
        """The log at `<SAFEGUARD_HOME>/audit.db`, keyed from the OS keychain by default."""
        provider = key_provider if key_provider is not None else KeyringKeyProvider()
        return cls(AuditLog(default_audit_path(), provider))

    @property
    def log(self) -> AuditLog:
        return self._log

    def record(self, event: AuditEvent) -> None:
        """One row per upstream request. Raises (and writes nothing) for a value out of shape."""
        if event.request_sha256 is None:
            return  # nothing left the machine
        self._log.record(
            session_id="",  # the chat routes keep no session (ADR 0002 point 1)
            upstream_host=_host(event.upstream_host or ""),
            model=event.model or "",
            entity_counts=_plain(event.entity_counts),
            masked_payload_sha256=event.request_sha256,
            bytes_out=event.bytes_out,
            status=NO_RESPONSE if event.upstream_status is None else event.upstream_status,
            local=event.local,
        )

    def verify(self) -> VerifyResult:
        return self._log.verify()

    def close(self) -> None:
        self._log.close()


def _plain(counts: Mapping[str, int]) -> dict[str, int]:
    """Plain type names: the proxy may pass a Counter keyed by `EntityType`."""
    return {str(name): count for name, count in counts.items()}
