# -*- coding: utf-8 -*-
import os
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
AUTH_RATE_LIMIT_PER_MINUTE = int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "20"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(25 * 1024 * 1024)))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").lower() in ("1", "true", "yes")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        length = request.headers.get("content-length")
        if length and int(length) > MAX_REQUEST_BYTES:
            return JSONResponse({"ok": False, "error": "request too large"}, status_code=413)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cache-Control", "no-store")
        if COOKIE_SECURE:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class InMemoryRateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        hits = self._hits[key]
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


rate_limiter = InMemoryRateLimiter()


async def rate_limit(request: Request):
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    limit = AUTH_RATE_LIMIT_PER_MINUTE if request.url.path.startswith("/api/auth") else RATE_LIMIT_PER_MINUTE
    if not rate_limiter.allow(f"{ip}:{request.url.path}", limit):
        return JSONResponse({"ok": False, "error": "rate limit exceeded"}, status_code=429)
    return None


def cookie_options() -> dict:
    return {
        "max_age": 86400 * int(os.getenv("TOKEN_EXPIRE_DAYS", "30")),
        "httponly": True,
        "samesite": os.getenv("COOKIE_SAMESITE", "lax"),
        "secure": COOKIE_SECURE,
    }

