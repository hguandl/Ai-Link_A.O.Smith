"""The Ai-Link A.O. Smith integration."""
import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError

from .protocol import extract_output_data, temperature_command, numeric

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .api import AOSmithAPI, AOSmithAPIError, AOSmithAuthError
from .translations import async_load_translation, get_language

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ai-Link A.O. Smith from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    _LOGGER.info("Setting up A.O. Smith integration")
    
    api = None
    try:
        # Initialize API
        api = AOSmithAPI(
            access_token=entry.data["access_token"],
            user_id=entry.data["user_id"],
            family_id=entry.data["family_id"],
            cookie=entry.data.get("cookie"),
            mobile=entry.data.get("mobile")
        )
        
        await api.async_authenticate()
        
        # Create coordinator
        update_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        coordinator = AOSmithDataUpdateCoordinator(
            hass,
            api,
            update_interval=timedelta(seconds=update_interval),
        )
        # Attach the config entry to coordinator so entities can reference it
        coordinator.config_entry = entry

        # 加载翻译配置
        coordinator.translation = await async_load_translation(hass, entry)
        _LOGGER.info("Loaded translation for language: %s", get_language(hass, entry))
        
        # Fetch initial data
        await coordinator.async_config_entry_first_refresh()
        
        # Store coordinator
        hass.data[DOMAIN][entry.entry_id] = coordinator
        
        _LOGGER.debug("Loaded data for %d devices", len(coordinator.data))
        
        # Set up platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        _LOGGER.info("A.O. Smith integration setup completed successfully with %d devices", 
                    len(coordinator.data))
        return True
        
    except ConfigEntryAuthFailed:
        if api is not None:
            await api.close()
        raise
    except AOSmithAuthError:
        if api is not None:
            await api.close()
        raise ConfigEntryAuthFailed("Authentication failed") from None
    except Exception:
        if api is not None:
            await api.close()
        _LOGGER.error("Error setting up A.O. Smith integration")
        raise ConfigEntryNotReady("Unable to set up integration") from None

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.api.close()
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info("A.O. Smith integration unloaded successfully")
    
    return unload_ok

class AOSmithDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching A.O. Smith data."""
    
    def __init__(self, hass, api, update_interval: timedelta):
        """Initialize global data updater."""
        self.api = api
        self._command_lock = asyncio.Lock()
        self.data = {}
        self.translation = {}  # 初始化翻译属性
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
    
    async def async_command(self, device_id, identifier, inputs, expected):
        """Serialize writes and require a fresh device report to confirm success."""
        async with self._command_lock:
            try:
                status = await self.api.async_get_device_status(device_id)
                if not status or str(status.get("devState", "1")) == "0":
                    raise HomeAssistantError("Device status is unavailable")
                output = extract_output_data(status)
                if not output:
                    raise HomeAssistantError("Device status is unavailable")
                if identifier == "temperature":
                    value = inputs["temperature"]
                    identifier, inputs = temperature_command(output, value)
                model = status.get("productModel") or output.get("deviceModel")
                if not model:
                    raise HomeAssistantError("Device model is unavailable")
                await self.api.async_send_command(device_id, identifier, inputs, device_type=model)
                for delay in (1, 2, 3, 4):
                    await asyncio.sleep(delay)
                    status = await self.api.async_get_device_status(device_id)
                    if not status:
                        continue
                    output = extract_output_data(status)
                    updated = dict(self.data)
                    updated[device_id] = {**updated.get(device_id, {}), **status, "_status_available": True}
                    self.async_set_updated_data(updated)
                    if all(numeric(output, key) == float(value) for key, value in expected.items()):
                        return
                raise HomeAssistantError("Command sent, but the device did not confirm the requested state")
            except HomeAssistantError:
                raise
            except AOSmithAuthError:
                config_entry = getattr(self, "config_entry", None)
                if config_entry is not None:
                    config_entry.async_start_reauth_if_available(self.hass)
                raise ConfigEntryAuthFailed("Authentication failed") from None
            except AOSmithAPIError:
                raise HomeAssistantError("Device command failed") from None
            except Exception:
                raise HomeAssistantError("Device command failed") from None

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            if not self.api.is_authenticated:
                await self.api.async_authenticate()

            devices = await self.api.async_get_devices()
            data = {}
            for device in devices:
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                status = await asyncio.wait_for(
                    self.api.async_get_device_status(device_id), timeout=10.0
                )
                data[device_id] = {**device, **status, "_status_available": True}
            return data
        except AOSmithAuthError:
            raise ConfigEntryAuthFailed("Authentication failed") from None
        except asyncio.TimeoutError:
            raise UpdateFailed("Unable to update device data") from None
        except AOSmithAPIError:
            raise UpdateFailed("Unable to update device data") from None
        except Exception:
            raise UpdateFailed("Unable to update device data") from None
