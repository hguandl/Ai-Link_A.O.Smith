"""Protocols checked against the official GasWater and wallHung UIs."""
import json
import math

DURATION_PRESETS = (1, 5, 10, 15, 30, 60, 99)


def extract_event_data(device_data: dict, identifier: str):
    """Read one event from the detailed status, falling back to the homepage."""
    nested = device_data.get("appDeviceStatusInfoEntity")
    raw = nested.get("statusInfo") if isinstance(nested, dict) else None
    raw = raw or device_data.get("statusInfo")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    events = parsed.get("events", [])
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("identifier") == identifier:
                return event.get("outputData")
    return parsed.get("outputData") if identifier == "post" else None


def extract_output_data(device_data: dict) -> dict:
    """Read the property report as a dictionary."""
    output = extract_event_data(device_data, "post")
    return output if isinstance(output, dict) else {}


def numeric(output: dict, *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(output[key])
            if math.isfinite(value):
                return value
        except (KeyError, ValueError, TypeError):
            pass
    return None


def flag(output: dict, *keys: str) -> bool | None:
    value = numeric(output, *keys)
    return None if value is None else value == 1


def validate_integer(value: float, minimum: int, maximum: int) -> int:
    value = float(value)
    if not math.isfinite(value) or not value.is_integer() or not minimum <= value <= maximum:
        raise ValueError(f"Value must be an integer between {minimum} and {maximum}")
    return int(value)


def temperature_limits(output: dict) -> tuple[float, float, float]:
    minimum = 37 if flag(output, "minTemp35") is False else 35
    step = 0.5 if flag(output, "halfTempSetFlag") else 1
    return minimum, 70, step


def temperature_command(output: dict, value: float) -> tuple[str, dict]:
    minimum, maximum, step = temperature_limits(output)
    value = float(value)
    actual_step = 1 if value >= 50 else step
    if not math.isfinite(value) or not minimum <= value <= maximum or value % actual_step:
        raise ValueError(f"Temperature must be {minimum}–{maximum} °C in supported steps")
    current = numeric(output, "waterTemp", "setTemp")
    in_use = flag(output, "haveWater") or flag(output, "haveWaterUp")
    if current is None:
        raise ValueError("Current target temperature is unavailable")
    if in_use and value > current and (current >= 50 or value > 50):
        raise ValueError("Cannot raise the water temperature above 50 °C while hot water is in use")
    if flag(output, "halfTempSetFlag"):
        return "SetHalfTempValue", {"waterTemp": str(int(value * 2))}
    return "WaterTempSet", {"waterTemp": str(int(value))}


def is_e10(device_data: dict) -> bool:
    """Only enable the boiler protocol for the model whose status was captured."""
    category = device_data.get("deviceCategory") or device_data.get("productMajorClassCode")
    model = device_data.get("productModel") or extract_output_data(device_data).get("deviceModel")
    return str(category) == "24" and model == "LL1GBQ24-E10"


def boiler_temperature_limits(output: dict, heating: bool = False) -> tuple[int, int]:
    prefix = "warm" if heating else "water"
    minimum = numeric(output, f"{prefix}TempSetMin")
    maximum = numeric(output, f"{prefix}TempSetMax")
    lower, upper = (30, 85) if heating else (35, 60)
    if (minimum is None or maximum is None or not minimum.is_integer()
            or not maximum.is_integer() or not lower <= minimum <= maximum <= upper):
        raise ValueError("Boiler temperature limits are unavailable or unsupported")
    return int(minimum), int(maximum)


def boiler_command(device_data: dict, identifier: str, inputs: dict) -> tuple[str, dict, dict]:
    """Translate E10 actions using fresh status; never reuse gas-heater inputs."""
    if not is_e10(device_data) or str(device_data.get("devState")) != "1":
        raise ValueError("E10 is unavailable")
    output = extract_output_data(device_data)
    power = flag(output, "powerStatus")
    if power is None:
        raise ValueError("Boiler power state is unavailable")
    # The official page delegates combined systems to their system controller.
    standalone = flag(output, "wholeHouseWorkModel") is True or str(device_data.get("isTriplesupply")) == "0"
    if not standalone:
        raise ValueError("Combined-system boiler control is not supported")
    if identifier == "boiler_power":
        value = validate_integer(inputs["value"], 0, 1)
        return "SetDeviceOnOff", {"CommandValue": str(value)}, {"powerStatus": value}
    if not power:
        raise ValueError("Turn on the boiler power switch first")
    if identifier == "boiler_heating":
        value = validate_integer(inputs["value"], 0, 1)
        return "SetHeatingOnOff", {"CommandValue": str(value)}, {"warmStatus": value}
    if identifier not in ("boiler_water_temperature", "boiler_heating_temperature"):
        raise ValueError("Unsupported E10 command")
    heating = identifier == "boiler_heating_temperature"
    minimum, maximum = boiler_temperature_limits(output, heating)
    value = validate_integer(inputs["temperature"], minimum, maximum)
    key = "warmTemp" if heating else "waterTEMP"
    current = numeric(output, key)
    if current is None:
        raise ValueError("Current target temperature is unavailable")
    if heating:
        if flag(output, "AES_FuncOn") is not False:
            raise ValueError("Manual heating temperature is unavailable in SHC mode")
    else:
        if current == 15:
            raise ValueError("Domestic hot water is disabled")
        if value > current and value > 50:
            in_use = flag(output, "livingWaterMode")
            flame = flag(output, "hasAD_Fire")
            if in_use is None or flame is None or (in_use and flame):
                raise ValueError("Cannot raise water temperature above 50 °C while hot water is in use")
    return "SetTemperature", {"CommandValue": str(value), "CommandType": "1" if heating else "0"}, {key: value}


def is_cte_ht3(device_data: dict) -> bool:
    """Recognize only the captured category-17 model, including profile-only records."""
    nested = device_data.get("appDeviceStatusInfoEntity")
    raw = nested.get("statusInfo") if isinstance(nested, dict) else None
    raw = raw or device_data.get("statusInfo")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        parsed = None
    profile = parsed.get("profile") if isinstance(parsed, dict) else None
    profile = profile if isinstance(profile, dict) else {}
    category = (device_data.get("deviceCategory")
                or device_data.get("productMajorClassCode") or profile.get("productType"))
    model = device_data.get("productModel") or profile.get("deviceType") or profile.get("deviceModel")
    return str(category) == "17" and model == "CTE-HT3"


def electric_command(device_data, identifier, inputs):
    """Translate verified CTE-HT3 actions and their confirmation fields."""
    if not is_cte_ht3(device_data) or str(device_data.get("devState", "1")) == "0":
        raise ValueError("CTE-HT3 is unavailable")
    if identifier == "electric_power":
        value = validate_integer(inputs["value"], 0, 1)
        command, expected = {"powerStatus": str(value)}, {"powerStatus": value}
    elif identifier == "electric_temperature":
        value = validate_integer(inputs["temperature"], 35, 75)
        command, expected = {"Temperature": str(value)}, {"heatingTemp": value}
    elif identifier in ("electric_instant_heating", "electric_disinfection", "electric_max_capacity"):
        field, reported = {
            "electric_instant_heating": ("instantHeating", "instantHeating"),
            "electric_disinfection": ("Disinfection", "disinfection"),
            "electric_max_capacity": ("Max", "increaseCapacity"),
        }[identifier]
        value = validate_integer(inputs["value"], 0, 1)
        command, expected = {field: str(value)}, {reported: value}
    elif identifier == "electric_heating_mode":
        value = validate_integer(inputs["value"], 0, 1) + 1
        command, expected = {"HeaterMode": str(value)}, {"workModel": value}
    elif identifier in ("electric_reservation", "electric_reservation_hours"):
        output = extract_output_data(device_data)
        if identifier == "electric_reservation":
            enabled = validate_integer(inputs["value"], 0, 1)
            current_hours = numeric(output, "preheatTime")
            if current_hours is None:
                raise ValueError("Reservation delay is unavailable")
            hours = validate_integer(current_hours, 0, 24)
        else:
            enabled = flag(output, "preheatStatus3")
            if enabled is None:
                raise ValueError("Reservation state is unavailable")
            enabled = int(enabled)
            hours = validate_integer(inputs["value"], 0, 24)
        command = {"CountdownOne": {"OnOff": str(enabled), "Duration": str(hours)}}
        expected = {"preheatStatus3": enabled}
        if identifier == "electric_reservation_hours":
            expected["preheatTime"] = hours or 24
    else:
        raise ValueError("Unsupported CTE-HT3 command")
    return "SetElectricWaterHeater", command, expected
