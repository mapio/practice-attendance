"""The practices on disk: `data/practices.json` (docs/contract.md, *Practices
file*). The process is the only writer, so the file is read once and kept in
memory; each change goes to the journal and then to the file whole, through a
rename, so a crash leaves the old state or the new one."""

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import journal
from .errors import ApiError

OPENS_BEFORE = timedelta(minutes=60)
STATUSES = ("present", "absent")


def fmt(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(text):
    """A UTC instant from ISO 8601 text with a zone, or None."""
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        return None
    return t.astimezone(UTC) if t.tzinfo else None


def opens_at(practice):
    return parse_instant(practice["start"]) - OPENS_BEFORE


class Store:
    def __init__(self, data_dir, now=lambda: datetime.now(UTC)):
        self._dir = Path(data_dir)
        self._now = now
        self._lock = threading.RLock()
        path = self._dir / "practices.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self._next_id = raw.get("next_id", 1)
        self._practices = raw.get("practices", [])

    def all(self):
        with self._lock:
            return list(self._practices)

    def get(self, practice_id):
        with self._lock:
            return next((p for p in self._practices if p["id"] == practice_id), None)

    def named_players(self):
        """Every player id some attendance names."""
        with self._lock:
            return {
                player
                for p in self._practices
                for player in (p.get("attendance") or {})
            }

    def imported_at(self):
        try:
            return (self._dir / "imported_at").read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def mark_imported(self):
        self._write_text(self._dir / "imported_at", fmt(self._now()) + "\n")

    def _write_text(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_suffix(path.suffix + ".tmp")
        with scratch.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        scratch.replace(path)

    def _commit(self, op, practices, next_id):
        """Journal the change, write the whole file, then keep it in memory.
        `practices` is the new state; nothing happens if it equals the old."""
        practices = sorted(practices, key=lambda p: (p["start"], p["id"]))
        if practices == self._practices and next_id == self._next_id:
            return
        entry = journal.record(self._now(), op, self._practices, practices, next_id)
        journal.append(self._dir / "journal.jsonl", entry)
        text = json.dumps(
            {"next_id": next_id, "practices": practices},
            ensure_ascii=False,
            indent=1,
        )
        self._write_text(self._dir / "practices.json", text + "\n")
        self._practices, self._next_id = practices, next_id

    def _replace(self, practice_id, op, change):
        with self._lock:
            old = self.get(practice_id)
            if old is None:
                raise ApiError(404, "not_found", "not found")
            new = change(dict(old))
            self._commit(
                op,
                [new if p["id"] == practice_id else p for p in self._practices],
                self._next_id,
            )
            return new

    def add_manual(self, start, end, location):
        with self._lock:
            practice = {
                "id": self._next_id,
                "source": "manual",
                "start": fmt(start),
                "end": fmt(end),
                "location": location,
                "attendance": None,
            }
            self._commit("add", [*self._practices, practice], self._next_id + 1)
            return practice

    def edit_manual(self, practice_id, fields):
        def change(p):
            if p["source"] != "manual":
                raise ApiError(
                    409, "imported_practice", "the calendar changes this practice"
                )
            new = {**p, **fields}
            if new["end"] <= new["start"]:  # same-format UTC text sorts as time
                raise ApiError(422, "invalid_value", "end is not after start")
            return new

        return self._replace(practice_id, "edit", change)

    def delete_manual(self, practice_id):
        with self._lock:
            p = self.get(practice_id)
            if p is None:
                raise ApiError(404, "not_found", "not found")
            if p["source"] != "manual":
                raise ApiError(
                    409, "imported_practice", "the calendar changes this practice"
                )
            if p["attendance"] is not None:
                raise ApiError(409, "has_attendance", "the attendance was taken")
            self._commit(
                "delete",
                [q for q in self._practices if q["id"] != practice_id],
                self._next_id,
            )

    def set_attendance(self, practice_id, attendance, roster):
        """Replace the whole map; the rules of docs/contract.md, *Window* and
        *PUT attendance*, are checked here, under the lock."""

        def change(p):
            if p.get("cancelled"):
                raise ApiError(409, "cancelled", "the practice is cancelled")
            if self._now() < opens_at(p):
                raise ApiError(409, "not_open_yet", "the practice is not open yet")
            return {**p, "attendance": attendance}

        for player, status in attendance.items():
            if player not in roster:
                raise ApiError(422, "unknown_player", f"unknown player {player!r}")
            if status not in STATUSES:
                raise ApiError(422, "invalid_value", f"invalid status {status!r}")
        return self._replace(practice_id, "attendance", change)

    def reconcile(self, events, in_season):
        """Make the practices of `calendar` source match `events`, a dict
        key -> (start, end, location) of datetimes and text, those within the
        season; `in_season(start_text)` tells whether a practice is. Returns the
        counts of added, updated, cancelled and restored practices."""
        counts = {"added": 0, "updated": 0, "cancelled": 0, "restored": 0}
        with self._lock:
            next_id = self._next_id
            kept = []
            seen = set()
            for p in self._practices:
                if p["source"] != "calendar":
                    kept.append(p)
                    continue
                seen.add(p["key"])
                if p["key"] in events:
                    start, end, location = events[p["key"]]
                    new = {
                        **p,
                        "start": fmt(start),
                        "end": fmt(end),
                        "location": location,
                    }
                    if new.pop("cancelled", None):
                        counts["restored"] += 1
                    if (new["start"], new["end"], new["location"]) != (
                        p["start"],
                        p["end"],
                        p["location"],
                    ):
                        counts["updated"] += 1
                    kept.append(new)
                elif in_season(p["start"]) and not p.get("cancelled"):
                    kept.append({**p, "cancelled": True})
                    counts["cancelled"] += 1
                else:
                    kept.append(p)
            for key, (start, end, location) in events.items():
                if key not in seen:
                    kept.append(
                        {
                            "id": next_id,
                            "source": "calendar",
                            "key": key,
                            "start": fmt(start),
                            "end": fmt(end),
                            "location": location,
                            "attendance": None,
                        }
                    )
                    next_id += 1
                    counts["added"] += 1
            self._commit("import", kept, next_id)
        return counts
