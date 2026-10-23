from datetime import timedelta

import pytest

T = "2026-10-23T18:00:00Z"  # the clock stands at 12:00Z the same day


def add(client, api, start=T, **extra):
    return client.post(f"{api}/practices", json={"start": start, **extra})


def error(response, status, code):
    assert response.status_code == status
    assert response.json()["error"] == code


def test_unknown_token_and_practice_are_the_same_404(client, api):
    error(client.get("/api/zzzzzz"), 404, "not_found")
    error(client.get(f"{api}/99"), 404, "not_found")
    error(client.get(f"{api}/abc"), 404, "not_found")


def test_unknown_tokens_are_limited_but_the_known_one_is_not(client, api):
    codes = [client.get("/api/zzzzzz").status_code for _ in range(12)]
    assert codes == [404] * 10 + [429] * 2
    error(client.get("/api/zzzzzz"), 429, "rate_limited")
    for _ in range(15):
        assert client.get(api).status_code == 200


def test_token_is_case_insensitive(client, api):
    assert client.get(api.upper().replace("/API/", "/api/")).status_code == 200


def test_pages_and_unlimited_paths(client, tok):
    assert client.get(f"/{tok}").text == "<p>list</p>"
    assert client.get(f"/{tok}/stats").text == "<p>stats</p>"
    r = client.get("/zzzzzz")
    assert (r.status_code, r.text) == (404, "<p>notfound</p>")
    assert client.get("/").status_code == 404
    assert client.get("/favicon.ico").status_code == 204
    assert client.get("/privacy").status_code == 404
    for _ in range(15):  # these name no token: never counted
        client.get("/")
        client.get("/favicon.ico")
    assert client.get("/zzzzzz").status_code == 404


def test_page_of_a_practice_needs_it_to_exist(client, api, tok):
    add(client, api)
    assert client.get(f"/{tok}/1").text == "<p>practice</p>"
    assert client.get(f"/{tok}/2").status_code == 404


def test_add_edit_delete_manual_practice(client, api):
    r = add(client, api, location="Palestra")
    assert r.status_code == 201
    p = r.json()
    assert p["end"] == "2026-10-23T19:00:00Z" and p["source"] == "manual"
    assert p["opens_at"] == "2026-10-23T17:00:00Z" and p["open"] is False
    r = client.patch(
        f"{api}/1", json={"location": "Altrove", "end": "2026-10-23T20:00:00Z"}
    )
    assert r.json()["location"] == "Altrove" and r.json()["end"].endswith("20:00:00Z")
    error(client.patch(f"{api}/1", json={"end": T}), 422, "invalid_value")
    assert client.delete(f"{api}/1").json()["practices"] == []
    assert add(client, api).json()["id"] == 2  # ids are never reused


def test_invalid_add(client, api):
    error(client.post(f"{api}/practices", json={}), 422, "invalid_value")
    error(add(client, api, start="soon"), 422, "invalid_value")
    error(add(client, api, start="2026-10-23T18:00:00"), 422, "invalid_value")
    error(add(client, api, end=T), 422, "invalid_value")
    error(client.post(f"{api}/practices", content="[1"), 422, "invalid_value")


def test_attendance_window_follows_the_clock(client, api, clock):
    add(client, api)
    body = {"attendance": {"1": "present", "2": "absent"}}
    error(client.put(f"{api}/1/attendance", json=body), 409, "not_open_yet")
    assert client.get(f"{api}/1").json()["attendance"] is None
    clock.t += timedelta(hours=5)  # 17:00Z, exactly at opens_at
    r = client.put(f"{api}/1/attendance", json=body)
    assert r.status_code == 200 and r.json()["attendance"] == body["attendance"]
    assert r.json()["practice"]["taken"] is True
    clock.t += timedelta(days=30)  # it never locks
    body = {"attendance": {"1": "absent"}}
    assert client.put(f"{api}/1/attendance", json=body).json()["attendance"] == {
        "1": "absent"
    }


