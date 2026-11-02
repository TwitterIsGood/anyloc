from __future__ import annotations

import asyncio
import logging

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.usbmux import list_devices

from .models import DeviceInfo

logger = logging.getLogger(__name__)


async def discover_devices() -> list[DeviceInfo]:
    records = await list_devices()
    grouped: dict[str, list[str]] = {}
    for record in records:
        grouped.setdefault(record.serial, [])
        connection_type = str(record.connection_type)
        if connection_type not in grouped[record.serial]:
            grouped[record.serial].append(connection_type)

    devices: list[DeviceInfo] = []
    for udid, connection_types in grouped.items():
        preferred = "USB" if "USB" in connection_types else connection_types[0] if connection_types else None
        item = DeviceInfo(udid=udid, connection_types=connection_types)
        lockdown = None
        try:
            lockdown = await create_using_usbmux(
                serial=udid,
                autopair=True,
                connection_type=preferred,
                pair_timeout=8,
            )
            values = lockdown.all_values
            item.name = values.get("DeviceName") or values.get("DeviceClass") or "Unknown"
            item.product_type = values.get("ProductType") or "Unknown"
            item.product_version = values.get("ProductVersion") or "Unknown"
            item.trusted = bool(values.get("TrustedHostAttached", False))
            try:
                item.developer_mode = bool(await lockdown.get_developer_mode_status())
            except Exception:
                item.developer_mode = None
        except Exception as exc:
            item.error = f"{type(exc).__name__}: {exc}"
            logger.debug("device info failed for %s", udid, exc_info=True)
        finally:
            if lockdown is not None:
                try:
                    await lockdown.close()
                except Exception:
                    pass
        devices.append(item)

    devices.sort(key=lambda d: (0 if "USB" in d.connection_types else 1, d.name.lower(), d.udid))
    return devices


async def first_available_device() -> DeviceInfo | None:
    devices = await discover_devices()
    return devices[0] if devices else None


async def device_version(udid: str, connection_type: str | None = None) -> str:
    lockdown = await create_using_usbmux(
        serial=udid,
        autopair=True,
        connection_type=connection_type,
        pair_timeout=8,
    )
    try:
        return lockdown.product_version
    finally:
        await lockdown.close()


def version_major(version: str) -> int:
    try:
        return int(version.split(".", 1)[0])
    except (ValueError, AttributeError):
        return 0


async def sleep_forever() -> None:
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise
