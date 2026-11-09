"""Tamper-evident local audit log: one chained, HMACed row per upstream request (ADR 0021)."""

from gateway.audit.db import AuditLogError, NotAnAuditLog, default_audit_path
from gateway.audit.sink import AuditLogSink
from gateway.audit.store import AuditChainBroken, AuditLog, DeviceKeyMissing, PurgeResult
from gateway.audit.verify import Reason, VerifyResult, verify_file

__all__ = [
    "AuditChainBroken",
    "AuditLog",
    "AuditLogError",
    "AuditLogSink",
    "DeviceKeyMissing",
    "NotAnAuditLog",
    "PurgeResult",
    "Reason",
    "VerifyResult",
    "default_audit_path",
    "verify_file",
]
