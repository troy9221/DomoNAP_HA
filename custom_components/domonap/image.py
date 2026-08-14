from __future__ import annotations

import logging
from typing import Optional, Callable

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ACCOUNT_COORDINATOR, DOMAIN, API, EVENT_INCOMING_CALL
from .util import extract_phone_digits

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    entities: list[ImageEntity] = []
    api = hass.data[DOMAIN][config_entry.entry_id][API]

    response = await api.get_all_keys()
    if not isinstance(response, dict) or "error" in response:
        _LOGGER.error("Failed to load Domonap keys for image entities: %s", response)
        keys = []
    else:
        keys = response.get("results", [])

    for key in keys:
        if not isinstance(key, dict):
            continue
        # создаём сущность только если есть стартовый превью-URL
        if key.get("videoPreview") is not None:
            key_id = key.get("id")
            door_id = key.get("doorId")
            door_name = key.get("name")
            photo_url = key.get("videoPreview")
            if not key_id or not door_id or not door_name:
                _LOGGER.debug("Skipping invalid Domonap image key payload: %s", key)
                continue
            address: Optional[str] = key.get("addressString")

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

    coordinator = hass.data[DOMAIN][config_entry.entry_id].get(ACCOUNT_COORDINATOR)
    if coordinator is None:
        return

    phone_digits = extract_phone_digits(config_entry)
    known_face_ids: set[str] = set()

    def _add_face_entities() -> None:
        new_entities: list[DomonapFaceImageEntity] = []
        for face in coordinator.faces:
            image_id = str(face.get("imageId") or "")
            if not image_id or image_id in known_face_ids:
                continue
            known_face_ids.add(image_id)
            new_entities.append(
                DomonapFaceImageEntity(
                    hass,
                    coordinator,
                    config_entry.entry_id,
                    phone_digits,
                    image_id,
                )
            )
        if new_entities:
            async_add_entities(new_entities, True)

    _add_face_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(_add_face_entities))


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


class DomonapFaceImageEntity(CoordinatorEntity, ImageEntity):
    """Фото зарегистрированного лица / аватара для прохода."""

    _attr_has_entity_name = True
    _attr_translation_key = "face_image"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        entry_id: str,
        phone_digits: str | None,
        image_id: str,
    ):
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._entry_id = entry_id
        self._phone_digits = phone_digits
        self._image_id = image_id
        self._image_bytes: Optional[bytes] = None
        self._loaded_url: Optional[str] = None

    @property
    def unique_id(self) -> str:
        return f"{self._phone_digits or self._entry_id}_face_{self._image_id}"

    @property
    def suggested_object_id(self) -> str | None:
        tail = self._image_id[-6:] if self._image_id else "face"
        if self._phone_digits:
            return f"{self._phone_digits}_face_{tail}"
        return None

    @property
    def name(self) -> str | None:
        face = self._face()
        label = (face or {}).get("faceName") or (face or {}).get("name")
        if label:
            return str(label)
        return None

    @property
    def available(self) -> bool:
        return self._face() is not None

    @property
    def device_info(self):
        phone = self._phone_digits or self._entry_id
        return {
            "identifiers": {(DOMAIN, phone)},
            "name": f"Domonap {phone}",
            "manufacturer": "Domonap",
            "model": "Domonap Account",
        }

    @property
    def extra_state_attributes(self) -> dict:
        face = self._face() or {"imageId": self._image_id}
        return {
            "imageId": face.get("imageId") or self._image_id,
            "imageUrl": face.get("imageUrl"),
            "faceName": face.get("faceName") or face.get("name"),
        }

    def _face(self) -> Optional[dict]:
        for face in self.coordinator.faces:
            if str(face.get("imageId") or "") == self._image_id:
                return face
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._refresh_image()

    async def async_image(self) -> bytes | None:
        return self._image_bytes

    def _handle_coordinator_update(self) -> None:
        self.hass.async_create_task(self._refresh_image())
        super()._handle_coordinator_update()

    async def _refresh_image(self) -> None:
        face = self._face()
        url = (face or {}).get("imageUrl")
        if not url or url == self._loaded_url:
            self.async_write_ha_state()
            return
        response = await self.coordinator.api.fetch_external_bytes(url, authorized=True)
        if not response.get("ok"):
            response = await self.coordinator.api.fetch_external_bytes(
                url, authorized=False
            )
        if response.get("ok"):
            self._image_bytes = response["body"]
            self._loaded_url = url
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

