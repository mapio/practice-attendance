"""Several teams on one server: each its own file, data, tokens and calendar."""

import json

import pytest

from practice_attendance import server
from practice_attendance.team import ConfigError
from practice_attendance.tokens import player_token, staff_token

START = "2026-10-23T18:00:00Z"  # the clock is the 23rd, 12:00Z


@pytest.fixture
def salt(home):
    return (home / "salt").read_text().strip()


@pytest.fixture
def esempio(salt):
    return f"/api/{staff_token(salt, 'esempio')}"


@pytest.fixture
def prova(salt):
    return f"/api/{staff_token(salt, 'prova')}"


def test_each_team_has_its_own_practices_and_roster(client, esempio, prova):
    assert client.get(esempio).json()["team"]["name"] == "Squadra Esempio"
    assert client.get(prova).json()["team"]["name"] == "Squadra Prova"
    client.post(f"{esempio}/practices", json={"start": START})
    assert len(client.get(esempio).json()["practices"]) == 1
    assert client.get(prova).json()["practices"] == []
    # the same practice id and player id mean another practice and another player
    client.post(f"{prova}/practices", json={"start": START})
    client.put(f"{esempio}/1/attendance", json={"attendance": {"1": "present"}})
    client.put(f"{prova}/1/attendance", json={"attendance": {"1": "absent"}})
    assert client.get(f"{esempio}/1").json()["roster"]["1"] == "Anna"
    assert client.get(f"{prova}/1").json()["roster"]["1"] == "Agnese"
    assert client.get(f"{esempio}/1").json()["attendance"] == {"1": "present"}
    assert client.get(f"{prova}/1").json()["attendance"] == {"1": "absent"}


def test_the_data_of_each_team_is_in_a_directory_of_its_own(client, esempio, tmp_path):
    client.post(f"{esempio}/practices", json={"start": START})
    assert (tmp_path / "data" / "esempio" / "practices.json").exists()
    assert (tmp_path / "data" / "esempio" / "journal.jsonl").exists()
    assert not (tmp_path / "data" / "prova").exists()


def test_a_players_token_opens_only_her_own_team(client, salt, esempio, prova):
    client.post(f"{prova}/practices", json={"start": START})
    client.put(f"{prova}/1/attendance", json={"attendance": {"1": "present"}})
    mine = player_token(salt, "prova", "1")
    other = player_token(salt, "esempio", "1")
    assert mine != other
    assert client.get(f"/api/{mine}").json()["player"]["name"] == "Agnese"
    assert client.get(f"/api/{other}").json()["player"]["name"] == "Anna"
    assert client.get(f"/api/{other}").json()["practices"] == []
    # a player of one team sees no staff path of the other
    assert client.get(f"/api/{mine}/1").status_code == 404


def test_a_team_without_a_calendar_works_among_teams_with_one(client, esempio, prova):
    assert client.post(f"{prova}/import").status_code == 409
    assert client.post(f"{prova}/import").json()["error"] == "no_calendar"
    assert client.get(prova).json()["imported_at"] is None
    # manual practices are the way in, and the other team's import is its own
    added = client.post(f"{prova}/practices", json={"start": START, "location": "X"})
    assert added.status_code == 201 and added.json()["source"] == "manual"
    assert client.post(f"{esempio}/import").status_code == 200
    assert client.get(esempio).json()["imported_at"] is not None
    assert client.get(prova).json()["imported_at"] is None
    assert [p["source"] for p in client.get(prova).json()["practices"]] == ["manual"]
    assert all(
        p["source"] == "calendar" for p in client.get(esempio).json()["practices"]
    )


def test_links_are_those_of_the_team_of_the_token(client, salt, esempio, prova):
    body = client.get(f"{prova}/links").json()
    assert [p["name"] for p in body["players"]][:2] == ["Agnese", "Benedetta"]
    assert body["players"][0]["token"] == player_token(salt, "prova", "1")
    assert len(client.get(f"{esempio}/links").json()["players"]) == 12


def test_pages_and_manifests_of_every_team(client, salt):
    for team in ("esempio", "prova"):
        token = staff_token(salt, team)
        assert client.get(f"/{token}/").status_code == 200
        manifest = client.get(f"/{token}/manifest.webmanifest").json()
        assert manifest["scope"] == f"/{token}/"


def test_the_old_unseeded_tokens_open_nothing(client, salt):
    from practice_attendance.tokens import _token

    assert client.get(f"/api/{_token(f'{salt}:staff')}").status_code == 404


def test_tokens_that_collide_between_teams_stop_the_start(
    home, tmp_path, pages, monkeypatch
):
    # the same player id gives the same token in both teams
    monkeypatch.setattr(
        server, "player_token", lambda salt, team, player: player.rjust(6, "a")
    )
    with pytest.raises(ConfigError, match=r"prova: \$: a token is also .* esempio"):
        server.create_app(home, tmp_path / "data", static=pages)


def test_a_reload_whose_tokens_collide_with_another_team_is_refused(
    make_client, home, salt, esempio, prova, monkeypatch
):
    client = make_client()
    taken = player_token(salt, "esempio", "1")
    real = server.player_token
    monkeypatch.setattr(
        server,
        "player_token",
        lambda s, team, player: (
            taken if (team, player) == ("prova", "99") else real(s, team, player)
        ),
    )
    path = home / "prova.json"
    team = json.loads(path.read_text())
    team["roster"]["99"] = "Zoe"
    team["name"] = "Collide"
    path.write_text(json.dumps(team))
    assert client.get(prova).json()["team"]["name"] == "Squadra Prova"
    assert client.get(f"/api/{taken}").json()["player"]["name"] == "Anna"
    # a file that does not collide is accepted, the others untouched
    del team["roster"]["99"]
    team["name"] = "Nuova"
    path.write_text(json.dumps(team))
    assert client.get(prova).json()["team"]["name"] == "Nuova"
    assert client.get(esempio).json()["team"]["name"] == "Squadra Esempio"


def test_a_new_player_in_a_team_file_has_a_working_token_at_once(client, home, salt):
    path = home / "prova.json"
    team = json.loads(path.read_text())
    team["roster"]["7"] = "Giorgia"
    path.write_text(json.dumps(team))
    token = player_token(salt, "prova", "7")
    assert client.get(f"/api/{token}").json()["player"]["name"] == "Giorgia"


def test_a_team_file_added_while_running_is_served_after_a_restart(
    make_client, home, salt
):
    client = make_client()
    (home / "terza.json").write_text((home / "prova.json").read_text())
    token = staff_token(salt, "terza")
    assert client.get(f"/api/{token}").status_code == 404
    assert make_client().get(f"/api/{token}").status_code == 200


def test_one_refused_team_file_leaves_the_others_serving(client, home, esempio, prova):
    (home / "prova.json").write_text("{not json")
    assert client.get(prova).json()["team"]["name"] == "Squadra Prova"
    assert client.get(esempio).json()["team"]["name"] == "Squadra Esempio"
