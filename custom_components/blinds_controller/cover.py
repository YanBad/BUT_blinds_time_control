# TODO add-ons weather date of the time or sunset and sundown automations
# TODO clean up code

# Import necessary modules from Home Assistant
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
    STATE_ON,  # <-- Add this
    STATE_OFF, # <-- Add this
)
from homeassistant.helpers import entity_platform
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.event import async_track_state_change

# Import the logger and datetime modules
import logging
from datetime import datetime, timedelta, timezone
import asyncio
import urllib.request
import json


# Import the TravelCalculator and TravelStatus classes from the calculator module
# Currently using the:
# https://github.com/XKNX/xknx/blob/0.9.4/xknx/devices/travelcalculator.py
from .calculator import TravelCalculator
from .calculator import TravelStatus

# Import the domain constant from the current package
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_KNOWN_POSITION = "set_known_position"
SERVICE_SET_KNOWN_TILT_POSITION = "set_known_tilt_position"

# This function takes the Home Assistant instance, the configuration data,
# function to add entities, and optional discovery information.
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    platform = entity_platform.current_platform.get()

    # Register a service for setting the known position of the cover.
    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_POSITION, "set_known_position"
    )
    platform.async_register_entity_service(
        SERVICE_SET_KNOWN_TILT_POSITION, "set_known_tilt_position"
    )

# This function is called by Home Assistant to setup the component
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    name = entry.title 
    device_id = entry.entry_id 
    async_add_entities([BlindsCover(hass, entry, name, device_id)])


