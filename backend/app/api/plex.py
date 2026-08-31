from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import ApplicationSetting, AuditLog, Movie, PlexLibrary, PlexScanJob, PlexServer, Role, User
from app.security.secrets import encrypt_secret
from app.security.security import require_role
from app.services.configuration import IntegrationConfigurationService
from app.services.plex.account import PlexAccountService
from app.services.plex.service import PlexConnectionInfo, PlexService
from app.workers.tasks import scan_library

router = APIRouter(prefix="/api/plex", tags=["plex"])


class LibraryUpdate(BaseModel):
    enabled: bool | None = None
    visible_to_members: bool | None = None


class PlexSettingsUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=512)
    token: str | None = Field(default=None, min_length=1, max_length=1024)
    server_id: int | None = Field(default=None, ge=1)
    set_active: bool = True


class PlexAutoConnectRequest(BaseModel):
    client_identifier: str = Field(min_length=1, max_length=200)
    preferred_uri: str | None = Field(default=None, max_length=1024)
    set_active: bool = True


class ServerUpdate(BaseModel):
    enabled: bool | None = None


def _new_scan_job(db: Session, library: PlexLibrary, actor: User | None, mode: str) -> PlexScanJob:
    job = PlexScanJob(
        library_id=library.id,
        mode=mode,
        status="queued",
        requested_by_id=actor.id if actor else None,
    )
    db.add(job)
    db.flush()
    return job


def _set_active_server(db: Session, server_id: int) -> None:
    server = db.get(PlexServer, server_id)
    if not server or not server.enabled or server.base_url == "environment":
        raise HTTPException(404, "Plex server is not available")
    row = db.get(ApplicationSetting, "plex_active_server")
    if row is None:
        row = ApplicationSetting(key="plex_active_server", value={})
        db.add(row)
    row.value = {"server_id": server_id}


def _active_server_id(db: Session) -> int | None:
    return PlexService.active_server_id(db)


def _server_by_identifier(db: Session, identifier: str | None) -> PlexServer | None:
    if not identifier:
        return None
    return db.scalar(select(PlexServer).where(PlexServer.server_identifier == identifier).order_by(PlexServer.id).limit(1))


def _store_server(
    db: Session,
    base_url: str,
    token: str,
    info: PlexConnectionInfo,
    fallback_name: str | None = None,
    fallback_identifier: str | None = None,
    server_id: int | None = None,
) -> PlexServer:
    identifier = info.machine_identifier or fallback_identifier
    row = db.get(PlexServer, server_id) if server_id else _server_by_identifier(db, identifier)
    old_identifier = row.server_identifier if row else None
    if row is None:
        row = PlexServer(base_url=base_url.rstrip("/"), token_ciphertext=encrypt_secret(token))
        db.add(row)
        db.flush()
    row.base_url = base_url.rstrip("/")
    row.token_ciphertext = encrypt_secret(token)
    row.server_name = info.name or fallback_name or row.server_name
    row.server_identifier = identifier or row.server_identifier
    row.server_version = info.version
    row.enabled = True
    if old_identifier and row.server_identifier and old_identifier != row.server_identifier:
        for library in db.scalars(select(PlexLibrary).where(PlexLibrary.server_id == row.id)).all():
            library.enabled = False
    return row


def _server_payload(db: Session, row: PlexServer) -> dict:
    library_count = db.scalar(select(func.count(PlexLibrary.id)).where(PlexLibrary.server_id == row.id)) or 0
    enabled_count = db.scalar(
        select(func.count(PlexLibrary.id)).where(
            PlexLibrary.server_id == row.id,
            PlexLibrary.enabled.is_(True),
        )
    ) or 0
    return {
        "id": row.id,
        "name": row.server_name or f"Plex Server #{row.id}",
        "identifier": row.server_identifier,
        "version": row.server_version,
        "base_url": row.base_url,
        "enabled": row.enabled,
        "active": row.id == _active_server_id(db),
        "library_count": library_count,
        "enabled_library_count": enabled_count,
        "token_configured": row.token_ciphertext not in {"", "environment"},
    }


