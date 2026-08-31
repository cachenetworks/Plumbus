from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, Movie, PlexLibrary, PlexScanJob, PlexServer, Role, User
from app.security.secrets import encrypt_secret
from app.security.security import require_role
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


class PlexAutoConnectRequest(BaseModel):
    client_identifier: str = Field(min_length=1, max_length=200)
    preferred_uri: str | None = Field(default=None, max_length=1024)


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


def _require_plex_connection(db: Session) -> PlexConnectionInfo:
    info = PlexService.from_db(db).connect()
    if not info.connected:
        raise HTTPException(
            503,
            {
                "message": "Plumbus cannot reach the configured Plex server",
                "error": info.error or "Unknown Plex connection error",
            },
        )
    return info


def _store_server(
    db: Session,
    base_url: str,
    token: str,
    info: PlexConnectionInfo,
    fallback_name: str | None = None,
    fallback_identifier: str | None = None,
) -> PlexServer:
    row = db.get(PlexServer, 1)
    old_identifier = row.server_identifier if row else None
    if row is None:
        row = PlexServer(id=1, base_url=base_url, token_ciphertext=encrypt_secret(token))
        db.add(row)
    row.base_url = base_url.rstrip("/")
    row.token_ciphertext = encrypt_secret(token)
    row.server_name = info.name or fallback_name
    row.server_identifier = info.machine_identifier or fallback_identifier
    row.server_version = info.version
    row.enabled = True
    if old_identifier and row.server_identifier and old_identifier != row.server_identifier:
        for library in db.scalars(select(PlexLibrary).where(PlexLibrary.server_id == 1)).all():
            library.enabled = False
    return row


@router.get("/connection")
def connection(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    service = PlexService.from_db(db)
    info = service.connect()
    return {
        "connected": info.connected,
        "server_name": info.name,
        "version": info.version,
        "server_identifier": info.machine_identifier,
        "libraries": info.libraries or [],
        "base_url": service.base_url,
        "error": info.error,
    }


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
            db.add(
                AuditLog(
                    actor_user_id=actor.id,
                    event="plex.account_auto_connected",
                    target_type="plex_server",
                    target_id="1",
                    ip=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    event_metadata={"base_url": row.base_url, "server_identifier": row.server_identifier},
                )
            )
            db.commit()
            return {
                "connected": True,
                "server_name": row.server_name,
                "version": row.server_version,
                "server_identifier": row.server_identifier,
                "base_url": row.base_url,
                "libraries": info.libraries or [],
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
    row = db.get(PlexServer, 1)
    service = PlexService.from_db(db)
    return {
        "base_url": service.base_url,
        "token_configured": bool(service.token),
        "source": "environment" if not row or row.base_url == "environment" else "database",
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
    current = PlexService.from_db(db)
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

    row = _store_server(db, payload.base_url.rstrip("/"), candidate_token, info)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            event="plex.settings_changed",
            target_type="plex_server",
            target_id="1",
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
    return {
        "connected": True,
        "server_name": info.name,
        "version": info.version,
        "server_identifier": info.machine_identifier,
        "token_configured": True,
    }


@router.post("/libraries/discover")
def discover_libraries(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    info = _require_plex_connection(db)
    rows: list[dict] = []
    for remote in info.libraries or []:
        library = db.scalar(
            select(PlexLibrary).where(
                PlexLibrary.server_id == 1,
                PlexLibrary.plex_key == remote["key"],
            )
        )
        if library is None:
            library = PlexLibrary(
                server_id=1,
                plex_key=remote["key"],
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
                "plex_key": library.plex_key,
                "title": library.title,
                "type": library.library_type,
                "enabled": library.enabled,
                "visible_to_members": library.visible_to_members,
            }
        )
    db.commit()
    return rows


@router.get("/libraries")
def list_libraries(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    rows = db.scalars(select(PlexLibrary).order_by(PlexLibrary.title)).all()
    return [
        {
            "id": x.id,
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
    return {"id": library.id, "enabled": library.enabled, "visible_to_members": library.visible_to_members}


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
    _require_plex_connection(db)
    job = _new_scan_job(db, library, actor, "single_library")
    db.commit()
    scan_library.delay(job.id)
    return {"job_id": job.id, "status": job.status}


@router.post("/scans/full")
def queue_full_scan(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> dict:
    libraries = db.scalars(select(PlexLibrary).where(PlexLibrary.enabled.is_(True))).all()
    if not libraries:
        raise HTTPException(409, "No Plex libraries are enabled")
    _require_plex_connection(db)
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
    _require_plex_connection(db)
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
    _require_plex_connection(db)
    job = _new_scan_job(db, library, actor, "single_movie")
    db.commit()
    scan_library.delay(job.id, movie.id)
    return {"job_id": job.id, "status": job.status, "movie_id": movie.id}


@router.get("/scans")
def scan_status(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    jobs = db.scalars(select(PlexScanJob).order_by(PlexScanJob.id.desc()).limit(100)).all()
    return [
        {
            "id": job.id,
            "library_id": job.library_id,
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
        for job in jobs
    ]
