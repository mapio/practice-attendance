# Contract

What the server and the staff page agree on. A change here is made first, then
in the code that depends on it.

## Files

- `teams/team.json`: the team file, one per server. Read at startup and again
  when it changes (see *Reloading*); edited by hand.
- `teams/salt`: the secret the token derives from (see *Tokens*). Required: the
  server does not start without it.
- `data/practices.json`: the practices, with their attendance, written by the
  server only.
- `data/journal.jsonl`: one line per change of `practices.json` (the practices
  added, changed and cancelled, and the attendance taken, whole, before and
  after, with what was done and the time), added and flushed to the disk before
  the file is replaced. Players appear by identifier only, and the calendar
  address never. Never read by the server.
- `data/imported_at`: the UTC instant of the last import, one line, written by
  the server (it is not in `practices.json`, so that an import that changes
  nothing leaves that file as it was).
- `i18n.json`: UI strings, `{lang: {key: text}}`.

The server takes the paths of the team file and of the data directory as
options; the salt is the file `salt` beside the team file.

### Team file

- `name`: the team's name, shown on the pages.
- `lang`: language code of the UI, a key of `i18n.json`.
- `timezone`: IANA name (`Europe/Rome`) in which the dates of the pages and of
  the statistics are read.
- `season`: `{from, to}`, local dates (`YYYY-MM-DD`), inclusive: the calendar is
  imported only within them, and they are the default range of the statistics.
