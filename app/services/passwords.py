from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Dict


ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 210_000
SALT_BYTES = 16


def make_password_credential(password: str) -> Dict[str, Any]:
    salt = os.urandom(SALT_BYTES).hex()
    digest = _derive(password, salt)
    return {
        "algorithm": ALGORITHM,
        "iterations": ITERATIONS,
        "salt": salt,
        "hash": digest,
    }


def verify_password(password: str, credential: Dict[str, Any]) -> bool:
    if credential.get("algorithm") != ALGORITHM:
        return False
    salt = str(credential.get("salt") or "")
    expected = str(credential.get("hash") or "")
    if not salt or not expected:
        return False
    actual = _derive(password, salt, int(credential.get("iterations") or ITERATIONS))
    return hmac.compare_digest(actual, expected)


def _derive(password: str, salt: str, iterations: int = ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return digest.hex()
