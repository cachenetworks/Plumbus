import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Callable

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import ROLE_RANK, Role, Session as UserSession, User, UserStatus

SESSION_COOKIE = "plumbus_session"


def utcnow() -> datetime:
    return datetime.now(UTC)


def random_token(bytes_length: int = 32) -> str:
    return secrets.token_urlsafe(bytes_length)


def token_hash(token: str) -> str:
    return hmac.new(settings.TOKEN_ENCRYPTION_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_expiry(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    raw_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    if not raw_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    session = db.scalar(
        select(UserSession).where(
            UserSession.session_hash == token_hash(raw_session),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
    )
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, session.user_id)
    if not user or user.status == UserStatus.BANNED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account unavailable")
    if user.status == UserStatus.SUSPENDED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account suspended")
    request.state.user = user
    return user


def require_role(minimum: Role) -> Callable:
    def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[minimum]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return dependency


def assert_can_assign_role(actor: User, target_role: Role) -> None:
    if target_role == Role.SUPERADMIN and actor.role != Role.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Only SuperAdmin can assign SuperAdmin")
    if target_role == Role.ADMIN and actor.role != Role.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Only SuperAdmin can create Admin access")
    if ROLE_RANK[target_role] > ROLE_RANK[actor.role]:
        raise HTTPException(status_code=403, detail="Cannot assign a role above your own")