- `calendar`: optional, `{url, title}`. `url` is the secret iCal address of the
  calendar; it is never sent to a browser nor written to a log or a journal. It
  may also be a file path (relative to the team file's directory) or a `file://`
  URL, for trials and tests. `title` is the word a practice's title begins with
  (`Allenamento`), compared ignoring case. Without it nothing is imported and
  every practice is added by hand.
- `roster`: `{player-id: display name}`. Ids are stable text and carry no name
  (`"7"`); the practices refer to them.

The server validates the whole file at startup and names the offending path.

### Reloading

On each request the server compares the modification time of the team file with
the one it loaded; when it differs, the file is read again. A file that passes
the validation replaces the configuration at once. One that fails, or that drops
a player an attendance names, is refused: the previous configuration stays in
force and the server logs the reason once per change.

### Practices file

`data/practices.json` is `{"next_id": k, "practices": [...]}`, rewritten whole,
atomically, after each change; `next_id` only grows, so a practice id is never
reused. A practice is:

- `id`: integer from `next_id`.
- `source`: `calendar` or `manual`.
- `key`: for `calendar` only, what ties it to its event: the event's `UID`, and
  for an occurrence of a recurring event `UID#` and the occurrence's original
  start, UTC, as in `RECURRENCE-ID`, so that moving an occurrence does not make
  it another practice.
- `start`, `end`: UTC instants, ISO 8601 with seconds and `Z`.
- `location`: text, possibly empty.
- `cancelled`: `true` when the calendar no longer holds the practice (see
  *Import*); absent otherwise.
- `attendance`: `null` while nobody has taken it, else `{player-id: status}`,
  `status` being `present` or `absent`. A player the map does not name was not
  expected at that practice.
- `absences`: absent while nobody has announced; else `{player-id: instant}`,
  the UTC instant at which that player announced she will not come (see
  *Notice*). Information for the staff: never part of `attendance`, never
  counted by the statistics, and kept when the attendance is taken.

Practices are kept in order of `start`.

## Tokens

The server has one token for the staff and one for each player of the roster,
never written in the team file: six lowercase letters from `acdefhmnprtu`,
common Italian letters that cannot be taken for a digit or for another letter. A
URL is matched case-insensitively. The server computes them: the SHA-256 of
`<salt>:staff` for the staff, of `<salt>:player:<id>` for a player, read as
base-12 digits, first six. A roster that makes two tokens equal, the staff's
included, is refused like any invalid team file.

The content of the salt file, minus surrounding whitespace, must not be empty;
it is never committed and must be kept, since the links depend on it. The server
does not log a token; `practice-attendance urls` prints the staff link, and with
`--players` the link of each player, each ending in a slash: that is the scope
of the installed app, so a link opens in the app where it is installed. A link
cannot be revoked alone: changing the salt changes them all.

The staff token opens everything. A player token opens only the page and the
record of that player (see *Athlete record*) and, to announce or withdraw her
absence, the two requests of *Notice*: on any other path it gets `404`, as if it
were unknown but without counting against the limit below. The server returns
the players' tokens to the staff alone, in the list of links.

There are 12⁶ = 2,985,984 slugs. To make guessing impractical, requests with an
unknown token are limited per client address (as the server sees it, so behind a
proxy it runs with proxy headers on, trusting a proxy on loopback only; an IPv6
address counts by its /64) to 10 per minute; the next ones get `429`. Requests
with a known token are not limited.

## Window

Attendance is accepted from `opens_at`, 60 minutes before the practice's
`start`, and never locks: it can be corrected afterwards. Before `opens_at` the
write is refused with `409 not_open_yet`. Reads are never refused.

### Notice

A player may announce her absence from `notice_opens_at`, 6 hours before the
practice's `start`, up to `start`, and withdraw it in the same interval. Before
it the request is refused with `409 notice_not_open_yet`, from `start` on with
`409 notice_closed`, and for a cancelled practice with `409 cancelled`.
Announcing twice keeps the first instant; withdrawing what was not announced
changes nothing.

## Import

`POST /api/<tok>/import` reads the calendar and reconciles the practices of
`calendar` source with it, within the `season` and only for the events whose
title begins with `calendar.title`. Events of the whole day, and those without a
start time, are not practices. Recurring events are expanded, their exceptions
and moved occurrences applied.

- An event with no practice of that `key` adds one.
- An event whose practice differs in `start`, `end` or `location` updates it;
  its attendance is kept, whatever the new time.
- A practice of `calendar` source within the season whose event is gone, or has
  `STATUS:CANCELLED`, gets `cancelled: true`, with its attendance kept. A
  cancelled practice whose event comes back loses the mark.
- Practices of `manual` source are never touched.

The import is repeatable: the same calendar gives the same file, byte for byte.
It is atomic: if the calendar cannot be fetched (10 seconds, 5 MB at most) or
parsed, nothing is written and the answer is `502 calendar_unavailable`. Two
imports never run together; one asked while another runs waits for it.

## Derived state

What the server returns.

- `team`: `{name, lang, timezone, season}`.
- `practice`:
  `{id, source, start, end, location, cancelled, taken, opens_at, open}`;
  `taken` is whether `attendance` is not `null`, `opens_at` a UTC instant and
  `open` whether now is past it. In the list it also carries `present` and `of`,
  the counts of its attendance (`null` if not taken), and `announced`, the
  number of players who announced their absence.
- `roster`: `{id: name}`.
- `attendance`: the practice's map, or `null`.
- `announced`: the ids of the players who announced their absence, in the
  roster's order.

### Statistics

For a range of local dates (`from`, `to`, inclusive, by the `start` of the
practice in the team's timezone), the practices counted are those not cancelled;
those taken count for the players they name.

`{from, to, practices, not_taken, players: [{id, name, present, of}]}`:
`practices` the number of practices counted that were taken, `not_taken` the
number of those in the range whose `start` has passed and that nobody has taken
(so a gap in the record shows), `present` and `of` the practices of that player
in which she is `present`, and in which she is named at all. `players` are in
the roster's order.

### Athlete record

What a player's page shows, over the practices of the season (by the local date
of `start`) that are not cancelled, have been taken and name her:

`{team, player, present, of, percent, team_percent, practices, next}`:

- `player`: `{id, name}`, hers.
- `present`, `of`: as in the statistics, over the season.
- `percent`: `present` over `of` as a whole number, halves rounded up; `null`
  when `of` is `0`.
- `team_percent`: the same ratio over every player of the roster, hers included;
  `null` when no player is named. It is the only datum of the others that is
  returned: no other name, count or practice id ever is.
- `practices`: `[{start, end, location, status}]`, newest first, `status` being
  `present` or `absent`.
- `next`: the first practice, not cancelled, whose `start` has not come, as
  `{id, start, end, location, notice_opens_at, notice_open, announced}`
  (`notice_open` whether now is within *Notice*'s interval, `announced` whether
  she has announced); `null` when there is none.

A practice nobody has taken does not appear: a gap in the record is not an
absence.

## Endpoints

URLs are short because they are typed and sent around: a token alone (`<tok>`
below) and a practice by its id (`<p>`), for example `/hmdpet/12`. An unknown
token and an unknown practice give the same `404`, so nothing can be probed.

### Pages

Static HTML in `src/practice_attendance/static/`, with Pico (`vendor/pico/`),
`style.css`, `app.js` and `i18n.json`; the page reads the token from its URL.

- `/`: `404`, `notfound.html`, the page of the link that does not work.
- `/<tok>` and `/<tok>/`: `list.html`, the list of practices, for the staff
  token; `athlete.html`, the record of a player (see *Athlete record*), for a
  player's. The directory form is the scope and start of the installed app.
- `/<tok>/manifest.webmanifest`: the web manifest of that token's page, for the
  staff token and for each player's: `application/manifest+json`, name
  `PracticeAttendance` and short name `Attendance` for everyone,
  `display: standalone`, and `id`, `start_url` and `scope` all `/<tok>/`; icons
  `/icons/icon-192.png`, `/icons/icon-512.png` and
  `/icons/icon-maskable-512.png` (maskable). An unknown token gives the usual
  `404` and counts against the limit on unknown tokens. Every page with a token
  links it, and carries `theme-color`, `apple-touch-icon` and
  `apple-mobile-web-app-title` for Safari, which ignores the manifest; the error
  page and `/install` do not. There is no service worker and nothing is stored
  on the device.
- `/icons/<name>`: one of `icon-192.png`, `icon-512.png`,
  `icon-maskable-512.png` (any other name is `404`): the file of that name
  beside the team file if it is there, else the neutral icon of `static/icons/`
  (made by `scripts/make-icons` from the SVGs there). No token, not counted by
  the limit.
- `/install`: `install.html`, how to add the app to the home screen (Android
  Chrome, iPhone Safari), in Italian or, with `?lang=en`, English; no token, not
  counted by the limit, no manifest. Every other page, the error page included,
  links it from its foot.
- `/<tok>/<p>`: `practice.html`, the screen of one practice, where the staff
  ticks who is present.
- `/<tok>/stats`: `stats.html`, the statistics.
- `/<tok>/links`: `links.html`, the players' links, to copy and send, for the
  staff token only.
- `/privacy`: the privacy notice, the file `privacy.html` beside the team file
  (`404` when it is not there); no token, and not counted by the limit on
  unknown tokens.
- `/static/...`: scripts, style and `i18n.json`.
- `/favicon.ico`: `204`; it names no token and is not counted by the limit.
- `?lang=xx` overrides the team's language on any page.

A page request that ends in `404` or `429` is answered with the status and the
HTML of a page that says the link does not work and whom to ask. Under `/api`
the answer stays JSON.

### API

The same paths under `/api`, mirroring the pages (never typed, so no need to
shorten further).

- `GET /api/<tok>`: for the staff token `{team, practices, imported_at}`,
  `practices` a list by `start` of the practices that are not cancelled, then of
  the cancelled ones that have attendance; `imported_at` the UTC instant of the
  last import, or `null`. For a player's, her record (see *Athlete record*), and
  it is the only request she can make.
- `GET /api/<tok>/<p>`: `{team, practice, roster, attendance, announced}`.
- `PUT /api/<tok>/<p>/attendance`: body `{attendance: {player-id: status}}`,
  replacing the whole map. `200`, the same body as the `GET`. `409 not_open_yet`
  before `opens_at`, `409 cancelled` for a cancelled practice,
  `422 unknown_player` for an id the roster lacks, `422 invalid_value` for a
  status that is neither `present` nor `absent`. The last write wins; the
  journal keeps what it replaced.
- `POST /api/<tok>/practices`: body `{start, end?, location?}` (`start` and
  `end` UTC instants; `end` one hour after `start` when missing); adds a
  practice of `manual` source. `201`, the practice. `422 invalid_value` if `end`
  is not after `start`.
- `PATCH /api/<tok>/<p>`: body with any of `start`, `end`, `location`, for a
  `manual` practice. `200`, the practice. `409 imported_practice` for one of
  `calendar` source, which the calendar changes.
- `DELETE /api/<tok>/<p>`: a `manual` practice with no attendance. `200`, the
  list. `409 imported_practice`, or `409 has_attendance`.
- `POST /api/<tok>/import`: see *Import*. `200`,
  `{added, updated, cancelled, restored}`, the numbers of practices.
  `409 no_calendar` when the team file has none, `502 calendar_unavailable`.
- `GET /api/<tok>/stats?from=YYYY-MM-DD&to=YYYY-MM-DD`: the statistics; the
  range defaults to the season. `422 invalid_value` for a malformed date or
  `from` after `to`.
- `GET /api/<tok>/links`: `{team, players: [{id, name, token}]}`, in the
  roster's order; the staff token only.
- `PUT /api/<tok>/<p>/absence` and `DELETE /api/<tok>/<p>/absence`: a player's
  token only, for her own absence (see *Notice*); `200`, her record. `404` for
  the staff token, `409 notice_not_open_yet`, `409 notice_closed`,
  `409 cancelled`.

### Errors

Status `404` (unknown token or practice, or a path a player's token does not
open), `409` (the request breaks a rule), `422` (the request is malformed),
`429` (too many unknown tokens from one address, see *Tokens*), `502` (the
calendar cannot be read), `500` (an error of the server, logged in
`logs/app.log` with its traceback). Body `{"error": code, "detail": text}`, with
`code` among `not_open_yet`, `cancelled`, `imported_practice`, `has_attendance`,
`no_calendar`, `notice_not_open_yet`, `notice_closed`, `unknown_player`,
`invalid_value`, `not_found`, `rate_limited`, `calendar_unavailable`,
`server_error`.

## Strings

The keys are those of `i18n.json`, which is their one list: one for each text a
page shows, and `err_<code>` for each error code of the contract. A value may
hold `{0}`, replaced by an argument. A key missing from a language falls back to
`en`; an `err_<code>` missing from both is shown as the server's `detail`.
Names, dates and numbers come from data and `Intl`, in the page's language. A
test checks that every language has the same keys, that every key a script names
exists and every other key is used, and that every error code has a message.
