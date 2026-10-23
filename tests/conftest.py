import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from practice_attendance.server import create_app
from practice_attendance.tokens import staff_token

EXAMPLE = Path(__file__).parent.parent / "example"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 23, 12, 0, tzinfo=UTC))


@pytest.fixture
def pages(tmp_path):
    """Stand-in pages: the real ones belong to the front end."""
    static = tmp_path / "static"
    static.mkdir()
    for name in ("list", "practice", "stats", "notfound"):
        (static / f"{name}.html").write_text(f"<p>{name}</p>")
    return static


@pytest.fixture
def home(tmp_path):
    """A copy of the example team, salt and calendar."""
    home = tmp_path / "teams"
    shutil.copytree(EXAMPLE, home)
    return home


@pytest.fixture
def make_client(home, tmp_path, pages, clock):
    def make(**kwargs):
        app = create_app(home / "team.json", tmp_path / "data", clock, pages)
        return TestClient(app, **kwargs)

    return make


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def tok(home):
    return staff_token((home / "salt").read_text().strip())


@pytest.fixture
def api(client, tok):
    return f"/api/{tok}"
