from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.security import derive_csrf_token

settings = get_settings()

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="kp-csrf")


def issue_prelogin_token() -> str:
    """签发预登录 CSRF 令牌；同一值写入 Cookie 并由响应返回给页面。"""
    return _serializer.dumps(secrets.token_urlsafe(32))


def verify_prelogin_token(token: str | None, cookie_value: str | None) -> bool:
    if not token or not cookie_value or not secrets.compare_digest(token, cookie_value):
        return False
    try:
        _serializer.loads(token, max_age=settings.csrf_prelogin_seconds)
    except (BadSignature, SignatureExpired):
        return False
    return True


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return origin in settings.allowed_origins


def _reject(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": {"code": "CSRF_INVALID", "message": message}},
    )


class CsrfMiddleware(BaseHTTPMiddleware):
    """浏览器写请求必须来自允许的来源，并携带与当前会话匹配的 CSRF 令牌。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)

        if not _origin_allowed(request):
            return _reject("请求来源校验失败")

        token = request.headers.get("x-csrf-token")
        session_cookie = request.cookies.get(settings.session_cookie_name)

        if session_cookie:
            expected = derive_csrf_token(session_cookie, settings.secret_key)
            if not token or not secrets.compare_digest(token, expected):
                # 旧会话可能已过期或已轮换，而浏览器仍保留其 Cookie。登录
                # 接口必须允许用当前预登录令牌建立新会话，否则用户会被
                # 残留 Cookie 永久挡在登录页。
                cookie_token = request.cookies.get(settings.csrf_cookie_name)
                if request.url.path != "/api/auth/login" or not verify_prelogin_token(
                    token, cookie_token
                ):
                    return _reject("请求令牌校验失败")
        else:
            cookie_token = request.cookies.get(settings.csrf_cookie_name)
            if not verify_prelogin_token(token, cookie_token):
                return _reject("请求令牌校验失败")

        return await call_next(request)