def _require_server_connection(db: Session, server_id: int) -> tuple[PlexService, PlexConnectionInfo]:
    server = db.get(PlexServer, server_id)
    if not server or not server.enabled:
        raise HTTPException(404, "Plex server is disabled or unavailable")
    service = PlexService.from_db(db, server_id=server_id)
    info = service.connect()
    if not info.connected:
        raise HTTPException(
            503,
            {
                "message": f"Plumbus cannot reach {server.server_name or 'this Plex server'}",
                "server_id": server_id,
                "base_url": service.base_url,
                "error": info.error or "Unknown Plex connection error",
            },
        )
    return service, info


def _sync_libraries(db: Session, server: PlexServer, info: PlexConnectionInfo) -> list[dict]:
    rows: list[dict] = []
    remote_keys: set[str] = set()
    for remote in info.libraries or []:
        key = str(remote["key"])
        remote_keys.add(key)
        library = db.scalar(
            select(PlexLibrary).where(
                PlexLibrary.server_id == server.id,
                PlexLibrary.plex_key == key,
            )
        )
        if library is None:
            library = PlexLibrary(
                server_id=server.id,
                plex_key=key,
                title=remote["title"],
                library_type=remote["type"],
                enabled=False,
                visible_to_members=True,
            )
            db.add(library)
        else:
            library.title = remote["title"]
            library.library_type = remote["type"]
        db.flush()
        rows.append(
            {
                "id": library.id,
                "server_id": server.id,
                "server_name": server.server_name,
                "plex_key": library.plex_key,
                "title": library.title,
                "type": library.library_type,
                "enabled": library.enabled,
                "visible_to_members": library.visible_to_members,
                "last_scan_at": library.last_scan_at,
            }
        )
    return rows


