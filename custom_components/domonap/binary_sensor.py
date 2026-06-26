import logging
from typing import Optional, Callable
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from .const import DOMAIN, API, EVENT_INCOMING_CALL, RESET_DELAY

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    entities = []
    api = hass.data[DOMAIN][config_entry.entry_id][API]
    keys = await api.get_all_keys()

    seen_door_ids = set()

    for key in keys:
        key_id = key.get("id")
        door_id = key.get("doorId")
        door_name = key.get("name")
        key_address = key.get("address")
        if not key_id or not door_id or not door_name:
            _LOGGER.debug("Skipping invalid Domonap call sensor key payload: %s", key)
            continue
        if not (key.get("httpVideoUrl") or key.get("webrtcVideoUrl")):
            _LOGGER.debug(
                "No camera URL for door %s (%s), skipping call sensor",
                door_id,
                door_name,
            )
            continue
        if door_id in seen_door_ids:
            continue
        seen_door_ids.add(door_id)
        entities.append(IntercomCallBinarySensor(hass, api, key_id, door_id, door_name, key_address, key))

    async_add_entities(entities, True)


class IntercomCallBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-incoming"
    _attr_device_class = "running"
    _attr_translation_key = "incoming_call"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, api, key_id: str, door_id: str, name: str, key_address: str | None, key_data: dict):
        self._hass = hass
        self._api = api
        self._key_id = key_id
        self._door_id = door_id
        self._name = name
        self._key_address = key_address
        self._key_data = key_data
        self._state = False
        self._reset_timer: Optional[Callable[[], None]] = None
        self._listener = None

    @property
    def unique_id(self):
        return f"{self._door_id}_call"

    @property
    def is_on(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._key_data

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
        }

    async def async_added_to_hass(self):
        self._listener = self._hass.bus.async_listen(
            EVENT_INCOMING_CALL, self._handle_incoming_call
        )

    async def async_will_remove_from_hass(self):
        if self._listener:
            self._listener()
        if self._reset_timer:
            self._reset_timer()
            self._reset_timer = None

    @callback
    def _handle_incoming_call(self, event):
        door_id = event.data.get("DoorId")
        if door_id == self._door_id:
            _LOGGER.debug(
                "Incoming call detected for door %s (%s)", self._door_id, self._name
            )
            self._state = True
            self.async_write_ha_state()
            
            if self._reset_timer:
                self._reset_timer()
            
            self._reset_timer = async_call_later(
                self._hass, RESET_DELAY, self._reset_state
            )

    @callback
    def _reset_state(self, _now):
        _LOGGER.debug(
            "Resetting call state for door %s (%s)", self._door_id, self._name
        )
        self._state = False
        self._reset_timer = None
        self.async_write_ha_state()
