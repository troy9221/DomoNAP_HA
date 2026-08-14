"""Автодашборд «Домофон»: двери авторизованного аккаунта + поддержка + лица."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DASHBOARD_SETUP_FLAG,
    DASHBOARD_STRATEGY_TYPE,
    DASHBOARD_TITLE,
    DASHBOARD_URL_PATH,
    DOMAIN,
    is_domonap_dashboard_config,
)

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_ITEM = {
    "icon": "mdi:doorbell-video",
    "title": DASHBOARD_TITLE,
    "url_path": DASHBOARD_URL_PATH,
    "require_admin": False,
    "show_in_sidebar": True,
}


def _lovelace_data(hass: HomeAssistant):
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        return hass.data.get(LOVELACE_DATA) or hass.data.get("lovelace")
    except Exception:
        return hass.data.get("lovelace")


def _strategy_config() -> dict[str, Any]:
    return {"strategy": {"type": DASHBOARD_STRATEGY_TYPE}}


async def async_setup_dashboard(hass: HomeAssistant) -> None:
    """Создать боковой дашборд, если его ещё нет."""
    async def _run(_event=None) -> None:
        try:
            await _ensure_storage_dashboard(hass)
        except Exception:
            _LOGGER.debug("Could not create Domonap Lovelace dashboard", exc_info=True)

    domain_data = hass.data.setdefault(DOMAIN, {})
    if hass.is_running:
        await _run()
        return
    if domain_data.get(DASHBOARD_SETUP_FLAG):
        return
    domain_data[DASHBOARD_SETUP_FLAG] = True
    hass.bus.async_listen_once("homeassistant_started", _run)


async def _ensure_storage_dashboard(hass: HomeAssistant) -> None:
    lovelace_data = _lovelace_data(hass)
    if lovelace_data is None:
        _LOGGER.debug("Lovelace is not ready, skip dashboard setup")
        return

    dashboards = getattr(lovelace_data, "dashboards", None)
    if not isinstance(dashboards, dict):
        return

    existing = dashboards.get(DASHBOARD_URL_PATH)
    if existing is None:
        item = await _create_dashboard_item(hass)
        if item is None:
            return
        existing = dashboards.get(DASHBOARD_URL_PATH)
        if existing is None:
            existing = _attach_storage_dashboard(hass, lovelace_data, item)
        if existing is None:
            return

    await _ensure_strategy(existing)


async def _create_dashboard_item(hass: HomeAssistant) -> dict[str, Any] | None:
    try:
        from homeassistant.components.lovelace.dashboard import DashboardsCollection
    except Exception:
        _LOGGER.debug("DashboardsCollection is unavailable")
        return None

    collection = DashboardsCollection(hass)
    await collection.async_load()
    for item in collection.async_items():
        if item.get("url_path") == DASHBOARD_URL_PATH:
            return item

    payload = dict(_DASHBOARD_ITEM)
    try:
        created = await collection.async_create_item(payload)
    except (HomeAssistantError, ValueError) as err:
        _LOGGER.warning("Could not create Domonap dashboard: %s", err)
        return None
    if isinstance(created, dict) and created.get("url_path") == DASHBOARD_URL_PATH:
        _LOGGER.info("Created Lovelace dashboard /%s", DASHBOARD_URL_PATH)
        return created
    for item in collection.async_items():
        if item.get("url_path") == DASHBOARD_URL_PATH:
            _LOGGER.info("Created Lovelace dashboard /%s", DASHBOARD_URL_PATH)
            return item
    return None


def _attach_storage_dashboard(hass: HomeAssistant, lovelace_data, item: dict[str, Any]):
    try:
        from homeassistant.components import frontend
        from homeassistant.components.lovelace.dashboard import LovelaceStorage
    except Exception:
        return None

    url_path = item["url_path"]
    store = LovelaceStorage(hass, item)
    lovelace_data.dashboards[url_path] = store
    kwargs: dict[str, Any] = {
        "sidebar_title": item.get("title") or DASHBOARD_TITLE,
        "sidebar_icon": item.get("icon") or "mdi:doorbell-video",
        "frontend_url_path": url_path,
        "require_admin": bool(item.get("require_admin", False)),
        "config": {"mode": "storage"},
        "update": False,
    }
    try:
        frontend.async_register_built_in_panel(hass, "lovelace", **kwargs)
    except TypeError:
        kwargs.pop("update", None)
        frontend.async_register_built_in_panel(hass, "lovelace", **kwargs)
    except ValueError:
        _LOGGER.debug("Domonap dashboard panel already registered")
    return store


async def _ensure_strategy(store) -> None:
    load = getattr(store, "async_load", None)
    save = getattr(store, "async_save", None)
    if not callable(save):
        return

    config = None
    if callable(load):
        try:
            config = await load(False)
        except Exception:
            config = None

    if config and not is_domonap_dashboard_config(config):
        _LOGGER.debug("Leave existing dashboard /%s unchanged", DASHBOARD_URL_PATH)
        return
    if is_domonap_dashboard_config(config):
        return
    await save(_strategy_config())
    _LOGGER.info("Saved Domonap dashboard strategy on /%s", DASHBOARD_URL_PATH)
