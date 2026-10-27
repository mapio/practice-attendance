// The pages: one script, the page named by <body data-page>.
"use strict";

const TOKEN = location.pathname.split("/")[1] || "";
const PARAMS = new URLSearchParams(location.search);
const TIMEOUT_MS = 20000;

// The manifest is the token's own: the pages that have a token link it, the
// error page, where the token may be wrong, and the install page do not.
// Safari ignores the manifest, so the page says the same for itself.
if (!["notfound", "install"].includes(document.body.dataset.page)) {
  const meta = (name, content, media) => h("meta", { name, content, media });
  document.head.append(
    h("link", { rel: "manifest", href: `/${TOKEN}/manifest.webmanifest` }),
    h("link", { rel: "apple-touch-icon", href: "/icons/icon-192.png" }),
    meta("apple-mobile-web-app-title", "Attendance"),
    meta("theme-color", "#f6f7f9", "(prefers-color-scheme: light)"),
    meta("theme-color", "#12151c", "(prefers-color-scheme: dark)"),
  );
}

let I18N = {};
let LANG = "it";
let TZ = "Europe/Rome";

// ---- small helpers

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

function lookup(key) {
  return (I18N[LANG] || {})[key] ?? (I18N.en || {})[key];
}

function t(key, ...args) {
  const s = lookup(key) ?? key;
  return s.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
}

// With the query kept, ?lang=xx survives the navigation.
function link(path) {
  const lang = PARAMS.get("lang");
  return `/${TOKEN}${path}` + (lang ? `?lang=${encodeURIComponent(lang)}` : "");
}

async function loadStrings(teamLang) {
  if (!Object.keys(I18N).length) {
    I18N = await (await fetch("/static/i18n.json")).json();
  }
  const want = PARAMS.get("lang") || teamLang || "it";
  LANG = I18N[want] ? want : "it";
  document.documentElement.lang = LANG;
}

function applyStaticStrings() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  document.title = t(document.body.dataset.title || "app_title");
  if (
    document.body.dataset.page !== "install" &&
    !document.getElementById("foot")
  ) {
    const lang = PARAMS.get("lang");
    document.body.append(
      h(
        "p",
        { id: "foot", class: "muted small foot" },
        h(
          "a",
          {
            href:
              "/install" + (lang ? `?lang=${encodeURIComponent(lang)}` : ""),
          },
          t("install_link"),
        ),
      ),
    );
  }
}

// ---- dates, in the team's timezone

function parts(date, opts) {
  const out = {};
  for (const p of new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ,
    hourCycle: "h23",
    ...opts,
  }).formatToParts(date)) {
    out[p.type] = p.value;
  }
  return out;
}

function fmt(date, opts) {
  return new Intl.DateTimeFormat(LANG, { timeZone: TZ, ...opts }).format(date);
}

const fmtDay = (iso) =>
  fmt(new Date(iso), { weekday: "short", day: "numeric", month: "short" });