class BlindsCover(CoverEntity, RestoreEntity):
    """Representation of a blinds cover entity."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, name: str, device_id: str):
        """Initialize the cover."""
        self.hass = hass
        self.entry = entry
        self._unique_id = device_id
        self._name = name or device_id
        self._available = True

        # --- CORRECTED INITIALIZATION ---
        # Use .get() for all optional values to prevent KeyErrors on startup.

        # Core configuration (required)
        self._travel_time_down = entry.data["time_down"]
        self._travel_time_up = entry.data["time_up"]
        self._up_switch_entity_id = entry.data["entity_up"]
        self._down_switch_entity_id = entry.data["entity_down"]
        
        # Core configuration (optional)
        self._travel_tilt_closed = entry.data.get("tilt_closed", 0)
        self._travel_tilt_open = entry.data.get("tilt_open", 0)
        self._send_stop_at_end = entry.data.get("send_stop_at_end", False)

        # Add-ons configuration (optional)
        self._timed_control_down = entry.data.get("timed_control_down", False)
        self._time_to_roll_down = entry.data.get("time_to_roll_down")
        self._timed_control_up = entry.data.get("timed_control_up", False)
        self._time_to_roll_up = entry.data.get("time_to_roll_up")
        self._delay_control = entry.data.get("delay_control", False)
        self._delay_sunrise = entry.data.get("delay_sunrise", 0)
        self._delay_sunset = entry.data.get("delay_sunset", 0)
        self._night_lights = entry.data.get("night_lights", False)
        self._entity_night_lights = entry.data.get("entity_night_lights")
        self._tilting_day = entry.data.get("tilting_day", False)
        
        # Weather protection configuration (optional)
        self._protect_the_blinds = entry.data.get("protect_the_blinds", False)
        self._set_wind_speed = entry.data.get("wind_speed", 100)
        self._wmo_code = entry.data.get("wmo_code", 100)

        # Netatmo configuration (optional)
        self._netamo_enable = entry.data.get("netamo_enable", False)
        self._netamo_speed_entity = entry.data.get("netamo_speed_entity")
        self._netamo_speed = entry.data.get("netamo_speed", 100)
        self._netamo_gust_entity = entry.data.get("netamo_gust_entity")
        self._netamo_gust = entry.data.get("netamo_gust", 100)
        # This is the key that was causing the error. Now it's handled safely.
        self._netamo_rain_entity = entry.data.get("netamo_rain_entity") 
        
        # Internal state
        self._target_position = 0
        self._target_tilt_position = 0
        self._weather_check_counter = 0
        self._tilt_check_counter = 0

        self._sun_next_sunrise = None
        self._sun_next_sunset = None
        self._wind_speed = None
        self._gust_speed = None
        
        self._switch_close_state = STATE_OFF
        self._switch_open_state = STATE_OFF
        self._night_lights_state = STATE_OFF
        
        self._unsubscribe_auto_updater = None

        # Setup calculators
        self.travel_calc = TravelCalculator(self._travel_time_down, self._travel_time_up)
        self.tilt_calc = None
        if self.has_tilt_support():
            self.tilt_calc = TravelCalculator(self._travel_tilt_closed, self._travel_tilt_open)

    # (The rest of the class remains the same as in the previous response)
    # ... (Properties: name, unique_id, device_class, etc.) ...
    
    #<editor-fold desc="-- Properties --">
    @property
    def name(self):
        """Return the name of the cover."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of the entity."""
        return f"cover_timebased_synced_uuid_{self._unique_id}"

    @property
    def device_class(self):
        """Return the device class of the cover."""
        return None

    @property
    def extra_state_attributes(self):
        """Return the device state attributes."""
        attrs = {
            "entity_up": self._up_switch_entity_id,
            "entity_down": self._down_switch_entity_id,
            "time_up": self._travel_time_up,
            "time_down": self._travel_time_down,
        }
        if self.has_tilt_support():
            attrs.update({
                "tilt_open": self._travel_tilt_open,
                "tilt_closed": self._travel_tilt_closed,
            })
        return attrs
    
    @property
    def supported_features(self) -> CoverEntityFeature:
        """Flag supported features."""
        supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if self.current_cover_position is not None:
            supported_features |= CoverEntityFeature.SET_POSITION

        if self.has_tilt_support():
            supported_features |= (
                CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT |
                CoverEntityFeature.STOP_TILT | CoverEntityFeature.SET_TILT_POSITION
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
    def is_closed(self):
        """Return if the cover is closed."""
        return self.travel_calc.is_closed()

    @property
    def is_opening(self):
        """Return if the cover is opening."""
        return (self.travel_calc.is_traveling() and self.travel_calc.travel_direction == TravelStatus.DIRECTION_UP) or \
               (self.has_tilt_support() and self.tilt_calc.is_traveling() and self.tilt_calc.travel_direction == TravelStatus.DIRECTION_UP)

    @property
    def is_closing(self):
        """Return if the cover is closing."""
        return (self.travel_calc.is_traveling() and self.travel_calc.travel_direction == TravelStatus.DIRECTION_DOWN) or \
               (self.has_tilt_support() and self.tilt_calc.is_traveling() and self.tilt_calc.travel_direction == TravelStatus.DIRECTION_DOWN)

    @property
    def available(self):
        """Return if the entity is available."""
        return self._available
    #</editor-fold>

    # ... (Core Cover Methods: async_open_cover, async_close_cover, etc.) ...
    
    #<editor-fold desc="-- Core Methods --">
    async def async_open_cover(self, **kwargs):
        """Open the cover."""
        if self.travel_calc.current_position() < 100:
            self.travel_calc.start_travel_up()
            self._start_auto_updater()
            self._update_tilt_before_travel(SERVICE_OPEN_COVER)
            await self._async_handle_command(SERVICE_OPEN_COVER)

    async def async_close_cover(self, **kwargs):
        """Close the cover."""
        if self.travel_calc.current_position() > 0:
            self.travel_calc.start_travel_down()
            self._start_auto_updater()
            self._update_tilt_before_travel(SERVICE_CLOSE_COVER)
            await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover."""
        self._stop_travel()
        await self._async_handle_command(SERVICE_STOP_COVER)

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = kwargs[ATTR_POSITION]
            current_position = self.travel_calc.current_position()
            command = None
            if position < current_position:
                command = SERVICE_CLOSE_COVER
            elif position > current_position:
                command = SERVICE_OPEN_COVER
            
            if command:
                self._start_auto_updater()
                self.travel_calc.start_travel(position)
                self._update_tilt_before_travel(command)
                await self._async_handle_command(command)

    async def async_open_cover_tilt(self, **kwargs):
        """Open the cover tilt."""
        if self.has_tilt_support() and self.tilt_calc.current_position() < 100:
            self.tilt_calc.start_travel_up()
            self._start_auto_updater()
            await self._async_handle_command(SERVICE_OPEN_COVER)

    async def async_close_cover_tilt(self, **kwargs):
        """Close the cover tilt."""
        if self.has_tilt_support() and self.tilt_calc.current_position() > 0:
            self.tilt_calc.start_travel_down()
            self._start_auto_updater()
            await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        if self.has_tilt_support() and ATTR_TILT_POSITION in kwargs:
            position = kwargs[ATTR_TILT_POSITION]
            current_position = self.tilt_calc.current_position()
            command = None
            if position < current_position:
                command = SERVICE_CLOSE_COVER
            elif position > current_position:
                command = SERVICE_OPEN_COVER

            if command:
                self._start_auto_updater()
                self.tilt_calc.start_travel(position)
                await self._async_handle_command(command)

    async def async_set_known_position(self, position: int):
        """Set a known position of the cover."""
        self.travel_calc.set_position(position)
        self.async_write_ha_state()

    async def async_set_known_tilt_position(self, position: int):
        """Set a known tilt position of the cover."""
        if self.has_tilt_support():
            self.tilt_calc.set_position(position)
            self.async_write_ha_state()
    #</editor-fold>

    # ... (Automation and State Handling) ...
    
    #<editor-fold desc="-- Automation & State --">
    async def async_added_to_hass(self):
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()

        # Restore last state
        old_state = await self.async_get_last_state()
        if old_state:
            if ATTR_CURRENT_POSITION in old_state.attributes:
                self.travel_calc.set_position(old_state.attributes[ATTR_CURRENT_POSITION])
            if self.has_tilt_support() and ATTR_CURRENT_TILT_POSITION in old_state.attributes:
                self.tilt_calc.set_position(old_state.attributes[ATTR_CURRENT_TILT_POSITION])

        # Listen for state changes of external entities
        self.async_on_remove(
            self.hass.bus.async_listen("state_changed", self._handle_state_changed)
        )
        
        # Listen for sun state changes
        self.async_on_remove(
            async_track_state_change(self.hass, "sun.sun", self._update_sun_state)
        )
        await self._update_sun_state() # Get initial sun state

        # Listen for Netatmo sensor changes
        if self._netamo_enable:
            if self._netamo_speed_entity:
                self.async_on_remove(async_track_state_change(self.hass, self._netamo_speed_entity, self._update_netatmo_state))
            if self._netamo_gust_entity:
                self.async_on_remove(async_track_state_change(self.hass, self._netamo_gust_entity, self._update_netatmo_state))
            await self._update_netatmo_state() # Get initial Netatmo state
        
        # Set up the main automation loop (runs every minute)
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_run_automations, timedelta(minutes=1))
        )

    async def _handle_state_changed(self, event):
        """Handle state changes of linked switches."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")

        if not new_state or not old_state or new_state.state == old_state.state:
            return

        # Update internal state for switches
        if entity_id == self._down_switch_entity_id:
            self._switch_close_state = new_state.state
        elif entity_id == self._up_switch_entity_id:
            self._switch_open_state = new_state.state
        elif entity_id == self._entity_night_lights:
            self._night_lights_state = new_state.state
        else:
            return

        # Logic to handle physical button presses
        is_opening = self._switch_open_state == STATE_ON
        is_closing = self._switch_close_state == STATE_ON

        # Stop if both buttons are pressed or released simultaneously
        if (is_opening and is_closing) or (not is_opening and not is_closing):
            self._stop_travel()
            # If one button was pressed to override the other, turn the other off
            if is_opening and is_closing:
                if old_state.state == STATE_OFF and entity_id == self._down_switch_entity_id:
                     await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._up_switch_entity_id}, False)
                elif old_state.state == STATE_OFF and entity_id == self._up_switch_entity_id:
                     await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._down_switch_entity_id}, False)

        elif is_opening: # Opening command
            self._target_position = 100
            self.travel_calc.start_travel_up()
            self._update_tilt_before_travel(SERVICE_OPEN_COVER)
            self._start_auto_updater()
        
        elif is_closing: # Closing command
            self._target_position = 0
            self.travel_calc.start_travel_down()
            self._update_tilt_before_travel(SERVICE_CLOSE_COVER)
            self._start_auto_updater()
        
        self.async_write_ha_state()
    #</editor-fold>

    # ... (Automation Logic) ...

    #<editor-fold desc="-- Automation Logic --">
    async def _async_run_automations(self, now=None):
        """Run all automation checks."""
        if self.travel_calc.is_traveling():
            return # Don't run automations while the cover is moving manually

        # Each method returns True if it performed an action
        if await self._handle_timed_control(): return
        if await self._handle_sun_control(): return
        if await self._handle_night_lights(now): return
        if await self._handle_day_tilting(now): return
        if await self._handle_weather_protection(): return
    
    async def _handle_timed_control(self) -> bool:
        """Handle automations based on fixed time."""
        now = dt_util.now()
        
        try:
            if self._timed_control_up and self.current_cover_position < 100:
                time_up = dt_util.parse_time(self._time_to_roll_up)
                if now.hour == time_up.hour and now.minute == time_up.minute:
                    _LOGGER.info("Timed control: Opening cover")
                    await self.async_open_cover()
                    return True

            if self._timed_control_down and self.current_cover_position > 0:
                time_down = dt_util.parse_time(self._time_to_roll_down)
                if now.hour == time_down.hour and now.minute == time_down.minute:
                    _LOGGER.info("Timed control: Closing cover")
                    await self.async_close_cover()
                    return True
        except (ValueError, TypeError):
            _LOGGER.error("Invalid time format for timed control. Please use HH:MM.")
        
        return False

    async def _handle_sun_control(self) -> bool:
        """Handle automations based on sunrise/sunset."""
        if not self._delay_control or not self._sun_next_sunrise or not self._sun_next_sunset:
            return False

        now = dt_util.now()
        sunrise_time = self._sun_next_sunrise + timedelta(minutes=self._delay_sunrise)
        sunset_time = self._sun_next_sunset + timedelta(minutes=self._delay_sunset)

        if now.hour == sunrise_time.hour and now.minute == sunrise_time.minute and self.current_cover_position < 100:
            _LOGGER.info("Sun control: Opening cover based on sunrise + delay")
            await self.async_open_cover()
            return True
        
        if now.hour == sunset_time.hour and now.minute == sunset_time.minute and self.current_cover_position > 0:
            _LOGGER.info("Sun control: Closing cover based on sunset + delay")
            await self.async_close_cover()
            return True
        
        return False

    async def _handle_night_lights(self, now: datetime) -> bool:
        """Close the cover at night if a light is turned on."""
        if not self._night_lights or self._night_lights_state != STATE_ON:
            return False
        
        if self._is_night(now) and self.current_cover_position > 0:
            _LOGGER.info("Night lights: Closing cover")
            await self.async_close_cover()
            return True
        return False

    async def _handle_day_tilting(self, now: datetime) -> bool:
        """Open the tilt during the day."""
        if not self.has_tilt_support() or not self._tilting_day:
            return False
            
        self._tilt_check_counter += 1
        if self._tilt_check_counter >= 10: # Check every 10 minutes
            self._tilt_check_counter = 0
            if not self._is_night(now) and not self.tilt_calc.is_traveling() and self.current_cover_tilt_position < 100:
                _LOGGER.info("Day tilting: Opening tilt")
                await self.async_open_cover_tilt()
                return True
        return False

    async def _handle_weather_protection(self) -> bool:
        """Handle weather-based protection automations."""
        # Netatmo check (real-time)
        if self._netamo_enable and self.current_cover_position < 100:
            try:
                if self._wind_speed is not None and float(self._wind_speed) > float(self._netamo_speed):
                    _LOGGER.warning("Netatmo protection: Wind speed high (%s). Opening cover.", self._wind_speed)
                    await self.async_open_cover()
                    return True
                if self._gust_speed is not None and float(self._gust_speed) > float(self._netamo_gust):
                    _LOGGER.warning("Netatmo protection: Gust speed high (%s). Opening cover.", self._gust_speed)
                    await self.async_open_cover()
                    return True
            except (ValueError, TypeError) as e:
                _LOGGER.debug("Could not compare Netatmo speeds: %s", e)

        # Open-Meteo check (periodic)
        if self._protect_the_blinds:
            self._weather_check_counter += 1
            if self._weather_check_counter >= 30: # Check every 30 minutes
                self._weather_check_counter = 0
                latitude = self.hass.config.latitude
                longitude = self.hass.config.longitude
                api_url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=wind_speed_10m&daily=weather_code"
                
                try:
                    response = await self.hass.async_add_executor_job(urllib.request.urlopen, api_url)
                    data = json.loads(response.read().decode('utf-8'))
                    _LOGGER.debug("Retrieved weather data: %s", data)
                    
                    wind_speed = data.get('current', {}).get('wind_speed_10m')
                    if wind_speed is not None and wind_speed > self._set_wind_speed and self.current_cover_position < 100:
                        _LOGGER.warning("Weather protection: Wind speed too high (%s). Opening cover.", wind_speed)
                        await self.async_open_cover()
                        return True

                    today_weather_code = data.get('daily', {}).get('weather_code', [0])[0]
                    if today_weather_code > self._wmo_code and self.current_cover_position < 100:
                        _LOGGER.warning("Weather protection: Bad weather code (%s). Opening cover.", today_weather_code)
                        await self.async_open_cover()
                        return True
                        
                except Exception as e:
                    _LOGGER.error("Error retrieving weather data: %s", e)
        return False
    #</editor-fold>

    # ... (Helper and Internal Methods) ...

    #<editor-fold desc="-- Helpers & Internals --">
    def has_tilt_support(self):
        """Return if the cover supports tilt."""
        return self._travel_tilt_open > 0 and self._travel_tilt_closed > 0

    def _position_reached(self):
        """Return if the cover has reached its target position."""
        return self.travel_calc.position_reached() and \
               (not self.has_tilt_support() or self.tilt_calc.position_reached())

    def _stop_travel(self):
        """Stop the cover and the auto updater."""
        if self.travel_calc.is_traveling() or (self.has_tilt_support() and self.tilt_calc.is_traveling()):
            self.travel_calc.stop()
            if self.has_tilt_support():
                self.tilt_calc.stop()
            self._stop_auto_updater()

    def _start_auto_updater(self):
        """Start the autoupdater to update HASS while cover is traveling."""
        if self._unsubscribe_auto_updater is None:
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self._auto_updater_hook, timedelta(seconds=0.1)
            )

    def _stop_auto_updater(self):
        """Stop the autoupdater."""
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None
        self._target_position = 0
        self._target_tilt_position = 0

    @callback
    def _auto_updater_hook(self, now):
        """Call for the autoupdater."""
        self.async_schedule_update_ha_state()
        if self._position_reached():
            self._stop_auto_updater()
        self.hass.async_create_task(self._auto_stop_if_necessary())

    async def _auto_stop_if_necessary(self):
        """Stop the cover motor if the target position is reached."""
        if self._position_reached():
            self._stop_travel()
            
            is_at_end = self.current_cover_position in [0, 100]
            if not is_at_end or (is_at_end and self._send_stop_at_end):
                 await self._async_handle_command(SERVICE_STOP_COVER)
    
    def _update_tilt_before_travel(self, command):
        """Set tilt to fully open or closed before travel."""
        if self.has_tilt_support():
            if command == SERVICE_OPEN_COVER:
                self.tilt_calc.start_travel_up()
            elif command == SERVICE_CLOSE_COVER:
                self.tilt_calc.start_travel_down()

    async def _async_handle_command(self, command: str):
        """Execute a cover command by controlling the switches."""
        if command == SERVICE_CLOSE_COVER:
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._up_switch_entity_id}, False)
            await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": self._down_switch_entity_id}, False)
        elif command == SERVICE_OPEN_COVER:
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._down_switch_entity_id}, False)
            await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": self._up_switch_entity_id}, False)
        elif command == SERVICE_STOP_COVER:
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._up_switch_entity_id}, False)
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": self._down_switch_entity_id}, False)
        
        self.async_write_ha_state()

    async def _update_sun_state(self, entity_id=None, old_state=None, new_state=None):
        """Update sunrise and sunset times from sun.sun entity."""
        sun_state = self.hass.states.get("sun.sun")
        if sun_state and sun_state.attributes:
            self._sun_next_sunrise = dt_util.parse_datetime(sun_state.attributes.get("next_rising"))
            self._sun_next_sunset = dt_util.parse_datetime(sun_state.attributes.get("next_setting"))

    async def _update_netatmo_state(self, entity_id=None, old_state=None, new_state=None):
        """Update Netatmo sensor values."""
        if self._netamo_speed_entity:
            speed_state = self.hass.states.get(self._netamo_speed_entity)
            if speed_state: self._wind_speed = speed_state.state
        if self._netamo_gust_entity:
            gust_state = self.hass.states.get(self._netamo_gust_entity)
            if gust_state: self._gust_speed = gust_state.state
    
    def _is_night(self, now: datetime) -> bool:
        """Determine if the current time is between sunset and sunrise."""
        if not self._sun_next_sunrise or not self._sun_next_sunset:
            return False
        
        # This logic correctly handles the time period crossing midnight
        if self._sun_next_sunset < self._sun_next_sunrise:
            # Night period crosses midnight (e.g., sunset 20:00, sunrise 06:00)
            return now >= self._sun_next_sunset or now < self._sun_next_sunrise
        else:
            # Day period crosses midnight (e.g., polar regions)
            return self._sun_next_sunset <= now < self._sun_next_sunrise
    #</editor-fold>
