import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import PlexServer


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.TOKEN_ENCRYPTION_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class PlexConfigurationService:
    def __init__(self, db: Session):
        self.db = db

    def active_server(self) -> PlexServer | None:
        return self.db.scalar(select(PlexServer).where(PlexServer.enabled.is_(True)).order_by(PlexServer.id.asc()))

    def decrypt_token(self, server: PlexServer) -> str:
        if not server.token_ciphertext:
            return ""
        try:
            return _fernet().decrypt(server.token_ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise HTTPException(500, "Stored Plex token cannot be decrypted with the configured encryption key") from exc

    def upsert(self, base_url: str, token: str) -> PlexServer:
        server = self.active_server()
        if server is None:
            server = PlexServer(base_url=base_url.rstrip("/"), token_ciphertext="", enabled=True)
            self.db.add(server)
            self.db.flush()
        server.base_url = base_url.rstrip("/")
        if token:
            server.token_ciphertext = _fernet().encrypt(token.encode("utf-8")).decode("utf-8")
        server.enabled = True
        self.db.flush()
        return server
