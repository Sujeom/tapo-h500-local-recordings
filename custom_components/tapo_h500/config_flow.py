"""Config flow for Tapo H500."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import H500Client
from .media import clip_path, signed_url
from .const import (
    AUTO_DOWNLOAD_MODES, CONF_AUTO_DOWNLOAD, CONF_CLOUD_PASSWORD, CONF_FACE_NAMES,
    DATA_HUBS,
    CONF_CAMERA_ORDER, CONF_CONVERT_MP4, CONF_KEEP_DOWNLOADS, CONF_KEEP_RINGS,
    CONF_KEEP_PERSON, DEFAULT_KEEP_PERSON,
    CONF_POLL_INTERVAL, CONF_SILENT_HOURS,
    CONF_SENSITIVITY, DEFAULT_SENSITIVITY, SENSITIVITY_LEVELS,
    DEFAULT_AUTO_DOWNLOAD, DEFAULT_CONVERT_MP4, DEFAULT_KEEP_DOWNLOADS,
    DEFAULT_KEEP_RINGS,
    DEFAULT_POLL_INTERVAL, DEFAULT_SILENT_HOURS, DOMAIN, LOOKBACK_SECONDS,
)


# Defined once and used by both forms. A poll is about 40ms of hub time, so the
# 1s floor is far above anything the hardware needs; the ceiling only stops a
# typo turning the integration off for a day. Two copies of this drifted apart
# once already -- the floor sat above the default, so the default could not be
# saved -- so there is deliberately only one.
POLL_INTERVAL = vol.All(vol.Coerce(int), vol.Range(min=1, max=600))


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
                    # The interval goes to options, not data. Options is where
                    # the coordinator reads it and where the options flow later
                    # writes it; left in data it would be recorded, ignored,
                    # and silently replaced by the default.
                    interval = user_input.pop(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
                    return self.async_create_entry(
                        title=f"Tapo H500 ({user_input[CONF_HOST]})",
                        data=user_input,
                        options={CONF_POLL_INTERVAL: interval},
                    )

        # Keep whatever was typed when the form comes back with an error, so a
        # wrong password does not also cost the host and the interval.
        previous = user_input or {}
        schema = vol.Schema({
            vol.Required(CONF_HOST,
                         default=previous.get(CONF_HOST, vol.UNDEFINED)): str,
            vol.Required(CONF_USERNAME,
                         default=previous.get(CONF_USERNAME, "admin")): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_CLOUD_PASSWORD): str,
            vol.Required(
                CONF_POLL_INTERVAL,
                default=previous.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): POLL_INTERVAL,
        })
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors)


class TapoH500OptionsFlow(config_entries.OptionsFlow):
    """Four screens: how the integration behaves, who the faces are, where the
    cameras sit, and how busy each one has to get to be worth mentioning."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "faces", "layout", "sensitivity"])

    def _merged(self, user_input: dict) -> dict:
        """Options are replaced wholesale on save, so anything the current form
        does not ask about has to be carried across explicitly.

        Face names are the reason this exists: they live in options but appear
        on neither form in full, so saving the settings screen used to delete
        every one of them without a word.
        """
        return {**self.config_entry.options, **user_input}

    async def async_step_faces(self, user_input=None):
        """Name the faces the hub has clustered, without touching a card.

        The hub invents a stable id per person and refuses to say who they
        are. Naming them used to mean reading an id off a card and calling a
        service with it; here every face the hub has actually seen is listed
        with a box to type into, and clearing a box removes the name.
        """
        names = dict(self.config_entry.options.get(CONF_FACE_NAMES) or {})
        if user_input is not None:
            for face_id, name in user_input.items():
                cleaned = (name or "").strip()
                if cleaned:
                    names[str(face_id)] = cleaned
                else:
                    names.pop(str(face_id), None)
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_FACE_NAMES: names})

        coordinator = self.hass.data[DOMAIN][DATA_HUBS][self.config_entry.entry_id]
        seen = coordinator.faces_seen()
        # Everyone already named stays editable even if they have not been
        # seen today, or a name could only ever be added and never corrected.
        face_ids = sorted(set(seen) | set(names))
        if not face_ids:
            return self.async_abort(reason="no_faces")

        schema = vol.Schema({
            vol.Optional(face_id, default=names.get(face_id, "")): str
            for face_id in face_ids
        })
        # The form labels each box with the raw id, which alone says nothing.
        # A twelve-digit number cannot be matched to a person from memory, so
        # each line carries where and how often they were seen and, where the
        # clip has downloaded, a link to that sighting's own photograph.
        lines = await self.hass.async_add_executor_job(
            self._face_lines, face_ids, seen, coordinator)
        return self.async_show_form(
            step_id="faces", data_schema=schema,
            description_placeholders={"faces": "\n".join(lines)})

    def _face_lines(self, face_ids, seen, coordinator) -> list[str]:
        """One markdown line per face. Blocking: it checks files on disk."""
        lines = []
        for face_id in face_ids:
            face = seen.get(face_id) or {}
            photo = self._photo_url(face, coordinator)
            label = f"[**{face_id}** — see photo]({photo})" if photo \
                else f"**{face_id}**"
            if face.get("sightings"):
                where = ", ".join(face.get("cameras") or []) or "a camera"
                lines.append(f"- {label}: seen {face['sightings']}x recently "
                             f"on {where}")
            else:
                # Named before and quiet since. Kept so a name stays editable.
                lines.append(f"- {label}: not seen recently")
        return lines

    def _photo_url(self, face: dict, coordinator) -> str | None:
        """An absolute signed link to this face's newest sighting.

        None unless the clip has actually downloaded: the hub indexes a
        recording only once it has finished and the thumbnail is written by
        the download, so linking unconditionally would offer a dead link for
        anyone seen in the last minute.

        Absolute, not the root-relative path signed_url returns. A card puts
        that straight into an <img src> and the browser resolves it, but a
        markdown link is handled by the frontend's own router, which treats
        "/media/local/..." as an in-app route, finds no such page and goes
        nowhere. Giving the full origin also makes Home Assistant render it as
        an external link, which is what opens it in a new tab instead of
        replacing the settings page.
        """
        index, moment = face.get("camera_index"), face.get("last_seen")
        if index is None or moment is None or index >= len(coordinator.cameras):
            return None
        try:
            path = clip_path(self.hass, coordinator.cameras[index], moment, ".jpg")
            if not path.is_file():
                return None
            signed = signed_url(self.hass, path)
        except Exception:  # noqa: BLE001 - a missing photo is not an error
            return None
        try:
            # Whichever address this installation is actually reachable on.
            return f"{get_url(self.hass).rstrip('/')}{signed}"
        except NoURLAvailableError:
            # No configured URL at all. The relative form is still correct for
            # anything that resolves it against the origin, so offer it rather
            # than nothing.
            return signed

    async def async_step_layout(self, user_input=None):
        """Say where each camera sits between the street and the door.

        The hub reports no geometry, and the order cameras appear in the paired
        list is the order they were added, which means nothing. Without a
        layout a trail is a list of places; with it, the same trail says
        whether someone is walking towards the door or away from it.

        The form arrives with an answer already filled in wherever one can be
        worked out from the recordings: people arrive from the street and walk
        towards the door, so the camera that sees them first is the one nearer
        the street. That is a suggestion and stays one -- it fills in the
        defaults, and nothing is stored until this form is submitted. A
        guessed direction is worse than none, because "someone is approaching
        the door" is what people wire a siren to.
        """
        ranks = dict(self.config_entry.options.get(CONF_CAMERA_ORDER) or {})
        if user_input is not None:
            keep = {name: int(value) for name, value in user_input.items()
                    if value is not None}
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_CAMERA_ORDER: keep})

        coordinator = self.hass.data[DOMAIN][DATA_HUBS][self.config_entry.entry_id]
        names = [camera.get("alias") for camera in coordinator.cameras
                 if camera.get("alias")]
        if len(names) < 2:
            # Direction needs two places to be between.
            return self.async_abort(reason="one_camera")

        # Anything already saved wins. Overwriting a deliberate answer with an
        # inferred one on every visit to this screen would silently undo it.
        suggested = coordinator.suggested_ranks()
        schema = vol.Schema({
            vol.Optional(name,
                         default=ranks.get(name, suggested.get(name, 0))):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=20))
            for name in names
        })
        return self.async_show_form(
            step_id="layout", data_schema=schema,
            description_placeholders={
                "suggestion": self._layout_note(names, ranks, suggested)})

    @staticmethod
    def _layout_note(names, ranks, suggested) -> str:
        """One line saying whether these numbers were inferred or are yours."""
        inferred = [name for name in names
                    if name not in ranks and name in suggested]
        if not inferred:
            return ""
        order = " → ".join(sorted(inferred, key=lambda name: suggested[name]))
        return (f"\n\nSuggested from how people have actually moved between "
                f"them: **{order}**, street first. Change anything that looks "
                f"wrong — nothing is stored until you submit.")

    async def async_step_sensitivity(self, user_input=None):
        """How busy each camera has to be before it counts as unusual.

        Three levels rather than the two numbers behind them. A multiplier
        against the camera's own hourly average, and a floor below which
        nothing is flagged, are the right model for the code and the wrong
        question to ask a person: nobody knows what multiple of its own
        average their front door reaches on a Saturday.

        Per camera because the same numbers cannot fit two. Three times
        typical is a busy afternoon on a doorbell facing a pavement and
        somebody in the garden on a back gate.
        """
        levels = dict(self.config_entry.options.get(CONF_SENSITIVITY) or {})
        if user_input is not None:
            levels.update({str(name): str(value)
                           for name, value in user_input.items()})
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_SENSITIVITY: levels})

        coordinator = self.hass.data[DOMAIN][DATA_HUBS][self.config_entry.entry_id]
        names = [camera.get("alias") for camera in coordinator.cameras
                 if camera.get("alias")]
        if not names:
            return self.async_abort(reason="no_cameras")

        schema = vol.Schema({
            vol.Required(name, default=levels.get(name, DEFAULT_SENSITIVITY)):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=list(SENSITIVITY_LEVELS),
                    translation_key=CONF_SENSITIVITY,
                ))
            for name in names
        })
        return self.async_show_form(step_id="sensitivity", data_schema=schema)

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=self._merged(user_input))
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_POLL_INTERVAL,
                default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            ): POLL_INTERVAL,
            vol.Required(
                CONF_AUTO_DOWNLOAD,
                default=options.get(CONF_AUTO_DOWNLOAD, DEFAULT_AUTO_DOWNLOAD),
            ): selector.SelectSelector(selector.SelectSelectorConfig(
                options=AUTO_DOWNLOAD_MODES,
                translation_key=CONF_AUTO_DOWNLOAD,
            )),
            vol.Required(
                CONF_KEEP_DOWNLOADS,
                default=options.get(CONF_KEEP_DOWNLOADS, DEFAULT_KEEP_DOWNLOADS),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
            vol.Required(
                CONF_KEEP_RINGS,
                default=options.get(CONF_KEEP_RINGS, DEFAULT_KEEP_RINGS),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
            vol.Required(
                CONF_KEEP_PERSON,
                default=options.get(CONF_KEEP_PERSON, DEFAULT_KEEP_PERSON),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=500)),
            vol.Required(
                CONF_CONVERT_MP4,
                default=options.get(CONF_CONVERT_MP4, DEFAULT_CONVERT_MP4),
            ): bool,
            # Capped at the poll window: the hub is asked for a day of
            # recordings, so "nothing in three days" is not a question it can
            # answer. Offering 72 here would produce a sensor that never
            # turned on, for a reason nobody could see.
            vol.Required(
                CONF_SILENT_HOURS,
                default=options.get(CONF_SILENT_HOURS, DEFAULT_SILENT_HOURS),
            ): vol.All(vol.Coerce(int),
                       vol.Range(min=1, max=LOOKBACK_SECONDS // 3600)),
        })
        return self.async_show_form(step_id="settings", data_schema=schema)
