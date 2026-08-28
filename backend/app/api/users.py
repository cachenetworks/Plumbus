from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, ROLE_RANK, Role, User, UserStatus
from app.security.security import assert_can_assign_role, require_role

router = APIRouter(prefix="/api/admin/users", tags=["users"])


class RoleChange(BaseModel):
    role: Role


class StatusChange(BaseModel):
    status: UserStatus


def _assert_can_manage(actor: User, target: User) -> None:
    if target.role == Role.SUPERADMIN and actor.role != Role.SUPERADMIN:
        raise HTTPException(403, "Only SuperAdmin can manage a SuperAdmin")
    if ROLE_RANK[target.role] >= ROLE_RANK[actor.role] and target.id != actor.id:
        raise HTTPException(403, "Cannot manage a peer or higher role")


def _audit(db: Session, actor: User, event: str, target: User, request: Request, metadata: dict | None = None) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event=event,
            target_type="user",
            target_id=str(target.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            metadata=metadata or {},
        )
    )


@router.get("")
def list_users(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    users = db.scalars(select(User).order_by(User.registered_at.desc()).limit(500)).all()
    return [
        {
            "id": u.id,
            "discord_id": u.discord_id,
            "username": u.username,
            "global_name": u.global_name,
            "avatar": u.avatar,
            "role": u.role.value,
            "status": u.status.value,
            "joined": u.registered_at,
            "last_login": u.last_login_at,
            "invite_id": u.invite_id,
        }
        for u in users
    ]


@router.patch("/{user_id}/role")
def change_role(
    user_id: int,
    payload: RoleChange,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    _assert_can_manage(actor, target)
    assert_can_assign_role(actor, payload.role)
    if target.role == Role.SUPERADMIN and payload.role != Role.SUPERADMIN:
        count = db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPERADMIN)) or 0
        if count <= 1:
            raise HTTPException(409, "Cannot demote the final SuperAdmin")
    old = target.role
    target.role = payload.role
    _audit(db, actor, "user.role_changed", target, request, {"from": old.value, "to": payload.role.value})
    db.commit()
    return {"id": target.id, "role": target.role.value}


@router.patch("/{user_id}/status")
def change_status(
    user_id: int,
    payload: StatusChange,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    _assert_can_manage(actor, target)
    if target.role == Role.SUPERADMIN and payload.status != UserStatus.ACTIVE:
        count = db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPERADMIN, User.status == UserStatus.ACTIVE)) or 0
        if count <= 1:
            raise HTTPException(409, "Cannot disable the final active SuperAdmin")
    previous = target.status
    target.status = payload.status
    event = {
        UserStatus.SUSPENDED: "user.suspended",
        UserStatus.BANNED: "user.banned",
        UserStatus.ACTIVE: "user.unsuspended",
    }[payload.status]
    _audit(db, actor, event, target, request, {"from": previous.value, "to": payload.status.value})
    db.commit()
    return {"id": target.id, "status": target.status.value}


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> None:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.role == Role.SUPERADMIN:
        count = db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPERADMIN)) or 0
        if count <= 1:
            raise HTTPException(409, "Cannot delete the final SuperAdmin")
    _audit(db, actor, "user.deleted", target, request)
    db.delete(target)
    db.commit()
