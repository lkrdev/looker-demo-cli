"""Looker SDK client initialization supporting both OAuth tokens and API3 credentials."""

from __future__ import annotations

import looker_sdk
from looker_sdk import methods40
from looker_sdk.rtl import api_settings, auth_session, serialize, transport


class BearerAuthSession(auth_session.AuthSession):
    """Auth session that authenticates using an existing Bearer token."""

    def __init__(
        self,
        token: str,
        settings: api_settings.PApiSettings,
        transp: transport.Transport,
    ) -> None:
        super().__init__(
            settings,
            transp,
            lambda data, structure: serialize.deserialize40(data=data, structure=structure),
            "4.0",
        )
        self._token = token

    def authenticate(self, transport_options: transport.TransportOptions | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @property
    def is_authenticated(self) -> bool:
        return True


class DirectSettings(api_settings.ApiSettings):
    """Minimal settings configured with a direct base URL and optional API3 credentials."""

    def __init__(self, base_url: str, client_id: str = "", client_secret: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        super().__init__(filename="", section="")

    def is_configured(self) -> bool:
        return True

    def read_config(self) -> api_settings.SettingsConfig:
        cfg = super().read_config()
        if self.base_url:
            cfg["base_url"] = self.base_url
        if self._client_id:
            cfg["client_id"] = self._client_id
        if self._client_secret:
            cfg["client_secret"] = self._client_secret
        return cfg


def get_looker_sdk(
    base_url: str | None = None,
    access_token: str | None = None,
    headers: dict[str, str] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> methods40.Looker40SDK:
    """Return an authenticated Looker40SDK instance.

    Uses an explicit Bearer access token, Authorization header, or explicit
    API3 client_id/client_secret if provided; otherwise falls back to standard
    Looker SDK API3 environment configuration.
    """
    token = access_token
    if not token and headers:
        auth_val = headers.get("Authorization") or headers.get("authorization") or ""
        if auth_val.startswith("Bearer "):
            token = auth_val[7:].strip()

    if token and base_url:
        settings = DirectSettings(base_url)
        sdk = looker_sdk.init40(config_settings=settings)
        sdk.auth = BearerAuthSession(token, settings, sdk.transport)
        return sdk

    if base_url and client_id and client_secret:
        settings = DirectSettings(base_url, client_id=client_id, client_secret=client_secret)
        return looker_sdk.init40(config_settings=settings)

    return looker_sdk.init40()
