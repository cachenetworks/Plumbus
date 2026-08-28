from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings


class SecurityGateMiddleware(BaseHTTPMiddleware):
    """Origin checks plus Redis fixed-window throttles for sensitive unauthenticated flows."""

    def __init__(self, app):
        super().__init__(app)
        self.redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        origins = set(settings.cors_origins)
        parsed = urlparse(settings.APP_URL)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
        self.allowed_origins = origins

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in self.allowed_origins:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)

        path = request.url.path
        sensitive = path.startswith("/api/auth/discord/") or path.startswith("/api/invites/")
        if sensitive:
            ip = request.client.host if request.client else "unknown"
            bucket = int(__import__("time").time() // 300)
            key = f"ratelimit:{ip}:{bucket}:{path.split('/')[3:5]}"
            try:
                count = await self.redis.incr(key)
                if count == 1:
                    await self.redis.expire(key, 310)
                if count > 40:
                    return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "300"})
            except Exception:
                # Availability-safe fallback: authenticated authorization still applies. Redis health
                # is independently monitored and production operators should treat outage as degraded.
                pass

        return await call_next(request)
