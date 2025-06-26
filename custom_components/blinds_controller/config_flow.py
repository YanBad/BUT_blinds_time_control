import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN


class BlindsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Blinds Controller."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return BlindsOptionsFlow(config_entry)

    @callback
    def _get_entity_ids(self, platform="switch"):
        """Return a sorted list of entity IDs for a given platform."""
        return sorted(self.hass.states.async_entity_ids(platform))

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            return self.async_create_entry(
                title=user_input["ent_name"],
                data=user_input,
            )

        all_switches = self._get_entity_ids("switch")
        if not all_switches:
            errors["base"] = "no_switches"
            
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("ent_name"): str,
                    vol.Required("entity_up"): vol.In(all_switches),
                    vol.Required("entity_down"): vol.In(all_switches),
                    vol.Required("time_up"): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required("time_down"): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("tilt_open", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("tilt_closed", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("startup_delay", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
                    vol.Optional("send_stop_at_end", default=True): bool,
                }
            ),
            errors=errors,
        )


class BlindsOptionsFlow(config_entries.OptionsFlow):
    """Handle an options flow for Blinds Controller."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        # --- THIS IS THE FIX ---
        # The super().__init__() call is now correct, and self.config_entry is assigned.
        super().__init__()
        self.config_entry = config_entry

    @callback
    def _get_entity_ids(self, platform="switch"):
        """Return a sorted list of entity IDs for a given platform."""
        return sorted(self.hass.states.async_entity_ids(platform))

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Update the existing entry with the new data
            return self.async_create_entry(title="", data=user_input)

        all_switches = self._get_entity_ids("switch")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("ent_name", default=self.config_entry.options.get("ent_name", self.config_entry.data.get("ent_name"))): str,
                    vol.Required("entity_up", default=self.config_entry.options.get("entity_up", self.config_entry.data.get("entity_up"))): vol.In(all_switches),
                    vol.Required("entity_down", default=self.config_entry.options.get("entity_down", self.config_entry.data.get("entity_down"))): vol.In(all_switches),
                    vol.Required("time_up", default=self.config_entry.options.get("time_up", self.config_entry.data.get("time_up"))): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Required("time_down", default=self.config_entry.options.get("time_down", self.config_entry.data.get("time_down"))): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("tilt_open", default=self.config_entry.options.get("tilt_open", self.config_entry.data.get("tilt_open", 0.0))): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("tilt_closed", default=self.config_entry.options.get("tilt_closed", self.config_entry.data.get("tilt_closed", 0.0))): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("startup_delay", default=self.config_entry.options.get("startup_delay", self.config_entry.data.get("startup_delay", 0.0))): vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
                    vol.Optional("send_stop_at_end", default=self.config_entry.options.get("send_stop_at_end", self.config_entry.data.get("send_stop_at_end", True))): bool,
                }
            ),
        )
