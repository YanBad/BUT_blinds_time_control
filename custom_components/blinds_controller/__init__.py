# Import necessary modules from Home Assistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# Import the domain constant from the current package
from .const import DOMAIN
from . import cover  # Import the cover platform

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the blinds controller component."""
    # Return True to enable the config flow.
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up your integration with the configuration entry."""
    # Store the entry data in the hass.data dictionary
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data
    
    # Corrected function call: async_forward_entry_setups (plural)
    # This forwards the setup to the 'cover' platform.
    await hass.config_entries.async_forward_entry_setups(entry, ["cover"])
    
    # Add an update listener to reload the integration when options change
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload your integration when the configuration entry is removed."""
    # Forward the unload to the 'cover' platform
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "cover")
    
    # If unload was successful, remove the entry data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Reload the config entry when options are updated."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
