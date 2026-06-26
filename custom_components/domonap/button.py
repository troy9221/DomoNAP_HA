import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, API
from .util import extract_phone_digits

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    entities: list[ButtonEntity] = []

    api = hass.data[DOMAIN][config_entry.entry_id][API]

    # Button: open relay using door_id from the last incoming call
    phone_digits = extract_phone_digits(config_entry) or config_entry.entry_id
    entities.append(IntercomOpenLastCallDoor(api, config_entry.entry_id, phone_digits))

    # Existing per-door buttons
    keys = await api.get_all_keys()
    for key in keys:
        key_id = key["id"]
        door_id = key["doorId"]
        door_name = key["name"]
        key_address = key.get("address")
        entities.append(IntercomDoor(api, key_id, door_id, door_name, key_address, key))

    async_add_entities(entities, True)


class IntercomOpenLastCallDoor(ButtonEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-incoming"
    _attr_translation_key = "open_relay_by_last_call_door_id"

    def __init__(self, api, entry_id: str, phone_digits: str):
        self._api = api
        self._entry_id = entry_id
        self._phone_digits = phone_digits

    @property
    def unique_id(self) -> str:
        return f"{self._phone_digits}_open_relay_by_last_call_door_id"

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
    def suggested_object_id(self) -> str:
        # Ensures entity_id like button.<phone>_open_relay_by_last_call_door_id
        return f"{self._phone_digits}_open_relay_by_last_call_door_id"

    async def async_press(self) -> None:
        # Find last-call sensor and open by its door_id.
        sensor_entity_id = f"sensor.{self._phone_digits}_last_call_door_id"
        state = self.hass.states.get(sensor_entity_id) if self.hass else None
        if state is None or state.state in ("unknown", "unavailable", "none", "None", ""):
            _LOGGER.debug("No last call door_id found in %s", sensor_entity_id)
            return

        door_id = state.state
        raw_call_id = state.attributes.get("CallId") if state.attributes else None
        call_id = str(raw_call_id).strip() if raw_call_id is not None else ""
        try:
            res = await self._api.open_relay_by_door_id(door_id)
            if not (isinstance(res, dict) and res.get("ok") is True):
                _LOGGER.error("Failed to open relay by last call door_id=%s: %s", door_id, res)
                return

            # Simplified: CallId must be non-empty after strip().
            if call_id:
                end_res = await self._api.end_call_notify(call_id)
                if not (isinstance(end_res, dict) and end_res.get("ok") is True):
                    _LOGGER.error("end_call_notify failed for call_id=%s: %s", call_id, end_res)

        except Exception:
            _LOGGER.exception("Error opening relay by last call door_id=%s", door_id)


class IntercomDoor(ButtonEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:lock"
    _attr_translation_key = "open_door"

    def __init__(self, api, key_id, door_id: str, name: str, key_address: str | None, key_data: dict):
        self._api = api
        self._key_id = key_id
        self._door_id = door_id
        self._name = name
        self._key_address = key_address
        self._key_data = key_data

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._key_data

    @property
    def unique_id(self):
        return self._door_id

    @property
    def device_info(self):
        name = self._name
        if self._key_address:
            name = f"{self._name} ({self._key_address})"
        return {
            "identifiers": {(DOMAIN, self._key_id)},
            "name": name,
            "manufacturer": "Domonap",
            "model": "Intercom Device",
            "via_device": (DOMAIN, self._key_id),
        }

    async def async_press(self):
        try:
            response = await self._api.open_relay_by_key_id(self._key_id)
            if response.get('ok') is not True:
                _LOGGER.error(f"Failed to open the door {self._name}. Response: {response}")
        except Exception as e:
            _LOGGER.error(f"Error opening the door {self._name}: {e}")