@router.get("/connection")
def connection(
    server_id: int | None = Query(default=None, ge=1),
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    service = PlexService.from_db(db, server_id=server_id)
    info = service.connect()
    return {
        "connected": info.connected,
        "server_id": service.server_id,
        "server_name": info.name,
        "version": info.version,
        "server_identifier": info.machine_identifier,
        "libraries": info.libraries or [],
        "base_url": service.base_url,
        "error": info.error,
    }


@router.get("/servers")
def saved_servers(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    rows = db.scalars(
        select(PlexServer)
        .where(PlexServer.base_url != "environment")
        .order_by(PlexServer.server_name.asc().nullslast(), PlexServer.id)
    ).all()
    return [_server_payload(db, row) for row in rows]


@router.post("/servers/{server_id}/test")
def test_server(
    server_id: int,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    server = db.get(PlexServer, server_id)
    if not server:
        raise HTTPException(404, "Plex server not found")
    service = PlexService.from_db(db, server_id=server_id)
    info = service.connect()
    return {
        "server_id": server.id,
        "connected": info.connected,
        "name": info.name or server.server_name,
        "version": info.version or server.server_version,
        "identifier": info.machine_identifier or server.server_identifier,
        "base_url": service.base_url,
        "library_count": len(info.libraries or []),
        "error": info.error,
    }


@router.post("/servers/{server_id}/activate")
def activate_server(
    server_id: int,
    request: Request,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    _set_active_server(db, server_id)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="plex.server_activated",
            target_type="plex_server",
            target_id=str(server_id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={},
        )
    )
    db.commit()
    return {"active_server_id": server_id}


@router.patch("/servers/{server_id}")
def update_server(
    server_id: int,
    payload: ServerUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    server = db.get(PlexServer, server_id)
    if not server:
        raise HTTPException(404, "Plex server not found")
    if payload.enabled is not None:
        server.enabled = payload.enabled
        if not server.enabled and _active_server_id(db) == server.id:
            replacement = db.scalar(
                select(PlexServer).where(
                    PlexServer.id != server.id,
                    PlexServer.enabled.is_(True),
                    PlexServer.base_url != "environment",
                ).order_by(PlexServer.id).limit(1)
            )
            if replacement:
                _set_active_server(db, replacement.id)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="plex.server_updated",
            target_type="plex_server",
            target_id=str(server.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={"enabled": server.enabled},
        )
    )
    db.commit()
    return _server_payload(db, server)


@router.get("/account/status")
def account_status(
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    return {"linked": bool(PlexAccountService(db).account_token(refresh=False))}


@router.post("/account/sign-in")
def account_sign_in(
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    site = IntegrationConfigurationService(db).site()
    result = PlexAccountService(db).start_sign_in(f"{site.app_url}/admin/plex")
    db.commit()
    return result


@router.get("/account/sign-in/{pin_id}")
def account_sign_in_poll(
    pin_id: int,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    try:
        result = PlexAccountService(db).poll_sign_in(pin_id)
    except Exception as exc:
        raise HTTPException(502, f"Plex sign-in check failed: {exc}") from exc
    db.commit()
    return result


@router.get("/account/servers")
def account_servers(
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    try:
        resources = PlexAccountService(db).resources()
    except Exception as exc:
        raise HTTPException(502, f"Unable to load Plex account servers: {exc}") from exc
    return [
        {
            "name": resource.get("name"),
            "client_identifier": resource.get("client_identifier"),
            "owned": resource.get("owned", False),
            "saved_server_id": (_server_by_identifier(db, resource.get("client_identifier")) or PlexServer()).id,
            "connections": resource.get("connections", []),
        }
        for resource in resources
    ]


@router.post("/account/auto-connect")
def auto_connect_account_server(
    payload: PlexAutoConnectRequest,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    try:
        resources = PlexAccountService(db).resources()
    except Exception as exc:
        raise HTTPException(502, f"Unable to load Plex account servers: {exc}") from exc
    resource = next(
        (item for item in resources if item.get("client_identifier") == payload.client_identifier),
        None,
    )
    if not resource:
        raise HTTPException(404, "That Plex server is not available on the linked Plex account")
    token = str(resource.get("access_token") or "")
    if not token:
        raise HTTPException(409, "Plex did not provide an access token for this server")

    candidate_urls: list[str] = []
    if payload.preferred_uri:
        candidate_urls.append(payload.preferred_uri.rstrip("/"))
    for connection_item in resource.get("connections", []):
        uri = str(connection_item.get("uri") or "").rstrip("/")
        if uri and uri not in candidate_urls:
            candidate_urls.append(uri)

    attempts: list[dict] = []
    for url in candidate_urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            attempts.append({"url": url, "error": "Invalid HTTP(S) Plex URL"})
            continue
        candidate = PlexService(url, token)
        info = candidate.connect()
        if info.connected:
            row = _store_server(
                db,
                url,
                token,
                info,
                fallback_name=str(resource.get("name") or "Plex"),
                fallback_identifier=payload.client_identifier,
            )
            if payload.set_active or _active_server_id(db) is None:
                _set_active_server(db, row.id)
            _sync_libraries(db, row, info)
            db.add(
                AuditLog(
                    actor_user_id=actor.id,
                    event="plex.account_auto_connected",
                    target_type="plex_server",
                    target_id=str(row.id),
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    event_metadata={"base_url": row.base_url, "server_identifier": row.server_identifier},
                )
            )
            db.commit()
            return {
                "connected": True,
                "server": _server_payload(db, row),
                "libraries": _sync_libraries(db, row, info),
                "attempts": attempts,
            }
        attempts.append({"url": url, "error": info.error or "Connection failed"})

    raise HTTPException(
        502,
        {
            "message": "None of the Plex server connections reported by Plex were reachable from this container",
            "attempts": attempts,
        },
    )


@router.get("/settings")
def plex_settings(
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    service = PlexService.from_db(db)
    row = db.get(PlexServer, service.server_id) if service.server_id else None
    return {
        "server_id": service.server_id,
        "base_url": service.base_url,
        "token_configured": bool(service.token),
        "source": "environment" if not row else "database",
        "server_name": None if not row else row.server_name,
        "server_identifier": None if not row else row.server_identifier,
        "server_version": None if not row else row.server_version,
    }


@router.put("/settings")
def update_plex_settings(
    payload: PlexSettingsUpdate,
    request: Request,
    actor: User = Depends(require_role(Role.SUPERADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    current = PlexService.from_db(db, server_id=payload.server_id)
    candidate_token = payload.token or current.token
    if not candidate_token:
        raise HTTPException(400, "A Plex token is required")
    parsed = urlparse(payload.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "Plex URL must be a complete http:// or https:// address")

    candidate = PlexService(payload.base_url.rstrip("/"), candidate_token)
    info = candidate.connect()
    if not info.connected:
        raise HTTPException(
            400,
            {
                "message": "Unable to connect to Plex with the supplied settings",
                "error": info.error or "Unknown Plex connection error",
            },
        )

    row = _store_server(
        db,
        payload.base_url.rstrip("/"),
        candidate_token,
        info,
        server_id=payload.server_id,
    )
    if payload.set_active or _active_server_id(db) is None:
        _set_active_server(db, row.id)
    _sync_libraries(db, row, info)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="plex.settings_changed",
            target_type="plex_server",
            target_id=str(row.id),
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            event_metadata={
                "base_url": row.base_url,
                "server_identifier": info.machine_identifier,
                "token_rotated": payload.token is not None,
            },
        )
    )
    db.commit()
    return _server_payload(db, row)


@router.post("/servers/{server_id}/libraries/discover")
def discover_server_libraries(
    server_id: int,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    _service, info = _require_server_connection(db, server_id)
    server = db.get(PlexServer, server_id)
    rows = _sync_libraries(db, server, info)
    db.commit()
    return rows


@router.post("/libraries/discover")
def discover_libraries(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    active = _active_server_id(db)
    if active is None:
        raise HTTPException(409, "Choose an active Plex server first")
    return discover_server_libraries(active, actor, db)


@router.get("/servers/{server_id}/libraries")
def server_libraries(
    server_id: int,
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    server = db.get(PlexServer, server_id)
    if not server:
        raise HTTPException(404, "Plex server not found")
    rows = db.scalars(
        select(PlexLibrary).where(PlexLibrary.server_id == server_id).order_by(PlexLibrary.title)
    ).all()
    return [
        {
            "id": x.id,
            "server_id": x.server_id,
            "server_name": server.server_name,
            "plex_key": x.plex_key,
            "title": x.title,
            "type": x.library_type,
            "enabled": x.enabled,
            "visible_to_members": x.visible_to_members,
            "last_scan_at": x.last_scan_at,
        }
        for x in rows
    ]


@router.get("/libraries")
def list_libraries(
    server_id: int | None = Query(default=None, ge=1),
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    stmt = select(PlexLibrary).order_by(PlexLibrary.title)
    if server_id:
        stmt = stmt.where(PlexLibrary.server_id == server_id)
    rows = db.scalars(stmt).all()
    servers = {x.id: x for x in db.scalars(select(PlexServer)).all()}
    return [
        {
            "id": x.id,
            "server_id": x.server_id,
            "server_name": servers.get(x.server_id).server_name if servers.get(x.server_id) else None,
            "plex_key": x.plex_key,
            "title": x.title,
            "type": x.library_type,
            "enabled": x.enabled,
            "visible_to_members": x.visible_to_members,
            "last_scan_at": x.last_scan_at,
        }
        for x in rows
    ]


@router.patch("/libraries/{library_id}")
def update_library(
    library_id: int,
    payload: LibraryUpdate,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    library = db.get(PlexLibrary, library_id)
    if not library:
        raise HTTPException(404, "Library not found")
    if payload.enabled is not None:
        library.enabled = payload.enabled
    if payload.visible_to_members is not None:
        library.visible_to_members = payload.visible_to_members
    db.commit()
    return {
        "id": library.id,
        "server_id": library.server_id,
        "enabled": library.enabled,
        "visible_to_members": library.visible_to_members,
    }


@router.post("/libraries/{library_id}/scan")
def queue_scan(
    library_id: int,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    library = db.get(PlexLibrary, library_id)
    if not library:
        raise HTTPException(404, "Library not found")
    if not library.enabled:
        raise HTTPException(409, "Enable the library before scanning it")
    _require_server_connection(db, library.server_id)
    job = _new_scan_job(db, library, actor, "single_library")
    db.commit()
    scan_library.delay(job.id)
    return {"job_id": job.id, "status": job.status, "server_id": library.server_id}


@router.post("/servers/{server_id}/scan")
def queue_server_scan(
    server_id: int,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    _require_server_connection(db, server_id)
    libraries = db.scalars(
        select(PlexLibrary).where(
            PlexLibrary.server_id == server_id,
            PlexLibrary.enabled.is_(True),
        )
    ).all()
    if not libraries:
        raise HTTPException(409, "No enabled libraries exist on this Plex server")
    jobs = [_new_scan_job(db, library, actor, "server_full") for library in libraries]
    db.commit()
    for job in jobs:
        scan_library.delay(job.id)
    return {"server_id": server_id, "job_ids": [job.id for job in jobs], "queued": len(jobs)}


@router.post("/scans/full")
def queue_full_scan(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    libraries = db.scalars(select(PlexLibrary).where(PlexLibrary.enabled.is_(True))).all()
    if not libraries:
        raise HTTPException(409, "No Plex libraries are enabled")
    jobs = [_new_scan_job(db, library, actor, "full") for library in libraries]
    db.commit()
    for job in jobs:
        scan_library.delay(job.id)
    return {"job_ids": [job.id for job in jobs], "queued": len(jobs)}


@router.post("/scans/incremental")
def queue_incremental_scan(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    libraries = db.scalars(select(PlexLibrary).where(PlexLibrary.enabled.is_(True))).all()
    if not libraries:
        raise HTTPException(409, "No Plex libraries are enabled")
    jobs = [_new_scan_job(db, library, actor, "incremental") for library in libraries]
    db.commit()
    for job in jobs:
        scan_library.delay(job.id)
    return {"job_ids": [job.id for job in jobs], "queued": len(jobs)}


@router.post("/movies/{movie_id}/refresh")
def queue_movie_refresh(
    movie_id: int,
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    library = db.get(PlexLibrary, movie.library_id)
    if not library or not library.enabled:
        raise HTTPException(409, "The movie's Plex library is not enabled")
    _require_server_connection(db, library.server_id)
    job = _new_scan_job(db, library, actor, "single_movie")
    db.commit()
    scan_library.delay(job.id, movie.id)
    return {"job_id": job.id, "status": job.status, "movie_id": movie.id, "server_id": library.server_id}


@router.get("/scans")
def scan_status(
    server_id: int | None = Query(default=None, ge=1),
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    stmt = select(PlexScanJob).order_by(PlexScanJob.id.desc()).limit(100)
    if server_id:
        stmt = stmt.join(PlexLibrary, PlexScanJob.library_id == PlexLibrary.id).where(PlexLibrary.server_id == server_id)
    jobs = db.scalars(stmt).all()
    libraries = {x.id: x for x in db.scalars(select(PlexLibrary)).all()}
    servers = {x.id: x for x in db.scalars(select(PlexServer)).all()}
    output = []
    for job in jobs:
        library = libraries.get(job.library_id) if job.library_id else None
        server = servers.get(library.server_id) if library else None
        output.append(
            {
                "id": job.id,
                "library_id": job.library_id,
                "library_name": library.title if library else None,
                "server_id": library.server_id if library else None,
                "server_name": server.server_name if server else None,
                "mode": job.mode,
                "status": job.status,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "last_error": job.last_error,
                "items_scanned": job.items_scanned,
                "items_added": job.items_added,
                "items_updated": job.items_updated,
                "items_removed": job.items_removed,
            }
        )
    return output
