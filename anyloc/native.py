from __future__ import annotations

import asyncio
import logging
from typing import Any

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.simulate_location import DtSimulateLocation

from .device import version_major

logger = logging.getLogger(__name__)


class BackendError(RuntimeError):
    pass


class DeveloperModeRequired(BackendError):
    pass


class NativeBackend:
    """iOS location simulation through Apple's developer services.

    iOS < 17: lockdown service `com.apple.dt.simulatelocation`.
    iOS >= 17: no-root userspace RSD tunnel + DVT LocationSimulation.
    """

    def __init__(self, udid: str, connection_type: str = "USB") -> None:
        self.udid = udid
        self.connection_type = connection_type.upper()
        self.product_version: str | None = None
        self.product_type: str | None = None
        self.developer_mode: bool | None = None
        self.transport = "legacy-lockdown"
        self._lockdown = None
        self._tunnel: UserspaceRsdTunnel | None = None
        self._rsd = None
        self._dvt: DvtProvider | None = None
        self._location: LocationSimulation | DtSimulateLocation | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        async with self._lock:
            if self._location is not None:
                return
            logger.info("connecting to %s over %s", self.udid, self.connection_type)
            self._lockdown = await create_using_usbmux(
                serial=self.udid,
                autopair=True,
                connection_type=self.connection_type,
                pair_timeout=20,
            )
            self.product_version = self._lockdown.product_version
            self.product_type = self._lockdown.all_values.get("ProductType", "Unknown")

            try:
                self.developer_mode = bool(await self._lockdown.get_developer_mode_status())
            except Exception:
                self.developer_mode = None

            if version_major(self.product_version) >= 17:
                if self.developer_mode is False:
                    raise DeveloperModeRequired(
                        "iPhone 未开启开发者模式。请先在手机设置中打开“开发者模式”并重启设备，"
                        "然后重新连接。点击界面上的“显示开发者模式开关”可把入口显示出来。"
                    )

            if version_major(self.product_version) < 17:
                self.transport = "legacy-lockdown"
                self._location = DtSimulateLocation(self._lockdown)
                logger.info("using legacy com.apple.dt.simulatelocation on iOS %s", self.product_version)
                return

            self.transport = "coredevice-userspace-dvt"
            self._tunnel = UserspaceRsdTunnel(
                serial=self.udid,
                autopair=True,
                remotepairing_fallback=True,
            )
            self._rsd = await self._tunnel.aopen()
            self._dvt = DvtProvider(self._rsd)
            await self._dvt.connect()
            self._location = LocationSimulation(self._dvt)
            await self._location.connect()
            logger.info("DVT LocationSimulation ready on iOS %s", self.product_version)

    async def set_location(self, latitude: float, longitude: float) -> None:
        await self.start()
        assert self._location is not None
        try:
            await self._location.set(float(latitude), float(longitude))
        except Exception as exc:
            raise BackendError(f"定位写入失败: {type(exc).__name__}: {exc}") from exc

    async def clear(self) -> None:
        await self.start()
        assert self._location is not None
        try:
            await self._location.clear()
        except Exception as exc:
            raise BackendError(f"清除定位失败: {type(exc).__name__}: {exc}") from exc

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            location = self._location
            self._location = None
            if isinstance(location, LocationSimulation):
                try:
                    await location.close()
                except Exception:
                    logger.debug("closing location simulation failed", exc_info=True)
            if self._dvt is not None:
                try:
                    await self._dvt.close()
                except Exception:
                    logger.debug("closing DVT failed", exc_info=True)
                self._dvt = None
            if self._tunnel is not None:
                try:
                    await self._tunnel.aclose()
                except Exception:
                    logger.debug("closing userspace tunnel failed", exc_info=True)
                self._tunnel = None
            self._rsd = None
            if self._lockdown is not None:
                try:
                    await self._lockdown.close()
                except Exception:
                    logger.debug("closing lockdown failed", exc_info=True)
                self._lockdown = None

    @property
    def connected(self) -> bool:
        return self._location is not None and not self._closed

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "udid": self.udid,
            "product_type": self.product_type,
            "product_version": self.product_version,
            "connection_type": self.connection_type,
            "transport": self.transport,
            "developer_mode": self.developer_mode,
        }
