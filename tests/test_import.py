import json

from practice_attendance import calendar
from practice_attendance.store import Store
from practice_attendance.team import parse

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
{events}
END:VCALENDAR
"""

EVENT = """BEGIN:VEVENT
UID:{uid}
DTSTAMP:20261001T080000Z
DTSTART:{start}
DTEND:{end}
SUMMARY:{summary}
LOCATION:{location}
{extra}END:VEVENT"""


def event(
    uid="e1",
    start="20261101T170000Z",
    end="20261101T183000Z",
    summary="Allenamento",
    location="Qui",
    extra="",
):
    return EVENT.format(**locals())


def put_calendar(home, *events):
    (home / "calendar.ics").write_text(ICS.format(events="\n".join(events)))


def run(client, api):
    r = client.post(f"{api}/import")
    assert r.status_code == 200, r.text
    return r.json()


def practices(client, api):
    return client.get(api).json()["practices"]


def counts(**kw):
    return {"added": 0, "updated": 0, "cancelled": 0, "restored": 0} | kw


def test_example_calendar_semantics(client, api):
    run(client, api)
    ps = practices(client, api)
    starts = [p["start"] for p in ps]
    assert "2026-10-27T18:00:00Z" in starts  # moved 18:00 -> 19:00 Rome (CET, UTC+1)
    assert "2026-10-27T17:00:00Z" not in starts
    assert "2026-10-29T16:00:00Z" not in starts  # EXDATE
    assert "2026-11-03T17:00:00Z" not in starts  # STATUS:CANCELLED
    assert "2026-10-31T15:00:00Z" not in starts  # "Partita" is not a practice
    assert not any(s.startswith("2026-11-14") for s in starts)  # all day
    assert "2026-10-06T16:00:00Z" in starts  # CEST, UTC+2
    assert all(p["location"].startswith("Palestra Esempio") for p in ps)


def test_import_adds_then_is_idempotent_byte_for_byte(client, api, tmp_path):
    first = run(client, api)
    assert first["added"] > 20 and first["updated"] == first["cancelled"] == 0
    path = tmp_path / "data" / "practices.json"
    journal = tmp_path / "data" / "journal.jsonl"
    before = (path.read_bytes(), journal.read_bytes())
    assert run(client, api) == counts()
    assert (path.read_bytes(), journal.read_bytes()) == before


def test_move_keeps_attendance_and_key(client, api, home, clock):
    put_calendar(home, event())
    run(client, api)
    pid = practices(client, api)[0]["id"]
    clock.t = clock.t.replace(month=11, day=1, hour=16)
    client.put(f"{api}/{pid}/attendance", json={"attendance": {"1": "present"}})
    put_calendar(
        home, event(start="20261108T170000Z", end="20261108T183000Z", location="Là")
    )
    assert run(client, api) == counts(updated=1)
    (p,) = practices(client, api)
    assert (p["id"], p["start"], p["location"], p["taken"]) == (
        pid,
        "2026-11-08T17:00:00Z",
        "Là",
        True,
    )


def test_cancel_and_restore_keep_attendance(client, api, home, clock):
    put_calendar(
        home, event(), event(uid="e2", start="20261102T170000Z", end="20261102T180000Z")
    )
    run(client, api)
    clock.t = clock.t.replace(month=11, day=1, hour=16)
    client.put(f"{api}/1/attendance", json={"attendance": {"1": "present"}})
    put_calendar(
        home, event(uid="e2", start="20261102T170000Z", end="20261102T180000Z")
    )
    assert run(client, api) == counts(cancelled=1)
    # cancelled with attendance: still listed, after the others
    ps = practices(client, api)
    assert [(p["id"], p["cancelled"]) for p in ps] == [(2, False), (1, True)]
    put_calendar(
        home,
        event(extra="STATUS:CANCELLED\n"),
        event(uid="e2", start="20261102T170000Z", end="20261102T180000Z"),
    )
    assert run(client, api) == counts()  # cancelled event: stays cancelled
    put_calendar(
        home, event(), event(uid="e2", start="20261102T170000Z", end="20261102T180000Z")
    )
    assert run(client, api) == counts(restored=1)
    assert client.get(f"{api}/1").json()["attendance"] == {"1": "present"}


def test_cancelled_without_attendance_is_hidden(client, api, home):
    put_calendar(home, event())
    run(client, api)
    put_calendar(home)
    assert run(client, api) == counts(cancelled=1)
    assert practices(client, api) == []


def test_manual_practices_and_other_seasons_are_untouched(client, api, home):
    client.post(f"{api}/practices", json={"start": "2026-11-05T17:00:00Z"})
    put_calendar(home, event())
    run(client, api)
    put_calendar(home)
    run(client, api)
    ps = practices(client, api)
    assert [p["source"] for p in ps] == ["manual"] and not ps[0]["cancelled"]


def test_events_outside_season_or_title_are_ignored(client, api, home):
    put_calendar(
        home,
        event(uid="a", start="20250101T170000Z", end="20250101T180000Z"),
        event(uid="b", summary="Riunione"),
        event(uid="c", summary="allenamento extra"),  # case-insensitive prefix
    )
    assert run(client, api) == counts(added=1)


def test_event_without_end_lasts_an_hour(home):
    team = parse(json.loads((home / "team.json").read_text()))
    data = ICS.format(
        events="BEGIN:VEVENT\nUID:x\nDTSTAMP:20261001T080000Z\nDTSTART:20261101T170000Z\nSUMMARY:Allenamento\nEND:VEVENT"
    ).encode()
    ((start, end, location),) = calendar.practices(data, team).values()
    assert (end - start).total_seconds() == 3600 and location == ""


def test_fetch_accepts_paths_and_file_urls_and_refuses_the_rest(home):
    assert calendar.fetch("calendar.ics", home).startswith(b"BEGIN:VCALENDAR")
    assert calendar.fetch((home / "calendar.ics").as_uri()).startswith(b"BEGIN")
    for bad in ("ftp://x/y", "missing.ics"):
        try:
            calendar.fetch(bad, home)
        except calendar.CalendarUnavailable:
            continue
        raise AssertionError(bad)


def test_fetch_caps_the_size(home, monkeypatch):
    monkeypatch.setattr(calendar, "MAX_BYTES", 100)
    try:
        calendar.fetch("calendar.ics", home)
    except calendar.CalendarUnavailable:
        return
    raise AssertionError("not capped")


def test_journal_keeps_what_was_replaced_and_no_calendar_address(
    client, api, tmp_path, clock
):
    client.post(f"{api}/practices", json={"start": "2026-10-23T10:00:00Z"})
    client.put(f"{api}/1/attendance", json={"attendance": {"1": "present"}})
    client.put(f"{api}/1/attendance", json={"attendance": {"1": "absent"}})
    run(client, api)
    lines = (tmp_path / "data" / "journal.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["op"] for r in records] == ["add", "attendance", "attendance", "import"]
    change = records[2]["changed"][0]
    assert change["before"]["attendance"] == {"1": "present"}
    assert change["after"]["attendance"] == {"1": "absent"}
    assert "calendar.ics" not in "".join(lines)


def test_ids_survive_a_restart(client, api, tmp_path, make_client):
    client.post(f"{api}/practices", json={"start": "2026-10-23T10:00:00Z"})
    client.delete(f"{api}/1")
    again = make_client()
    assert (
        again.post(f"{api}/practices", json={"start": "2026-10-23T10:00:00Z"}).json()[
            "id"
        ]
        == 2
    )


def test_store_sorts_by_start(tmp_path):
    from datetime import UTC, datetime

    store = Store(tmp_path)
    t = datetime(2026, 1, 1, tzinfo=UTC)
    store.add_manual(t.replace(day=5), t.replace(day=5, hour=1), "")
    store.add_manual(t.replace(day=2), t.replace(day=2, hour=1), "")
    assert [p["id"] for p in store.all()] == [2, 1]
