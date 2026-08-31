from app.core.config import settings
from app.models.models import ApplicationSetting, PlexLibrary, PlexServer
from app.security.secrets import encrypt_secret
from app.services.plex.service import PlexService


def test_library_uses_its_own_plex_server_even_when_another_is_default(db, monkeypatch):
    monkeypatch.setattr(settings, "MOCK_PLEX", False)

    server_a = PlexServer(
        id=11,
        base_url="http://plex-a.test:32400",
        token_ciphertext=encrypt_secret("token-a"),
        server_name="Plex A",
        server_identifier="machine-a",
        enabled=True,
    )
    server_b = PlexServer(
        id=12,
        base_url="http://plex-b.test:32400",
        token_ciphertext=encrypt_secret("token-b"),
        server_name="Plex B",
        server_identifier="machine-b",
        enabled=True,
    )
    db.add_all([server_a, server_b])
    db.flush()

    library_b = PlexLibrary(
        server_id=server_b.id,
        plex_key="7",
        title="Movies B",
        library_type="movie",
        enabled=True,
        visible_to_members=True,
    )
    db.add(library_b)
    db.add(ApplicationSetting(key="plex_active_server", value={"server_id": server_a.id}))
    db.commit()

    service = PlexService.for_library(db, library_b)

    assert service.server_id == server_b.id
    assert service.base_url == "http://plex-b.test:32400"
    assert service.token == "token-b"
