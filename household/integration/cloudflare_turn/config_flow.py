"""Config flow: paste the TURN key ID and its API token."""

from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InvalidAuth, fetch_ice_servers
from .const import CONF_API_TOKEN, CONF_KEY_ID, DOMAIN

SCHEMA = vol.Schema({vol.Required(CONF_KEY_ID): str, vol.Required(CONF_API_TOKEN): str})


class CloudflareTurnConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {k: v.strip() for k, v in user_input.items()}
            await self.async_set_unique_id(user_input[CONF_KEY_ID])
            self._abort_if_unique_id_configured()
            try:
                await fetch_ice_servers(
                    async_get_clientsession(self.hass), user_input[CONF_KEY_ID], user_input[CONF_API_TOKEN]
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError, KeyError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Cloudflare TURN", data=user_input)

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {k: v.strip() for k, v in user_input.items()}
            try:
                await fetch_ice_servers(
                    async_get_clientsession(self.hass), user_input[CONF_KEY_ID], user_input[CONF_API_TOKEN]
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except (aiohttp.ClientError, TimeoutError, KeyError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data=user_input)

        return self.async_show_form(step_id="reauth_confirm", data_schema=SCHEMA, errors=errors)