def test_attendance_validation(client, api, clock):
    add(client, api, start="2026-10-23T10:00:00Z")
    put = lambda a: client.put(f"{api}/1/attendance", json={"attendance": a})
    error(put({"99": "present"}), 422, "unknown_player")
    error(put({"1": "late"}), 422, "invalid_value")
    error(put({"1": 1}), 422, "invalid_value")
    error(
        client.put(f"{api}/1/attendance", json={"attendance": [1]}),
        422,
        "invalid_value",
    )
    assert client.get(f"{api}/1").json()["attendance"] is None


def test_list_counts_and_delete_rules(client, api):
    add(client, api, start="2026-10-23T10:00:00Z")
    client.put(
        f"{api}/1/attendance",
        json={"attendance": {"1": "present", "2": "absent", "3": "present"}},
    )
    p = client.get(api).json()["practices"][0]
    assert (p["present"], p["of"]) == (2, 3)
    error(client.delete(f"{api}/1"), 409, "has_attendance")


def test_import_endpoint_and_imported_practices_are_protected(client, api):
    r = client.post(f"{api}/import")
    assert r.status_code == 200 and r.json()["added"] > 0 and r.json()["cancelled"] == 0
    assert client.get(api).json()["imported_at"] == "2026-10-23T12:00:00Z"
    error(client.patch(f"{api}/1", json={"location": "x"}), 409, "imported_practice")
    error(client.delete(f"{api}/1"), 409, "imported_practice")


def test_cancelled_practice_refuses_attendance(client, api, home):
    client.post(f"{api}/import")
    first = client.get(api).json()["practices"][0]["id"]
    ics = home / "calendar.ics"
    ics.write_text(
        ics.read_text().replace(
            "RRULE:FREQ=WEEKLY;BYDAY=TU,TH", "RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=0"
        )
    )
    ics.write_text(
        ics.read_text()
        .replace(";COUNT=0", "")
        .replace("UID:allenamento-settimanale", "UID:altro")
    )
    client.post(f"{api}/import")
    error(
        client.put(f"{api}/{first}/attendance", json={"attendance": {}}),
        409,
        "cancelled",
    )


def test_import_without_calendar_and_with_unavailable_one(client, api, home):
    import json

    path = home / "calendar.ics"
    path.write_text("not a calendar")
    error(client.post(f"{api}/import"), 502, "calendar_unavailable")
    path.unlink()
    error(client.post(f"{api}/import"), 502, "calendar_unavailable")
    team = json.loads((home / "team.json").read_text())
    del team["calendar"]
    (home / "team.json").write_text(json.dumps(team))
    error(client.post(f"{api}/import"), 409, "no_calendar")


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-13-01",
        "from=yesterday",
        "to=20261001",
        "from=2026-11-01&to=2026-10-01",
    ],
)
def test_stats_rejects_bad_ranges(client, api, query):
    error(client.get(f"{api}/stats?{query}"), 422, "invalid_value")


def test_stats(client, api, clock):
    add(client, api, start="2026-10-20T16:00:00Z")  # taken
    add(client, api, start="2026-10-21T16:00:00Z")  # past, not taken
    add(client, api, start="2026-10-22T16:00:00Z")  # taken, then cancelled below
    add(
        client, api, start="2026-11-20T16:00:00Z"
    )  # in the future: not counted as a gap
    client.put(
        f"{api}/1/attendance", json={"attendance": {"1": "present", "2": "absent"}}
    )
    stats = client.get(f"{api}/stats").json()
    assert (stats["from"], stats["to"]) == ("2026-09-01", "2027-06-15")
    assert (stats["practices"], stats["not_taken"]) == (1, 2)
    by = {p["id"]: p for p in stats["players"]}
    assert (by["1"]["present"], by["1"]["of"]) == (1, 1)
    assert (by["2"]["present"], by["2"]["of"]) == (0, 1)
    assert by["3"]["of"] == 0
    assert [p["id"] for p in stats["players"]] == [str(i) for i in range(1, 13)]
    narrow = client.get(f"{api}/stats?from=2026-10-21&to=2026-10-21").json()
    assert (narrow["practices"], narrow["not_taken"]) == (0, 1)
    # the range is read in the team's zone: 23:30Z is already the next day in Rome
    add(client, api, start="2026-10-24T22:30:00Z")
    r = client.get(f"{api}/stats?from=2026-10-25&to=2026-10-25").json()
    assert r["not_taken"] == 0  # future
