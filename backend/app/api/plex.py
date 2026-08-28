from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import PlexLibrary, PlexScanJob, Role, User
from app.security.security import require_role
from app.services.plex.service import PlexService
from app.workers.tasks import scan_library

router = APIRouter(prefix="/api/plex", tags=["plex"])


class LibraryUpdate(BaseModel):
    enabled: bool | None = None
    visible_to_members: bool | None = None


@router.get("/connection")
def connection(actor: User = Depends(require_role(Role.ADMIN))) -> dict:
    del actor
    info = PlexService().connect()
    return {
        "connected": info.connected,
        "server_name": info.name,
        "version": info.version,
        "server_identifier": info.machine_identifier,
        "libraries": info.libraries or [],
    }


@router.post("/libraries/discover")
def discover_libraries(
    actor: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> list[dict]:
    del actor
    plex = PlexService()
    info = plex.connect()
    if not info.connected:
        raise HTTPException(503, "Unable to connect to Plex")
    rows: list[dict] = []
    for remote in info.libraries or []:
        library = db.scalar(select(PlexLibrary).where(PlexLibrary.server_id == 1, PlexLibrary.plex_key == remote["key"]))
        if library is None:
            # Environment-backed Plex is represented by server_id=1 in the first migration/bootstrap.
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
