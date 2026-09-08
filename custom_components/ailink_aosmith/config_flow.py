"""Config flow for Ai-Link A.O. Smith."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COOKIE,
    CONF_ENABLE_RAW_SENSORS,
    CONF_FAMILY_ID,
    CONF_LANGUAGE,
    CONF_MOBILE,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
    DEFAULT_ENABLE_RAW_SENSORS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
LANGUAGE_OPTIONS = {
    "auto": "Auto (Home Assistant)",
    "en": "English",
    "zh-Hans": "简体中文",
}


class AOSmithConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup and native token replacement flows."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                devices = await self._get_devices(
                    user_input[CONF_ACCESS_TOKEN],
                    user_input[CONF_USER_ID],
                    user_input[CONF_FAMILY_ID],
                    user_input.get(CONF_COOKIE),
                    user_input.get(CONF_MOBILE),
                )
                if devices:
                    _LOGGER.info("Authentication succeeded")
                    return self.async_create_entry(
                        title=f"Ai-Link A.O. Smith - {len(devices)} devices",
                        data=user_input,
                    )
                errors["base"] = "no_devices"
            except Exception as err:
                errors["base"] = self._flow_error(err)
                _LOGGER.error("Credential validation failed")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): str,
                    vol.Required(CONF_USER_ID): str,
                    vol.Required(CONF_FAMILY_ID): str,
                    vol.Optional(CONF_COOKIE): str,
                    vol.Optional(CONF_MOBILE): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "access_token": "通常为: Bearer xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                "cookie": "通常为: cna=xxxxxxx",
            },
        )

    async def async_step_reauth(self, entry_data):
        """Start token-only reauthentication for the existing entry."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Accept a replacement token without exposing the old token."""
        entry = getattr(self, "_reauth_entry", None) or self._get_reauth_entry()
        return await self._async_step_token_replacement(user_input, entry, "reauth_confirm")

    async def async_step_reconfigure(self, user_input=None):
        """Reconfigure only the token while preserving entry identity and data."""
        entry = self._get_reconfigure_entry()
        return await self._async_step_token_replacement(user_input, entry, "reconfigure")

    async def _async_step_token_replacement(self, user_input, entry, step_id):
        """Validate a new token, then atomically replace only that token."""
        errors = {}
        if user_input is not None:
            token = user_input.get(CONF_ACCESS_TOKEN, "")
            if not isinstance(token, str) or not token.strip():
                errors["base"] = "auth_error"
            else:
                try:
                    devices = await self._get_devices(
                        token,
                        entry.data[CONF_USER_ID],
                        entry.data[CONF_FAMILY_ID],
                        entry.data.get(CONF_COOKIE),
                        entry.data.get(CONF_MOBILE),
                    )
                    if not devices:
                        errors["base"] = "no_devices"
                    else:
                        new_data = dict(entry.data)
                        new_data[CONF_ACCESS_TOKEN] = token
                        self.hass.config_entries.async_update_entry(
                            entry, data=new_data
                        )
                        return self.async_abort(reason="reauth_successful")
                except Exception as err:
                    errors["base"] = self._flow_error(err)
                    _LOGGER.error("Replacement token validation failed")

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required(CONF_ACCESS_TOKEN): str}),
            errors=errors,
        )

    @staticmethod
    def _flow_error(error: Exception) -> str:
        """Map typed API failures to one safe Home Assistant error key."""
        from .api import AOSmithAuthError, AOSmithNoDevicesError

        if isinstance(error, AOSmithAuthError):
            return "auth_error"
        if isinstance(error, AOSmithNoDevicesError):
            return "no_devices"
        return "cannot_connect"

    async def _get_devices(
        self,
        access_token: str,
        user_id: str,
        family_id: str,
        cookie: str | None = None,
        mobile: str | None = None,
    ) -> list:
        """Get devices and always close the validation client."""
        from .api import AOSmithAPI

        api = AOSmithAPI(access_token, user_id, family_id, cookie, mobile)
        try:
            await api.async_authenticate()
            return await api.async_get_devices()
        finally:
            await api.close()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AOSmithOptionsFlow(config_entry)


class AOSmithOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Ai-Link A.O. Smith."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._config_entry.options
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                vol.Optional(
                    CONF_LANGUAGE,
                    default=options.get(CONF_LANGUAGE, "auto"),
                ): vol.In(LANGUAGE_OPTIONS),
                vol.Optional(
                    CONF_ENABLE_RAW_SENSORS,
                    default=options.get(
                        CONF_ENABLE_RAW_SENSORS,
                        DEFAULT_ENABLE_RAW_SENSORS,
                    ),
                ): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
