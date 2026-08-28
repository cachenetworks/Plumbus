from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.models import PlexLibrary, PlexScanJob
from app.services.plex.scanner import PlexScanner
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.scan_library")
def scan_library(job_id: int) -> dict:
    with SessionLocal() as db:
        job = db.get(PlexScanJob, job_id)
        if not job or job.library_id is None:
            return {"ok": False, "error": "job_not_found"}
        library = db.get(PlexLibrary, job.library_id)
        if not library:
            return {"ok": False, "error": "library_not_found"}
        result = PlexScanner(db).scan_library(library, job)
        return {
            "ok": result.status == "completed",
            "job_id": result.id,
            "status": result.status,
            "items_scanned": result.items_scanned,
        }


@celery_app.task(name="app.workers.tasks.sync_enabled_libraries")
def sync_enabled_libraries() -> dict:
    queued: list[int] = []
    with SessionLocal() as db:
        libraries = db.scalars(select(PlexLibrary).where(PlexLibrary.enabled.is_(True))).all()
        for library in libraries:
            job = PlexScanJob(library_id=library.id, mode="incremental", status="queued")
            db.add(job)
            db.flush()
            queued.append(job.id)
        db.commit()
    for job_id in queued:
        scan_library.delay(job_id)
    return {"queued": queued}
