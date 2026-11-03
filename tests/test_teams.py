"""Several teams on one server: each its own file, data, tokens and calendar."""

import json

import pytest

from practice_attendance import server
from practice_attendance.team import ConfigError
from practice_attendance.tokens import ALPHABET, coach_token, player_token, staff_token

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
    (home / "quarta.json").write_text((home / "prova.json").read_text())
    token = staff_token(salt, "quarta")
    assert client.get(f"/api/{token}").status_code == 404
    assert make_client().get(f"/api/{token}").status_code == 200


def test_one_refused_team_file_leaves_the_others_serving(client, home, esempio, prova):
    (home / "prova.json").write_text("{not json")
    assert client.get(prova).json()["team"]["name"] == "Squadra Prova"
    assert client.get(esempio).json()["team"]["name"] == "Squadra Esempio"


# the coach token: one link for all her teams


@pytest.fixture
def marta(salt):
    return coach_token(salt, "marta")


@pytest.fixture
def paola(salt):
    return coach_token(salt, "paola")


def test_a_coach_token_is_ten_letters_and_the_others_six(salt, marta):
    assert len(marta) == 10 and set(marta) <= set(ALPHABET)
    assert marta != coach_token(salt, "paola")
    assert len(staff_token(salt, "esempio")) == len(player_token(salt, "x", "1")) == 6


def test_a_coach_sees_her_teams_by_id_and_name_and_no_other(client, marta, paola):
    body = client.get(f"/api/{marta}").json()
    assert body == {
        "teams": [
            {"id": "esempio", "name": "Squadra Esempio"},
            {"id": "prova", "name": "Squadra Prova"},
        ]
    }
    assert client.get(f"/api/{paola}").json() == {
        "teams": [{"id": "terza", "name": "Squadra Terza"}]
    }
    assert "Terza" not in client.get(f"/api/{marta}").text


def test_a_coach_has_the_staff_rights_on_each_of_her_teams(client, marta):
    for team, name in (("esempio", "Squadra Esempio"), ("prova", "Squadra Prova")):
        api = f"/api/{marta}/{team}"
        assert client.get(api).json()["team"]["name"] == name
        made = client.post(f"{api}/practices", json={"start": START})
        assert made.status_code == 201
        assert client.get(f"{api}/1").json()["practice"]["id"] == 1
        assert client.get(f"{api}/stats").status_code == 200
        assert len(client.get(f"{api}/links").json()["players"]) > 0
        assert client.post(f"{api}/import").status_code in (200, 409)


def test_a_coach_takes_attendance_on_two_teams_with_data_apart(
    client, marta, esempio, prova, tmp_path
):
    put = lambda team, status: client.put(
        f"/api/{marta}/{team}/1/attendance", json={"attendance": {"1": status}}
    )
    for team in ("esempio", "prova"):
        client.post(f"/api/{marta}/{team}/practices", json={"start": START})
    assert put("esempio", "present").status_code == 200
    assert put("prova", "absent").status_code == 200
    assert client.get(f"{esempio}/1").json()["attendance"] == {"1": "present"}
    assert client.get(f"{prova}/1").json()["attendance"] == {"1": "absent"}
    assert (tmp_path / "data" / "prova" / "practices.json").exists()
    assert not (tmp_path / "data" / "terza").exists()


def test_a_coach_has_nothing_on_a_team_that_does_not_name_her(
    client, marta, paola, salt
):
    for method, path in (
        ("get", f"/api/{marta}/terza"),
        ("get", f"/api/{marta}/terza/stats"),
        ("get", f"/api/{marta}/terza/links"),
        ("post", f"/api/{marta}/terza/practices"),
        ("get", f"/{marta}/terza/"),
        ("get", f"/{marta}/terza/1"),
        ("get", f"/api/{paola}/esempio"),
        ("get", f"/api/{marta}/nosuchteam"),
    ):
        assert getattr(client, method)(path).status_code in (404, 422), path
    assert client.get(f"/api/{marta}/terza").json()["error"] == "not_found"
    assert client.get(f"/api/{paola}/terza").status_code == 200


