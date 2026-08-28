import hmac
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import Movie, PlexLibrary, PlexScanJob
from app.workers.tasks import scan_library

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/plex", status_code=202)
def plex_webhook(
    payload: str = Form(...),
    secret: str = Query(default=""),
    db: Session = Depends(get_db),
) -> dict:
    if not settings.PLEX_WEBHOOK_SECRET:
        raise HTTPException(503, "Plex webhooks are not enabled")
    if not secret or not hmac.compare_digest(secret, settings.PLEX_WEBHOOK_SECRET):
        raise HTTPException(403, "Invalid webhook secret")

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid Plex webhook payload") from exc

    event_name = str(event.get("event") or "")[:120]
    metadata = event.get("Metadata") if isinstance(event.get("Metadata"), dict) else {}
    rating_key = str(metadata.get("ratingKey") or "")

    queued: list[tuple[int, int | None]] = []
    if rating_key:
        movie = db.scalar(select(Movie).where(Movie.rating_key == rating_key).limit(1))
        if movie:
            library = db.get(PlexLibrary, movie.library_id)
            if library and library.enabled:
                job = PlexScanJob(library_id=library.id, mode="webhook", status="queued")
                db.add(job)
                db.flush()
                queued.append((job.id, movie.id))

    if not queued and event_name.startswith("library."):
        libraries = db.scalars(select(PlexLibrary).where(PlexLibrary.enabled.is_(True))).all()
        for library in libraries:
            job = PlexScanJob(library_id=library.id, mode="webhook", status="queued")
            db.add(job)
            db.flush()
            queued.append((job.id, None))

    db.commit()
    for job_id, movie_id in queued:
        scan_library.delay(job_id, movie_id)

    return {"accepted": True, "event": event_name, "queued_jobs": [job_id for job_id, _ in queued]}
