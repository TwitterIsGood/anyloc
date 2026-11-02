from __future__ import annotations

import os
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

def _find_port(start: int = 8787) -> int:
    """Pick the first free localhost port so a second copy can still launch."""
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("没有找到可用的本地端口（8787-8886）")


def _probe_server(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.35) as response:
            data = response.read(4096)
        status = json.loads(data)
        return isinstance(status, dict) and "keepalive_seconds" in status and "route_playing" in status
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _find_existing_server(start: int = 8787) -> int | None:
    for port in range(start, start + 100):
        if _probe_server(port):
            return port
    return None


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _probe_server(port):
            return True
        time.sleep(0.2)
    return False


def _run_server(port: int) -> None:
    import uvicorn

    from anyloc.server import create_app

    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("ANYLOC_LOG_LEVEL", "warning"),
        access_log=False,
    )


def _launch_server(port: int) -> subprocess.Popen[bytes]:
    log_dir = Path.home() / "Library" / "Logs" / "AnyLoc"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "server.log").open("ab")
    command = [sys.executable, "--server", str(port)] if getattr(sys, "frozen", False) else [sys.executable, __file__, "--server", str(port)]
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        cwd=str(Path.home()),
    )
    log_file.close()
    return child


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--server":
        _run_server(int(sys.argv[2]))
        return

    requested = int(os.environ.get("ANYLOC_PORT", "8787"))
    existing = None if os.environ.get("ANYLOC_IGNORE_EXISTING") == "1" else _find_existing_server(requested)
    if existing is not None:
        if os.environ.get("ANYLOC_NO_OPEN") != "1":
            webbrowser.open(f"http://127.0.0.1:{existing}/")
        return

    port = _find_port(requested)
    url = f"http://127.0.0.1:{port}/"
    child = _launch_server(port)
    if not _wait_for_server(port):
        child.terminate()
        raise RuntimeError(f"AnyLoc 服务启动失败，详见 ~/Library/Logs/AnyLoc/server.log（端口 {port}）")
    if os.environ.get("ANYLOC_NO_OPEN") != "1":
        webbrowser.open(url)


if __name__ == "__main__":
    # Keep the short-lived launcher predictable when Finder or a shell stops it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    main()
