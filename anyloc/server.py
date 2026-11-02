from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.amfi import AmfiService

from .bluetooth import bluetooth_diagnose
from .device import discover_devices
from .state import AppState, RoutePoint

WEB_DIR = Path(__file__).parent / "web"


class ConnectRequest(BaseModel):
    udid: str | None = None
    connection_type: str = "USB"
    keepalive_seconds: float = Field(default=25.0, ge=0, le=3600)
    mode: str = "native"
    bluetooth_mac: str | None = None


class LocationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class KeepaliveRequest(BaseModel):
    seconds: float = Field(ge=0, le=3600)


class RoutePointRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    points: list[RoutePointRequest] = Field(min_length=1, max_length=20000)
    interval_ms: int = Field(default=300, ge=50, le=60000)
    loop: bool = False


class DeveloperModeRequest(BaseModel):
    udid: str | None = None


def create_app(state: AppState | None = None) -> FastAPI:
    app_state = state or AppState()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await app_state.shutdown()

    app = FastAPI(title="AnyLoc", version="0.1.0", lifespan=lifespan)
    app.state.anyloc = app_state

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return app_state.status()

    @app.get("/api/devices")
    async def devices() -> list[dict[str, Any]]:
        try:
            return [device.to_dict() for device in await discover_devices()]
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"设备发现失败: {type(exc).__name__}: {exc}") from exc

    @app.post("/api/connect")
    async def connect(request: ConnectRequest) -> dict[str, Any]:
        udid = request.udid
        if not udid:
            found = await discover_devices()
            if not found:
                raise HTTPException(status_code=404, detail="没有检测到 iOS 设备")
            udid = found[0].udid
        try:
            return await app_state.connect(
                udid=udid,
                connection_type=request.connection_type,
                keepalive_seconds=request.keepalive_seconds,
                mode=request.mode,
                bluetooth_mac=request.bluetooth_mac,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"连接失败: {type(exc).__name__}: {exc}") from exc

    @app.post("/api/disconnect")
    async def disconnect() -> dict[str, Any]:
        return await app_state.disconnect()

    @app.post("/api/location")
    async def location(request: LocationRequest) -> dict[str, Any]:
        try:
            return await app_state.set_location(request.latitude, request.longitude)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"定位失败: {type(exc).__name__}: {exc}") from exc

    @app.post("/api/developer-mode/reveal")
    async def reveal_developer_mode(request: DeveloperModeRequest) -> dict[str, Any]:
        udid = request.udid
        if not udid:
            found = await discover_devices()
            if not found:
                raise HTTPException(status_code=404, detail="没有检测到 iOS 设备")
            udid = found[0].udid
        lockdown = await create_using_usbmux(serial=udid, autopair=True, pair_timeout=10)
        try:
            before = await lockdown.get_developer_mode_status()
            if before:
                return {
                    "success": True,
                    "already_enabled": True,
                    "message": "开发者模式已经开启。",
                }
            await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
            return {
                "success": True,
                "already_enabled": False,
                "message": "已显示开发者模式入口。请在 iPhone 设置 → 隐私与安全性 → 开发者模式中打开，按提示重启，然后回来重新连接。",
            }
        finally:
            await lockdown.close()

    @app.post("/api/clear")
    async def clear() -> dict[str, Any]:
        try:
            return await app_state.clear()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"清除失败: {type(exc).__name__}: {exc}") from exc

    @app.post("/api/keepalive")
    async def keepalive(request: KeepaliveRequest) -> dict[str, Any]:
        return await app_state.set_keepalive(request.seconds)

    @app.post("/api/route")
    async def play_route(request: RouteRequest) -> dict[str, Any]:
        try:
            points = [RoutePoint(p.latitude, p.longitude) for p in request.points]
            return await app_state.play_route(points, interval_ms=request.interval_ms, loop=request.loop)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"路线播放失败: {type(exc).__name__}: {exc}") from exc

    @app.post("/api/route/stop")
    async def stop_route() -> dict[str, Any]:
        return await app_state.stop_route()

    @app.get("/api/bluetooth")
    async def bluetooth() -> dict[str, Any]:
        return await bluetooth_diagnose()

    @app.post("/api/bluetooth/connect")
    async def bluetooth_connect() -> dict[str, Any]:
        diagnose = await bluetooth_diagnose()
        raise HTTPException(
            status_code=501,
            detail=diagnose["reason"] or "蓝牙后端尚未配置",
        )

    return app
