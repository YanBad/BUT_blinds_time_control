"""Cover entity for time-based blinds controller."""
import asyncio
from datetime import datetime, timedelta
import logging
import time

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
import voluptuous as vol

from .calculator import PositionType, TravelCalculator, TravelStatus
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
    """Representation of a time-based blinds cover."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize the cover."""
        self.hass = hass
        self.entry = entry

        self._attr_name = self.entry.options.get("ent_name", self.entry.data.get("ent_name"))
        self._attr_unique_id = f"cover_timebased_synced_uuid_{entry.entry_id}"
        self._attr_should_poll = False
        self._attr_device_class = CoverDeviceClass.BLIND

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self._attr_name,
            manufacturer="Blinds Controller (Custom)",
            model="Time-Based",
        )

        self._configure_entity()

        self._unsubscribe_auto_updater = None
        self._remove_listeners = None

        # Tracking for external switches and command echos
        self._switch_press_time: dict[str, float] = {}
        self._expected_switch_states: dict[str, tuple[str, float]] = {}
        self._travel_task: asyncio.Task | None = None

        # Travel calculator initialized with effective dead time (dead_time - stop_delay)
        self.travel_calc = TravelCalculator(
            self._travel_time_down, self._travel_time_up, self._effective_dead_time
        )
        self.tilt_calc = None
        if self.has_tilt_support():
            self.tilt_calc = TravelCalculator(
                self._travel_tilt_closed, self._travel_tilt_open, 0.0
            )

    def _configure_entity(self):
        """Read configuration from options or data."""
        data = self.entry.data
        options = self.entry.options

        def get_val(key, default):
            if key in options and options[key] is not None:
                return options[key]
            if key in data and data[key] is not None:
                return data[key]
            return default

        def get_float(key, default):
            val = get_val(key, default)
            try:
                return float(val)
            except (ValueError, TypeError):
                return float(default)

        self._travel_time_down = get_float("time_down", 30.0)
        self._travel_time_up = get_float("time_up", 30.0)
        # Start delay / dead time (Totzeit)
        self._dead_time = get_float("dead_time", get_float("startup_delay", 0.78))
        # Stop delay / coasting time (Nachlaufzeit)
        self._stop_delay = get_float("stop_delay", 0.15)
        # Net effective dead time to add to commanded movement (dead_time - stop_delay)
        self._effective_dead_time = max(0.0, self._dead_time - self._stop_delay)
        # Sperrzeit / Lockout threshold
        self._sperrzeit = get_float("sperrzeit", get_float("pulse_duration", 0.2))
        self._overtravel_time = get_float("overtravel_time", 1.0)
        self._interlock_delay = get_float("interlock_delay", 0.2)
        self._travel_tilt_closed = get_float("tilt_closed", 0.0)
        self._travel_tilt_open = get_float("tilt_open", 0.0)
        self._up_switch_entity_id = get_val("entity_up", None)
        self._down_switch_entity_id = get_val("entity_down", None)
        self._monitor_switches = bool(get_val("monitor_switches", False))
        self._send_stop_at_end = bool(get_val("send_stop_at_end", True))

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Flag supported features."""
        features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        if self.has_tilt_support():
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
        return features

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of cover (0 = fully closed, 100 = fully open)."""
        return self.travel_calc.current_position()

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return current tilt position of cover."""
        if self.has_tilt_support() and self.tilt_calc is not None:
            return self.tilt_calc.current_position()
        return None

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        if self.travel_calc.travel_direction == TravelStatus.DIRECTION_UP:
            return True
        if self.has_tilt_support() and self.tilt_calc and self.tilt_calc.travel_direction == TravelStatus.DIRECTION_UP:
            return True
        return False

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        if self.travel_calc.travel_direction == TravelStatus.DIRECTION_DOWN:
            return True
        if self.has_tilt_support() and self.tilt_calc and self.tilt_calc.travel_direction == TravelStatus.DIRECTION_DOWN:
            return True
        return False

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos == 0

    def has_tilt_support(self) -> bool:
        """Check if tilt is supported."""
        return self._travel_tilt_open > 0 and self._travel_tilt_closed > 0

    async def async_open_cover(self, **kwargs):
        """Open the cover fully."""
        if not self.is_closing and self.current_cover_position is not None and self.current_cover_position >= 100:
            return

        current_pos = self.travel_calc.current_position()
        move_time = self.travel_calc.travel_time_between(current_pos, 100, include_startup=True)
        await self._async_start_travel(SERVICE_OPEN_COVER, 100, move_time)

    async def async_close_cover(self, **kwargs):
        """Close the cover fully."""
        if not self.is_opening and self.current_cover_position is not None and self.current_cover_position <= 0:
            return

        current_pos = self.travel_calc.current_position()
        move_time = self.travel_calc.travel_time_between(current_pos, 0, include_startup=True)
        await self._async_start_travel(SERVICE_CLOSE_COVER, 0, move_time)

    async def async_stop_cover(self, **kwargs):
        """Stop the cover immediately."""
        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()

        self.travel_calc.stop()
        if self.has_tilt_support() and self.tilt_calc is not None:
            self.tilt_calc.stop()
        self.stop_auto_updater()
        await self._async_handle_command(SERVICE_STOP_COVER)
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        target_pos = kwargs[ATTR_POSITION]
        current_pos = self.travel_calc.current_position()

        if target_pos == current_pos:
            return

        move_time = self.travel_calc.travel_time_between(current_pos, target_pos, include_startup=True)

        # Sperrzeit check: for intermediate positions, ignore micro-pulses
        if target_pos not in (0, 100) and move_time <= self._sperrzeit:
            _LOGGER.warning(
                "Commanded move time %.2fs is <= sperrzeit (%.2fs). Ignored to prevent full-travel pulse.",
                move_time,
                self._sperrzeit,
            )
            return

        command = SERVICE_OPEN_COVER if target_pos > current_pos else SERVICE_CLOSE_COVER
        await self._async_start_travel(command, target_pos, move_time)

    async def async_open_cover_tilt(self, **kwargs):
        """Tilt open the blinds / ankippen for the exact configured tilt time."""
        if not self.has_tilt_support():
            return

        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()

        self._travel_task = self.hass.async_create_task(
            self._async_tilt_action(SERVICE_OPEN_COVER, self._travel_tilt_open, 100)
        )

    async def async_close_cover_tilt(self, **kwargs):
        """Tilt close the blinds for the exact configured tilt time."""
        if not self.has_tilt_support():
            return

        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()

        self._travel_task = self.hass.async_create_task(
            self._async_tilt_action(SERVICE_CLOSE_COVER, self._travel_tilt_closed, 0)
        )

    async def async_stop_cover_tilt(self, **kwargs):
        """Stop the cover tilt."""
        await self.async_stop_cover()

    async def async_set_cover_tilt_position(self, **kwargs):
        """Move the cover tilt to a specific position."""
        if not self.has_tilt_support():
            return

        target_pos = kwargs[ATTR_TILT_POSITION]
        current_pos = self.current_cover_tilt_position if self.current_cover_tilt_position is not None else 0

        if target_pos == current_pos:
            return

        diff_ratio = abs(target_pos - current_pos) / 100.0
        full_time = self._travel_tilt_open if target_pos > current_pos else self._travel_tilt_closed
        move_time = full_time * diff_ratio
        cmd = SERVICE_OPEN_COVER if target_pos > current_pos else SERVICE_CLOSE_COVER

        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()

        self._travel_task = self.hass.async_create_task(
            self._async_tilt_action(cmd, move_time, target_pos)
        )

    async def _async_start_travel(self, command: str, target_pos: int, move_time: float) -> None:
        """Start a timed travel to target_pos with exact millisecond precision."""
        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()

        is_end = target_pos in (0, 100)
        self._travel_task = self.hass.async_create_task(
            self._async_run_travel(command, target_pos, move_time, is_end)
        )

    async def _async_run_travel(
        self, command: str, target_pos: int, move_time: float, is_end_position: bool
    ) -> None:
        """Run travel for exact duration, then stop relays."""
        try:
            # 1. Turn ON the relay (with interlock if direction changes)
            await self._async_handle_command(command)

            # 2. Start travel calculator
            self.travel_calc.start_travel(target_pos)
            if self.has_tilt_support() and self.tilt_calc is not None:
                if target_pos == 100:
                    self.tilt_calc.set_position(100)
                elif target_pos == 0:
                    self.tilt_calc.set_position(0)

            # 3. Start UI interval updater (for smooth slider in HA)
            self.start_auto_updater()
            self.async_write_ha_state()

            # 4. Sleep for EXACT calculated travel time
            if move_time > 0:
                await asyncio.sleep(move_time)

            # 5. End position overtravel (to ensure physical limit switch is hit)
            if is_end_position and self._overtravel_time > 0:
                _LOGGER.debug(
                    "Overtravel active for cover %s (%.1fs extra run time).",
                    self.name,
                    self._overtravel_time,
                )
                await asyncio.sleep(self._overtravel_time)

            # 6. Turn OFF relay / send STOP
            if self._send_stop_at_end:
                _LOGGER.debug("Sending STOP command for cover %s at travel end.", self.name)
                await self._async_handle_command(SERVICE_STOP_COVER)

            # 7. Update calculator to confirmed final position
            self.stop_auto_updater()
            if is_end_position:
                self.travel_calc.set_position(target_pos)
                if self.has_tilt_support() and self.tilt_calc is not None:
                    self.tilt_calc.set_position(100 if target_pos == 100 else 0)
            else:
                self.travel_calc.stop()

        except asyncio.CancelledError:
            # Shield switch turn-off so cancel cannot leave relays energised!
            await asyncio.shield(self._async_handle_command(SERVICE_STOP_COVER))
            self.stop_auto_updater()
            self.travel_calc.stop()
            raise
        finally:
            self.stop_auto_updater()
            self.async_write_ha_state()

    async def _async_tilt_action(self, command: str, move_time: float, target_tilt_pos: int) -> None:
        """Execute a tilt movement for an exact duration without triggering main travel."""
        if move_time <= 0:
            return

        # Calculate exact relay hold duration: move_time + dead_time - stop_delay
        relay_duration = max(self._sperrzeit + 0.05, move_time + self._dead_time - self._stop_delay)

        try:
            if self.tilt_calc is not None:
                self.tilt_calc.travel_direction = (
                    TravelStatus.DIRECTION_UP if command == SERVICE_OPEN_COVER else TravelStatus.DIRECTION_DOWN
                )
            self.async_write_ha_state()

            _LOGGER.debug(
                "Tilting cover %s (%s) for %.2fs motor movement (relay ON for %.2fs).",
                self.name,
                command,
                move_time,
                relay_duration,
            )
            # 1. Turn ON switch
            await self._async_handle_command(command)
            # 2. Wait EXACT relay time (NO overtravel!)
            await asyncio.sleep(relay_duration)
            # 3. Turn OFF switch
            await self._async_handle_command(SERVICE_STOP_COVER)

            if self.tilt_calc is not None:
                self.tilt_calc.set_position(target_tilt_pos)
        except asyncio.CancelledError:
            await asyncio.shield(self._async_handle_command(SERVICE_STOP_COVER))
            raise
        finally:
            if self.tilt_calc is not None:
                self.tilt_calc.travel_direction = TravelStatus.STOPPED
            self.async_write_ha_state()

    async def _async_switch_turn_on(self, entity_id: str | None) -> None:
        """Turn on a switch entity and mark it as expected to suppress echo."""
        if not entity_id:
            return
        self._expected_switch_states[entity_id] = (STATE_ON, time.time())
        try:
            domain = entity_id.split(".")[0] if "." in entity_id else "switch"
            await self.hass.services.async_call(
                domain, "turn_on", {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:
            _LOGGER.error("Failed to turn on %s: %s", entity_id, err)

    async def _async_switch_turn_off(self, entity_id: str | None) -> None:
        """Turn off a switch entity and mark it as expected to suppress echo."""
        if not entity_id:
            return
        self._expected_switch_states[entity_id] = (STATE_OFF, time.time())
        try:
            domain = entity_id.split(".")[0] if "." in entity_id else "switch"
            await self.hass.services.async_call(
                domain, "turn_off", {"entity_id": entity_id}, blocking=True
            )
        except Exception as err:
            _LOGGER.error("Failed to turn off %s: %s", entity_id, err)

    async def _async_handle_command(self, command: str) -> None:
        """Execute the switch sequence for open, close, or stop with interlock."""
        if command == SERVICE_OPEN_COVER:
            down_state = self.hass.states.get(self._down_switch_entity_id) if self._down_switch_entity_id else None
            was_down_on = down_state and down_state.state == STATE_ON

            await self._async_switch_turn_off(self._down_switch_entity_id)
            if was_down_on and self._interlock_delay > 0:
                await asyncio.sleep(self._interlock_delay)
            await self._async_switch_turn_on(self._up_switch_entity_id)

        elif command == SERVICE_CLOSE_COVER:
            up_state = self.hass.states.get(self._up_switch_entity_id) if self._up_switch_entity_id else None
            was_up_on = up_state and up_state.state == STATE_ON

            await self._async_switch_turn_off(self._up_switch_entity_id)
            if was_up_on and self._interlock_delay > 0:
                await asyncio.sleep(self._interlock_delay)
            await self._async_switch_turn_on(self._down_switch_entity_id)

        elif command == SERVICE_STOP_COVER:
            await self._async_switch_turn_off(self._up_switch_entity_id)
            await self._async_switch_turn_off(self._down_switch_entity_id)

    def start_auto_updater(self):
        """Start the interval updater to refresh HASS state during motion."""
        if self._unsubscribe_auto_updater is None:
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self.auto_updater_hook, timedelta(seconds=0.5)
            )

    def stop_auto_updater(self):
        """Stop the interval updater."""
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    @callback
    def auto_updater_hook(self, now: datetime) -> None:
        """Periodic hook called every 0.5s while moving."""
        if self.travel_calc.position_reached():
            final_pos = self.travel_calc.current_position()
            if final_pos in (0, 100):
                self.travel_calc.set_position(final_pos)
                if self.has_tilt_support() and self.tilt_calc is not None:
                    self.tilt_calc.set_position(100 if final_pos == 100 else 0)
            else:
                self.travel_calc.stop()
            self.stop_auto_updater()

        self.async_write_ha_state()

    @callback
    def _async_switch_state_changed(self, event: Event) -> None:
        """Handle state changes of physical/monitored switch entities."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        if not new_state:
            return

        # 1. Echo suppression: Ignore state change if it was commanded by this entity
        expected = self._expected_switch_states.get(entity_id)
        if expected is not None:
            expected_state, timestamp = expected
            if new_state.state == expected_state and (time.time() - timestamp < 5.0):
                del self._expected_switch_states[entity_id]
                _LOGGER.debug("Suppressed command echo for %s: %s", entity_id, new_state.state)
                return

        # 2. Process genuine external switch interaction
        self.hass.async_create_task(
            self._handle_external_switch_change(entity_id, new_state.state)
        )

    async def _handle_external_switch_change(self, entity_id: str, new_state_str: str) -> None:
        """Handle an external switch press (short pulse vs long hold with dead time and stop delay)."""
        now = time.time()

        if new_state_str == STATE_ON:
            # If the cover is ALREADY traveling in either direction:
            # Tapping a physical switch while in motion acts as a STOP command!
            if self.is_opening or self.is_closing:
                _LOGGER.debug("Cover %s stopped by external switch tap while moving.", self.name)
                await self.async_stop_cover()
                return

            # Cover was stationary -> start traveling
            self._switch_press_time[entity_id] = now
            if entity_id == self._up_switch_entity_id:
                _LOGGER.debug("Cover %s starting UP from external switch.", self.name)
                self.travel_calc.start_travel_up()
                if self.has_tilt_support() and self.tilt_calc is not None:
                    self.tilt_calc.set_position(100)
                self.start_auto_updater()
                self.async_write_ha_state()

            elif entity_id == self._down_switch_entity_id:
                _LOGGER.debug("Cover %s starting DOWN from external switch.", self.name)
                self.travel_calc.start_travel_down()
                if self.has_tilt_support() and self.tilt_calc is not None:
                    self.tilt_calc.set_position(0)
                self.start_auto_updater()
                self.async_write_ha_state()

        elif new_state_str == STATE_OFF:
            press_start = self._switch_press_time.pop(entity_id, None)
            if press_start is not None:
                duration = now - press_start
                _LOGGER.debug(
                    "External switch %s released after %.2fs (sperrzeit: %.2fs, net_dead_time: %.2fs)",
                    entity_id,
                    duration,
                    self._sperrzeit,
                    self._effective_dead_time,
                )

                if duration <= self._sperrzeit:
                    # Short press (<= sperrzeit, e.g. 0.1s):
                    # In the hardware, this triggers full travel ("schliesst/öffnet komplett").
                    # We do NOT stop! Travel continues to the end position (0 or 100).
                    _LOGGER.debug(
                        "Short pulse (%.2fs <= %.2fs) -> Continuing full travel to end.",
                        duration,
                        self._sperrzeit,
                    )
                else:
                    # Long hold (> sperrzeit):
                    # Motor moved while held (factoring in dead_time and stop_delay via effective_dead_time).
                    _LOGGER.debug(
                        "Hold released (%.2fs > %.2fs) -> Stopping cover at calculated position.",
                        duration,
                        self._sperrzeit,
                    )
                    self.travel_calc.stop()
                    if self.has_tilt_support() and self.tilt_calc is not None:
                        self.tilt_calc.stop()
                    self.stop_auto_updater()
                    self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Call when entity is added to hass."""
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state:
            old_pos = old_state.attributes.get(ATTR_CURRENT_POSITION)
            if old_pos is not None:
                try:
                    self.travel_calc.set_position(int(round(float(old_pos))))
                except (ValueError, TypeError):
                    pass
            if self.has_tilt_support() and self.tilt_calc is not None:
                old_tilt = old_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
                if old_tilt is not None:
                    try:
                        self.tilt_calc.set_position(int(round(float(old_tilt))))
                    except (ValueError, TypeError):
                        pass

        # Only track external switch state changes if explicitly enabled
        if self._monitor_switches:
            monitored_entities = [
                e for e in [self._up_switch_entity_id, self._down_switch_entity_id] if e
            ]
            if monitored_entities:
                self._remove_listeners = async_track_state_change_event(
                    self.hass,
                    monitored_entities,
                    self._async_switch_state_changed,
                )

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        self.stop_auto_updater()
        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()
        if self._remove_listeners:
            self._remove_listeners()
            self._remove_listeners = None

    async def async_set_known_position(self, position: int):
        """Service to set the known position of the cover."""
        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()
        self.stop_auto_updater()
        self.travel_calc.set_position(position)
        self.async_write_ha_state()

    async def async_set_known_tilt_position(self, position: int):
        """Service to set the known tilt position of the cover."""
        if self._travel_task and not self._travel_task.done():
            self._travel_task.cancel()
        self.stop_auto_updater()
        if self.has_tilt_support() and self.tilt_calc is not None:
            self.tilt_calc.set_position(position)
        self.async_write_ha_state()
