import json
from datetime import timedelta

import pytest

from practice_attendance import __main__ as cli
from practice_attendance import server
from practice_attendance.stats import percent
from practice_attendance.team import ConfigError
from practice_attendance.tokens import ALPHABET, player_token, staff_token

A, B, C = (f"2026-10-{d}T18:00:00Z" for d in (20, 21, 22))  # the clock: 23rd, 12:00Z


def error(response, status, code):
    assert response.status_code == status
    assert response.json()["error"] == code


@pytest.fixture
def salt(home):
    return (home / "salt").read_text().strip()


@pytest.fixture
def record(client, api, salt):
    """Anna (1), Bianca (2) and Chiara (3) over three practices: Chiara is named
    in the first only, Bianca not in the third's record, which nobody took."""
    for start in (A, B, C):
        client.post(f"{api}/practices", json={"start": start})
    put = lambda p, a: client.put(f"{api}/{p}/attendance", json={"attendance": a})
    put(1, {"1": "present", "2": "absent", "3": "present"})
    put(2, {"1": "absent", "2": "present"})

    def get(player):
        return client.get(f"/api/{player_token(salt, player)}")

    return get


def test_a_players_token_is_six_letters_of_her_own(salt):
    tokens = {player_token(salt, i) for i in "123"} | {staff_token(salt)}
    assert len(tokens) == 4
    assert all(len(t) == 6 and set(t) <= set(ALPHABET) for t in tokens)


def test_record_is_hers_with_only_the_teams_percentage_of_the_others(record):
    r = record("1")
    assert r.status_code == 200
    body = r.json()
    assert body["player"] == {"id": "1", "name": "Anna"}
    assert (body["present"], body["of"], body["percent"]) == (1, 2, 50)
    assert body["team_percent"] == 60  # 3 present of 5 named
    assert [(p["start"], p["status"]) for p in body["practices"]] == [
        (B, "absent"),  # newest first, and the one nobody took is not there
        (A, "present"),
    ]
    assert body["team"]["name"]
    assert "Bianca" not in r.text and "Chiara" not in r.text


def test_a_practice_that_does_not_name_her_is_not_in_her_record(record):
    body = record("3").json()
    assert (body["present"], body["of"], body["percent"]) == (1, 1, 100)
    assert [p["start"] for p in body["practices"]] == [A]
    empty = record("4").json()
    assert (empty["of"], empty["percent"], empty["practices"]) == (0, None, [])


def test_an_excused_practice_is_an_absence_in_the_ratio_and_shown_apart(
    client, api, record
):
    client.put(
        f"{api}/2/attendance",
        json={"attendance": {"1": "excused", "2": "present"}},
    )
    body = record("1").json()
    assert (body["present"], body["excused"], body["of"]) == (1, 1, 2)
    assert body["percent"] == 50  # as if she had been absent
    assert body["team_percent"] == 60  # 3 present of 5 named, as before
    assert [(p["start"], p["status"]) for p in body["practices"]] == [
        (B, "excused"),
        (A, "present"),
    ]
    assert record("2").json()["excused"] == 0


def test_the_page_and_the_record_answer_to_the_players_token_alone(
    client, record, salt, tok
):
    mine = player_token(salt, "1")
    assert client.get(f"/{mine}").text == "<p>athlete</p>"
    assert client.get(f"/{tok}").text == "<p>list</p>"
    assert client.get(f"/{mine.upper()}").text == "<p>athlete</p>"


def test_a_players_token_opens_nothing_else_and_is_not_counted(client, record, salt):
    mine = player_token(salt, "1")
    paths = [
        ("get", f"/api/{mine}/stats"),
        ("get", f"/api/{mine}/1"),
        ("get", f"/{mine}/stats"),
        ("get", f"/{mine}/1"),
        ("post", f"/api/{mine}/import"),
        ("post", f"/api/{mine}/practices"),
        ("put", f"/api/{mine}/1/attendance"),
        ("patch", f"/api/{mine}/1"),
        ("delete", f"/api/{mine}/1"),
    ]
    for _ in range(2):  # more than the limit of unknown tokens allows
        for method, path in paths:
            assert getattr(client, method)(path).status_code == 404, path
    assert client.get(f"/api/{mine}").status_code == 200
    assert client.get("/api/zzzzzz").status_code == 404  # not limited yet


def test_a_roster_whose_tokens_collide_is_refused(home, tmp_path, pages, monkeypatch):
    monkeypatch.setattr(server, "player_token", lambda salt, player: "aaaaaa")
    with pytest.raises(ConfigError, match=r"\$\.roster\.2"):
        server.create_app(home / "team.json", tmp_path / "data", static=pages)


def test_percent_rounds_halves_up_and_has_none_without_practices():
    assert [percent(p, 3) for p in range(4)] == [0, 33, 67, 100]
    assert percent(1, 8) == 13 and percent(1, 2) == 50
    assert percent(0, 0) is None


