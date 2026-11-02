"""HomeKit-compatible thermostats for hot water and heating."""
import math
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode, HVACAction, ATTR_HVAC_MODE
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from .const import DOMAIN, DEVICE_CATEGORY_WATER_HEATER
from .entity import AOSmithEntity
from .protocol import numeric, flag, temperature_limits, is_e10, boiler_temperature_limits, is_cte_ht3


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for key, data in coordinator.data.items():
        if str(data.get("deviceCategory")) == DEVICE_CATEGORY_WATER_HEATER:
            entities.append(AOSmithClimate(coordinator, key))
        elif is_e10(data):
            entities.append(AOSmithBoilerClimate(coordinator, key))
        elif is_cte_ht3(data):
            entities.append(AOSmithElectricClimate(coordinator, key))
    async_add_entities(entities)


class AOSmithClimate(AOSmithEntity, ClimateEntity):
    """Expose target/outlet temperature and actual burner activity separately."""
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                               | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = f"{self.device_data.get('productName', 'Water Heater')} 温控"
        self._attr_unique_id = f"{device_id}_climate"

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

    @property
    def target_temperature_step(self):
        return temperature_limits(self._get_output_data())[2]

    @property
    def hvac_mode(self):
        on = flag(self._get_output_data(), "powerStatus", "powerOn")
        return None if on is None else HVACMode.HEAT if on else HVACMode.OFF

    @property
    def hvac_action(self):
        if self.hvac_mode is None:
            return None
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        heating = flag(self._get_output_data(), "heating")
        return None if heating is None else HVACAction.HEATING if heating else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs):
        value = float(kwargs[ATTR_TEMPERATURE])
        if not math.isfinite(value) or not self.min_temp <= value <= self.max_temp:
            raise ValueError(f"Temperature must be {self.min_temp}–{self.max_temp} °C")
        # HomeKit sends fractional degrees even for whole-degree devices.
        step = 1 if value >= 50 else self.target_temperature_step
        value = math.floor(value / step + 0.5) * step
        await self.coordinator.async_command(self.device_id, "temperature", {"temperature": value}, {"waterTemp": value})

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self.hvac_modes:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        value = "1" if hvac_mode == HVACMode.HEAT else "0"
        await self.coordinator.async_command(self.device_id, "SetDeviceOnOff", {"powerStatus": value}, {"powerStatus": value})

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)


class AOSmithBoilerClimate(AOSmithClimate):
    """E10 heating-water circuit; temperatures are water, never room air."""
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = f"{self.device_data.get('productName', 'Boiler')} 采暖供水"
        self._attr_unique_id = f"{device_id}_heating_climate"

    @property
    def available(self):
        if not super().available:
            return False
        try:
            boiler_temperature_limits(self._get_output_data(), heating=True)
        except ValueError:
            return False
        return True

    @property
    def current_temperature(self):
        return numeric(self._get_output_data(), "warmOutTEMP")

    @property
    def target_temperature(self):
        return numeric(self._get_output_data(), "warmTemp")

    @property
    def min_temp(self):
        try:
            return boiler_temperature_limits(self._get_output_data(), heating=True)[0]
        except ValueError:
            return 30

    @property
    def max_temp(self):
        try:
            return boiler_temperature_limits(self._get_output_data(), heating=True)[1]
        except ValueError:
            return 85

    @property
    def target_temperature_step(self):
        return 1

    @property
    def hvac_mode(self):
        output = self._get_output_data()
        power, heating = flag(output, "powerStatus"), flag(output, "warmStatus")
        if power is False:
            return HVACMode.OFF
        if power is None or heating is None:
            return None
        return HVACMode.HEAT if heating else HVACMode.OFF

    @property
    def hvac_action(self):
        # The captured flame flags do not identify which circuit is burning.
        return HVACAction.OFF if self.hvac_mode == HVACMode.OFF else None

    async def async_set_temperature(self, **kwargs):
        value = float(kwargs[ATTR_TEMPERATURE])
        minimum, maximum = boiler_temperature_limits(self._get_output_data(), heating=True)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Temperature must be {minimum}–{maximum} °C")
        value = math.floor(value + 0.5)
        await self.coordinator.async_command(self.device_id, "boiler_heating_temperature", {"temperature": value}, {"warmTemp": value})

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self.hvac_modes:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        value = 1 if hvac_mode == HVACMode.HEAT else 0
        await self.coordinator.async_command(self.device_id, "boiler_heating", {"value": value}, {"warmStatus": value})


class AOSmithElectricClimate(AOSmithEntity, ClimateEntity):
    """CTE-HT3: heat selects one tank; auto selects both tanks."""
    _attr_has_entity_name = True
    _attr_translation_key = "electric_temperature"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                                | ClimateEntityFeature.TURN_ON
                                | ClimateEntityFeature.TURN_OFF)
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    _attr_icon = "mdi:water-boiler"
    _attr_min_temp = 35
    _attr_max_temp = 75
    _attr_target_temperature_step = 1
    _attr_precision = 1.0

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        config = self.translation.get("entity", {}).get("climate", {}).get(self._attr_translation_key, {})
        self._attr_name = config.get("name", "Water temperature")
        self._attr_unique_id = f"{device_id}_electric_climate"

    @property
    def current_temperature(self):
        return numeric(self._get_output_data(), "realTemp")

    @property
    def target_temperature(self):
        return numeric(self._get_output_data(), "heatingTemp")

    @property
    def hvac_mode(self):
        output = self._get_output_data()
        power = flag(output, "powerStatus")
        if power is False:
            return HVACMode.OFF
        if power is None:
            return None
        return {1: HVACMode.HEAT, 2: HVACMode.AUTO}.get(numeric(output, "workModel"))

    @property
    def hvac_action(self):
        power = flag(self._get_output_data(), "powerStatus")
        if power is False:
            return HVACAction.OFF
        if power is None:
            return None
        heating = flag(self._get_output_data(), "heatStatus")
        return None if heating is None else HVACAction.HEATING if heating else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs):
        value = float(kwargs[ATTR_TEMPERATURE])
        if not math.isfinite(value) or not self.min_temp <= value <= self.max_temp:
            raise ValueError(f"Temperature must be {self.min_temp}–{self.max_temp} °C")
        # HomeKit sends fractional temperatures even for whole-degree devices.
        value = math.floor(value + 0.5)
        commands = self._control_commands(kwargs.get(ATTR_HVAC_MODE), value)
        await self.coordinator.async_commands(self.device_id, commands)

    def _control_commands(self, hvac_mode, temperature=None):
        """Validate first, then order power, temperature and tank selection."""
        if hvac_mode not in self.hvac_modes and (hvac_mode is not None or temperature is None):
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        commands = []
        if hvac_mode != HVACMode.OFF or temperature is not None:
            commands.append(("electric_power", {"value": 1}, {}))
        if temperature is not None:
            commands.append(("electric_temperature", {"temperature": temperature}, {}))
        if hvac_mode == HVACMode.OFF:
            commands.append(("electric_power", {"value": 0}, {}))
        elif hvac_mode is not None:
            value = 1 if hvac_mode == HVACMode.AUTO else 0
            commands.append(("electric_heating_mode", {"value": value}, {}))
        return commands

    async def async_set_hvac_mode(self, hvac_mode):
        await self.coordinator.async_commands(self.device_id, self._control_commands(hvac_mode))

    async def async_turn_on(self):
        await self.coordinator.async_command(self.device_id, "electric_power", {"value": 1}, {})

    async def async_turn_off(self):
        await self.coordinator.async_command(self.device_id, "electric_power", {"value": 0}, {})
