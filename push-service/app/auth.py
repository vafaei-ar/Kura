"""Clinician authentication primitives (no third-party deps).

Two concerns, both implemented with the Python standard library so the service
stays easy to deploy on the free Azure tier (we deliberately avoid passlib/
bcrypt wheels that have broken the build before):

1. Password hashing  — PBKDF2-HMAC-SHA256 with a per-user random salt. Stored as
   ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``. Verification is constant
   time. Passwords are NEVER stored in plaintext.

2. Session tokens — a compact signed token ``<payload_b64>.<sig_b64>`` where the
   payload is JSON ``{"sub": clinician_id, "exp": unix_ts}`` and the signature is
   HMAC-SHA256 over the payload using the server's signing secret. Stateless, so
   it needs no session table; tampering or expiry is rejected on verify.

This layer is intentionally swappable: when the deployment moves to institutional
SSO (PSU Microsoft Entra), only the token issuance/verification is replaced — the
clinician table and roles carry over.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 200_000


# --- password hashing ----------------------------------------------------

def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_s, salt_hex, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


# --- session tokens ------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_token(clinician_id: str, secret: str, *, ttl_hours: int = 12) -> str:
    payload = {"sub": clinician_id, "exp": int(time.time()) + ttl_hours * 3600}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64e(sig)}"


def verify_token(token: str, secret: str) -> Optional[str]:
    """Return the clinician_id if the token is valid and unexpired, else None."""
    try:
        payload_b64, sig_b64 = token.split(".")
    except (ValueError, AttributeError):
        return None
    expected_sig = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected_sig, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(payload_b64))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("sub")
