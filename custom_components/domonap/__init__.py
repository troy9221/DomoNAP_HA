from __future__ import annotations

import logging
from secrets import token_urlsafe
from typing import Optional, TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    API,
    PARAM_ACCESS_TOKEN,
    PARAM_DEVICE_TOKEN,
    PARAM_INSTANCE_ID,
    PARAM_REFRESH_TOKEN,
    PARAM_REFRESH_EXPIRATION,
    MEDIA_PROXY,
    PARAM_WEBRTC_PROXY_SECRET,
    PLATFORMS,
    WEBRTC_PROXY,
)

if TYPE_CHECKING:
    from .api import IntercomAPI

_LOGGER = logging.getLogger(__name__)

REAUTH_NOTIFICATION_TITLE = "Domonap: требуется повторная авторизация"
REAUTH_NOTIFICATION_MESSAGE = (
    "Refresh token отсутствует или недействителен. "
    "Выполните повторную авторизацию интеграции Domonap в Home Assistant."
)


def _reauth_notification_id(entry: ConfigEntry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_reauth_required"


def _create_reauth_notification(hass: HomeAssistant, entry: ConfigEntry) -> None:
    persistent_notification.async_create(
        hass,
        REAUTH_NOTIFICATION_MESSAGE,
        title=REAUTH_NOTIFICATION_TITLE,
        notification_id=_reauth_notification_id(entry),
    )


def _dismiss_reauth_notification(hass: HomeAssistant, entry: ConfigEntry) -> None:
    persistent_notification.async_dismiss(hass, _reauth_notification_id(entry))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Register global actions (services).
    from .actions import async_setup_actions
    from .media_proxy import DomonapMediaProxy, DomonapMediaProxyView
    from .webrtc_proxy import DomonapWebRTCProxy, DomonapWebRTCProxySessionView, DomonapWebRTCProxyView

    await async_setup_actions(hass)
    proxy = DomonapWebRTCProxy(hass)
    hass.data[DOMAIN][WEBRTC_PROXY] = proxy
    hass.http.register_view(DomonapWebRTCProxyView(proxy))
    hass.http.register_view(DomonapWebRTCProxySessionView(proxy))
    media_proxy = DomonapMediaProxy(hass)
    hass.data[DOMAIN][MEDIA_PROXY] = media_proxy
    hass.http.register_view(DomonapMediaProxyView(media_proxy))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .api import IntercomAPI, is_android_guid
    from .notify_consumer import IntercomNotifyConsumer

    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    stored_device_token = entry.data.get(PARAM_DEVICE_TOKEN)
    api = IntercomAPI(
        device_token=(
            stored_device_token if is_android_guid(stored_device_token) else None
        ),
        instance_id=entry.data.get(PARAM_INSTANCE_ID),
    )

    new_data = dict(entry.data)
    if not new_data.get(PARAM_WEBRTC_PROXY_SECRET):
        new_data[PARAM_WEBRTC_PROXY_SECRET] = token_urlsafe(24)
    if not is_android_guid(new_data.get(PARAM_DEVICE_TOKEN)):
        if new_data.get(PARAM_DEVICE_TOKEN):
            _LOGGER.info("Replacing legacy Domonap DeviceToken with Android GUID")
        new_data[PARAM_DEVICE_TOKEN] = api.device_token
    if not new_data.get(PARAM_INSTANCE_ID):
        new_data[PARAM_INSTANCE_ID] = api.instance_id
    if new_data != entry.data:
        hass.config_entries.async_update_entry(entry, data=new_data)

    api.set_tokens(
        new_data.get(PARAM_ACCESS_TOKEN),
        new_data.get(PARAM_REFRESH_TOKEN),
        new_data.get(PARAM_REFRESH_EXPIRATION),
    )
    setup_complete = False

    def update_entry(
        access_token: Optional[str],
        refresh_token: Optional[str],
        refresh_expiration_date: Optional[str],
    ) -> None:
        nonlocal setup_complete
        _LOGGER.debug("Updating entry tokens in config_entry data")
        new_data = dict(entry.data)
        new_data.setdefault(PARAM_DEVICE_TOKEN, api.device_token)
        new_data.setdefault(PARAM_INSTANCE_ID, api.instance_id)
        if access_token and refresh_token and refresh_expiration_date:
            new_data.update(
                {
                    PARAM_ACCESS_TOKEN: access_token,
                    PARAM_REFRESH_TOKEN: refresh_token,
                    PARAM_REFRESH_EXPIRATION: refresh_expiration_date,
                }
            )
            _dismiss_reauth_notification(hass, entry)
        else:
            new_data.pop(PARAM_ACCESS_TOKEN, None)
            new_data.pop(PARAM_REFRESH_TOKEN, None)
            new_data.pop(PARAM_REFRESH_EXPIRATION, None)
            _create_reauth_notification(hass, entry)
            if setup_complete and hasattr(entry, "async_start_reauth"):
                entry.async_start_reauth(hass)
        hass.config_entries.async_update_entry(entry, data=new_data)

    api.token_update_callback = update_entry
    if not api.has_valid_refresh_token():
        api.mark_session_expired("refresh token missing or expired")
        raise ConfigEntryAuthFailed(REAUTH_NOTIFICATION_MESSAGE)
    _dismiss_reauth_notification(hass, entry)

    consumer = IntercomNotifyConsumer(
        hass,
        api,
        hass.data[DOMAIN].get(MEDIA_PROXY),
        new_data.get(PARAM_WEBRTC_PROXY_SECRET),
    )
    hass.data[DOMAIN][entry.entry_id][API] = api
    hass.data[DOMAIN][entry.entry_id]["notify_consumer"] = consumer

    setup_complete = True
    entry.async_create_background_task(hass, consumer.start(), "domonap_notify")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    stored = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})

    consumer = stored.get("notify_consumer")
    if consumer:
        try:
            await consumer.stop()
        except Exception:
            _LOGGER.debug("Exception while stopping notify consumer", exc_info=True)

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    api = stored.get(API)
    if api:
        try:
            await api.close()
        except Exception:
            _LOGGER.debug("Exception while closing API client", exc_info=True)

    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    # If this was the last entry, remove services.
    remaining_entries = [
        key for key in hass.data.get(DOMAIN, {}) if key != WEBRTC_PROXY
    ]
    if not remaining_entries:
        from .actions import async_unload_actions

        await async_unload_actions(hass)

    return unloaded