def test_a_coach_token_without_a_team_opens_only_the_tab_page_and_the_listing(
    client, marta
):
    assert client.get(f"/{marta}/").status_code == 200
    assert client.get(f"/{marta}").status_code == 200
    for path in (
        f"/api/{marta}/stats",
        f"/api/{marta}/links",
        f"/api/{marta}/1",
        f"/api/{marta}/practices",
        f"/{marta}/stats",
        f"/{marta}/links",
        f"/{marta}/1",
    ):
        assert client.get(path).status_code == 404, path
    assert (
        client.post(f"/api/{marta}/practices", json={"start": START}).status_code == 404
    )
    assert (
        client.put(f"/api/{marta}/1/attendance", json={"attendance": {}}).status_code
        == 404
    )


def test_a_coach_wrong_team_counts_against_the_limit_of_unknown_tokens(client, marta):
    from practice_attendance.server import UNKNOWN_TOKEN_LIMIT

    for _ in range(UNKNOWN_TOKEN_LIMIT):
        assert client.get(f"/api/{marta}/terza").status_code == 404
    assert client.get(f"/api/{marta}/terza").status_code == 429


def test_the_team_pages_of_a_coach_are_the_staff_pages(client, marta):
    client.post(f"/api/{marta}/prova/practices", json={"start": START})
    assert "<p>list</p>" in client.get(f"/{marta}/").text
    assert "<p>list</p>" in client.get(f"/{marta}/prova/").text
    assert "<p>list</p>" in client.get(f"/{marta}/prova").text
    assert "<p>practice</p>" in client.get(f"/{marta}/prova/1").text
    assert "<p>stats</p>" in client.get(f"/{marta}/prova/stats").text
    assert "<p>links</p>" in client.get(f"/{marta}/prova/links").text


def test_the_manifest_of_a_coach_covers_all_her_teams(client, marta):
    body = client.get(f"/{marta}/manifest.webmanifest").json()
    assert body["scope"] == body["start_url"] == body["id"] == f"/{marta}/"


def test_the_staff_and_player_tokens_still_work_beside_a_coach(client, salt, esempio):
    assert client.get(esempio).status_code == 200
    mine = player_token(salt, "esempio", "1")
    assert client.get(f"/api/{mine}").status_code == 200
    # and a staff token takes no team id
    assert client.get(f"{esempio}/prova").status_code == 404


def test_a_coach_follows_the_files(make_client, home, salt):
    client = make_client()
    team = json.loads((home / "terza.json").read_text())
    team["coach"] = "marta"
    (home / "terza.json").write_text(json.dumps(team))
    marta = coach_token(salt, "marta")
    ids = [t["id"] for t in client.get(f"/api/{marta}").json()["teams"]]
    assert ids == ["esempio", "prova", "terza"]
    # nobody names paola any more: her token opens nothing
    assert client.get(f"/api/{coach_token(salt, 'paola')}").status_code == 404


def test_a_coach_id_must_be_an_id(home, tmp_path, pages):
    team = json.loads((home / "terza.json").read_text())
    team["coach"] = "Paola Rossi"
    (home / "terza.json").write_text(json.dumps(team))
    with pytest.raises(ConfigError, match=r"terza: \$\.coach"):
        server.create_app(home, tmp_path / "data", static=pages)


def test_a_coach_token_equal_to_another_token_is_refused(
    home, tmp_path, pages, monkeypatch, salt
):
    taken = staff_token(salt, "esempio")
    monkeypatch.setattr(
        server, "coach_token", lambda s, coach: taken if coach == "paola" else "x" * 10
    )
    with pytest.raises(ConfigError, match=r"terza: \$"):
        server.create_app(home, tmp_path / "data", static=pages)


def test_a_reload_naming_a_coach_whose_token_collides_is_refused(
    make_client, home, salt, monkeypatch
):
    client = make_client()
    real = server.coach_token
    taken = player_token(salt, "esempio", "1")
    monkeypatch.setattr(
        server, "coach_token", lambda s, c: taken if c == "zeta" else real(s, c)
    )
    path = home / "prova.json"
    team = json.loads(path.read_text())
    team["coach"] = "zeta"
    team["name"] = "Collide"
    path.write_text(json.dumps(team))
    api = f"/api/{staff_token(salt, 'prova')}"
    assert client.get(api).json()["team"]["name"] == "Squadra Prova"
    assert client.get(f"/api/{taken}").json()["player"]["name"] == "Anna"
