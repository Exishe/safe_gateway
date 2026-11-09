"""Adapter from the proxy's per-request audit hook to `AuditLog.record` (ADR 0021 point 11).

Phase 4 defines an `AuditSink` protocol, called once per request with counts and
hashes, never text. It is built in parallel, so the integration step points that
protocol's method at `record_request` (renaming it if the names differ). The call
waits for the disk, so the proxy should make it from a worker thread.
"""

from collections.abc import Mapping
from typing import Self

from gateway.audit.db import default_audit_path
from gateway.audit.store import AuditLog
from gateway.audit.verify import VerifyResult
from gateway.engine.session_disk import KeyProvider
from gateway.secrets import KeyringKeyProvider


class AuditLogSink:
    def __init__(self, log: AuditLog) -> None:
        self._log = log

    @classmethod
    def open_default(cls, key_provider: KeyProvider | None = None) -> Self:
        """The log at `<SAFEGUARD_HOME>/audit.db`, keyed from the OS keychain by default."""
        provider = key_provider if key_provider is not None else KeyringKeyProvider()
        return cls(AuditLog(default_audit_path(), provider))

    def record_request(  # noqa: PLR0913  (one keyword per column: the proxy's contract)
        self,
        *,
        session_id: str,
        upstream_host: str,
        model: str,
        entity_counts: Mapping[str, int],
        masked_payload_sha256: str,
        bytes_out: int,
        status: int,
        local: bool,
    ) -> int:
        """One row per upstream request. `entity_counts` may be a Counter keyed by EntityType."""
        return self._log.record(
            session_id=session_id,
            upstream_host=upstream_host,
            model=model,
            entity_counts={str(name): count for name, count in entity_counts.items()},
            masked_payload_sha256=masked_payload_sha256,
            bytes_out=bytes_out,
            status=status,
            local=local,
        )

    def verify(self) -> VerifyResult:
        return self._log.verify()

    def close(self) -> None:
        self._log.close()
