from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import webbrowser
from pathlib import Path

import typer
import uvicorn

from .bluetooth import bluetooth_diagnose, probe_bluetooth
from .device import discover_devices
from .server import create_app
from .state import AppState

app = typer.Typer(help="AnyLoc: local iOS location controller", no_args_is_help=True)


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@app.command("web")
def web(
    host: str = typer.Option("127.0.0.1", help="Listen address"),
    port: int = typer.Option(8787, help="Listen port"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser"),
    log_level: str = typer.Option("info", help="uvicorn log level"),
) -> None:
    """Start the local control UI."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://{host}:{port}"
    if not no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    typer.echo(f"AnyLoc UI: {url}")
    uvicorn.run(create_app(), host=host, port=port, log_level=log_level)


@app.command("devices")
def devices() -> None:
    """List connected iOS devices."""
    typer.echo(_json([d.to_dict() for d in asyncio.run(discover_devices())]))


@app.command("doctor")
def doctor() -> None:
    """Show native and Bluetooth backend diagnostics."""
    async def run():
        return {
            "devices": [d.to_dict() for d in await discover_devices()],
            "bluetooth": await bluetooth_diagnose(),
        }

    typer.echo(_json(asyncio.run(run())))


@app.command("bt")
def bt() -> None:
    """Show the Bluetooth/Go Catcher backend status."""
    typer.echo(_json(asyncio.run(bluetooth_diagnose())))


@app.command("bt-probe")
def bt_probe(
    udid: str | None = typer.Option(None, help="Read the Bluetooth address from this UDID"),
    mac: str | None = typer.Option(None, help="Bluetooth MAC address, e.g. AA:BB:CC:DD:EE:FF"),
) -> None:
    """Open the RFCOMM channel without performing iAP2 authentication."""
    typer.echo(_json(asyncio.run(probe_bluetooth(mac=mac, udid=udid))))


@app.command("test")
def test(
    udid: str = typer.Option(..., help="Target device UDID"),
    latitude: float = typer.Option(39.9042, help="Test latitude"),
    longitude: float = typer.Option(116.4074, help="Test longitude"),
    connection_type: str = typer.Option("USB", help="USB or Network"),
    hold: float = typer.Option(2.0, help="Seconds to hold before clearing"),
) -> None:
    """Connect, set one coordinate, then clear it. Useful for backend validation."""
    async def run():
        state = AppState()
        try:
            await state.connect(udid=udid, connection_type=connection_type, keepalive_seconds=0)
            typer.echo(_json(await state.set_location(latitude, longitude)))
            await asyncio.sleep(max(0.0, hold))
            typer.echo(_json(await state.clear()))
        finally:
            await state.shutdown()

    asyncio.run(run())


@app.command("version")
def version() -> None:
    from . import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
