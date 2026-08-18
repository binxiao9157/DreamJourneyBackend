from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Dict


ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
MIN_SUPPORTED_ITERATIONS = 100_000
MAX_SUPPORTED_ITERATIONS = 2_000_000
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
    try:
        iterations = int(credential.get("iterations") or ITERATIONS)
    except (TypeError, ValueError):
        return False
    if iterations < MIN_SUPPORTED_ITERATIONS or iterations > MAX_SUPPORTED_ITERATIONS:
        return False
    actual = _derive(password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def password_credential_needs_rehash(credential: Dict[str, Any]) -> bool:
    try:
        iterations = int(credential.get("iterations") or 0)
    except (TypeError, ValueError):
        return True
    return credential.get("algorithm") != ALGORITHM or iterations < ITERATIONS


def _derive(password: str, salt: str, iterations: int = ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return digest.hex()
