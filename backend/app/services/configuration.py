from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import ApplicationSetting
from app.security.secrets import decrypt_secret, encrypt_secret


@dataclass(slots=True)
class SiteConfiguration:
    app_url: str
    site_name: str

    @property
    def secure_cookies(self) -> bool:
        return urlparse(self.app_url).scheme == "https"


@dataclass(slots=True)
class DiscordConfiguration:
    client_id: str
    client_secret: str
    redirect_uri: str
    initial_superadmin_discord_id: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.initial_superadmin_discord_id)


class IntegrationConfigurationService:
    def __init__(self, db: Session):
        self.db = db

    def site(self) -> SiteConfiguration:
        row = self.db.get(ApplicationSetting, "site")
        value = row.value if row and isinstance(row.value, dict) else {}
        return SiteConfiguration(
            app_url=str(value.get("app_url") or settings.APP_URL).rstrip("/"),
            site_name=str(value.get("site_name") or "Plumbus Cinema"),
        )

    def set_site(self, app_url: str, site_name: str, updated_by_id: int | None = None) -> SiteConfiguration:
        row = self.db.get(ApplicationSetting, "site")
        if row is None:
            row = ApplicationSetting(key="site", value={})
            self.db.add(row)
        row.value = {"app_url": app_url.rstrip("/"), "site_name": site_name.strip() or "Plumbus Cinema"}
        row.updated_by_id = updated_by_id
        self.db.flush()
        return self.site()

    def discord(self) -> DiscordConfiguration:
        row = self.db.get(ApplicationSetting, "discord_oauth")
        value = row.value if row and isinstance(row.value, dict) else {}
        encrypted = str(value.get("client_secret") or "")
        secret = ""
        if encrypted:
            secret = decrypt_secret(encrypted)
        elif settings.DISCORD_CLIENT_SECRET:
            secret = settings.DISCORD_CLIENT_SECRET
        site = self.site()
        return DiscordConfiguration(
            client_id=str(value.get("client_id") or settings.DISCORD_CLIENT_ID),
            client_secret=secret,
            redirect_uri=f"{site.app_url}/api/auth/discord/callback",
            initial_superadmin_discord_id=str(
                value.get("initial_superadmin_discord_id") or settings.INITIAL_SUPERADMIN_DISCORD_ID
            ),
        )

    def set_discord(
        self,
        client_id: str,
        client_secret: str,
        initial_superadmin_discord_id: str,
        updated_by_id: int | None = None,
    ) -> DiscordConfiguration:
        current = self.db.get(ApplicationSetting, "discord_oauth")
        old_value = current.value if current and isinstance(current.value, dict) else {}
        if current is None:
            current = ApplicationSetting(key="discord_oauth", value={})
            self.db.add(current)
        encrypted = old_value.get("client_secret")
        if client_secret:
            encrypted = encrypt_secret(client_secret)
        if not encrypted:
            raise ValueError("Discord client secret is required")
        current.value = {
            "client_id": client_id.strip(),
            "client_secret": encrypted,
            "initial_superadmin_discord_id": initial_superadmin_discord_id.strip(),
        }
        current.updated_by_id = updated_by_id
        self.db.flush()
        return self.discord()
