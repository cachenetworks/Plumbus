from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import AuditLog, PlexLibrary, PlexScanJob, PlexServer, Role, User
from app.security.secrets import encrypt_secret
from app.security.security import require_role
from app.services.plex.service import PlexService
from app.workers.tasks import scan_library

router = APIRouter(prefix="/api/plex", tags=["plex"])


class LibraryUpdate(BaseModel):
    enabled: bool | None = None
    visible_to_members: bool | None = None


class PlexSettingsUpdate(BaseModel):
    base_url: str = Field(min_length=8, max_length=512)
    token: str | None = Field(default=None, min_length=1, max_length=1024)


@router.get("/connection")
def connection(
    actor: User = Depends(require_role(Role.SUPPORT)),
    db: Session = Depends(get_db),
) -> dict:
    del actor
    info = PlexService.from_db(db).connect()
    return {
        "connected": info.connected,
        "server_name": info.name,
        "version": info.version,
        "server_identifier": info.machine_identifier,
        "libraries": info.libraries or [],
    }


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

    candidate = PlexService(payload.base_url.rstrip("/"), candidate_token)
    info = candidate.connect()
    if not info.connected:
        raise HTTPException(400, "Unable to connect to Plex with the supplied settings")

    row = db.get(PlexServer, 1)
    old_identifier = row.server_identifier if row else None
    if row is None:
        row = PlexServer(id=1, base_url=payload.base_url.rstrip("/"), token_ciphertext=encrypt_secret(candidate_token))
        db.add(row)
    else:
        row.base_url = payload.base_url.rstrip("/")
        row.token_ciphertext = encrypt_secret(candidate_token)
    row.server_name = info.name
    row.server_identifier = info.machine_identifier
    row.server_version = info.version
    row.enabled = True

    # A different Plex server may reuse library numeric keys, so disable old mappings until
    # the administrator explicitly discovers and re-enables libraries from the new server.
    if old_identifier and info.machine_identifier and old_identifier != info.machine_identifier:
        for library in db.scalars(select(PlexLibrary).where(PlexLibrary.server_id == 1)).all():
            library.enabled = False

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
    plex = PlexService.from_db(db)
    info = plex.connect()
    if not info.connected:
        raise HTTPException(503, "Unable to connect to Plex")
    rows: list[dict] = []
    for remote in info.libraries or []:
        library = db.scalar(select(PlexLibrary).where(PlexLibrary.server_id == 1, PlexLibrary.plex_key == remote["key"]))
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
        rows.append({"id": library.id, "plex_key": library.plex_key, "title": library.title, "type": library.library_type, "enabled": library.enabled, "visible_to_members": library.visible_to_members})
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
        {"id": x.id, "plex_key": x.plex_key, "title": x.title, "type": x.library_type, "enabled": x.enabled, "visible_to_members": x.visible_to_members, "last_scan_at": x.last_scan_at}
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
    job = PlexScanJob(library_id=library.id, mode="single_library", status="queued", requested_by_id=actor.id)
    db.add(job)
    db.commit()
    db.refresh(job)
    scan_library.delay(job.id)
    return {"job_id": job.id, "status": job.status}


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
