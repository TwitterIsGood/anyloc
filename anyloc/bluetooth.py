from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from pymobiledevice3.lockdown import create_using_usbmux

logger = logging.getLogger(__name__)

DEFAULT_DYLIB = Path("/Applications/AnyGo.app/Contents/MacOS/libiOSBluetoothConnect.dylib")
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
BRIDGE_SOURCE = PACKAGE_ROOT / "bridges" / "bt" / "anyloc-bt-bridge"


def _bluetooth_devices_from_profiler() -> list[dict[str, Any]]:
    try:
        raw = subprocess.check_output(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for controller in data.get("SPBluetoothDataType", []):
        for key in ("device_connected", "device_not_connected"):
            for entry in controller.get(key, []):
                for name, props in entry.items():
                    result.append({
                        "name": name,
                        "connected": key == "device_connected",
                        **props,
                    })
    return result


def _dylib_path() -> Path:
    return Path(os.environ.get("ANYLOC_BT_DYLIB", str(DEFAULT_DYLIB)))


def _certifier_command() -> str:
    return os.environ.get("ANYLOC_BT_CERTIFY_CMD", "").strip()


def _bridge_runtime() -> Path:
    runtime = Path(os.environ.get("ANYLOC_BT_RUNTIME", str(Path.home() / ".anyloc" / "bt-runtime")))
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "anyloc-bt-bridge"
    if not target.exists() or target.stat().st_mtime < BRIDGE_SOURCE.stat().st_mtime:
        shutil.copy2(BRIDGE_SOURCE, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    dep = runtime / "libspdlogDll.dylib"
    source_dep = _dylib_path().parent / "libspdlogDll.dylib"
    if source_dep.is_file() and (not dep.exists() and not dep.is_symlink()):
        try:
            dep.symlink_to(source_dep)
        except OSError:
            shutil.copy2(source_dep, dep)
    return target


async def bluetooth_diagnose() -> dict[str, Any]:
    devices = await asyncio.to_thread(_bluetooth_devices_from_profiler)
    dylib = _dylib_path()
    cert = os.environ.get("ANYLOC_GO_CATCHER_CERT", "").strip()
    chip = os.environ.get("ANYLOC_GO_CATCHER_CHIP_INFO", "").strip()
    certifier = _certifier_command()
    credentials_ready = bool(cert and chip and Path(cert).is_file() and Path(chip).is_file())
    certifier_ready = bool(certifier)

    reason = None
    if not dylib.is_file():
        reason = "未找到 iAP2 运行时：libiOSBluetoothConnect.dylib 不存在"
    elif not BRIDGE_SOURCE.is_file():
        reason = "未找到 anyloc-bt-bridge 桥接程序"
    elif not credentials_ready:
        reason = "未配置 Go Catcher/MFi 的证书和 chipInfo 文件"
    elif not certifier_ready:
        reason = (
            "证书/chipInfo 已配置，但缺少动态 challenge certifier。"
            "iAP2 每次连接都会收到新的 challenge，必须通过 ANYLOC_BT_CERTIFY_CMD 返回动态响应。"
        )

    return {
        "available": bool(dylib.is_file() and BRIDGE_SOURCE.is_file() and credentials_ready and certifier_ready),
        "runtime_present": dylib.is_file(),
        "bridge_present": BRIDGE_SOURCE.is_file(),
        "credentials_configured": credentials_ready,
        "certifier_configured": certifier_ready,
        "reason": reason,
        "devices": [
            d for d in devices
            if "iphone" in str(d.get("name", "")).lower() or "ipad" in str(d.get("name", "")).lower()
        ],
        "note": "蓝牙模式是 iAP2/RFCOMM 外部 GPS 注入，不是 WiFi/USB 的替代传输。",
    }


async def probe_bluetooth(mac: str | None = None, udid: str | None = None) -> dict[str, Any]:
    if not mac:
        if not udid:
            raise ValueError("mac or udid is required")
        mac = await bluetooth_address(udid)
    bridge = _bridge_runtime()
    env = {**os.environ, "ANYLOC_BT_DYLIB": str(_dylib_path())}
    process = await asyncio.create_subprocess_exec(
        "arch", "-x86_64", str(bridge), "probe", mac,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "mac": mac,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
    }


async def bluetooth_address(udid: str) -> str:
    lockdown = await create_using_usbmux(serial=udid, autopair=True, pair_timeout=10)
    try:
        address = lockdown.all_values.get("BluetoothAddress")
        if not address:
            raise RuntimeError("设备未报告 BluetoothAddress")
        return str(address)
    finally:
        await lockdown.close()


class BluetoothBackend:
    """iAP2 external-GPS backend.

    The actual iAP2 implementation is loaded from a locally installed AnyGo
    runtime. A dynamic challenge signer is mandatory and supplied by the user.
    """

    def __init__(self, udid: str, mac: str | None = None) -> None:
        self.udid = udid
        self.mac = mac
        self.process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task | None = None
        self._stderr_lines: list[str] = []
        self._connected = False
        self._closed = False

    async def start(self) -> None:
        diagnose = await bluetooth_diagnose()
        if not diagnose["available"]:
            raise RuntimeError(diagnose["reason"] or "蓝牙后端不可用")
        if self.mac is None:
            self.mac = await bluetooth_address(self.udid)
        bridge = _bridge_runtime()
        cert = os.environ["ANYLOC_GO_CATCHER_CERT"]
        chip = os.environ["ANYLOC_GO_CATCHER_CHIP_INFO"]
        certifier = _certifier_command()
        env = {
            **os.environ,
            "ANYLOC_BT_DYLIB": str(_dylib_path()),
            "ANYLOC_BT_CERTIFY_CMD": certifier,
        }
        logger.info("starting iAP2 bridge for %s", self.mac)
        self.process = await asyncio.create_subprocess_exec(
            "arch", "-x86_64", str(bridge), "connect", self.mac, cert, chip,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="anyloc-bt-stderr")
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=45)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise RuntimeError("iAP2 连接超时；请保持设备在蓝牙设置页面并确认 Go Catcher 认证") from exc
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            await self.close()
            raise RuntimeError("iAP2 bridge 未返回 ready；" + " | ".join(self._stderr_lines[-5:]))
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            await self.close()
            raise RuntimeError(f"iAP2 bridge 返回异常: {text}") from exc
        if not payload.get("ok") or payload.get("type") != "ready":
            await self.close()
            raise RuntimeError(f"iAP2 认证失败: {payload}")
        self._connected = True

    async def _read_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_lines.append(text)
                self._stderr_lines = self._stderr_lines[-50:]
                logger.info("[bt-bridge] %s", text)

    async def set_location(self, latitude: float, longitude: float) -> None:
        if not self._connected:
            await self.start()
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("iAP2 bridge 未运行")
        async with self._lock:
            self.process.stdin.write(f"{float(latitude):.8f} {float(longitude):.8f}\n".encode())
            await self.process.stdin.drain()
            line = await asyncio.wait_for(self.process.stdout.readline(), timeout=8)
        if not line:
            raise RuntimeError("iAP2 bridge 已退出；" + " | ".join(self._stderr_lines[-5:]))
        payload = json.loads(line.decode("utf-8", errors="replace"))
        if not payload.get("ok"):
            raise RuntimeError(f"iAP2 定位写入失败: {payload}")

    async def clear(self) -> None:
        if self.process is not None and self.process.stdin is not None:
            try:
                self.process.stdin.write(b"quit\n")
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except Exception:
                pass
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connected = False
        if self.process is not None:
            if self.process.returncode is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
            self.process = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "udid": self.udid,
            "product_type": None,
            "product_version": None,
            "connection_type": "Bluetooth",
            "transport": "iap2-rfcomm-gprmc",
            "developer_mode": None,
        }