const fmtTime = (iso) =>
  fmt(new Date(iso), { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const fmtLong = (iso) =>
  fmt(new Date(iso), { weekday: "long", day: "numeric", month: "long" });

function localDate(iso) {
  const p = parts(new Date(iso), {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return `${p.year}-${p.month}-${p.day}`;
}

function localTime(iso) {
  const p = parts(new Date(iso), { hour: "2-digit", minute: "2-digit" });
  return `${p.hour}:${p.minute}`;
}

// The UTC instant of a wall-clock time in the team's timezone.
function zonedToUtc(day, time) {
  const [y, mo, d] = day.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const wall = Date.UTC(y, mo - 1, d, hh, mm);
  let guess = wall;
  for (let i = 0; i < 2; i++) {
    const p = parts(new Date(guess), {
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
    guess += wall - shown;
  }
  return new Date(guess).toISOString().replace(/\.\d+Z$/, "Z");
}

// ---- the API

class ApiError extends Error {
  constructor(status, code, detail) {
    super(detail || code);
    this.status = status;
    this.code = code;
  }
}

async function api(method, path, body) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(`/api/${TOKEN}${path}`, {
      method,
      signal: ctl.signal,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network", "");
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    if (res.ok) throw new ApiError(res.status, "server_error", "");
  }
  if (!res.ok)
    throw new ApiError(
      res.status,
      data?.error || "server_error",
      data?.detail || "",
    );
  return data;
}

function errorText(e) {
  return lookup(`err_${e.code}`) ?? (e.detail || t("err_server_error"));
}

// ---- shared parts of a page

function frame(team, current) {
  const nav = h(
    "nav",
    {},
    h(
      "a",
      { href: link(""), "aria-current": current === "list" ? "page" : false },
      t("nav_practices"),
    ),
    h(
      "a",
      {
        href: link("/stats"),
        "aria-current": current === "stats" ? "page" : false,
      },
      t("nav_stats"),
    ),
    h(
      "a",
      {
        href: link("/links"),
        "aria-current": current === "links" ? "page" : false,
      },
      t("nav_links"),
    ),
  );
  document
    .getElementById("top")
    .replaceChildren(h("strong", {}, team.name), nav);
}

function showError(root, e, retry) {
  root.replaceChildren(
    h(
      "div",
      { class: "msg err", role: "alert" },
      h("p", {}, errorText(e)),
      retry && h("button", { class: "btn", onclick: retry }, t("retry")),
    ),
  );
}

// Runs load(), showing "loading" first and an error with Retry on failure.
async function guarded(root, load, render) {
  root.replaceChildren(
    h("p", { class: "muted", role: "status" }, t("loading")),
  );
  try {
    render(await load());
  } catch (e) {
    showError(root, e, () => guarded(root, load, render));
  }
}

function flash(el, kind, text) {
  el.className = `msg ${kind}`;
  el.textContent = text;
  el.hidden = !text;
}

// Disables a button and shows the busy label while fn runs.
async function busy(btn, label, fn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

// A dialog with the fields of a manual practice; onSave gets {start, end, location}.
function practiceDialog(practice, { onSave, onDelete }) {
  const day = h("input", {
    type: "date",
    required: true,
    value: practice ? localDate(practice.start) : "",
  });
  const from = h("input", {
    type: "time",
    required: true,
    value: practice ? localTime(practice.start) : "18:00",
  });
  const to = h("input", {
    type: "time",
    required: true,
    value: practice ? localTime(practice.end) : "19:30",
  });
  const place = h("input", {
    type: "text",
    value: practice?.location || "",
    maxlength: "100",
  });
  const status = h("div", { class: "msg err", role: "alert", hidden: true });
  const save = h("button", { class: "btn primary", type: "submit" }, t("save"));
  const dlg = h("dialog", {});
  const form = h(
    "form",
    { method: "dialog" },
    h("h2", {}, t(practice ? "edit_title" : "add_title")),
    h("label", {}, t("field_day"), day),
    h(
      "div",
      { class: "pair" },
      h("label", {}, t("field_from"), from),
      h("label", {}, t("field_to"), to),
    ),
    h("label", {}, t("field_location"), place),
    status,
    h(
      "div",
      { class: "actions" },
      save,
      h(
        "button",
        {
          class: "btn secondary",
          type: "button",
          onclick: () => dlg.close(),
        },
        t("cancel"),
      ),
      onDelete &&
        h(
          "button",
          {
            class: "btn danger",
            type: "button",
            onclick: async (ev) => {
              if (!confirm(t("confirm_delete"))) return;
              await busy(ev.target, t("saving"), async () => {
                try {
                  await onDelete();
                } catch (e) {
                  flash(status, "err", errorText(e));
                }
              });
            },
          },
          t("delete"),
        ),
    ),
  );
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    flash(status, "err", "");
    const body = {
      start: zonedToUtc(day.value, from.value),
      end: zonedToUtc(day.value, to.value),
      location: place.value.trim(),
    };
    await busy(save, t("saving"), async () => {
      try {
        await onSave(body);
      } catch (e) {
        flash(status, "err", errorText(e));
      }
    });
  });
  dlg.append(h("article", {}, form));
  // A page re-renders after a change: the previous dialog must not pile up.
  for (const old of document.querySelectorAll("dialog")) old.remove();
  document.body.append(dlg);
  return dlg;
}

// ---- list page

function practiceCard(p, now) {
  const upcoming = new Date(p.end) >= now;
  let state;
  if (p.cancelled)
    state = h("span", { class: "tag cancelled" }, t("cancelled"));
  else if (p.taken)
    state = h("span", { class: "tag taken" }, `${p.present}/${p.of}`);
  else if (!upcoming)
    state = h("span", { class: "tag missing" }, t("not_taken"));
  else if (!p.open)
    state = h(
      "span",
      { class: "tag soon" },
      t("opens_at", fmtTime(p.opens_at)),
    );
  else state = h("span", { class: "tag todo" }, t("to_take"));
  return h(
    "a",
    {
      class: `card${p.cancelled ? " is-cancelled" : ""}`,
      href: link(`/${p.id}`),
    },
    h(
      "div",
      { class: "when" },
      h("span", { class: "day" }, fmtDay(p.start)),
      h("span", { class: "time" }, `${fmtTime(p.start)}-${fmtTime(p.end)}`),
    ),
    h(
      "div",
      { class: "where" },
      p.location,
      p.source === "manual" && h("span", { class: "tag manual" }, t("manual")),
      p.announced > 0 &&
        !p.taken &&
        !p.cancelled &&
        h("span", { class: "tag soon" }, t("announced_count", p.announced)),
    ),
    state,
  );
}

function renderList(root, data) {
  TZ = data.team.timezone;
  const now = new Date();
  const upcoming = data.practices.filter((p) => new Date(p.end) >= now);
  const past = data.practices.filter((p) => new Date(p.end) < now).reverse();
  const status = h("div", { class: "msg", role: "status", hidden: true });
  const importBtn = h("button", { class: "btn" }, t("import"));
  importBtn.addEventListener("click", () =>
    busy(importBtn, t("importing"), async () => {
      try {
        const r = await api("POST", "/import");
        const none = !(r.added || r.updated || r.cancelled || r.restored);
        await refresh();
        flash(
          root.querySelector(".msg[role=status]"),
          "ok",
          none
            ? t("import_none")
            : t("import_result", r.added, r.updated, r.cancelled, r.restored),
        );
      } catch (e) {
        flash(status, "err", errorText(e));
      }
    }),
  );
  const dlg = practiceDialog(null, {
    onSave: async (body) => {
      await api("POST", "/practices", body);
      dlg.close();
      await refresh();
    },
  });
  const group = (title, ps) =>
    h(
      "section",
      {},
      h("h2", {}, title),
      ps.length
        ? ps.map((p) => practiceCard(p, now))
        : h("p", { class: "muted" }, t("none")),
    );
  root.replaceChildren(
    h(
      "div",
      { class: "toolbar" },
      importBtn,
      h(
        "button",
        { class: "btn", onclick: () => dlg.showModal() },
        t("add_practice"),
      ),
    ),
    h(
      "p",
      { class: "muted small" },
      data.imported_at
        ? t(
            "imported_at",
            fmt(new Date(data.imported_at), {
              day: "numeric",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
              hourCycle: "h23",
            }),
          )
        : t("never_imported"),
    ),
    status,
    group(t("upcoming"), upcoming),
    group(t("past"), past),
  );
}

async function refresh() {
  const data = await api("GET", "");
  renderList(document.getElementById("app"), data);
  frame(data.team, "list");
}

async function listPage() {
  const root = document.getElementById("app");
  await guarded(
    root,
    async () => {
      const data = await api("GET", "");
      await loadStrings(data.team.lang);
      applyStaticStrings();
      TZ = data.team.timezone;
      frame(data.team, "list");
      return data;
    },
    (data) => renderList(root, data),
  );
}

// ---- practice page

function renderPractice(root, data, reload) {
  const { practice, roster } = data;
  TZ = data.team.timezone;
  const ids = Object.keys(roster);
  const taken = data.attendance !== null;
  // id -> "present" | "absent"; missing: not expected at this practice.
  const att = {};
  for (const id of ids) {
    if (!taken) att[id] = "absent";
    else if (data.attendance[id]) att[id] = data.attendance[id];
  }
  let dirty = false;
  const readOnly = practice.cancelled;
  const count = h("span", { class: "count", "aria-live": "polite" });
  const rows = new Map();
  const msg = h("div", { class: "msg", role: "status", hidden: true });
  const save = h(
    "button",
    { class: "btn primary big", disabled: readOnly || !practice.open },
    t("save"),
  );

  const update = () => {
    const named = Object.keys(att);
    const present = named.filter((id) => att[id] === "present").length;
    count.textContent = `${present}/${named.length}`;
    for (const [id, row] of rows) {
      row.setAttribute("aria-pressed", att[id] === "present");
      row.classList.toggle("unexpected", att[id] === undefined);
      row.querySelector(".state").textContent =
        att[id] === "present"
          ? t("present")
          : att[id] === "absent"
            ? t("absent")
            : t("not_expected");
    }
  };
  const touch = () => {
    dirty = true;
    flash(msg, "warn", t("unsaved"));
    update();
  };
  const setAll = (status) => {
    for (const id of ids) att[id] = status;
    touch();
  };

  for (const id of ids) {
    const row = h(
      "button",
      {
        class: "player",
        type: "button",
        disabled: readOnly,
        onclick: () => {
          att[id] = att[id] === "present" ? "absent" : "present";
          touch();
        },
      },
      h("span", { class: "check", "aria-hidden": "true" }),
      h(
        "span",
        { class: "name" },
        roster[id],
        data.announced.includes(id) &&
          h("span", { class: "tag soon" }, t("announced_mark")),
      ),
      h("span", { class: "state" }),
    );
    rows.set(id, row);
  }

  save.addEventListener("click", () =>
    busy(save, t("saving"), async () => {
      try {
        const res = await api("PUT", `/${practice.id}/attendance`, {
          attendance: att,
        });
        dirty = false;
        const ok = Object.values(res.attendance).filter(
          (s) => s === "present",
        ).length;
        flash(msg, "ok", t("saved", ok, Object.keys(res.attendance).length));
        msg.append(" ", h("a", { href: link("") }, t("back_to_list")));
      } catch (e) {
        flash(msg, "err", errorText(e));
      }
    }),
  );
  window.addEventListener("beforeunload", (ev) => {
    if (dirty) ev.preventDefault();
  });

  const notes = [];
  if (practice.cancelled)
    notes.push(h("div", { class: "msg err" }, t("cancelled_note")));
  else if (!practice.open)
    notes.push(
      h(
        "div",
        { class: "msg warn" },
        t("not_open_note", fmtTime(practice.opens_at)),
      ),
    );
  else if (!taken) notes.push(h("div", { class: "msg" }, t("not_taken_note")));

  let edit = null;
  if (practice.source === "manual") {
    const dlg = practiceDialog(practice, {
      onSave: async (body) => {
        await api("PATCH", `/${practice.id}`, body);
        dlg.close();
        reload();
      },
      onDelete: async () => {
        dirty = false;
        await api("DELETE", `/${practice.id}`);
        location.href = link("");
      },
    });
    edit = h(
      "button",
      { class: "btn", onclick: () => dlg.showModal() },
      t("edit"),
    );
  }

  root.replaceChildren(
    h("a", { class: "back", href: link("") }, `← ${t("back_to_list")}`),
    h(
      "header",
      { class: "pinfo" },
      h("h1", {}, fmtLong(practice.start)),
      h(
        "p",
        {},
        `${fmtTime(practice.start)}-${fmtTime(practice.end)}`,
        practice.location && ` · ${practice.location}`,
        practice.source === "manual" &&
          h("span", { class: "tag manual" }, t("manual")),
      ),
      edit,
    ),
    ...notes,
    h(
      "div",
      { class: "sticky" },
      h(
        "div",
        { class: "countbar" },
        count,
        h("span", { class: "muted" }, t("present_label")),
      ),
      h(
        "div",
        { class: "shortcuts" },
        h(
          "button",
          {
            class: "btn",
            disabled: readOnly,
            onclick: () => setAll("present"),
          },
          t("all_present"),
        ),
        h(
          "button",
          { class: "btn", disabled: readOnly, onclick: () => setAll("absent") },
          t("reset"),
        ),
      ),
    ),
    h("div", { class: "players" }, [...rows.values()]),
    h("div", { class: "savebar" }, msg, save),
  );
  update();
}

async function practicePage() {
  const root = document.getElementById("app");
  const id = location.pathname.split("/")[2];
  const load = async () => {
    const data = await api("GET", `/${id}`);
    await loadStrings(data.team.lang);
    applyStaticStrings();
    TZ = data.team.timezone;
    frame(data.team, "list");
    return data;
  };
  const go = () =>
    guarded(root, load, (data) => renderPractice(root, data, go));
  await go();
}

// ---- stats page

function monthRange(value) {
  const [y, m] = value.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${value}-01`, `${value}-${String(last).padStart(2, "0")}`];
}

function renderStats(root, stats) {
  const rows = stats.players.map((p) => {
    const pct = p.of ? Math.round((100 * p.present) / p.of) : 0;
    return h(
      "li",
      { class: "stat" },
      h("span", { class: "name" }, p.name),
      h(
        "span",
        { class: "bar", role: "img", "aria-label": `${p.present}/${p.of}` },
        h("span", { style: `width:${pct}%` }),
      ),
      h("span", { class: "num" }, `${p.present}/${p.of}`),
    );
  });
  root.replaceChildren(
    h(
      "p",
      { class: "muted" },
      t("stats_summary", stats.practices, stats.not_taken),
    ),
    stats.practices
      ? h("ul", { class: "stats" }, rows)
      : h("p", { class: "muted" }, t("stats_empty")),
  );
}

async function statsPage() {
  const result = document.getElementById("result");
  const controls = document.getElementById("controls");
  let first;
  try {
    first = await api("GET", "/stats");
    const info = await api("GET", "");
    await loadStrings(info.team.lang);
    applyStaticStrings();
    TZ = info.team.timezone;
    frame(info.team, "stats");
    first.season = info.team.season;
  } catch (e) {
    await loadStrings(null);
    applyStaticStrings();
    showError(result, e, () => location.reload());
    return;
  }
  const season = first.season;
  const today = localDate(new Date().toISOString());
  const from = h("input", { type: "date", value: season.from });
  const to = h("input", { type: "date", value: season.to });
  const month = h("input", { type: "month", value: today.slice(0, 7) });
  const custom = h(
    "div",
    { class: "pair" },
    h("label", {}, t("field_from"), from),
    h("label", {}, t("field_to"), to),
  );
  const monthBox = h("label", {}, t("range_month"), month);
  let mode = "season";

  const range = () =>
    mode === "season"
      ? [season.from, season.to]
      : mode === "month"
        ? monthRange(month.value || today.slice(0, 7))
        : [from.value, to.value];
  const run = () =>
    guarded(
      result,
      () => api("GET", `/stats?from=${range()[0]}&to=${range()[1]}`),
      (s) => renderStats(result, s),
    );
  const show = () => {
    monthBox.hidden = mode !== "month";
    custom.hidden = mode !== "custom";
    for (const b of controls.querySelectorAll("[data-mode]"))
      setPressed(b, b.dataset.mode === mode);
  };
  // The chosen range is the filled button, the others are outlined.
  const setPressed = (b, on) => {
    b.setAttribute("aria-pressed", on);
    b.classList.toggle("outline", !on);
  };
  const tab = (m, label) =>
    h(
      "button",
      {
        type: "button",
        "data-mode": m,
        onclick: () => {
          mode = m;
          show();
          run();
        },
      },
      label,
    );

  controls.replaceChildren(
    h(
      "div",
      { class: "toolbar" },
      tab("season", t("range_season")),
      tab("month", t("range_month")),
      tab("custom", t("range_custom")),
    ),
    monthBox,
    custom,
  );
  for (const el of [month, from, to]) el.addEventListener("change", run);
  show();
  renderStats(result, first);
}

// ---- athlete page: one player's own record, read only

// The next practice, with the button to announce an absence while it is allowed.
function nextPractice(next, show) {
  if (!next) return null;
  const msg = h("div", { class: "msg err", role: "alert", hidden: true });
  const send = (method, label) => {
    const btn = h(
      "button",
      {
        class: method === "PUT" ? "" : "secondary",
        onclick: () =>
          busy(btn, t("saving"), async () => {
            try {
              show(await api(method, `/${next.id}/absence`));
            } catch (e) {
              flash(msg, "err", errorText(e));
            }
          }),
      },
      label,
    );
    return btn;
  };
  const opens = `${fmtDay(next.notice_opens_at)} ${fmtTime(next.notice_opens_at)}`;
  return h(
    "article",
    { class: "next" },
    h("h2", {}, t("next_practice")),
    h(
      "p",
      {},
      h("strong", {}, fmtLong(next.start)),
      ` ${fmtTime(next.start)}-${fmtTime(next.end)}`,
      next.location && ` · ${next.location}`,
    ),
    next.announced && h("p", { class: "msg ok" }, t("notice_done")),
    next.announced && next.notice_open && send("DELETE", t("notice_withdraw")),
    !next.announced &&
      (next.notice_open
        ? send("PUT", t("notice_announce"))
        : h("p", { class: "muted" }, t("notice_later", opens))),
    msg,
  );
}

function renderAthlete(root, record) {
  const rows = record.practices.map((p) =>
    h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "when" },
        h("span", { class: "day" }, fmtDay(p.start)),
        h("span", { class: "time" }, `${fmtTime(p.start)}-${fmtTime(p.end)}`),
      ),
      h("div", { class: "where" }, p.location),
      h(
        "span",
        { class: `tag ${p.status === "present" ? "taken" : "missing"}` },
        t(p.status),
      ),
    ),
  );
  // replaceChildren would print a missing part as "null" or "0"
  const blocks = [
    h("h1", {}, record.player.name),
    nextPractice(record.next, (r) => renderAthlete(root, r)),
    record.of
      ? h(
          "p",
          { class: "summary" },
          t("athlete_summary", record.present, record.of, record.percent),
        )
      : h("p", { class: "muted" }, t("athlete_empty")),
    record.of
      ? h(
          "div",
          {
            class: "bar",
            role: "img",
            "aria-label": `${record.present}/${record.of}`,
          },
          h("span", { style: `width:${record.percent}%` }),
        )
      : null,
    record.team_percent != null &&
      h("p", { class: "muted" }, t("athlete_team", record.team_percent)),
    rows.length && h("section", {}, h("h2", {}, t("athlete_practices")), rows),
    h(
      "p",
      { class: "muted small" },
      h("a", { href: "/privacy" }, t("privacy")),
    ),
  ];
  root.replaceChildren(...blocks.filter(Boolean));
}

async function athletePage() {
  const root = document.getElementById("app");
  await guarded(
    root,
    async () => {
      const record = await api("GET", "");
      await loadStrings(record.team.lang);
      applyStaticStrings();
      TZ = record.team.timezone;
      document
        .getElementById("top")
        .replaceChildren(h("strong", {}, record.team.name));
      return record;
    },
    (record) => renderAthlete(root, record),
  );
}

// ---- links page: each player's link, to copy and send

function renderLinks(root, data) {
  const msg = h("div", { class: "msg warn", role: "status", hidden: true });
  const rows = data.players.map((p) => {
    const url = `${location.origin}/${p.token}`;
    const field = h("input", {
      class: "url",
      readonly: true,
      value: url,
      "aria-label": p.name,
    });
    const btn = h("button", { class: "btn" }, t("copy"));
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(url);
        flash(msg, "warn", "");
        btn.textContent = t("copied");
        setTimeout(() => (btn.textContent = t("copy")), 1500);
      } catch {
        field.focus();
        field.select();
        flash(msg, "warn", t("copy_manual"));
      }
    });
    return h(
      "li",
      { class: "linkrow" },
      h("span", { class: "name" }, p.name),
      field,
      btn,
    );
  });
  root.replaceChildren(
    h("p", { class: "muted" }, t("links_note")),
    msg,
    h("ul", { class: "links" }, rows),
  );
}

async function linksPage() {
  const root = document.getElementById("app");
  await guarded(
    root,
    async () => {
      const data = await api("GET", "/links");
      await loadStrings(data.team.lang);
      applyStaticStrings();
      TZ = data.team.timezone;
      frame(data.team, "links");
      return data;
    },
    (data) => renderLinks(root, data),
  );
}

// ---- not found

async function notFoundPage() {
  try {
    await loadStrings(null);
  } catch {
    // no strings: the keys show
  }
  applyStaticStrings();
}

// ---- install page: the same for everyone, so no token

async function installPage() {
  try {
    await loadStrings(null);
  } catch {
    // no strings: the keys show
  }
  applyStaticStrings();
  const other = LANG === "it" ? "en" : "it";
  document
    .getElementById("app")
    .replaceChildren(
      h("h1", {}, t("install_title")),
      h("h2", {}, t("install_android")),
      h(
        "ol",
        {},
        h("li", {}, t("install_android_1")),
        h("li", {}, t("install_android_2")),
      ),
      h("h2", {}, t("install_iphone")),
      h(
        "ol",
        {},
        h("li", {}, t("install_iphone_1")),
        h("li", {}, t("install_iphone_2")),
      ),
      h("p", {}, h("small", {}, t("install_note"))),
      h(
        "p",
        {},
        h("a", { href: `/install?lang=${other}` }, t("install_other_lang")),
      ),
    );
}

// ---- start

const PAGES = {
  list: listPage,
  practice: practicePage,
  stats: statsPage,
  athlete: athletePage,
  links: linksPage,
  notfound: notFoundPage,
  install: installPage,
};
PAGES[document.body.dataset.page]?.();
