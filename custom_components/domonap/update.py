from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    GITHUB_RELEASES_LIST_URL,
    GITHUB_RELEASES_URL,
    GITHUB_REPO,
    UPDATE_COORDINATOR,
    github_tag_archive_url,
    is_newer_version,
    normalize_release_version,
    parse_github_release_payload,
)
from .release_install import install_component_from_zip
from .util import extract_phone_digits

_LOGGER = logging.getLogger(__name__)

# Панель «лений HA показывает сущность только с feature INSTALL.
# Проверяем GitHub при старте и затем каждые 30 минут — HACS сам может
# держать кэш пользовательского репозитория до ~48 часов.
UPDATE_SCAN_INTERVAL = timedelta(minutes=30)
_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "DomoNAP_HA",
    "X-GitHub-Api-Version": "2022-11-28",
}


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
    async_add_entities(
        [DomonapIntegrationUpdate(coordinator, config_entry, phone_digits)]
    )


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
        payload = await self._async_fetch_release(session)
        if not payload:
            return {
                "installed": installed,
                "latest": installed,
                "notes": None,
                "url": f"https://github.com/{GITHUB_REPO}/releases",
                "zipball_url": None,
                "tag_name": None,
            }

        tag = payload.get("tag_name")
        latest = normalize_release_version(tag) or installed
        if not is_newer_version(latest, installed):
            latest = installed
        return {
            "installed": installed,
            "latest": latest,
            "notes": payload.get("body") or payload.get("name"),
            "url": payload.get("html_url")
            or f"https://github.com/{GITHUB_REPO}/releases",
            "zipball_url": payload.get("zipball_url"),
            "tag_name": tag,
        }

    async def _async_fetch_release(
        self, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for url in (GITHUB_RELEASES_URL, GITHUB_RELEASES_LIST_URL):
            try:
                async with session.get(
                    url,
                    headers=_GITHUB_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 404:
                        return {}
                    if resp.status >= 400:
                        last_error = UpdateFailed(f"GitHub HTTP {resp.status}")
                        _LOGGER.debug(
                            "GitHub releases %s failed: HTTP %s", url, resp.status
                        )
                        continue
                    payload = await resp.json()
            except (TimeoutError, aiohttp.ClientError) as err:
                last_error = UpdateFailed(str(err))
                _LOGGER.debug("GitHub releases %s failed: %s", url, err)
                continue
            parsed = parse_github_release_payload(payload)
            if parsed:
                return parsed
        if last_error:
            raise last_error
        return {}

    async def _async_installed_version(self) -> str:
        integration = await async_get_integration(self.hass, DOMAIN)
        return str(integration.version or "")


class DomonapIntegrationUpdate(
    CoordinatorEntity[DomonapVersionCoordinator],
    UpdateEntity,
):
    """Сущность обновления: Настройки → Система → Обновления."""

    _attr_has_entity_name = True
    _attr_translation_key = "integration"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.BACKUP
        | UpdateEntityFeature.RELEASE_NOTES
    )
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
        self._installed_override: str | None = None
        self._attr_in_progress = False

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
        if self._installed_override:
            return self._installed_override
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

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        target = normalize_release_version(version) or self.latest_version
        if not target:
            raise HomeAssistantError("No Domonap version available to install")

        data = self.coordinator.data or {}
        urls: list[str] = []
        zipball = data.get("zipball_url")
        if zipball:
            urls.append(zipball)
        tag = data.get("tag_name") or target
        urls.append(github_tag_archive_url(str(tag)))
        if not str(tag).startswith("v"):
            urls.append(github_tag_archive_url(f"v{tag}"))

        integration = await async_get_integration(self.hass, DOMAIN)
        dest = Path(integration.file_path)
        session = async_get_clientsession(self.hass)

        self._attr_in_progress = True
        self.async_write_ha_state()
        archive: bytes | None = None
        last_error: str | None = None
        try:
            for url in urls:
                try:
                    async with session.get(
                        url,
                        headers={"User-Agent": "DomoNAP_HA"},
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        if resp.status >= 400:
                            last_error = f"HTTP {resp.status} for {url}"
                            continue
                        archive = await resp.read()
                        break
                except (TimeoutError, aiohttp.ClientError) as err:
                    last_error = str(err)
            if not archive:
                raise HomeAssistantError(
                    f"Failed to download Domonap {target}: {last_error}"
                )

            await self.hass.async_add_executor_job(
                install_component_from_zip,
                archive,
                dest,
                backup,
            )
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()

        self._installed_override = target
        self.async_write_ha_state()
        from homeassistant.components import persistent_notification

        persistent_notification.async_create(
            self.hass,
            f"Domonap {target} скачан. Перезапустите Home Assistant, "
            "чтобы применить обновление.",
            title="Domonap: требуется перезапуск",
            notification_id=f"{DOMAIN}_restart_required",
        )
        _LOGGER.info("Installed Domonap %s into %s; restart required", target, dest)
