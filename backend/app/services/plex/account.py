from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session

from app.models.models import ApplicationSetting
from app.security.secrets import decrypt_secret, encrypt_secret

PLEX_PRODUCT = "Plumbus Cinema"
PLEX_PINS = "https://clients.plex.tv/api/v2/pins"
PLEX_NONCE = "https://clients.plex.tv/api/v2/auth/nonce"
PLEX_TOKEN = "https://clients.plex.tv/api/v2/auth/token"
PLEX_RESOURCES = "https://clients.plex.tv/api/v2/resources"
PLEX_DEVICES = (
    "https://clients.plex.tv/api/v2/devices",
    "https://plex.tv/api/v2/devices",
)
PLEX_AUTH_APP = "https://app.plex.tv/auth#?"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _is_legacy_pms_token(token: str) -> bool:
    # Plex's classic PMS tokens are 20-character opaque strings. JWT account
    # tokens currently returned by /resources can be rejected by PMS with 401.
    return len(token) == 20 and "." not in token


@dataclass(slots=True)
class PlexDeviceIdentity:
    client_id: str
    kid: str
    private_key: Ed25519PrivateKey


class PlexAccountService:
    def __init__(self, db: Session):
        self.db = db

    def _row(self) -> ApplicationSetting:
        row = self.db.get(ApplicationSetting, "plex_account")
        if row is None:
            row = ApplicationSetting(key="plex_account", value={})
            self.db.add(row)
            self.db.flush()
        return row

    def _identity(self) -> PlexDeviceIdentity:
        row = self._row()
        value = dict(row.value or {})
        if not value.get("client_id") or not value.get("private_key") or not value.get("kid"):
            private_key = Ed25519PrivateKey.generate()
            private_raw = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            value.update(
                {
                    "client_id": secrets.token_urlsafe(24),
                    "kid": secrets.token_urlsafe(12),
                    "private_key": encrypt_secret(_b64url(private_raw)),
                }
            )
            row.value = value
            self.db.flush()
        raw = decrypt_secret(str(value["private_key"]))
        padded = raw + "=" * (-len(raw) % 4)
        private_key = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(padded))
        return PlexDeviceIdentity(str(value["client_id"]), str(value["kid"]), private_key)

    def _jwk(self, identity: PlexDeviceIdentity) -> dict:
        public_raw = identity.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": _b64url(public_raw),
            "kid": identity.kid,
            "use": "sig",
            "alg": "EdDSA",
        }

    def _device_jwt(self, identity: PlexDeviceIdentity, extra: dict | None = None) -> str:
        now = int(time.time())
        header = {"typ": "JWT", "alg": "EdDSA", "kid": identity.kid}
        payload = {"aud": "plex.tv", "iss": identity.client_id, "iat": now, "exp": now + 300}
        if extra:
            payload.update(extra)
        head = _b64url(json.dumps(header, separators=(",", ":")).encode())
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{head}.{body}".encode("ascii")
        signature = _b64url(identity.private_key.sign(signing_input))
        return f"{head}.{body}.{signature}"

    def headers(self, token: str | None = None) -> dict[str, str]:
        identity = self._identity()
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": PLEX_PRODUCT,
            "X-Plex-Version": "1.0.0",
            "X-Plex-Client-Identifier": identity.client_id,
        }
        if token:
            headers["X-Plex-Token"] = token
        return headers

    def start_sign_in(self, forward_url: str) -> dict:
        identity = self._identity()
        response = httpx.post(
            PLEX_PINS,
            params={"strong": "true"},
            headers=self.headers(),
            json={"strong": True, "jwk": self._jwk(identity)},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        pin_id = int(data["id"])
        code = str(data["code"])
        row = self._row()
        value = dict(row.value or {})
        value["pending_pin_id"] = pin_id
        row.value = value
        self.db.flush()
        fragment = urlencode(
            {
                "clientID": identity.client_id,
                "code": code,
                "forwardUrl": forward_url,
                "context[device][product]": PLEX_PRODUCT,
            }
        )
        return {"pin_id": pin_id, "auth_url": f"{PLEX_AUTH_APP}{fragment}"}

    def poll_sign_in(self, pin_id: int) -> dict:
        identity = self._identity()
        response = httpx.get(
            f"{PLEX_PINS}/{pin_id}",
            params={"deviceJWT": self._device_jwt(identity)},
            headers=self.headers(),
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("authToken") or data.get("auth_token")
        if not token:
            return {"authenticated": False}
        row = self._row()
        value = dict(row.value or {})
        value.update(
            {
                "account_token": encrypt_secret(str(token)),
                "linked_at": int(time.time()),
                "pending_pin_id": None,
            }
        )
        row.value = value
        self.db.flush()
        return {"authenticated": True}

    def account_token(self, refresh: bool = True) -> str:
        row = self._row()
        value = dict(row.value or {})
        encrypted = value.get("account_token")
        if not encrypted:
            return ""
        token = decrypt_secret(str(encrypted))
        if refresh:
            linked = int(value.get("linked_at") or 0)
            if time.time() - linked > 5 * 24 * 3600:
                token = self.refresh_token(token)
        return token

    def refresh_token(self, current_token: str) -> str:
        identity = self._identity()
        nonce_response = httpx.get(PLEX_NONCE, headers=self.headers(), timeout=15)
        nonce_response.raise_for_status()
        nonce = nonce_response.json()["nonce"]
        signed = self._device_jwt(
            identity,
            {"nonce": nonce, "scope": "username,email,friendly_name"},
        )
        response = httpx.post(
            PLEX_TOKEN,
            headers=self.headers(),
            json={"jwt": signed},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        token = str(data.get("auth_token") or data.get("authToken") or "")
        if not token:
            return current_token
        row = self._row()
        value = dict(row.value or {})
        value["account_token"] = encrypt_secret(token)
        value["linked_at"] = int(time.time())
        row.value = value
        self.db.flush()
        return token

    def _probe_connection(self, uri: str, token: str) -> tuple[bool, str | None]:
        try:
            response = httpx.get(
                uri.rstrip("/") + "/identity",
                headers=self.headers(token),
                timeout=4,
                follow_redirects=True,
            )
            response.raise_for_status()
            return True, None
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if token:
                message = message.replace(token, "[redacted]")
            return False, message[:500]

    def _pms_tokens_from_devices(self, account_token: str) -> dict[str, str]:
        last_error: Exception | None = None
        for endpoint in PLEX_DEVICES:
            try:
                response = httpx.get(
                    endpoint,
                    headers=self.headers(account_token),
                    timeout=20,
                    follow_redirects=True,
                )
                response.raise_for_status()
                payload = response.json()
                devices = payload if isinstance(payload, list) else payload.get("devices", [])
                result: dict[str, str] = {}
                for device in devices:
                    if not isinstance(device, dict):
                        continue
                    provides = str(device.get("provides") or "")
                    if "server" not in provides:
                        continue
                    identifier = str(device.get("clientIdentifier") or "")
                    device_token = str(device.get("token") or "")
                    if identifier and device_token:
                        result[identifier] = device_token
                return result
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        return {}

    def resources(self, probe: bool = True) -> list[dict]:
        account_token = self.account_token(refresh=True)
        if not account_token:
            return []

        response = httpx.get(
            PLEX_RESOURCES,
            params={"includeHttps": 1, "includeRelay": 1, "includeIPv6": 1},
            headers=self.headers(account_token),
            timeout=20,
        )
        response.raise_for_status()

        device_tokens: dict[str, str] = {}
        device_token_error: str | None = None
        try:
            device_tokens = self._pms_tokens_from_devices(account_token)
        except Exception as exc:
            device_token_error = f"{type(exc).__name__}: {exc}"[:500]

        resources = []
        for item in response.json():
            if "server" not in str(item.get("provides") or ""):
                continue

            identifier = str(item.get("clientIdentifier") or "")
            resource_token = str(item.get("accessToken") or "")
            server_token = device_tokens.get(identifier, "")
            token_source = "devices" if server_token else ""
            if not server_token and _is_legacy_pms_token(resource_token):
                server_token = resource_token
                token_source = "resources_legacy"

            connections = []
            for connection in item.get("connections") or []:
                uri = connection.get("uri")
                if not uri:
                    continue
                if not server_token:
                    if resource_token and not _is_legacy_pms_token(resource_token):
                        token_error = (
                            "Plex returned a JWT resource token, but this PMS requires a legacy server token. "
                            "No matching /api/v2/devices token was available."
                        )
                    else:
                        token_error = device_token_error or "No PMS-compatible server token was returned by Plex"
                    reachable, error = False, token_error
                elif probe:
                    reachable, error = self._probe_connection(str(uri), server_token)
                else:
                    reachable, error = False, None
                connections.append(
                    {
                        "uri": uri,
                        "local": bool(connection.get("local")),
                        "relay": bool(connection.get("relay")),
                        "protocol": connection.get("protocol"),
                        "reachable": reachable,
                        "error": error,
                    }
                )
            connections.sort(
                key=lambda x: (
                    not bool(x.get("reachable")),
                    bool(x.get("relay")),
                    not bool(x.get("local")),
                    x.get("protocol") != "https",
                )
            )
            resources.append(
                {
                    "name": item.get("name"),
                    "client_identifier": identifier,
                    "owned": bool(item.get("owned")),
                    "access_token": server_token,
                    "token_source": token_source,
                    "pms_token_available": bool(server_token),
                    "connections": connections,
                }
            )
        return resources
