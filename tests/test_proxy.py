"""The limiter behind a proxy, against the real server process: the header a
client sends cannot choose the address it is counted under."""

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

EXAMPLE = Path(__file__).parent.parent / "example"


@pytest.fixture
def live(tmp_path):
    """The server with `--proxy-headers` on a free loopback port, as the unit runs it."""
    shutil.copytree(EXAMPLE, tmp_path / "teams")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "practice_attendance",
            "serve",
            "--team",
            str(tmp_path / "teams" / "team.json"),
            "--data",
            str(tmp_path / "data"),
            "--logs",
            str(tmp_path / "logs"),
            "--port",
            str(port),
            "--proxy-headers",
        ],
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{base}/favicon.ico", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("the server did not start")
        yield base
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_a_forged_forwarded_address_does_not_escape_the_limit(live):
    """A tunnel appends the visitor's address to what the visitor sent: the
    leftmost entry is the visitor's to choose, the last one is the tunnel's."""

    def ask(i):
        forwarded = f"198.51.100.{i}, 203.0.113.7"  # forged, then the real one
        return httpx.get(
            f"{live}/api/zzzz{i:02d}", headers={"X-Forwarded-For": forwarded}, timeout=5
        ).status_code

    assert [ask(i) for i in range(10)] == [404] * 10
    assert ask(10) == 429
