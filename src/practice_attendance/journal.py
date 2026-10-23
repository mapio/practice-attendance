"""The journal (docs/contract.md, *Files*): an append-only record of every change
of the practices, to rebuild what a wrong tap or a bug lost. A line is on the
disk before the rename that replaces `practices.json`, so whatever that file
shows is in here. Players are identifiers, and the calendar address never
appears."""

import json
import os
from datetime import UTC


def stamp(t):
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(t, op, old, new, next_id):
    """What turns the practices `old` into `new` (lists of JSON), by id."""
    before = {p["id"]: p for p in old}
    after = {p["id"]: p for p in new}
    return {
        "t": stamp(t),
        "op": op,
        "next_id": next_id,
        "added": [p for p in new if p["id"] not in before],
        "removed": [p for p in old if p["id"] not in after],
        "changed": [
            {"before": before[i], "after": after[i]}
            for i in before
            if i in after and before[i] != after[i]
        ],
    }


def append(path, entry):
    """Add one line and make sure it is on the disk before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
