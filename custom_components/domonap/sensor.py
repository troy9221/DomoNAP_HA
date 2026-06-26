from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, API, EVENT_INCOMING_CALL
from .util import extract_phone_digits

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    entities: list[SensorEntity] = []
    api = hass.data[DOMAIN][config_entry.entry_id][API]

    response = await api.get_all_keys()
    if isinstance(response, dict) and "error" in response:
        _LOGGER.error("Failed to load Domonap keys for sensors: %s", response)
        async_add_entities(entities, True)
        return
    keys = response.get("results", [])

    for key in keys:
        try:
            key_id: str = key["id"]
            door_id: str = key["doorId"]
            door_name: str = key["name"]
            address: Optional[str] = key.get("addressString")
            pin: Optional[str] = key.get("domofonPublicPin")

            if not pin:
                _LOGGER.debug(
                    "No domofonPublicPin for door %s (%s), skipping PIN sensor",
                    door_id,
                    door_name,
                )
                continue

            entities.append(
                DomonapDoorCodeSensor(
                    key_id=key_id,
                    door_id=door_id,
                    device_name=door_name,
                    address=address,
                    pin=pin,
                    key_data=key,
                ))

        except Exception:
            _LOGGER.exception("Failed to create PIN sensor from key payload: %s", key)

    # One per config entry: stores the last DoorId that rang.
    phone_digits = extract_phone_digits(config_entry)
    entities.append(DomonapLastCallDoorIdSensor(hass, config_entry.entry_id, phone_digits))

    async_add_entities(entities, True)


class DomonapDoorCodeSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:key-variant"
    _attr_translation_key = "door_code"
    _attr_should_poll = False

    def __init__(self, key_id: str, door_id: str, device_name: str, address: Optional[str], pin: str, key_data: dict):
        self._key_id = key_id
        self._door_id = door_id
        self._device_name = device_name
        self._address = address
        self._pin = pin
        self._key_data = key_data

    @property
    def unique_id(self) -> str:
        return f"{self._door_id}_door_code"

    @property
    def native_value(self) -> str | None:
        return self._pin

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        attrs = dict(self._key_data)
        if self._address:
            attrs["addressString"] = self._address
        return attrs

    @property
    def device_info(self):
        # Имя устройства — это "дверь". Сущность будет называться "<device>: <translated entity name>"
        info = {
            "identifiers": {(DOMAIN, self._key_id)},
            "name": self._device_name,
            "manufacturer": "Domonap",
            "model": "Intercom Device",
        }
        if self._address:
            info["suggested_area"] = self._address
        return info


class DomonapLastCallDoorIdSensor(SensorEntity):
    """Sensor that stores DoorId of the last incoming call."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-incoming"
    _attr_translation_key = "last_call_door_id"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str, phone_digits: str | None):
        self._hass = hass
        self._entry_id = entry_id
        self._phone_digits = phone_digits
        self._state: str | None = None
        self._attrs: dict[str, Any] = {}
        self._unsub = None

    @property
    def device_info(self):
        # Отображаем сенсор как часть устройства-аккаунта (телефон).
        # Идентификатор должен быть стабильным и уникальным.
        phone = self._phone_digits or self._entry_id
        return {
            "identifiers": {(DOMAIN, phone)},
            "name": f"Domonap {phone}",
            "manufacturer": "Domonap",
            "model": "Domonap Account",
        }

    @property
    def unique_id(self) -> str:
        # Required by the task: base it on phone digits.
        if self._phone_digits:
            return f"{self._phone_digits}_last_call_door_id"
        return f"{self._entry_id}_last_call_door_id"

    @property
    def suggested_object_id(self) -> str | None:
        # Enforces entity_id like sensor.<phone_digits>_last_call_door_id
        if self._phone_digits:
            return f"{self._phone_digits}_last_call_door_id"
        return None

    @property
    def native_value(self) -> str | None:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        self._unsub = self._hass.bus.async_listen(EVENT_INCOMING_CALL, self._handle_incoming_call)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_incoming_call(self, event) -> None:
        # Store DoorId as main state and keep the whole event payload as attributes.
        door_id = event.data.get("DoorId")
        if not door_id:
            return

        self._state = str(door_id)

        # event.data should be JSON-serializable (dict with simple values). Keep it as-is.
        # Add our own timestamp of when HA processed the event.
        attrs = dict(event.data)
        attrs["ts"] = datetime.now(timezone.utc).isoformat() + "Z"

        self._attrs = attrs
        self.async_write_ha_state()
