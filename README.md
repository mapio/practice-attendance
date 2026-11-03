# Practice attendance

A very small web app to track who comes to a team's practices. A staff member
marks the attendance of each practice on a phone or a tablet; every athlete has
a page of her own, behind a secret link, that shows how she is doing. Data are
JSON files: no database, no accounts, no cookies. The practices are imported
from a calendar, when a team has one; otherwise the staff add them by hand.

One server serves any number of teams: each is a file `teams/<id>.json` (see
`example/`), with its data in `data/<id>/`, and its own staff and athlete links.
The links carry no team id: the token alone tells the team and the role.

A team file may name a `coach`. A coach is the teams that name her, and she has
one more link, with a selector for her teams, so that one home-screen app covers
them all; `urls` prints it under `# coach <id>`.

```
uv run practice-attendance serve --teams example --data /tmp/data
uv run practice-attendance urls --teams example --players
```

A new team is a new file in the team directory and a restart; the files already
read are watched, so editing a roster or a date needs none. `docs/contract.md`
is the full description.

This repository is the code only; it is just started.

## Licence

GNU General Public License, version 3 or later: see `LICENSE`.
