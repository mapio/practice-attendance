"""The statistics (docs/contract.md, *Statistics*)."""

from .store import parse_instant


def compute(practices, team, first, last, now):
    """Over the practices whose local start date is in `first`..`last`."""
    taken = not_taken = 0
    present = dict.fromkeys(team.roster, 0)
    named = dict.fromkeys(team.roster, 0)
    for p in practices:
        start = parse_instant(p["start"])
        if p.get("cancelled") or not first <= start.astimezone(team.tz).date() <= last:
            continue
        if p["attendance"] is None:
            not_taken += start <= now
            continue
        taken += 1
        for player, status in p["attendance"].items():
            if player in named:
                named[player] += 1
                present[player] += status == "present"
    return {
        "from": first.isoformat(),
        "to": last.isoformat(),
        "practices": taken,
        "not_taken": not_taken,
        "players": [
            {"id": i, "name": name, "present": present[i], "of": named[i]}
            for i, name in team.roster.items()
        ],
    }
