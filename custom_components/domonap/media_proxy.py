from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from secrets import token_urlsafe

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MEDIA_PROXY_TOKEN_TTL = 300.0  # секунды
MEDIA_PROXY_MAX_TOKENS = 256

try:
    from homeassistant.helpers.network import NoURLAvailableError, get_url
except ImportError:  # pragma: no cover - depends on HA version
    NoURLAvailableError = ValueError
    get_url = None


@dataclass(slots=True)
class MediaProxyTarget:
    api: object
    url: str
    fallback_url: str | None = None
    authorized: bool = True
    fallback_authorized: bool = True
    created_at: float = field(default_factory=time.monotonic)


class DomonapMediaProxy:
    def __init__(
        self,
        hass: HomeAssistant,
        token_ttl: float = MEDIA_PROXY_TOKEN_TTL,
        max_tokens: int = MEDIA_PROXY_MAX_TOKENS,
    ) -> None:
        self._hass = hass
        self._targets: OrderedDict[tuple[str, str], MediaProxyTarget] = OrderedDict()
        self._token_ttl = token_ttl
        self._max_tokens = max_tokens

    def register_url(
        self,
        proxy_secret: str,
        api: object,
        url: str,
        fallback_url: str | None = None,
        authorized: bool = True,
        fallback_authorized: bool = True,
    ) -> str:
        self._prune()
        token = token_urlsafe(18)
        self._targets[(proxy_secret, token)] = MediaProxyTarget(
            api=api,
            url=url,
            fallback_url=fallback_url,
            authorized=authorized,
            fallback_authorized=fallback_authorized,
        )
        self._enforce_max_size()
        return self.get_proxy_url(proxy_secret, token)

    def get_proxy_path(self, proxy_secret: str, token: str) -> str:
        return f"/api/{DOMAIN}/media_proxy/{proxy_secret}/{token}"

    def get_proxy_url(self, proxy_secret: str, token: str) -> str:
        path = self.get_proxy_path(proxy_secret, token)
        base_url = self._get_base_url()
        return f"{base_url}{path}" if base_url else path

    def _is_expired(self, target: MediaProxyTarget) -> bool:
        if self._token_ttl <= 0:
            return False
        return (time.monotonic() - target.created_at) > self._token_ttl

    def _prune(self) -> None:
        """Удаляет просроченные записи."""
        if self._token_ttl <= 0:
            return
        now = time.monotonic()
        expired = [
            key
            for key, target in self._targets.items()
            if (now - target.created_at) > self._token_ttl
        ]
        for key in expired:
            self._targets.pop(key, None)
        if expired:
            _LOGGER.debug("Pruned %d expired Domonap media proxy tokens", len(expired))

    def _enforce_max_size(self) -> None:
        """Вытесняет самые старые записи при превышении лимита."""
        if self._max_tokens <= 0:
            return
        while len(self._targets) > self._max_tokens:
            self._targets.popitem(last=False)

    async def get_media(self, proxy_secret: str, token: str) -> web.Response:
        target = self._targets.get((proxy_secret, token))
        if target is not None and self._is_expired(target):
            self._targets.pop((proxy_secret, token), None)
            target = None
        if target is None:
            raise web.HTTPNotFound(text="Unknown media")

        response = await target.api.fetch_external_bytes(
            target.url,
            authorized=target.authorized,
        )
        if not response.get("ok") and target.fallback_url:
            _LOGGER.debug(
                "Domonap media proxy fallback for %s after %s",
                target.url,
                response.get("error"),
            )
            response = await target.api.fetch_external_bytes(
                target.fallback_url,
                authorized=target.fallback_authorized,
            )

        if not response.get("ok"):
            _LOGGER.warning(
                "Domonap media proxy failed for %s: %s",
                target.url,
                response.get("error"),
            )
            raise web.HTTPBadGateway(
                text=str(response.get("error", "Media fetch failed"))
            )

        headers = {
            "Cache-Control": "no-store",
            "Content-Type": response.get("content_type") or "application/octet-stream",
        }
        return web.Response(body=response["body"], headers=headers)

    def _get_base_url(self) -> str | None:
        if get_url is None:
            return None

        for prefer_external in (True, False):
            try:
                return get_url(self._hass, prefer_external=prefer_external)
            except (NoURLAvailableError, TypeError, ValueError):
                continue

        _LOGGER.debug("Unable to build Domonap media proxy URL", exc_info=True)
        return None


class DomonapMediaProxyView(HomeAssistantView):
    url = f"/api/{DOMAIN}/media_proxy/{{proxy_secret}}/{{token}}"
    name = f"api:{DOMAIN}:media_proxy"
    requires_auth = False

    def __init__(self, proxy: DomonapMediaProxy) -> None:
        self._proxy = proxy

    async def get(
        self,
        request: web.Request,
        proxy_secret: str,
        token: str,
    ) -> web.Response:
        return await self._proxy.get_media(proxy_secret, token)
