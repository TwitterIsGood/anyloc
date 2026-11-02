from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .bluetooth import BluetoothBackend
from .native import BackendError, NativeBackend

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RoutePoint:
    latitude: float
    longitude: float


class AppState:
    def __init__(self) -> None:
        self.backend: NativeBackend | BluetoothBackend | None = None
        self.last_location: dict[str, float] | None = None
        self.last_applied_at: str | None = None
        self.last_error: str | None = None
        self.keepalive_seconds = 25.0
        self._keepalive_task: asyncio.Task | None = None
        self._route_task: asyncio.Task | None = None
        self._route_cancel = asyncio.Event()
        self._route_playing = False
        self._lock = asyncio.Lock()

    async def connect(
        self,
        udid: str,
        connection_type: str = "USB",
        keepalive_seconds: float = 25.0,
        mode: str = "native",
        bluetooth_mac: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            await self._stop_tasks()
            await self._close_backend()
            self.last_error = None
            self.keepalive_seconds = max(0.0, float(keepalive_seconds))
            if mode == "bluetooth":
                backend: NativeBackend | BluetoothBackend = BluetoothBackend(udid=udid, mac=bluetooth_mac)
            else:
                backend = NativeBackend(udid=udid, connection_type=connection_type)
            try:
                await backend.start()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                raise
            self.backend = backend
            if self.keepalive_seconds > 0:
                self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="anyloc-keepalive")
            return self.status()

    async def set_location(self, latitude: float, longitude: float) -> dict[str, Any]:
        if self.backend is None:
            raise BackendError("尚未连接设备")
        await self.backend.set_location(latitude, longitude)
        self.last_location = {"latitude": float(latitude), "longitude": float(longitude)}
        self.last_applied_at = datetime.now(timezone.utc).isoformat()
        self.last_error = None
        return self.status()

    async def clear(self) -> dict[str, Any]:
        self._route_cancel.set()
        if self._route_task is not None:
            self._route_task.cancel()
            try:
                await self._route_task
            except (asyncio.CancelledError, Exception):
                pass
            self._route_task = None
        self._route_playing = False
        if self.backend is None:
            raise BackendError("尚未连接设备")
        await self.backend.clear()
        self.last_location = None
        self.last_applied_at = None
        self.last_error = None
        if isinstance(self.backend, BluetoothBackend):
            self.backend = None
        return self.status()

    async def play_route(
        self,
        points: list[RoutePoint],
        interval_ms: int = 300,
        loop: bool = False,
    ) -> dict[str, Any]:
        if self.backend is None:
            raise BackendError("尚未连接设备")
        if not points:
            raise ValueError("route must contain at least one point")
        if interval_ms < 50:
            raise ValueError("interval_ms must be >= 50")
        if self._route_task is not None:
            self._route_cancel.set()
            self._route_task.cancel()
            try:
                await self._route_task
            except (asyncio.CancelledError, Exception):
                pass
        self._route_cancel = asyncio.Event()
        self._route_playing = True
        self._route_task = asyncio.create_task(
            self._play_route_loop(points, interval_ms / 1000.0, loop, self._route_cancel),
            name="anyloc-route-playback",
        )
        return self.status()

    async def stop_route(self) -> dict[str, Any]:
        self._route_cancel.set()
        if self._route_task is not None:
            self._route_task.cancel()
            try:
                await self._route_task
            except (asyncio.CancelledError, Exception):
                pass
            self._route_task = None
        self._route_playing = False
        return self.status()

    async def set_keepalive(self, seconds: float) -> dict[str, Any]:
        self.keepalive_seconds = max(0.0, float(seconds))
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None
        if self.backend is not None and self.keepalive_seconds > 0:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="anyloc-keepalive")
        return self.status()

    async def disconnect(self) -> dict[str, Any]:
        async with self._lock:
            await self._stop_tasks()
            await self._close_backend()
            self.last_location = None
            self.last_applied_at = None
            self.last_error = None
        return self.status()

    async def shutdown(self) -> None:
        await self.disconnect()

    def status(self) -> dict[str, Any]:
        backend_status = self.backend.status() if self.backend is not None else {
            "connected": False,
            "udid": None,
            "product_type": None,
            "product_version": None,
            "connection_type": None,
            "transport": None,
            "developer_mode": None,
        }
        return {
            **backend_status,
            "mode": (
                "bluetooth" if isinstance(self.backend, BluetoothBackend)
                else "native" if isinstance(self.backend, NativeBackend)
                else None
            ),
            "last_location": self.last_location,
            "last_applied_at": self.last_applied_at,
            "last_error": self.last_error,
            "keepalive_seconds": self.keepalive_seconds,
            "route_playing": self._route_playing,
        }

    async def _stop_tasks(self) -> None:
        self._route_cancel.set()
        for task in (self._route_task, self._keepalive_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._route_task = None
        self._keepalive_task = None
        self._route_playing = False

    async def _close_backend(self) -> None:
        backend = self.backend
        self.backend = None
        if backend is not None:
            try:
                await backend.close()
            except Exception:
                logger.debug("backend close failed", exc_info=True)

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.keepalive_seconds)
                if self.backend is None or self.last_location is None or self._route_playing:
                    continue
                try:
                    await self.backend.set_location(
                        self.last_location["latitude"],
                        self.last_location["longitude"],
                    )
                    self.last_applied_at = datetime.now(timezone.utc).isoformat()
                    self.last_error = None
                except Exception as exc:
                    self.last_error = f"keepalive: {type(exc).__name__}: {exc}"
                    logger.warning("keepalive failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def _play_route_loop(
        self,
        points: list[RoutePoint],
        interval: float,
        loop: bool,
        cancel_event: asyncio.Event,
    ) -> None:
        try:
            while not cancel_event.is_set():
                for point in points:
                    if cancel_event.is_set():
                        break
                    await self.set_location(point.latitude, point.longitude)
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
                if not loop:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"route: {type(exc).__name__}: {exc}"
            logger.warning("route playback failed: %s", exc)
        finally:
            self._route_playing = False
