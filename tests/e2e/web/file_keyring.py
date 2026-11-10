"""A keyring backend for the browser tests only: secrets in a JSON file.

The test machine has no OS keychain, and `safeguard serve` rightly refuses to start
without one, because the audit log's device key lives there (ADR 0021 points 7 and
12). The e2e test selects this class with `PYTHON_KEYRING_BACKEND` and points
`SAFEGUARD_E2E_KEYRING` at a file in its temporary directory, so the real audit
path runs, keys and all. Never use it outside tests: the file is plain text.
"""

import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

VARIABLE = "SAFEGUARD_E2E_KEYRING"


class FileKeyring(KeyringBackend):
    priority = 1  # only ever selected by name, through PYTHON_KEYRING_BACKEND

    @staticmethod
    def _path() -> Path:
        path = os.environ.get(VARIABLE)
        if not path:
            msg = f"set {VARIABLE} to the test's keyring file"
            raise RuntimeError(msg)
        return Path(path)

    def _read(self) -> dict[str, dict[str, str]]:
        path = self._path()
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        path = self._path()
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file)
        temporary.replace(path)

    def get_password(self, service: str, username: str) -> str | None:
        return self._read().get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        data = self._read()
        data.setdefault(service, {})[username] = password
        self._write(data)

    def delete_password(self, service: str, username: str) -> None:
        data = self._read()
        if username not in data.get(service, {}):
            raise PasswordDeleteError(username)
        del data[service][username]
        self._write(data)
