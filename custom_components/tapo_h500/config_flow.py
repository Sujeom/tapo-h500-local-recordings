"""Config flow for Tapo H500."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .api import H500Client
from .const import CONF_CLOUD_PASSWORD, DOMAIN


class TapoH500ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = H500Client(
                user_input[CONF_HOST], user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD], user_input[CONF_CLOUD_PASSWORD],
            )
            try:
                await self.hass.async_add_executor_job(client.connect)
                cameras = await self.hass.async_add_executor_job(client.cameras)
            except Exception:
                errors["base"] = "cannot_connect"
            finally:
                await self.hass.async_add_executor_job(client.close)
            if not errors:
                if not cameras:
                    errors["base"] = "no_cameras"
                else:
                    await self.async_set_unique_id(user_input[CONF_HOST])
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: user_input[CONF_HOST]})
                    return self.async_create_entry(
                        title=f"Tapo H500 ({user_input[CONF_HOST]})",
                        data=user_input,
                    )

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME, default="admin"): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_CLOUD_PASSWORD): str,
        })
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors)
