from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    IntercomAPI,
    is_api_error,
    normalize_face_items,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ACCOUNT_SCAN_INTERVAL = timedelta(minutes=5)


class DomonapAccountCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Периодически обновляет лица (проход по аватару) и обращения поддержки."""

    def __init__(self, hass: HomeAssistant, api: IntercomAPI) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_account",
            update_interval=ACCOUNT_SCAN_INTERVAL,
        )
        self.api = api

    @property
    def faces(self) -> list[dict[str, Any]]:
        data = self.data or {}
        return list(data.get("faces") or [])

    @property
    def tickets(self) -> list[dict[str, Any]]:
        data = self.data or {}
        return list(data.get("tickets") or [])

    async def _async_update_data(self) -> dict[str, Any]:
        previous = self.data or {}
        faces = list(previous.get("faces") or [])
        tickets = list(previous.get("tickets") or [])
        last_ticket_id = previous.get("last_ticket_id")
        last_ticket_messages: list[dict[str, Any]] = list(
            previous.get("last_ticket_messages") or []
        )
        faces_payload = previous.get("faces_payload") or {}

        try:
            faces_payload_new = await self.api.get_faces()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("GetFaces raised: %s", err)
            faces_payload_new = None
        if faces_payload_new is None:
            pass
        elif is_api_error(faces_payload_new):
            _LOGGER.debug("GetFaces failed: %s", faces_payload_new)
        else:
            faces = normalize_face_items(faces_payload_new)
            faces_payload = (
                faces_payload_new if isinstance(faces_payload_new, dict) else {}
            )

        try:
            tickets_new = await self.api.get_all_tickets()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("GetPagedTickets raised: %s", err)
            tickets_new = None
        if tickets_new is None:
            pass
        else:
            tickets = tickets_new
            last_ticket_id = (
                str(tickets[0].get("ticketId") or tickets[0].get("id") or "")
                if tickets
                else None
            )
            last_ticket_messages = []
            if last_ticket_id:
                try:
                    last_ticket_messages = await self.api.fetch_ticket_conversation(
                        last_ticket_id
                    )
                    tickets[0]["messages"] = last_ticket_messages
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Ticket conversation raised: %s", err)
                    last_ticket_messages = list(
                        previous.get("last_ticket_messages") or []
                    )
        _LOGGER.debug(
            "Account snapshot: %d faces, %d tickets, %d messages",
            len(faces),
            len(tickets),
            len(last_ticket_messages),
        )
        return {
            "faces": faces,
            "tickets": tickets,
            "last_ticket_id": last_ticket_id,
            "last_ticket_messages": last_ticket_messages,
            "faces_payload": faces_payload if isinstance(faces_payload, dict) else {},
            "tickets_payload": {},
        }
