"""The server: the pages and the API of docs/contract.md."""

import json
import logging
import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import calendar, stats
from .errors import ApiError, invalid, not_found
from .limiter import Limiter
from .store import Store, fmt, opens_at, parse_instant
from .team import Team, TeamFile
from .tokens import read_salt, staff_token

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("practice-attendance")
UNKNOWN_TOKEN_LIMIT = 10  # requests with an unknown token, per address, per window
UNKNOWN_TOKEN_WINDOW = 60  # seconds
# the pages and scripts change with the code: keep a copy but revalidate it
NO_CACHE = {"Cache-Control": "no-cache"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class RevalidatedFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = NO_CACHE["Cache-Control"]
        return response


def create_app(team_path, data_dir, now=lambda: datetime.now(UTC), static=STATIC):
    """The app; a test fixes `now`. Raises `ValueError` or `ConfigError` if the
    salt or the team file is unusable."""
    team_path = Path(team_path)
    static = Path(static)
    token = staff_token(read_salt(team_path.with_name("salt")))
    store = Store(data_dir, now)
    languages = None
    if (static / "i18n.json").exists():
        languages = set(json.loads((static / "i18n.json").read_text(encoding="utf-8")))
    teams = TeamFile(team_path, store.named_players, languages)
    limiter = Limiter(
        UNKNOWN_TOKEN_LIMIT, UNKNOWN_TOKEN_WINDOW, lambda: now().timestamp()
    )
    importing = threading.Lock()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.store = store

    def page(name, status=200):
        path = static / name
        if not path.exists():  # the pages are another part's: say so, do not crash
            return JSONResponse({"error": "not_found", "detail": name}, status_code=404)
        return FileResponse(path, status_code=status, headers=NO_CACHE)

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        if not request.url.path.startswith("/api/"):
            return page("notfound.html", error.status)
        body = {"error": error.code, "detail": error.detail}
        return JSONResponse(body, status_code=error.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        if error.status_code == 404:
            return await api_error(request, not_found())
        return JSONResponse(
            {"error": "invalid_value", "detail": str(error.detail)},
            status_code=error.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def malformed(request, error):
        return await api_error(request, invalid("the body is not the expected JSON"))

    @app.exception_handler(Exception)
    async def unexpected(request, error):
        # the route's template names the page, never the path as typed (a token)
        where = getattr(request.scope.get("route"), "path", "?")
        log.error("unhandled error in %s", where, exc_info=error)
        failure = ApiError(500, "server_error", "something went wrong")
        return await api_error(request, failure)

    def staff(request: Request, tok: str):
        """The team, if `tok` is the staff token; else 404, or 429 once the
        address has asked for too many unknown ones."""
        if tok.lower() != token:
            if not limiter.allow(request.client.host if request.client else "?"):
                raise ApiError(429, "rate_limited", "too many attempts")
            raise not_found()
        return teams.current()

    Staff = Annotated[Team, Depends(staff)]
    Payload = Annotated[dict, Body()]

    def practice_of(p):
        found = store.get(int(p)) if p.isascii() and p.isdigit() else None
        if found is None:
            raise not_found()
        return found

    def view(p, counts=False):
        out = {
            "id": p["id"],
            "source": p["source"],
            "start": p["start"],
            "end": p["end"],
            "location": p["location"],
            "cancelled": bool(p.get("cancelled")),
            "taken": p["attendance"] is not None,
            "opens_at": fmt(opens_at(p)),
            "open": now() >= opens_at(p),
        }
        if counts:
            taken = p["attendance"]
            out["present"] = (
                None if taken is None else sum(s == "present" for s in taken.values())
            )
            out["of"] = None if taken is None else len(taken)
        return out

    def listing(team):
        shown = [
            p
            for p in store.all()
            if not p.get("cancelled") or p["attendance"] is not None
        ]
        shown.sort(key=lambda p: (bool(p.get("cancelled")), p["start"], p["id"]))
        return {
            "team": team.public(),
            "practices": [view(p, counts=True) for p in shown],
            "imported_at": store.imported_at(),
        }

    def detail(team, p):
        return {
            "team": team.public(),
            "practice": view(p),
            "roster": team.roster,
            "attendance": p["attendance"],
        }

    # not counted by the limit: they name no token
    app.mount("/static", RevalidatedFiles(directory=static), name="static")

    @app.get("/")
    def root():
        return page("notfound.html", 404)

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/privacy")
    def privacy():
        path = team_path.with_name("privacy.html")
        if not path.exists():
            raise not_found()
        return FileResponse(path, headers=NO_CACHE)

    # API

    @app.get("/api/{tok}")
    def api_list(team: Staff):
        return listing(team)

    @app.get("/api/{tok}/stats")
    def api_stats(
        team: Staff,
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        for text in (first, last):
            if text is not None and not DATE.fullmatch(text):
                raise invalid("a date is YYYY-MM-DD")
        try:
            first = date.fromisoformat(first) if first else team.season_from
            last = date.fromisoformat(last) if last else team.season_to
        except ValueError:
            raise invalid("not a date") from None
        if first > last:
            raise invalid("from is after to")
        return stats.compute(store.all(), team, first, last, now())

    @app.post("/api/{tok}/import")
    def api_import(team: Staff):
        if team.calendar is None:
            raise ApiError(409, "no_calendar", "the team file names no calendar")
        with importing:  # one import at a time; another waits for it
            try:
                events = calendar.practices(
                    calendar.fetch(team.calendar.url, team_path.parent), team
                )
            except calendar.CalendarUnavailable as error:
                log.warning("calendar unavailable: %s", error)
                raise ApiError(
                    502, "calendar_unavailable", "the calendar cannot be read"
                ) from None

            def in_season(start):
                local = parse_instant(start).astimezone(team.tz).date()
                return team.season_from <= local <= team.season_to

            counts = store.reconcile(events, in_season)
            store.mark_imported()
        return counts

    @app.post("/api/{tok}/practices", status_code=201)
    def api_add(payload: Payload, team: Staff):
        start = parse_instant(payload.get("start"))
        if start is None:
            raise invalid("start is a UTC instant")
        end = start + timedelta(hours=1)
        if payload.get("end") is not None:
            end = parse_instant(payload["end"])
            if end is None or end <= start:
                raise invalid("end is a UTC instant after start")
        location = payload.get("location", "")
        if not isinstance(location, str):
            raise invalid("location is text")
        return view(store.add_manual(start, end, location))

    @app.get("/api/{tok}/{p}")
    def api_practice(p: str, team: Staff):
        return detail(team, practice_of(p))

    @app.patch("/api/{tok}/{p}")
    def api_edit(p: str, payload: Payload, team: Staff):
        current = practice_of(p)
        fields = {}
        for name in ("start", "end"):
            if name in payload:
                instant = parse_instant(payload[name])
                if instant is None:
                    raise invalid(f"{name} is a UTC instant")
                fields[name] = fmt(instant)
        if "location" in payload:
            if not isinstance(payload["location"], str):
                raise invalid("location is text")
            fields["location"] = payload["location"]
        return view(store.edit_manual(current["id"], fields))

    @app.delete("/api/{tok}/{p}")
    def api_delete(p: str, team: Staff):
        store.delete_manual(practice_of(p)["id"])
        return listing(team)

    @app.put("/api/{tok}/{p}/attendance")
    def api_attendance(p: str, payload: Payload, team: Staff):
        attendance = payload.get("attendance")
        if not isinstance(attendance, dict) or not all(
            isinstance(s, str) for s in attendance.values()
        ):
            raise invalid("attendance is {player-id: status}")
        updated = store.set_attendance(practice_of(p)["id"], attendance, team.roster)
        return detail(team, updated)

    # pages

    @app.get("/{tok}")
    def page_list(team: Staff):
        return page("list.html")

    @app.get("/{tok}/stats")
    def page_stats(team: Staff):
        return page("stats.html")

    @app.get("/{tok}/{p}")
    def page_practice(p: str, team: Staff):
        practice_of(p)
        return page("practice.html")

    return app
