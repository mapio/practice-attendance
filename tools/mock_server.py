"""Mock of the practice-attendance server, to try the static pages.

Serves src/practice_attendance/static/ at the contract's URLs and implements
docs/contract.md's API in memory, with invented data: nothing is persisted and
no token is derived, the staff token is fixed.
"""

import argparse
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent.parent / "src/practice_attendance/static"
TOKEN = "hmdpet"
TZ = ZoneInfo("Europe/Rome")
NAMES = [
    "Giulia",
    "Sofia",
    "Aurora",
    "Chiara",
    "Martina",
    "Alice",
    "Emma",
    "Greta",
    "Beatrice",
    "Ginevra",
    "Vittoria",
    "Francesca",
]
TEAM = {
    "name": "Squadra di prova",
    "lang": "it",
    "timezone": "Europe/Rome",
    "season": {"from": "2026-09-01", "to": "2027-06-30"},
}
ROSTER = {str(i + 1): n for i, n in enumerate(NAMES)}

opts = argparse.Namespace(open_always=False, no_calendar=False, now=None)
practices: list[dict] = []
state = {"next_id": 1, "imports": 0, "imported_at": None}


def now() -> datetime:
    return opts.now or datetime.now(UTC)


def iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s) -> datetime:
    if not isinstance(s, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", s
    ):
        raise Err(422, "invalid_value", "not a UTC instant")
    return datetime.fromisoformat(s)


