from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, API, WEBRTC_PROXY, MEDIA_PROXY
from .util import (
    INVALID_LAST_CALL_STATES,
    extract_phone_digits,
    open_relay_from_last_call_state,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_OPEN_RELAY_BY_DOOR_ID = "open_relay_by_door_id"
SERVICE_OPEN_RELAY_BY_KEY_ID = "open_relay_by_key_id"
SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID = "open_relay_by_last_call_door_id"

SERVICE_OPEN_RELAY_BY_DOOR_ID_SCHEMA = vol.Schema(
    {
        vol.Required("door_id"): cv.string,
        # When multiple config entries are set up, allow targeting a specific one.
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_OPEN_RELAY_BY_KEY_ID_SCHEMA = vol.Schema(
    {
        vol.Required("key_id"): cv.string,
        # When multiple config entries are set up, allow targeting a specific one.
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID_SCHEMA = vol.Schema(
    {
        # Optional entity_id of the sensor. When omitted, we will try to find one.
        vol.Optional("entity_id"): cv.entity_id,
        # When multiple config entries are set up, allow targeting a specific one.
        vol.Optional("config_entry_id"): cv.string,
    }
)


# Service keys stored under hass.data[DOMAIN] that are NOT config entries.
_NON_ENTRY_KEYS = frozenset({WEBRTC_PROXY, MEDIA_PROXY})


def _is_entry_bucket(value: Any) -> bool:
    """True if the value looks like a config-entry data bucket (dict with API)."""
    return isinstance(value, dict) and API in value


def _get_entry_api(hass: HomeAssistant, entry_id: str | None) -> Any:
    """Safely fetch the IntercomAPI object for a given entry_id."""
    if not entry_id:
        return None
    bucket = hass.data.get(DOMAIN, {}).get(entry_id)
    if not _is_entry_bucket(bucket):
        return None
    return bucket.get(API)


def _select_entry_id(hass: HomeAssistant, requested_entry_id: str | None) -> str | None:
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data:
        return None

    if requested_entry_id:
        if requested_entry_id in _NON_ENTRY_KEYS:
            return None
        if requested_entry_id in domain_data and _is_entry_bucket(domain_data[requested_entry_id]):
            return requested_entry_id
        return None

    # Fallback: first real config entry (skip service keys like webrtc_proxy/media_proxy)
    for key, value in domain_data.items():
        if key in _NON_ENTRY_KEYS:
            continue
        if _is_entry_bucket(value):
            return key

    return None


def _find_last_call_sensor_entity_id(hass: HomeAssistant, entry_id: str | None) -> str | None:
    """Try to find last_call_door_id sensor entity_id."""
    # Prefer the new naming: sensor.<phone_digits>_last_call_door_id
    if entry_id:
        try:
            entry = hass.config_entries.async_get_entry(entry_id)
        except Exception:
            entry = None

        if entry is not None:
            phone_digits = extract_phone_digits(entry)
            if phone_digits:
                candidate = f"sensor.{phone_digits}_last_call_door_id"
                if hass.states.get(candidate) is not None:
                    return candidate

        # Backward compatibility (previous logic)
        legacy = f"sensor.{DOMAIN}_{entry_id}_last_call_door_id"
        if hass.states.get(legacy) is not None:
            return legacy

    # Fallback: first sensor entity with expected unique_id suffix in entity_id
    for st in hass.states.async_all("sensor"):
        if st.entity_id.endswith("_last_call_door_id") and st.entity_id.startswith("sensor."):
            return st.entity_id

    return None


async def async_setup_actions(hass: HomeAssistant) -> None:
    """Register Domonap actions (services)."""

    async def handle_open_relay_by_door_id(call: ServiceCall) -> None:
        door_id: str = call.data["door_id"]
        requested_entry_id: str | None = call.data.get("config_entry_id")

        entry_id = _select_entry_id(hass, requested_entry_id)
        if not entry_id:
            _LOGGER.error("No Domonap config entries are set up")
            raise HomeAssistantError("No Domonap config entries are set up")

        api = _get_entry_api(hass, entry_id)
        if api is None:
            _LOGGER.error("Domonap API is not available for entry_id=%s", entry_id)
            raise HomeAssistantError(f"Domonap API is not available for entry_id={entry_id}")

        res: Any = await api.open_relay_by_door_id(door_id)
        if isinstance(res, dict) and res.get("ok") is True:
            _LOGGER.debug("Door relay opened (door_id=%s, entry_id=%s)", door_id, entry_id)
            return

        _LOGGER.error("Failed to open relay by door_id=%s entry_id=%s: %s", door_id, entry_id, res)
        raise HomeAssistantError(f"Failed to open relay by door_id={door_id}")

    async def handle_open_relay_by_key_id(call: ServiceCall) -> None:
        key_id: str = call.data["key_id"]
        requested_entry_id: str | None = call.data.get("config_entry_id")

        entry_id = _select_entry_id(hass, requested_entry_id)
        if not entry_id:
            _LOGGER.error("No Domonap config entries are set up")
            raise HomeAssistantError("No Domonap config entries are set up")

        api = _get_entry_api(hass, entry_id)
        if api is None:
            _LOGGER.error("Domonap API is not available for entry_id=%s", entry_id)
            raise HomeAssistantError(f"Domonap API is not available for entry_id={entry_id}")

        res: Any = await api.open_relay_by_key_id(key_id)
        if isinstance(res, dict) and res.get("ok") is True:
            _LOGGER.debug("Door relay opened (key_id=%s, entry_id=%s)", key_id, entry_id)
            return

        _LOGGER.error("Failed to open relay by key_id=%s entry_id=%s: %s", key_id, entry_id, res)
        raise HomeAssistantError(f"Failed to open relay by key_id={key_id}")

    async def handle_open_relay_by_last_call_door_id(call: ServiceCall) -> dict[str, Any]:
        """Open door based on last incoming call sensor state."""
        requested_entry_id: str | None = call.data.get("config_entry_id")
        entry_id = _select_entry_id(hass, requested_entry_id)
        if not entry_id:
            return {"status": "error", "reason": "no_config_entries"}

        api = _get_entry_api(hass, entry_id)
        if api is None:
            return {"status": "error", "reason": "api_unavailable", "config_entry_id": entry_id}

        entity_id: str | None = call.data.get("entity_id")
        if not entity_id:
            entity_id = _find_last_call_sensor_entity_id(hass, entry_id)

        if not entity_id:
            return {"status": "error", "reason": "sensor_not_found", "config_entry_id": entry_id}

        st = hass.states.get(entity_id)
        if st is None:
            return {"status": "error", "reason": "sensor_not_found", "entity_id": entity_id}

        if st.state in INVALID_LAST_CALL_STATES:
            return {"status": "skipped", "reason": "no_last_call", "entity_id": entity_id, "state": st.state}

        attrs = st.attributes or {}

        # Try to get a human-friendly door name from sensor attributes.
        door_name = (
            attrs.get("DoorName")
            or attrs.get("door_name")
            or attrs.get("Address")
            or attrs.get("Body")
            or attrs.get("Title")
        )

        result = await open_relay_from_last_call_state(api, st)

        return {
            "status": "ok" if result["ok"] else "error",
            "door_id": result["door_id"],
            "door_name": door_name,
            "call_id": result["call_id"],
            "end_call_result": result["end_call_result"],
            "entity_id": entity_id,
            "config_entry_id": entry_id,
            "response": result["response"],
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_OPEN_RELAY_BY_DOOR_ID,
        handle_open_relay_by_door_id,
        schema=SERVICE_OPEN_RELAY_BY_DOOR_ID_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_OPEN_RELAY_BY_KEY_ID,
        handle_open_relay_by_key_id,
        schema=SERVICE_OPEN_RELAY_BY_KEY_ID_SCHEMA,
    )

    # New: open door using last-call sensor
    hass.services.async_register(
        DOMAIN,
        SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID,
        handle_open_relay_by_last_call_door_id,
        schema=SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID_SCHEMA,
        supports_response=True,
    )


async def async_unload_actions(hass: HomeAssistant) -> None:
    """Unregister Domonap actions (services)."""
    for service in (
        SERVICE_OPEN_RELAY_BY_DOOR_ID,
        SERVICE_OPEN_RELAY_BY_KEY_ID,
        SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID,
    ):
        try:
            hass.services.async_remove(DOMAIN, service)
        except Exception:
            _LOGGER.debug("Failed to remove service %s.%s", DOMAIN, service, exc_info=True)
