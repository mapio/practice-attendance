"""The server: the pages and the API of docs/contract.md."""

import json
import logging
import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, NamedTuple

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import calendar, stats
from .errors import ApiError, invalid, not_found
from .limiter import Limiter
from .store import Store, fmt, opens_at, parse_instant
from .team import ConfigError, Team, TeamFile, team_files
from .tokens import player_token, read_salt, staff_token

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("practice-attendance")
UNKNOWN_TOKEN_LIMIT = 10  # requests with an unknown token, per address, per window
UNKNOWN_TOKEN_WINDOW = 60  # seconds
# the pages and scripts change with the code: keep a copy but revalidate it
NO_CACHE = {"Cache-Control": "no-cache"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# the home-screen app: one name for everyone (the phone never renames it)
APP_NAME, APP_SHORT_NAME = "PracticeAttendance", "Attendance"
# an operator's own icons, beside the team file, win over the neutral ones
ICONS = ("icon-192.png", "icon-512.png", "icon-maskable-512.png")
PNG = {"type": "image/png"}
THEME_COLOR = "#ffffff"  # the pages' light background, Pico's


class RevalidatedFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = NO_CACHE["Cache-Control"]
        return response


class Scope(NamedTuple):
    """What a token opens: the team in force, the player (`None` for the staff)
    and the team's own store."""

    id: str
    team: Team
    player: str | None
    store: Store


def create_app(teams_dir, data_dir, now=lambda: datetime.now(UTC), static=STATIC):
    """The app; a test fixes `now`. Raises `ValueError` or `ConfigError` if the
    salt or a team file is unusable."""
    teams_dir = Path(teams_dir)
    static = Path(static)
    salt = read_salt(teams_dir / "salt")
    languages = None
    if (static / "i18n.json").exists():
        languages = set(json.loads((static / "i18n.json").read_text(encoding="utf-8")))
    claims = {}  # team id -> {token: player id, None for the staff's}

    def claim(team_id):
        """The `check` of a team's file: the tokens of `team` as `{token: player}`
        (`None` for the staff), or a `ConfigError` when two are equal, in the team
        or between teams. They are kept only once the file is accepted."""

        def check(team):
            found = {staff_token(salt, team_id): None}
            for player in team.roster:
                slug = player_token(salt, team_id, player)
                if slug in found:
                    raise ConfigError(f"$.roster.{player}: its token is not its own")
                found[slug] = player
            for other, others in claims.items():
                if other != team_id and found.keys() & others.keys():
                    raise ConfigError(f"$: a token is also one of team {other}")
            claims[team_id] = found

        return check

    stores, teams = {}, {}
    for team_id, path in team_files(teams_dir).items():
        stores[team_id] = Store(Path(data_dir) / team_id, now)
        try:
            teams[team_id] = TeamFile(
                path, stores[team_id].named_players, languages, claim(team_id)
            )
        except ConfigError as error:
            raise ConfigError(f"{team_id}: {error}") from None
    limiter = Limiter(
        UNKNOWN_TOKEN_LIMIT, UNKNOWN_TOKEN_WINDOW, lambda: now().timestamp()
    )
    importing = threading.Lock()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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

    def caller(request: Request, tok: str):
        """The `Scope` of a token, whose team is the one that holds it; else 404,
        or 429 once the address has asked for too many unknown tokens."""
        slug = tok.lower()
        for team_id, file in teams.items():
            team = file.current()  # every file is checked: a player may be new
            if slug in claims[team_id]:
                return Scope(team_id, team, claims[team_id][slug], stores[team_id])
        if not limiter.allow(request.client.host if request.client else "?"):
            raise ApiError(429, "rate_limited", "too many attempts")
        raise not_found()

    def staff(scope: Annotated[Scope, Depends(caller)]):
        """The scope, for a staff token alone: a player's gets the 404 of any
        path that is not hers."""
        if scope.player is not None:
            raise not_found()
        return scope

    def athlete(scope: Annotated[Scope, Depends(caller)]):
        """The scope, for a player's token alone."""
        if scope.player is None:
            raise not_found()
        return scope

    Caller = Annotated[Scope, Depends(caller)]
    Athlete = Annotated[Scope, Depends(athlete)]
    Staff = Annotated[Scope, Depends(staff)]
    Payload = Annotated[dict, Body()]

    def practice_of(store, p):
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
            out["excused"] = (
                None if taken is None else sum(s == "excused" for s in taken.values())
            )
            out["of"] = None if taken is None else len(taken)
            out["announced"] = len(p.get("absences", {}))
        return out

    def listing(scope):
        team, store = scope.team, scope.store
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
            "announced": [i for i in team.roster if i in p.get("absences", {})],
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
        path = teams_dir / "privacy.html"
        if not path.exists():
            raise not_found()
        return FileResponse(path, headers=NO_CACHE)

    @app.get("/install")
    def install():
        return page("install.html")

    @app.get("/icons/{name}")
    def icons(name: str):
        if name not in ICONS:
            raise not_found()
        own = teams_dir / name
        return FileResponse(own if own.is_file() else static / "icons" / name)

    # API

    @app.get("/api/{tok}")
    def api_list(scope: Caller):
        if scope.player is not None:
            return stats.athlete(scope.store.all(), scope.team, scope.player, now())
        return listing(scope)

    @app.get("/api/{tok}/links")
    def api_links(scope: Staff):
        team = scope.team
        tokens = {
            player: slug
            for slug, player in claims[scope.id].items()
            if player is not None
        }
        return {
            "team": team.public(),
            "players": [
                {"id": i, "name": name, "token": tokens[i]}
                for i, name in team.roster.items()
            ],
        }

    @app.get("/api/{tok}/stats")
    def api_stats(
        scope: Staff,
        first: str | None = Query(None, alias="from"),
        last: str | None = Query(None, alias="to"),
    ):
        team, store = scope.team, scope.store
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
    def api_import(scope: Staff):
        team, store = scope.team, scope.store
        if team.calendar is None:
            raise ApiError(409, "no_calendar", "the team file names no calendar")
        with importing:  # one import at a time; another waits for it
            try:
                events = calendar.practices(
                    calendar.fetch(team.calendar.url, teams_dir), team
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
    def api_add(payload: Payload, scope: Staff):
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
        return view(scope.store.add_manual(start, end, location))

    @app.get("/api/{tok}/{p}")
    def api_practice(p: str, scope: Staff):
        return detail(scope.team, practice_of(scope.store, p))

    @app.patch("/api/{tok}/{p}")
    def api_edit(p: str, payload: Payload, scope: Staff):
        store = scope.store
        current = practice_of(store, p)
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
    def api_delete(p: str, scope: Staff):
        scope.store.delete_manual(practice_of(scope.store, p)["id"])
        return listing(scope)

    @app.put("/api/{tok}/{p}/absence")
    def api_announce(p: str, scope: Athlete):
        team, player, store = scope.team, scope.player, scope.store
        store.set_absence(practice_of(store, p)["id"], player, True)
        return stats.athlete(store.all(), team, player, now())

    @app.delete("/api/{tok}/{p}/absence")
    def api_withdraw(p: str, scope: Athlete):
        team, player, store = scope.team, scope.player, scope.store
        store.set_absence(practice_of(store, p)["id"], player, False)
        return stats.athlete(store.all(), team, player, now())

    @app.put("/api/{tok}/{p}/attendance")
    def api_attendance(p: str, payload: Payload, scope: Staff):
        team, store = scope.team, scope.store
        attendance = payload.get("attendance")
        if not isinstance(attendance, dict) or not all(
            isinstance(s, str) for s in attendance.values()
        ):
            raise invalid("attendance is {player-id: status}")
        updated = store.set_attendance(
            practice_of(store, p)["id"], attendance, team.roster
        )
        return detail(team, updated)

    # pages

    @app.get("/{tok}")
    @app.get("/{tok}/")  # the installed app's scope and start: a directory
    def page_list(scope: Caller):
        return page("list.html" if scope.player is None else "athlete.html")

    # before `/{tok}/{p}`, which would take the name for a practice
    @app.get("/{tok}/manifest.webmanifest")
    def manifest(tok: str, scope: Caller):
        start = f"/{tok}/"
        body = {
            "id": start,
            "name": APP_NAME,
            "short_name": APP_SHORT_NAME,
            "display": "standalone",
            "start_url": start,
            "scope": start,
            "theme_color": THEME_COLOR,
            "background_color": THEME_COLOR,
            "icons": [
                {"src": "/icons/icon-192.png", "sizes": "192x192", **PNG},
                {"src": "/icons/icon-512.png", "sizes": "512x512", **PNG},
                {
                    "src": "/icons/icon-maskable-512.png",
                    "sizes": "512x512",
                    "purpose": "maskable",
                    **PNG,
                },
            ],
        }
        return JSONResponse(
            body, media_type="application/manifest+json", headers=NO_CACHE
        )

    @app.get("/{tok}/links")
    def page_links(scope: Staff):
        return page("links.html")

    @app.get("/{tok}/stats")
    def page_stats(scope: Staff):
        return page("stats.html")

    @app.get("/{tok}/{p}")
    def page_practice(p: str, scope: Staff):
        practice_of(scope.store, p)
        return page("practice.html")

    return app
