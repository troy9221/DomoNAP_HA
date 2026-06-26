from __future__ import annotations

import logging
from typing import Optional, Callable

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import DOMAIN, API, EVENT_INCOMING_CALL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    entities: list[IntercomCallImageEntity] = []
    api = hass.data[DOMAIN][config_entry.entry_id][API]

    response = await api.get_all_keys()
    if isinstance(response, dict) and "error" in response:
        _LOGGER.error("Failed to load Domonap keys for image entities: %s", response)
        async_add_entities(entities, True)
        return
    keys = response.get("results", [])

    for key in keys:
        # создаём сущность только если есть стартовый превью-URL
        if key.get("videoPreview") is not None:
            key_id: str = key["id"]
            door_id: str = key["doorId"]
            door_name: str = key["name"]
            address: Optional[str] = key.get("addressString")
            photo_url: str = key["videoPreview"]

            entities.append(
                IntercomCallImageEntity(
                    hass=hass,
                    api=api,
                    key_id=key_id,
                    door_id=door_id,
                    device_name=door_name,
                    address=address,
                    photo_url=photo_url,
                    key_data=key,
                )
            )

    async_add_entities(entities, True)


class IntercomCallImageEntity(ImageEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "incoming_call_image"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        api,
        key_id: str,
        door_id: str,
        device_name: str,
        address: Optional[str] = None,
        photo_url: Optional[str] = None,
        key_data: dict = None,
    ):
        super().__init__(hass)
        self._api = api
        self._key_id = key_id
        self._door_id = door_id
        self._device_name = device_name
        self._address = address
        self._photo_url = photo_url
        self._key_data = key_data
        self._image_bytes: Optional[bytes] = None
        self._unsub: Optional[Callable[[], None]] = None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = dict(self._key_data) if self._key_data else {}
        if self._address:
            attrs["addressString"] = self._address
        return attrs

    @property
    def unique_id(self) -> str:
        return f"{self._door_id}_photo"

    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._key_id)},
            "name": self._device_name,
            "manufacturer": "Domonap",
            "model": "Intercom Device",
        }
        if self._address:
            info["suggested_area"] = self._address
        return info

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(
            EVENT_INCOMING_CALL, self._handle_incoming_call
        )

        if self._photo_url:
            data = await self._http_get_bytes(self._photo_url)
            if data:
                await self._set_image(data)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_image(self) -> bytes | None:
        return self._image_bytes

    @callback
    def _handle_incoming_call(self, event) -> None:
        if event.data.get("DoorId") != self._door_id:
            return

        original_photo_url: Optional[str] = event.data.get("OriginalPhotoUrl")
        original_video_preview: Optional[str] = event.data.get("OriginalVideoPreview")
        photo_url: Optional[str] = (
            original_photo_url
            or original_video_preview
            or event.data.get("PhotoUrl")
        )
        if not photo_url:
            return
        authorized = original_photo_url is None
        fallback_url: Optional[str] = original_video_preview or event.data.get(
            "VideoPreview"
        )

        async def _fetch_and_set():
            data = await self._http_get_bytes(
                photo_url,
                authorized=authorized,
                fallback_url=fallback_url,
            )
            if data:
                await self._set_image(data)

        self.hass.async_create_task(_fetch_and_set())

    async def _set_image(self, data: bytes) -> None:
        self._image_bytes = data
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def _http_get_bytes(
        self,
        url: str,
        *,
        authorized: bool = True,
        fallback_url: Optional[str] = None,
        fallback_authorized: bool = True,
    ) -> Optional[bytes]:
        response = await self._api.fetch_external_bytes(url, authorized=authorized)
        if response.get("ok"):
            return response["body"]

        if fallback_url:
            _LOGGER.debug(
                "GET %s failed: %s. Trying fallback %s",
                url,
                response.get("error"),
                fallback_url,
            )
            response = await self._api.fetch_external_bytes(
                fallback_url,
                authorized=fallback_authorized,
            )
            if response.get("ok"):
                return response["body"]

        _LOGGER.debug("GET %s failed: %s", url, response.get("error"))
        return None
