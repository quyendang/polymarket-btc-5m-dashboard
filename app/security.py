"""Single-admin authentication, CSRF, and login throttling."""

from __future__ import annotations

import secrets
import sys
import time
from collections import defaultdict, deque
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

from app.config import settings


_hasher = PasswordHasher()
_attempts: dict[str, deque[float]] = defaultdict(deque)
_window_seconds = 15 * 60
_max_attempts = 5


@lru_cache
def configured_password_hash() -> str:
    if settings.dashboard_password_hash:
        return settings.dashboard_password_hash
    if settings.production:
        raise RuntimeError("DASHBOARD_PASSWORD_HASH is required in production")
    return _hasher.hash(settings.dashboard_dev_password)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str) -> bool:
    try:
        return _hasher.verify(configured_password_hash(), password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def check_login_rate_limit(ip: str) -> None:
    now = time.time()
    attempts = _attempts[ip]
    while attempts and attempts[0] < now - _window_seconds:
        attempts.popleft()
    if len(attempts) >= _max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Quá nhiều lần đăng nhập. Hãy thử lại sau 15 phút.",
        )


def record_login_failure(ip: str) -> None:
    _attempts[ip].append(time.time())


def clear_login_failures(ip: str) -> None:
    _attempts.pop(ip, None)


def login_session(request: Request) -> str:
    csrf = secrets.token_urlsafe(32)
    request.session.clear()
    request.session.update({"authenticated": True, "csrf": csrf, "login_at": int(time.time())})
    return csrf


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chưa đăng nhập.")


def require_csrf(request: Request) -> None:
    require_auth(request)
    expected = request.session.get("csrf")
    supplied = request.headers.get("x-csrf-token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token không hợp lệ.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.security 'your-password'")
    print(hash_password(sys.argv[1]))
