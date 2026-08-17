"""Автодашборд Domonap: двери авторизованного аккаунта + поддержка + лица."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DASHBOARD_SETUP_FLAG,
    DASHBOARD_TITLE,
    DASHBOARD_URL_PATH,
    DOMAIN,
    is_domonap_dashboard_config,
)
from .dashboard_layout import generate_dashboard_config, layout_fingerprint

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

    await _ensure_dashboard_title(hass, lovelace_data)
    await _ensure_dashboard_config(hass, existing)


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


async def _ensure_dashboard_title(hass: HomeAssistant, lovelace_data=None) -> None:
    """Переименовать сайдбар с устаревшего «Домофон» на Domonap."""
    item = await _update_dashboard_storage_title(hass)
    if item is None and lovelace_data is not None:
        store = getattr(lovelace_data, "dashboards", {}).get(DASHBOARD_URL_PATH)
        conf = getattr(store, "config", None) if store is not None else None
        if isinstance(conf, dict):
            item = conf
    _refresh_frontend_panel(hass, item)


async def _update_dashboard_storage_title(hass: HomeAssistant) -> dict[str, Any] | None:
    try:
        from homeassistant.components.lovelace.dashboard import DashboardsCollection
    except Exception:
        return await _update_dashboard_store_file(hass)

    try:
        collection = DashboardsCollection(hass)
        await collection.async_load()
    except Exception:
        return await _update_dashboard_store_file(hass)

    for item in collection.async_items():
        if item.get("url_path") != DASHBOARD_URL_PATH:
            continue
        title = str(item.get("title") or "")
        if title == DASHBOARD_TITLE:
            return item
        item_id = item.get("id")
        update = getattr(collection, "async_update_item", None)
        if item_id is None or not callable(update):
            return await _update_dashboard_store_file(hass)
        try:
            updated = await update(item_id, {"title": DASHBOARD_TITLE})
            _LOGGER.info(
                "Renamed Lovelace dashboard /%s title to %s",
                DASHBOARD_URL_PATH,
                DASHBOARD_TITLE,
            )
            return updated if isinstance(updated, dict) else {**item, "title": DASHBOARD_TITLE}
        except Exception:
            _LOGGER.debug("Could not rename Domonap dashboard via collection", exc_info=True)
            return await _update_dashboard_store_file(hass)
    return await _update_dashboard_store_file(hass)


async def _update_dashboard_store_file(hass: HomeAssistant) -> dict[str, Any] | None:
    """Fallback: правим .storage/lovelace_dashboards напрямую."""
    try:
        from homeassistant.helpers.storage import Store
    except Exception:
        return None

    store = Store(hass, 1, "lovelace_dashboards")
    try:
        data = await store.async_load()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None

    changed = False
    found: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict) or item.get("url_path") != DASHBOARD_URL_PATH:
            continue
        found = item
        title = str(item.get("title") or "")
        if title != DASHBOARD_TITLE:
            item["title"] = DASHBOARD_TITLE
            changed = True
        break

    if changed:
        try:
            await store.async_save(data)
            _LOGGER.info(
                "Renamed Lovelace dashboard /%s title to %s via storage",
                DASHBOARD_URL_PATH,
                DASHBOARD_TITLE,
            )
        except Exception:
            _LOGGER.debug("Could not save lovelace_dashboards title", exc_info=True)
            return found
    return found


def _refresh_frontend_panel(hass: HomeAssistant, item: dict[str, Any] | None) -> None:
    """Обновить подпись в боковом меню (уже зарегистрированная панель)."""
    try:
        from homeassistant.components import frontend
    except Exception:
        return

    icon = (item or {}).get("icon") or "mdi:doorbell-video"
    require_admin = bool((item or {}).get("require_admin", False))
    panels = hass.data.get("frontend_panels") or {}
    current = panels.get(DASHBOARD_URL_PATH)
    current_title = getattr(current, "sidebar_title", None) if current is not None else None
    if current_title == DASHBOARD_TITLE:
        return

    try:
        frontend.async_remove_panel(hass, DASHBOARD_URL_PATH)
    except Exception:
        _LOGGER.debug("Could not remove Domonap frontend panel before rename", exc_info=True)

    kwargs: dict[str, Any] = {
        "sidebar_title": DASHBOARD_TITLE,
        "sidebar_icon": icon,
        "frontend_url_path": DASHBOARD_URL_PATH,
        "require_admin": require_admin,
        "config": {"mode": "storage"},
        "update": False,
    }
    try:
        frontend.async_register_built_in_panel(hass, "lovelace", **kwargs)
        _LOGGER.info("Registered Domonap sidebar panel title as %s", DASHBOARD_TITLE)
    except TypeError:
        kwargs.pop("update", None)
        try:
            frontend.async_register_built_in_panel(hass, "lovelace", **kwargs)
            _LOGGER.info("Registered Domonap sidebar panel title as %s", DASHBOARD_TITLE)
        except Exception:
            _LOGGER.debug("Could not re-register Domonap frontend panel", exc_info=True)
    except ValueError:
        # Панель уже есть — пробуем поправить объект в памяти.
        if current is not None and hasattr(current, "sidebar_title"):
            try:
                current.sidebar_title = DASHBOARD_TITLE
            except Exception:
                _LOGGER.debug("Could not mutate frontend panel title", exc_info=True)
    except Exception:
        _LOGGER.debug("Could not refresh Domonap frontend panel", exc_info=True)


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
        "sidebar_title": DASHBOARD_TITLE,
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
        _refresh_frontend_panel(hass, item)
    return store


async def _ensure_dashboard_config(hass: HomeAssistant, store) -> None:
    """Сохранить обычные Lovelace-views вместо JS-strategy.

    Strategy-дашборд ждёт custom element ``ll-strategy-dashboard-domonap``.
    Если extra JS ещё не загрузился (Companion, медленная сеть, гонка при
    старте), HA показывает Timeout waiting for strategy element.
    """
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

    generated = generate_dashboard_config(hass)
    if layout_fingerprint(config) == layout_fingerprint(generated):
        return
    await save(generated)
    _LOGGER.info("Saved Domonap dashboard views on /%s", DASHBOARD_URL_PATH)
