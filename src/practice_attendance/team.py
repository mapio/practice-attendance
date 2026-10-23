"""The team file (docs/contract.md, *Team file* and *Reloading*): validated whole,
with the path of whatever is wrong in the message, and read again when it
changes."""

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("practice-attendance")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Calendar:
    url: str
    title: str


@dataclass(frozen=True)
class Team:
    name: str
    lang: str
    timezone: str
    season_from: date
    season_to: date
    calendar: Calendar | None
    roster: dict  # player id -> display name, in file order

    @property
    def tz(self):
        return ZoneInfo(self.timezone)

    def public(self):
        """The `team` of the API: the calendar address never leaves the server."""
        return {
            "name": self.name,
            "lang": self.lang,
            "timezone": self.timezone,
            "season": {
                "from": self.season_from.isoformat(),
                "to": self.season_to.isoformat(),
            },
        }


def _text(obj, key, path, empty=False):
    value = obj.get(key)
    if not isinstance(value, str) or not (value.strip() or empty):
        raise ConfigError(f"{path}.{key}: expected a non-empty text")
    return value


def _date(obj, key, path):
    try:
        return date.fromisoformat(_text(obj, key, path))
    except ValueError:
        raise ConfigError(f"{path}.{key}: expected a date YYYY-MM-DD") from None


def parse(obj, languages=None):
    """The `Team` of the file's JSON, or a `ConfigError` naming the path."""
    if not isinstance(obj, dict):
        raise ConfigError("$: expected an object")
    name = _text(obj, "name", "$")
    lang = _text(obj, "lang", "$")
    if languages is not None and lang not in languages:
        raise ConfigError(f"$.lang: {lang!r} is not in i18n.json")
    timezone = _text(obj, "timezone", "$")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"$.timezone: unknown zone {timezone!r}") from None
    season = obj.get("season")
    if not isinstance(season, dict):
        raise ConfigError("$.season: expected {from, to}")
    start, end = _date(season, "from", "$.season"), _date(season, "to", "$.season")
    if start > end:
        raise ConfigError("$.season: from is after to")
    calendar = None
    if "calendar" in obj:
        raw = obj["calendar"]
        if not isinstance(raw, dict):
            raise ConfigError("$.calendar: expected {url, title}")
        calendar = Calendar(
            _text(raw, "url", "$.calendar"), _text(raw, "title", "$.calendar")
        )
    roster = obj.get("roster")
    if not isinstance(roster, dict) or not roster:
        raise ConfigError("$.roster: expected {player-id: name}")
    for player, shown in roster.items():
        if not player or not isinstance(shown, str) or not shown.strip():
            raise ConfigError(f"$.roster.{player}: expected a non-empty name")
    return Team(name, lang, timezone, start, end, calendar, dict(roster))


class TeamFile:
    """The configuration in force. `named` tells which players the attendance
    names, so that a file dropping one of them is refused; `check`, if given,
    raises `ConfigError` for a team it refuses for its own reasons."""

    def __init__(self, path, named, languages=None, check=None):
        self._path = Path(path)
        self._named = named
        self._languages = languages
        self._check = check
        self.team = self._read()  # at startup a failure is fatal
        self._mtime = self._path.stat().st_mtime_ns

    def _read(self):
        try:
            obj = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ConfigError(f"{self._path}: {error}") from None
        team = parse(obj, self._languages)
        dropped = sorted(self._named() - set(team.roster))
        if dropped:
            raise ConfigError(f"$.roster: drops players with attendance: {dropped}")
        if self._check:
            self._check(team)
        return team

    def current(self):
        """The team, read again first if the file's modification time changed."""
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError:
            return self.team
        if mtime != self._mtime:
            self._mtime = mtime  # a refused version is logged once, not per request
            try:
                self.team = self._read()
            except ConfigError as error:
                log.error("team file refused, keeping the previous one: %s", error)
        return self.team
