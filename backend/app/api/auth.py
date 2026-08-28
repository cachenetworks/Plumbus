from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import AuditLog, Invitation, OAuthState, Role, Session as UserSession, User, UserStatus
from app.security.security import SESSION_COOKIE, get_current_user, random_token, token_hash
from app.services.invitations.service import InvitationService

router = APIRouter(prefix="/api/auth", tags=["auth"])
DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"
DISCORD_ME = "https://discord.com/api/users/@me"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _oauth_redirect(db: Session, flow: str, invite_id: int | None = None) -> str:
    raw_state = random_token(32)
    db.add(OAuthState(state_hash=token_hash(raw_state), flow=flow, invite_id=invite_id, expires_at=datetime.now(UTC) + timedelta(minutes=10)))
    db.commit()
    query = urlencode({"client_id":settings.DISCORD_CLIENT_ID,"response_type":"code","redirect_uri":settings.DISCORD_REDIRECT_URI,"scope":"identify","state":raw_state,"prompt":"consent"})
    return f"{DISCORD_AUTHORIZE}?{query}"


def _bootstrap_available(db: Session) -> bool:
    if not settings.INITIAL_SUPERADMIN_DISCORD_ID:
        return False
    count = db.scalar(select(func.count()).select_from(User).where(User.role == Role.SUPERADMIN)) or 0
    return count == 0


@router.get("/bootstrap/status")
def bootstrap_status(db: Session = Depends(get_db)) -> dict:
    return {"available": _bootstrap_available(db)}


@router.get("/discord/bootstrap")
def discord_bootstrap(db: Session = Depends(get_db)) -> RedirectResponse:
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise HTTPException(503, "Discord OAuth is not configured")
    if not _bootstrap_available(db):
        raise HTTPException(404, "Initial setup is not available")
    return RedirectResponse(_oauth_redirect(db, "bootstrap"), status_code=302)


@router.get("/discord/login")
def discord_login(db: Session = Depends(get_db)) -> RedirectResponse:
    if not settings.DISCORD_CLIENT_ID or not settings.DISCORD_CLIENT_SECRET:
        raise HTTPException(503, "Discord OAuth is not configured")
    return RedirectResponse(_oauth_redirect(db, "login"), status_code=302)


@router.get("/discord/register/{invite_token}")
def discord_register(invite_token: str, db: Session = Depends(get_db)) -> RedirectResponse:
    invite = InvitationService(db).find_valid(invite_token)
    return RedirectResponse(_oauth_redirect(db, "register", invite.id), status_code=302)


