"""Lightweight authentication for the wireless dashboard.

Single operator token with a random salt, stored hashed in a secrets file
outside the repository. On first run a token is generated and printed to the
service log so the operator can obtain it once over the trusted local link.
Never stored in plaintext in the repo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from ..util.logger import get_logger

log = get_logger("security")


def _hash(token: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + token).encode()).hexdigest()


def _file_hmac(token: str, salt: str) -> str:
    return hmac.new(salt.encode(), token.encode(), hashlib.sha256).hexdigest()


class TokenStore:
    def __init__(self, secrets_file: Path) -> None:
        self._file = Path(secrets_file)
        self._salt = ""
        self._hash = ""
        self._load_or_create()

    def _load_or_create(self) -> None:
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text())
                self._salt = data["salt"]
                self._hash = data["token_hash"]
                return
            except Exception:  # noqa: BLE001
                pass
        self._salt = secrets.token_hex(16)
        token = secrets.token_urlsafe(18)
        self._hash = _hash(token, self._salt)
        self._file.write_text(json.dumps(
            {"salt": self._salt, "token_hash": self._hash, "generated_once": True}))
        self._file.chmod(0o600)
        # Operator reads the token from the service log on first boot.
        log.info("DASHBOARD AUTH TOKEN GENERATED (write this down once): " + token)

    def verify(self, token: str) -> bool:
        if not token:
            return False
        return hmac.compare_digest(_hash(token, self._salt), self._hash)


def random_session_id() -> str:
    return secrets.token_urlsafe(24)
