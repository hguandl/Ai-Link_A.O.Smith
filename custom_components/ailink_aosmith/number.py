"""Exact 1–99 minute recirculation duration."""
from homeassistant.components.number import NumberEntity, NumberMode, NumberDeviceClass
from homeassistant.const import UnitOfTime
from .const import DOMAIN, DEVICE_CATEGORY_WATER_HEATER
from .entity import AOSmithEntity
from .protocol import numeric, validate_integer, is_cte_ht3


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for key, data in coordinator.data.items():
        if str(data.get("deviceCategory")) == DEVICE_CATEGORY_WATER_HEATER:
            entities.append(AOSmithCuriesTimeNumber(coordinator, key))
        elif is_cte_ht3(data):
            entities.append(AOSmithReservationHours(coordinator, key))
    async_add_entities(entities)


class AOSmithCuriesTimeNumber(AOSmithEntity, NumberEntity):
    _attr_native_min_value = 1
    _attr_native_max_value = 99
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:timer-cog"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = f"{self.device_data.get('productName', 'Water Heater')} 一键零冷水运行时长"
        self._attr_unique_id = f"{device_id}_curies_time"

    @property
    def native_value(self):
        return numeric(self._get_output_data(), "curiesTime")

    async def async_set_native_value(self, value):
        value = validate_integer(value, 1, 99)
        await self.coordinator.async_command(self.device_id, "WaterCruiseTimer", {"WaterCruiseTimer": str(value)}, {"curiesTime": value})


class AOSmithReservationHours(AOSmithEntity, NumberEntity):
    """Set the bathing reservation delay without changing its enabled state."""
    _attr_has_entity_name = True
    _attr_translation_key = "electric_reservation_hours"
    _attr_native_min_value = 0
    _attr_native_max_value = 24
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, device_id):
        super().__init__(coordinator, device_id)
        self._attr_name = self.translation.get("entity", {}).get("number", {}).get(self._attr_translation_key, {}).get("name", "Bathing reservation delay")
        self._attr_unique_id = f"{device_id}_electric_reservation_hours"

    @property
    def native_value(self):
        return numeric(self._get_output_data(), "preheatTime")

    async def async_set_native_value(self, value):
        await self.coordinator.async_command(self.device_id, "electric_reservation_hours", {"value": value}, {})
