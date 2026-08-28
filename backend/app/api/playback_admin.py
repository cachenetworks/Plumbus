from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, PlaybackToken, Role, User
from app.security.security import get_current_user, require_role
from app.services.playback.service import PlaybackService

router = APIRouter(prefix="/api/playback", tags=["playback-admin"])


@router.get("/active")
def active_playback_tokens(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    rows = db.scalars(
        select(PlaybackToken)
        .where(
            PlaybackToken.revoked_at.is_(None),
            PlaybackToken.expires_at > datetime.now(UTC),
        )
        .order_by(PlaybackToken.created_at.desc())
        .limit(250)
    ).all()
    result: list[dict] = []
    for row in rows:
        user = db.get(User, row.user_id)
        movie = db.get(Movie, row.movie_id)
        result.append(
            {
                "id": row.id,
                "user_id": row.user_id,
                "discord_id": user.discord_id if user else None,
                "username": user.global_name or user.username if user else None,
                "movie_id": row.movie_id,
                "movie_title": movie.title if movie else None,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "ip": row.ip,
                "user_agent": row.user_agent,
            }
        )
    return result


@router.post("/tokens/{token_id}/revoke")
def revoke_playback_token(
    token_id: int,
    request: Request,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    token = db.get(PlaybackToken, token_id)
    if not token:
        raise HTTPException(404, "Playback token not found")
    if token.user_id != actor.id and actor.role not in {Role.ADMIN, Role.SUPERADMIN}:
        raise HTTPException(403, "Not allowed to revoke this playback token")
    if token.revoked_at is None:
        PlaybackService(db).revoke(token)
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                event="playback.revoked",
                target_type="playback_token",
                target_id=str(token.id),
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                event_metadata={"movie_id": token.movie_id, "owner_user_id": token.user_id},
            )
        )
        db.commit()
    return {"id": token.id, "revoked": True}
