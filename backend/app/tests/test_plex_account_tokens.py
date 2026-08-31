import httpx

from app.services.plex.account import PLEX_DEVICES, PLEX_RESOURCES, PlexAccountService


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_resources_use_legacy_device_token_for_pms(db, monkeypatch):
    service = PlexAccountService(db)
    legacy_token = "abcdefghijklmnopqrst"
    jwt_resource_token = "eyJ.resource.jwt.token"
    probe_tokens: list[str] = []

    monkeypatch.setattr(service, "account_token", lambda refresh=True: "eyJ.account.jwt.token")

    def fake_get(url, **kwargs):
        del kwargs
        if url == PLEX_RESOURCES:
            return FakeResponse(
                [
                    {
                        "name": "Home Plex",
                        "clientIdentifier": "server-machine-id",
                        "provides": "server",
                        "owned": True,
                        "accessToken": jwt_resource_token,
                        "connections": [
                            {
                                "uri": "https://example.plex.direct:32400",
                                "local": False,
                                "relay": False,
                                "protocol": "https",
                            }
                        ],
                    }
                ]
            )
        if url == PLEX_DEVICES[0]:
            return FakeResponse(
                [
                    {
                        "name": "Home Plex",
                        "clientIdentifier": "server-machine-id",
                        "provides": "server",
                        "token": legacy_token,
                        "connections": [{"uri": "https://example.plex.direct:32400"}],
                    }
                ]
            )
        raise AssertionError(f"Unexpected URL: {url}")

    def fake_probe(uri: str, token: str):
        assert uri == "https://example.plex.direct:32400"
        probe_tokens.append(token)
        return True, None

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(service, "_probe_connection", fake_probe)

    resources = service.resources()

    assert len(resources) == 1
    assert resources[0]["access_token"] == legacy_token
    assert resources[0]["token_source"] == "devices"
    assert resources[0]["pms_token_available"] is True
    assert resources[0]["connections"][0]["reachable"] is True
    assert probe_tokens == [legacy_token]
