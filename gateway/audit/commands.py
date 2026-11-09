"""`safeguard audit verify | export | purge`. `gateway/cli.py` only registers these.

Exit codes (ADR 0021 point 10, ADR 0022 point 5): 0 done and intact, 1 the log is
broken (an export is still written), 2 nothing could be checked or done.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from keyring.errors import KeyringError

from gateway.audit.db import NotAnAuditLog, default_audit_path, open_readonly
from gateway.audit.export import build_export, day_range, summary_lines, write_private
from gateway.audit.pdf import text_pdf
from gateway.audit.store import (
    MIN_RETENTION_DAYS,
    AuditChainBroken,
    AuditLog,
    DeviceKeyMissing,
    utc_now,
)
from gateway.audit.verify import VerifyResult, verify_file
from gateway.config import config_path, load_config
from gateway.engine.session_disk import KeyProvider
from gateway.secrets import KeyringKeyProvider, load_device_key

OK, BROKEN, CANNOT = 0, 1, 2


def default_key_provider() -> KeyProvider:
    return KeyringKeyProvider()


def _out(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def _err(text: str) -> None:
    sys.stderr.write(f"safeguard: {text}\n")


def _days(value: str) -> int:
    days = int(value)
    if days < 1:
        msg = "must be at least 1"
        raise argparse.ArgumentTypeError(msg)
    return days


def _add_db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", type=Path, help="the audit log file (default: <SAFEGUARD_HOME>/audit.db)"
    )


def register(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    audit = add_parser("audit", help="verify, export or purge the tamper-evident audit log")
    actions = audit.add_subparsers(dest="audit_command", metavar="ACTION")
    _add_db(actions.add_parser("verify", help="check the chain; name the first broken row"))
    export = actions.add_parser("export", help="write signed JSON (and a PDF summary)")
    export.add_argument("--from", dest="start", type=date.fromisoformat, help="first UTC day")
    export.add_argument("--to", dest="end", type=date.fromisoformat, help="last UTC day")
    export.add_argument("-o", "--output", type=Path, required=True, help="the JSON file")
    export.add_argument("--pdf", action="store_true", help="also write OUTPUT with .pdf")
    _add_db(export)
    purge = actions.add_parser("purge", help="delete old rows behind a checkpoint")
    purge.add_argument(
        "--older-than-days",
        type=_days,
        help="default: retention_days in config.toml (required with --db)",
    )
    purge.add_argument(
        "--force",
        action="store_true",
        help=f"allow a period under {MIN_RETENTION_DAYS} days",
    )
    _add_db(purge)
    audit.set_defaults(audit_parser=audit)


def _path(args: argparse.Namespace) -> Path:
    return args.db or default_audit_path()


def _read_key() -> bytes:
    key = load_device_key(default_key_provider())
    if key is None:
        msg = "this keychain has no audit device key, so the log cannot be checked here"
        raise DeviceKeyMissing(msg)
    return key


def _describe(result: VerifyResult) -> str:
    if not result.ok:
        return f"BROKEN at row {result.first_bad_id}: {result.reason} ({result.detail})"
    text = f"OK: {result.rows_checked} rows verified"
    if result.head_id is not None:
        text += f", head row {result.head_id} {result.head_hash}"
    if result.purged_through_id:
        text += f"; rows 1 to {result.purged_through_id} purged under retention"
    return text


def _verify(args: argparse.Namespace) -> int:
    path = _path(args)
    if not path.is_file():
        _err(f"no audit log at {path}")
        return CANNOT
    result = verify_file(path, _read_key())
    _out(f"audit log {path}: {_describe(result)}")
    return OK if result.ok else BROKEN


def _export(args: argparse.Namespace) -> int:
    output: Path = args.output
    if output.suffix.lower() == ".pdf":
        _err("-o names the JSON file; add --pdf to also write the PDF summary")
        return CANNOT
    day_range(args.start, args.end)  # refuse --from after --to before touching anything
    path = _path(args)
    if not path.is_file():
        _err(f"no audit log at {path}")
        return CANNOT
    key = _read_key()
    connection = open_readonly(path)
    try:
        document = build_export(connection, key, start=args.start, end=args.end, now=utc_now())
    finally:
        connection.close()
    write_private(output, (json.dumps(document, indent=2) + "\n").encode("ascii"))
    rows = document["rows"]
    _out(f"wrote {output} ({len(rows) if isinstance(rows, list) else 0} rows, signed)")
    if args.pdf:
        pdf_path = output.with_suffix(".pdf")
        lines = summary_lines(document, output.name)
        write_private(pdf_path, text_pdf(lines, title="SafeGuard Gateway audit export"))
        _out(f"wrote {pdf_path}")
    verification = document["verification"]
    if isinstance(verification, dict) and not verification.get("ok"):
        _err(
            f"warning: the log fails verification at row {verification.get('first_bad_id')} "
            f"({verification.get('reason')}); the export records this"
        )
        return BROKEN
    return OK


def _purge(args: argparse.Namespace) -> int:
    if args.db is not None and args.older_than_days is None:
        # config.toml belongs to the default home's log, not to the file named here.
        _err("with --db, pass --older-than-days N: config.toml applies to the default log only")
        return CANNOT
    # The same reader as `safeguard serve`, so both apply the same period.
    days = args.older_than_days or load_config().retention_days
    if days is None:
        _err(
            "no retention period: pass --older-than-days N or set retention_days "
            f"in {config_path()}"
        )
        return CANNOT
    path = _path(args)
    if not path.is_file():
        _out(f"no audit log at {path}; nothing to purge")
        return OK
    with AuditLog(path, default_key_provider()) as log:
        try:
            result = log.purge(older_than_days=days, force=args.force)
        except AuditChainBroken as error:
            _err(str(error))
            return BROKEN
    if result.checkpoint_id is None:
        _out(f"nothing older than {days} days to purge")
    else:
        _out(
            f"purged {result.deleted} rows older than {days} days; checkpoint row "
            f"{result.checkpoint_id} carries row {result.purged_through_id}'s hash"
        )
    return OK


def run(args: argparse.Namespace) -> int:
    handlers = {"verify": _verify, "export": _export, "purge": _purge}
    handler = handlers.get(args.audit_command)
    if handler is None:
        args.audit_parser.print_help()
        return OK
    try:
        return handler(args)
    except KeyringError as error:
        # The type only, as gateway/secrets.py does: some backends repeat what they read.
        _err(f"cannot read the OS keychain ({type(error).__name__})")
    except (NotAnAuditLog, DeviceKeyMissing, ValueError, OSError) as error:
        _err(str(error))
    return CANNOT
