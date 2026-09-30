"""E10 regressions using anonymized status values and synthetic identities."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.ailink_aosmith import AOSmithDataUpdateCoordinator
from custom_components.ailink_aosmith.api import AOSmithAPI
from custom_components.ailink_aosmith import climate, water_heater, switch, fan, number, sensor
from custom_components.ailink_aosmith.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/e10.json').read_text(encoding='utf-8'))


def status(**values):
    data = copy.deepcopy(FIXTURE)
    data['appDeviceStatusInfoEntity']['statusInfo']['events'][0]['outputData'].update(values)
    return data


class E10Tests(unittest.IsolatedAsyncioTestCase):
    def coordinator(self, data=None):
        c = object.__new__(AOSmithDataUpdateCoordinator)
        c._command_lock = asyncio.Lock()
        c.api = SimpleNamespace(async_get_device_status=AsyncMock(return_value=data or status()),
                                async_send_command=AsyncMock())
        c.data = {'test-e10': data or status()}
        c.config_entry = None
        c.async_set_updated_data = lambda data: setattr(c, 'data', data)
        return c

    async def entities(self, platform, data=None):
        c = self.coordinator(data)
        hass = SimpleNamespace(data={DOMAIN: {'entry': c}})
        entry = SimpleNamespace(entry_id='entry', options={})
        entities = []
        await platform.async_setup_entry(hass, entry, lambda items, *args: entities.extend(items))
        return c, entities

    async def test_discovery_keeps_gas_and_e10_but_not_unverified_boilers(self):
        for container in ('devInfoItemInfoList', 'roomInfoItemInfoList'):
            devices = [{'deviceId': 'gas', 'deviceCategory': '19'}, status(),
                       {**status(), 'deviceId': 'unknown', 'productModel': 'OTHER',
                        'appDeviceStatusInfoEntity': {}},
                       {'deviceId': 'electric', 'deviceCategory': '17'}]
            info = {container: devices if container == 'devInfoItemInfoList' else [{'deviceList': devices}]}
            api = AOSmithAPI('fake-token', 'fake-user', 'fake-family')
            api._post_json = AsyncMock(return_value={'status': 200, 'info': info})
            self.assertEqual([d['deviceId'] for d in await api.async_get_devices()], ['gas', 'test-e10'])

    async def test_homepage_model_in_json_status_is_discovered(self):
        device = status()
        del device['productModel']
        device['deviceCategory'] = 24
        device['statusInfo'] = json.dumps(device.pop('appDeviceStatusInfoEntity')['statusInfo'])
        api = AOSmithAPI('fake-token', 'fake-user', 'fake-family')
        api._post_json = AsyncMock(return_value={'status': 200, 'info': {'devInfoItemInfoList': [device]}})
        self.assertEqual(len(await api.async_get_devices()), 1)

    async def test_independent_water_and_heating_entities(self):
        c, entities = await self.entities(climate)
        self.assertEqual(len(entities), 1)
        heating = entities[0]
        self.assertEqual(heating.target_temperature, 35)
        self.assertEqual(heating.current_temperature, 41)
        self.assertEqual((heating.min_temp, heating.max_temp), (30, 85))
        self.assertEqual(heating.hvac_mode, 'off')
        c.data['test-e10'] = status(warmStatus='1')
        self.assertEqual(heating.hvac_mode, 'heat')
        # No guessed burner-to-circuit mapping: E10 has no gas-heater 'heating' field.
        self.assertIsNone(heating.hvac_action)
        c.data['test-e10'] = status(powerStatus='0', warmStatus='1')
        self.assertEqual(heating.hvac_mode, 'off')
        c.data['test-e10'] = status(powerStatus=None)
        self.assertIsNone(heating.hvac_mode)
        _, entities = await self.entities(water_heater)
        self.assertEqual(len(entities), 1)
        water = entities[0]
        self.assertEqual(water.target_temperature, 43)
        self.assertEqual(water.current_temperature, 38)
        self.assertEqual((water.min_temp, water.max_temp), (35, 60))
        self.assertEqual(water.supported_features, water_heater.WaterHeaterEntityFeature.TARGET_TEMPERATURE)

    async def test_no_gas_booster_or_duration_controls_for_e10(self):
        for platform in (fan, number):
            _, entities = await self.entities(platform)
            self.assertEqual(entities, [])
        _, entities = await self.entities(switch)
        self.assertEqual(len(entities), 1)
        self.assertTrue(entities[0].is_on)

    async def test_e10_sensors_use_own_fields(self):
        cfg = json.loads(Path('custom_components/ailink_aosmith/translations/zh-Hans.json').read_text(encoding='utf-8'))
        with patch.object(sensor, 'async_load_translation', AsyncMock(return_value=cfg)):
            _, entities = await self.entities(sensor)
        values = {e._sensor_key: e.native_value for e in entities}
        self.assertEqual(values['waterTEMP'], 43)
        self.assertEqual(values['warmTemp'], 35)
        self.assertEqual(values['warmOutTEMP'], 41)
        self.assertNotIn('outWaterTemp', values)
        self.assertNotIn('totalGasNum', values)

    async def test_entity_actions_keep_circuits_separate(self):
        c, entities = await self.entities(climate)
        c.async_command = AsyncMock()
        await entities[0].async_set_temperature(temperature=35.5)
        c.async_command.assert_awaited_once_with('test-e10', 'boiler_heating_temperature',
                                               {'temperature': 36}, {'warmTemp': 36})
        await entities[0].async_turn_off()
        c.async_command.assert_awaited_with('test-e10', 'boiler_heating', {'value': 0}, {'warmStatus': 0})
        c, entities = await self.entities(water_heater)
        c.async_command = AsyncMock()
        await entities[0].async_set_temperature(temperature=44)
        c.async_command.assert_awaited_once_with('test-e10', 'boiler_water_temperature',
                                               {'temperature': 44}, {'waterTEMP': 44})
        with self.assertRaises(HomeAssistantError):
            await entities[0].async_turn_off()
        self.assertEqual(c.async_command.await_count, 1)
        c, entities = await self.entities(switch)
        c.async_command = AsyncMock()
        await entities[0].async_turn_off()
        c.async_command.assert_awaited_once_with('test-e10', 'boiler_power', {'value': 0}, {'powerStatus': 0})

    async def test_missing_limits_make_temperature_entities_unavailable(self):
        for platform, values in ((climate, {'warmTempSetMax': None}),
                                 (water_heater, {'waterTempSetMin': None})):
            c, entities = await self.entities(platform, status(**values))
            c.last_update_success = True
            self.assertFalse(entities[0].available)
        c, entities = await self.entities(water_heater, status(waterTEMP=15))
        self.assertFalse(entities[0].is_on)
        self.assertIsNone(entities[0].target_temperature)

    async def test_gas_sensor_mapping_is_unchanged_in_mixed_household(self):
        cfg = json.loads(Path('custom_components/ailink_aosmith/translations/en.json').read_text(encoding='utf-8'))
        gas = {'deviceCategory': '19', 'productModel': 'JSQ31-VJS',
               'statusInfo': {'outputData': {'waterTemp': 40, 'waterFlow': 2}}}
        with patch.object(sensor, 'async_load_translation', AsyncMock(return_value=cfg)):
            _, entities = await self.entities(sensor, gas)
        mapped = {entity._sensor_key: entity for entity in entities}
        self.assertEqual(mapped['waterFlow'].name, cfg['entity']['sensor']['waterFlow']['name'])
        self.assertNotIn('warmTemp', mapped)
        self.assertNotIn('waterTEMP', mapped)

    async def test_e10_commands_use_category24_and_commandvalue(self):
        cases = [
            ('boiler_water_temperature', {'temperature': 44}, 'SetTemperature',
             {'CommandValue': '44', 'CommandType': '0'}, {'waterTEMP': 44}),
            ('boiler_heating_temperature', {'temperature': 36}, 'SetTemperature',
             {'CommandValue': '36', 'CommandType': '1'}, {'warmTemp': 36}),
            ('boiler_heating', {'value': 1}, 'SetHeatingOnOff', {'CommandValue': '1'}, {'warmStatus': 1}),
            ('boiler_power', {'value': 0}, 'SetDeviceOnOff', {'CommandValue': '0'}, {'powerStatus': 0}),
        ]
        for identifier, inputs, service, command, updated in cases:
            c = self.coordinator()
            c.api.async_get_device_status.side_effect = [status(), status(**updated)]
            with patch('custom_components.ailink_aosmith.asyncio.sleep', AsyncMock()):
                await c.async_command('test-e10', identifier, inputs, updated)
            c.api.async_send_command.assert_awaited_once_with('test-e10', service, command,
                                                            device_type='LL1GBQ24-E10', product_type='24')

    async def test_invalid_or_locked_commands_never_sent(self):
        cases = [
            (status(), 'boiler_water_temperature', {'temperature': 61}),
            (status(), 'boiler_water_temperature', {'temperature': 34}),
            (status(), 'boiler_water_temperature', {'temperature': 43.5}),
            (status(), 'boiler_heating_temperature', {'temperature': 29}),
            (status(), 'boiler_heating_temperature', {'temperature': float('nan')}),
            (status(waterTempSetMax=None), 'boiler_water_temperature', {'temperature': 44}),
            (status(AES_FuncOn='1'), 'boiler_heating_temperature', {'temperature': 36}),
            (status(powerStatus='0'), 'boiler_heating', {'value': 1}),
            (status(waterTEMP=15), 'boiler_water_temperature', {'temperature': 44}),
            (status(livingWaterMode=1, hasAD_Fire=1), 'boiler_water_temperature', {'temperature': 51}),
            ({**status(), 'devState': 0}, 'boiler_power', {'value': 1}),
            ({**status(), 'isTriplesupply': 1}, 'boiler_power', {'value': 0}),
            (status(), 'WaterTempSet', {'waterTemp': '44'}),
            (status(), 'boiler_power', {'value': 2}),
        ]
        for data, identifier, inputs in cases:
            with self.subTest(identifier=identifier, inputs=inputs):
                c = self.coordinator(data)
                with self.assertRaises(HomeAssistantError):
                    await c.async_command('test-e10', identifier, inputs, {})
                c.api.async_send_command.assert_not_awaited()

    async def test_stale_state_does_not_confirm_e10_command(self):
        c = self.coordinator()
        with patch('custom_components.ailink_aosmith.asyncio.sleep', AsyncMock()):
            with self.assertRaises(HomeAssistantError):
                await c.async_command('test-e10', 'boiler_water_temperature', {'temperature': 44}, {'waterTEMP': 44})
        c.api.async_send_command.assert_awaited_once()

    async def test_api_category24_envelope_preserves_gas_default(self):
        api = AOSmithAPI('fake-token', 'fake-user', 'fake-family')
        api._post_json = AsyncMock(return_value={'status': 200})
        await api.async_send_command('test-e10', 'SetTemperature', {'CommandValue': '44', 'CommandType': '0'},
                                     device_type='LL1GBQ24-E10', product_type='24')
        payload = api._post_json.call_args.args[1]
        profile = json.loads(payload['payLoad'])['profile']
        self.assertEqual(profile, {'deviceId': 'test-e10', 'productType': '24', 'deviceType': 'LL1GBQ24-E10'})
        self.assertEqual(payload['deviceId'], 'test-e10')
        self.assertFalse(api._post_json.call_args.kwargs['retry_auth'])
        await api.async_send_command('gas', 'WaterTempSet', {'waterTemp': '40'})
        self.assertEqual(json.loads(api._post_json.call_args.args[1]['payLoad'])['profile']['productType'], '19')
