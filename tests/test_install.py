"""The app can be put on a phone's home screen: a manifest for each token, the
icons, and the page that says how. Nothing is stored on the device."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from practice_attendance.server import STATIC, UNKNOWN_TOKEN_LIMIT, create_app
from practice_attendance.tokens import player_token

NAMES = ("icon-192.png", "icon-512.png", "icon-maskable-512.png")
NEUTRAL = {name: (STATIC / "icons" / name).read_bytes() for name in NAMES}


@pytest.fixture
def real(home, tmp_path, clock):
    """A client on the real pages, which the install page and the icons need."""
    return TestClient(create_app(home / "team.json", tmp_path / "data", clock, STATIC))


@pytest.fixture
def tokens(home, tok):
    salt = (home / "salt").read_text().strip()
    team = json.loads((home / "team.json").read_text())
    return {"staff": tok, "athlete": player_token(salt, next(iter(team["roster"])))}


def manifest_of(client, token):
    return client.get(f"/{token}/manifest.webmanifest")


def test_every_token_gets_a_manifest_that_starts_on_its_own_page(real, tokens):
    for kind, token in tokens.items():
        answer = manifest_of(real, token)
        assert answer.status_code == 200, kind
        assert answer.headers["content-type"] == "application/manifest+json"
        assert answer.headers["cache-control"] == "no-cache"
        body = answer.json()
        assert (body["name"], body["short_name"]) == (
            "PracticeAttendance",
            "Attendance",
        )
        assert body["display"] == "standalone"
        # a directory, and not the whole site: one app's links are not another's
        assert body["start_url"] == body["id"] == body["scope"] == f"/{token}/"
        assert {
            (i["src"], i["sizes"], i.get("purpose", "any")) for i in body["icons"]
        } == {
            ("/icons/icon-192.png", "192x192", "any"),
            ("/icons/icon-512.png", "512x512", "any"),
            ("/icons/icon-maskable-512.png", "512x512", "maskable"),
        }
        for icon in body["icons"]:
            assert real.get(icon["src"]).headers["content-type"] == icon["type"]


def test_the_page_answers_at_its_directory_too(real, tokens):
    for token in tokens.values():
        answer = real.get(f"/{token}/", follow_redirects=False)
        assert answer.status_code == 200
        assert answer.text == real.get(f"/{token}").text
    assert real.get("/nosuchtoken/", follow_redirects=False).status_code == 404


def test_the_manifest_does_not_take_the_place_of_a_practice(client, tok):
    client.post(f"/api/{tok}/practices", json={"start": "2026-10-23T10:00:00Z"})
    assert "<p>practice</p>" in client.get(f"/{tok}/1").text
    assert manifest_of(client, tok).status_code == 200


def test_an_unknown_token_gets_the_usual_refusal_and_counts_against_the_limit(real):
    for i in range(UNKNOWN_TOKEN_LIMIT):
        answer = manifest_of(real, f"zzzz{i:02d}")
        assert answer.status_code == 404 and "notfound" in answer.text
    assert manifest_of(real, "zzzz99").status_code == 429
    assert real.get("/zzzz98").status_code == 429  # one count, shared with the pages


def test_a_known_token_is_never_limited_even_while_blocked(real, tokens):
    for i in range(UNKNOWN_TOKEN_LIMIT + 1):
        manifest_of(real, f"zzzz{i:02d}")
    for token in tokens.values():
        assert manifest_of(real, token).status_code == 200


def test_the_install_page_serves_without_a_token_and_is_not_counted(real):
    for _ in range(UNKNOWN_TOKEN_LIMIT + 5):
        answer = real.get("/install")
        assert answer.status_code == 200 and 'data-page="install"' in answer.text
    assert answer.headers["cache-control"] == "no-cache"
    assert "manifest" not in answer.text
    assert real.get("/api/zzzzzz").status_code == 404  # the limit is untouched


def test_the_icons_are_the_neutral_ones_unless_the_team_directory_has_its_own(
    real, home
):
    for name, neutral in NEUTRAL.items():
        answer = real.get(f"/icons/{name}")
        assert answer.status_code == 200 and answer.content == neutral
        assert answer.headers["content-type"] == "image/png"
    (home / "icon-192.png").write_bytes(b"team icon")
    assert real.get("/icons/icon-192.png").content == b"team icon"
    assert real.get("/icons/icon-512.png").content == NEUTRAL["icon-512.png"]


def test_only_the_named_icons_are_served(real, home):
    (home / "secret.png").write_bytes(b"x")
    for name in ("secret.png", "icon.svg", "icon-maskable.svg", "..%2Fsalt"):
        assert real.get(f"/icons/{name}").status_code == 404


def test_the_pages_link_the_manifest_but_the_error_and_install_pages_do_not():
    static = STATIC
    script = (static / "app.js").read_text()
    assert '["notfound", "install"].includes(document.body.dataset.page)' in script
    assert "/manifest.webmanifest" in script
    for page in ("list", "practice", "stats", "athlete", "links"):
        html = (static / f"{page}.html").read_text()
        assert "manifest" not in html and f'data-page="{page}"' in html


def test_every_string_of_the_install_page_exists_in_every_language():
    languages = json.loads((STATIC / "i18n.json").read_text(encoding="utf-8"))
    script = (STATIC / "app.js").read_text()
    used = set(re.findall(r'\bt\("(install_\w+)"', script))
    assert used
    for lang, keys in languages.items():
        assert used <= set(keys), lang
    assert len({frozenset(keys) for keys in languages.values()}) == 1
