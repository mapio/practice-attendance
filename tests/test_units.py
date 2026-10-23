import json

import pytest

from practice_attendance.limiter import Limiter
from practice_attendance.team import ConfigError, parse
from practice_attendance.tokens import ALPHABET, read_salt, staff_token


def test_token_is_six_letters_of_the_alphabet_and_depends_on_the_salt():
    a, b = staff_token("one"), staff_token("two")
    assert len(a) == 6 and set(a) <= set(ALPHABET)
    assert a == staff_token("one") and a != b


def test_salt_is_stripped_and_must_not_be_empty(tmp_path):
    (tmp_path / "salt").write_text("  s3cret \n")
    assert read_salt(tmp_path / "salt") == "s3cret"
    (tmp_path / "salt").write_text(" \n")
    with pytest.raises(ValueError, match="empty"):
        read_salt(tmp_path / "salt")
    with pytest.raises(ValueError, match="cannot read"):
        read_salt(tmp_path / "missing")


def test_limiter_counts_per_address_and_forgets_after_the_window():
    now = [0.0]
    limiter = Limiter(3, 60, lambda: now[0])
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b")
    now[0] = 60
    assert limiter.allow("a")


TEAM = {
    "name": "T",
    "lang": "it",
    "timezone": "Europe/Rome",
    "season": {"from": "2026-09-01", "to": "2027-06-15"},
    "roster": {"1": "Anna"},
}


def test_valid_team_parses_and_calendar_is_optional():
    team = parse(TEAM)
    assert team.calendar is None and "calendar" not in team.public()


@pytest.mark.parametrize(
    ("change", "path"),
    [
        ({"name": ""}, "$.name"),
        ({"timezone": "Mars/Base"}, "$.timezone"),
        ({"season": {"from": "2026-09-01", "to": "bad"}}, "$.season.to"),
        ({"season": {"from": "2027-01-01", "to": "2026-01-01"}}, "$.season"),
        ({"calendar": {"url": "x"}}, "$.calendar.title"),
        ({"roster": {}}, "$.roster"),
        ({"roster": {"1": ""}}, "$.roster.1"),
        ({"lang": "xx"}, "$.lang"),
    ],
)
def test_invalid_team_names_the_path(change, path):
    with pytest.raises(ConfigError, match=path.replace("$", r"\$")):
        parse({**TEAM, **change}, languages={"it", "en"})


def test_server_does_not_start_without_salt(home, tmp_path, pages):
    from practice_attendance.server import create_app

    (home / "salt").unlink()
    with pytest.raises(ValueError, match="salt"):
        create_app(home / "team.json", tmp_path / "data", static=pages)


def test_reload_keeps_the_previous_team_when_the_new_file_is_refused(client, api, home):
    path = home / "team.json"
    team = json.loads(path.read_text())
    team["name"] = "Nuova"
    path.write_text(json.dumps(team))
    assert client.get(api).json()["team"]["name"] == "Nuova"
    path.write_text("{not json")
    assert client.get(api).json()["team"]["name"] == "Nuova"


def test_reload_refuses_a_file_dropping_a_player_with_attendance(
    client, api, home, clock
):
    client.post(f"{api}/practices", json={"start": "2026-10-23T10:00:00Z"})
    client.put(f"{api}/1/attendance", json={"attendance": {"12": "present"}})
    path = home / "team.json"
    team = json.loads(path.read_text())
    del team["roster"]["12"]
    team["name"] = "Senza"
    path.write_text(json.dumps(team))
    body = client.get(api).json()
    assert body["team"]["name"] != "Senza"
