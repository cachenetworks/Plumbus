from time import perf_counter
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.models import AuditLog, Session as UserSession
from app.security.security import SESSION_COOKIE, token_hash


class SecurityGateMiddleware(BaseHTTPMiddleware):
    """Origin checks, throttling, security events and sanitized API request logs."""

    def __init__(self, app):
        super().__init__(app)
        self.redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        origins = set(settings.cors_origins)
        parsed = urlparse(settings.APP_URL)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
        self.allowed_origins = origins

    @staticmethod
    def _ip(request: Request) -> str | None:
        return request.client.host if request.client else None

    @staticmethod
    def _same_origin(request: Request, origin: str) -> bool:
        try:
            parsed = urlparse(origin)
            request_host = request.headers.get("host", "").lower()
            return bool(parsed.netloc and parsed.netloc.lower() == request_host)
        except Exception:
            return False

    @staticmethod
    def _actor_id(request: Request) -> int | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        try:
            with SessionLocal() as db:
                session = db.scalar(
                    select(UserSession).where(
                        UserSession.session_hash == token_hash(raw),
                        UserSession.revoked_at.is_(None),
                    )
                )
                return session.user_id if session else None
        except Exception:
            return None

    @staticmethod
    def _write_event(
        request: Request,
        event: str,
        metadata: dict,
        actor_user_id: int | None = None,
    ) -> None:
        try:
            with SessionLocal() as db:
                db.add(
                    AuditLog(
                        actor_user_id=actor_user_id,
                        event=event,
                        target_type="api",
                        target_id=request.url.path[:120],
                        ip=SecurityGateMiddleware._ip(request),
                        user_agent=request.headers.get("user-agent"),
                        event_metadata=metadata,
                    )
                )
                db.commit()
        except Exception:
            # Request logging must never take the API down if the database is unavailable.
            pass

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        path = request.url.path

        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in self.allowed_origins and not self._same_origin(request, origin):
                self._write_event(
                    request,
                    "security.origin_rejected",
                    {"method": request.method, "path": path},
                    self._actor_id(request),
                )
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)

        sensitive = (
            path.startswith("/api/auth/discord/")
            or path.startswith("/api/invites/")
            or path.startswith("/api/setup/")
        )
        if sensitive:
            ip = self._ip(request) or "unknown"
            bucket = int(__import__("time").time() // 300)
            key = f"ratelimit:{ip}:{bucket}:{path.split('/')[3:5]}"
            try:
                count = await self.redis.incr(key)
                if count == 1:
                    await self.redis.expire(key, 310)
                if count > 40:
                    self._write_event(
                        request,
                        "security.rate_limited",
                        {"method": request.method, "path": path, "window_seconds": 300},
                        self._actor_id(request),
                    )
                    return JSONResponse(
                        {"detail": "Too many requests"},
                        status_code=429,
                        headers={"Retry-After": "300"},
                    )
            except Exception:
                pass

        response = await call_next(request)

        # Do not log stream/media bodies or health probes. Query strings are intentionally never
        # persisted because webhook credentials and other sensitive values can appear there.
        if path.startswith("/api/") and not path.startswith("/api/webhooks/"):
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            self._write_event(
                request,
                "api.request",
                {
                    "method": request.method,
                    "path": path,
                    "status_code": response.status_code,
                    "duration_ms": elapsed_ms,
                },
                self._actor_id(request),
            )
        return response
