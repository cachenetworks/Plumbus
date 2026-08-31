from app.models.models import ApplicationSetting
from app.security.secrets import decrypt_secret
from app.services.configuration import IntegrationConfigurationService


def test_setup_status_creates_recoverable_claim_code(client, db):
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    assert response.json()["completed"] is False
    row = db.get(ApplicationSetting, "setup_claim")
    assert row is not None
    code = decrypt_secret(row.value["code"])
    assert len(code) >= 10
    assert row.value["hash"] != code


def test_setup_requires_correct_claim_code(client, db):
    client.get("/api/setup/status")
    bad = client.post("/api/setup/claim", json={"code": "WRONGCODE00"})
    assert bad.status_code == 403

    row = db.get(ApplicationSetting, "setup_claim")
    code = decrypt_secret(row.value["code"])
    good = client.post("/api/setup/claim", json={"code": code})
    assert good.status_code == 200
    assert good.json()["claimed"] is True


def test_discord_configuration_is_encrypted_and_database_backed(db):
    service = IntegrationConfigurationService(db)
    service.set_site("https://cinema.example.test", "Private Cinema")
    service.set_discord("123456789012345678", "very-secret-discord-value", "987654321098765432")
    db.commit()

    row = db.get(ApplicationSetting, "discord_oauth")
    assert row.value["client_secret"] != "very-secret-discord-value"

    resolved = IntegrationConfigurationService(db).discord()
    assert resolved.client_id == "123456789012345678"
    assert resolved.client_secret == "very-secret-discord-value"
    assert resolved.initial_superadmin_discord_id == "987654321098765432"
    assert resolved.redirect_uri == "https://cinema.example.test/api/auth/discord/callback"
