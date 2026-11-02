"""Check the packaged HTTP service and second launch without a phone/browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

app = Path(sys.argv[1]).resolve()
executable = app / "Contents/MacOS/AnyLoc"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
env = {**os.environ, "ANYLOC_PORT": str(port), "ANYLOC_NO_OPEN": "1"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def request(path):
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
        return response.read()

def service_pid():
    return int(subprocess.check_output([
        "/usr/sbin/lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"
    ], text=True).strip())

pid = None
try:
    subprocess.run([str(executable)], env={**env, "ANYLOC_IGNORE_EXISTING": "1"},
                   check=True, timeout=60)
    pid = service_pid()
    assert json.loads(request("/api/status"))["connected"] is False
    assert b"<title>AnyLoc</title>" in request("/")
    subprocess.run([str(executable)], env=env, check=True, timeout=20)
    assert service_pid() == pid, "Second launch created another server"
    assert (app / "Contents/Resources/anyloc/web/index.html").is_file()
    print("PASS: bundled server, web UI, resources and repeat launch")
except Exception:
    log = Path.home() / "Library/Logs/AnyLoc/server.log"
    if log.exists():
        print(log.read_text(errors="replace")[-16000:], file=sys.stderr)
    raise
finally:
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
