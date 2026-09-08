"""A.O. Smith API client."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp

from .const import API_BASE_URL, DEVICE_CATEGORY_WATER_HEATER

_LOGGER = logging.getLogger(__name__)

ENCODE_SALT = "AILink_2021#"
# gitleaks:allow — public client-side protocol constant, not an account credential.
# Source: official AI-LiNK H5 module 45760, downloaded 2026-09-09.
SIGN_SECRET = "ng957stzh4zy3dts"


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
    ) -> None:
        """Initialize the API client."""
        self._access_token = access_token.removeprefix("Bearer ").strip()
        self._user_id = user_id
        self._family_id = family_id
        self._cookie = cookie
        self._mobile = mobile
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
        data = await self._post_json("/AiLinkService/device/invokeMethod", payload)
        self._require_success(data)
        return data

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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
                if response.status in (401, 403):
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
        return data

    @staticmethod
    def _require_success(data: Dict[str, Any]) -> None:
        """Reject business-level failures without treating them as auth errors."""
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
