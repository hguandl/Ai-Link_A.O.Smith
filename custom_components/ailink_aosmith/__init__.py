"""The Ai-Link A.O. Smith integration."""
import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .protocol import extract_output_data, temperature_command, numeric, is_e10, boiler_command, is_cte_ht3, electric_command

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .api import AOSmithAPI, AOSmithAPIError, AOSmithAuthError
from .translations import async_load_translation, get_language

_LOGGER = logging.getLogger(__name__)
TOKEN_RENEW_AHEAD_SECONDS = 12 * 60
TOKEN_RENEW_GAP_SECONDS = 3 * 60
TOKEN_EXPIRED_RENEW_GAP_SECONDS = 30 * 60
TOKEN_FAILURE_RENEW_GAP_SECONDS = 5 * 60


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ai-Link A.O. Smith from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    _LOGGER.info("Setting up A.O. Smith integration")
    
    api = None
    try:
        async def async_save_rotated_token(token: str) -> None:
            """Persist a cloud-rotated token without logging or reloading."""
            if entry.data.get("access_token") == token:
                return
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, "access_token": token},
            )

        # Initialize API
        api = AOSmithAPI(
            access_token=entry.data["access_token"],
            user_id=entry.data["user_id"],
            family_id=entry.data["family_id"],
            cookie=entry.data.get("cookie"),
            mobile=entry.data.get("mobile"),
            on_token_update=async_save_rotated_token,
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
        
    except AOSmithAuthError:
        if api is not None:
            await api.close()
        # Keep retrying setup: an old token can become usable again as soon as
        # the phone app rotates the account token.
        raise ConfigEntryNotReady("Authentication is temporarily unavailable") from None
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
        self._last_token_sync = None
        self._last_failure_token_sync = None
        self._post_expiry_attempted_token = None
        
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
                status = await self._async_get_recoverable_status(device_id)
                if not status or str(status.get("devState", "1")) == "0":
                    raise HomeAssistantError("Device status is unavailable")
                output = extract_output_data(status)
                if not output:
                    raise HomeAssistantError("Device status is unavailable")
                device_data = {**self.data.get(device_id, {}), **status}
                electric = is_cte_ht3(device_data)
                boiler = is_e10(device_data)
                if electric:
                    identifier, inputs, expected = electric_command(device_data, identifier, inputs)
                elif boiler:
                    try:
                        identifier, inputs, expected = boiler_command(device_data, identifier, inputs)
                    except (ValueError, KeyError, TypeError) as err:
                        raise HomeAssistantError(str(err)) from None
                elif identifier.startswith("boiler_"):
                    raise HomeAssistantError("E10 device identity is unavailable")
                elif identifier == "temperature":
                    value = inputs["temperature"]
                    identifier, inputs = temperature_command(output, value)
                model = status.get("productModel") or output.get("deviceModel")
                if not model:
                    raise HomeAssistantError("Device model is unavailable")
                if electric:
                    await self.api.async_send_command(device_id, identifier, inputs, device_type=model, product_type="17")
                elif boiler:
                    await self.api.async_send_command(device_id, identifier, inputs, device_type=model, product_type="24")
                else:
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
                raise HomeAssistantError(
                    "Access token is temporarily unavailable; open the AI-LiNK app once or replace the token in integration settings"
                ) from None
            except AOSmithAPIError:
                raise HomeAssistantError("Device command failed") from None
            except Exception:
                raise HomeAssistantError("Device command failed") from None

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            if not self.api.is_authenticated:
                await self.api.async_authenticate()

            await self._async_sync_token()
            devices = await self.api.async_get_devices()
            data = {}
            for device in devices:
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                try:
                    status = await asyncio.wait_for(
                        self._async_get_recoverable_status(device_id), timeout=10.0
                    )
                    if not status:
                        raise AOSmithAPIError("Device status unavailable")
                    data[device_id] = {
                        **device,
                        **status,
                        "_status_available": True,
                    }
                except AOSmithAuthError:
                    raise
                except (AOSmithAPIError, asyncio.TimeoutError):
                    # A transient failure for one device must not discard fresh
                    # data from the other devices in the same account.
                    data[device_id] = {**device, "_status_available": False}
            return data
        except AOSmithAuthError:
            # Do not stop the coordinator: continued polling is what lets the
            # integration adopt the next token produced by the phone app.
            raise UpdateFailed("Authentication is temporarily unavailable") from None
        except asyncio.TimeoutError:
            raise UpdateFailed("Unable to update device data") from None
        except AOSmithAPIError:
            raise UpdateFailed("Unable to update device data") from None
        except Exception:
            raise UpdateFailed("Unable to update device data") from None

    async def _async_get_recoverable_status(self, device_id):
        """Read status and retry once if the cloud returned an empty auth record."""
        status = await self.api.async_get_device_status(device_id)
        if self._looks_authorised(status):
            return status

        if await self._async_sync_token(force=True):
            status = await self.api.async_get_device_status(device_id)
            if self._looks_authorised(status):
                return status
        raise AOSmithAuthError("Cloud returned an empty device record")

    async def _async_sync_token(self, *, force=False):
        """Synchronize an expiring/stale token without treating ``exp`` as failure."""
        remaining = self.api.token_seconds_left
        if not force and (
            remaining is None or remaining > TOKEN_RENEW_AHEAD_SECONDS
        ):
            return False

        now = time.monotonic()
        if force:
            gap = TOKEN_FAILURE_RENEW_GAP_SECONDS
        elif remaining is not None and remaining <= 0:
            gap = (
                0
                if self._post_expiry_attempted_token != self.api.access_token
                else TOKEN_EXPIRED_RENEW_GAP_SECONDS
            )
        else:
            gap = TOKEN_RENEW_GAP_SECONDS
        last_attempt = (
            self._last_failure_token_sync if force else self._last_token_sync
        )
        if last_attempt is not None and now - last_attempt < gap:
            return False

        self._last_token_sync = now
        if force:
            self._last_failure_token_sync = now
        before = self.api.access_token
        if remaining is not None and remaining <= 0:
            self._post_expiry_attempted_token = before
        await self.api.async_renew_token()
        if self.api.access_token == before and remaining is not None and remaining <= 0:
            await self.api.async_mint_token()
        return self.api.access_token != before

    @staticmethod
    def _looks_authorised(status):
        """Detect HTTP-200 empty records used by the cloud for rejected tokens."""
        if not isinstance(status, dict):
            return False
        entity = status.get("appDeviceStatusInfoEntity")
        entity = entity if isinstance(entity, dict) else {}
        return bool(
            status.get("productModel")
            or status.get("productMajorClassCode")
            or status.get("statusInfo")
            or entity.get("statusInfo")
            or status.get("deviceId")
        )
