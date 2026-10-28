"""The statistics and the athlete record (docs/contract.md, *Statistics* and
*Athlete record*)."""

from .store import fmt, notice_opens_at, parse_instant


def compute(practices, team, first, last, now):
    """Over the practices whose local start date is in `first`..`last`."""
    taken = not_taken = 0
    present = dict.fromkeys(team.roster, 0)
    excused = dict.fromkeys(team.roster, 0)
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
                excused[player] += status == "excused"
    return {
        "from": first.isoformat(),
        "to": last.isoformat(),
        "practices": taken,
        "not_taken": not_taken,
        "players": [
            {
                "id": i,
                "name": name,
                "present": present[i],
                "excused": excused[i],
                "of": named[i],
            }
            for i, name in team.roster.items()
        ],
    }


def percent(present, of):
    """A whole number, halves rounded up; `None` when nothing is counted."""
    return (200 * present + of) // (2 * of) if of else None


def athlete(practices, team, player, now):
    """One player's record over the season: hers alone, and the team's
    percentage as the only datum of the others."""
    season = compute(practices, team, team.season_from, team.season_to, now)
    mine = next(p for p in season["players"] if p["id"] == player)
    everyone = sum(p["present"] for p in season["players"])
    named = sum(p["of"] for p in season["players"])
    seen = []
    for p in practices:
        start = parse_instant(p["start"])
        if (
            p.get("cancelled")
            or p["attendance"] is None
            or player not in p["attendance"]
        ):
            continue
        if not team.season_from <= start.astimezone(team.tz).date() <= team.season_to:
            continue
        seen.append(
            {
                "start": p["start"],
                "end": p["end"],
                "location": p["location"],
                "status": p["attendance"][player],
            }
        )
    seen.sort(key=lambda p: p["start"], reverse=True)
    coming = next(
        (
            p
            for p in practices
            if not p.get("cancelled") and parse_instant(p["start"]) > now
        ),
        None,
    )
    return {
        "team": team.public(),
        "player": {"id": player, "name": mine["name"]},
        "present": mine["present"],
        "excused": mine["excused"],
        "of": mine["of"],
        "percent": percent(mine["present"], mine["of"]),
        "team_percent": percent(everyone, named),
        "practices": seen,
        "next": coming
        and {
            "id": coming["id"],
            "start": coming["start"],
            "end": coming["end"],
            "location": coming["location"],
            "notice_opens_at": fmt(notice_opens_at(coming)),
            "notice_open": now >= notice_opens_at(coming),
            "announced": player in coming.get("absences", {}),
        },
    }