def local(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def add(source, start, end, location="", attendance=None, cancelled=False) -> dict:
    p = {
        "id": state["next_id"],
        "source": source,
        "start": iso(start),
        "end": iso(end),
        "location": location,
        "attendance": attendance,
    }
    if cancelled:
        p["cancelled"] = True
    state["next_id"] += 1
    practices.append(p)
    practices.sort(key=lambda q: q["start"])
    return p


def seed() -> None:
    absent = [{"4"}, {"5"}, {"6", "7", "11"}, {"2", "9"}]
    days = [(10, 13), (10, 15), (10, 20), (10, 22), (10, 27), (10, 29), (11, 3)]
    for k, (m, d) in enumerate(days):
        start = local(2026, m, d, 18, 0)
        att = None
        if k < len(absent):
            att = {i: ("absent" if i in absent[k] else "present") for i in ROSTER}
        add(
            "calendar",
            start,
            start + timedelta(minutes=90),
            "Palestra A",
            att,
            cancelled=(k == 1),
        )
    start = local(2026, 10, 31, 10, 0)
    add("manual", start, start + timedelta(minutes=90), "Palestrina")


class Err(Exception):
    def __init__(self, status, code, detail=""):
        self.status, self.code, self.detail = status, code, detail


def public(p: dict, in_list=False) -> dict:
    start = datetime.fromisoformat(p["start"])
    opens = start - timedelta(minutes=60)
    out = {
        "id": p["id"],
        "source": p["source"],
        "start": p["start"],
        "end": p["end"],
        "location": p["location"],
        "cancelled": bool(p.get("cancelled")),
        "taken": p["attendance"] is not None,
        "opens_at": iso(opens),
        "open": opts.open_always or now() >= opens,
    }
    if in_list:
        att = p["attendance"]
        out["present"] = (
            None if att is None else sum(v == "present" for v in att.values())
        )
        out["of"] = None if att is None else len(att)
    return out


def listing() -> list[dict]:
    ps = [p for p in practices if not p.get("cancelled") or p["attendance"] is not None]
    return [public(p, True) for p in ps]


def find(pid: str) -> dict:
    for p in practices:
        if str(p["id"]) == pid:
            return p
    raise Err(404, "not_found", "no such practice")


def detail(p: dict) -> dict:
    return {
        "team": TEAM,
        "practice": public(p),
        "roster": ROSTER,
        "attendance": p["attendance"],
    }


def stats(frm: date, to: date) -> dict:
    out = {i: [0, 0] for i in ROSTER}
    taken = missed = 0
    for p in practices:
        if p.get("cancelled"):
            continue
        start = datetime.fromisoformat(p["start"])
        if not frm <= start.astimezone(TZ).date() <= to:
            continue
        if p["attendance"] is None:
            missed += start <= now()
            continue
        taken += 1
        for i, s in p["attendance"].items():
            out[i][1] += 1
            out[i][0] += s == "present"
    return {
        "from": frm.isoformat(),
        "to": to.isoformat(),
        "practices": taken,
        "not_taken": missed,
        "players": [
            {"id": i, "name": n, "present": out[i][0], "of": out[i][1]}
            for i, n in ROSTER.items()
        ],
    }


def day(s) -> date:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        raise Err(422, "invalid_value", "bad date") from None


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
# Mounted first, or /static/x would be taken for a token and a practice.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(Err)
async def on_err(request: Request, e: Err):
    if e.status == 404 and not request.url.path.startswith("/api"):
        return FileResponse(STATIC / "notfound.html", status_code=404)
    return JSONResponse({"error": e.code, "detail": e.detail}, status_code=e.status)


def check(tok: str) -> None:
    if tok.lower() != TOKEN:
        raise Err(404, "not_found", "unknown link")


async def body(request: Request) -> dict:
    try:
        b = await request.json()
    except ValueError:
        b = None
    if not isinstance(b, dict):
        raise Err(422, "invalid_value", "body must be an object")
    return b


@app.get("/api/{tok}")
def api_list(tok: str):
    check(tok)
    return {"team": TEAM, "practices": listing(), "imported_at": state["imported_at"]}


@app.get("/api/{tok}/stats")
def api_stats(tok: str, request: Request):
    check(tok)
    q = request.query_params
    s = TEAM["season"]
    frm, to = day(q.get("from", s["from"])), day(q.get("to", s["to"]))
    if frm > to:
        raise Err(422, "invalid_value", "from after to")
    return stats(frm, to)


@app.post("/api/{tok}/import")
def api_import(tok: str):
    check(tok)
    if opts.no_calendar:
        raise Err(409, "no_calendar", "the team has no calendar")
    state["imports"] += 1
    state["imported_at"] = iso(now())
    r = {"added": 0, "updated": 0, "cancelled": 0, "restored": 0}
    if state["imports"] == 1:
        start = local(2026, 11, 5, 18, 0)
        add("calendar", start, start + timedelta(minutes=90), "Palestra A")
        r["added"] = 1
        r["cancelled"] = 1
        last = [
            p for p in practices if p["source"] == "calendar" and not p["attendance"]
        ][-2]
        last["cancelled"] = True
    return r


@app.post("/api/{tok}/practices", status_code=201)
async def api_add(tok: str, request: Request):
    check(tok)
    b = await body(request)
    start = parse(b.get("start"))
    end = parse(b["end"]) if b.get("end") else start + timedelta(hours=1)
    loc = b.get("location", "")
    if end <= start or not isinstance(loc, str):
        raise Err(422, "invalid_value", "end must follow start")
    return public(add("manual", start, end, loc), True)


@app.get("/api/{tok}/{p}")
def api_practice(tok: str, p: str):
    check(tok)
    return detail(find(p))


@app.put("/api/{tok}/{p}/attendance")
async def api_put(tok: str, p: str, request: Request):
    check(tok)
    pr = find(p)
    att = (await body(request)).get("attendance")
    if not isinstance(att, dict):
        raise Err(422, "invalid_value", "attendance must be a map")
    if pr.get("cancelled"):
        raise Err(409, "cancelled", "the practice is cancelled")
    if not public(pr)["open"]:
        raise Err(409, "not_open_yet", "not open yet")
    for i, s in att.items():
        if i not in ROSTER:
            raise Err(422, "unknown_player", i)
        if s not in ("present", "absent"):
            raise Err(422, "invalid_value", str(s))
    pr["attendance"] = att
    return detail(pr)


@app.patch("/api/{tok}/{p}")
async def api_patch(tok: str, p: str, request: Request):
    check(tok)
    pr = find(p)
    b = await body(request)
    if pr["source"] != "manual":
        raise Err(409, "imported_practice", "changed by the calendar")
    start = parse(b.get("start", pr["start"]))
    end = parse(b.get("end", pr["end"]))
    loc = b.get("location", pr["location"])
    if end <= start or not isinstance(loc, str):
        raise Err(422, "invalid_value", "end must follow start")
    pr.update(start=iso(start), end=iso(end), location=loc)
    practices.sort(key=lambda q: q["start"])
    return public(pr, True)


@app.delete("/api/{tok}/{p}")
def api_delete(tok: str, p: str):
    check(tok)
    pr = find(p)
    if pr["source"] != "manual":
        raise Err(409, "imported_practice", "changed by the calendar")
    if pr["attendance"] is not None:
        raise Err(409, "has_attendance", "attendance was taken")
    practices.remove(pr)
    return listing()


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/")
def root():
    raise Err(404, "not_found", "")


@app.get("/{tok}")
def page_list(tok: str):
    check(tok)
    return FileResponse(STATIC / "list.html")


@app.get("/{tok}/stats")
def page_stats(tok: str):
    check(tok)
    return FileResponse(STATIC / "stats.html")


@app.get("/{tok}/{p}")
def page_practice(tok: str, p: str):
    check(tok)
    find(p)
    return FileResponse(STATIC / "practice.html")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument(
        "--open-always", action="store_true", help="ignore the opens_at window"
    )
    ap.add_argument(
        "--no-calendar", action="store_true", help="the import answers no_calendar"
    )
    ap.add_argument(
        "--now", help="fake clock, a UTC instant such as 2026-10-22T15:00:00Z"
    )
    a = ap.parse_args()
    opts.open_always, opts.no_calendar = a.open_always, a.no_calendar
    opts.now = parse(a.now) if a.now else None
    seed()
    print(f"staff URL: http://{a.host}:{a.port}/{TOKEN}", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
