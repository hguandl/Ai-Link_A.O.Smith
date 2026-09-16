"""A.O. Smith API client."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from .const import API_BASE_URL, DEVICE_CATEGORY_WATER_HEATER

_LOGGER = logging.getLogger(__name__)

ENCODE_SALT = "AILink_2021#"
# gitleaks:allow — public client-side protocol constant, not an account credential.
# Source: official AI-LiNK H5 module 45760, downloaded 2026-09-09.
SIGN_SECRET = "ng957stzh4zy3dts"
AUTH_STATUSES = {"401", "403", "1001"}


class AOSmithAPIError(Exception):
    """Base error for the AI-LiNK API."""


class AOSmithAuthError(AOSmithAPIError):
    """The supplied AI-LiNK credentials are not accepted."""


class AOSmithNoDevicesError(AOSmithAPIError):
    """The credentials were accepted but no supported device was returned."""


class AOSmithAPI:
    """A.O. Smith API client using a pre-obtained access token."""

    def __init__(
        self,
        access_token: str,
        user_id: str,
        family_id: str,
        cookie: str | None = None,
        mobile: str | None = None,
        on_token_update: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        """Initialize the API client."""
        self._access_token = self._normalise_token(access_token)
        self._user_id = user_id
        self._family_id = family_id
        self._cookie = cookie
        self._mobile = mobile
        self._on_token_update = on_token_update
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_authenticated = False

    async def async_authenticate(self) -> None:
        """Create a session and verify the supplied credentials."""
        if self._session:
            await self.close()

        self._is_authenticated = False
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        try:
            devices = await self.async_get_devices()
            if not devices:
                raise AOSmithNoDevicesError("No devices found")
            self._is_authenticated = True
            _LOGGER.info("Authentication succeeded")
        except AOSmithAPIError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise AOSmithAPIError("Authentication failed") from None

    async def async_get_devices(self) -> List[Dict[str, Any]]:
        """Get the user's water-heater devices."""
        payload = {
            "encode": self._generate_encode(),
            "homePageVersion": "3",
            "userId": self._user_id,
            "familyId": self._family_id,
        }
        data = await self._post_json(
            "/AiLinkService/appDevice/getHomepageV2", payload
        )
        self._require_success(data)
        info = data.get("info")
        if not isinstance(info, dict):
            raise AOSmithAPIError("Invalid device response")

        devices: list[dict[str, Any]] = []
        listed = info.get("devInfoItemInfoList")
        if isinstance(listed, list):
            devices.extend(item for item in listed if isinstance(item, dict))
        if not devices:
            rooms = info.get("roomInfoItemInfoList")
            if isinstance(rooms, list):
                for room in rooms:
                    if not isinstance(room, dict):
                        continue
                    room_devices = room.get("deviceList")
                    if isinstance(room_devices, list):
                        devices.extend(
                            item for item in room_devices if isinstance(item, dict)
                        )

        return [
            device
            for device in devices
            if str(device.get("deviceCategory")) == DEVICE_CATEGORY_WATER_HEATER
        ]

    async def async_get_device_status(self, device_id: str) -> Dict[str, Any]:
        """Get current status for one device."""
        payload = {
            "userId": self._user_id,
            "familyId": self._family_id,
            "deviceId": device_id,
            "encode": self._generate_encode(device_id),
        }
        data = await self._post_json(
            "/AiLinkService/appDevice/getDeviceCurrInfo", payload
        )
        self._require_success(data)
        info = data.get("info")
        if not isinstance(info, dict):
            raise AOSmithAPIError("Invalid device status response")
        return info

    async def async_send_command(
        self,
        device_id: str,
        service_identifier: str,
        input_data: Dict[str, Any] | None = None,
        *,
        device_type: str = "JSQ31-VJS",
    ) -> Dict[str, Any]:
        """Send one control command; writes are deliberately not retried."""
        if input_data is None:
            input_data = {}
        payload = {
            "userId": self._user_id,
            "familyId": self._family_id,
            "appSource": 2,
            "commandSource": 1,
            "invokeTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "payLoad": json.dumps(
                {
                    "profile": {
                        "deviceId": device_id,
                        "productType": "19",
                        "deviceType": device_type,
                    },
                    "service": {
                        "identifier": service_identifier,
                        "inputData": input_data,
                    },
                },
                ensure_ascii=False,
            ),
        }
        # A physical command is never replayed automatically: a response can be
        # lost after the cloud has already accepted the write.
        data = await self._post_json(
            "/AiLinkService/device/invokeMethod", payload, retry_auth=False
        )
        self._require_success(data)
        return data

    async def _post_json(
        self,
        path: str,
        payload: Dict[str, Any],
        *,
        retry_auth: bool = True,
    ) -> Dict[str, Any]:
        """POST one compact JSON body and decode one JSON response."""
        if self._session is None:
            raise AOSmithAPIError("API session unavailable")

        body = self._serialize_payload(payload)
        headers = self._generate_headers(payload, body=body)
        try:
            async with self._session.post(
                f"{API_BASE_URL}{path}",
                data=body,
                headers=headers,
                allow_redirects=False,
            ) as response:
                await self._adopt_response_token(response.headers)
                if response.status in (401, 403):
                    if retry_auth and await self.async_renew_token():
                        return await self._post_json(
                            path, payload, retry_auth=False
                        )
                    raise AOSmithAuthError("Authentication failed")
                if response.status != 200:
                    raise AOSmithAPIError("API request failed")
                try:
                    data = await response.json()
                except Exception:
                    raise AOSmithAPIError("Invalid API response") from None
        except (AOSmithAuthError, AOSmithAPIError):
            raise
        except asyncio.TimeoutError:
            raise AOSmithAPIError("API request timed out") from None
        except (aiohttp.ClientError, OSError):
            raise AOSmithAPIError("API request failed") from None
        except Exception:
            raise AOSmithAPIError("API request failed") from None

        if not isinstance(data, dict):
            raise AOSmithAPIError("Invalid API response")
        if str(data.get("status")) in AUTH_STATUSES:
            if retry_auth and await self.async_renew_token():
                return await self._post_json(path, payload, retry_auth=False)
            raise AOSmithAuthError("Authentication failed")
        return data

    async def async_renew_token(self) -> bool:
        """Adopt the account's newest token through ``getLastToken``.

        The endpoint does not necessarily mint a token. It returns the newest
        token already known for the account, and an old accepted token is enough
        to ask for it.
        """
        if self._session is None:
            return False
        before = self._access_token
        body = self._serialize_payload({"token": f"Bearer {before}"})
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "UserId": self._user_id,
            "source": "IOS",
            "version": "V1.0.1",
        }
        try:
            async with self._session.post(
                f"{API_BASE_URL}/AiLinkService/api/getLastToken",
                data=body,
                headers=headers,
                allow_redirects=False,
            ) as response:
                await self._adopt_response_token(response.headers)
                if response.status != 200:
                    return self._access_token != before
                try:
                    data = await response.json()
                except Exception:
                    return self._access_token != before
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return False

        info = data.get("info") if isinstance(data, dict) else None
        if isinstance(info, dict) and info.get("token"):
            await self._set_token(str(info["token"]), reason="getLastToken")
        return self._access_token != before

    async def async_mint_token(self) -> bool:
        """Ask a read-only app endpoint to re-issue a token in its response."""
        before = self._access_token
        payload = {
            "familyId": self._family_id,
            "userId": self._user_id,
            "encode": self._generate_encode(),
        }
        try:
            await self._post_json(
                "/AiLinkService//appDevice/getAntifreeze",
                payload,
                retry_auth=False,
            )
        except AOSmithAPIError:
            # Some token-minting endpoints return a business error while still
            # carrying the replacement in the Authorization response header.
            pass
        return self._access_token != before

    async def _adopt_response_token(self, headers: Any) -> None:
        """Read a rotated token from a cloud response header, if present."""
        value = None
        if headers is not None:
            value = headers.get("Authorization") or headers.get("authorization")
        if value:
            await self._set_token(str(value), reason="response header")

    async def _set_token(self, token: str, *, reason: str) -> bool:
        """Replace the in-memory token and persist it through the callback."""
        token = self._normalise_token(token)
        if not token or token == self._access_token:
            return False
        self._access_token = token
        _LOGGER.info("AI-LiNK access token updated from %s", reason)
        if self._on_token_update is not None:
            result = self._on_token_update(token)
            if inspect.isawaitable(result):
                await result
        return True

    @staticmethod
    def _normalise_token(token: str) -> str:
        """Return a raw bearer token without logging or decoding its contents."""
        token = token.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token

    @property
    def access_token(self) -> str:
        """Return the current raw token for persistence and comparison."""
        return self._access_token

    @property
    def token_seconds_left(self) -> float | None:
        """Read the unverified JWT ``exp`` claim as a scheduling hint only."""
        try:
            payload = self._access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            return float(data["exp"]) - time.time()
        except Exception:
            return None

    @staticmethod
    def _require_success(data: Dict[str, Any]) -> None:
        """Reject business-level failures without treating them as auth errors."""
        if str(data.get("status")) in AUTH_STATUSES:
            raise AOSmithAuthError("Authentication failed")
        if str(data.get("status")) != "200":
            raise AOSmithAPIError("API rejected the request")

    @staticmethod
    def _serialize_payload(payload: Dict[str, Any]) -> bytes:
        """Serialize a request once using the official compact UTF-8 form."""
        try:
            return json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise AOSmithAPIError("Invalid API request") from None

    def _generate_headers(
        self, payload: Dict[str, Any], *, body: bytes | None = None
    ) -> Dict[str, str]:
        """Generate headers whose hash/signature match the transmitted bytes."""
        body = body if body is not None else self._serialize_payload(payload)
        md5data = hashlib.md5(body).hexdigest()
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4()).upper()
        sign = hashlib.md5(
            f"{md5data}{timestamp}{nonce}{SIGN_SECRET}".encode("utf-8")
        ).hexdigest()
        return {
            "Host": "ailink-api.hotwater.com.cn",
            "Authorization": f"Bearer {self._access_token}",
            "version": "V1.0.1",
            "familyUk": "",
            "UserId": self._user_id,
            "timestamp": timestamp,
            "nonce": nonce,
            "Accept": "*/*",
            "source": "IOS",
            "md5data": md5data,
            "Accept-Language": "zh-Hans-CN;q=1",
            "Content-Type": "application/json",
            "traceId": f"{timestamp}-69861-{self._user_id}-00",
            "User-Agent": "AI jia zhi kong/2.2.5 (iPhone; iOS 26.0; Scale/3.00)",
            "Cookie": self._cookie or "",
            "sign": sign,
        }

    def _generate_md5data(self, payload: Dict[str, Any]) -> str:
        """Generate md5data for the exact compact request representation."""
        return hashlib.md5(self._serialize_payload(payload)).hexdigest()

    def _generate_encode(self, device_id: str | None = None) -> str:
        """Generate the sorted-value digest used by the official H5 client."""
        values = {"familyId": self._family_id, "userId": self._user_id}
        if device_id is not None:
            values["deviceId"] = device_id
        value = "".join(str(values[key]) for key in sorted(values)) + ENCODE_SALT
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    @property
    def is_authenticated(self) -> bool:
        """Return whether the last authentication check succeeded."""
        return self._is_authenticated

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._is_authenticated = False
