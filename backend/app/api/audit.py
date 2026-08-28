from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Role, User
from app.security.security import require_role

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _serialize(rows: list[AuditLog], actor: User) -> list[dict]:
    sanitized = actor.role == Role.SUPPORT
    return [
        {
            "id": row.id,
            "actor_user_id": row.actor_user_id,
            "event": row.event,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "created_at": row.created_at,
            "ip": None if sanitized else row.ip,
            "user_agent": None if sanitized else row.user_agent,
            "metadata": {} if sanitized else row.event_metadata,
        }
        for row in rows
    ]


@router.get("")
def audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return _serialize(list(rows), actor)


@router.get("/api-requests")
def api_request_logs(
    limit: int = Query(default=100, ge=1, le=500),
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.event == "api.request")
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return _serialize(list(rows), actor)


@router.get("/security-events")
def security_events(
    limit: int = Query(default=100, ge=1, le=500),
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.event.like("security.%"))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return _serialize(list(rows), actor)
