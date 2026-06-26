from homeassistant import config_entries
import voluptuous as vol
import re
from secrets import token_urlsafe
from typing import Any, Optional

from .const import DOMAIN, CONF_COUNTRY_CODE, CONF_PHONE_NUMBER, CONF_CONFIRM_CODE, PARAM_REFRESH_EXPIRATION, \
    PARAM_REFRESH_TOKEN, PARAM_ACCESS_TOKEN, PARAM_WEBRTC_PROXY_SECRET, PARAM_DEVICE_TOKEN, PARAM_INSTANCE_ID
from .api import IntercomAPI, is_android_guid


class IntercomFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._country_code = None
        self._phone_number = None
        self._confirm_code = None
        self._api = IntercomAPI()
        self._reauth_entry = None

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._country_code = entry_data.get(CONF_COUNTRY_CODE)
        self._phone_number = entry_data.get(CONF_PHONE_NUMBER)
        stored_device_token = entry_data.get(PARAM_DEVICE_TOKEN)
        self._api = IntercomAPI(
            device_token=(
                stored_device_token if is_android_guid(stored_device_token) else None
            ),
            instance_id=entry_data.get(PARAM_INSTANCE_ID),
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not self._country_code or not self._phone_number:
                return await self.async_step_user()
            response = await self._send_authorization_code()
            if response is not True:
                errors["base"] = "authorization_failed"
            else:
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
        )

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._country_code = user_input[CONF_COUNTRY_CODE]
            self._phone_number = user_input[CONF_PHONE_NUMBER]

            response = await self._send_authorization_code()
            if response is not True:
                errors["base"] = "authorization_failed"
            else:
                return await self.async_step_confirm()

        data_schema = vol.Schema({
            vol.Required(CONF_COUNTRY_CODE): str,
            vol.Required(CONF_PHONE_NUMBER): str,
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._confirm_code = user_input[CONF_CONFIRM_CODE]

            response = await self._api.confirm_authorization(
                self._country_code, self._phone_number, self._confirm_code
            )
            if (
                not self._api.access_token
                or not self._api.refresh_token
                or (
                    isinstance(response, dict)
                    and ("errorText" in response or "error" in response)
                )
            ):
                errors["base"] = "confirmation_failed"
            else:
                data = self._entry_data()
                if self._reauth_entry is not None:
                    self.hass.config_entries.async_update_entry(
                        self._reauth_entry,
                        title="+" + self._country_code + " " + self._phone_number,
                        data=data,
                    )
                    await self.hass.config_entries.async_reload(
                        self._reauth_entry.entry_id
                    )
                    return self.async_abort(reason="reauth_successful")
                return self.async_create_entry(
                    title= "+" + self._country_code + " " + self._phone_number,
                    data=data,
                )

        data_schema = vol.Schema({
            vol.Required(CONF_CONFIRM_CODE): str,
        })

        return self.async_show_form(
            step_id="confirm", data_schema=data_schema, errors=errors
        )


    async def _send_authorization_code(self):
        return await self._api.authorize(self._country_code, self._phone_number)

    def _entry_data(self) -> dict[str, Optional[str]]:
        data = dict(self._reauth_entry.data) if self._reauth_entry is not None else {}
        data.setdefault(PARAM_WEBRTC_PROXY_SECRET, token_urlsafe(24))
        data.update(
            {
                PARAM_ACCESS_TOKEN: self._api.access_token,
                PARAM_REFRESH_TOKEN: self._api.refresh_token,
                PARAM_REFRESH_EXPIRATION: self._api.refresh_expiration_date,
                PARAM_DEVICE_TOKEN: self._api.device_token,
                PARAM_INSTANCE_ID: self._api.instance_id,
                CONF_COUNTRY_CODE: self._country_code,
                CONF_PHONE_NUMBER: self._phone_number,
            }
        )
        return data
