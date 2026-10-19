from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def derive_csrf_token(session_token: str, secret_key: str) -> str:
    """由会话令牌派生 CSRF 令牌：服务端可重算校验，无需保存明文。"""
    return hmac.new(
        secret_key.encode("utf-8"),
        f"kp-csrf:{hash_session_token(session_token)}".encode(),
        hashlib.sha256,
    ).hexdigest()
