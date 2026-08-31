from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import audit, auth, entry, health, history, invites, movies, playback, playback_admin, playback_targets, plex, settings as settings_api, setup, users, webhooks
from app.core.config import settings
from app.security.middleware import SecurityGateMiddleware

app = FastAPI(
    title="Plumbus API",
    version="1.0.0",
    docs_url="/api/docs" if settings.APP_ENV != "production" else None,
    redoc_url=None,
)

app.add_middleware(SecurityGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Range", "X-CSRF-Token"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


for router in (
    health.router,
    entry.router,
    setup.router,
    auth.router,
    invites.router,
    movies.router,
    movies.art_router,
    playback.router,
    playback_targets.router,
    playback_admin.router,
    plex.router,
    users.router,
    history.router,
    audit.router,
    settings_api.router,
    webhooks.router,
):
    app.include_router(router)
