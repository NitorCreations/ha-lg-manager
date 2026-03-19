"""Config flow for HA LG Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_FIREWALL_CLIENTS_PATH,
    CONF_INVENTORY_PATH,
    CONF_MERAKI_API_KEY,
    CONF_MERAKI_API_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_INVENTORY_PATH,
    DEFAULT_MERAKI_API_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)


def _options_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_INVENTORY_PATH,
                default=user_input.get(CONF_INVENTORY_PATH, DEFAULT_INVENTORY_PATH),
            ): str,
            vol.Optional(
                CONF_FIREWALL_CLIENTS_PATH,
                default=user_input.get(CONF_FIREWALL_CLIENTS_PATH, ""),
            ): str,
            vol.Optional(
                CONF_MERAKI_API_URL,
                default=user_input.get(CONF_MERAKI_API_URL, DEFAULT_MERAKI_API_URL),
            ): str,
            vol.Optional(
                CONF_MERAKI_API_KEY,
                default=user_input.get(CONF_MERAKI_API_KEY, ""),
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30)),
        }
    )


class LgTvManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="LG TV Manager",
                data={},
                options=user_input,
            )

        return self.async_show_form(step_id="user", data_schema=_options_schema())

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return LgTvManagerOptionsFlow(config_entry)


class LgTvManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
        )
