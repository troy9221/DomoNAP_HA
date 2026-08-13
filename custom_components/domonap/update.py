from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    GITHUB_RELEASES_URL,
    GITHUB_REPO,
    UPDATE_COORDINATOR,
    normalize_release_version,
)
from .util import extract_phone_digits

_LOGGER = logging.getLogger(__name__)

UPDATE_SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    coordinator = hass.data[DOMAIN].get(UPDATE_COORDINATOR)
    if coordinator is None:
        coordinator = DomonapVersionCoordinator(hass)
        hass.data[DOMAIN][UPDATE_COORDINATOR] = coordinator
        await coordinator.async_refresh()

    phone_digits = extract_phone_digits(config_entry) or config_entry.entry_id
    async_add_entities([DomonapIntegrationUpdate(coordinator, config_entry, phone_digits)])


class DomonapVersionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Периодически проверяет GitHub Releases на новую версию интеграции."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Domonap version",
            update_interval=UPDATE_SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        installed = await self._async_installed_version()
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                GITHUB_RELEASES_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "DomoNAP_HA",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 404:
                    _LOGGER.debug(
                        "No GitHub releases for %s; HA cannot offer an update yet",
                        GITHUB_REPO,
                    )
                    return {
                        "installed": installed,
                        "latest": installed,
                        "notes": None,
                        "url": f"https://github.com/{GITHUB_REPO}/releases",
                    }
                if resp.status >= 400:
                    raise UpdateFailed(f"GitHub HTTP {resp.status}")
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(str(err)) from err

        latest = normalize_release_version(payload.get("tag_name")) or installed
        return {
            "installed": installed,
            "latest": latest,
            "notes": payload.get("body") or payload.get("name"),
            "url": payload.get("html_url")
            or f"https://github.com/{GITHUB_REPO}/releases",
        }

    async def _async_installed_version(self) -> str:
        integration = await async_get_integration(self.hass, DOMAIN)
        return str(integration.version or "")


class DomonapIntegrationUpdate(
    CoordinatorEntity[DomonapVersionCoordinator],
    UpdateEntity,
):
    """Сущность обновления: появляется в Настройки → Система → Обновления."""

    _attr_has_entity_name = True
    _attr_translation_key = "integration"
    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES
    _attr_title = "Domonap"

    def __init__(
        self,
        coordinator: DomonapVersionCoordinator,
        entry: ConfigEntry,
        phone_digits: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._phone_digits = phone_digits

    @property
    def unique_id(self) -> str:
        return f"{self._phone_digits}_integration_update"

    @property
    def device_info(self):
        phone = self._phone_digits or self._entry.entry_id
        return {
            "identifiers": {(DOMAIN, phone)},
            "name": f"Domonap {phone}",
            "manufacturer": "Domonap",
            "model": "Domonap Account",
        }

    @property
    def installed_version(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("installed")

    @property
    def latest_version(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("latest")

    @property
    def release_url(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("url")

    async def async_release_notes(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("notes")