def test_urls_prints_the_staff_link_and_with_players_each_players(home, salt, capsys):
    team = str(home / "team.json")
    assert cli.main(["urls", "--team", team, "--base", "https://x.test/"]) == 0
    assert capsys.readouterr().out == f"https://x.test/{staff_token(salt)}/\n"
    assert (
        cli.main(["urls", "--team", team, "--base", "https://x.test", "--players"]) == 0
    )
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"https://x.test/{staff_token(salt)}/"
    assert lines[1] == f"Anna: https://x.test/{player_token(salt, '1')}/"
    assert len(lines) == 1 + 12


# the notice and the links


def seed(client, api, start):
    client.post(f"{api}/practices", json={"start": start})


def test_next_is_the_first_coming_practice_not_cancelled(client, api, salt, clock):
    seed(client, api, "2026-10-24T18:00:00Z")
    seed(client, api, "2026-10-22T18:00:00Z")  # past
    body = client.get(f"/api/{player_token(salt, '1')}").json()
    assert body["next"] == {
        "id": 1,
        "start": "2026-10-24T18:00:00Z",
        "end": "2026-10-24T19:00:00Z",
        "location": "",
        "notice_opens_at": "2026-10-24T12:00:00Z",
        "notice_open": False,
        "announced": False,
    }


def test_no_next_without_a_coming_practice(client, salt):
    assert client.get(f"/api/{player_token(salt, '1')}").json()["next"] is None


def test_notice_follows_its_window(client, api, salt, clock):
    mine = f"/api/{player_token(salt, '1')}"
    seed(client, api, "2026-10-23T18:30:00Z")  # the clock stands at 12:00Z
    error(client.put(f"{mine}/1/absence"), 409, "notice_not_open_yet")
    clock.t += timedelta(minutes=29)  # 12:29Z: a minute before it opens
    error(client.put(f"{mine}/1/absence"), 409, "notice_not_open_yet")
    clock.t += timedelta(minutes=1)  # 18:30Z minus 6h is 12:30Z: just open
    r = client.put(f"{mine}/1/absence")
    assert r.status_code == 200 and r.json()["next"]["announced"] is True
    assert r.json()["next"]["notice_open"] is True
    first = client.get(f"{api}/1").json()["announced"]
    assert first == ["1"]
    clock.t += timedelta(hours=1)
    assert client.put(f"{mine}/1/absence").status_code == 200  # again: no change
    r = client.delete(f"{mine}/1/absence")
    assert r.json()["next"]["announced"] is False
    assert client.get(f"{api}/1").json()["announced"] == []
    assert client.delete(f"{mine}/1/absence").status_code == 200  # nothing to do
    clock.t += timedelta(hours=5)  # 13:30Z plus 5h: 18:30Z, the start
    error(client.put(f"{mine}/1/absence"), 409, "notice_closed")


def test_announcing_twice_keeps_the_first_instant(client, api, salt, clock, home):
    mine = f"/api/{player_token(salt, '1')}"
    seed(client, api, "2026-10-23T18:00:00Z")
    client.put(f"{mine}/1/absence")
    clock.t += timedelta(hours=2)
    client.put(f"{mine}/1/absence")
    data = json.loads((home.parent / "data" / "practices.json").read_text())
    assert data["practices"][0]["absences"] == {"1": "2026-10-23T12:00:00Z"}


def test_a_notice_is_information_for_the_staff_alone(client, api, salt, clock):
    mine = f"/api/{player_token(salt, '1')}"
    seed(client, api, "2026-10-23T18:00:00Z")
    client.put(f"{mine}/1/absence")
    listed = client.get(api).json()["practices"][0]
    assert listed["announced"] == 1 and listed["taken"] is False
    clock.t += timedelta(hours=5)  # 17:00Z, the attendance opens
    client.put(f"{api}/1/attendance", json={"attendance": {"1": "present"}})
    assert client.get(f"{api}/1").json()["announced"] == ["1"]  # kept
    stats = client.get(f"{api}/stats").json()
    assert stats["players"][0]["present"] == 1  # the notice did not count


def test_only_a_player_may_announce_and_only_for_herself(client, api, salt, clock, tok):
    seed(client, api, "2026-10-23T18:00:00Z")
    error(client.put(f"/api/{tok}/1/absence"), 404, "not_found")
    error(client.put(f"/api/{player_token(salt, '1')}/9/absence"), 404, "not_found")
    client.put(f"/api/{player_token(salt, '2')}/1/absence")
    assert client.get(f"{api}/1").json()["announced"] == ["2"]


def test_the_links_are_for_the_staff_alone(client, api, tok, salt):
    r = client.get(f"{api}/links")
    players = r.json()["players"]
    assert [p["id"] for p in players] == [str(i) for i in range(1, 13)]
    assert players[0] == {"id": "1", "name": "Anna", "token": player_token(salt, "1")}
    assert client.get(f"/{tok}/links").text == "<p>links</p>"
    mine = player_token(salt, "1")
    error(client.get(f"/api/{mine}/links"), 404, "not_found")
    assert client.get(f"/{mine}/links").status_code == 404
