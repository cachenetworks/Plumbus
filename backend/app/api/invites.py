from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import AuditLog, Invitation, InvitationRedemption, Role, User
from app.security.security import require_role
from app.services.invitations.service import InvitationService

router = APIRouter(tags=["invites"])


class InviteCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    expires_in_minutes: int | None = Field(default=1440, ge=1, le=60 * 24 * 365)
    max_uses: int | None = Field(default=1, ge=1, le=10000)
    assigned_role: Role = Role.MEMBER


def _status(invite: Invitation) -> str:
    now = datetime.now(UTC)
    if invite.revoked_at:
        return "Revoked"
    if invite.expires_at and invite.expires_at <= now:
        return "Expired"
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        return "Used"
    return "Active"


@router.get("/api/invites/{token}/status")
def public_invite_status(token: str, db: Session = Depends(get_db)) -> dict:
    invite = InvitationService(db).find_valid(token)
    return {
        "valid": True,
        "label": invite.label,
        "assigned_role": invite.assigned_role.value,
        "expires_at": invite.expires_at,
        "uses_remaining": None if invite.max_uses is None else max(invite.max_uses - invite.use_count, 0),
        "continue_url": f"/api/auth/discord/register/{token}",
    }


@router.post("/api/admin/invites")
def create_invite(
    payload: InviteCreate,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    invite, raw = InvitationService(db).create_invite(
        actor=actor,
        assigned_role=payload.assigned_role,
        expires_in_minutes=payload.expires_in_minutes,
        max_uses=payload.max_uses,
        label=payload.label,
        note=payload.note,
    )
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="invite.created",
            target_type="invite",
            target_id=str(invite.id),
            ip=request.client.host if request.client else None,
            metadata={"assigned_role": invite.assigned_role.value, "max_uses": invite.max_uses},
        )
    )
    db.commit()
    return {
        "id": invite.id,
        "token": raw,
        "invite_url": f"{settings.APP_URL}/invite/{raw}",
        "expires_at": invite.expires_at,
        "max_uses": invite.max_uses,
        "assigned_role": invite.assigned_role.value,
    }


@router.get("/api/admin/invites")
def list_invites(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    invites = db.scalars(select(Invitation).order_by(Invitation.created_at.desc()).limit(250)).all()
    result: list[dict] = []
    for invite in invites:
        redemption_rows = db.scalars(
            select(InvitationRedemption).where(InvitationRedemption.invitation_id == invite.id)
        ).all()
        result.append(
            {
                "id": invite.id,
                "label": invite.label,
                "created_by_id": invite.created_by_id,
                "created_at": invite.created_at,
                "expires_at": invite.expires_at,
                "assigned_role": invite.assigned_role.value,
                "max_uses": invite.max_uses,
                "use_count": invite.use_count,
                "uses_remaining": None if invite.max_uses is None else max(invite.max_uses - invite.use_count, 0),
                "redeemed_by_user_ids": [r.user_id for r in redemption_rows],
                "revoked": invite.revoked_at is not None,
                "status": _status(invite),
            }
        )
    return result


@router.post("/api/admin/invites/{invite_id}/revoke")
def revoke_invite(
    invite_id: int,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    invite = db.get(Invitation, invite_id)
    if not invite:
        raise HTTPException(404, "Invitation not found")
    InvitationService(db).revoke(invite, actor)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="invite.revoked",
            target_type="invite",
            target_id=str(invite.id),
            ip=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {"id": invite.id, "status": "Revoked"}
