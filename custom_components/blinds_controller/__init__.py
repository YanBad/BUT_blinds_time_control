# Import necessary modules from Home Assistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# Import the domain constant from the current package
from .const import DOMAIN

async def async_setup(hass: HomeAssistant, config: dict):
    # Return True here and the user will be able to initiate the config flow from the integrations page
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    # Set up your integration with the configuration entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

    # Load the cover platform with the configuration entry
    # Corrected: Await the call directly and pass platforms as a list
    await hass.config_entries.async_forward_entry_setups(entry, ["cover"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    # Unload your integration when the configuration entry is removed
    # Corrected: Use async_unload_platforms with a list of platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["cover"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Optional: Clean up DOMAIN data if no more entries are left
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            
    return unload_ok
