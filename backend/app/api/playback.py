from collections.abc import Iterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.models.models import AuditLog, Movie, MovieMedia, User, UserStatus
from app.security.security import get_current_user
from app.services.playback.service import PlaybackService
from app.services.plex.service import PlexService

router = APIRouter(tags=["playback"])


@router.post("/api/playback/movies/{movie_id}")
def create_playback(
    movie_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    movie = db.get(Movie, movie_id)
    if not movie:
        raise HTTPException(404, "Movie not found")
    token, raw = PlaybackService(db).create_token(
        user,
        movie,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(
        AuditLog(
            actor_user_id=user.id,
            event="playback.created",
            target_type="movie",
            target_id=str(movie.id),
            ip=request.client.host if request.client else None,
        )
    )
    db.commit()
    return {
        "playback_url": f"{settings.APP_URL}/stream/{raw}",
        "expires_at": token.expires_at,
        "media": PlaybackService(db).get_media_info(db.get(MovieMedia, token.movie_media_id)),
    }


@router.get("/stream/{raw_token}")
def stream(raw_token: str, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    token = PlaybackService(db).validate_token(raw_token)
    user = db.get(User, token.user_id)
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(403, "Playback owner is not active")
    media = db.get(MovieMedia, token.movie_media_id) if token.movie_media_id else None
    if not media or not media.part_key:
        raise HTTPException(410, "Indexed media is no longer available")

    plex = PlexService.from_db(db)
    if not plex.base_url or not plex.token:
        raise HTTPException(503, "Plex is not configured")
    upstream_url = plex.stream_url(media.part_key)
    headers = plex._headers()
    headers.pop("Accept", None)
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.Client(timeout=None, follow_redirects=True)
    upstream_request = client.build_request("GET", upstream_url, headers=headers)
    upstream = client.send(upstream_request, stream=True)
    if upstream.status_code >= 400:
        upstream.close()
        client.close()
        raise HTTPException(502, f"Plex stream returned HTTP {upstream.status_code}")

    allowed_headers = {
        "content-type": "Content-Type",
        "content-length": "Content-Length",
        "content-range": "Content-Range",
        "accept-ranges": "Accept-Ranges",
        "etag": "ETag",
        "last-modified": "Last-Modified",
    }
    response_headers = {
        out_name: upstream.headers[in_name]
        for in_name, out_name in allowed_headers.items()
        if in_name in upstream.headers
    }
    response_headers.setdefault("Accept-Ranges", "bytes")
    response_headers["Cache-Control"] = "private, no-store"

    def body() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_bytes(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
            client.close()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )
