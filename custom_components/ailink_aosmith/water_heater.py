"""Native water heater entity, sharing validated controls with the thermostat."""
from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature, STATE_GAS
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from .const import DOMAIN, DEVICE_CATEGORY_WATER_HEATER
from .entity import AOSmithEntity
from .protocol import numeric, flag, temperature_limits, is_e10, boiler_temperature_limits


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for key, data in coordinator.data.items():
        if str(data.get("deviceCategory")) == DEVICE_CATEGORY_WATER_HEATER:
            entities.append(AOSmithWaterHeater(coordinator, key))
        elif is_e10(data):
            entities.append(AOSmithBoilerWaterHeater(coordinator, key))
    async_add_entities(entities)


class AOSmithWaterHeater(AOSmithEntity, WaterHeaterEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (WaterHeaterEntityFeature.TARGET_TEMPERATURE
                               | WaterHeaterEntityFeature.ON_OFF | WaterHeaterEntityFeature.OPERATION_MODE)
    _attr_operation_list = [STATE_OFF, STATE_GAS]
    _attr_precision = 1.0

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = self.device_data.get("productName", "A.O. Smith Water Heater")
        self._attr_unique_id = f"{device_id}_water_heater"

    @property
    def current_operation(self):
        value = flag(self._get_output_data(), "powerStatus", "powerOn")
        return None if value is None else STATE_GAS if value else STATE_OFF

    @property
    def is_on(self):
        return flag(self._get_output_data(), "powerStatus", "powerOn")

    @property
    def current_temperature(self):
        return numeric(self._get_output_data(), "outWaterTemp")

    @property
    def target_temperature(self):
        return numeric(self._get_output_data(), "waterTemp", "setTemp")

    @property
    def min_temp(self):
        return temperature_limits(self._get_output_data())[0]

    @property
    def max_temp(self):
        return temperature_limits(self._get_output_data())[1]

    async def async_set_temperature(self, **kwargs):
        value = kwargs[ATTR_TEMPERATURE]
        await self.coordinator.async_command(self.device_id, "temperature", {"temperature": value}, {"waterTemp": value})

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_command(self.device_id, "SetDeviceOnOff", {"powerStatus": "1"}, {"powerStatus": 1})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_command(self.device_id, "SetDeviceOnOff", {"powerStatus": "0"}, {"powerStatus": 0})

    async def async_set_operation_mode(self, operation_mode):
        if operation_mode == STATE_GAS:
            await self.async_turn_on()
        elif operation_mode == STATE_OFF:
            await self.async_turn_off()
        else:
            raise ValueError(f"Unsupported operation mode: {operation_mode}")

    @property
    def extra_state_attributes(self):
        output = self._get_output_data()
        return {key: output[key] for key in ("waterFlow", "fanSpeed", "outWaterTemp", "heating", "powerStatus", "cruiseStatus", "halfPipeCirclelStatus", "pressurize", "pressurizeLevel", "curiesTime") if key in output}


class AOSmithBoilerWaterHeater(AOSmithWaterHeater):
    """E10 domestic hot water; whole-boiler power has a separate switch."""
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
    _attr_operation_list = []

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = f"{self.device_data.get('productName', 'Boiler')} 生活热水"
        self._attr_unique_id = f"{device_id}_domestic_water_heater"

    @property
    def available(self):
        if not super().available:
            return False
        try:
            boiler_temperature_limits(self._get_output_data())
        except ValueError:
            return False
        return True

    @property
    def current_temperature(self):
        return numeric(self._get_output_data(), "waterOutTEMP")

    @property
    def target_temperature(self):
        value = numeric(self._get_output_data(), "waterTEMP")
        return None if value == 15 else value

    @property
    def is_on(self):
        power = flag(self._get_output_data(), "powerStatus")
        target = numeric(self._get_output_data(), "waterTEMP")
        if power is False or target == 15:
            return False
        return None if power is None or target is None else True

    @property
    def current_operation(self):
        on = self.is_on
        return None if on is None else STATE_GAS if on else STATE_OFF

    @property
    def min_temp(self):
        try:
            return boiler_temperature_limits(self._get_output_data())[0]
        except ValueError:
            return 35

    @property
    def max_temp(self):
        try:
            return boiler_temperature_limits(self._get_output_data())[1]
        except ValueError:
            return 60

    async def async_set_temperature(self, **kwargs):
        value = kwargs[ATTR_TEMPERATURE]
        await self.coordinator.async_command(self.device_id, "boiler_water_temperature", {"temperature": value}, {"waterTEMP": value})

    async def async_turn_on(self, **kwargs):
        raise HomeAssistantError("Use the separate boiler power switch")

    async def async_turn_off(self, **kwargs):
        raise HomeAssistantError("Use the separate boiler power switch")

    @property
    def extra_state_attributes(self):
        output = self._get_output_data()
        return {key: output[key] for key in ("waterFlow", "waterOutTEMP", "waterTEMP", "powerStatus") if key in output}
