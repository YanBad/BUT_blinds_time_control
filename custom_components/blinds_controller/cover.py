import logging
from datetime import datetime, timedelta
import asyncio

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverEntityFeature,
    CoverEntity,
)
from homeassistant.const import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
)
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
import voluptuous as vol

from .calculator import TravelCalculator, TravelStatus
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_KNOWN_POSITION = "set_known_position"
SERVICE_SET_KNOWN_TILT_POSITION = "set_known_tilt_position"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the blinds cover from a config entry."""
    async_add_entities([BlindsCover(hass, entry)])

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_POSITION,
        {vol.Required("position"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))},
        "async_set_known_position",
    )
    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_TILT_POSITION,
        {vol.Required("position"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100))},
        "async_set_known_tilt_position",
    )


class BlindsCover(CoverEntity, RestoreEntity):
    """Representation of a blinds cover."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the cover."""
        self.hass = hass
        self.entry = entry

        # Listen for option updates
        entry.async_on_unload(entry.add_update_listener(self.async_options_updated))

        self._attr_name = self.entry.options.get("ent_name", self.entry.data.get("ent_name"))
        self._attr_unique_id = f"cover_timebased_synced_uuid_{entry.entry_id}"
        self._attr_should_poll = False
        self._attr_device_class = "blind"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self._attr_name,
            manufacturer="Blinds Controller (Custom)",
            model="Time-Based",
        )

        self._configure_entity()
        
        self._unsubscribe_auto_updater = None

        self.travel_calc = TravelCalculator(
            self._travel_time_down, self._travel_time_up, self._startup_delay
        )
        self.tilt_calc = None
        if self.has_tilt_support():
            self.tilt_calc = TravelCalculator(
                self._travel_tilt_closed, self._travel_tilt_open, self._startup_delay
            )

    def _configure_entity(self):
        """Read configuration from options or data."""
        self._travel_time_down = self.entry.options.get("time_down", self.entry.data.get("time_down", 30.0))
        self._travel_time_up = self.entry.options.get("time_up", self.entry.data.get("time_up", 30.0))
        self._travel_tilt_closed = self.entry.options.get("tilt_closed", self.entry.data.get("tilt_closed", 1.5))
        self._travel_tilt_open = self.entry.options.get("tilt_open", self.entry.data.get("tilt_open", 1.5))
        self._startup_delay = self.entry.options.get("startup_delay", self.entry.data.get("startup_delay", 0.0))
        self._up_switch_entity_id = self.entry.options.get("entity_up", self.entry.data.get("entity_up"))
        self._down_switch_entity_id = self.entry.options.get("entity_down", self.entry.data.get("entity_down"))
        self._send_stop_at_end = self.entry.options.get("send_stop_at_end", self.entry.data.get("send_stop_at_end", True))

    @staticmethod
    async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry):
        """Handle options update."""
        # This is a static method that triggers a reload of the integration.
        # The __init__ will be called again with the updated entry.
        await hass.config_entries.async_reload(entry.entry_id)
        
    @property
    def supported_features(self) -> CoverEntityFeature:
        """Flag supported features."""
        supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if self.has_tilt_support():
            supported_features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        return supported_features

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of cover."""
        return self.travel_calc.current_position()

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position of cover."""
        if self.has_tilt_support():
            return self.tilt_calc.current_position()
        return None

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening or not."""
        return self.travel_calc.travel_direction == TravelStatus.DIRECTION_UP

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing or not."""
        return self.travel_calc.travel_direction == TravelStatus.DIRECTION_DOWN

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed or not."""
        return self.travel_calc.is_closed()

    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        if self.travel_calc.current_position() < 100:
            self.travel_calc.start_travel_up()
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_OPEN_COVER)

    async def async_close_cover(self, **kwargs):
        """Close cover."""
        if self.travel_calc.current_position() > 0:
            self.travel_calc.start_travel_down()
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_CLOSE_COVER)
    
    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        self.travel_calc.stop()
        if self.has_tilt_support():
            self.tilt_calc.stop()
        self.stop_auto_updater()
        await self._async_handle_command(SERVICE_STOP_COVER)

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        position = kwargs[ATTR_POSITION]
        current_position = self.travel_calc.current_position()

        command = None
        if position > current_position:
            command = SERVICE_OPEN_COVER
        elif position < current_position:
            command = SERVICE_CLOSE_COVER

        if command:
            self.travel_calc.start_travel(position)
            self.start_auto_updater()
            await self._async_handle_command(command)

    def has_tilt_support(self) -> bool:
        """Check if tilt is supported."""
        return self._travel_tilt_open > 0 and self._travel_tilt_closed > 0

    async def async_open_cover_tilt(self, **kwargs):
        """Open the cover tilt."""
        if self.has_tilt_support() and self.tilt_calc.current_position() < 100:
            self.tilt_calc.start_travel_up()
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_OPEN_COVER)

    async def async_close_cover_tilt(self, **kwargs):
        """Close the cover tilt."""
        if self.has_tilt_support() and self.tilt_calc.current_position() > 0:
            self.tilt_calc.start_travel_down()
            self.start_auto_updater()
            await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_stop_cover_tilt(self, **kwargs):
        """Stop the cover tilt."""
        await self.async_stop_cover()

    async def async_set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        if not self.has_tilt_support():
            return
            
        position = kwargs[ATTR_TILT_POSITION]
        current_tilt_position = self.tilt_calc.current_position()

        command = None
        if position > current_tilt_position:
            command = SERVICE_OPEN_COVER
        elif position < current_tilt_position:
            command = SERVICE_CLOSE_COVER

        if command:
            self.tilt_calc.start_travel(position)
            self.start_auto_updater()
            await self._async_handle_command(command)

    def start_auto_updater(self):
        """Start the auto updater to update HASS state."""
        if self._unsubscribe_auto_updater is None:
            interval = timedelta(seconds=0.1)
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self.auto_updater_hook, interval
            )

    def stop_auto_updater(self):
        """Stop the auto updater."""
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    @callback
    def auto_updater_hook(self, now: datetime) -> None:
        """Call for the updater."""
        self.async_schedule_update_ha_state()

        position_reached = self.travel_calc.position_reached() and \
                           (not self.has_tilt_support() or self.tilt_calc.position_reached())

        if position_reached:
            self.stop_auto_updater()
            self.travel_calc.stop()
            if self.has_tilt_support():
                self.tilt_calc.stop()
            
            self.hass.async_create_task(self.auto_stop_if_necessary())
            
            self.async_write_ha_state()

    async def auto_stop_if_necessary(self):
        """Send stop command if required."""
        if self._send_stop_at_end:
            _LOGGER.debug("Auto-stopping cover %s as it reached its final position.", self.name)
            await self._async_handle_command(SERVICE_STOP_COVER)
            
    async def _async_handle_command(self, command: str) -> None:
        """Handle the cover commands."""
        if command == SERVICE_OPEN_COVER:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self._down_switch_entity_id}, blocking=True)
            await asyncio.sleep(0.1) # Interlock delay
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": self._up_switch_entity_id}, blocking=True)
        elif command == SERVICE_CLOSE_COVER:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self._up_switch_entity_id}, blocking=True)
            await asyncio.sleep(0.1) # Interlock delay
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": self._down_switch_entity_id}, blocking=True)
        elif command == SERVICE_STOP_COVER:
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self._up_switch_entity_id}, blocking=True)
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": self._down_switch_entity_id}, blocking=True)
            
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state and old_state.attributes.get(ATTR_CURRENT_POSITION) is not None:
            self.travel_calc.set_position(int(old_state.attributes.get(ATTR_CURRENT_POSITION)))
            if self.has_tilt_support() and old_state.attributes.get(ATTR_CURRENT_TILT_POSITION) is not None:
                self.tilt_calc.set_position(int(old_state.attributes.get(ATTR_CURRENT_TILT_POSITION)))

    async def async_set_known_position(self, position: int):
        """Service to set the known position of the cover."""
        self.travel_calc.set_position(position)
        self.async_write_ha_state()

    async def async_set_known_tilt_position(self, position: int):
        """Service to set the known tilt position of the cover."""
        if self.has_tilt_support():
            self.tilt_calc.set_position(position)
            self.async_write_ha_state()
