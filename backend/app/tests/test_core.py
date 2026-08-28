from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.models import Invitation, Movie, MovieMedia, PlexLibrary, PlexScanJob, PlexServer, Role, Session as UserSession, User, UserStatus
from app.security.security import random_token, token_hash
from app.services.invitations.service import InvitationService
from app.services.playback.service import PlaybackService
from app.services.plex.scanner import PlexScanner


def make_user(db, discord_id="100", role=Role.MEMBER, status=UserStatus.ACTIVE):
    user = User(discord_id=discord_id, username=f"user-{discord_id}", role=role, status=status)
    db.add(user); db.commit(); db.refresh(user)
    return user


def test_tokens_are_opaque_and_hashed():
    raw = random_token(32)
    digest = token_hash(raw)
    assert raw != digest
    assert len(digest) == 64
    assert token_hash(raw) == digest


def test_admin_cannot_create_admin_invite(db):
    actor = make_user(db, role=Role.ADMIN)
    with pytest.raises(HTTPException) as exc:
        InvitationService(db).create_invite(actor, assigned_role=Role.ADMIN)
    assert exc.value.status_code == 403


def test_superadmin_can_create_admin_invite_and_raw_token_is_not_stored(db):
    actor = make_user(db, role=Role.SUPERADMIN)
    invite, raw = InvitationService(db).create_invite(actor, assigned_role=Role.ADMIN)
    assert invite.assigned_role == Role.ADMIN
    assert invite.token_hash == token_hash(raw)
    assert raw not in invite.token_hash


def test_expired_and_revoked_invites_are_rejected(db):
    actor = make_user(db, role=Role.SUPERADMIN)
    invite, raw = InvitationService(db).create_invite(actor)
    invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as expired:
        InvitationService(db).find_valid(raw)
    assert expired.value.status_code == 410

    invite.expires_at = None
    invite.revoked_at = datetime.now(UTC)
    db.commit()
    with pytest.raises(HTTPException) as revoked:
        InvitationService(db).find_valid(raw)
    assert revoked.value.status_code == 410


def test_consumed_single_use_invite_cannot_be_reused(db):
    actor = make_user(db, discord_id="200", role=Role.SUPERADMIN)
    invite, raw = InvitationService(db).create_invite(actor, max_uses=1)
    member = make_user(db, discord_id="201")
    InvitationService(db).redeem(invite, member, "127.0.0.1")
    db.commit()
    with pytest.raises(HTTPException) as exc:
        InvitationService(db).find_valid(raw)
    assert exc.value.status_code == 410


def test_playback_token_expires(db):
    user = make_user(db)
    server = PlexServer(id=1, base_url="mock", token_ciphertext="mock")
    db.add(server); db.flush()
    library = PlexLibrary(server_id=1, plex_key="1", title="Movies", library_type="movie", enabled=True)
    db.add(library); db.flush()
    movie = Movie(library_id=library.id, rating_key="m1", title="Test")
    db.add(movie); db.flush()
    db.add(MovieMedia(movie_id=movie.id, part_key="/library/parts/1/file.mp4", container="mp4", video_codec="h264", height=1080))
    db.commit()
    token, raw = PlaybackService(db).create_token(user, movie)
    token.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        PlaybackService(db).validate_token(raw)
    assert exc.value.status_code == 404


def test_mock_plex_scanner_indexes_movies(db):
    db.add(PlexServer(id=1, base_url="mock", token_ciphertext="mock")); db.flush()
    library = PlexLibrary(server_id=1, plex_key="1", title="Movies", library_type="movie", enabled=True)
    db.add(library); db.flush()
    job = PlexScanJob(library_id=library.id, mode="full", status="queued")
    db.add(job); db.commit(); db.refresh(job)
    result = PlexScanner(db).scan_library(library, job)
    assert result.status == "completed"
    assert result.items_scanned == 5
    assert db.query(Movie).count() == 5


def test_suspended_user_session_is_rejected(client, db):
    user = make_user(db, status=UserStatus.SUSPENDED)
    raw = random_token(32)
    db.add(UserSession(session_hash=token_hash(raw), user_id=user.id, expires_at=datetime.now(UTC)+timedelta(hours=1)))
    db.commit()
    response = client.get("/api/auth/me", cookies={"plumbus_session": raw})
    assert response.status_code == 403


def test_normal_login_never_creates_unknown_user(client, db):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert db.query(User).count() == 0


def test_invalid_oauth_state_is_rejected_before_discord_exchange(client, db):
    response = client.get("/api/auth/discord/callback?code=not-used&state=invalid-state")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired OAuth state"
    assert db.query(User).count() == 0


def test_admin_api_rejects_anonymous_requests(client):
    response = client.get("/api/admin/users")
    assert response.status_code == 401
