from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Invitation, InvitationRedemption, Role, User
from app.security.security import assert_can_assign_role, random_token, token_hash


class InvitationService:
    def __init__(self, db: Session):
        self.db = db

    def create_invite(
        self,
        actor: User,
        assigned_role: Role = Role.MEMBER,
        expires_in_minutes: int | None = 24 * 60,
        max_uses: int | None = 1,
        label: str | None = None,
        note: str | None = None,
    ) -> tuple[Invitation, str]:
        assert_can_assign_role(actor, assigned_role)
        raw = random_token(32)
        invite = Invitation(
            token_hash=token_hash(raw),
            created_by_id=actor.id,
            assigned_role=assigned_role,
            expires_at=(datetime.now(UTC) + timedelta(minutes=expires_in_minutes)) if expires_in_minutes else None,
            max_uses=max_uses,
            label=label,
            note=note,
        )
        self.db.add(invite)
        self.db.commit()
        self.db.refresh(invite)
        return invite, raw

    def find_valid(self, raw_token: str, lock: bool = False) -> Invitation:
        stmt = select(Invitation).where(Invitation.token_hash == token_hash(raw_token))
        if lock:
            stmt = stmt.with_for_update()
        invite = self.db.scalar(stmt)
        now = datetime.now(UTC)
        if not invite:
            raise HTTPException(404, "Invitation not found")
        if invite.revoked_at is not None:
            raise HTTPException(410, "Invitation revoked")
        if invite.expires_at is not None and invite.expires_at <= now:
            raise HTTPException(410, "Invitation expired")
        if invite.max_uses is not None and invite.use_count >= invite.max_uses:
            raise HTTPException(410, "Invitation has no remaining uses")
        return invite

    def redeem(self, invite: Invitation, user: User, ip: str | None) -> None:
        # Call inside the same transaction used to create the user. Row locking in the callback
        # prevents concurrent final-use redemptions from exceeding max_uses.
        invite.use_count += 1
        self.db.add(InvitationRedemption(invitation_id=invite.id, user_id=user.id, ip=ip))

    def revoke(self, invite: Invitation, actor: User) -> Invitation:
        invite.revoked_at = datetime.now(UTC)
        invite.revoked_by_id = actor.id
        self.db.commit()
        self.db.refresh(invite)
        return invite
