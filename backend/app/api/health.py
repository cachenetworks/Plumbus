from fastapi import APIRouter, Depends, HTTPException
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import Role, User
from app.security.security import require_role
from app.services.plex.service import PlexService

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/database")
def database_health(actor: User = Depends(require_role(Role.SUPPORT)), db: Session = Depends(get_db)) -> dict:
    del actor
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "database unavailable")


@router.get("/health/redis")
def redis_health(actor: User = Depends(require_role(Role.SUPPORT))) -> dict:
    del actor
    try:
        client = Redis.from_url(settings.REDIS_URL, socket_timeout=2)
        return {"status": "ok" if client.ping() else "unavailable"}
    except Exception:
        raise HTTPException(503, "redis unavailable")


@router.get("/health/plex")
def plex_health(actor: User = Depends(require_role(Role.SUPPORT))) -> dict:
    del actor
    info = PlexService().connect()
    if not info.connected:
        raise HTTPException(503, "plex unavailable")
    return {"status": "ok", "server_name": info.name, "version": info.version}
