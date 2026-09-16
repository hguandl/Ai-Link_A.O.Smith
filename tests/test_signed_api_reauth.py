"""Regression tests for signed transport and native reauthentication."""
import asyncio
import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.ailink_aosmith import api as api_module
from custom_components.ailink_aosmith.api import (
    AOSmithAPIError,
    AOSmithAuthError,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, headers=None):
        self.status = status
        self._payload = payload
        self._text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    async def text(self):
        return self._text

    async def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        if self._payload is None:
            raise json.JSONDecodeError("invalid", self._text, 0)
        return self._payload

    def raise_for_status(self):
        return None


class ResponseContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        response = self.response.pop(0) if isinstance(self.response, list) else self.response
        return ResponseContext(response)

    async def close(self):
        pass


class Nonce:
    def __str__(self):
        return "NONCE"


class SignedApiTests(unittest.IsolatedAsyncioTestCase):
    def make_api(self, response=None, error=None):
        api = api_module.AOSmithAPI("Bearer secret-token", "用户-α", "家庭-β")
        api._session = FakeSession(response, error)
        return api

    async def test_signed_body_is_exact_utf8_bytes(self):
        response = FakeResponse(
            payload={"status": 200, "info": {"devInfoItemInfoList": [{"deviceId": "d1", "deviceCategory": "19"}]}},
        )
        api = self.make_api(response)
        nonce = "NONCE"
        with patch.object(api_module.time, "time", return_value=1700000000), patch.object(
            api_module.uuid, "uuid4", return_value=Nonce()
        ):
            await api.async_get_devices()
        _, kwargs = api._session.calls[0]
        self.assertNotIn("json", kwargs)
        body = kwargs["data"]
        self.assertIsInstance(body, bytes)
        self.assertFalse(kwargs["allow_redirects"])
        expected = json.dumps(
            {"encode": api._generate_encode(), "homePageVersion": "3", "userId": "用户-α", "familyId": "家庭-β"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(body, expected)
        md5data = hashlib.md5(body).hexdigest()
        self.assertEqual(kwargs["headers"]["md5data"], md5data)
        self.assertEqual(kwargs["headers"]["sign"], hashlib.md5((md5data + "1700000000000" + nonce + "ng957stzh4zy3dts").encode()).hexdigest())

    def test_encode_uses_sorted_request_values_and_salt(self):
        api = api_module.AOSmithAPI("token", "user", "family")
        expected = hashlib.md5("familyuserAILink_2021#".encode("utf-8")).hexdigest()
        self.assertEqual(api._generate_encode(), expected)
        expected = hashlib.md5("targetfamilyuserAILink_2021#".encode("utf-8")).hexdigest()
        self.assertEqual(api._generate_encode("target"), expected)

    async def test_auth_http_errors_are_typed_and_redacted(self):
        for status in (401, 403):
            api = self.make_api(FakeResponse(status=status, text="secret-token user-α device-1"))
            with self.assertRaises(Exception) as caught:
                await api.async_get_devices()
            self.assertEqual(type(caught.exception).__name__, "AOSmithAuthError")
            self.assertNotIn("secret-token", str(caught.exception))
            self.assertNotIn("device-1", repr(caught.exception))

    async def test_non_auth_http_json_business_and_timeout_are_not_success(self):
        cases = [
            (FakeResponse(status=500, text="secret-token"), None),
            (FakeResponse(status=302, text="redirect"), None),
            (FakeResponse(status=200, payload=None, text="not json"), None),
            (FakeResponse(status=200, payload={"status": 500, "msg": "bad"}), None),
            (None, asyncio.TimeoutError()),
        ]
        for response, error in cases:
            api = self.make_api(response, error)
            with self.assertRaises(Exception) as caught:
                await api.async_get_devices()
            self.assertEqual(type(caught.exception).__name__, "AOSmithAPIError")
            self.assertNotIn("secret-token", str(caught.exception))

    async def test_json_auth_status_is_typed_auth_failure(self):
        api = self.make_api(FakeResponse(payload={"status": 401, "msg": "expired"}))
        with self.assertRaises(AOSmithAuthError):
            await api.async_get_devices()

    async def test_response_authorization_header_is_adopted_and_persisted(self):
        updates = []
        response = FakeResponse(
            payload={"status": 200, "info": {"devInfoItemInfoList": []}},
            headers={"Authorization": "Bearer rotated-token"},
        )
        api = api_module.AOSmithAPI(
            "old-token", "user", "family", on_token_update=updates.append
        )
        api._session = FakeSession(response)

        await api.async_get_devices()

        self.assertEqual(api.access_token, "rotated-token")
        self.assertEqual(updates, ["rotated-token"])

    async def test_get_last_token_adopts_newest_account_token(self):
        api = self.make_api(FakeResponse(
            payload={"status": 200, "info": {"token": "Bearer newest-token"}}
        ))

        self.assertTrue(await api.async_renew_token())

        self.assertEqual(api.access_token, "newest-token")
        args, kwargs = api._session.calls[0]
        self.assertTrue(args[0].endswith("/AiLinkService/api/getLastToken"))
        self.assertEqual(
            json.loads(kwargs["data"]), {"token": "Bearer secret-token"}
        )

    async def test_business_command_failure_does_not_retry(self):
        response = FakeResponse(payload={"status": 500, "msg": "rejected"})
        api = self.make_api(response)
        with self.assertRaises(Exception) as caught:
            await api.async_send_command("d1", "WaterTempSet", {"waterTemp": "40"})
        self.assertEqual(type(caught.exception).__name__, "AOSmithAPIError")
        self.assertEqual(len(api._session.calls), 1)


class ReauthSurfaceTests(unittest.TestCase):
    def test_native_reauth_and_reconfigure_steps_exist(self):
        from custom_components.ailink_aosmith.config_flow import AOSmithConfigFlow
        self.assertTrue(hasattr(AOSmithConfigFlow, "async_step_reauth"))
        self.assertTrue(hasattr(AOSmithConfigFlow, "async_step_reconfigure"))

    def test_error_translations_are_singular_and_bilingual(self):
        for language in ("en", "zh-Hans"):
            path = f"custom_components/ailink_aosmith/translations/{language}.json"
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            errors = data["config"]["error"]
            self.assertIn("auth_error", errors)
            self.assertIn("cannot_connect", errors)
            self.assertIn("no_devices", errors)
            self.assertIn("reauth_successful", data["config"]["abort"])


@unittest.skipUnless(api_module.aiohttp, "aiohttp required")
class RuntimeAuthTests(unittest.IsolatedAsyncioTestCase):
    def coordinator(self, api):
        from custom_components.ailink_aosmith import AOSmithDataUpdateCoordinator

        coordinator = object.__new__(AOSmithDataUpdateCoordinator)
        coordinator.api = api
        coordinator._command_lock = asyncio.Lock()
        coordinator.data = {"d1": {"waterTemp": "38"}}
        coordinator.async_set_updated_data = AsyncMock()
        coordinator.hass = SimpleNamespace()
        coordinator._last_token_sync = None
        coordinator._last_failure_token_sync = None
        coordinator._post_expiry_attempted_token = None
        return coordinator

    async def test_setup_auth_failure_keeps_retrying(self):
        from custom_components.ailink_aosmith import async_setup_entry
        from homeassistant.exceptions import ConfigEntryNotReady

        class AuthFailAPI:
            def __init__(self, *args, **kwargs):
                pass

            async def async_authenticate(self):
                raise AOSmithAuthError("Bearer secret-token")

            async def close(self):
                pass

        entry = SimpleNamespace(
            data={"access_token": "secret-token", "user_id": "u", "family_id": "f"},
            options={},
            entry_id="entry",
        )
        hass = SimpleNamespace(data={})
        with patch("custom_components.ailink_aosmith.AOSmithAPI", AuthFailAPI):
            with self.assertRaises(ConfigEntryNotReady) as caught:
                await async_setup_entry(hass, entry)
        self.assertNotIn("secret-token", repr(caught.exception))

    async def test_poll_auth_failure_keeps_coordinator_retrying(self):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        api = SimpleNamespace(
            is_authenticated=False,
            async_authenticate=AsyncMock(side_effect=AOSmithAuthError("secret")),
        )
        with self.assertRaises(UpdateFailed):
            await self.coordinator(api)._async_update_data()

    async def test_detail_auth_failure_is_not_silently_basic_info(self):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        api = SimpleNamespace(
            is_authenticated=True,
            token_seconds_left=None,
            access_token="token",
            async_renew_token=AsyncMock(return_value=False),
            async_mint_token=AsyncMock(return_value=False),
            async_get_devices=AsyncMock(return_value=[{"deviceId": "d1"}]),
            async_get_device_status=AsyncMock(side_effect=AOSmithAuthError("secret")),
        )
        with self.assertRaises(UpdateFailed):
            await self.coordinator(api)._async_update_data()

    async def test_one_status_failure_does_not_discard_other_devices(self):
        api = SimpleNamespace(
            is_authenticated=True,
            token_seconds_left=None,
            access_token="token",
            async_renew_token=AsyncMock(return_value=False),
            async_mint_token=AsyncMock(return_value=False),
            async_get_devices=AsyncMock(return_value=[
                {"deviceId": "d1", "deviceCategory": "19"},
                {"deviceId": "d2", "deviceCategory": "19"},
            ]),
            async_get_device_status=AsyncMock(side_effect=[
                {"devState": 1, "productModel": "JSQ31-VJS"},
                AOSmithAPIError("temporary failure"),
            ]),
        )

        data = await self.coordinator(api)._async_update_data()

        self.assertTrue(data["d1"]["_status_available"])
        self.assertEqual(data["d1"]["productModel"], "JSQ31-VJS")
        self.assertFalse(data["d2"]["_status_available"])

    async def test_command_auth_failure_has_no_optimistic_update(self):
        from homeassistant.exceptions import HomeAssistantError

        api = SimpleNamespace(
            async_get_device_status=AsyncMock(return_value={"productModel": "JSQ31-VJS", "devState": 1,
                "statusInfo": {"events": [{"identifier": "post", "outputData": {"waterTemp": "38"}}]}}),
            async_send_command=AsyncMock(side_effect=AOSmithAuthError("secret")),
        )
        coordinator = self.coordinator(api)
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_command("d1", "WaterTempSet", {"waterTemp": "40"}, {"waterTemp": 40})
        coordinator.async_set_updated_data.assert_not_awaited()

    async def test_empty_auth_record_recovers_after_token_sync(self):
        api = SimpleNamespace(
            access_token="old-token",
            token_seconds_left=60,
            async_get_device_status=AsyncMock(side_effect=[
                {"devState": 0, "productModel": ""},
                {"devState": 1, "productModel": "JSQ31-VJS"},
            ]),
            async_mint_token=AsyncMock(return_value=False),
        )

        async def renew():
            api.access_token = "new-token"
            return True

        api.async_renew_token = AsyncMock(side_effect=renew)
        status = await self.coordinator(api)._async_get_recoverable_status("d1")
        self.assertEqual(status["productModel"], "JSQ31-VJS")
        self.assertEqual(api.async_get_device_status.await_count, 2)

    async def test_token_sync_never_gives_up_after_repeated_failures(self):
        api = SimpleNamespace(
            access_token="old-token",
            token_seconds_left=60,
            async_mint_token=AsyncMock(return_value=False),
        )
        attempts = 0

        async def renew():
            nonlocal attempts
            attempts += 1
            if attempts == 5:
                api.access_token = "recovered-token"
                return True
            return False

        api.async_renew_token = AsyncMock(side_effect=renew)
        coordinator = self.coordinator(api)
        with patch("custom_components.ailink_aosmith.time.monotonic", side_effect=[301, 602, 903, 1204, 1505]):
            results = [await coordinator._async_sync_token(force=True) for _ in range(5)]
        self.assertEqual(results, [False, False, False, False, True])
        self.assertEqual(attempts, 5)

    async def test_first_failure_sync_is_not_delayed_by_system_uptime(self):
        api = SimpleNamespace(
            access_token="old-token",
            token_seconds_left=60,
            async_renew_token=AsyncMock(return_value=False),
            async_mint_token=AsyncMock(return_value=False),
        )
        coordinator = self.coordinator(api)

        with patch("custom_components.ailink_aosmith.time.monotonic", return_value=1):
            await coordinator._async_sync_token(force=True)

        api.async_renew_token.assert_awaited_once()

    async def test_command_business_failure_is_safe(self):
        from homeassistant.exceptions import HomeAssistantError

        api = SimpleNamespace(
            async_get_device_status=AsyncMock(return_value={"productModel": "JSQ31-VJS", "devState": 1,
                "statusInfo": {"events": [{"identifier": "post", "outputData": {"waterTemp": "38"}}]}}),
            async_send_command=AsyncMock(side_effect=AOSmithAPIError("secret")),
        )
        coordinator = self.coordinator(api)
        with self.assertRaises(HomeAssistantError):
            await coordinator.async_command("d1", "WaterTempSet", {"waterTemp": "40"}, {"waterTemp": 40})
        coordinator.async_set_updated_data.assert_not_awaited()

    async def test_reauth_failure_does_not_update_saved_entry(self):
        from custom_components.ailink_aosmith.config_flow import AOSmithConfigFlow

        entry = SimpleNamespace(
            data={"access_token": "old-token", "user_id": "u", "family_id": "f", "cookie": "c"},
            options={"update_interval": 60},
        )
        update_entry = Mock()
        flow = AOSmithConfigFlow()
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update_entry))
        flow._reauth_entry = entry
        flow._get_devices = AsyncMock(side_effect=AOSmithAuthError("old-token"))
        result = await flow.async_step_reauth_confirm({"access_token": "new-token"})
        self.assertEqual(result["errors"], {"base": "auth_error"})
        update_entry.assert_not_called()
        self.assertEqual(entry.data["access_token"], "old-token")

    async def test_reauth_success_preserves_identity_options_and_cookie(self):
        from custom_components.ailink_aosmith.config_flow import AOSmithConfigFlow

        entry = SimpleNamespace(
            entry_id="entry-id",
            data={"access_token": "old-token", "user_id": "u", "family_id": "f", "cookie": "c"},
            options={"update_interval": 60},
        )
        update_entry = Mock()
        reload_abort = Mock(return_value={"type": "abort", "reason": "reauth_successful"})
        flow = AOSmithConfigFlow()
        flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update_entry))
        flow.async_update_reload_and_abort = reload_abort
        flow._reauth_entry = entry
        flow._get_devices = AsyncMock(return_value=[{"deviceId": "d1"}])
        result = await flow.async_step_reauth_confirm({"access_token": "new-token"})
        self.assertEqual(result["type"], "abort")
        update_entry.assert_not_called()
        reload_abort.assert_called_once_with(
            entry, data_updates={"access_token": "new-token"}, reason="reauth_successful"
        )
        self.assertEqual(entry.data, {"access_token": "old-token", "user_id": "u", "family_id": "f", "cookie": "c"})
        self.assertEqual(entry.options, {"update_interval": 60})


if __name__ == "__main__":
    unittest.main()
