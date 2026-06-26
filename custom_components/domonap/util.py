from __future__ import annotations

import re

from homeassistant.config_entries import ConfigEntry


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

