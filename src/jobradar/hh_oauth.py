from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .storage import VacancyStore


@dataclass(frozen=True)
class HHToken:
    access_token: str
    refresh_token: str
    expires_at: int


class HHOAuthManager:
    AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
    TOKEN_URL = "https://api.hh.ru/token"
    ME_URL = "https://api.hh.ru/me"

    def __init__(
        self,
        store: VacancyStore,
        *,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        bootstrap_access_token: str = "",
        user_agent: str = "JobRadar/0.2",
        timeout: int = 15,
    ) -> None:
        self.store = store
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.redirect_uri = redirect_uri.strip()
        self.bootstrap_access_token = bootstrap_access_token.strip()
        self.user_agent = user_agent
        self.timeout = timeout

    @property
    def can_authorize(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def _stored(self) -> HHToken | None:
        access = self.store.get_setting("hh_access_token") or ""
        refresh = self.store.get_setting("hh_refresh_token") or ""
        raw_expires = self.store.get_setting("hh_access_expires_at") or "0"
        try:
            expires = int(raw_expires)
        except ValueError:
            expires = 0
        if not access:
            return None
        return HHToken(access, refresh, expires)

    def _save(self, payload: dict) -> HHToken:
        access = str(payload.get("access_token") or "").strip()
        refresh = str(payload.get("refresh_token") or "").strip()
        expires_in = int(payload.get("expires_in") or 0)
        if not access:
            raise RuntimeError("HH token response did not contain access_token")
        # Keep a small safety margin so we do not start a request with a token
        # that expires mid-cycle.
        expires_at = int(time.time()) + max(0, expires_in - 30)
        self.store.set_setting("hh_access_token", access)
        if refresh:
            self.store.set_setting("hh_refresh_token", refresh)
        self.store.set_setting("hh_access_expires_at", str(expires_at))
        return HHToken(access, refresh, expires_at)

    def _post_token(self, values: dict[str, str]) -> HHToken:
        body = urlencode(values).encode("utf-8")
        request = Request(
            self.TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self.user_agent,
                "HH-User-Agent": self.user_agent,
                "Accept": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return self._save(payload)

    def authorization_url(self) -> str:
        if not self.can_authorize:
            raise RuntimeError("HH OAuth client credentials are not configured")
        state = secrets.token_urlsafe(24)
        self.store.set_setting("hh_oauth_state", state)
        return self.AUTHORIZE_URL + "?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "state": state,
                "redirect_uri": self.redirect_uri,
            }
        )

    def exchange_redirect(self, redirect_value: str) -> HHToken:
        if not self.can_authorize:
            raise RuntimeError("HH OAuth client credentials are not configured")
        raw = redirect_value.strip()
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
        code = (query.get("code") or [""])[0].strip()
        state = (query.get("state") or [""])[0].strip()
        expected_state = self.store.get_setting("hh_oauth_state") or ""
        if not code:
            raise ValueError("В redirect URL нет authorization code")
        if not expected_state or not state or not secrets.compare_digest(state, expected_state):
            raise ValueError("OAuth state не совпал — начни заново через /hh_auth")
        token = self._post_token(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            }
        )
        self.store.set_setting("hh_oauth_state", "")
        return token

    def refresh(self, refresh_token: str) -> HHToken:
        if not self.client_id or not self.client_secret:
            raise RuntimeError("HH OAuth client credentials are unavailable for refresh")
        if not refresh_token:
            raise RuntimeError("HH refresh token is unavailable")
        return self._post_token(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": refresh_token,
            }
        )

    def access_token(self) -> str:
        stored = self._stored()
        if stored is not None:
            # HH documents that refresh is allowed only after access-token expiry.
            if stored.expires_at <= 0 or int(time.time()) < stored.expires_at:
                return stored.access_token
            if stored.refresh_token:
                return self.refresh(stored.refresh_token).access_token
            return stored.access_token
        return self.bootstrap_access_token

    def verify_applicant(self, access_token: str) -> dict:
        request = Request(
            self.ME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": self.user_agent,
                "HH-User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        auth_type = str(payload.get("auth_type") or "").lower()
        if auth_type and auth_type != "applicant":
            raise RuntimeError(f"HH OAuth belongs to {auth_type}, not an applicant")
        return payload

    def connected(self) -> bool:
        return bool(self._stored() or self.bootstrap_access_token)
