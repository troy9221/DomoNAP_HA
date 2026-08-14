from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import ACCOUNT_COORDINATOR, DOMAIN, API, WEBRTC_PROXY, MEDIA_PROXY, UPDATE_COORDINATOR
from .api import is_api_error
from .util import (
    INVALID_LAST_CALL_STATES,
    extract_phone_digits,
    open_relay_from_last_call_state,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_OPEN_RELAY_BY_DOOR_ID = "open_relay_by_door_id"
SERVICE_OPEN_RELAY_BY_KEY_ID = "open_relay_by_key_id"
SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID = "open_relay_by_last_call_door_id"
SERVICE_SEND_SUPPORT_MESSAGE = "send_support_message"
SERVICE_CREATE_SUPPORT_TICKET = "create_support_ticket"
SERVICE_GET_SUPPORT_TICKETS = "get_support_tickets"
SERVICE_GET_SUPPORT_TICKET_MESSAGES = "get_support_ticket_messages"
SERVICE_CREATE_FACE = "create_face"
SERVICE_DELETE_FACE = "delete_face"

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

SERVICE_SEND_SUPPORT_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("ticket_id"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_CREATE_SUPPORT_TICKET_SCHEMA = vol.Schema(
    {
        vol.Required("text"): cv.string,
        vol.Optional("property_id"): cv.string,
        vol.Optional("theme_id"): cv.string,
        vol.Optional("theme_header"): cv.string,
        vol.Optional("address"): cv.string,
        vol.Optional("support_help_type"): cv.string,
        vol.Optional("support_help_suggestion_id"): cv.string,
        vol.Optional("activation_code"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_GET_SUPPORT_TICKETS_SCHEMA = vol.Schema(
    {
        vol.Optional("search"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_GET_SUPPORT_TICKET_MESSAGES_SCHEMA = vol.Schema(
    {
        vol.Required("ticket_id"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_CREATE_FACE_SCHEMA = vol.Schema(
    {
        vol.Required("image_url"): cv.string,
        vol.Optional("filename"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_DELETE_FACE_SCHEMA = vol.Schema(
    {
        vol.Required("image_id"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)


# Service keys stored under hass.data[DOMAIN] that are NOT config entries.
_NON_ENTRY_KEYS = frozenset({WEBRTC_PROXY, MEDIA_PROXY, UPDATE_COORDINATOR})


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


def _get_account_coordinator(hass: HomeAssistant, entry_id: str | None) -> Any:
    if not entry_id:
        return None
    bucket = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(bucket, dict):
        return None
    return bucket.get(ACCOUNT_COORDINATOR)


async def _refresh_account(hass: HomeAssistant, entry_id: str | None) -> None:
    coordinator = _get_account_coordinator(hass, entry_id)
    if coordinator is not None:
        await coordinator.async_request_refresh()


def _require_entry_api(hass: HomeAssistant, requested_entry_id: str | None) -> tuple[str, Any]:
    entry_id = _select_entry_id(hass, requested_entry_id)
    if not entry_id:
        raise HomeAssistantError("No Domonap config entries are set up")
    api = _get_entry_api(hass, entry_id)
    if api is None:
        raise HomeAssistantError(f"Domonap API is not available for entry_id={entry_id}")
    return entry_id, api


def _guess_image_meta(url: str, filename: str | None) -> tuple[str, str]:
    name = filename or url.rsplit("/", 1)[-1].split("?", 1)[0] or "face.jpg"
    lower = name.lower()
    if lower.endswith(".png"):
        return name, "image/png"
    if lower.endswith(".webp"):
        return name, "image/webp"
    if lower.endswith(".heic") or lower.endswith(".heif"):
        return name, "image/heic"
    if not lower.endswith((".jpg", ".jpeg")):
        name = f"{name}.jpg" if "." not in name else name
    return name, "image/jpeg"


def _www_root(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("www")).resolve()


def _resolve_local_face_path(hass: HomeAssistant, image_url: str) -> Path | None:
    """Map /local/... or a www filename to a file under config/www.

    HTTP(S) URLs return None so the caller downloads them. Anything else is
    treated as a local www path.
    """
    raw = (image_url or "").strip()
    if not raw or raw.startswith(("http://", "https://")):
        return None
    www = _www_root(hass)
    if raw.startswith("/local/"):
        rel = raw[len("/local/") :]
    elif raw.startswith("local/"):
        rel = raw[len("local/") :]
    elif raw.startswith("/config/www/"):
        rel = raw[len("/config/www/") :]
    elif raw.startswith("www/"):
        rel = raw[len("www/") :]
    elif "/" not in raw.replace("\\", "/"):
        rel = raw
    else:
        rel = raw.lstrip("/")
    path = (www / rel).resolve()
    if not path.is_relative_to(www):
        raise HomeAssistantError("Фото для прохода по лицу должно лежать в /config/www")
    return path


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

    async def handle_send_support_message(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        res = await api.send_message_to_support(
            call.data["text"],
            ticket_id=call.data.get("ticket_id"),
        )
        if is_api_error(res) or (isinstance(res, dict) and res.get("ok") is False):
            raise HomeAssistantError(f"Failed to send support message: {res}")
        await _refresh_account(hass, entry_id)
        return {"status": "ok", "config_entry_id": entry_id, "response": res}

    async def handle_create_support_ticket(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        payload = {"text": call.data["text"]}
        optional = {
            "propertyId": call.data.get("property_id"),
            "themeId": call.data.get("theme_id"),
            "themeHeader": call.data.get("theme_header"),
            "address": call.data.get("address"),
            "supportHelpType": call.data.get("support_help_type"),
            "supportHelpSuggestionId": call.data.get("support_help_suggestion_id"),
            "activationCode": call.data.get("activation_code"),
        }
        payload.update({key: value for key, value in optional.items() if value})
        res = await api.create_support_ticket(payload)
        if is_api_error(res):
            raise HomeAssistantError(f"Failed to create support ticket: {res}")
        await _refresh_account(hass, entry_id)
        return {"status": "ok", "config_entry_id": entry_id, "response": res}

    async def handle_get_support_tickets(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        res = await api.get_paged_tickets(search=call.data.get("search") or "")
        if is_api_error(res):
            raise HomeAssistantError(f"Failed to load support tickets: {res}")
        return {"status": "ok", "config_entry_id": entry_id, "response": res}

    async def handle_get_support_ticket_messages(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        ticket_id = call.data["ticket_id"]
        messages = await api.fetch_ticket_conversation(ticket_id)
        return {
            "status": "ok",
            "config_entry_id": entry_id,
            "ticket_id": ticket_id,
            "messages": messages,
        }

    async def handle_create_face(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        image_url = call.data["image_url"]
        filename, content_type = _guess_image_meta(image_url, call.data.get("filename"))
        local_path = _resolve_local_face_path(hass, image_url)
        if local_path is not None:
            if not local_path.is_file():
                raise HomeAssistantError(
                    "Нет файла "
                    f"{local_path.name} в /config/www. "
                    "Скопируйте фото лица туда (например domonap-face.jpg) и нажмите ещё раз."
                )
            try:
                body = await hass.async_add_executor_job(local_path.read_bytes)
            except OSError as err:
                raise HomeAssistantError(f"Не удалось прочитать фото: {err}") from err
            if not body:
                raise HomeAssistantError(f"Файл пустой: {local_path.name}")
        else:
            downloaded = await api.fetch_external_bytes(image_url, authorized=True)
            if not downloaded.get("ok"):
                downloaded = await api.fetch_external_bytes(image_url, authorized=False)
            if not downloaded.get("ok"):
                raise HomeAssistantError(
                    f"Failed to download face image: {downloaded.get('error')}"
                )
            body = downloaded["body"]
        res = await api.create_face(
            body,
            filename=filename,
            content_type=content_type,
        )
        if is_api_error(res):
            raise HomeAssistantError(f"Failed to register face: {res}")
        await _refresh_account(hass, entry_id)
        return {"status": "ok", "config_entry_id": entry_id, "response": res}

    async def handle_delete_face(call: ServiceCall) -> dict[str, Any]:
        entry_id, api = _require_entry_api(hass, call.data.get("config_entry_id"))
        res = await api.delete_face(call.data["image_id"])
        if is_api_error(res):
            raise HomeAssistantError(f"Failed to delete face: {res}")
        await _refresh_account(hass, entry_id)
        return {"status": "ok", "config_entry_id": entry_id, "response": res}

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

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_SUPPORT_MESSAGE,
        handle_send_support_message,
        schema=SERVICE_SEND_SUPPORT_MESSAGE_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_SUPPORT_TICKET,
        handle_create_support_ticket,
        schema=SERVICE_CREATE_SUPPORT_TICKET_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SUPPORT_TICKETS,
        handle_get_support_tickets,
        schema=SERVICE_GET_SUPPORT_TICKETS_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SUPPORT_TICKET_MESSAGES,
        handle_get_support_ticket_messages,
        schema=SERVICE_GET_SUPPORT_TICKET_MESSAGES_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_FACE,
        handle_create_face,
        schema=SERVICE_CREATE_FACE_SCHEMA,
        supports_response=True,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_FACE,
        handle_delete_face,
        schema=SERVICE_DELETE_FACE_SCHEMA,
        supports_response=True,
    )


async def async_unload_actions(hass: HomeAssistant) -> None:
    """Unregister Domonap actions (services)."""
    for service in (
        SERVICE_OPEN_RELAY_BY_DOOR_ID,
        SERVICE_OPEN_RELAY_BY_KEY_ID,
        SERVICE_OPEN_RELAY_BY_LAST_CALL_DOOR_ID,
        SERVICE_SEND_SUPPORT_MESSAGE,
        SERVICE_CREATE_SUPPORT_TICKET,
        SERVICE_GET_SUPPORT_TICKETS,
        SERVICE_GET_SUPPORT_TICKET_MESSAGES,
        SERVICE_CREATE_FACE,
        SERVICE_DELETE_FACE,
    ):
        try:
            hass.services.async_remove(DOMAIN, service)
        except Exception:
            _LOGGER.debug("Failed to remove service %s.%s", DOMAIN, service, exc_info=True)
