"""Config flow for Tapo H500."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import H500Client
from .const import (
    AUTO_DOWNLOAD_MODES, CONF_AUTO_DOWNLOAD, CONF_CLOUD_PASSWORD,
    CONF_CONVERT_MP4, CONF_POLL_INTERVAL, DEFAULT_AUTO_DOWNLOAD,
    DEFAULT_CONVERT_MP4, DEFAULT_POLL_INTERVAL, DOMAIN,
)


class TapoH500ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return TapoH500OptionsFlow()

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


class TapoH500OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_POLL_INTERVAL,
                default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=600)),
            vol.Required(
                CONF_AUTO_DOWNLOAD,
                default=options.get(CONF_AUTO_DOWNLOAD, DEFAULT_AUTO_DOWNLOAD),
            ): selector.SelectSelector(selector.SelectSelectorConfig(
                options=AUTO_DOWNLOAD_MODES,
                translation_key=CONF_AUTO_DOWNLOAD,
            )),
            vol.Required(
                CONF_CONVERT_MP4,
                default=options.get(CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
