from __future__ import annotations

import logging
import re
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)

# Состояния сенсора last_call, которые считаются "нет актуального звонка".
INVALID_LAST_CALL_STATES = ("unknown", "unavailable", "none", "None", "")


def extract_phone_digits(entry: ConfigEntry) -> str | None:
    """Return phone number containing only digits.

    Prefers `entry.data["phone_number"]` (already sanitized by config flow),
    falls back to parsing `entry.title` (format like "+7 9991234567").
    """

    # Prefer explicit data (from config_flow)
    phone = entry.data.get("phone_number")
    if isinstance(phone, str) and phone.strip():
        digits = re.sub(r"\D", "", phone)
        return digits or None

    # Fallback: parse from title
    title = entry.title or ""
    digits = re.sub(r"\D", "", title)
    return digits or None


def is_valid_last_call_state(state: Optional[State]) -> bool:
    """Return True if the last-call sensor holds a usable door_id."""
    return state is not None and state.state not in INVALID_LAST_CALL_STATES


def extract_call_id(state: State) -> str:
    """Return the CallId attribute of a last-call sensor state (may be empty)."""
    raw_call_id = (state.attributes or {}).get("CallId")
    return str(raw_call_id).strip() if raw_call_id is not None else ""


async def open_relay_from_last_call_state(api: Any, state: State) -> dict[str, Any]:
    """Open the relay for the door stored in a last-call sensor state.

    Reads `door_id` from the sensor state and `CallId` from its attributes,
    opens the relay and, on success, notifies the backend that the call ended.

    Returns a result dict:
        {"ok": bool, "door_id": str, "call_id": str | None,
         "response": <open response>, "end_call_result": <end response> | None}
    """
    door_id = state.state
    call_id = extract_call_id(state)

    response: Any = await api.open_relay_by_door_id(door_id)
    ok = isinstance(response, dict) and response.get("ok") is True

    end_call_result: Any = None
    if ok and call_id:
        try:
            end_call_result = await api.end_call_notify(call_id)
            if not (isinstance(end_call_result, dict) and end_call_result.get("ok") is True):
                _LOGGER.error(
                    "end_call_notify failed for call_id=%s: %s", call_id, end_call_result
                )
        except Exception:
            _LOGGER.exception("end_call_notify failed for call_id=%s", call_id)
            end_call_result = {"ok": False, "error": "exception"}

    if not ok:
        _LOGGER.error(
            "Failed to open relay by last call door_id=%s: %s", door_id, response
        )

    return {
        "ok": ok,
        "door_id": door_id,
        "call_id": call_id or None,
        "response": response,
        "end_call_result": end_call_result,
    }
