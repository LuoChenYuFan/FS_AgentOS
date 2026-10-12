"""Password hashes. The gateway stores hashes, never the password itself."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_password(password: str, *, iterations: int) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_text, salt, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        int(iterations_text),
    )
    return hmac.compare_digest(digest.hex(), digest_hex)