@router.get("/discord/callback")
def discord_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)) -> Response:
    oauth_state = db.scalar(select(OAuthState).where(OAuthState.state_hash == token_hash(state), OAuthState.consumed_at.is_(None), OAuthState.expires_at > datetime.now(UTC)).with_for_update())
    if not oauth_state:
        raise HTTPException(400, "Invalid or expired OAuth state")
    oauth_state.consumed_at = datetime.now(UTC)
    db.flush()

    token_response = httpx.post(DISCORD_TOKEN, data={"client_id":settings.DISCORD_CLIENT_ID,"client_secret":settings.DISCORD_CLIENT_SECRET,"grant_type":"authorization_code","code":code,"redirect_uri":settings.DISCORD_REDIRECT_URI}, timeout=15)
    if token_response.status_code != 200:
        db.rollback(); raise HTTPException(400, "Discord token exchange failed")
    access_token = token_response.json().get("access_token")
    identity_response = httpx.get(DISCORD_ME, headers={"Authorization":f"Bearer {access_token}"}, timeout=15)
    if identity_response.status_code != 200:
        db.rollback(); raise HTTPException(400, "Discord identity lookup failed")
    identity = identity_response.json()
    discord_id = str(identity["id"])
    user = db.scalar(select(User).where(User.discord_id == discord_id))
    now = datetime.now(UTC)

    if oauth_state.flow == "login":
        if not user:
            db.rollback(); return RedirectResponse(f"{settings.APP_URL}/login?error=invite_required", status_code=302)
    elif oauth_state.flow == "bootstrap":
        if user:
            db.rollback(); raise HTTPException(409, "Configured SuperAdmin Discord account already exists")
        if not _bootstrap_available(db) or discord_id != settings.INITIAL_SUPERADMIN_DISCORD_ID:
            db.rollback(); raise HTTPException(403, "This Discord account is not authorized for initial setup")
        user = User(discord_id=discord_id, username=identity.get("username") or discord_id, global_name=identity.get("global_name"), avatar=identity.get("avatar"), role=Role.SUPERADMIN, status=UserStatus.ACTIVE, last_login_at=now)
        db.add(user); db.flush()
        db.add(AuditLog(actor_user_id=user.id, event="user.created", target_type="user", target_id=str(user.id), ip=_client_ip(request), event_metadata={"source":"initial_superadmin_bootstrap"}))
    elif oauth_state.flow == "register":
        if user:
            db.rollback(); return RedirectResponse(f"{settings.APP_URL}/login?error=already_registered", status_code=302)
        invite = db.scalar(select(Invitation).where(Invitation.id == oauth_state.invite_id).with_for_update())
        if not invite:
            db.rollback(); raise HTTPException(410, "Invitation unavailable")
        if invite.revoked_at or (invite.expires_at and invite.expires_at <= now) or (invite.max_uses is not None and invite.use_count >= invite.max_uses):
            db.rollback(); raise HTTPException(410, "Invitation is no longer valid")
        assigned_role = Role.SUPERADMIN if settings.INITIAL_SUPERADMIN_DISCORD_ID and discord_id == settings.INITIAL_SUPERADMIN_DISCORD_ID and _bootstrap_available(db) else invite.assigned_role
        user = User(discord_id=discord_id, username=identity.get("username") or discord_id, global_name=identity.get("global_name"), avatar=identity.get("avatar"), role=assigned_role, status=UserStatus.ACTIVE, invite_id=invite.id, last_login_at=now)
        db.add(user); db.flush()
        InvitationService(db).redeem(invite, user, _client_ip(request))
        db.add(AuditLog(actor_user_id=user.id, event="user.created", target_type="user", target_id=str(user.id), ip=_client_ip(request)))
        db.add(AuditLog(actor_user_id=user.id, event="invite.redeemed", target_type="invite", target_id=str(invite.id), ip=_client_ip(request)))
    else:
        db.rollback(); raise HTTPException(400, "Unknown OAuth flow")

    if user.status == UserStatus.BANNED:
        db.rollback(); raise HTTPException(403, "Account banned")
    if user.status == UserStatus.SUSPENDED:
        db.rollback(); raise HTTPException(403, "Account suspended")

    user.username = identity.get("username") or user.username
    user.global_name = identity.get("global_name")
    user.avatar = identity.get("avatar")
    user.last_login_at = now
    raw_session = random_token(32)
    db.add(UserSession(session_hash=token_hash(raw_session), user_id=user.id, expires_at=now + timedelta(days=30), ip=_client_ip(request), user_agent=request.headers.get("user-agent")))
    db.add(AuditLog(actor_user_id=user.id, event="user.login", target_type="user", target_id=str(user.id), ip=_client_ip(request)))
    db.commit()

    response = RedirectResponse(f"{settings.APP_URL}/browse", status_code=302)
    response.set_cookie(SESSION_COOKIE, raw_session, max_age=30*24*60*60, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax", path="/")
    return response


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id":user.id,"discord_id":user.discord_id,"username":user.username,"global_name":user.global_name,"avatar":user.avatar,"role":user.role.value,"status":user.status.value}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        session = db.scalar(select(UserSession).where(UserSession.session_hash == token_hash(raw), UserSession.revoked_at.is_(None)))
        if session:
            session.revoked_at = datetime.now(UTC)
            db.add(AuditLog(actor_user_id=session.user_id, event="user.logout", target_type="user", target_id=str(session.user_id), ip=_client_ip(request)))
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response
