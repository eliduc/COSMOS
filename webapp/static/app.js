/* Pipeline web UI (v1) — vanilla JS against the API contract in
 * webapp/API-CONTRACT.md. No frameworks, no CDNs, works offline.
 * Polling only: running jobs are polled via GET /api/jobs/{id} every 2 s.
 * All user-visible strings go through t()/tf() from i18n.js (loaded
 * before this file); the "crew:lang" event re-renders everything visible.
 */
"use strict";

var API = "/api";
var POLL_MS = 2000;

/* ---------------------------------------------------------------- helpers */

function $(id) { return document.getElementById(id); }

/** Create a DOM element; attrs.text sets textContent (no innerHTML for data). */
function el(tag, attrs, children) {
  var node = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      if (k === "text") { node.textContent = attrs[k]; }
      else if (k === "class") { node.className = attrs[k]; }
      else { node.setAttribute(k, attrs[k]); }
    });
  }
  (children || []).forEach(function (c) { node.appendChild(c); });
  return node;
}

/** House keyboard pattern (mirrors the tour-leaf in onboarding.js): make a
 *  click-only non-button element operable from the keyboard — role="button",
 *  tabindex="0", Enter/Space fire the same action as click. */
function keyActivate(node, fn) {
  node.setAttribute("role", "button");
  node.setAttribute("tabindex", "0");
  node.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  });
}

/** Render a possibly-missing scalar; the contract does not guarantee every
 *  field on every instance, so missing values become an em dash. */
function fmt(v) {
  if (v === null || v === undefined || v === "") { return "—"; }
  return String(v);
}

function fmtBytes(n) {
  if (typeof n !== "number" || !isFinite(n)) { return "—"; }
  if (n < 1024) { return n + " " + t("app.bytes.b"); }
  if (n < 1024 * 1024) { return (n / 1024).toFixed(1) + " " + t("app.bytes.kb"); }
  return (n / (1024 * 1024)).toFixed(1) + " " + t("app.bytes.mb");
}

/** BCP-47 tag for the active UI language, so numbers/dates localize for all
 *  five languages, not just en/ru (audit 2026-07-15). */
function localeTag() {
  return { ru: "ru-RU", en: "en-US", uz: "uz-Latn-UZ",
           kk: "kk-KZ", ar: "ar" }[uiLang()] || "en-US";
}

function fmtTime(iso) {
  if (!iso) { return "—"; }
  var d = new Date(iso);
  if (isNaN(d.getTime())) { return String(iso); }
  return d.toLocaleString(localeTag());
}

function toast(message, kind) {
  var box = el("div", { class: "toast " + (kind || "error"), text: message });
  $("toasts").appendChild(box);
  setTimeout(function () {
    box.classList.add("fade");
    setTimeout(function () { box.remove(); }, 400);
  }, 6000);
}

/** In-app confirmation dialog (replaces window.confirm so it matches the
 *  app's look). Returns a Promise<boolean>. Esc / backdrop = cancel,
 *  Enter = confirm. opts.danger paints the confirm button red. */
function appConfirm(message, opts) {
  opts = opts || {};
  return new Promise(function (resolve) {
    var done = false;
    function close(val) {
      if (done) { return; }
      done = true;
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(val);
    }
    var cancel = el("button", { class: "btn btn-ghost", type: "button",
                                text: opts.cancel || t("app.confirm.cancel") });
    var ok = el("button", { type: "button",
                            class: "btn " + (opts.danger ? "btn-danger" : "btn-primary"),
                            text: opts.ok || t("app.confirm.ok") });
    cancel.addEventListener("click", function () { close(false); });
    ok.addEventListener("click", function () { close(true); });
    // opts.details — список пунктов под текстом: диалог должен называть
    // ПРИЧИНЫ, а не просто спрашивать «вы уверены».
    var parts = [el("p", { class: "confirm-msg", text: message })];
    if (opts.details && opts.details.length) {
      parts.push(el("ul", { class: "confirm-details" },
        opts.details.map(function (d) { return el("li", { text: d }); })));
    }
    parts.push(el("div", { class: "confirm-actions" }, [cancel, ok]));
    var msgId = "confirm-msg-" + Date.now();
    parts[0].id = msgId;
    var dialog = el("div", { class: "confirm-dialog", role: "alertdialog",
                             "aria-modal": "true",
                             "aria-labelledby": msgId }, parts);
    var overlay = el("div", { class: "confirm-overlay" }, [dialog]);
    overlay.addEventListener("mousedown", function (e) {
      if (e.target === overlay) { close(false); }
    });
    // Enter подтверждает ТОЛЬКО когда фокус не стоит на «Отмене»: клавиша
    // активирует сфокусированную кнопку, а не решение по умолчанию (внешний
    // аудит R6, P0-08 — Enter на «Отмене» подтверждал опасное действие).
    // Tab заперт внутри диалога: за его пределами активировать нечего.
    function onKey(e) {
      if (e.key === "Escape") { close(false); }
      else if (e.key === "Enter") {
        close(document.activeElement !== cancel);
      } else if (e.key === "Tab") {
        e.preventDefault();
        (document.activeElement === ok ? cancel : ok).focus();
      }
    }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    ok.focus();
  });
}

/** Modal text prompt (mirrors appConfirm). Resolves the entered string, or
 *  null on cancel/escape. */
function appPrompt(message, opts) {
  opts = opts || {};
  return new Promise(function (resolve) {
    var done = false;
    function close(val) {
      if (done) { return; }
      done = true;
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(val);
    }
    var input = el("input", { type: "text", class: "confirm-input" });
    if (opts.value) { input.value = opts.value; }
    var cancel = el("button", { class: "btn btn-ghost", type: "button",
                                text: t("app.confirm.cancel") });
    var ok = el("button", { class: "btn btn-primary", type: "button",
                            text: opts.ok || t("app.confirm.ok") });
    cancel.addEventListener("click", function () { close(null); });
    ok.addEventListener("click", function () { close(input.value); });
    var msg = el("p", { class: "confirm-msg", text: message });
    msg.id = "prompt-msg-" + Date.now();
    input.setAttribute("aria-labelledby", msg.id);
    var dialog = el("div", { class: "confirm-dialog", role: "alertdialog",
                             "aria-modal": "true",
                             "aria-labelledby": msg.id }, [
      msg, input,
      el("div", { class: "confirm-actions" }, [cancel, ok])
    ]);
    var overlay = el("div", { class: "confirm-overlay" }, [dialog]);
    overlay.addEventListener("mousedown", function (e) {
      if (e.target === overlay) { close(null); }
    });
    // Как в appConfirm: Enter на «Отмене» отменяет, а не подтверждает;
    // Tab ходит по кольцу поле → отмена → ок.
    function onKey(e) {
      if (e.key === "Escape") { close(null); }
      else if (e.key === "Enter") {
        close(document.activeElement === cancel ? null : input.value);
      } else if (e.key === "Tab") {
        e.preventDefault();
        var ring = [input, cancel, ok];
        var i = ring.indexOf(document.activeElement);
        ring[(i + (e.shiftKey ? ring.length - 1 : 1)) % ring.length].focus();
      }
    }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    input.focus();
  });
}

/** fetch wrapper: resolves with parsed JSON, rejects with an Error carrying
 *  .status and .body (parsed JSON if possible). Network failures are loud. */
function api(path, options) {
  return fetch(API + path, options).then(function (resp) {
    return resp.text().then(function (text) {
      var body = null;
      if (text) {
        try { body = JSON.parse(text); } catch (e) { body = text; }
      }
      if (resp.status === 401) {
        // Session missing/expired. In token mode the whole app is behind
        // /login; in Cloudflare Access mode there is no app login page —
        // surface the error instead of bouncing to a dead URL. Redirect
        // only when the mode is POSITIVELY known to be token: before
        // /api/me resolves, state.me is null and the mode is unknown.
        if (state.me && state.me.mode === "token") {
          window.location.href = "/login";
        }
        var authErr = new Error(t("app.t.needLogin"));
        authErr.status = 401;
        authErr.body = body;
        throw authErr;
      }
      if (!resp.ok) {
        var err = new Error("HTTP " + resp.status + " " + path);
        err.status = resp.status;
        err.body = body;
        throw err;
      }
      return body;
    });
  }, function () {
    var err = new Error(tf("app.t.noConn", { p: path }));
    err.status = 0;
    throw err;
  });
}

function apiErrorToast(err, prefix) {
  var msg = prefix ? prefix + ": " : "";
  if (err.status === 0) { msg += err.message; }
  else if (err.body && typeof err.body === "object" && err.body.detail) {
    msg += "HTTP " + err.status + " — " + JSON.stringify(err.body.detail);
  } else {
    msg += err.message;
  }
  toast(msg, "error");
}

/* Gate verdict -> badge class + readable text. Colors per contract:
 * OK #2e7d32, INFEASIBLE_INPUT_OR_MODEL #f9a825, INFEASIBLE_INCUMBENT #c62828. */
function verdictBadge(status) {
  var cls = "badge";
  var text = fmt(status);
  if (status === "OK") { cls += " badge-ok"; }
  else if (status === "INFEASIBLE_INPUT_OR_MODEL") { cls += " badge-warn"; }
  else if (status === "INFEASIBLE_INCUMBENT") { cls += " badge-bad"; }
  else if (status) { cls += " badge-neutral"; }
  return el("span", { class: cls, text: text });
}

var JOB_STATUSES = ["running", "succeeded", "failed", "stopped"];

function jobStatusChip(job) {
  var status = job && job.status;
  var cls = "chip";
  if (status === "running") { cls += " chip-running"; }
  else if (status === "succeeded") { cls += " chip-ok"; }
  else if (status === "failed") { cls += " chip-bad"; }
  else if (status === "stopped") { cls += " chip-neutral"; }
  var label = JOB_STATUSES.indexOf(status) >= 0
    ? t("app.jobStatus." + status) : fmt(status);
  if (job && job.gate_nonzero) { label += t("app.gateNonzero"); }
  return el("span", { class: cls, text: label });
}

var JOB_TYPES = ["stage1", "complete", "stage2", "validate", "multirole"];

function jobTypeLabel(type) {
  return JOB_TYPES.indexOf(type) >= 0 ? t("app.jobType." + type) : fmt(type);
}

function roleLabel(role) {
  return (role === "admin" || role === "user")
    ? t("html.role." + role) : fmt(role);
}

/* ------------------------------------------------------------------ state */

var state = {
  me: null,                 // last GET /api/me: {email, role, mode, tour_seen}
  instances: [],            // last GET /api/instances
  jobs: [],                 // last GET /api/jobs (newest first)
  activeJobId: null,        // job shown in the live-log panel
  activeJobTimer: null,     // setInterval handle for the 2 s poll
  lastActiveJob: null,      // last rendered job object (lang re-render)
  jobsListTimer: null,      // slow refresh of the history while tab is open
  resultsInstance: null,
  pairingViz: null,         // {data, sel, base, nights, dh} — Гант+карта этапа 1
  labActivitySeq: 0,        // last scan-activity seq the UI has rendered
  labActivityTimer: null,   // setInterval handle for the live R&D feed
  labTestingId: null,       // id of the improvement whose Test phase is live
  labItems: [],             // cached /evolution list for client-side sorting
  labSort: "published",     // "published" | "impact" | "found" (Алгоритмы)
  labSortDir: "desc",       // "desc" | "asc" — повторный щелчок разворачивает
  labScoreTimer: null,      // setInterval handle while impact scoring runs
  advisorParams: {},        // instance_id -> confirmed solver knobs to apply
  advisorRec: null,         // last /advise response (lang re-render)
  kbSkill: null,            // last GET /solver-advisor/skill
  kbBackends: null,         // last GET /solver-advisor/backends
  kbBuildTimer: null,       // setInterval handle while a skill build runs
  scenarios: [],            // last GET /api/scenarios (lang re-render)
  scenCompare: null,        // last compare response shown (lang re-render)
  scenGlobalRules: null,    // current global tier-1 params (form defaults)
  vacInstance: null,        // instance id whose bidding round is shown
  vacMeta: null,            // last GET /api/vacation/{id} (lang re-render)
  qualInstance: null,       // instance id whose qualifications are shown
  qualComp: null,           // last compliance response (lang re-render)
  boardInstance: null,      // instance id whose roster board is shown
  board: null,              // last GET /api/board response (lang re-render)
  boardSelected: null,      // pid of the pairing open in the editor
  basesInstance: null,      // instance id whose base balance is shown
  bases: null,              // last GET /api/base-balance/{id} (lang re-render)
  cabLinks: [],             // my crew links (GET /api/cabinet)
  cabInstance: null,        // instance id whose cabinet is shown
  cabPreviewEmp: null,      // admin/owner preview: ?employee= override
  cabinet: null,            // last GET /api/cabinet/{id} (lang re-render)
  cabWindows: [],           // vacation windows being edited (client-side)
  cabLinksInstance: null,   // instance id in the link-management card
  askBusy: false,           // one Ask AI request at a time
  askResult: null,          // last /api/ask response (lang re-render of meta)
  suggestions: null,        // last GET /api/suggestions (lang re-render)
  crs: null,                // last GET /api/cr (lang re-render)
  loops: null,              // last GET /api/loops (lang re-render)
  loopOpen: null,           // key of the loop row expanded into details
  loopDetail: null,         // last detail response of that loop
  loopInstance: null        // instance whose data-health history is shown
};

function isAdmin() { return !!(state.me && state.me.role === "admin"); }
// The single super admin (server decides via /api/me → is_super; the
// deployment's CREW_SUPER_ADMIN address in access mode, or the token-mode
// operator). Gates the «Установки»/Settings section. Cosmetic only — the
// server re-checks.
function isSuperAdmin() { return !!(state.me && state.me.is_super); }

/* ------------------------------------------------------- identity (/api/me) */

function renderIdentity() {
  var emailNode = $("me-email");
  var roleNode = $("me-role");
  if (state.me && state.me.email) {
    emailNode.textContent = state.me.email;
    roleNode.textContent = roleLabel(state.me.role);
    roleNode.className = "chip " +
      (state.me.role === "admin" ? "chip-role-admin" : "chip-neutral");
  } else {
    // Legacy token mode (or /api/me not deployed yet): no per-user identity.
    emailNode.textContent = "";
    roleNode.classList.add("hidden");
  }
  // Версия продукта внизу всех экранов (РВ-141): номер — из настройки
  // площадки через /api/me; нет номера — нет подписи.
  var verNode = $("product-version");
  if (verNode) {
    var ver = state.me && state.me.product_version;
    verNode.textContent = ver ? tf("app.productVersion", { v: ver }) : "";
    verNode.classList.toggle("hidden", !ver);
  }
  renderNav();   // re-gate the admin section by role
  // The owner column in the instances table depends on the role.
  renderInstancesTable();
}

function loadMe() {
  return api("/me").then(function (me) {
    state.me = (me && typeof me === "object") ? me : null;
    renderIdentity();
    document.dispatchEvent(new CustomEvent("crew:me"));
  }).catch(function (err) {
    // 404 = backend without /api/me (legacy token mode): degrade quietly,
    // the app keeps working with the shared-token session. Anything else
    // is a real problem and stays loud.
    state.me = null;
    renderIdentity();
    document.dispatchEvent(new CustomEvent("crew:me"));
    if (err.status !== 404 && err.status !== 401) {
      apiErrorToast(err, t("app.t.meFail"));
    }
  });
}

/* -------------------------------------------------- tabs (two-tier nav) */

// i18n label per leaf tab (= its panel-<id>).
var TAB_LABEL = {
  flow: "html.tab.flow",
  cruns: "html.tab.cruns",
  data: "html.tab.data", format: "html.tab.format",
  jobs: "html.tab.jobs", runs: "html.tab.runs",
  results: "html.tab.results", scenarios: "html.tab.scenarios",
  board: "html.tab.board", vacation: "html.tab.vacation",
  quals: "html.tab.quals", bases: "html.tab.bases",
  cabinet: "html.tab.cabinet", demo: "html.tab.demo", ops: "html.tab.ops",
  ask: "html.tab.ask", suggest: "html.tab.suggest",
  users: "html.tab.users", lab: "html.tab.lab", kb: "html.tab.kb",
  crs: "html.tab.crs", loops: "html.tab.loops", learn: "html.tab.learn",
  activity: "html.tab.activity", llmreg: "html.tab.llmreg",
  templates: "html.tab.templates",
  rules: "html.tab.rules", studio: "html.tab.studio",
  settings: "html.tab.settings"
};

// Groups collapse the 13 leaf tabs into a top-level section bar; each
// section reveals its own sub-tab row. Order = display order. New features
// slot into an existing group's `tabs` list.
var TAB_GROUPS = [
  // Главный экран (поручение владельца 2026-08-28): блок-схема
  // конвейера — основной, остальные экраны — вспомогательные.
  { id: "flow", i18n: "html.tab.flow", tabs: ["flow"] },
  // Прогоны COSMOS (слово владельца 07.09.2026) — сразу после конвейера,
  // как список прогонов в панели CDPM (порядок панели = порядок тура).
  { id: "cruns", i18n: "html.tab.cruns", tabs: ["cruns"] },
  { id: "data", i18n: "html.group.data", tabs: ["data", "format", "rules"] },
  { id: "opt", i18n: "html.group.opt",
    tabs: ["jobs", "runs", "results", "scenarios", "ops"] },
  { id: "crew", i18n: "html.group.crew",
    tabs: ["board", "vacation", "quals", "bases"] },
  // crew self-service: single page, visible to every authenticated user
  // (the crew LINK, not the role, decides what it shows)
  { id: "me", i18n: "html.tab.cabinet", tabs: ["cabinet"] },
  // Auto-improvement subsystem (admin): the research agent + the solver
  // knowledge base today, with room for the regulatory / feedback / UX-idea
  // agents and the Test->Stage->Prod promotion pipeline to come. Placed
  // between Кабинет (me) and Демо (demo) on 2026-07-16 to foreground the
  // self-improvement thesis; admin-gated, so non-admins see no gap here.
  { id: "evo", i18n: "html.group.evo",
    tabs: ["loops", "crs", "lab", "kb"], admin: true },
  { id: "demo", i18n: "html.tab.demo", tabs: ["demo"] },
  { id: "help", i18n: "html.group.help", tabs: ["ask", "suggest", "learn"] },
  { id: "admin", i18n: "html.group.admin",
    tabs: ["users", "activity", "llmreg", "templates", "studio"],
    admin: true },
  // Global application settings — visible ONLY to the single super admin
  // (the deployment's CREW_SUPER_ADMIN address). `super: true` gates it
  // beyond the admin flag; the server re-checks every /api/settings* call.
  { id: "su", i18n: "html.group.su", tabs: ["settings"],
    admin: true, super: true }
];

var _nav = { group: "flow", tab: "flow", lastTab: {} };

function _groupOf(name) {
  for (var i = 0; i < TAB_GROUPS.length; i++) {
    if (TAB_GROUPS[i].tabs.indexOf(name) >= 0) { return TAB_GROUPS[i]; }
  }
  return TAB_GROUPS[0];
}

/** Rebuild the section bar + sub-tab row (labels via t(); admin section
 *  hidden for non-admins). Called on load, role change and language switch. */
function renderNav() {
  var groups = $("groups");
  if (!groups) { return; }
  groups.textContent = "";
  TAB_GROUPS.forEach(function (g) {
    if (g.admin && !isAdmin()) { return; }
    if (g.super && !isSuperAdmin()) { return; }
    var btn = el("button", {
      class: "tab group" + (g.id === _nav.group ? " active" : ""),
      text: t(g.i18n)
    });
    btn.dataset.group = g.id;
    // A single-page section has no sub-tab row, so mirror its sole leaf's
    // data-tab here — the onboarding tour highlights by data-tab (audit
    // 2026-07-15: the last tour step 'demo' had no chip to highlight).
    if (g.tabs.length === 1) { btn.dataset.tab = g.tabs[0]; }
    btn.addEventListener("click", function () { showGroup(g.id); });
    groups.appendChild(btn);
  });
  renderSubtabs();
}

function renderSubtabs() {
  var bar = $("subtabs");
  if (!bar) { return; }
  bar.textContent = "";
  var g = _groupOf(_nav.tab);
  // a single-page section is its own destination — no sub-row
  if (!g || g.tabs.length <= 1) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  g.tabs.forEach(function (name) {
    var btn = el("button", {
      class: "tab subtab" + (name === _nav.tab ? " active" : ""),
      text: t(TAB_LABEL[name] || name)
    });
    btn.dataset.tab = name;
    btn.addEventListener("click", function () { showTab(name); });
    bar.appendChild(btn);
  });
}

/** Switch to a section, landing on its remembered (or first) page.
 *  Re-clicking the ACTIVE section is the "up" action (user feedback
 *  2026-07-16): it returns to the section's root — its first page — and
 *  showTab() below resets any nested view (an open demo storyboard). */
function showGroup(groupId) {
  var g = TAB_GROUPS.find(function (x) { return x.id === groupId; });
  if (!g || (g.admin && !isAdmin()) || (g.super && !isSuperAdmin())) { return; }
  if (_nav.group === groupId) { showTab(g.tabs[0]); return; }
  showTab(_nav.lastTab[groupId] || g.tabs[0]);
}

function showTab(name) {
  var g = _groupOf(name);
  if ((g.admin && !isAdmin()) || (g.super && !isSuperAdmin())) {
    name = "data"; g = _groupOf(name);
  }
  _nav.group = g.id;
  _nav.tab = name;
  _nav.lastTab[g.id] = name;
  document.querySelectorAll("#groups .group").forEach(function (b) {
    b.classList.toggle("active", b.dataset.group === g.id);
  });
  renderSubtabs();
  document.querySelectorAll(".tab-panel").forEach(function (p) {
    p.classList.toggle("hidden", p.id !== "panel-" + name);
  });
  // The run log + history live on the «Прогоны» tab now; «Расчёты» is just
  // the launcher. Poll the jobs list only while «Прогоны» is open.
  if (name === "runs") {
    refreshJobs();
    startJobsListPolling();
    mountRunsDashboard();
  } else {
    stopJobsListPolling();
  }
  // Прогоны COSMOS: список читается при показе и опрашивается, пока у
  // какого-то прогона идёт расчёт; вне экрана опрос останавливается.
  if (name === "cruns") {
    refreshCosmosRuns();
    startCosmosRunsPolling();
    // Подраздел, на котором человек остановился, помнит браузер: общий
    // экран обеих систем открывается тем же, чем закрыт.
    crunsShowView(_crunsViewLoad());
  } else {
    stopCosmosRunsPolling();
  }
  if (name === "results") { refreshResults(); }
  if (name === "ops") { refreshOps(); }
  if (name === "scenarios") { refreshScenarios(); }
  if (name === "flow") { refreshFlow(); }
  if (name === "vacation") { refreshVacation(); }
  if (name === "quals") { refreshQuals(); }
  if (name === "board") { refreshBoard(); }
  if (name === "bases") { refreshBases(); }
  if (name === "cabinet") { refreshCabinet(); }
  if (name === "ask") { refreshAsk(); }
  if (name === "suggest") { refreshSuggest(); }
  if (name === "loops") { refreshLoops(); }
  if (name === "crs") { refreshCrs(); }
  if (name === "learn") { renderLearn(); }
  if (name === "users") { refreshUsers(); }
  if (name === "activity") { refreshActivity(); }
  if (name === "llmreg") { refreshLlmReg(); }
  if (name === "templates") { refreshTemplateBase(); }
  if (name === "rules") { refreshRulePacks(); }
  if (name === "studio") { refreshStudio(); }
  if (name === "settings") { refreshSettings(); }
  if (name === "lab") { refreshLab(); resumeLabActivity(); }
  if (name === "kb") { refreshKb(); }
  _navBeacon(name);
  // Липкие горизонтальные полосы — и для контейнеров, созданных ПОСЛЕ
  // старта (детали контуров, витрины, отчёты): исходный вызов в init()
  // видел только разметку первого рендера. Повторный вызов дёшев и
  // безопасен — уже обвешанные контейнеры отфильтровываются по датасету.
  initStickyHScroll();
  if (name === "demo" && typeof demoState !== "undefined") {
    // Entering «Демо» always lands on the gallery: reaching the tab again
    // IS the "up from a storyboard" action. Tour/lessons that deep-link a
    // specific demo call openDemoView() right after showTab(), so the
    // reset never fights them (demo.js re-renders on the crew:tab event).
    demoState.view = "launcher";
  }
  document.dispatchEvent(new CustomEvent("crew:tab", { detail: { tab: name } }));
}

/* ---------------------------------------------------------- tab 1: Данные */

/* Подпись набора: сначала название, введённое человеком, затем
   идентификатор. Идентификатор нужен системе — он остаётся видимым,
   но не вместо имени. */
/* Название набора по его идентификатору: в журналах и таблицах
   идентификатор приходит голой строкой, объекта набора там нет. */
function instTitleById(id) {
  var list = (state && state.instances) || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === id && list[i].title) {
      return list[i].title + " — " + id;
    }
  }
  return fmt(id);
}

function instLabel(inst) {
  var src = " (" + sourceLabel(inst.source) + ")";
  if (inst && inst.title) { return inst.title + " — " + inst.id + src; }
  return inst.id + src;
}

function sourceLabel(source) {
  if (source === "gerad") { return "GERAD"; }
  if (source === "bts") { return "BTS"; }
  if (source === "upload") { return t("app.source.upload"); }
  return fmt(source);
}

/** Clickable L/C chip for a dataset's llm_scope. Owner/admin toggles it;
 *  loosening to C (cloud allowed) is confirmed, tightening is immediate. */
function scopeChipCell(inst) {
  var scope = inst.llm_scope || "local-only";
  var chip = el("span", {
    class: "chip chip-click small " +
           (scope === "any" ? "chip-neutral" : "chip-warn"),
    title: t("app.advisor.scope.title"),
    text: scope === "any" ? t("app.scope.c") : t("app.scope.l")
  });
  var toggleLC = function () {
    var next = (inst.llm_scope === "any") ? "local-only" : "any";
    var go = function () {
      api("/instances/" + encodeURIComponent(inst.id) + "/llm-scope", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: next })
      }).then(function () {
        inst.llm_scope = next;
        renderInstancesTable();
        toast(t("app.advisor.scopeSet"), "ok");
      }).catch(function (err) {
        apiErrorToast(err, t("app.advisor.scopeErr2"));
      });
    };
    if (next === "any") {
      appConfirm(t("app.advisor.scopeConfirm")).then(function (ok) {
        if (ok) { go(); }
      });
    } else { go(); }
  };
  chip.addEventListener("click", toggleLC);
  keyActivate(chip, toggleLC);
  // country chip (regulatory agent jurisdiction), owner/admin-editable
  var country = inst.country || "INTL";
  var cchip = el("span", { class: "chip chip-click chip-neutral small",
                           title: t("app.country.title"), text: country });
  var editCountry = function () {
    appPrompt(t("app.country.prompt"), { value: country }).then(function (v) {
      if (v === null) { return; }
      api("/instance-country/" + encodeURIComponent(inst.id), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ country: (v || "").trim().toUpperCase() })
      }).then(function (r) {
        inst.country = r.country;
        renderInstancesTable();
        toast(t("app.country.set"), "ok");
      }).catch(function (err) { apiErrorToast(err, t("app.country.err")); });
    });
  };
  cchip.addEventListener("click", editCountry);
  keyActivate(cchip, editCountry);
  return el("td", null, [chip, document.createTextNode(" "), cchip]);
}

/* --- Правила КОНКРЕТНОГО набора (замечание аудита P1-10) ------------------
 * Правила принадлежат набору, а не установке: правка администратора не
 * меняет нормы чужого набора задним числом. Чип в таблице честно называет
 * происхождение, чтобы «правила установки» и «правила набора» нельзя было
 * перепутать. */
var INST_RULES_ORIGIN = {
  defaults: ["chip-neutral", "app.instRules.origin.defaults"],
  installation: ["chip-neutral", "app.instRules.origin.installation"],
  instance: ["chip-ok", "app.instRules.origin.instance"],
  scenario: ["chip-warn", "app.instRules.origin.scenario"]
};

function instRulesChipCell(inst) {
  var cell = el("td");
  var btn = el("button", { class: "btn btn-ghost btn-xs",
                           text: t("app.instRules.open") });
  btn.addEventListener("click", function () { openInstRules(inst.id); });
  cell.appendChild(btn);
  // Происхождение приходит В САМОМ перечне: чип обязан стоять у КАЖДОЙ
  // строки, а не только у той, которую уже открывали. Число отличий от
  // шаблона требует разбора значений — оно появляется, когда набор открыт.
  var cached = state.instRules && state.instRules[inst.id];
  var origin = (cached && cached.origin) || inst.rules_origin;
  if (!origin) { return cell; }
  var spec = INST_RULES_ORIGIN[origin] || INST_RULES_ORIGIN.defaults;
  var text = t(spec[1]);
  if (cached && cached.origin === "instance" && cached.overridden.length) {
    text += " (" + cached.overridden.length + ")";
  }
  cell.appendChild(el("span", { class: "chip " + spec[0], text: text }));
  return cell;
}

function renderInstRulesPanel(data) {
  var panel = $("inst-rules-panel");
  panel.classList.remove("hidden");
  $("inst-rules-title").textContent =
    t("app.instRules.title") + ": " + (data.label || data.instance);
  var spec = INST_RULES_ORIGIN[data.origin] || INST_RULES_ORIGIN.defaults;
  var origin = t(spec[1]);
  if (data.origin === "instance" && data.overridden.length) {
    origin += " — " + t("app.instRules.overridden") + ": "
      + data.overridden.join(", ");
  }
  $("inst-rules-origin").textContent = origin;
  var grid = $("inst-rules-grid");
  grid.textContent = "";
  CONFIG_PARAMS.forEach(function (p) {
    var key = p[0];
    var value = (data.values && data.values[key] !== undefined
                 && data.values[key] !== null) ? data.values[key] : p[2];
    var input = el("input", { type: "number", id: "instcfg-" + key,
                              value: String(value), step: "1", min: "0" });
    var hint = [el("span", { text: key + " " })];
    // Правило, которое решатель НЕ ищет: включив его, оператор получит
    // повторяющийся отказ вердикта без автоматического пути к легальному
    // ростеру — без пометки это читается как поломка решателя.
    if (data.enforcement && data.enforcement[key] === "certificate") {
      hint.push(el("span", { class: "chip chip-warn",
                             title: t("app.cfg.certOnly.hint"),
                             text: t("app.cfg.certOnly") }));
    }
    grid.appendChild(el("div", { class: "form-row" }, [
      el("label", { for: "instcfg-" + key, text: t(p[1]) }),
      input,
      el("span", { class: "hint mono cfg-hint" }, hint)
    ]));
  });
}

function openInstRules(instId) {
  state.instRulesOpen = instId;
  api("/instances/" + encodeURIComponent(instId) + "/rules")
    .then(function (data) {
      state.instRules = state.instRules || {};
      state.instRules[instId] = data;
      renderInstRulesPanel(data);
      renderInstancesTable();
    })
    .catch(function (err) { apiErrorToast(err, t("app.instRules.loadFail")); });
}

function saveInstRules(evt) {
  evt.preventDefault();
  var instId = state.instRulesOpen;
  if (!instId) { return; }
  var parameters = {};
  var bad = [];
  CONFIG_PARAMS.forEach(function (p) {
    var input = $("instcfg-" + p[0]);
    var v = input ? parseInt(input.value, 10) : NaN;
    // comp_* допускают 0 (= правило выключено).
    var minOk = p[0].indexOf("comp_") === 0 ? (v >= 0) : (v > 0);
    if (!isFinite(v) || !minOk) { bad.push(t(p[1])); }
    else { parameters[p[0]] = v; }
  });
  if (bad.length) {
    toast(t("app.t.badValues") + bad.join(", "), "error");
    return;
  }
  api("/instances/" + encodeURIComponent(instId) + "/rules", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parameters: parameters })
  })
    .then(function (data) {
      state.instRules[instId] = data;
      renderInstRulesPanel(data);
      renderInstancesTable();
      toast(t("app.instRules.saved"), "ok");
    })
    .catch(function (err) { apiErrorToast(err, t("app.instRules.saveFail")); });
}

function resetInstRules() {
  var instId = state.instRulesOpen;
  if (!instId) { return; }
  appConfirm(t("app.instRules.resetConfirm")).then(function (ok) {
    if (!ok) { return; }
    api("/instances/" + encodeURIComponent(instId) + "/rules",
        { method: "DELETE" })
      .then(function (data) {
        state.instRules[instId] = data;
        renderInstRulesPanel(data);
        renderInstancesTable();
        toast(t("app.instRules.reset.done"), "ok");
      })
      .catch(function (err) {
        apiErrorToast(err, t("app.instRules.saveFail"));
      });
  });
}

function renderInstancesTable() {
  var admin = isAdmin();
  var colCount = admin ? 10 : 9;
  $("instances-owner-th").classList.toggle("hidden", !admin);
  var tbody = $("instances-tbody");
  tbody.textContent = "";
  if (!state.instances.length) {
    tbody.appendChild(el("tr", null, [
      el("td", { colspan: String(colCount), class: "empty-row",
                 text: t("app.instances.none") })
    ]));
    return;
  }
  state.instances.forEach(function (inst) {
    var cells = [
      el("td", { class: "mono", text: instLabel(inst) }),
      el("td", { text: sourceLabel(inst.source) }),
      scopeChipCell(inst)
    ];
    if (admin) {
      // The API includes the owner only for admin callers.
      cells.push(el("td", { class: "mono small", text: fmt(inst.owner) }));
    }
    cells = cells.concat([
      el("td", { class: "num", text: fmt(inst.legs) }),
      el("td", { class: "num", text: fmt(inst.employees) }),
      el("td", { class: "num", text: fmt(inst.slots) }),
      el("td", { class: "num", text: fmt(inst.catalog_objective) }),
      el("td", null, [verdictBadge(inst.last_gate_status)]),
      instRulesChipCell(inst)
    ]);
    tbody.appendChild(el("tr", null, cells));
  });
}

function refreshInstances() {
  return api("/instances").then(function (list) {
    state.instances = Array.isArray(list) ? list : [];
    renderInstancesTable();
    renderInstancePickers();
  }).catch(function (err) {
    apiErrorToast(err, t("app.t.instancesList"));
    $("instances-tbody").textContent = "";
    $("instances-tbody").appendChild(el("tr", null, [
      el("td", { colspan: String(isAdmin() ? 10 : 9), class: "empty-row",
                 text: t("app.instances.loadError") })
    ]));
  });
}

function renderUploadResult(node) {
  var box = $("upload-result");
  box.textContent = "";
  box.classList.remove("hidden", "ok", "bad");
  box.appendChild(node);
  return box;
}

/* Документ постановки контура. Показывается не факт загрузки, а
   ПРОЧИТАННЫЕ ПРАВИЛА: только по ним видно, что расчёт пойдёт по тому
   документу, который передан, а не по умолчаниям. */
function renderRules(st) {
  var box = $("rules-state");
  if (!box) { return; }
  box.classList.remove("hidden", "bad");
  // Умолчание построения цепочек — ПО ФАКТУ загруженного документа, а не
  // константой в разметке: правила документа несут равнозначные станции,
  // которые колонная формулировка не выражает и на которых отказывает
  // всегда. Там, где документа нет (эталонные наборы порталов), линия
  // расчёта остаётся прежней — менять её у всех было бы подменой
  // постановки.
  var lvl = $("stage1-pairing-level");
  if (lvl && !lvl.dataset.userChosen) {
    lvl.value = (st && st.loaded && !st.error) ? "3" : "";
    lvl.dispatchEvent(new Event("change", { bubbles: true }));
  }
  if (!st || !st.loaded) {
    box.textContent = t("app.rules.none");
    return;
  }
  if (st.error) {
    box.classList.add("bad");
    box.textContent = t("app.rules.rejected") + st.error;
    return;
  }
  var rows = Object.keys(st.rules || {}).map(function (k) {
    return el("tr", null, [el("td", { text: k }),
                           el("td", { text: fmt(st.rules[k]) })]);
  });
  box.innerHTML = "";
  box.appendChild(el("p", { class: "result-title",
    text: tf("app.rules.accepted", { sha: st.sha,
                                    kb: Math.round(st.bytes / 1024) }) }));
  box.appendChild(el("table", null, [el("tbody", null, rows)]));
}

function refreshRules() {
  return api("/rules").then(renderRules).catch(function () {});
}

function handleRulesSubmit(evt) {
  evt.preventDefault();
  var f = $("rules-file");
  if (!f.files || !f.files.length) {
    toast(t("app.rules.pickFile"), "error");
    return;
  }
  var btn = $("rules-submit");
  btn.disabled = true;
  f.files[0].text().then(function (text) {
    return api("/rules/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text })
    }).then(function (st) {
      renderRules(st);
      toast("документ постановки принят", "ok");
    });
  }).catch(function (e) {
    var box = $("rules-state");
    box.classList.remove("hidden");
    box.classList.add("bad");
    box.textContent = "Документ отклонён: " + (e && e.message ? e.message : e);
  }).finally(function () { btn.disabled = false; });
}

/* Приёмка каталога. Показывается КАЖДАЯ проверка с числом
   рассмотренных объектов: ноль нарушений при нуле рассмотренного — это
   проверка вхолостую, и её надо видеть, а не считать успехом. */
function handleAuditRun() {
  var btn = $("audit-run");
  var box = $("audit-state");
  var inline = $("audit-inline");
  btn.disabled = true;
  if (inline) { inline.textContent = t("app.audit.running"); }
  box.textContent = t("app.audit.running");
  api("/audit/pairing", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  }).then(function (res) {
    var rows = (res.checks || []).map(function (c) {
      var mark = c.ok ? "OK" : (c.idle ? t("app.audit.idle")
                                       : (c.level === "WARN" ? "—"
                                          : t("app.audit.violated")));
      return el("tr", null, [
        el("td", { text: mark }),
        el("td", { text: c.name }),
        el("td", { class: "num", text: fmt(c.violations) }),
        el("td", { class: "num", text: fmt(c.seen) })
      ]);
    });
    box.innerHTML = "";
    box.appendChild(el("p", {
      class: "result-title",
      text: (res.passed ? t("app.audit.passed") : t("app.audit.failed")) +
            tf("app.audit.counts", { chains: fmt(res.chains),
                                     checks: fmt((res.checks || []).length),
                                     objects: fmt(res.objects) })
    }));
    box.appendChild(el("p", { class: "muted",
      text: tf("app.audit.sources", { catalog: fmt(res.catalog),
                                      payload: fmt(res.payload) }) }));
    box.appendChild(el("table", null, [
      el("thead", null, [el("tr", null, [
        el("th", { text: "" }), el("th", { text: t("app.audit.thCheck") }),
        el("th", { text: t("app.audit.thViolations") }),
        el("th", { text: t("app.audit.thSeen") })])]),
      el("tbody", null, rows)]));
    /* Результаты лежат ниже ряда карточек — без подводки экрана
       нажатие выглядит безрезультатным, а таблицу надо ещё найти. */
    if (inline) {
      var bad = (res.checks || []).filter(function (c) {
        return !c.ok && c.level !== "WARN";
      }).length;
      inline.textContent = res.passed
        ? t("app.audit.passInline")
        : tf("app.audit.failInline", { bad: fmt(bad) });
    }
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }).catch(function (e) {
    var msg = t("app.audit.error") + (e && e.message ? e.message : e);
    box.textContent = msg;
    if (inline) { inline.textContent = msg; }
  }).finally(function () { btn.disabled = false; });
}

/* Отправка с ЖИВЫМ прогрессом (проверка инструкции 2026-08-12: выгрузка
   в десятки мегабайт уходила без единого признака жизни). XHR вместо
   fetch — только у него есть события хода отправки; контракт ответа
   (разбор JSON, 401) повторяет api(). */
function apiUploadProgress(path, bodyString, onPhase) {
  return new Promise(function (resolve, reject) {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", API + path);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.upload.onprogress = function (e) {
      if (e.lengthComputable) { onPhase("send", e.loaded, e.total); }
    };
    xhr.upload.onload = function () { onPhase("server"); };
    xhr.onload = function () {
      var body = null;
      try { body = JSON.parse(xhr.responseText); }
      catch (e) { body = xhr.responseText; }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body); return; }
      var err = new Error("HTTP " + xhr.status);
      err.status = xhr.status;
      err.body = body;
      reject(err);
    };
    xhr.onerror = function () { reject(new Error(t("app.t.netFail"))); };
    xhr.send(bodyString);
  });
}

function uploadPhase(phase, loaded, total) {
  var box = $("upload-progress");
  if (!box) { return; }
  box.classList.remove("hidden");
  if (phase === "read") { box.textContent = t("app.up.reading"); }
  else if (phase === "parse") { box.textContent = t("app.up.parsing"); }
  else if (phase === "send") {
    var mb = function (b) { return (b / 1048576).toFixed(1); };
    var pct = total ? Math.round(loaded / total * 100) : 0;
    box.textContent = t("app.up.sending") + " " + mb(loaded) + " / " +
                      mb(total) + " " + t("app.up.mb") + " (" + pct + " %)";
  } else if (phase === "server") { box.textContent = t("app.up.server"); }
  else { box.textContent = ""; box.classList.add("hidden"); }
}

function handleUploadSubmit(evt) {
  evt.preventDefault();
  var name = $("upload-name").value.trim();
  if (!name) { toast(t("app.t.needName"), "error"); return; }

  var fileInput = $("upload-file");
  var readText;
  if (fileInput.files && fileInput.files.length > 0) {
    uploadPhase("read");
    readText = fileInput.files[0].text();
  } else {
    var pasted = $("upload-text").value.trim();
    if (!pasted) {
      toast(t("app.t.needJson"), "error");
      return;
    }
    readText = Promise.resolve(pasted);
  }

  var submitBtn = $("upload-submit");
  submitBtn.disabled = true;

  readText.then(function (text) {
    uploadPhase("parse");
    var payload;
    try {
      payload = JSON.parse(text);
    } catch (e) {
      var errBox = renderUploadResult(el("div", null, [
        el("p", { class: "result-title", text: t("app.upload.jsonParseFail") }),
        el("ul", null, [el("li", { text: e.message })])
      ]));
      errBox.classList.add("bad");
      return null;
    }
    var fmtSel = $("upload-format");
    return apiUploadProgress("/instances/upload", JSON.stringify({
      name: name, payload: payload,
      format: fmtSel ? fmtSel.value : "canonical"
    }), uploadPhase).then(function (resp) {
      /* Отчёт преобразования показывается ПОЛНОСТЬЮ, включая то, что
         отклонено и почему: молчаливое отбрасывание участков — это
         неполное расписание, выданное за полное. Оба блока — отчёт и
         сводка сохранения — собираются в ОДИН вызов renderUploadResult:
         он очищает контейнер, и два вызова подряд молча затирали отчёт
         сводкой (дефект того же класса «заявлено, но не действует» —
         пойман браузерной проверкой 2026-08-12). */
      var blocks = [];
      if (resp && resp.ingest) {
        var ing = resp.ingest;
        var lines = [
          el("li", { text: "горизонт: " + fmt(ing.month) }),
          el("li", { text: "участков в выгрузке: " + fmt(ing.legs_total) }),
          el("li", { text: t("app.up.flights") + fmt(ing.flights) }),
          el("li", { text: "мест: " + fmt(ing.seats) }),
          el("li", { text: "сотрудников: " + fmt(ing.employees) +
                           " (к назначению " + fmt(ing.employees_allocable) + ")" })
        ];
        Object.keys(ing.rejected || {}).forEach(function (k) {
          lines.push(el("li", { text: "отклонено — " + k + ": " +
                                      fmt(ing.rejected[k]) }));
        });
        /* Перенесённые персональные ограничения (2026-08-12): условие,
           попавшее не в то поле, не действует молча — поэтому отчёт
           показывает, что именно легло в исполняемые поля решателя. */
        function dictLine(title, d) {
          if (!d) { return; }
          var parts = Object.keys(d).filter(function (k) {
            return !Array.isArray(d[k]);
          }).map(function (k) { return k + ": " + fmt(d[k]); });
          lines.push(el("li", { text: title + " — " + parts.join(", ") }));
        }
        dictLine("запреты станций", ing.stations_denied);
        dictLine("несовместимость (не летать вместе)", ing.do_not_fly_with);
        dictLine("окна допусков", ing.qualification_windows);
        var orphan = (ing.qualification_windows || {})[
          "требования без носителей"] || [];
        if (orphan.length) {
          lines.push(el("li", { text:
            t("app.up.orphanReq") + orphan.join(", ") }));
        }
        blocks.push(el("p", { class: "result-title",
                              text: t("app.up.convTitle") }));
        blocks.push(el("ul", null, lines));
      }
      var stats = (resp && resp.validation && resp.validation.stats) || {};
      var statLines = Object.keys(stats).map(function (k) {
        return el("li", { text: k + ": " + fmt(stats[k]) });
      });
      blocks.push(el("p", { class: "result-title",
                            text: t("app.upload.saved") + fmt(resp && resp.id) }));
      blocks.push(el("ul", null, statLines));
      var box = renderUploadResult(el("div", null, blocks));
      box.classList.add("ok");
      $("upload-form").reset();
      refreshInstances();
    }).catch(function (err) {
      var errors = [];
      if (err.status === 422 && err.body && err.body.validation &&
          Array.isArray(err.body.validation.errors)) {
        errors = err.body.validation.errors;
      } else if (err.body && err.body.detail) {
        errors = [typeof err.body.detail === "string"
                  ? err.body.detail : JSON.stringify(err.body.detail)];
      } else {
        errors = [err.message];
      }
      var box = renderUploadResult(el("div", null, [
        el("p", { class: "result-title", text: t("app.upload.failed") }),
        el("ul", null, errors.map(function (m) { return el("li", { text: String(m) }); }))
      ]));
      box.classList.add("bad");
    });
  }).catch(function (err) {
    toast(t("app.t.fileReadFail") + err.message, "error");
  }).finally(function () {
    submitBtn.disabled = false;
    uploadPhase("done");
  });
}

/* ------------------------------------------------------------ SSIM import */

function addBaseRow(airport, count) {
  var holder = $("ssim-bases");
  var airportInput = el("input", {
    type: "text", class: "base-airport mono", maxlength: "3",
    placeholder: "ATL", value: airport || "", autocomplete: "off"
  });
  var countInput = el("input", {
    type: "number", class: "base-count", min: "1", step: "1",
    placeholder: t("app.ph.baseCount"),
    value: (count !== undefined && count !== null) ? String(count) : ""
  });
  var removeBtn = el("button", {
    type: "button", class: "btn btn-ghost btn-small",
    text: t("app.btn.removeBase")
  });
  var row = el("div", { class: "bases-row" }, [airportInput, countInput, removeBtn]);
  removeBtn.addEventListener("click", function () { row.remove(); });
  holder.appendChild(row);
}

function collectBases() {
  var rows = document.querySelectorAll("#ssim-bases .bases-row");
  var bases = [];
  var errors = [];
  rows.forEach(function (row) {
    var airport = row.querySelector(".base-airport").value.trim().toUpperCase();
    var countRaw = row.querySelector(".base-count").value;
    if (!airport && !countRaw) { return; } // fully empty row is ignored
    var count = parseInt(countRaw, 10);
    if (airport.length !== 3) {
      errors.push(tf("app.ssim.baseCode3", { v: airport }));
    } else if (!isFinite(count) || count < 1) {
      errors.push(tf("app.ssim.baseCount", { a: airport }));
    } else {
      bases.push({ airport: airport, count: count });
    }
  });
  if (!bases.length && !errors.length) {
    errors.push(t("app.ssim.needBase"));
  }
  return { bases: bases, errors: errors };
}

function renderSsimResult(node) {
  var box = $("ssim-result");
  box.textContent = "";
  box.classList.remove("hidden", "ok", "bad");
  box.appendChild(node);
  return box;
}

/** Human-readable summary of the adapter's conversion-check artifact. */
function conversionCheckLines(check) {
  if (!check || typeof check !== "object") { return []; }
  var lines = [
    t("app.cc.records") + ": " + fmt(check.records_total),
    t("app.cc.parsed") + ": " + fmt(check.records_parsed_type3),
    t("app.cc.skipped") + ": " + JSON.stringify(check.records_skipped_by_type || {}),
    t("app.cc.range") + ": " + fmt(check.expansion_from) + " — " + fmt(check.expansion_to),
    t("app.cc.expanded") + ": " + fmt(check.flights_expanded),
    t("app.cc.airports") + ": " + fmt(check.airports_resolved)
  ];
  var viol = (check.offset_violations || []).length +
             (check.duration_violations || []).length +
             (check.duplicate_flight_ids || []).length +
             (check.malformed_type3 || []).length;
  lines.push(t("app.cc.violations") + ": " + viol);
  lines.push(t("app.cc.verdict") + ": " + (check.ok ? "OK" : t("app.cc.failWord")));
  return lines;
}

function handleSsimSubmit(evt) {
  evt.preventDefault();
  var name = $("ssim-name").value.trim();
  if (!name) { toast(t("app.t.needName"), "error"); return; }

  var basesRes = collectBases();
  if (basesRes.errors.length) {
    var box0 = renderSsimResult(el("div", null, [
      el("p", { class: "result-title", text: t("app.ssim.basesErrors") }),
      el("ul", null, basesRes.errors.map(function (m) {
        return el("li", { text: m });
      }))
    ]));
    box0.classList.add("bad");
    return;
  }

  var fileInput = $("ssim-file");
  var readText;
  if (fileInput.files && fileInput.files.length > 0) {
    readText = fileInput.files[0].text();
  } else {
    var pasted = $("ssim-text").value;
    if (!pasted.trim()) {
      toast(t("app.t.needSsim"), "error");
      return;
    }
    readText = Promise.resolve(pasted);
  }

  var submitBtn = $("ssim-submit");
  submitBtn.disabled = true;

  readText.then(function (text) {
    return api("/import/ssim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        ssim_text: text,
        date_from: $("ssim-date-from").value || null,
        date_to: $("ssim-date-to").value || null,
        bases: basesRes.bases
      })
    }).then(function (resp) {
      var lines = conversionCheckLines(resp && resp.conversion_check);
      var box = renderSsimResult(el("div", null, [
        el("p", { class: "result-title",
                  text: t("app.ssim.ok") + fmt(resp && resp.id) }),
        el("ul", null, lines.map(function (m) { return el("li", { text: m }); }))
      ]));
      box.classList.add("ok");
      $("ssim-form").reset();
      $("ssim-bases").textContent = "";
      addBaseRow();
      refreshInstances();
    }).catch(function (err) {
      var errors = [];
      if (err.body && typeof err.body === "object" && Array.isArray(err.body.errors)) {
        errors = err.body.errors;
      } else if (err.body && err.body.detail) {
        errors = [typeof err.body.detail === "string"
                  ? err.body.detail : JSON.stringify(err.body.detail)];
      } else {
        errors = [err.message];
      }
      var children = [
        el("p", { class: "result-title", text: t("app.ssim.fail") }),
        el("ul", null, errors.map(function (m) {
          return el("li", { class: "mono small", text: String(m) });
        }))
      ];
      var checkLines = conversionCheckLines(
        err.body && typeof err.body === "object" ? err.body.conversion_check : null);
      if (checkLines.length) {
        children.push(el("p", { class: "result-title", text: t("app.ssim.checkTitle") }));
        children.push(el("ul", null, checkLines.map(function (m) {
          return el("li", { text: m });
        })));
      }
      var box = renderSsimResult(el("div", null, children));
      box.classList.add("bad");
    });
  }).catch(function (err) {
    toast(t("app.t.fileReadFail") + err.message, "error");
  }).finally(function () {
    submitBtn.disabled = false;
  });
}

/* ------------------------------------------------------- Meridian import */

/** Human-readable summary of the Meridian conversion-check artifact. */
function meridianCheckLines(check) {
  if (!check || typeof check !== "object") { return []; }
  var lines = [
    t("app.mer.legs") + ": " + fmt(check.legs_total) + " → " + fmt(check.legs_converted),
    t("app.mer.excluded") + ": " + JSON.stringify(check.legs_excluded || {}),
    t("app.cc.range") + ": " + fmt(check.range_from) + " — " + fmt(check.range_to),
    t("app.mer.seatSource") + ": " + JSON.stringify(check.seat_source || {}),
    t("app.mer.employees") + ": " + fmt(check.employees_total),
    t("app.mer.utcGuard") + ": " + fmt(check.utc_guard_checked) + " / " +
      (check.utc_guard_mismatches || []).length,
    t("app.cc.verdict") + ": " + (check.ok ? "OK" : t("app.cc.failWord"))
  ];
  return lines;
}

function renderMeridianResult(node) {
  var box = $("meridian-result");
  box.textContent = "";
  box.classList.remove("hidden", "ok", "bad");
  box.appendChild(node);
  return box;
}

function handleMeridianSubmit(evt) {
  evt.preventDefault();
  var name = $("meridian-name").value.trim();
  if (!name) { toast(t("app.t.needName"), "error"); return; }

  var fileInput = $("meridian-file");
  if (!fileInput.files || fileInput.files.length === 0) {
    toast(t("app.t.needMeridian"), "error");
    return;
  }
  var solverInput = $("meridian-solver-file");
  var readSolver = (solverInput.files && solverInput.files.length > 0)
    ? solverInput.files[0].text()
    : Promise.resolve(null);

  var submitBtn = $("meridian-submit");
  submitBtn.disabled = true;

  Promise.all([fileInput.files[0].text(), readSolver]).then(function (texts) {
    return api("/import/meridian", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        meridian_json: texts[0],
        solver_json: texts[1],
        date_from: $("meridian-date-from").value || null,
        date_to: $("meridian-date-to").value || null,
        keep_structure: !!($("meridian-keep-structure") || {}).checked
      })
    }).then(function (resp) {
      var lines = meridianCheckLines(resp && resp.conversion_check);
      var box = renderMeridianResult(el("div", null, [
        el("p", { class: "result-title",
                  text: t("app.mer.ok") + fmt(resp && resp.id) }),
        el("ul", null, lines.map(function (m) { return el("li", { text: m }); }))
      ]));
      box.classList.add("ok");
      $("meridian-form").reset();
      refreshInstances();
    }).catch(function (err) {
      var errors = [];
      if (err.body && typeof err.body === "object" && Array.isArray(err.body.errors)) {
        errors = err.body.errors;
      } else if (err.body && err.body.detail) {
        errors = [typeof err.body.detail === "string"
                  ? err.body.detail : JSON.stringify(err.body.detail)];
      } else {
        errors = [err.message];
      }
      var children = [
        el("p", { class: "result-title", text: t("app.mer.fail") }),
        el("ul", null, errors.slice(0, 20).map(function (m) {
          return el("li", { class: "mono small", text: String(m) });
        }))
      ];
      if (errors.length > 20) {
        children.push(el("p", { class: "muted small",
          text: t("app.mer.more") + " " + (errors.length - 20) }));
      }
      var checkLines = meridianCheckLines(
        err.body && typeof err.body === "object" ? err.body.conversion_check : null);
      if (checkLines.length) {
        children.push(el("p", { class: "result-title", text: t("app.ssim.checkTitle") }));
        children.push(el("ul", null, checkLines.map(function (m) {
          return el("li", { text: m });
        })));
      }
      var box = renderMeridianResult(el("div", null, children));
      box.classList.add("bad");
    });
  }).catch(function (err) {
    toast(t("app.t.fileReadFail") + err.message, "error");
  }).finally(function () {
    submitBtn.disabled = false;
  });
}

/* ------------------------------------------------- rules import (zone 2) */

/* Клиентское предугадывание формата по первым килобайтам файла —
   зеркало серверного detect_format_text. Догадка ТОЛЬКО информационная
   (чип): список форматов она не трогает — при «автоопределении»
   решает сервер, а явный выбор пользователя никогда не затирается
   (ревью 2026-07-24). */
function guessRulesFormat(text, fileName) {
  var s = String(text || "").replace(/^﻿/, "").replace(/^[\s]+/, "");
  var name = String(fileName || "").toLowerCase();
  if (!s) { return null; }
  if (s[0] === "{" || s[0] === "[") {
    if (s.indexOf('"cosmos-rules-json"') >= 0) { return "cosmos-rules-json"; }
    if (s.indexOf('"norm-table"') >= 0) { return "norm-table"; }
    if (s.indexOf('"constraintWeightOverrides"') >= 0) { return "solver-format5"; }
    if (/"format"\s*:\s*1\b/.test(s) && s.indexOf('"employees"') >= 0) {
      return "meridian-limits";
    }
    if (s[0] === "[" && s.indexOf('"name"') >= 0 && s.indexOf('"value"') >= 0) {
      return "norm-table";
    }
    return null;
  }
  if (s[0] === "<") { return "roster-xml"; }
  if (/(^|\n)\s*(module\s|rule\b)|%\w+%\s*=\s*parameter\b/.test(s) &&
      (/\bend\b/.test(s) || s.indexOf("= parameter") >= 0)) {
    return "jeppesen-rave";
  }
  var first = s.split("\n")[0];
  var delims = [";", ",", "\t"];
  for (var i = 0; i < delims.length; i++) {
    var cells = first.toLowerCase().split(delims[i]);
    if (cells.length > 1 &&
        cells.some(function (c) { return ["name", "имя", "constraint", "ограничение", "rule", "правило", "норма", "norm"].indexOf(c.trim()) >= 0; }) &&
        cells.some(function (c) { return ["value", "значение"].indexOf(c.trim()) >= 0; })) {
      return "norm-table";
    }
  }
  if (/\.rave$/.test(name)) { return "jeppesen-rave"; }
  if (/\.xml$/.test(name)) { return "roster-xml"; }
  return null;
}

var rulesSniffGen = 0;   // защита от гонки при быстрой смене файла

function handleRulesFileChange() {
  var fileInput = $("rules-import-file");
  var chip = $("rules-format-chip");
  var gen = ++rulesSniffGen;
  if (!fileInput.files || fileInput.files.length === 0) {
    chip.classList.add("hidden");
    return;
  }
  var file = fileInput.files[0];
  file.slice(0, 65536).text().then(function (head) {
    if (gen !== rulesSniffGen) { return; }   // выбран другой файл
    var fmt = guessRulesFormat(head, file.name);
    chip.classList.remove("hidden", "chip-neutral", "chip-ok",
                          "chip-warn");
    if (fmt) {
      chip.classList.add("chip-ok");
      chip.textContent = t("app.rules.fmtDetected") + fmt;
    } else {
      chip.classList.add("chip-warn");
      chip.textContent = t("app.rules.fmtUnknown");
    }
  }).catch(function () {
    if (gen === rulesSniffGen) { chip.classList.add("hidden"); }
  });
}

function handleRulesImportSubmit(evt) {
  evt.preventDefault();
  var fileInput = $("rules-import-file");
  if (!fileInput.files || fileInput.files.length === 0) {
    toast(t("app.t.needRulesFile"), "error");
    return;
  }
  var box = $("rules-import-result");
  var submitBtn = $("rules-import-submit");
  submitBtn.disabled = true;
  fileInput.files[0].text().then(function (text) {
    return api("/import/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        format: $("rules-import-format").value,
        name: $("rules-import-name").value.trim() || null,
        country: $("rules-import-country").value.trim().toUpperCase() || null,
        file_text: text,
        source_name: fileInput.files[0].name
      })
    }).then(function (resp) {
      box.textContent = "";
      box.classList.remove("hidden", "ok", "bad");
      var lines = [
        t("app.rules.imported") + ": " + fmt(resp && resp.imported_rules),
        t("app.rules.packName") + ": " + fmt(resp && resp.pack_name)
      ];
      (resp && resp.errors || []).slice(0, 5).forEach(function (e) {
        lines.push(t("app.rules.ruleError") + ": " + e);
      });
      box.appendChild(el("div", null, [
        el("p", { class: "result-title",
                  text: t("app.rules.ok") + fmt(resp && resp.pack_id) }),
        el("ul", null, lines.map(function (m) { return el("li", { text: m }); }))
      ]));
      box.classList.add("ok");
      $("rules-import-form").reset();
      $("rules-format-chip").classList.add("hidden");
    }).catch(function (err) {
      var errors = (err.body && err.body.errors) ||
                   [(err.body && err.body.detail) || err.message];
      box.textContent = "";
      box.classList.remove("hidden", "ok", "bad");
      box.appendChild(el("div", null, [
        el("p", { class: "result-title", text: t("app.rules.fail") }),
        el("ul", null, errors.map(function (m) {
          return el("li", { class: "mono small", text: String(m) });
        }))
      ]));
      box.classList.add("bad");
    });
  }).catch(function (err) {
    toast(t("app.t.fileReadFail") + err.message, "error");
  }).finally(function () {
    submitBtn.disabled = false;
  });
}

/* --------------------------------------------- universal file import (LLM) */

var importState = { token: null, last: null };

function abToB64(buf) {
  var bytes = new Uint8Array(buf);
  var bin = "";
  var chunk = 0x8000;
  for (var i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode.apply(
      null, bytes.subarray(i, Math.min(i + chunk, bytes.length)));
  }
  return btoa(bin);
}

function handleImportSubmit(evt) {
  evt.preventDefault();
  var name = $("import-name").value.trim();
  if (!name) { toast(t("app.t.needName"), "error"); return; }
  var input = $("import-files");
  if (!input.files || !input.files.length) {
    toast(t("app.imp.needFiles"), "error");
    return;
  }
  var scope = $("import-scope").value;
  var btn = $("import-analyze");
  btn.disabled = true;
  var box = $("import-result");
  box.classList.remove("hidden");
  box.textContent = "";
  box.appendChild(el("p", { class: "muted", text: t("app.imp.analyzing") }));

  var reads = Array.prototype.map.call(input.files, function (f) {
    return f.arrayBuffer().then(function (buf) {
      return { name: f.name, content_b64: abToB64(buf) };
    });
  });
  Promise.all(reads).then(function (items) {
    return api("/import/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: scope, files: items })
    });
  }).then(function (data) {
    importState.token = data.token;
    importState.last = data;
    renderImportResult(data);
  }).catch(function (err) {
    box.textContent = "";
    if (err.status === 400) {
      box.appendChild(el("p", { class: "chip chip-warn",
                                text: t("app.advisor.scopeErr") }));
    } else if (err.status === 422) {
      renderImportErrors(box, err);
    } else {
      apiErrorToast(err, t("app.imp.failed"));
    }
  }).finally(function () { btn.disabled = false; });
}

function renderImportErrors(box, err) {
  box.textContent = "";
  var errs = (err.body && (err.body.errors ||
              (err.body.validation && err.body.validation.errors))) || null;
  box.appendChild(el("p", { class: "chip chip-bad", text: t("app.imp.badInput") }));
  if (Array.isArray(errs)) {
    box.appendChild(el("ul", null, errs.slice(0, 20).map(function (m) {
      return el("li", { class: "small mono", text: String(m) });
    })));
  }
}

function impRuleText(rule) {
  if (!rule || typeof rule !== "object") { return "—"; }
  if ("const" in rule) { return "= " + fmt(rule.const); }
  if (rule.columns) { return rule.columns.join(" + "); }
  return fmt(rule.column);
}

function impPreviewTable(rows) {
  if (!Array.isArray(rows) || !rows.length) {
    return el("p", { class: "muted small", text: "—" });
  }
  var cols = [];
  rows.forEach(function (r) {
    Object.keys(r).forEach(function (k) {
      if (cols.indexOf(k) < 0) { cols.push(k); }
    });
  });
  cols = cols.slice(0, 8);
  return demoLikeTable(
    cols.map(function (c) { return el("th", { class: "mono small", text: c }); }),
    rows.map(function (r) {
      return el("tr", null, cols.map(function (c) {
        var v = r[c];
        if (Array.isArray(v)) { v = v.join(", "); }
        return el("td", { class: "small", text: fmt(v) });
      }));
    }));
}

/** Local table builder (demo.js has one but loads later). */
function demoLikeTable(headCells, bodyRows) {
  return el("div", { class: "table-wrap" }, [
    el("table", { class: "data-table" }, [
      el("thead", null, [el("tr", null, headCells)]),
      el("tbody", null, bodyRows)
    ])
  ]);
}

function renderImportResult(data) {
  var box = $("import-result");
  box.textContent = "";
  box.classList.remove("hidden");

  // where the mapping came from
  var srcTxt;
  if (data.source === "canonical") {
    srcTxt = t("app.imp.srcCanonical");
  } else if (data.source === "llm") {
    srcTxt = tf("app.imp.srcLlm", { backend: backendLabel(data.backend),
                                    model: data.model || "—" });
  } else {
    srcTxt = t("app.imp.srcFallback");
  }
  box.appendChild(el("div", { class: "advisor-src", text: srcTxt }));
  // strict-mode transparency: say when the model saw only masked values
  if (data.samples_masked) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.imp.masked") }));
  }

  // mapping plan (skip for already-canonical input)
  (data.plan && data.plan.files || []).forEach(function (pf) {
    if (pf.role === "ignore") { return; }
    var rows = Object.keys(pf.mapping || {}).map(function (field) {
      return el("tr", null, [
        el("td", { class: "mono small", text: field }),
        el("td", { class: "small", text: impRuleText(pf.mapping[field]) })
      ]);
    });
    box.appendChild(el("div", { class: "advisor-block" }, [
      el("div", { class: "advisor-h",
                  text: pf.file + " → " + t("app.imp.role." + pf.role) }),
      rows.length ? demoLikeTable(
        [el("th", { class: "small", text: t("app.imp.field") }),
         el("th", { class: "small", text: t("app.imp.from") })], rows)
        : el("p", { class: "muted small", text: "—" })
    ]));
  });
  if (data.plan && data.plan.rationale) {
    box.appendChild(el("p", { class: "muted small", text: data.plan.rationale }));
  }

  // stats
  var s = data.stats || {};
  box.appendChild(el("p", { class: "small", text: tf("app.imp.stats", {
    fl: fmt(s.flights), em: fmt(s.employees),
    as: fmt(s.assignments), ap: fmt(s.airports) }) }));

  // checks
  var v = data.validation || {};
  var sem = data.semantic || { errors: [], warnings: [] };
  var blocking = (!v.ok) || (sem.errors && sem.errors.length);
  var checks = el("div", { class: "advisor-block" });
  checks.appendChild(el("div", { class: "advisor-h", text: t("app.imp.checks") }));
  if (v.ok && !(sem.errors && sem.errors.length)) {
    checks.appendChild(el("span", { class: "chip chip-ok", text: t("app.imp.checksOk") }));
  } else {
    checks.appendChild(el("span", { class: "chip chip-bad", text: t("app.imp.checksBad") }));
  }
  var errList = (v.errors || []).concat(sem.errors || []);
  if (errList.length) {
    checks.appendChild(el("ul", { class: "import-errs" },
      errList.slice(0, 20).map(function (m) {
        return el("li", { class: "small", text: String(m) }); })));
  }
  var warns = (data.warnings || []).concat(sem.warnings || []);
  if (warns.length) {
    checks.appendChild(el("ul", { class: "import-warns" },
      warns.slice(0, 20).map(function (m) {
        return el("li", { class: "small", text: String(m) }); })));
  }
  box.appendChild(checks);

  // history anomalies (Ф6): statistical oddities the data owner must
  // adjudicate — advisory only, never blocks the commit
  var anom = (data.anomalies && data.anomalies.warnings) || [];
  if (anom.length) {
    box.appendChild(el("div", { class: "advisor-block" }, [
      el("div", { class: "advisor-h", text: t("app.imp.anomalies") }),
      el("p", { class: "muted small", text: t("app.imp.anomaliesHint") }),
      el("ul", { class: "import-warns" },
        anom.slice(0, 30).map(function (m) {
          return el("li", { class: "small", text: String(m) }); }))
    ]));
  }

  // preview
  var pv = data.preview || {};
  [["flights", "app.imp.pvFlights"], ["employees", "app.imp.pvEmployees"],
   ["assignments", "app.imp.pvAssignments"]].forEach(function (pair) {
    box.appendChild(el("div", { class: "advisor-block" }, [
      el("div", { class: "advisor-h", text: t(pair[1]) }),
      impPreviewTable(pv[pair[0]])
    ]));
  });

  // commit
  var actions = el("div", { class: "advisor-actions" });
  var commit = el("button", { class: "btn btn-primary btn-small",
                              text: t("app.imp.commit") });
  if (blocking) {
    commit.disabled = true;
    commit.title = t("app.imp.blocked");
  }
  commit.addEventListener("click", function () { commitImport(); });
  actions.appendChild(commit);
  box.appendChild(actions);
  if (blocking) {
    box.appendChild(el("p", { class: "muted small", text: t("app.imp.blocked") }));
  }
}

function commitImport() {
  var name = $("import-name").value.trim();
  if (!name) { toast(t("app.t.needName"), "error"); return; }
  if (!importState.token) { return; }
  api("/import/commit", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: importState.token, name: name })
  }).then(function (resp) {
    toast(tf("app.imp.saved", { id: resp.id }), "ok");
    $("import-result").classList.add("hidden");
    $("import-result").textContent = "";
    $("import-form").reset();
    importState.token = null;
    importState.last = null;
    refreshInstances();
  }).catch(function (err) {
    if (err.status === 404) { toast(t("app.imp.expired"), "error"); }
    else if (err.status === 422) { renderImportErrors($("import-result"), err); }
    else { apiErrorToast(err, t("app.imp.failed")); }
  });
}

/* --------------------------------------------------------- tab 2: Расчёты */

function renderInstancePickers() {
  ["job-instance", "results-instance", "scen-base", "vac-instance", "qual-instance", "board-instance", "bases-instance", "ops-instance", "delta-instance"].forEach(function (selectId) {
    var select = $(selectId);
    var prev = select.value;
    select.textContent = "";
    state.instances.forEach(function (inst) {
      var label = instLabel(inst);
      select.appendChild(el("option", { value: inst.id, text: label }));
    });
    if (prev && state.instances.some(function (i) { return i.id === prev; })) {
      select.value = prev;
    }
  });
  updateStageButtons();
}

function selectedJobInstance() {
  var id = $("job-instance").value;
  return state.instances.find(function (i) { return i.id === id; }) || null;
}

/** Enable/disable stage buttons per instance capabilities from the contract:
 *  stage2 needs stage2_available; complete is gerad-5..7 only; stage1 and
 *  validate are allowed for any instance. */
function updateStageButtons() {
  var inst = selectedJobInstance();
  var none = !inst;
  $("btn-stage1").disabled = none;
  $("btn-validate").disabled = none;
  $("btn-stage2").disabled = none || !inst.stage2_available;
  $("btn-complete").disabled = none || !/^gerad-[5-7]$/.test(inst.id || "");
  var hint = $("btn-stage2").previousElementSibling;
  if (hint && inst) {
    hint.textContent = inst.stage2_available
      ? (inst.has_best_catalog
         ? t("app.hint.catalogFound")
         : t("app.hint.catalogMissing"))
      : t("app.hint.stage2NA");
  }
  advisorScopeChip();
}

function startJob(type) {
  var inst = selectedJobInstance();
  if (!inst) { toast(t("app.t.pickInstance"), "error"); return; }
  // Start from any solver-advisor knobs the human confirmed for this
  // instance (pricer/profile/mp/caps…); the explicit inputs below override
  // budget/seed. The backend re-validates every knob (sanitize_params).
  var params = Object.assign({}, state.advisorParams[inst.id] || {});
  if (type === "stage1") {
    var budgetMin = parseInt($("stage1-budget").value, 10);
    if (!isFinite(budgetMin) || budgetMin < 1) {
      toast(t("app.t.budget1"), "error");
      return;
    }
    params.budget_min = budgetMin;
    // Построитель цепочек: пустой выбор оставляет прежнюю линию этапа 1
    // (генерация колонок), уровень включает построитель с этим уровнем
    // свободы. Способ поиска относится только к построителю.
    var lvlEl = $("stage1-pairing-level");
    var lvl = lvlEl ? parseInt(lvlEl.value, 10) : NaN;
    if (isFinite(lvl) && lvl >= 1 && lvl <= 3) {
      params.pairing_level = lvl;
      var mEl = $("stage1-pairing-method");
      params.pairing_method = (mEl && mEl.value) || "enumerate";
      var engEl = $("stage1-select-engine");
      if (engEl && engEl.value && engEl.value !== "cpsat") {
        params.select_engine = engEl.value;
      }
    }
  } else if (type === "stage2" || type === "multirole") {
    var rEng = $("stage2-roster-engine");
    if (type === "stage2" && rEng && rEng.value
        && rEng.value !== "timefold") {
      params.roster_engine = rEng.value;
    }
    var budgetS = parseInt($("stage2-budget").value, 10);
    var seed = parseInt($("stage2-seed").value, 10);
    if (!isFinite(budgetS) || budgetS < 1) {
      toast(t("app.t.budget2"), "error");
      return;
    }
    if (!isFinite(seed)) {
      toast(t("app.t.seedInt"), "error");
      return;
    }
    params.budget_s = budgetS;
    params.seed = seed;
    if (type === "multirole") {
      // Lane-этапы 1 мультироли ограничены бюджетом из поля этапа 1.
      var mrBudgetMin = parseInt($("stage1-budget").value, 10);
      if (!isFinite(mrBudgetMin) || mrBudgetMin < 1) {
        toast(t("app.t.budget1"), "error");
        return;
      }
      params.budget_min = mrBudgetMin;
    }
  }
  api("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: type, instance_id: inst.id, params: params })
  }).then(function (resp) {
    toast(t("app.t.jobStarted") + jobTypeLabel(type) + " / " + instLabel(inst), "ok");
    // Seed-data runs overwrite canonical artifacts — surface the warning.
    if (resp && resp.note_code === "seed_overwrite") {
      toast(t("app.seedNote"), "warn");
    } else if (resp && resp.note) {
      toast(String(resp.note), "warn");
    }
    setActiveJob(resp.job_id);
    refreshJobs();
    // Jump to «Прогоны» so the freshly launched run's live log is visible.
    showTab("runs");
  }).catch(function (err) {
    if (err.status === 409) {
      // 409 — не только «такая задача уже идёт»: это и столкновение
      // ресурсных правил двух пакетов (R4 §4.2). Текст сервера несёт
      // имена обоих пакетов — показываем его, а не общую фразу.
      var detail = err.body && err.body.detail;
      toast(typeof detail === "string" && detail
            ? detail : t("app.t.jobDup"), "error");
    } else if (err.status === 400) {
      apiErrorToast(err, t("app.t.jobRejected"));
    } else {
      apiErrorToast(err, t("app.t.jobStartFail"));
    }
  });
}

/* --------------------------------------------- solver advisor (consult) */

// Whitelist mirror of solver_advisor.sanitize_params — the UI shows the same
// knobs the backend accepts, so the human edits exactly what can be applied.
/* Зеркало границ бэкенда (solver_advisor): R5-06 — pairing_method не был
 * перечислением, редактор рисовал его числовым полем, и рекомендация
 * «columns» терялась на parseInt. */
var ADV_ENUM = { pricer: ["duty", "legs"], profile: ["gerad", "v2"],
                 pairing_method: ["enumerate", "columns"] };
var ADV_INT = ["mp", "price_cap", "final_tl", "total_cap", "max_landings",
               "pairing_level", "w_night", "w_dh", "solve_s",
               "rescue_dh", "rescue_per_leg", "rescue_s",
               "budget_s", "seed"];

function backendLabel(b) {
  if (b === "cloud") { return t("app.advisor.backend.cloud"); }
  if (b === "local") { return t("app.advisor.backend.local"); }
  return fmt(b);
}

/** Reflect the selected instance's llm_scope as a clickable chip. */
function advisorScopeChip() {
  var chip = $("advisor-scope-chip");
  if (!chip) { return; }
  var inst = selectedJobInstance();
  if (!inst) { chip.classList.add("hidden"); return; }
  chip.classList.remove("hidden");
  var scope = inst.llm_scope || "local-only";
  chip.textContent = scope === "any"
    ? t("app.advisor.scope.any") : t("app.advisor.scope.local");
  chip.className = "chip small chip-click "
    + (scope === "any" ? "chip-neutral" : "chip-warn");
  chip.title = t("app.advisor.scope.title");
}

/** Owner/admin toggles the dataset's LLM eligibility. Loosening to 'any'
 *  (cloud allowed) is confirmed; tightening is immediate. */
function toggleScope() {
  var inst = selectedJobInstance();
  if (!inst) { return; }
  var next = (inst.llm_scope === "any") ? "local-only" : "any";
  var go = function () {
    api("/instances/" + encodeURIComponent(inst.id) + "/llm-scope", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: next })
    }).then(function (r) {
      inst.llm_scope = r.llm_scope;
      var s = state.instances.find(function (i) { return i.id === inst.id; });
      if (s) { s.llm_scope = r.llm_scope; }
      advisorScopeChip();
      toast(t("app.advisor.scopeSet"), "ok");
    }).catch(function (err) { apiErrorToast(err, t("app.advisor.scopeErr2")); });
  };
  if (next === "any") {
    appConfirm(t("app.advisor.scopeConfirm")).then(function (ok) {
      if (ok) { go(); }
    });
  } else { go(); }
}

function advise() {
  var inst = selectedJobInstance();
  if (!inst) { toast(t("app.t.pickInstance"), "error"); return; }
  var box = $("advisor-result");
  box.classList.remove("hidden");
  box.textContent = "";
  box.appendChild(el("p", { class: "muted", text: t("app.advisor.loading") }));
  var btn = $("btn-advise");
  btn.disabled = true;
  api("/instances/" + encodeURIComponent(inst.id) + "/advise", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_type: $("advisor-jobtype").value,
                           backend: $("advisor-backend").value })
  }).then(function (res) {
    state.advisorRec = res;
    renderAdvisor(res);
  }).catch(function (err) {
    box.textContent = "";
    box.appendChild(el("p", { class: "muted",
      text: err.status === 400 ? t("app.advisor.scopeErr") : t("app.advisor.err") }));
    if (err.status !== 400) { apiErrorToast(err, t("app.advisor.err")); }
  }).finally(function () { btn.disabled = false; });
}

function advBlock(title, node) {
  return el("div", { class: "advisor-block" },
            [el("div", { class: "advisor-h", text: title }), node]);
}

function advPhaseNode(phase) {
  phase = phase || {};
  var lines = [];
  if (phase.engine) { lines.push(el("div", { text: phase.engine })); }
  var flags = [];
  if (phase.pricer) { flags.push("pricer=" + phase.pricer); }
  if (phase.profile) { flags.push("profile=" + phase.profile); }
  if (flags.length) {
    lines.push(el("div", { class: "mono small muted", text: flags.join("  ") }));
  }
  if (phase.notes) { lines.push(el("div", { class: "small muted", text: phase.notes })); }
  if (!lines.length) { lines.push(el("div", { class: "muted", text: "—" })); }
  return el("div", {}, lines);
}

/** Editable knob table (the human confirms/edits before applying). */
function advParamsEditor(params) {
  var wrap = el("div", { class: "advisor-params" });
  Object.keys(params).forEach(function (key) {
    var row = el("div", { class: "advisor-param-row" });
    row.appendChild(el("label", { class: "small", text: key }));
    var input;
    if (ADV_ENUM[key]) {
      input = el("select", { id: "adv-p-" + key, "data-key": key, "data-kind": "enum" });
      ADV_ENUM[key].forEach(function (opt) {
        var o = el("option", { value: opt, text: opt });
        if (opt === params[key]) { o.setAttribute("selected", "selected"); }
        input.appendChild(o);
      });
    } else {
      input = el("input", { id: "adv-p-" + key, type: "number",
                            "data-key": key, "data-kind": "int",
                            value: String(params[key]) });
    }
    row.appendChild(input);
    wrap.appendChild(row);
  });
  return wrap;
}

function renderAdvisor(res) {
  var box = $("advisor-result");
  box.textContent = "";
  box.classList.remove("hidden");
  var rec = res.recommendation || {};
  var src = res.source === "llm"
    ? tf("app.advisor.srcLlm", { backend: backendLabel(res.backend),
                                 model: res.model || "—" })
    : t("app.advisor.srcFallback");
  box.appendChild(el("div", { class: "advisor-src", text: src }));
  box.appendChild(advBlock(t("app.advisor.stage1"), advPhaseNode(rec.stage1)));
  box.appendChild(advBlock(t("app.advisor.stage2"), advPhaseNode(rec.stage2)));
  if (rec.split) {
    box.appendChild(advBlock(t("app.advisor.split"),
                             el("div", { class: "small", text: rec.split })));
  }
  if (rec.params && Object.keys(rec.params).length) {
    box.appendChild(advBlock(t("app.advisor.params"),
                             advParamsEditor(rec.params)));
  }
  if (rec.warnings && rec.warnings.length) {
    var ul = el("ul", { class: "advisor-warn" });
    rec.warnings.forEach(function (w) { ul.appendChild(el("li", { text: w })); });
    box.appendChild(advBlock(t("app.advisor.warnings"), ul));
  }
  if (rec.rationale) {
    box.appendChild(el("p", { class: "advisor-rationale", text: rec.rationale }));
  }
  var actions = el("div", { class: "advisor-actions" });
  var apply = el("button", { class: "btn btn-primary btn-small",
                             text: t("app.advisor.apply") });
  apply.addEventListener("click", applyAdvisor);
  var reset = el("button", { class: "btn btn-ghost btn-small",
                             text: t("app.advisor.reset") });
  reset.addEventListener("click", resetAdvisor);
  actions.appendChild(apply);
  actions.appendChild(reset);
  box.appendChild(actions);
  // Transparency: exactly what left the machine (aggregates only).
  var det = el("details", { class: "advisor-desc" });
  det.appendChild(el("summary", { text: t("app.advisor.descriptor") }));
  det.appendChild(el("pre", { class: "mono small",
    text: JSON.stringify(res.descriptor || {}, null, 1) }));
  box.appendChild(det);
}

/** Read the edited knob table and stage it onto the run for this instance. */
function applyAdvisor() {
  var inst = selectedJobInstance();
  if (!inst) { return; }
  var knobs = {};
  document.querySelectorAll("#advisor-result [data-key]").forEach(function (n) {
    var key = n.getAttribute("data-key");
    if (n.getAttribute("data-kind") === "int") {
      var v = parseInt(n.value, 10);
      if (isFinite(v)) { knobs[key] = v; }
    } else {
      knobs[key] = n.value;
    }
  });
  // budget_s / seed feed the existing stage-2 inputs; the rest ride params.
  if (typeof knobs.budget_s === "number") {
    $("stage2-budget").value = String(knobs.budget_s);
  }
  if (typeof knobs.seed === "number") {
    $("stage2-seed").value = String(knobs.seed);
  }
  state.advisorParams[inst.id] = knobs;
  var applied = Object.keys(knobs).map(function (k) { return k + "=" + knobs[k]; });
  var note = $("advisor-result").querySelector(".advisor-applied");
  if (note) { note.remove(); }
  $("advisor-result").appendChild(el("div", { class: "advisor-applied",
    text: t("app.advisor.applied") + " " + (applied.join(", ") || "—") }));
  toast(t("app.advisor.applied"), "ok");
}

function resetAdvisor() {
  var inst = selectedJobInstance();
  if (inst) { delete state.advisorParams[inst.id]; }
  var note = $("advisor-result").querySelector(".advisor-applied");
  if (note) { note.remove(); }
  toast(t("app.advisor.resetDone"), "ok");
}

/* ------------------------------------------------ tab 8: База знаний (KB) */

function refreshKb() {
  if (!isAdmin()) { return; }
  api("/solver-advisor/skill").then(function (sk) {
    state.kbSkill = sk;
    $("kb-doc").textContent = (sk && sk.text) || "";
    renderKbMeta();
  }).catch(function (err) { apiErrorToast(err, t("app.kb.loadErr")); });
  api("/solver-advisor/backends").then(function (b) {
    state.kbBackends = b;
    renderKbMeta();
  }).catch(function () {});
  api("/solver-advisor/build-status").then(renderKbBuildStatus)
    .catch(function () {});
}

function renderKbMeta() {
  var box = $("kb-meta");
  box.textContent = "";
  var meta = (state.kbSkill && state.kbSkill.meta) || {};
  var chips = el("div", { class: "kb-chips" });
  chips.appendChild(el("span", { class: "chip chip-neutral small",
    text: tf("app.kb.version", { n: fmt(meta.version) }) }));
  chips.appendChild(el("span", { class: "chip chip-neutral small",
    text: t("app.kb.source." + (meta.source || "unknown")) || fmt(meta.source) }));
  if (meta.updated_at) {
    chips.appendChild(el("span", { class: "muted small",
      text: fmtTime(meta.updated_at) }));
  }
  if (meta.model) {
    chips.appendChild(el("span", { class: "muted small mono", text: fmt(meta.model) }));
  }
  box.appendChild(chips);
  var b = state.kbBackends;
  if (b) {
    var line = el("div", { class: "kb-backends small muted" });
    line.appendChild(el("span", { text: tf("app.kb.backends", {
      local: b.local ? "✓" : "✗", cloud: b.cloud ? "✓" : "✗",
      model: b.local_model || "—" }) }));
    box.appendChild(line);
  }
}

function kbBuild() {
  var btn = $("btn-kb-build");
  btn.disabled = true;
  // The skill is always REBUILT by the cloud model on the Test stand from
  // public benchmarks (the documented process); the knowledge base itself
  // is a local file everywhere. No backend choice to avoid confusion.
  api("/solver-advisor/build", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ backend: "cloud" })
  }).then(function () {
    toast(t("app.kb.buildStarted"), "ok");
    startKbBuildPolling();
  }).catch(function (err) {
    if (err.status === 409) { toast(t("app.kb.buildBusy"), "warn"); }
    else { apiErrorToast(err, t("app.kb.buildErr")); }
  }).finally(function () { btn.disabled = false; });
}

function renderKbBuildStatus(st) {
  var box = $("kb-build-status");
  if (!st || (!st.running && st.ok === null && !st.finished_at)) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  if (st.running) {
    box.className = "kb-build-status building";
    box.textContent = t("app.kb.building");
  } else if (st.ok) {
    box.className = "kb-build-status ok";
    box.textContent = tf("app.kb.buildOk", { n: fmt(st.version) });
  } else {
    box.className = "kb-build-status fail";
    box.textContent = tf("app.kb.buildFail", { error: fmt(st.error) });
  }
}

function startKbBuildPolling() {
  if (state.kbBuildTimer) { clearInterval(state.kbBuildTimer); }
  state.kbBuildTimer = setInterval(function () {
    api("/solver-advisor/build-status").then(function (st) {
      renderKbBuildStatus(st);
      if (!st.running) {
        clearInterval(state.kbBuildTimer);
        state.kbBuildTimer = null;
        refreshKb();
      }
    }).catch(function () {});
  }, 3000);
}

/* --- rich «Прогоны» dashboard (Panel, admin-only, lazy iframe) ----------- */

var runsDashMounted = false;

function mountRunsDashboard() {
  var head = $("runs-rich-head");
  var frame = $("runs-dash-frame");
  if (!head || !frame) { return; }
  if (!isAdmin()) {
    head.classList.add("hidden");
    frame.classList.add("hidden");
    return;
  }
  head.classList.remove("hidden");
  frame.classList.remove("hidden");
  // Load the in-process Panel app on first admin visit (it opens a Bokeh
  // websocket; no reason to pay that before the tab is shown), and reload it
  // when the UI language changes — the dashboard reads ?lang= to localize its
  // own strings (the iframe is a separate document, isolated from i18n.js).
  // Only (re)assign src when the target actually changes, to avoid needless
  // websocket churn.
  var want = "/runs-dashboard?lang=" + encodeURIComponent(uiLang());
  if (frame.getAttribute("data-lang-src") !== want) {
    frame.setAttribute("data-lang-src", want);
    frame.src = want;
    runsDashMounted = true;
  }
}

/* --- live log panel ------------------------------------------------------ */

function setActiveJob(jobId) {
  state.activeJobId = jobId;
  if (state.activeJobTimer) {
    clearInterval(state.activeJobTimer);
    state.activeJobTimer = null;
  }
  if (!jobId) {
    $("active-job-body").classList.add("hidden");
    $("active-job-empty").classList.remove("hidden");
    if (state.cvgTimer) { clearInterval(state.cvgTimer); state.cvgTimer = null; }
    state.cvgClock = null;
    var chart = $("active-job-chart");
    if (chart) { chart.classList.add("hidden"); }
    return;
  }
  $("active-job-body").classList.remove("hidden");
  $("active-job-empty").classList.add("hidden");
  $("active-job-log-link").setAttribute("href", API + "/jobs/" + encodeURIComponent(jobId) + "/log");
  pollActiveJob();
  state.activeJobTimer = setInterval(pollActiveJob, POLL_MS);
}

function pollActiveJob() {
  var jobId = state.activeJobId;
  if (!jobId) { return; }
  api("/jobs/" + encodeURIComponent(jobId)).then(function (job) {
    if (state.activeJobId !== jobId) { return; } // switched away meanwhile
    renderActiveJob(job);
    pollConvergence(jobId, job);
    if (job && job.status !== "running" && state.activeJobTimer) {
      clearInterval(state.activeJobTimer);
      state.activeJobTimer = null;
      refreshJobs();
      refreshInstances(); // gate badge / catalog flags may have changed
    }
  }).catch(function (err) {
    if (state.activeJobId !== jobId) { return; }
    if (state.activeJobTimer) {
      clearInterval(state.activeJobTimer);
      state.activeJobTimer = null;
    }
    apiErrorToast(err, t("app.t.jobPoll") + jobId);
  });
}

function renderActiveJob(job) {
  state.lastActiveJob = job;   // re-rendered as-is on language switch
  var title = jobTypeLabel(job.type) + " / " +
              instTitleById(job.instance_id) + " — " + fmt(job.id);
  $("active-job-title").textContent = title;
  var statusHolder = $("active-job-status");
  var chip = jobStatusChip(job);
  statusHolder.replaceWith(chip);
  chip.id = "active-job-status";
  $("btn-stop-job").classList.toggle("hidden", job.status !== "running");

  var pre = $("active-job-log");
  var lines = Array.isArray(job.log_tail) ? job.log_tail : [];
  // Keep the view pinned to the bottom only if the user has not scrolled up.
  var pinned = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
  pre.textContent = lines.join("\n");
  if (pinned) { pre.scrollTop = pre.scrollHeight; }
}

function stopActiveJob() {
  var jobId = state.activeJobId;
  if (!jobId) { return; }
  api("/jobs/" + encodeURIComponent(jobId) + "/stop", { method: "POST" })
    .then(function () {
      toast(t("app.t.stopRequested") + jobId, "ok");
      pollActiveJob();
    })
    .catch(function (err) { apiErrorToast(err, t("app.t.stopFail")); });
}

/* ------------------------------------------------------------------ */
/* График схождения оптимизации («коридор неопределённости»).
   Данные — машинный след прогона (/api/jobs/{id}/progress), который
   пишут сами решатели; журнал не парсится. Этап 1 несёт инкумбент И
   нижнюю границу — их схождение доказывает оптимальность; этап 2
   (локальный поиск) границы не имеет — рисуются инкумбент и пунктир
   идеала, о чём сказано подписью, а не молчанием. */

function pollConvergence(jobId, job) {
  if (["stage1", "stage2", "multirole"].indexOf(job.type) < 0) { return; }
  api("/jobs/" + encodeURIComponent(jobId) + "/progress").then(function (r) {
    if (state.activeJobId !== jobId) { return; }
    // Живое время из бюджета (замечание владельца 2026-08-12): сервер
    // отдаёт elapsed_s/budget_s; тикер дотикивает секунды между
    // опросами от момента ответа — часовые зоны не участвуют.
    state.cvgClock = {
      at: Date.now(),
      elapsed: (r && typeof r.elapsed_s === "number") ? r.elapsed_s : null,
      budget: (r && typeof r.budget_s === "number") ? r.budget_s : null,
      events: r && r.events || [],
      // Тип и запись задачи держим здесь же: между опросами (каждые 2 с)
      // график перерисовывает СЕКУНДНЫЙ тикер, и ему нужно то же, с чем
      // рисовал опрос, — иначе кривая стояла бы до следующего ответа.
      jobType: job.type,
      job: job
    };
    renderConvergence($("active-job-chart"), state.cvgClock.events,
                      job.type, job);
  }).catch(function () { /* график вторичен: молча без него */ });
}

function cvgTimeText() {
  var c = state.cvgClock;
  if (!c) { return ""; }
  var elapsed;
  if (c.elapsed != null) {
    elapsed = c.elapsed + (Date.now() - c.at) / 1000;   // тикаем сами
  } else if (c.events.length) {
    elapsed = c.events[c.events.length - 1].t;          // завершено
  } else { return ""; }
  var e = Math.round(elapsed);
  return c.budget ? e + " / " + Math.round(c.budget) : String(e);
}

function cvgStartTicker() {
  if (state.cvgTimer) { clearInterval(state.cvgTimer); }
  state.cvgTimer = setInterval(function () {
    var cell = $("cvg-time-val");
    var c = state.cvgClock;
    if (!cell || !c || c.elapsed == null) {
      clearInterval(state.cvgTimer);
      state.cvgTimer = null;
      return;
    }
    if (c.events && c.events.length >= 2 && c.job) {
      // Кривая ДВИЖЕТСЯ каждую секунду: последняя ступень и отметка
      // «сейчас» доходят до текущего момента, не дожидаясь опроса
      // (замечание владельца 2026-08-13). Перерисовка ставит тикер
      // заново — отдельный вызов здесь не нужен.
      renderConvergence($("active-job-chart"), c.events, c.jobType, c.job);
      return;
    }
    cell.textContent = cvgTimeText();
  }, 1000);
}

function cvgStep(pts, endX) {
  // Ступенчатый путь: решение улучшается скачком, наклон рисовал бы
  // несуществующие промежуточные решения. Последняя ступень тянется до
  // endX — момента «сейчас» у идущей задачи (кривая растёт слева
  // направо) или конца прогона у завершённой.
  var d = "";
  for (var i = 0; i < pts.length; i++) {
    var p = pts[i];
    d += (i ? " H" + p[0].toFixed(1) + " V" : "M" + p[0].toFixed(1) + " ") +
         p[1].toFixed(1);
  }
  if (pts.length && endX > pts[pts.length - 1][0]) {
    d += " H" + endX.toFixed(1);
  }
  return d;
}

function cvgElapsed() {
  /* Секунды от старта прогона по часам СЕРВЕРА, дотиканные локально
     между опросами (часовые зоны не участвуют). null — если задача
     завершена: у неё время берётся из последнего события следа. */
  var c = state.cvgClock;
  if (!c || c.elapsed == null) { return null; }
  return c.elapsed + (Date.now() - c.at) / 1000;
}

function cvgTimeLabel(sec, budget) {
  /* Подпись деления оси. До десяти минут — секунды, дальше минуты:
     «450» на часовом бюджете читается хуже, чем «7 мин». */
  if (budget > 900) { return Math.round(sec / 60) + " " + t("app.cvg.min"); }
  return String(Math.round(sec));
}

function renderConvergence(box, events, jobType, job) {
  if (!box) { return; }
  var evs = events.filter(function (e) {
    return typeof e.incumbent === "number";
  });
  var running = job && job.status === "running";
  if (evs.length < 2) {
    // Пустой ход у РАБОТАЮЩЕЙ задачи — не молчание: перебор кандидатов
    // идёт десятки минут до первого события покрытия, и панель обязана
    // показывать живое время из бюджета (замечание владельца
    // 2026-08-12), а не пустоту.
    if (!running) { box.classList.add("hidden"); return; }
    box.innerHTML = "";
    box.classList.remove("hidden");
    box.appendChild(el("div", { class: "cvg-title",
      text: jobType === "stage1" ? t("app.cvg.titleS1")
                                 : t("app.cvg.titleS2") }));
    var wh = el("div", { class: "cvg-head" });
    wh.appendChild(el("span", { class: "cvg-cell" }, [
      el("span", { class: "cvg-label", text: t("app.cvg.time") }),
      el("span", { class: "cvg-value", id: "cvg-time-val",
                   text: cvgTimeText() })]));
    box.appendChild(wh);
    box.appendChild(el("div", { class: "cvg-note",
                                text: t("app.cvg.waiting") }));
    cvgStartTicker();
    return;
  }
  var isS2 = evs[0].bound == null;
  // Этап 2: инкумбент — мягкий штраф (отрицательный); рисуем модуль.
  var val = function (e) { return isS2 ? Math.abs(e.incumbent) : e.incumbent; };
  // Ось времени — ВЕСЬ БЮДЖЕТ с самого начала (замечание владельца
  // 2026-08-13). Прежде домен растягивался по последнему событию, и
  // ростеринг, нашедший решение на 114-й секунде из 600, рисовал обвал у
  // ПРАВОГО края — читалось как «улучшение случилось в конце», хотя оно
  // случилось на пятой части бюджета. Домен расширяется за бюджет только
  // если прогон его перебрал (последнее событие или «сейчас» дальше).
  var clock = state.cvgClock || {};
  var budget = (typeof clock.budget === "number" && clock.budget > 0)
    ? clock.budget : 0;
  var nowT = cvgElapsed();
  var lastT = evs[evs.length - 1].t;
  var tMax = Math.max(budget, lastT, nowT || 0, 1);
  var vs = evs.map(val);
  // Отрицательная «граница» — это НЕ число: так CP-SAT отвечает, пока
  // граница ещё не построена (первые секунды). Прежде она попадала в
  // шкалу, и весь содержательный ход (2,67–2,74 млн) сплющивался в
  // линию у верхнего края, потому что ось тянулась до −298 млн.
  var bndEvs = isS2 ? [] : evs.filter(function (e) {
    return typeof e.bound === "number" && e.bound > 0;
  });
  var bs = bndEvs.map(function (e) { return e.bound; });
  var lo = Math.min.apply(null, vs.concat(bs.length ? bs : vs));
  var hi = Math.max.apply(null, vs);
  // Лог-шкала, когда линейная расплющила бы весь ход (обвал этапа 2).
  var useLog = lo > 0 && hi / lo > 100;
  var sc = function (v) { return useLog ? Math.log10(v) : v; };
  var sLo = sc(Math.max(lo, 1e-9)), sHi = sc(Math.max(hi, 1e-9));
  if (sHi - sLo < 1e-9) { sHi = sLo + 1; }
  var W = 640, H = 180, X0 = 52, Y0 = 12;
  var px = function (tt) { return X0 + (tt / tMax) * (W - X0 - 8); };
  var py = function (v) {
    return Y0 + (1 - (sc(Math.max(v, 1e-9)) - sLo) / (sHi - sLo)) * (H - Y0 - 24);
  };
  // Правый край кривой: «сейчас» у идущей задачи, последнее событие у
  // завершённой. Именно он даёт движение слева направо.
  var endX = px(Math.min(running && nowT != null ? nowT : lastT, tMax));
  var incPts = evs.map(function (e) { return [px(e.t), py(val(e))]; });
  var svg = rvSvg("svg", { viewBox: "0 0 " + W + " " + H, width: "100%",
                           role: "img" });
  // сетка: три горизонтали
  for (var g = 0; g <= 2; g++) {
    var gy = Y0 + g * (H - Y0 - 24) / 2;
    svg.appendChild(rvSvg("line", { x1: X0, y1: gy, x2: W - 8, y2: gy,
      stroke: "var(--border)", "stroke-width": 1 }));
    var gv = useLog ? Math.pow(10, sHi - g * (sHi - sLo) / 2)
                    : hi - g * (hi - lo) / 2;
    var lbl = rvSvg("text", { x: X0 - 6, y: gy + 4, "text-anchor": "end",
      "font-size": 10, fill: "var(--muted)" });
    lbl.textContent = rvGrp(gv);
    svg.appendChild(lbl);
  }
  // Ось времени: четыре деления от нуля до конца бюджета — видно и
  // сколько прошло, и сколько осталось.
  var axisY = H - 24 + 6;
  for (var k = 0; k <= 4; k++) {
    var tx = X0 + k * (W - X0 - 8) / 4;
    var tv = tMax * k / 4;
    if (k > 0) {
      svg.appendChild(rvSvg("line", { x1: tx, y1: Y0, x2: tx, y2: axisY,
        stroke: "var(--border)", "stroke-width": 1, opacity: 0.5 }));
    }
    var tl = rvSvg("text", { x: tx, y: H - 4,
      "text-anchor": k === 4 ? "end" : (k === 0 ? "start" : "middle"),
      "font-size": 10, fill: "var(--muted)" });
    tl.textContent = cvgTimeLabel(tv, tMax);
    svg.appendChild(tl);
  }
  if (!isS2 && bndEvs.length) {
    var bndPts = bndEvs.map(function (e) { return [px(e.t), py(e.bound)]; });
    // Полоса зазора начинается там, где граница ПОЯВИЛАСЬ: рисовать
    // зазор до этого момента значило бы показывать несуществующее
    // доказательство.
    var bandX0 = bndPts[0][0];
    var band = "";
    incPts.forEach(function (p, i) {
      band += (i ? " L" : "M") + Math.max(p[0], bandX0).toFixed(1) + " " +
              p[1].toFixed(1);
    });
    band += " L" + endX.toFixed(1) + " " +
            incPts[incPts.length - 1][1].toFixed(1);
    band += " L" + endX.toFixed(1) + " " +
            bndPts[bndPts.length - 1][1].toFixed(1);
    for (var i = bndPts.length - 1; i >= 0; i--) {
      band += " L" + bndPts[i][0].toFixed(1) + " " + bndPts[i][1].toFixed(1);
    }
    band += " Z";
    svg.appendChild(rvSvg("path", { d: band, fill: "var(--accent)",
                                    opacity: 0.10, id: "cvg-band" }));
    svg.appendChild(rvSvg("path", { d: cvgStep(bndPts, endX), id: "cvg-bound",
      fill: "none", stroke: "var(--ok)", "stroke-width": 2,
      "stroke-linejoin": "round" }));
  } else if (isS2) {
    // пунктир идеала (ноль штрафа) по нижнему краю
    var iy = H - 24 + 6;
    svg.appendChild(rvSvg("line", { x1: X0, y1: iy, x2: W - 8, y2: iy,
      stroke: "var(--ok)", "stroke-width": 1.5, "stroke-dasharray": "5 5" }));
  }
  svg.appendChild(rvSvg("path", { d: cvgStep(incPts, endX), id: "cvg-inc",
    fill: "none", stroke: "var(--accent)", "stroke-width": 2,
    "stroke-linejoin": "round" }));
  // Отметка «сейчас» у идущей задачи: кривая слева от неё — прошлое,
  // пустое поле справа — остаток бюджета.
  if (running && nowT != null) {
    svg.appendChild(rvSvg("line", { id: "cvg-now", x1: endX, y1: Y0,
      x2: endX, y2: axisY, stroke: "var(--accent)", "stroke-width": 1,
      "stroke-dasharray": "3 3", opacity: 0.7 }));
  }
  var last = evs[evs.length - 1];
  // числовая шапка
  var head = el("div", { class: "cvg-head" });
  function cell(label, value) {
    head.appendChild(el("span", { class: "cvg-cell" }, [
      el("span", { class: "cvg-label", text: label }),
      el("span", { class: "cvg-value", text: value })]));
  }
  cell(t("app.cvg.incumbent"), rvGrp(val(last)));
  if (!isS2) {
    // Пока граница не построена — прочерк, а не отрицательное число:
    // «−298 млн» читается как результат, хотя означает её отсутствие.
    var lastB = bndEvs.length ? bndEvs[bndEvs.length - 1].bound : null;
    cell(t("app.cvg.bound"), lastB == null ? "—" : rvGrp(lastB));
    var gap = (lastB != null && val(last))
      ? Math.abs(val(last) - lastB) / val(last) * 100 : null;
    cell(t("app.cvg.gap"), gap == null ? "—" : gap.toFixed(1) + " %");
  } else {
    cell(t("app.cvg.ideal"), "0" + (useLog ? " · " + t("app.cvg.log") : ""));
  }
  // Живое время из бюджета — «1340 / 3600» (тикает каждую секунду).
  var timeVal = el("span", { class: "cvg-value", id: "cvg-time-val",
                             text: cvgTimeText() });
  head.appendChild(el("span", { class: "cvg-cell" }, [
    el("span", { class: "cvg-label", text: t("app.cvg.time") }), timeVal]));
  box.innerHTML = "";
  box.classList.remove("hidden");
  // Заголовок — график ЧЕГО (проверка инструкции 2026-08-12: без него
  // читатель видел линии, но не предмет).
  box.appendChild(el("div", { class: "cvg-title",
    text: isS2 ? t("app.cvg.titleS2") : t("app.cvg.titleS1") }));
  box.appendChild(head);
  box.appendChild(svg);
  var note = el("div", { class: "cvg-note",
    text: isS2 ? t("app.cvg.noteS2") : t("app.cvg.noteS1") });
  box.appendChild(note);
  if (running) { cvgStartTicker(); }
}

/* --- history list -------------------------------------------------------- */

function renderJobsTable() {
  var tbody = $("jobs-tbody");
  tbody.textContent = "";
  if (!state.jobs.length) {
    tbody.appendChild(el("tr", null, [
      el("td", { colspan: "8", class: "empty-row", text: t("app.jobs.none") })
    ]));
    return;
  }
  state.jobs.forEach(function (job) {
    var openBtn = el("button", { class: "btn btn-ghost btn-small",
                                 text: t("app.btn.log") });
    openBtn.addEventListener("click", function () { setActiveJob(job.id); });
    var actions = [openBtn];
    if (job.status !== "running") {
      // Уборка истории (проверка инструкции 2026-08-12): запись и журнал
      // выполнения удаляются, артефакты результатов — нет.
      var delBtn = el("button", { class: "btn btn-ghost btn-small",
                                  text: t("app.btn.deleteRun") });
      delBtn.addEventListener("click", function () {
        if (!window.confirm(t("app.t.deleteRunConfirm") + " " + job.id)) {
          return;
        }
        api("/jobs/" + encodeURIComponent(job.id), { method: "DELETE" })
          .then(function () {
            toast(t("app.t.deletedRun") + job.id, "ok");
            if (state.activeJobId === job.id) { setActiveJob(null); }
            refreshJobs();
          })
          .catch(function (err) {
            apiErrorToast(err, t("app.t.deleteRunFail"));
          });
      });
      actions.push(delBtn);
    }
    var tr = el("tr", null, [
      el("td", { class: "mono", text: fmt(job.id) }),
      el("td", { text: jobTypeLabel(job.type) }),
      el("td", { class: "mono", text: instTitleById(job.instance_id) }),
      el("td", null, [jobStatusChip(job)]),
      el("td", { text: fmtTime(job.started_at) }),
      el("td", { text: fmtTime(job.ended_at) }),
      el("td", { class: "num", text: fmt(job.exit_code) }),
      el("td", null, actions)
    ]);
    tbody.appendChild(tr);
  });
}

function refreshJobs() {
  return api("/jobs").then(function (list) {
    state.jobs = Array.isArray(list) ? list : [];
    renderJobsTable();
  }).catch(function (err) {
    apiErrorToast(err, t("app.t.history"));
  });
}

function startJobsListPolling() {
  if (state.jobsListTimer) { return; }
  // The per-job poll is 2 s (contract); the history list refreshes slower.
  state.jobsListTimer = setInterval(function () {
    if (state.jobs.some(function (j) { return j.status === "running"; })) {
      refreshJobs();
    }
  }, 5000);
}

function stopJobsListPolling() {
  if (state.jobsListTimer) {
    clearInterval(state.jobsListTimer);
    state.jobsListTimer = null;
  }
}

/* ------------------------------------------------------ tab 3: Результаты */

/** Ordered field lists (i18n keys) so the cards read the same everywhere. */
var STAGE1_FIELDS = [
  ["objective", "app.f.objective"],
  ["ip_status", "app.f.ip_status"],
  ["covered", "app.f.covered"],
  ["total_legs", "app.f.total_legs"],
  ["proven_impossible", "app.f.proven_impossible"],
  ["source", "app.f.source"],
  ["fingerprint", "app.f.fingerprint"]
];

var STAGE2_FIELDS = [
  ["result_status", "app.f.result_status"],
  ["stage2_score", "app.f.stage2_score"],
  ["pref_hits", "app.f.pref_hits"],
  ["pref_attainable", "app.f.pref_attainable"],
  ["vacation_violations", "app.f.vacation_violations"],
  ["unassigned", "app.f.unassigned"],
  ["trajectory_violations", "app.f.trajectory_violations"],
  ["missing_legs", "app.f.missing_legs"],
  ["budget_s", "app.f.budget_s"],
  ["seed", "app.f.seed"],
  ["wall_s", "app.f.wall_s"]
];

function renderKv(holderId, obj, fields) {
  var holder = $(holderId);
  holder.textContent = "";
  if (!obj) {
    holder.appendChild(el("p", { class: "muted",
                                 text: t("app.results.noStage") }));
    return;
  }
  fields.forEach(function (pair) {
    var key = pair[0], label = t(pair[1]);
    var value = obj[key];
    if (Array.isArray(value)) {
      value = value.length ? value.join(", ") : t("app.none");
    }
    holder.appendChild(el("div", { class: "kv-row" }, [
      el("dt", { text: label }),
      el("dd", { text: fmt(value) })
    ]));
  });
}

function renderValidatorRow(tbody, name, row) {
  var dataCell;
  if (!row) {
    dataCell = el("td", { class: "muted", text: t("app.noData") });
  } else if (typeof row === "object") {
    var parts = Object.keys(row).map(function (k) {
      var v = row[k];
      if (v !== null && typeof v === "object") { v = JSON.stringify(v); }
      return k + ": " + fmt(v);
    });
    dataCell = el("td", { class: "mono small", text: parts.join("; ") });
  } else {
    dataCell = el("td", { text: fmt(row) });
  }
  tbody.appendChild(el("tr", null, [el("td", { text: name }), dataCell]));
}

/* Мультироль (фаза 2b): сводка вердикта + таблица сравнения с со-локацией.
   Карточка скрыта, пока мультироль ни разу не запускалась. */
var MR_COMPARE_ROWS = [
  ["result_status", "app.f.result_status"],
  ["stage2_score", "app.f.stage2_score"],
  ["pref_hits", "app.f.pref_hits"],
  ["vacation_violations", "app.f.vacation_violations"],
  ["unassigned", "app.f.unassigned"],
  ["wall_s", "app.f.wall_s"]
];

function renderMultirole(mr, ar, instId) {
  var card = $("multirole-card");
  if (!mr) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  var merged = mr.merged || {};
  var cov = merged.coverage || {};
  var phys = merged.physics || {};
  var comp = merged.composition || {};
  renderKv("multirole-kv", {
    result_status: mr.result_status,
    roles: (mr.roles || []).join(", "),
    covered: cov.fully_covered,
    missing: cov.missing,
    skill_mismatches: cov.skill_mismatches,
    physics: phys.violations_total,
    composition: comp.enabled ? comp.violations_total : "—",
    checked_at: mr.checked_at
  }, [
    ["result_status", "app.f.result_status"],
    ["roles", "app.mr.roles"],
    ["covered", "app.mr.covered"],
    ["missing", "app.f.missing_legs"],
    ["skill_mismatches", "app.mr.skillMismatches"],
    ["physics", "app.mr.physics"],
    ["composition", "app.mr.composition"],
    ["checked_at", "app.mr.checkedAt"]
  ]);
  var roles = mr.roles || [];
  var head = $("mr-compare-head");
  head.textContent = "";
  var hrow = el("tr", null, [el("th", { text: t("app.mr.metric") }),
                             el("th", { text: t("app.mr.colocation") })]);
  roles.forEach(function (r) { hrow.appendChild(el("th", { text: fmt(r) })); });
  head.appendChild(hrow);
  var body = $("mr-compare-tbody");
  body.textContent = "";
  MR_COMPARE_ROWS.forEach(function (pair) {
    var key = pair[0];
    var tr = el("tr", null, [el("td", { text: t(pair[1]) })]);
    var colo = mr.colocation;
    tr.appendChild(el("td", { text: colo ? fmt(colo[key]) : "—" }));
    roles.forEach(function (r) {
      var lane = (mr.lanes || {})[r] || {};
      var m = lane.metrics;
      // Вердикт — ВСЕГДА из свежего артефакта (журнальная строка может быть
      // от предыдущего прогона; сервер такие метрики уже отбрасывает, клиент
      // дублирует защиту).
      var v = (key === "result_status")
        ? lane.result_status
        : (m ? m[key] : null);
      tr.appendChild(el("td", { text: v == null ? "—" : fmt(v) }));
    });
    body.appendChild(tr);
  });
  // Аудит-2 (P1-06): источник активного ростера + явная промоция
  // multirole-OK — старый co-location артефакт не должен молча оставаться
  // «основным» после multirole-only OK.
  var src = $("mr-active-roster");
  if (!src) {
    src = el("div", { id: "mr-active-roster", class: "hint" });
    card.appendChild(src);
  }
  src.textContent = "";
  if (ar) {
    // P0-1: текстовая строка источника ростера живёт в одном месте —
    // results-ar-line у бейджа вердикта; здесь остаётся только кнопка
    // промоции multirole.
    if (mr.result_status === "OK" && ar.mode !== "multirole" && instId) {
      var btn = el("button", { class: "btn", type: "button",
                               text: t("app.ar.promote") });
      btn.addEventListener("click", function () {
        api("/instances/" + encodeURIComponent(instId) + "/roster/promote",
            { method: "POST" })
          .then(function () {
            toast(t("app.ar.promoted"), "ok");
            refreshResults();
          })
          .catch(function (err) {
            apiErrorToast(err, t("app.ar.promoteFail"));
          });
      });
      src.appendChild(btn);
    }
  }
}

function refreshResults() {
  var id = $("results-instance").value;
  state.resultsInstance = id || null;
  if (!id) {
    $("results-body").classList.add("hidden");
    $("results-empty").classList.remove("hidden");
    $("results-empty").textContent = t("html.results.empty");
    return;
  }
  api("/results/" + encodeURIComponent(id)).then(function (res) {
    // Guard against a slow response for a PREVIOUSLY selected instance
    // overwriting the current one (the picker may have moved on).
    if (state.resultsInstance !== id) { return; }
    $("results-body").classList.remove("hidden");
    $("results-empty").classList.add("hidden");

    // Verdict: prefer the live stage-2 result status; fall back to the
    // instance's last recorded gate status.
    var inst = state.instances.find(function (i) { return i.id === id; });
    var verdict = (res && res.stage2 && res.stage2.result_status) ||
                  (inst && inst.last_gate_status) || null;
    var badge = verdictBadge(verdict);
    var holder = $("results-verdict");
    holder.replaceWith(badge);
    badge.id = "results-verdict";

    // P0-1: источник активного ростера и его предупреждения (в т.ч.
    // «последний прогон не-OK») видны у ЛЮБОГО экземпляра, а не только
    // внутри multirole-карточки.
    var ar = res && res.active_roster;
    var arRow = $("results-ar-line");
    if (!arRow) {
      arRow = el("p", { id: "results-ar-line", class: "muted small" });
      badge.parentNode.appendChild(arRow);
    }
    arRow.textContent = "";
    if (ar && (ar.mode || ar.status)) {
      var arLine = t("app.ar.mode") + ": " + (ar.mode || ar.status);
      if ((ar.warnings || []).length) {
        arLine += " · " + ar.warnings.map(function (w) {
          return t("app.ar." + w) || w;
        }).join(" · ");
      }
      arRow.textContent = arLine;
    }

    renderKv("stage1-kv", res && res.stage1, STAGE1_FIELDS);
    renderKv("stage2-kv", res && res.stage2, STAGE2_FIELDS);
    renderMultirole(res && res.multirole,
                    res && res.active_roster, id);

    var vt = $("validators-tbody");
    vt.textContent = "";
    var validators = (res && res.validators) || {};
    renderValidatorRow(vt, t("app.v.coverage"), validators.coverage);
    renderValidatorRow(vt, t("app.v.trajectory"), validators.trajectory);

    renderRosterViz(id, !!(res && res.stage2 && res.stage2.result_status));
    renderPairingViz(id, !!(res && res.stage1));
    // Кнопка-вход на карточке этапа 1: подводка к схеме цепочек ниже
    // (гейт — наличие результата этапа 1, этап 2 не требуется).
    var pvRow = $("stage1-viz-row");
    if (!pvRow) {
      pvRow = el("p", { id: "stage1-viz-row" });
      $("stage1-card").appendChild(pvRow);
    }
    pvRow.textContent = "";
    if (res && res.stage1) {
      var pvBtn = el("button", { class: "btn btn-ghost btn-small",
                                 type: "button", text: t("app.pviz.openBtn") });
      pvBtn.addEventListener("click", function () {
        var h = $("pairing-viz-head");
        if (h) { h.scrollIntoView({ behavior: "smooth", block: "start" }); }
      });
      pvRow.appendChild(pvBtn);
    }
    loadCosting(id);
    loadPay(id);

    var rosterRow = $("roster-csv-row");
    rosterRow.textContent = "";
    if (res && res.stage2 && res.stage2.result_status) {
      rosterRow.appendChild(el("a", {
        class: "btn btn-primary btn-small",
        href: API + "/results/" + encodeURIComponent(id) + "/roster.csv",
        text: t("app.results.rosterBtn")
      }));
      // Протокол сверки решения с документами а/к — часть выходного
      // комплекта решения (владелец 2026-08-31). Ручка отдаёт
      // последний протокол; без прогона сверки — честный 404.
      rosterRow.appendChild(el("a", {
        class: "btn btn-ghost btn-small",
        href: API + "/source-check/protocol",
        text: t("app.results.srcCheckBtn")
      }));
    } else {
      rosterRow.appendChild(el("span", {
        class: "muted",
        text: t("app.results.rosterLater")
      }));
    }

    var list = $("artifacts-list");
    list.textContent = "";
    var artifacts = (res && Array.isArray(res.artifacts)) ? res.artifacts : [];
    if (!artifacts.length) {
      list.appendChild(el("li", { class: "muted",
                                  text: t("app.results.noArtifacts") }));
    }
    artifacts.forEach(function (a) {
      var li = el("li", null, [
        el("a", {
          href: API + "/artifacts/" + encodeURIComponent(a.name || ""),
          text: fmt(a.name)
        }),
        el("span", { class: "muted small", text: " — " + fmtBytes(a.bytes) })
      ]);
      list.appendChild(li);
    });
  }).catch(function (err) {
    if (state.resultsInstance !== id) { return; }
    apiErrorToast(err, t("app.t.results") + id);
    $("results-body").classList.add("hidden");
    $("results-empty").classList.remove("hidden");
    $("results-empty").textContent = tf("app.results.loadFail", { id: id });
  });
}

/* -------------------------------------- results: costing + hotels (#13) */

function costMoney(v, cur) {
  if (typeof v !== "number" || !isFinite(v)) { return "—"; }
  return v.toLocaleString(localeTag(),
                          { maximumFractionDigits: 0 }) + " " + cur;
}

function loadCosting(instId) {
  var card = $("costing-card");
  if (!card) { return; }
  card.classList.add("hidden");
  api("/costing/" + encodeURIComponent(instId)).then(function (rep) {
    // Bail if the selection moved on while this was in flight — otherwise a
    // slow instance's money/KPI would land on the current instance's card.
    if (state.resultsInstance !== instId) { return; }
    if (!rep || rep.available !== true) { return; }
    renderCosting(rep);
    card.classList.remove("hidden");
    // KPI strip on top of the money card (unit economics at a glance).
    api("/kpi/" + encodeURIComponent(instId)).then(function (k) {
      if (state.resultsInstance !== instId) { return; }
      if (k && k.available) { renderKpiStrip(k); }
    }).catch(function () {});
  }).catch(function () { /* costing is optional decoration */ });
}

/** Compact KPI tiles: unit cost / productivity / coverage / utilization. */
function renderKpiStrip(k) {
  var body = $("costing-body");
  var old = body.querySelector(".kpi-strip");
  if (old) { old.remove(); }
  var strip = el("div", { class: "kpi-strip" });
  function tile(value, label, cls) {
    strip.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: value }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  tile(k.unit_cost_block_h !== null
         ? costMoney(k.unit_cost_block_h, k.currency) : "—",
       t("app.kpi.unitCost"));
  tile(k.productivity_pct !== null ? k.productivity_pct + "%" : "—",
       t("app.kpi.productivity"));
  tile(k.coverage_pct !== null ? k.coverage_pct + "%"
         : t("app.kpi.noRoster"),
       t("app.kpi.coverage"),
       (k.coverage_pct !== null && k.coverage_pct < 100) ? "kpi-warn" : "");
  tile(k.crew_utilization_block_h !== null
         ? k.crew_utilization_block_h + " " + t("app.cost.blockH") : "—",
       t("app.kpi.utilization"));
  body.insertBefore(strip, body.firstChild);
}

/* --------------------------------- results: pay-and-credit (gap #1, P0) */

/** Monotone request counter: a config save re-fetches the SAME instance,
 *  so the instance-id guard alone cannot drop an out-of-order older
 *  response (audit 2026-07-15). */
var _paySeq = 0;

function loadPay(instId) {
  var card = $("pay-card");
  if (!card) { return; }
  card.classList.add("hidden");
  var seq = ++_paySeq;
  api("/pay/" + encodeURIComponent(instId)).then(function (rep) {
    if (seq !== _paySeq || state.resultsInstance !== instId) { return; }
    if (!rep || rep.available !== true) { return; }
    renderPay(rep);
    card.classList.remove("hidden");
  }).catch(function () { /* pay card is optional decoration */ });
}

var PAY_BINDING = {
  block: "app.pay.bind.block", duty_rig: "app.pay.bind.dutyRig",
  min_duty: "app.pay.bind.minDuty", duties: "app.pay.bind.duties",
  trip_rig: "app.pay.bind.tripRig"
};

function payBindingChip(b) {
  var cls = "chip " + (b === "block" || b === "duties"
                       ? "chip-ok" : "chip-warn");
  return el("span", { class: cls, text: t(PAY_BINDING[b] || b) });
}

function renderPay(rep) {
  var body = $("pay-body");
  body.textContent = "";
  var cur = rep.currency || "USD";
  var t0 = rep.totals || {};

  // headline tiles: the industry metric first
  var strip = el("div", { class: "kpi-strip" });
  function tile(value, label, cls) {
    strip.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: value }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  tile(t0.credit_to_block !== null ? String(t0.credit_to_block) : "—",
       t("app.pay.tile.ratio"),
       t0.credit_to_block > 1.1 ? "kpi-warn" : "");
  tile(t0.credit_h + " / " + t0.block_h + " " + t("app.cab.h"),
       t("app.pay.tile.creditBlock"));
  tile(costMoney(t0.soft_cost, cur), t("app.pay.tile.soft"),
       t0.soft_cost > 0 ? "kpi-warn" : "");
  if (rep.roster) {
    tile(costMoney(rep.roster.payroll_cost, cur),
         t("app.pay.tile.payroll"));
    tile(costMoney(rep.roster.guarantee_burn_cost, cur),
         t("app.pay.tile.burn"),
         rep.roster.guarantee_burn_h > 0 ? "kpi-warn" : "");
  }
  body.appendChild(strip);

  // contract line
  var c = rep.config || {};
  body.appendChild(el("p", { class: "muted small", text:
    tf("app.pay.contract", { duty: c.duty_rig, trip: c.trip_rig,
      minduty: c.min_duty_credit_h, guar: c.monthly_guarantee_h,
      rate: c.block_hour_rate }) }));
  if (t0.deadhead_h > 0) {
    body.appendChild(el("p", { class: "muted small", text:
      tf("app.pay.dhNote", { h: t0.deadhead_h }) }));
  }

  // top pairings by soft credit (credit above block)
  body.appendChild(el("h4", { text: t("app.pay.topH") }));
  body.appendChild(demoLikeTable(
    [el("th", { text: "#" }),
     el("th", { text: t("app.pay.th.duties") }),
     el("th", { text: t("app.cost.blockH") }),
     el("th", { text: t("app.pay.th.tafb") }),
     el("th", { text: t("app.pay.th.credit") }),
     el("th", { text: t("app.pay.th.ratio") }),
     el("th", { text: t("app.pay.th.binding") })],
    (rep.top_pairings || []).map(function (r) {
      return el("tr", null, [
        el("td", { class: "mono small", text: String(r.pairing) }),
        el("td", { class: "mono small", text: String(r.n_duties) }),
        el("td", { class: "mono small", text: r.block_h + " " + t("app.cab.h") }),
        el("td", { class: "mono small", text: r.tafb_h + " " + t("app.cab.h") }),
        el("td", { class: "mono small", text: r.credit_h + " " + t("app.cab.h") }),
        el("td", { class: "mono small", text: fmt(r.ratio) }),
        el("td", null, [payBindingChip(r.binding)])
      ]);
    })));

  // roster payroll: top guarantee burners
  if (rep.roster) {
    var ro = rep.roster;
    body.appendChild(el("h4", { text: tf("app.pay.rosterH",
      { n: ro.total_crew }) }));
    if (ro.edited) {
      body.appendChild(el("p", { class: "muted small",
                                 text: t("app.pay.edited") }));
    }
    body.appendChild(demoLikeTable(
      [el("th", { text: t("app.pay.th.crew") }),
       el("th", { text: t("app.pay.th.pairings") }),
       el("th", { text: t("app.pay.th.credit") }),
       el("th", { text: t("app.pay.th.paid") }),
       el("th", { text: t("app.pay.th.burn") })],
      ro.crew.slice(0, 10).map(function (r) {
        return el("tr", null, [
          el("td", { class: "mono small",
                     text: r.employee_id + " (" + fmt(r.base) + ")" }),
          el("td", { class: "mono small", text: String(r.pairings) }),
          el("td", { class: "mono small", text: r.credit_h + " " + t("app.cab.h") }),
          el("td", { class: "mono small", text: r.paid_h + " " + t("app.cab.h") }),
          el("td", { class: "mono small" + (r.guarantee_burn_h > 0 ? " pay-burn" : ""),
                     text: r.guarantee_burn_h + " " + t("app.cab.h") })
        ]);
      })));
    if (ro.crew.length > 10) {
      body.appendChild(el("p", { class: "muted small",
        text: tf("app.pay.crewCap", { shown: 10, total: ro.crew.length }) }));
    }
  } else {
    body.appendChild(el("p", { class: "muted small",
                               text: t("app.pay.noRoster") }));
  }

  // fixed slots so async fills can't reorder the card (audit 2026-07-15)
  body.appendChild(el("div", { id: "fairshare-box" }));
  body.appendChild(el("div", { id: "payreselect-box" }));

  // Phase C / #9: fair-share rebalancing over the current roster
  if (rep.roster) { loadFairShare(rep.instance, cur); }

  // Phase B: duty-proxy vs credit-cost selection (offline artifact)
  loadPayReselect(rep.instance);
}

/* ------------------------- fair-share rebalancer (#9 + pay Phase C) ----- */

function loadFairShare(instId, cur) {
  var seq = _paySeq;
  api("/fairshare/" + encodeURIComponent(instId)).then(function (fp) {
    if (seq !== _paySeq || state.resultsInstance !== instId) { return; }
    if (!fp || fp.available !== true) { return; }
    renderFairShare(instId, fp, cur);
  }).catch(function () { /* optional decoration */ });
}

function renderFairShare(instId, fp, cur) {
  var box = $("fairshare-box");     // fixed slot created by renderPay
  if (!box) { return; }
  box.textContent = "";
  box.appendChild(el("h4", { text: t("app.fs.h") }));
  box.appendChild(el("p", { class: "muted small", text: t("app.fs.p") }));

  function row(label, s) {
    return el("tr", null, [
      el("td", { class: "small", text: label }),
      el("td", { class: "mono small",
                 text: s.payroll_h + " " + t("app.cab.h") }),
      el("td", { class: "mono small",
                 text: s.burn_h + " " + t("app.cab.h") }),
      el("td", { class: "mono small", text: fmt(s.cv) })
    ]);
  }
  box.appendChild(demoLikeTable(
    [el("th", { text: "" }),
     el("th", { text: t("app.pay.tile.payroll") }),
     el("th", { text: t("app.pay.tile.burn") }),
     el("th", { text: t("app.fs.th.cv") })],
    [row(t("app.fs.before"), fp.before),
     row(t("app.fs.after"), fp.after)]));

  var money = costMoney(fp.savings_cost, cur || "USD");
  if (fp.moves.length) {
    box.appendChild(el("p", { class: "banner-ok", text:
      tf("app.fs.proposal", { n: fp.moves.length, h: fp.savings_h,
                              cost: money,
                              left: fp.headroom_left_h }) }));
    var btn = el("button", { class: "btn btn-primary", type: "button",
                             id: "btn-fs-apply",
                             text: t("app.fs.applyBtn") });
    btn.addEventListener("click", function () { fsApply(instId); });
    var note = el("span", { class: "muted small",
                            text: " " + t("app.fs.reversible") });
    var rowEl = el("div", { class: "form-row form-actions" });
    rowEl.appendChild(btn);
    rowEl.appendChild(note);
    box.appendChild(rowEl);
  } else {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.fs.none") }));
  }
}

function fsApply(instId) {
  var btn = $("btn-fs-apply");
  if (btn) { btn.disabled = true; }
  api("/fairshare/" + encodeURIComponent(instId) + "/apply", {
    method: "POST"
  }).then(function (fp) {
    if (state.resultsInstance !== instId) { return; }
    toast(t("app.fs.appliedToast"), "ok");
    // full pay card reload: per-crew table, tiles and the fair-share
    // section all change after the moves land
    loadPay(instId);
  }).catch(function (err) {
    if (btn) { btn.disabled = false; }
    apiErrorToast(err, t("app.fs.fail"));
  });
}

function loadPayReselect(instId) {
  var seq = _paySeq;      // a newer loadPay invalidates this fetch too
  api("/pay-reselect/" + encodeURIComponent(instId)).then(function (rb) {
    if (seq !== _paySeq || state.resultsInstance !== instId) { return; }
    if (!rb || rb.available !== true) { return; }
    var body = $("payreselect-box");  // fixed slot created by renderPay
    if (!body) { return; }
    body.textContent = "";
    body.appendChild(el("h4", { text: t("app.pay.rsel.h") }));
    body.appendChild(el("p", { class: "muted small",
                               text: t("app.pay.rsel.p") }));
    if (rb.config_stale) {
      body.appendChild(el("p", { class: "banner-warn small",
                                 text: t("app.pay.rsel.stale") }));
    }
    function row(label, s) {
      return el("tr", null, [
        el("td", { class: "small", text: label }),
        el("td", { class: "mono small", text: String(s.n_pairings) }),
        el("td", { class: "mono small",
                   text: s.credit_h + " " + t("app.cab.h") }),
        el("td", { class: "mono small",
                   text: s.deadhead_h + " " + t("app.cab.h") }),
        el("td", { class: "mono small", text: fmt(s.ratio) }),
        el("td", { class: "mono small",
                   text: fmt(s.duty_proxy_objective) })
      ]);
    }
    body.appendChild(demoLikeTable(
      [el("th", { text: "" }),
       el("th", { text: t("app.pay.th.pairings") }),
       el("th", { text: t("app.pay.th.credit") }),
       el("th", { text: t("app.pay.rsel.dh") }),
       el("th", { text: t("app.pay.th.ratio") }),
       el("th", { text: t("app.pay.rsel.duty") })],
      [row(t("app.pay.rsel.current"), rb.current),
       row(t("app.pay.rsel.credit"), rb.credit_selected)]));
    var sel = rb.savings_credit_h;
    var own = rb.ownership_savings_h;
    var total = rb.total_vs_deployed_h;
    if (typeof total === "number" && typeof own === "number") {
      // attributed: re-selection under a uniform ownership rule + the
      // pure ownership effect (audit 2026-07-15)
      body.appendChild(el("p", {
        class: total > 0 ? "banner-ok" : "muted small",
        text: tf("app.pay.rsel.savings2",
                 { total: total, sel: sel, own: own })
      }));
    } else {
      body.appendChild(el("p", {
        class: sel > 0 ? "banner-ok" : "muted small",
        text: tf("app.pay.rsel.savings", { h: sel })
      }));
    }
  }).catch(function () { /* Phase B artifact is optional */ });
}

function renderCosting(rep) {
  var body = $("costing-body");
  body.textContent = "";
  var cur = rep.currency || "";
  var t0 = rep.totals || {};

  // headline: total + components
  var kv = el("dl", { class: "kv" });
  function row(label, value) {
    kv.appendChild(el("dt", { text: label }));
    kv.appendChild(el("dd", { text: value }));
  }
  row(t("app.cost.total"), costMoney(t0.total_cost, cur));
  row(t("app.cost.pay"), costMoney(t0.pay_cost, cur)
      + " (" + fmt(t0.block_h) + " " + t("app.cost.blockH") + ")");
  row(t("app.cost.perDiem"), costMoney(t0.per_diem_cost, cur)
      + " (" + fmt(t0.tafb_h) + " " + t("app.cost.tafbH") + ")");
  row(t("app.cost.hotels"), costMoney(t0.hotel_cost, cur)
      + " (" + fmt(t0.nights) + " " + t("app.cost.nights") + ")");
  row(t("app.cost.pairings"), fmt(rep.pairings));
  body.appendChild(kv);

  // hotels by station
  if (Array.isArray(rep.hotel_by_station) && rep.hotel_by_station.length) {
    body.appendChild(el("h4", { text: t("app.cost.byStation") }));
    body.appendChild(demoLikeTable(
      [el("th", { text: t("app.cost.station") }),
       el("th", { class: "num", text: t("app.cost.nights") }),
       el("th", { class: "num", text: t("app.cost.rate") }),
       el("th", { class: "num", text: t("app.cost.sum") })],
      rep.hotel_by_station.slice(0, 12).map(function (s) {
        return el("tr", null, [
          el("td", { class: "mono", text: fmt(s.station) }),
          el("td", { class: "num", text: fmt(s.nights) }),
          el("td", { class: "num", text: costMoney(s.rate, cur) }),
          el("td", { class: "num", text: costMoney(s.cost, cur) })
        ]);
      })));
  }

  // rooms forecast (station x date)
  if (Array.isArray(rep.rooms_forecast) && rep.rooms_forecast.length) {
    body.appendChild(el("h4", { text: t("app.cost.forecast") }));
    body.appendChild(el("p", { class: "muted small",
                               text: t("app.cost.forecastCap") }));
    body.appendChild(demoLikeTable(
      [el("th", { text: t("app.cost.date") }),
       el("th", { class: "num", text: t("app.cost.rooms") }),
       el("th", { text: t("app.cost.breakdown") })],
      rep.rooms_forecast.slice(0, 21).map(function (d) {
        var parts = Object.keys(d.stations).map(function (s) {
          return s + ": " + d.stations[s];
        }).join(", ");
        return el("tr", null, [
          el("td", { class: "mono", text: fmt(d.date) }),
          el("td", { class: "num", text: fmt(d.rooms) }),
          el("td", { class: "small", text: parts })
        ]);
      })));
  }
}

/* -------------------------------------------- cost config (Формат и правила) */

function loadCostConfig() {
  api("/cost-config").then(function (cfg) {
    $("cc-currency").value = cfg.currency || "USD";
    $("cc-block").value = cfg.block_hour_rate;
    $("cc-perdiem").value = cfg.per_diem_hour;
    $("cc-hotel").value = cfg.hotel_default_rate;
    var lines = Object.keys(cfg.hotel_rates || {}).sort().map(function (k) {
      return k + " " + cfg.hotel_rates[k];
    });
    $("cc-hotel-rates").value = lines.join("\n");
    // PUT is admin-only — disable save for regular users, keep values visible
    $("cost-config-save").disabled = !isAdmin();
  }).catch(function () { /* form stays with placeholders */ });
}

function saveCostConfig(evt) {
  evt.preventDefault();
  var rates = {};
  var bad = null;
  $("cc-hotel-rates").value.split("\n").forEach(function (line) {
    var s = line.trim();
    if (!s) { return; }
    var m = s.match(/^([A-Za-z0-9]{2,5})[\s=:]+([\d.]+)$/);
    if (!m) { bad = s; return; }
    rates[m[1].toUpperCase()] = parseFloat(m[2]);
  });
  if (bad) {
    toast(tf("app.cost.badRateLine", { line: bad }), "error");
    return;
  }
  var body = {
    currency: $("cc-currency").value.trim() || "USD",
    block_hour_rate: parseFloat($("cc-block").value) || 0,
    per_diem_hour: parseFloat($("cc-perdiem").value) || 0,
    hotel_default_rate: parseFloat($("cc-hotel").value) || 0,
    hotel_rates: rates
  };
  api("/cost-config", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function () {
    costConfigStatusKey = "app.cost.saved";
    $("cost-config-status").textContent = t(costConfigStatusKey);
    toast(t("app.cost.saved"), "ok");
  }).catch(function (err) {
    // same stale-"saved" fix as the pay form (audit 2026-07-15)
    costConfigStatusKey = null;
    $("cost-config-status").textContent = "";
    apiErrorToast(err, t("app.cost.saveFail"));
  });
}

/** i18n key of the cost-config status line, so a language switch relabels
 *  it instead of leaving stale text (audit 2026-07-15). */
var costConfigStatusKey = null;

/* ------------------------------------- pay-and-credit contract knobs */

function loadPayConfig() {
  api("/pay-config").then(function (cfg) {
    $("pay-duty-rig").value = cfg.duty_rig;
    $("pay-trip-rig").value = cfg.trip_rig;
    $("pay-min-duty").value = cfg.min_duty_credit_h;
    $("pay-guarantee").value = cfg.monthly_guarantee_h;
    $("pay-config-save").disabled = !isAdmin();
  }).catch(function () { /* form stays with placeholders */ });
}

function savePayConfig(evt) {
  evt.preventDefault();
  var body = {
    duty_rig: parseFloat($("pay-duty-rig").value),
    trip_rig: parseFloat($("pay-trip-rig").value),
    min_duty_credit_h: parseFloat($("pay-min-duty").value),
    monthly_guarantee_h: parseFloat($("pay-guarantee").value)
  };
  api("/pay-config", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function () {
    payConfigStatusKey = "app.pay.cfgSaved";
    $("pay-config-status").textContent = t(payConfigStatusKey);
    toast(t("app.pay.cfgSaved"), "ok");
    if (state.resultsInstance) { loadPay(state.resultsInstance); }
  }).catch(function (err) {
    // a failed save must not leave an earlier "saved" next to unsaved
    // values (audit 2026-07-15)
    payConfigStatusKey = null;
    $("pay-config-status").textContent = "";
    apiErrorToast(err, t("app.pay.cfgFail"));
  });
}

/** i18n key of the pay-config status line (lang switch relabels it). */
var payConfigStatusKey = null;

// The save gate depends on the identity, which resolves asynchronously.
document.addEventListener("crew:me", function () {
  // The landing tab's showTab() ran before the identity resolved, so its
  // nav beacon was skipped — send it once now (audit 2026-07-16).
  if (state.me && _lastNavSent === null) { _navBeacon(_nav.tab); }
  var btn = $("cost-config-save");
  if (btn) { btn.disabled = !isAdmin(); }
  var pbtn = $("pay-config-save");
  if (pbtn) { pbtn.disabled = !isAdmin(); }
});

/* ---------------------------------- results: roster/pairing visualization */

var RV_NS = "http://www.w3.org/2000/svg";
var RV_WD_RU = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
var RV_WD_EN = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
var RV_REST_MIN = 600;

function rvWd(dateStr) {
  var w = new Date(dateStr + "T00:00:00Z").getUTCDay();
  return (uiLang() === "en" ? RV_WD_EN : RV_WD_RU)[w];
}
function rvWeekend(dateStr) {
  var w = new Date(dateStr + "T00:00:00Z").getUTCDay();
  return w === 0 || w === 6;
}
function rvDom(dateStr) { return parseInt(dateStr.slice(8, 10), 10); }
function rvGrp(n) {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}
function rvHhmm(min) {
  min = Math.round(min || 0);
  return Math.floor(min / 60) + t("app.viz.hUnit") + String(min % 60).replace(/^(\d)$/, "0$1");
}
function rvSvg(tag, attrs) {
  var e = document.createElementNS(RV_NS, tag);
  if (attrs) { Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); }); }
  return e;
}
function rvMin(a, b) { return (new Date(b) - new Date(a)) / 60000; }
function rvTime(iso) { return iso.slice(11, 16); }
/** Escape values that reach a tooltip via innerHTML — leg ids, airport
 *  codes and crew ids come from uploaded payloads and must never inject. */
function rvEsc(v) {
  return String(v == null ? "" : v).replace(/[&<>"]/g, function (ch) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
  });
}

function rvTip() {
  var tip = $("rv-tip");
  if (!tip) {
    tip = el("div", { id: "rv-tip", role: "tooltip" });
    document.body.appendChild(tip);
  }
  return tip;
}
function rvShowTip(html, x, y) {
  var tip = rvTip();
  tip.innerHTML = html;
  tip.style.opacity = "1";
  var r = tip.getBoundingClientRect();
  var nx = x + 14, ny = y + 14;
  if (nx + r.width > window.innerWidth - 8) { nx = x - r.width - 14; }
  if (ny + r.height > window.innerHeight - 8) { ny = y - r.height - 14; }
  tip.style.left = nx + "px";
  tip.style.top = ny + "px";
}
function rvHideTip() { var t = $("rv-tip"); if (t) { t.style.opacity = "0"; } }

function renderRosterViz(id, hasStage2) {
  var box = $("roster-viz"), empty = $("roster-viz-empty");
  if (!box) { return; }
  if (!hasStage2) {
    box.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  api("/results/" + encodeURIComponent(id) + "/roster").then(function (d) {
    state.rosterViz = { data: d, pair: 0, sort: "base" };
    empty.classList.add("hidden");
    box.classList.remove("hidden");
    rvLegends();
    rvSortControls();
    rvBuildGrid();
    rvDrawDist();
    rvGauges();
    rvPairPicker();
    rvDrawGantt();
    rvCoverage();
  }).catch(function (err) {
    box.classList.add("hidden");
    empty.classList.remove("hidden");
    if (err.status !== 404) { apiErrorToast(err, t("html.results.viz")); }
  });
}

function rvLegend(host, items) {
  host.textContent = "";
  items.forEach(function (it) {
    var s = el("span", { class: "rv-lg" });
    s.appendChild(el("span", { class: "rv-sw " + it.c }));
    s.appendChild(document.createTextNode(it.t));
    host.appendChild(s);
  });
}
function rvLegends() {
  rvLegend($("rv-legend"), [
    { c: "duty", t: t("app.viz.lg.duty") }, { c: "dh", t: t("app.viz.lg.dh") },
    { c: "vac", t: t("app.viz.lg.vac") }, { c: "off", t: t("app.viz.lg.off") }
  ]);
  rvLegend($("rv-glegend"), [
    { c: "duty", t: t("app.viz.lg.flight") }, { c: "dh", t: t("app.viz.lg.dh") }
  ]);
}

function rvSortedCrew() {
  var d = state.rosterViz.data, a = d.crew.slice(), k = state.rosterViz.sort;
  if (k === "block") { a.sort(function (x, y) { return y.block_min - x.block_min; }); }
  else if (k === "pref") { a.sort(function (x, y) { return (y.pref_off_violated - x.pref_off_violated) || (y.block_min - x.block_min); }); }
  else { a.sort(function (x, y) { return (x.base || "").localeCompare(y.base || "") || (y.sen - x.sen); }); }
  return a;
}
function rvSortControls() {
  var host = $("rv-sort");
  host.textContent = "";
  [["base", "app.viz.sort.base"], ["block", "app.viz.sort.block"],
   ["pref", "app.viz.sort.pref"]].forEach(function (pair) {
    var b = el("button", { class: "rv-seg", type: "button", text: t(pair[1]) });
    b.setAttribute("aria-pressed", pair[0] === state.rosterViz.sort);
    b.addEventListener("click", function () {
      state.rosterViz.sort = pair[0];
      rvSortControls();
      rvBuildGrid();
    });
    host.appendChild(b);
  });
}

function rvBuildGrid() {
  var d = state.rosterViz.data, days = d.meta.days;
  var tbl = $("rv-grid");
  tbl.textContent = "";
  var maxBlock = 1;
  d.crew.forEach(function (c) { if (c.block_min > maxBlock) { maxBlock = c.block_min; } });
  var thead = el("thead"), htr = el("tr");
  htr.appendChild(el("th", { class: "rv-corner", text: t("app.viz.crewCol") }));
  days.forEach(function (dt) {
    var th = el("th", { class: "rv-dcol" + (rvWeekend(dt) ? " we" : "") });
    th.appendChild(el("span", { text: String(rvDom(dt)) }));
    th.appendChild(el("span", { class: "rv-wd", text: rvWd(dt) }));
    htr.appendChild(th);
  });
  htr.appendChild(el("th", { class: "rv-railh", text: t("app.viz.blockCol") }));
  thead.appendChild(htr);
  tbl.appendChild(thead);
  var tb = el("tbody");
  var STATE = {
    duty: t("app.viz.state.duty"), deadhead: t("app.viz.state.deadhead"),
    vacation: t("app.viz.state.vacation"), standby: t("app.viz.state.standby"),
    off: t("app.viz.state.off"), off_pref: t("app.viz.state.off_pref")
  };
  rvSortedCrew().forEach(function (c) {
    var tr = el("tr");
    var cc = el("td", { class: "rv-crew" });
    var cx = el("div", { class: "rv-cx" });
    cx.appendChild(el("span", { class: "rv-cid", text: c.id }));
    cx.appendChild(el("span", { class: "rv-badge", text: c.base || "—" }));
    if (c.sen != null) { cx.appendChild(el("span", { class: "rv-sen", text: "с" + c.sen })); }
    cc.appendChild(cx);
    tr.appendChild(cc);
    c.cells.forEach(function (cell) {
      var td = el("td", { class: "rv-cell " + cell.s + (rvWeekend(cell.d) ? " we" : "") });
      td.addEventListener("mouseenter", function (e) {
        var pid = cell.p ? ("<div class='rv-tt'>" + t("app.viz.pairing") + " " + rvEsc(cell.p) + "</div>") : "";
        rvShowTip("<b>" + rvEsc(c.id) + "</b> · " + rvEsc(c.base || "—") +
          "<div class='rv-tt'>" + t("app.viz.day") + " " + rvDom(cell.d) + " (" + rvWd(cell.d) + ")</div>" +
          (STATE[cell.s] || cell.s) + pid, e.clientX, e.clientY);
      });
      td.addEventListener("mousemove", function (e) { rvShowTip(rvTip().innerHTML, e.clientX, e.clientY); });
      td.addEventListener("mouseleave", rvHideTip);
      tr.appendChild(td);
    });
    var rc = el("td", { class: "rv-railcell" });
    var rt = el("div", { class: "rv-railtrack" });
    var bar = el("div", { class: "rv-railbar" });
    bar.style.width = Math.max(4, Math.round(56 * c.block_min / maxBlock)) + "px";
    rt.appendChild(bar);
    rt.appendChild(el("span", { class: "rv-railnum", text: rvHhmm(c.block_min) }));
    rc.appendChild(rt);
    tr.appendChild(rc);
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  var note = $("rv-grid-note");
  note.textContent = d.meta.crew_truncated ? t("app.viz.truncated") : "";
}

function rvDrawDist() {
  var d = state.rosterViz.data, svg = $("rv-dist");
  var W = svg.clientWidth || 520, rowH = 12, padL = 6, padR = 52, padT = 6, padB = 20;
  var vals = d.crew.map(function (c) { return c.block_min; }).sort(function (a, b) { return a - b; });
  var n = vals.length;
  if (!n) { return; }
  var H = padT + padB + n * rowH;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("height", H);
  svg.textContent = "";
  var max = Math.max.apply(null, vals) || 1, plotW = W - padL - padR;
  function X(v) { return padL + plotW * v / max; }
  var med = vals[Math.floor(n / 2)];
  var g = rvSvg("line", { x1: X(med), x2: X(med), y1: padT - 2, y2: H - padB + 2,
    stroke: "var(--accent)", "stroke-dasharray": "3 3", "stroke-width": "1.2", opacity: ".7" });
  svg.appendChild(g);
  vals.forEach(function (v, i) {
    var y = padT + i * rowH;
    var emph = (i === 0 || i === n - 1);
    svg.appendChild(rvSvg("rect", { x: padL, y: y + 2, width: Math.max(2, X(v) - padL),
      height: rowH - 3, rx: 2, fill: emph ? "var(--accent)" : "var(--rv-duty)",
      opacity: emph ? ".95" : ".6" }));
  });
  function lbl(v, txt, anchor) {
    var e = rvSvg("text", { x: X(v), y: H - padB + 14, fill: "var(--muted)",
      "font-size": "10", "text-anchor": anchor });
    e.textContent = txt;
    svg.appendChild(e);
  }
  lbl(vals[0], t("app.viz.min") + " " + rvHhmm(vals[0]), "start");
  lbl(med, t("app.viz.median") + " " + rvHhmm(med), "middle");
  lbl(max, t("app.viz.max") + " " + rvHhmm(max), "end");
}

function rvGauges() {
  var d = state.rosterViz.data, s2 = d.stage2, s1 = d.stage1, host = $("rv-gauges");
  host.textContent = "";
  var prefPct = (s2.pref_attainable ? Math.round(100 * s2.pref_hits / s2.pref_attainable) : 0);
  var covPct = (d.coverage.total_legs
    ? Math.round(100 * (d.coverage.total_legs - (d.coverage.missing || 0)) / d.coverage.total_legs) : 0);
  [{ lab: t("app.viz.prefGauge"), num: (s2.pref_hits != null ? s2.pref_hits + " / " + s2.pref_attainable : "—"), pct: prefPct },
   { lab: t("app.viz.coverGauge"), num: covPct + "%", pct: covPct }
  ].forEach(function (it) {
    var g = el("div", { class: "rv-gauge" });
    var l = el("div", { class: "rv-glabel" });
    l.appendChild(el("span", { text: it.lab }));
    l.appendChild(el("span", { class: "mono", text: it.num }));
    g.appendChild(l);
    var tr = el("div", { class: "rv-gtrack" });
    var f = el("div", { class: "rv-gfill" });
    f.style.width = it.pct + "%";
    f.style.background = "var(--ok)";
    tr.appendChild(f);
    g.appendChild(tr);
    host.appendChild(g);
  });
  var mini = el("div", { class: "rv-mini" });
  [[t("app.viz.mini.vac"), s2.vacation_violations], [t("app.viz.mini.traj"), s2.trajectory_violations],
   [t("app.viz.mini.open"), s2.unassigned], [t("app.viz.mini.over"), s1.overcovered_legs]
  ].forEach(function (pair) {
    var m = el("div", { class: "rv-m" });
    m.appendChild(el("div", { class: "rv-k", text: pair[0] }));
    var v = el("div", { class: "rv-v mono", text: fmt(pair[1]) });
    if (pair[1] === 0) { v.style.color = "var(--ok)"; }
    m.appendChild(v);
    mini.appendChild(m);
  });
  host.appendChild(mini);
}

function rvPairPicker() {
  var d = state.rosterViz.data, host = $("rv-pairpick");
  host.textContent = "";
  d.pairings_sample.forEach(function (p, i) {
    var lab = p.pid + " · " + p.span + t("app.viz.dayShort") + " · " + p.nlegs + t("app.viz.legShort")
      + (p.ndh ? (" · " + p.ndh + t("app.viz.dhShort")) : "");
    var b = el("button", { class: "rv-seg", type: "button", text: lab });
    b.setAttribute("aria-pressed", i === state.rosterViz.pair);
    b.addEventListener("click", function () {
      state.rosterViz.pair = i;
      rvPairPicker();
      rvDrawGantt();
    });
    host.appendChild(b);
  });
}

function rvDrawGantt() {
  var d = state.rosterViz.data;
  var p = d.pairings_sample[state.rosterViz.pair];
  var svg = $("rv-gantt");
  svg.textContent = "";
  if (!p) { return; }
  var W = svg.clientWidth || 720, padL = 8, padR = 8, padT = 40, trackH = 48, padB = 8;
  var legs = p.legs;
  var t0 = new Date(legs[0].dep_utc).getTime();
  var t1 = new Date(legs[legs.length - 1].arr_utc).getTime();
  var span = Math.max(1, t1 - t0), H = padT + trackH + padB, plotW = W - padL - padR;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("height", H);
  function X(ms) { return padL + plotW * (ms - t0) / span; }
  var defs = rvSvg("defs");
  defs.innerHTML = '<pattern id="rvdh" width="5" height="5" patternTransform="rotate(45)" patternUnits="userSpaceOnUse"><line x1="0" y1="0" x2="0" y2="5" stroke="var(--rv-duty)" stroke-width="2"/></pattern>';
  svg.appendChild(defs);
  // day gridlines + night bands (UTC)
  var d0 = new Date(t0); d0.setUTCHours(0, 0, 0, 0);
  for (var day = d0.getTime(); day <= t1; day += 864e5) {
    var nStart = day - 2 * 3600e3, nEnd = day + 6 * 3600e3;
    if (nEnd > t0 && nStart < t1) {
      var xs = X(Math.max(nStart, t0)), xe = X(Math.min(nEnd, t1));
      svg.appendChild(rvSvg("rect", { x: xs, y: padT, width: Math.max(0, xe - xs),
        height: trackH, fill: "var(--ink)", "fill-opacity": ".05" }));
    }
    if (day > t0 && day < t1) {
      svg.appendChild(rvSvg("line", { x1: X(day), x2: X(day), y1: padT - 8, y2: padT + trackH,
        stroke: "var(--border)", "stroke-width": "1" }));
    }
    if (day >= t0 - 864e5 && day <= t1) {
      var dl = rvSvg("text", { x: X(Math.max(day, t0)) + 3, y: padT - 24, fill: "var(--muted)", "font-size": "10" });
      dl.textContent = t("app.viz.dayShort") + " " + new Date(day).getUTCDate();
      svg.appendChild(dl);
    }
  }
  var bo = rvSvg("text", { x: padL, y: padT - 10, fill: "var(--muted)", "font-size": "10.5" });
  bo.textContent = t("app.viz.baseOut") + " " + legs[0].dep;
  svg.appendChild(bo);
  var br = rvSvg("text", { x: W - padR, y: padT - 10, fill: "var(--muted)", "font-size": "10.5", "text-anchor": "end" });
  br.textContent = t("app.viz.baseRet") + " " + legs[legs.length - 1].arr;
  svg.appendChild(br);
  var yMid = padT + trackH / 2;
  for (var i = 0; i < legs.length - 1; i++) {
    var a = legs[i], b = legs[i + 1];
    var gap = rvMin(a.arr_utc, b.dep_utc);
    var xa = X(new Date(a.arr_utc).getTime()), xb = X(new Date(b.dep_utc).getTime());
    if (gap >= RV_REST_MIN) {
      svg.appendChild(rvSvg("line", { x1: xa, x2: xb, y1: yMid, y2: yMid,
        stroke: "var(--ok)", "stroke-width": "1.4", "stroke-dasharray": "4 3" }));
      var rl = rvSvg("text", { x: (xa + xb) / 2, y: yMid - 6, fill: "var(--ok)", "font-size": "9.5", "text-anchor": "middle" });
      rl.textContent = t("app.viz.rest") + " " + rvHhmm(gap) + " @" + a.arr;
      svg.appendChild(rl);
    } else if (xb - xa > 1) {
      svg.appendChild(rvSvg("line", { x1: xa, x2: xb, y1: yMid, y2: yMid,
        stroke: "var(--muted)", "stroke-width": "3", opacity: ".35" }));
    }
  }
  legs.forEach(function (l) {
    var xa = X(new Date(l.dep_utc).getTime());
    var xw = Math.max(3, X(new Date(l.arr_utc).getTime()) - xa);
    var y = padT + trackH / 2 - 9;
    svg.appendChild(rvSvg("rect", { x: xa, y: y, width: xw, height: 18, rx: 3,
      fill: l.dh ? "var(--rv-duty)" : "var(--rv-duty)", "fill-opacity": l.dh ? ".16" : "1",
      stroke: "var(--rv-duty)", "stroke-width": l.dh ? "1.4" : "0" }));
    if (l.dh) {
      svg.appendChild(rvSvg("rect", { x: xa, y: y, width: xw, height: 18, rx: 3, fill: "url(#rvdh)" }));
    }
    var hit = rvSvg("rect", { x: xa, y: y, width: xw, height: 18, fill: "transparent" });
    hit.style.cursor = "default";
    hit.addEventListener("mouseenter", function (e) {
      rvShowTip("<b>" + rvEsc(l.leg) + "</b>" + (l.dh ? " · " + t("app.viz.lg.dh") : "") +
        "<div class='rv-tt'>" + rvEsc(l.dep) + " " + rvTime(l.dep_utc) + " → " + rvEsc(l.arr) + " " + rvTime(l.arr_utc) + "</div>" +
        t("app.viz.block") + " " + rvHhmm(rvMin(l.dep_utc, l.arr_utc)) + " · " + rvEsc(l.ac), e.clientX, e.clientY);
    });
    hit.addEventListener("mousemove", function (e) { rvShowTip(rvTip().innerHTML, e.clientX, e.clientY); });
    hit.addEventListener("mouseleave", rvHideTip);
    svg.appendChild(hit);
  });
  rvDrawSheet(p);
}

function rvDrawSheet(p) {
  var host = $("rv-sheet");
  host.textContent = "";
  var tbl = el("table"), thead = el("thead"), htr = el("tr");
  [t("app.viz.sheet.duty"), t("app.viz.sheet.dep"), "→", t("app.viz.sheet.arr"),
   t("app.viz.sheet.block"), t("app.viz.sheet.ac")].forEach(function (h) {
    htr.appendChild(el("th", { text: h }));
  });
  thead.appendChild(htr);
  tbl.appendChild(thead);
  var tb = el("tbody");
  var duty = 1, prevArr = null, totalBlock = 0, ndh = 0;
  function dutyRow(nn) {
    var tr = el("tr", { class: "rv-dp" });
    var td = el("td", { text: t("app.viz.duty") + " " + nn });
    td.colSpan = 6;
    tr.appendChild(td);
    tb.appendChild(tr);
  }
  dutyRow(duty);
  p.legs.forEach(function (l) {
    if (prevArr != null && rvMin(prevArr, l.dep_utc) >= RV_REST_MIN) { duty++; dutyRow(duty); }
    var tr = el("tr");
    var c1 = el("td");
    c1.innerHTML = (l.dh ? '<span class="rv-tag dh">DH</span> ' : '<span class="rv-tag op">✈</span> ');
    c1.appendChild(document.createTextNode(l.leg));
    tr.appendChild(c1);
    tr.appendChild(el("td", { class: "mono", text: rvTime(l.dep_utc) + " " + l.dep }));
    tr.appendChild(el("td", { text: "→" }));
    tr.appendChild(el("td", { class: "mono", text: rvTime(l.arr_utc) + " " + l.arr }));
    var bl = rvMin(l.dep_utc, l.arr_utc);
    tr.appendChild(el("td", { class: "mono", text: l.dh ? "—" : rvHhmm(bl) }));
    tr.appendChild(el("td", { class: "mono", text: l.ac }));
    tb.appendChild(tr);
    if (l.dh) { ndh++; } else { totalBlock += bl; }
    prevArr = l.arr_utc;
  });
  var tafb = rvMin(p.legs[0].dep_utc, p.legs[p.legs.length - 1].arr_utc);
  var ftr = el("tr", { class: "rv-foot" });
  var f = el("td", { text: t("app.viz.tafb") + " " + rvHhmm(tafb) + " · " + t("app.viz.block") + " "
    + rvHhmm(totalBlock) + " · " + t("app.viz.dhShort2") + " " + ndh });
  f.colSpan = 6;
  ftr.appendChild(f);
  tb.appendChild(ftr);
  tbl.appendChild(tb);
  host.appendChild(tbl);
}

function rvCoverage() {
  var d = state.rosterViz.data, cov = d.coverage;
  var total = cov.total_legs || 0, missing = cov.missing || 0, over = cov.over || 0;
  var covered = total - missing;
  var bar = $("rv-covbar");
  bar.textContent = "";
  function seg(w, col, txt) {
    var s = el("div", { class: "rv-covseg" });
    s.style.width = w + "%";
    s.style.background = col;
    if (w > 8) { s.textContent = txt; }
    bar.appendChild(s);
  }
  if (total) {
    seg(100 * covered / total, "var(--ok)", t("app.viz.closed") + " " + rvGrp(covered));
    if (missing > 0) { seg(100 * missing / total, "var(--rv-bad)", t("app.viz.open") + " " + missing); }
  }
  var cn = $("rv-covnote");
  cn.textContent = "";
  var pct = total ? Math.round(100 * covered / total) : 0;
  [{ n: pct + "%", t: t("app.viz.cov.closed"), d: rvGrp(covered) + " / " + rvGrp(total), col: "var(--ok)" },
   { n: fmt(over), t: t("app.viz.cov.over"), d: t("app.viz.cov.overD"), col: "var(--rv-vac)" },
   { n: fmt(missing), t: t("app.viz.cov.open"), d: t("app.viz.cov.openD"), col: "var(--rv-bad)" }
  ].forEach(function (c) {
    var card = el("div", { class: "rv-covcard" });
    var nn = el("div", { class: "rv-n mono", text: c.n });
    nn.style.color = c.col;
    card.appendChild(nn);
    card.appendChild(el("div", { class: "rv-t", text: c.t }));
    card.appendChild(el("div", { class: "rv-d", text: c.d }));
    cn.appendChild(card);
  });
}

/* --------------------- results: pairing chains — Gantt + network map (pv) */

/* Данные — GET /api/results/{id}/pairing: готовый каталог решения этапа 1,
 * ничего не пересчитывается. Гант: строка — цепочка, горизонталь — время
 * месяца (UTC); смена — подложка, рейс — сплошной сегмент, перегон —
 * штриховка, ночёвка — разрыв; красная полоса непокрытых — сверху. Карта:
 * узлы — города, дуги — рейсы; раскладка силовая с сеяным ГПСЧ (зерно —
 * метка решения), без географии: обезличенно и детерминированно — любая
 * перерисовка (в т.ч. смена языка) не двигает узлы. Клик по строке Ганта
 * подсвечивает маршрут цепочки на карте (общий state.pairingViz.sel). */

var PV_PX_PER_HOUR = 2;   // ширина часа на ленте, px
var PV_ROW_H = 13;        // высота строки цепочки, px

function renderPairingViz(id, hasStage1) {
  var box = $("pairing-viz"), empty = $("pairing-viz-empty");
  if (!box) { return; }
  if (!hasStage1) {
    box.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  api("/results/" + encodeURIComponent(id) + "/pairing").then(function (d) {
    // Защита от медленного ответа за ПРЕЖНИЙ экземпляр (селектор мог уйти).
    if (state.resultsInstance !== id) { return; }
    state.pairingViz = { data: d, sel: null, base: "", nights: false, dh: false };
    empty.classList.add("hidden");
    box.classList.remove("hidden");
    pvLegends();
    pvFilters();
    pvDrawGantt();
    pvDrawMap();
  }).catch(function (err) {
    box.classList.add("hidden");
    empty.classList.remove("hidden");
    if (err.status !== 404) { apiErrorToast(err, t("html.pviz.h")); }
  });
}

function pvLegends() {
  rvLegend($("pv-legend"), [
    { c: "duty", t: t("app.viz.lg.flight") }, { c: "dh", t: t("app.viz.lg.dh") },
    { c: "bad", t: t("app.pviz.lg.unc") }
  ]);
  rvLegend($("pv-map-legend"), [
    { c: "nbase", t: t("app.pviz.lg.base") }, { c: "node", t: t("app.pviz.lg.city") },
    { c: "bad", t: t("app.pviz.lg.unc") }, { c: "sel", t: t("app.pviz.lg.sel") }
  ]);
  // Переключатель подложки (пакет «Карта на гео-подложке»): режим
  // «карта мира» предлагается только при полных координатах; умолчание
  // ВСЕГДА схема — включает человек (см. netDrawMap).
  var st = state.pairingViz, d = st.data || {};
  var host = $("pv-map-legend");
  if (host && d.geo && d.geo.complete) {
    var mk = function (mode, label) {
      var b = el("button", { class: "rv-seg", type: "button",
                             text: label });
      b.setAttribute("aria-pressed",
                     String((st.geoMode || "schema") === mode));
      b.onclick = function () {
        st.geoMode = mode;
        pvLegends();
        pvDrawMap();
      };
      return b;
    };
    host.appendChild(el("span", { class: "pv-mode-gap" }));
    host.appendChild(mk("schema", t("app.pviz.mode.schema")));
    host.appendChild(mk("world", t("app.pviz.mode.world")));
  }
}

/* Фильтры-кнопки: база, «только ночёвочные», «только с перегонами».
 * Смена фильтра снимает выбор цепочки: выбранная строка могла уйти. */
function pvFilters() {
  var st = state.pairingViz, host = $("pv-filters");
  host.textContent = "";
  function seg(label, pressed, fn) {
    var b = el("button", { class: "rv-seg", type: "button", text: label });
    b.setAttribute("aria-pressed", pressed);
    b.addEventListener("click", function () {
      st.sel = null;
      fn();
      pvFilters();
      pvDrawGantt();
      pvDrawMap();
    });
    host.appendChild(b);
  }
  seg(t("app.pviz.f.allBases"), !st.base, function () { st.base = ""; });
  (((st.data || {}).meta || {}).bases || []).forEach(function (b) {
    seg(b, st.base === b, function () { st.base = (st.base === b ? "" : b); });
  });
  seg(t("app.pviz.f.nights"), st.nights, function () { st.nights = !st.nights; });
  seg(t("app.pviz.f.dh"), st.dh, function () { st.dh = !st.dh; });
}

function pvChains() {
  var st = state.pairingViz;
  return ((st.data || {}).chains || []).filter(function (c) {
    if (st.base && c.base !== st.base) { return false; }
    if (st.nights && !(c.nights > 0)) { return false; }
    if (st.dh && !(c.ndh > 0)) { return false; }
    return true;
  });
}

/** Ховер-подсказка: показать при входе, вести за курсором, спрятать. */
function pvHover(node, htmlFn) {
  node.addEventListener("mouseenter", function (e) {
    rvShowTip(htmlFn(), e.clientX, e.clientY);
  });
  node.addEventListener("mousemove", function (e) {
    rvShowTip(rvTip().innerHTML, e.clientX, e.clientY);
  });
  node.addEventListener("mouseleave", rvHideTip);
}

/* Сводка цепочки для ховера: стоимость и состав (маршрут). Все значения
 * приходят из загруженных наборов — обязательно через rvEsc. */
function pvChainTipHtml(c) {
  var route = [];
  (c.legs || []).forEach(function (l, i) {
    if (!i) { route.push(l.dep); }
    route.push(l.arr);
  });
  var rs = route.join("–");
  if (rs.length > 120) { rs = rs.slice(0, 117) + "…"; }
  return "<b>" + rvEsc(c.cid) + "</b> · " + rvEsc(c.base || "—") +
    "<div class='rv-tt'>" + t("app.pviz.tt.cost") + " " + rvGrp(c.cost || 0) + "</div>" +
    (c.nlegs || (c.legs || []).length) + t("app.viz.legShort") +
    " · " + (c.ndh || 0) + t("app.viz.dhShort") +
    " · " + t("app.pviz.tt.nights") + " " + (c.nights || 0) +
    " · " + t("app.pviz.tt.span") + " " + (c.span || 1) + t("app.viz.dayShort") +
    "<div class='rv-tt'>" + t("app.pviz.tt.route") + " " + rvEsc(rs) + "</div>";
}

function pvDrawGantt() {
  var st = state.pairingViz, d = st.data || {};
  var svg = $("pv-gantt"), note = $("pv-gantt-note");
  svg.textContent = "";
  note.textContent = "";
  var chains = pvChains();
  var unc = d.uncovered || [];
  var meta = d.meta || {};
  // границы времени: из meta, иначе — по фактическим рейсам
  var lo = meta.t0 ? new Date(meta.t0).getTime() : NaN;
  var hi = meta.t1 ? new Date(meta.t1).getTime() : NaN;
  if (!isFinite(lo) || !isFinite(hi) || !(hi > lo)) {
    lo = Infinity; hi = -Infinity;
    (d.chains || []).concat([{ legs: unc }]).forEach(function (c) {
      (c.legs || []).forEach(function (l) {
        var a = new Date(l.dep_utc).getTime(), b = new Date(l.arr_utc).getTime();
        if (a < lo) { lo = a; }
        if (b > hi) { hi = b; }
      });
    });
  }
  if (!isFinite(lo) || !(hi > lo)) { return; }
  var padL = 64, padR = 12, y0 = 46;
  var pxMs = PV_PX_PER_HOUR / 3600e3;
  var W = Math.max(640, Math.round(padL + (hi - lo) * pxMs + padR));
  var H = y0 + chains.length * PV_ROW_H + 10;
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  function X(ms) { return padL + (ms - lo) * pxMs; }
  var defs = rvSvg("defs");
  defs.innerHTML = '<pattern id="pvdh" width="5" height="5" patternTransform="rotate(45)" patternUnits="userSpaceOnUse"><line x1="0" y1="0" x2="0" y2="5" stroke="var(--rv-duty)" stroke-width="2"/></pattern>';
  svg.appendChild(defs);
  // сутки (UTC): вертикальные линии + числа месяца сверху
  var d0 = new Date(lo);
  d0.setUTCHours(0, 0, 0, 0);
  for (var day = d0.getTime(); day <= hi; day += 864e5) {
    if (day >= lo) {
      svg.appendChild(rvSvg("line", { x1: X(day), x2: X(day), y1: 14, y2: H - 4,
        stroke: "var(--border)", "stroke-width": "1" }));
    }
    var dl = rvSvg("text", { x: X(Math.max(day, lo)) + 3, y: 11,
      fill: "var(--muted)", "font-size": "9" });
    dl.textContent = new Date(day).getUTCDate();
    svg.appendChild(dl);
  }
  // красная полоса непокрытых — отдельным рядом СВЕРХУ
  var bandY = 18, bandH = 12;
  var bl = rvSvg("text", { x: 2, y: bandY + 10, fill: "var(--rv-bad)",
    "font-size": "9" });
  bl.textContent = t("app.pviz.uncRow");
  svg.appendChild(bl);
  svg.appendChild(rvSvg("rect", { x: padL, y: bandY, width: W - padL - padR,
    height: bandH, fill: "var(--rv-bad)", "fill-opacity": ".08" }));
  unc.forEach(function (l) {
    var xa = X(new Date(l.dep_utc).getTime());
    var xw = Math.max(2, X(new Date(l.arr_utc).getTime()) - xa);
    var r = rvSvg("rect", { x: xa, y: bandY + 1, width: xw, height: bandH - 2,
      rx: 2, fill: "var(--rv-bad)" });
    pvHover(r, function () {
      return "<b>" + rvEsc(l.leg) + "</b> · " + t("app.pviz.tt.unc") +
        "<div class='rv-tt'>" + rvEsc(l.dep) + " " + rvTime(l.dep_utc) + " → " +
        rvEsc(l.arr) + " " + rvTime(l.arr_utc) + "</div>" +
        (l.why === "proven_impossible" ? t("app.pviz.tt.why") : "");
    });
    svg.appendChild(r);
  });
  // строки цепочек
  chains.forEach(function (c, i) {
    var y = y0 + i * PV_ROW_H;
    var selected = st.sel === c.cid;
    var g = rvSvg("g", { class: "pv-row" });
    var lab = rvSvg("text", { x: 2, y: y + PV_ROW_H - 4, "font-size": "9",
      fill: selected ? "var(--accent)" : "var(--muted)" });
    lab.textContent = c.cid;
    g.appendChild(lab);
    // подложка всей строки — ховер со сводкой цепочки (стоимость, состав)
    var rowHit = rvSvg("rect", { x: 0, y: y, width: W, height: PV_ROW_H,
      fill: "transparent" });
    pvHover(rowHit, function () { return pvChainTipHtml(c); });
    g.appendChild(rowHit);
    // смены — светлая подложка от первого вылета до последнего прилёта;
    // ночёвка между сменами остаётся разрывом
    var duties = {};
    (c.legs || []).forEach(function (l) {
      var k = String(l.duty == null ? 0 : l.duty);
      var a = new Date(l.dep_utc).getTime(), b = new Date(l.arr_utc).getTime();
      if (!duties[k]) { duties[k] = { a: a, b: b }; }
      else {
        if (a < duties[k].a) { duties[k].a = a; }
        if (b > duties[k].b) { duties[k].b = b; }
      }
    });
    Object.keys(duties).forEach(function (k) {
      var dd = duties[k];
      g.appendChild(rvSvg("rect", { x: X(dd.a), y: y + 1,
        width: Math.max(2, X(dd.b) - X(dd.a)), height: PV_ROW_H - 2, rx: 2,
        fill: "var(--rv-duty)", "fill-opacity": ".13" }));
    });
    // рейсы: сплошной сегмент; перегон — штриховка
    (c.legs || []).forEach(function (l) {
      var xa = X(new Date(l.dep_utc).getTime());
      var xw = Math.max(2, X(new Date(l.arr_utc).getTime()) - xa);
      var ry = y + 2, rh = PV_ROW_H - 4;
      g.appendChild(rvSvg("rect", { x: xa, y: ry, width: xw, height: rh, rx: 2,
        fill: "var(--rv-duty)", "fill-opacity": l.dh ? ".16" : "1",
        stroke: "var(--rv-duty)", "stroke-width": l.dh ? "1" : "0" }));
      if (l.dh) {
        g.appendChild(rvSvg("rect", { x: xa, y: ry, width: xw, height: rh,
          rx: 2, fill: "url(#pvdh)" }));
      }
      var hit = rvSvg("rect", { x: xa, y: ry, width: xw, height: rh,
        fill: "transparent" });
      pvHover(hit, function () {
        return "<b>" + rvEsc(l.leg) + "</b>" +
          (l.dh ? " · " + t("app.viz.lg.dh") : "") +
          "<div class='rv-tt'>" + rvEsc(l.dep) + " " + rvTime(l.dep_utc) +
          " → " + rvEsc(l.arr) + " " + rvTime(l.arr_utc) + "</div>" +
          t("app.viz.block") + " " + rvHhmm(rvMin(l.dep_utc, l.arr_utc));
      });
      g.appendChild(hit);
    });
    if (selected) {
      g.appendChild(rvSvg("rect", { x: padL - 2, y: y,
        width: W - padL - padR + 4, height: PV_ROW_H, rx: 3, fill: "none",
        stroke: "var(--accent)", "stroke-width": "1.5" }));
    }
    g.addEventListener("click", function () {
      st.sel = (st.sel === c.cid) ? null : c.cid;
      pvDrawGantt();
      pvDrawMap();
    });
    svg.appendChild(g);
  });
  var total = meta.chains_total != null
    ? meta.chains_total : (d.chains || []).length;
  var parts = [tf("app.pviz.shown", { n: chains.length, total: total }),
               tf("app.pviz.uncCount", { n: unc.length })];
  if (meta.chains_truncated) {
    parts.push(tf("app.pviz.truncated", { n: (d.chains || []).length }));
  }
  if (meta.duty_split === "gap600") { parts.push(t("app.pviz.splitHeur")); }
  note.textContent = parts.join(" ");
}

/* ------- карта сети: общий движок вынесен в net-map.js (NetMap) -------
 * Пакет трёх работ 2026-08-30 (карта в CDPM): рисовальщик один на обе
 * панели, зависимости хозяина (тултипы pvHover, переводы t/tf)
 * инжектируются параметрами. Контракт cfg — в шапке net-map.js. */
function netDrawMap(cfg) {
  var full = Object.assign({}, cfg, {
    hover: pvHover,
    labels: {
      arc: t("app.pviz.tt.arc"),
      dhArc: t("app.pviz.tt.dhArc"),
      uncArc: t("app.pviz.tt.uncArc"),
      base: t("app.pviz.lg.base"),
      selNote: function (cid) {
        return tf("app.pviz.selNote", { cid: cid });
      }
    }
  });
  NetMap.draw(full);
}

/* pvDrawMap — тонкая обёртка экрана results над общим рисовальщиком. */
function pvDrawMap() {
  var st = state.pairingViz, d = st.data || {};
  netDrawMap({ svg: $("pv-map"), note: $("pv-map-note"), data: d,
               sel: st.sel, mode: st.geoMode || "schema",
               seed: d.label || d.run_id || "pv",
               redraw: pvDrawMap });
}

/* -------------------------------------- tab: сценарии «что если» (#11) */

/** True if the user has edited any rule field away from the value it was
 *  last built with (stored in data-baseline). */
function scenRulesDirty() {
  return CONFIG_PARAMS.some(function (spec) {
    var input = $("scen-" + spec[0]);
    return input && input.value !== input.dataset.baseline;
  });
}

/** Rules form pre-filled with the CURRENT global tier-1 values, so the user
 *  edits only what the what-if is about. Rebuilt on refresh so edited global
 *  rules are reflected — UNLESS the user has unsaved edits, which are kept
 *  (audit 2026-07-15: the old build-once guard baked in stale globals). */
function renderScenRulesForm(parameters, force) {
  var grid = $("scen-rules-grid");
  if (!grid) { return; }
  if (grid.childElementCount && !force && scenRulesDirty()) { return; }
  grid.textContent = "";
  CONFIG_PARAMS.forEach(function (spec) {
    var key = spec[0], dflt = spec[2];
    var value = (parameters && parameters[key] !== undefined
                 && parameters[key] !== null) ? parameters[key] : dflt;
    // comp_* допускают 0 (= правило выключено) — аудит-2 P2-05.
    var input = el("input", { type: "number", id: "scen-" + key,
                              value: String(value), step: "1",
                              min: key.indexOf("comp_") === 0 ? "0" : "1" });
    input.dataset.baseline = String(value);
    grid.appendChild(el("div", { class: "form-row" }, [
      el("label", { for: "scen-" + key, text: t(spec[1]) }),
      input,
      el("span", { class: "hint mono", text: key })
    ]));
  });
}

/** Reset the whole creation form to pristine globals (after a create). */
function resetScenarioForm() {
  $("scen-name").value = "";
  ["scen-cost-block", "scen-cost-perdiem", "scen-cost-hotel"]
    .forEach(function (id) { var n = $(id); if (n) { n.value = ""; } });
  renderScenRulesForm(state.scenGlobalRules, true);
}

/** Human summary of what the scenario changes vs the global setup. */
function scenChangesSummary(s) {
  var parts = [];
  var base = state.scenGlobalRules || {};
  CONFIG_PARAMS.forEach(function (spec) {
    var key = spec[0];
    var ref = (base[key] !== undefined && base[key] !== null)
      ? base[key] : spec[2];
    if (s.rules && s.rules[key] !== undefined && s.rules[key] !== ref) {
      parts.push(key + ": " + ref + "→" + s.rules[key]);
    }
  });
  Object.keys(s.cost_overrides || {}).forEach(function (key) {
    parts.push(key + " = " + s.cost_overrides[key]);
  });
  return parts.length ? parts.join(", ") : t("app.scen.noChanges");
}

function renderScenariosTable() {
  var tbody = $("scenarios-tbody");
  tbody.textContent = "";
  if (!state.scenarios.length) {
    tbody.appendChild(el("tr", null, [
      el("td", { colspan: "6", class: "empty-row",
                 text: t("app.scen.empty") })
    ]));
    return;
  }
  state.scenarios.forEach(function (s) {
    var status = s.last_gate_status
      ? el("span", { class: "chip " + (s.last_gate_status === "FEASIBLE"
                                       ? "chip-ok" : "chip-warn"),
                     text: s.last_gate_status })
      : el("span", { class: "muted",
                     text: s.has_catalog ? t("app.scen.stage1Done")
                                         : t("app.scen.notRun") });
    var actions = el("td", { class: "actions-cell" });
    var btnRun = el("button", { class: "btn btn-ghost btn-small",
                                text: t("app.scen.run") });
    btnRun.addEventListener("click", function () {
      var go = function () {
        var picker = $("job-instance");
        picker.value = s.id;
        // If the instances list hasn't caught up, the option is absent and
        // the select silently falls back to "" — don't claim success then.
        if (picker.value !== s.id) {
          toast(t("app.scen.runNotReady"), "warn");
          return;
        }
        updateStageButtons();
        showTab("jobs");
        toast(tf("app.scen.runHint", { id: s.id }), "ok");
      };
      if (state.instances.some(function (i) { return i.id === s.id; })) {
        go();
      } else {
        refreshInstances().then(go);
      }
    });
    var btnCmp = el("button", { class: "btn btn-ghost btn-small",
                                text: t("app.scen.compare") });
    btnCmp.addEventListener("click", function () { compareScenario(s.id); });
    var btnDel = el("button", { class: "btn btn-danger btn-small",
                                text: t("app.scen.delete") });
    btnDel.addEventListener("click", function () { deleteScenario(s); });
    actions.appendChild(btnRun);
    actions.appendChild(btnCmp);
    actions.appendChild(btnDel);
    tbody.appendChild(el("tr", null, [
      el("td", null, [
        el("div", { text: fmt(s.name) }),
        el("div", { class: "muted small mono", text: s.id })
      ]),
      el("td", { class: "mono small", text: fmt(s.base_instance) }),
      el("td", { class: "small", text: scenChangesSummary(s) }),
      el("td", null, [status]),
      el("td", { class: "small", text: fmtTime(s.created_at) }),
      actions
    ]));
  });
}

function refreshScenarios() {
  renderInstancePickers(); // keeps the base-instance picker current
  api("/airline-config").then(function (cfg) {
    state.scenGlobalRules = (cfg && cfg.parameters) || null;
    renderScenRulesForm(state.scenGlobalRules);
    renderScenariosTable(); // changes column depends on the global rules
  }).catch(function () { renderScenRulesForm(null); });
  api("/scenarios").then(function (resp) {
    state.scenarios = (resp && resp.scenarios) || [];
    renderScenariosTable();
  }).catch(function (err) { apiErrorToast(err, t("app.scen.loadFail")); });
}

function createScenario(evt) {
  evt.preventDefault();
  var name = $("scen-name").value.trim();
  if (!name) { toast(t("app.scen.nameRequired"), "error"); return; }
  var base = $("scen-base").value;
  if (!base) { toast(t("app.t.pickInstance"), "error"); return; }
  var rules = {};
  var bad = [];
  CONFIG_PARAMS.forEach(function (spec) {
    var input = $("scen-" + spec[0]);
    var v = input ? parseInt(input.value, 10) : NaN;
    // comp_* допускают 0 (= правило выключено) — ревью пакета: старая
    // проверка v <= 0 блокировала создание сценария с дефолтами.
    var minOk = spec[0].indexOf("comp_") === 0 ? (v >= 0) : (v > 0);
    if (!isFinite(v) || !minOk) { bad.push(t(spec[1])); }
    else { rules[spec[0]] = v; }
  });
  if (bad.length) {
    toast(t("app.t.badValues") + bad.join(", "), "error");
    return;
  }
  var overrides = {};
  [["scen-cost-block", "block_hour_rate"],
   ["scen-cost-perdiem", "per_diem_hour"],
   ["scen-cost-hotel", "hotel_default_rate"]].forEach(function (m) {
    var raw = $(m[0]).value;
    if (raw !== "") {
      var v = parseFloat(raw);
      if (isFinite(v) && v >= 0) { overrides[m[1]] = v; }
    }
  });
  $("scenario-create").disabled = true;
  api("/scenarios", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name, base_instance_id: base,
                           rules: rules, cost_overrides: overrides })
  }).then(function (resp) {
    toast(tf("app.scen.created", { id: resp.id }), "ok");
    resetScenarioForm();
    refreshInstances();
    refreshScenarios();
  }).catch(function (err) {
    apiErrorToast(err, t("app.scen.createFail"));
  }).finally(function () { $("scenario-create").disabled = false; });
}

function deleteScenario(s) {
  appConfirm(tf("app.scen.confirmDelete", { name: s.name || s.id }),
             { danger: true }).then(function (yes) {
    if (!yes) { return; }
    api("/scenarios/" + encodeURIComponent(s.id), { method: "DELETE" })
      .then(function () {
        toast(t("app.scen.deleted"), "ok");
        // Close the compare panel if the deleted id is EITHER side of it —
        // scenario-of-scenario means the deleted one can be the base too
        // (audit 2026-07-15: otherwise a nonsense all-changed panel lingers).
        var cmp = state.scenCompare;
        if (cmp && (cmp.scenario.instance === s.id
                    || cmp.base.instance === s.id)) {
          state.scenCompare = null;
          $("scen-compare").classList.add("hidden");
        }
        refreshInstances();
        refreshScenarios();
      }).catch(function (err) {
        apiErrorToast(err, t("app.scen.deleteFail"));
      });
  });
}

function compareScenario(id) {
  api("/scenarios/" + encodeURIComponent(id) + "/compare")
    .then(function (cmp) {
      state.scenCompare = cmp;
      renderScenCompare();
      $("scen-compare").scrollIntoView({ behavior: "smooth",
                                         block: "start" });
    }).catch(function (err) { apiErrorToast(err, t("app.scen.cmpFail")); });
}

/** Side-by-side panel: rules diff, rate diff, and the honest numbers of
 *  both instances (objective, gate, KPI). Missing sides say "not run yet"
 *  instead of pretending. */
function renderScenCompare() {
  var cmp = state.scenCompare;
  if (!cmp) { return; }
  var box = $("scen-compare-body");
  box.textContent = "";
  $("scen-compare").classList.remove("hidden");
  $("scen-compare-title").textContent =
    tf("app.scen.cmpTitle", { name: cmp.name || cmp.scenario.instance });

  var card = el("div", { class: "card" });

  // The base can vanish (a scenario built on another scenario that was
  // deleted) — say so instead of showing every rule as "changed from —".
  if (cmp.base && cmp.base.missing) {
    card.appendChild(el("p", { class: "banner-warn",
                               text: t("app.scen.cmpBaseGone") }));
  }

  // 1. what differs by construction: rules + rates
  card.appendChild(el("h4", { text: t("app.scen.cmpRules") }));
  if (cmp.rules_diff.length || cmp.cost_diff.length) {
    card.appendChild(demoLikeTable(
      [el("th", { text: t("app.scen.cmpParam") }),
       el("th", { class: "num", text: t("app.scen.cmpBase") }),
       el("th", { class: "num", text: t("app.scen.cmpScen") })],
      cmp.rules_diff.concat(cmp.cost_diff).map(function (d) {
        return el("tr", null, [
          el("td", { class: "mono", text: d.param }),
          el("td", { class: "num", text: fmt(d.base) }),
          el("td", { class: "num", text: fmt(d.scenario) })
        ]);
      })));
  } else {
    card.appendChild(el("p", { class: "muted", text: t("app.scen.noChanges") }));
  }

  // 2. what came out: objective / gate / KPI of both sides
  card.appendChild(el("h4", { text: t("app.scen.cmpResults") }));
  var sides = [cmp.base, cmp.scenario];
  var kb = cmp.base.kpi, ks = cmp.scenario.kpi;
  var cur = (ks && ks.currency) || (kb && kb.currency) || "";
  function metricRow(label, get, money) {
    return el("tr", null, [el("td", { text: label })].concat(
      sides.map(function (side) {
        var v = get(side);
        var txt = (v === null || v === undefined) ? "—"
          : (money ? costMoney(v, cur) : String(v));
        return el("td", { class: "num", text: txt });
      })));
  }
  card.appendChild(demoLikeTable(
    [el("th", { text: t("app.scen.cmpMetric") }),
     el("th", { class: "num mono", text: instTitleById(cmp.base.instance) }),
     el("th", { class: "num mono", text: instTitleById(cmp.scenario.instance) })],
    [
      metricRow(t("app.scen.cmpObjective"), function (s) {
        return s.catalog_objective;
      }),
      metricRow(t("app.scen.cmpGate"), function (s) {
        return s.last_gate_status;
      }),
      metricRow(t("app.cost.total"), function (s) {
        return s.kpi ? s.kpi.total_cost : null;
      }, true),
      metricRow(t("app.kpi.unitCost"), function (s) {
        return s.kpi ? s.kpi.unit_cost_block_h : null;
      }, true),
      metricRow(t("app.kpi.productivity"), function (s) {
        return s.kpi && s.kpi.productivity_pct !== null
          ? s.kpi.productivity_pct + "%" : null;
      }),
      metricRow(t("app.kpi.coverage"), function (s) {
        return s.kpi && s.kpi.coverage_pct !== null
          ? s.kpi.coverage_pct + "%" : null;
      })
    ]));
  if (!ks) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.scen.cmpNotRun") }));
  }
  box.appendChild(card);
}

/* ---------------------------------------- tab: торги отпусков (#14) */

function renderVacInstancePicker() {
  var select = $("vac-instance");
  if (!select) { return; }
  var prev = select.value;
  select.textContent = "";
  state.instances.forEach(function (inst) {
    select.appendChild(el("option", {
      value: inst.id, text: instLabel(inst)
    }));
  });
  if (prev && state.instances.some(function (i) { return i.id === prev; })) {
    select.value = prev;
  }
}

function refreshVacation() {
  renderVacInstancePicker();
  var id = $("vac-instance").value;
  if (!id) {
    $("vac-body").classList.add("hidden");
    $("vac-empty").classList.remove("hidden");
    return;
  }
  state.vacInstance = id;
  // Гонка смены набора (внешний аудит R6, P0-09): пока грузится новый
  // набор, старое тело скрыто и его кэш сброшен — кнопки прежнего экрана
  // не могут отправить мутацию уже с новым идентификатором.
  state.vacMeta = null;
  $("vac-body").classList.add("hidden");
  $("vac-body").setAttribute("aria-busy", "true");
  api("/vacation/" + encodeURIComponent(id)).then(function (meta) {
    if (state.vacInstance !== id) { return; }
    meta._loadedId = id;
    state.vacMeta = meta;
    $("vac-body").classList.remove("hidden");
    $("vac-body").removeAttribute("aria-busy");
    $("vac-empty").classList.add("hidden");
    renderVacation();
  }).catch(function (err) {
    if (state.vacInstance !== id) { return; }
    if (err.status === 404) {
      // instance not visible / gone — show the picker prompt
      $("vac-body").classList.add("hidden");
      $("vac-empty").classList.remove("hidden");
    } else { apiErrorToast(err, t("app.vac.loadFail")); }
  });
}

/** Compact "Jan 06–12" style label for a [startISO, endISO] window. */
function vacWindowLabel(w) {
  if (!Array.isArray(w) || w.length !== 2) { return "—"; }
  return fmtDay(w[0]) + " – " + fmtDay(w[1]);
}

function fmtDay(iso) {
  var d = new Date(iso + "T00:00:00");
  if (isNaN(d.getTime())) { return String(iso); }
  return d.toLocaleDateString(localeTag(), { month: "short", day: "numeric" });
}

function renderVacation() {
  var meta = state.vacMeta || {};
  $("vac-quota").value = meta.quota != null ? meta.quota : 1;
  var bids = meta.bids || [];
  $("vac-bids-count").textContent = tf("app.vac.bidsCount", { n: bids.length });

  var tbody = $("vac-bids-tbody");
  tbody.textContent = "";
  if (!bids.length) {
    tbody.appendChild(el("tr", null, [
      el("td", { colspan: "3", class: "empty-row", text: t("html.vac.noBids") })
    ]));
  } else {
    bids.forEach(function (b) {
      var wins = (b.windows || []).map(function (w, i) {
        return (i + 1) + ") " + vacWindowLabel(w);
      }).join("   ");
      tbody.appendChild(el("tr", null, [
        el("td", { class: "mono small", text: fmt(b.employee_id) }),
        el("td", { class: "num", text: fmt(b.seniority) }),
        el("td", { class: "small", text: wins })
      ]));
    });
  }

  // award result (if any)
  if (meta.award) {
    renderVacAward(meta.award);
    $("btn-vac-apply").classList.toggle("hidden",
      !(meta.award.granted && meta.award.granted.length));
  } else {
    $("vac-award").classList.add("hidden");
    $("btn-vac-apply").classList.add("hidden");
  }
}

function renderVacAward(aw) {
  var box = $("vac-award-body");
  box.textContent = "";
  $("vac-award").classList.remove("hidden");

  var kv = el("dl", { class: "kv" });
  function row(k, v) {
    // wrap each pair in .kv-row so the flex two-column styling applies
    kv.appendChild(el("div", { class: "kv-row" }, [
      el("dt", { text: k }), el("dd", { text: v })
    ]));
  }
  row(t("app.vac.aw.status"), fmt(aw.status));
  row(t("app.vac.aw.granted"), (aw.granted || []).length + " / " + fmt(aw.bids));
  row(t("app.vac.aw.denied"), (aw.denied || []).length);
  row(t("app.vac.aw.objective"), fmt(aw.objective));
  row(t("app.vac.aw.quota"), fmt(aw.quota));
  box.appendChild(kv);

  if ((aw.granted || []).length) {
    box.appendChild(el("h4", { text: t("app.vac.aw.grantedH") }));
    box.appendChild(demoLikeTable(
      [el("th", { text: t("html.vac.th.emp") }),
       el("th", { class: "num", text: t("html.vac.th.sen") }),
       el("th", { text: t("app.vac.aw.window") }),
       el("th", { class: "num", text: t("app.vac.aw.choice") }),
       el("th", { class: "num", text: t("app.vac.aw.days") })],
      aw.granted.map(function (g) {
        return el("tr", null, [
          el("td", { class: "mono", text: fmt(g.employee_id) }),
          el("td", { class: "num", text: fmt(g.seniority) }),
          el("td", { text: vacWindowLabel(g.window) }),
          el("td", { class: "num", text: fmt(g.choice_rank) }),
          el("td", { class: "num", text: (g.days || []).length })
        ]);
      })));
  }

  // per-day load bar-ish table (date -> count, capped)
  var load = aw.per_day_load || {};
  var days = Object.keys(load);
  if (days.length) {
    box.appendChild(el("h4", { text: t("app.vac.aw.loadH") }));
    box.appendChild(el("p", { class: "muted small",
      text: tf("app.vac.aw.loadCap", { q: aw.quota }) }));
    box.appendChild(demoLikeTable(
      [el("th", { text: t("app.vac.aw.date") }),
       el("th", { class: "num", text: t("app.vac.aw.offCount") })],
      days.slice(0, 31).map(function (d) {
        var over = load[d] > aw.quota;
        return el("tr", null, [
          el("td", { class: "mono", text: fmtDay(d) }),
          el("td", { class: "num" + (over ? " kpi-warn" : ""),
                     text: String(load[d]) })
        ]);
      })));
  }
}

function saveVacQuota(evt) {
  evt.preventDefault();
  var id = state.vacInstance;
  if (!id) { return; }
  var q = parseInt($("vac-quota").value, 10);
  if (!isFinite(q) || q < 0) { toast(t("app.vac.badQuota"), "error"); return; }
  $("vac-quota-save").disabled = true;
  api("/vacation/" + encodeURIComponent(id) + "/quota", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quota: q })
  }).then(function (meta) {
    if (state.vacInstance !== id) { return; }   // selection moved on
    state.vacMeta = meta;
    renderVacation();
    toast(t("app.vac.quotaSaved"), "ok");
  }).catch(function (err) {
    if (state.vacInstance !== id) { return; }
    apiErrorToast(err, t("app.vac.quotaFail"));
  }).finally(function () { $("vac-quota-save").disabled = false; });
}

function vacDemoBids() {
  var id = state.vacInstance;
  if (!id) { return; }
  api("/vacation/" + encodeURIComponent(id) + "/demo", { method: "POST" })
    .then(function (meta) {
      if (state.vacInstance !== id) { return; }
      state.vacMeta = meta;
      renderVacation();
      toast(t("app.vac.demoDone"), "ok");
    }).catch(function (err) {
      if (state.vacInstance !== id) { return; }
      apiErrorToast(err, t("app.vac.demoFail"));
    });
}

function vacAward() {
  var id = state.vacInstance;
  if (!id) { return; }
  $("btn-vac-award").disabled = true;
  api("/vacation/" + encodeURIComponent(id) + "/award", { method: "POST" })
    .then(function (aw) {
      if (state.vacInstance !== id) { return; }   // award for a stale instance
      if (state.vacMeta) { state.vacMeta.award = aw; }
      renderVacation();
      toast(tf("app.vac.awardDone", { n: (aw.granted || []).length }), "ok");
    }).catch(function (err) {
      if (state.vacInstance !== id) { return; }
      apiErrorToast(err, t("app.vac.awardFail"));
    }).finally(function () { $("btn-vac-award").disabled = false; });
}

function vacApply() {
  var id = state.vacInstance;
  if (!id) { return; }
  $("btn-vac-apply").disabled = true;
  api("/vacation/" + encodeURIComponent(id) + "/apply", { method: "POST" })
    .then(function (resp) {
      toast(tf("app.vac.applied", { id: resp.id }), "ok");
      // Hide the button to prevent an accidental second apply (which would
      // mint another derived instance); it reappears only when the award is
      // recomputed. The backend also replaces the prior derived on re-apply.
      if (state.vacInstance === id) {
        $("btn-vac-apply").classList.add("hidden");
      }
      refreshInstances();
    }).catch(function (err) {
      apiErrorToast(err, t("app.vac.applyFail"));
    }).finally(function () { $("btn-vac-apply").disabled = false; });
}

function vacClear() {
  var id = state.vacInstance;
  if (!id) { return; }
  appConfirm(t("app.vac.confirmClear"), { danger: true }).then(function (yes) {
    if (!yes) { return; }
    api("/vacation/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () {
        toast(t("app.vac.cleared"), "ok");
        refreshInstances();
        refreshVacation();
      }).catch(function (err) { apiErrorToast(err, t("app.vac.clearFail")); });
  });
}

/* ------------------------------------- tab: квалификации / свежесть (#5) */

var QUAL_STATUS = {
  expired: { cls: "chip-bad", key: "app.qual.st.expired" },
  expires_during: { cls: "chip-bad", key: "app.qual.st.during" },
  not_current: { cls: "chip-bad", key: "app.qual.st.notcurrent" },
  expires_soon: { cls: "chip-warn", key: "app.qual.st.soon" },
  ok: { cls: "chip-ok", key: "app.qual.st.ok" }
};

function qualStatusChip(status) {
  var s = QUAL_STATUS[status] || QUAL_STATUS.ok;
  return el("span", { class: "chip " + s.cls, text: t(s.key) });
}

function refreshQuals() {
  renderInstancePickers();
  var id = $("qual-instance").value;
  if (!id) {
    $("qual-body").classList.add("hidden");
    $("qual-empty").classList.remove("hidden");
    return;
  }
  state.qualInstance = id;
  state.qualComp = null;      // drop the prior instance's calendar
  // Гонка смены набора (R6, P0-09): старое тело скрывается на время
  // загрузки — см. refreshVacation.
  $("qual-body").classList.add("hidden");
  $("qual-body").setAttribute("aria-busy", "true");
  api("/qualifications/" + encodeURIComponent(id)).then(function (meta) {
    if (state.qualInstance !== id) { return; }
    $("qual-body").removeAttribute("aria-busy");
    $("qual-warn").value = meta.warn_days != null ? meta.warn_days : 30;
    $("qual-body").classList.remove("hidden");
    $("qual-empty").classList.add("hidden");
    // load the compliance calendar if there are records
    if (meta.records && Object.keys(meta.records).length) {
      loadCompliance(id);
    } else {
      $("qual-summary").classList.add("hidden");
    }
  }).catch(function (err) {
    if (state.qualInstance !== id) { return; }
    if (err.status === 404) {
      $("qual-body").classList.add("hidden");
      $("qual-empty").classList.remove("hidden");
    } else { apiErrorToast(err, t("app.qual.loadFail")); }
  });
}

function loadCompliance(id) {
  api("/qualifications/" + encodeURIComponent(id) + "/compliance")
    .then(function (comp) {
      if (state.qualInstance !== id) { return; }
      state.qualComp = comp;
      renderCompliance();
    }).catch(function (err) {
      if (state.qualInstance !== id) { return; }
      apiErrorToast(err, t("app.qual.compFail"));
    });
}

function renderCompliance() {
  var comp = state.qualComp;
  if (!comp) { return; }
  $("qual-summary").classList.remove("hidden");

  // tiles: red (expired+during+not_current), amber (soon), ok, month
  var c = comp.counts || {};
  var red = (c.expired || 0) + (c.expires_during || 0) + (c.not_current || 0);
  var tiles = $("qual-tiles");
  tiles.textContent = "";
  function tile(value, label, cls) {
    tiles.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: String(value) }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  tile(red, t("app.qual.tile.blocking"), red ? "kpi-warn" : "");
  tile(c.expires_soon || 0, t("app.qual.tile.soon"));
  tile(c.ok || 0, t("app.qual.tile.ok"));
  tile(fmtDay(comp.month.from) + " – " + fmtDay(comp.month.to),
       t("app.qual.tile.month"));

  // alerts table
  var box = $("qual-alerts");
  box.textContent = "";
  var alerts = comp.alerts || [];
  if (!alerts.length) {
    box.appendChild(el("p", { class: "banner-ok", text: t("app.qual.allOk") }));
  } else {
    box.appendChild(el("h4", { text: tf("app.qual.alertsH",
                                        { n: alerts.length }) }));
    if (alerts.length > 60) {
      box.appendChild(el("p", { class: "muted small",
        text: tf("app.qual.alertsCap", { shown: 60, total: alerts.length }) }));
    }
    box.appendChild(demoLikeTable(
      [el("th", { text: t("app.qual.th.emp") }),
       el("th", { text: t("app.qual.th.kind") }),
       el("th", { text: t("app.qual.th.code") }),
       el("th", { text: t("app.qual.th.status") }),
       el("th", { text: t("app.qual.th.detail") })],
      alerts.slice(0, 60).map(function (a) {
        return el("tr", null, [
          el("td", { class: "small", text: fmt(a.name || a.employee_id) }),
          el("td", { class: "small", text: t("app.qual.kind." + a.kind) }),
          el("td", { class: "mono small", text: fmt(a.code) }),
          el("td", null, [qualStatusChip(a.status)]),
          el("td", { class: "mono small", text: fmt(a.detail) })
        ]);
      })));
  }
  $("btn-qual-apply").classList.toggle("hidden",
    !(comp.blocking_employees > 0));
}

function saveQualWarn(evt) {
  evt.preventDefault();
  var id = state.qualInstance;
  if (!id) { return; }
  var w = parseInt($("qual-warn").value, 10);
  if (!isFinite(w) || w < 0 || w > 365) {
    toast(t("app.qual.badWarn"), "error");
    return;
  }
  $("qual-warn-save").disabled = true;
  api("/qualifications/" + encodeURIComponent(id) + "/warn", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ warn_days: w })
  }).then(function () {
    if (state.qualInstance !== id) { return; }
    toast(t("app.qual.warnSaved"), "ok");
    loadCompliance(id);
  }).catch(function (err) {
    if (state.qualInstance !== id) { return; }
    apiErrorToast(err, t("app.qual.warnFail"));
  }).finally(function () { $("qual-warn-save").disabled = false; });
}

/* ---------------------------------- base balance / sizing (Экипаж → Базы) */

var BASE_STATUS = {
  over: "app.bases.st.over", deficit: "app.bases.st.deficit",
  surplus: "app.bases.st.surplus", balanced: "app.bases.st.balanced",
  idle: "app.bases.st.idle", no_crew: "app.bases.st.no_crew"
};

function baseStatusChip(status) {
  var cls = "chip";
  if (status === "over" || status === "no_crew") { cls += " chip-bad"; }
  else if (status === "deficit") { cls += " chip-warn"; }
  else if (status === "surplus" || status === "idle") { cls += " chip-neutral"; }
  else { cls += " chip-ok"; }
  return el("span", { class: cls,
                      text: t(BASE_STATUS[status] || "app.bases.st.balanced") });
}

function baseUtilCell(util) {
  if (util === null || util === undefined) {
    return el("td", { class: "small muted", text: "—" });
  }
  var pct = Math.round(util * 100);
  var cls = "util-fill";
  if (util > 1.0) { cls += " util-over"; }
  else if (util >= 0.9) { cls += " util-high"; }
  var bar = el("div", { class: "util-bar" }, [
    el("div", { class: cls,
                style: "width:" + Math.min(100, pct) + "%" })
  ]);
  return el("td", null, [
    el("div", { class: "util-cell" }, [
      el("span", { class: "mono small", text: pct + "%" }), bar
    ])
  ]);
}

/** Global sizing knobs live outside the per-instance report: load them once
 *  on tab entry so admins can tune them even before any catalog exists
 *  (audit 2026-07-15). Values are NOT re-written on re-render — that would
 *  clobber unsaved edits. */
function loadBaseConfigForm() {
  var form = $("bases-config-form");
  if (!isAdmin()) { form.classList.add("hidden"); return; }
  api("/base-config").then(function (cfg) {
    $("bases-cap").value = cfg.cap_hours;
    $("bases-target").value = cfg.target_util;
    form.classList.remove("hidden");
  }).catch(function () { /* form stays hidden */ });
}

function refreshBases() {
  renderInstancePickers();
  loadBaseConfigForm();
  var id = $("bases-instance").value;
  if (!id) {
    $("bases-body").classList.add("hidden");
    $("bases-empty").classList.remove("hidden");
    return;
  }
  state.basesInstance = id;
  api("/base-balance/" + encodeURIComponent(id)).then(function (rep) {
    if (state.basesInstance !== id) { return; }     // stale response guard
    if (!rep.available) {
      state.bases = null;
      $("bases-body").classList.add("hidden");
      $("bases-empty").classList.remove("hidden");
      $("bases-empty").textContent = t("app.bases.noCatalog");
      return;
    }
    state.bases = rep;
    $("bases-body").classList.remove("hidden");
    $("bases-empty").classList.add("hidden");
    renderBases();
  }).catch(function (err) {
    if (state.basesInstance !== id) { return; }
    if (err.status === 404) {
      state.bases = null;
      $("bases-body").classList.add("hidden");
      $("bases-empty").classList.remove("hidden");
      $("bases-empty").textContent = t("html.bases.empty");
    } else { apiErrorToast(err, t("app.bases.loadFail")); }
  });
}

function renderBases() {
  var rep = state.bases;
  if (!rep) { return; }
  var rec = rep.recommendations || {};

  // ---- tiles
  var tiles = $("bases-tiles");
  tiles.textContent = "";
  function tile(value, label, cls) {
    tiles.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: String(value) }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  var gap = rec.total_required - rec.total_line;
  // score is null with <2 worked bases (no dispersion to measure)
  var score = rec.balance_score;
  tile(score === null || score === undefined ? "—" : score + "/100",
       t("app.bases.tile.score"),
       score !== null && score !== undefined && score < 75 ? "kpi-warn" : "");
  tile(rec.understaffed ? "+" + gap : "0",
       t("app.bases.tile.gap"), rec.understaffed ? "kpi-warn" : "");
  tile(rec.total_line + " / " + rec.total_required,
       t("app.bases.tile.crew"));
  tile((rec.transfers || []).length, t("app.bases.tile.moves"));

  // ---- recommendations (transfers + flags)
  var reco = $("bases-reco");
  reco.textContent = "";
  var transfers = rec.transfers || [];
  if (transfers.length) {
    reco.appendChild(el("h4", { text: t("app.bases.reco.h") }));
    var ul = el("ul", { class: "bases-reco-list" });
    transfers.forEach(function (tr) {
      var txt = tf("app.bases.reco.move",
                   { n: tr.crew, from: tr.from, to: tr.to, fleet: tr.fleet || "—" });
      var li = el("li", null, [document.createTextNode(txt)]);
      if (tr.requires_cross_training) {
        li.appendChild(document.createTextNode(" "));
        li.appendChild(el("span", { class: "chip chip-warn small",
                                    text: t("app.bases.reco.xtrain") }));
      }
      ul.appendChild(li);
    });
    reco.appendChild(ul);
  } else {
    reco.appendChild(el("p", { class: "banner-ok",
                               text: t("app.bases.reco.none") }));
  }
  (rec.flags || []).forEach(function (f) {
    var msg;
    if (f.kind === "non_base") {
      msg = tf("app.bases.flag.non_base",
               { n: f.pairings, h: f.block_h });
    } else {
      msg = tf("app.bases.flag." + f.kind, { base: f.base });
    }
    reco.appendChild(el("p", {
      class: "banner-warn small",
      text: msg
    }));
  });

  // ---- per-base table
  var head = $("bases-head");
  head.textContent = "";
  [t("app.bases.th.base"), t("app.bases.th.crew"),
   t("app.bases.th.demand"), t("app.bases.th.capacity"),
   t("app.bases.th.util"), t("app.bases.th.required"),
   t("app.bases.th.surplus"), t("app.bases.th.status")].forEach(function (h) {
    head.appendChild(el("th", { text: h }));
  });
  var tb = $("bases-tbody");
  tb.textContent = "";
  (rep.bases || []).forEach(function (r) {
    var crew = r.line + (r.reserve ? " + " + r.reserve + "R" : "");
    var surplus = r.surplus > 0 ? "+" + r.surplus : String(r.surplus);
    var row = el("tr", null, [
      el("td", { class: "mono", text: fmt(r.base) }),
      el("td", { class: "small", text: crew }),
      el("td", { class: "mono small", text: r.demand_block_h + " h" }),
      el("td", { class: "mono small", text: r.capacity_h + " h" }),
      baseUtilCell(r.util),
      el("td", { class: "mono small", text: String(r.required) }),
      el("td", { class: "mono small", text: surplus }),
      el("td", null, [baseStatusChip(r.status)])
    ]);
    tb.appendChild(row);
  });

  // ---- fleet summary
  var fleet = $("bases-fleet");
  fleet.textContent = "";
  fleet.appendChild(demoLikeTable(
    [el("th", { text: t("app.bases.th.fleet") }),
     el("th", { text: t("app.bases.th.fpairings") }),
     el("th", { text: t("app.bases.th.qual") }),
     el("th", { text: t("app.bases.th.fbases") })],
    (rep.fleet_summary || []).map(function (f) {
      return el("tr", null, [
        el("td", { class: "mono", text: fmt(f.fleet) }),
        el("td", { class: "mono small", text: String(f.pairings) }),
        el("td", { class: "mono small", text: String(f.qualified_crew) }),
        el("td", { class: "small", text: (f.bases || []).join(", ") })
      ]);
    })));

  // ---- inter-base links (top rows)
  var inter = $("bases-inter");
  inter.textContent = "";
  var links = rep.inter_base || [];
  if (!links.length) {
    inter.appendChild(el("p", { class: "muted small",
                               text: t("app.bases.noInter") }));
  } else {
    inter.appendChild(demoLikeTable(
      [el("th", { text: t("app.bases.th.from") }),
       el("th", { text: t("app.bases.th.to") }),
       el("th", { text: t("app.bases.th.ipairings") }),
       el("th", { text: t("app.bases.th.block") })],
      links.slice(0, 20).map(function (l) {
        return el("tr", null, [
          el("td", { class: "mono", text: fmt(l.from) }),
          el("td", { class: "mono", text: fmt(l.to) }),
          el("td", { class: "mono small", text: String(l.pairings) }),
          el("td", { class: "mono small", text: l.block_h + " h" })
        ]);
      })));
  }

  // (the admin sizing form is filled/gated by loadBaseConfigForm on tab
  //  entry — NOT here, so re-renders never clobber unsaved edits)
}

/** i18n key of the base-config status line (lang switch relabels it). */
var baseConfigStatusKey = null;

function saveBaseConfig(evt) {
  evt.preventDefault();
  var cap = parseFloat($("bases-cap").value);
  var target = parseFloat($("bases-target").value);
  if (!isFinite(cap) || cap < 1 || cap > 400) {
    toast(t("app.bases.badCap"), "error"); return;
  }
  if (!isFinite(target) || target < 0.1 || target > 1.0) {
    toast(t("app.bases.badTarget"), "error"); return;
  }
  $("bases-config-save").disabled = true;
  api("/base-config", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cap_hours: cap, target_util: target })
  }).then(function () {
    baseConfigStatusKey = "app.bases.cfgSaved";
    $("bases-config-status").textContent = t(baseConfigStatusKey);
    toast(t("app.bases.cfgSaved"), "ok");
    if (state.basesInstance) { refreshBases(); }   // recompute with new knobs
  }).catch(function (err) {
    apiErrorToast(err, t("app.bases.cfgFail"));
  }).finally(function () { $("bases-config-save").disabled = false; });
}

/* ------------------------------------- crew self-service cabinet (Кабинет) */

function cabDT(iso) {
  if (!iso) { return "—"; }
  var d = new Date(iso + (iso.indexOf("Z") < 0 ? "Z" : ""));
  if (isNaN(d.getTime())) { return String(iso); }
  return d.toLocaleString(localeTag(), {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    timeZone: "UTC"
  });
}

function refreshCabinet() {
  api("/cabinet").then(function (res) {
    state.cabLinks = res.links || [];
    var sel = $("cab-instance");
    sel.textContent = "";
    state.cabLinks.forEach(function (l) {
      sel.appendChild(el("option", { value: l.instance,
        text: l.instance + " — " + l.employee_id }));
    });
    sel.classList.toggle("hidden", state.cabLinks.length <= 1);
    // an explicit planner preview (set from the links card) wins
    if (state.cabPreviewEmp && state.cabInstance) {
      loadCabinet(state.cabInstance, state.cabPreviewEmp);
    } else if (state.cabLinks.length) {
      var want = state.cabInstance;
      var link = state.cabLinks.find(function (l) {
        return l.instance === want;
      }) || state.cabLinks[0];
      sel.value = link.instance;
      loadCabinet(link.instance, null);
    } else {
      state.cabinet = null;
      $("cab-body").classList.add("hidden");
      $("cab-empty").classList.remove("hidden");
    }
  }).catch(function (err) {
    apiErrorToast(err, t("app.cab.loadFail"));
    // never leave the panel blank: fall back to the empty state unless a
    // previously rendered cabinet is still on screen (audit 2026-07-15)
    if (!state.cabinet) {
      $("cab-body").classList.add("hidden");
      $("cab-empty").classList.remove("hidden");
    }
  });
  refreshCabLinksCard();
}

/** The in-flight token covers BOTH dimensions (instance, employee
 *  override): guarding on the instance alone let a slow preview response
 *  land after the planner switched back to their own cabinet, after which
 *  every action silently targeted the wrong employee (audit 2026-07-15). */
function _cabToken(id, emp) { return id + "|" + (emp || ""); }

function loadCabinet(id, empOverride) {
  state.cabInstance = id;
  state.cabPreviewEmp = empOverride || null;
  var token = _cabToken(id, empOverride);
  state.cabToken = token;
  var q = empOverride ? "?employee=" + encodeURIComponent(empOverride) : "";
  api("/cabinet/" + encodeURIComponent(id) + q).then(function (cab) {
    if (state.cabToken !== token) { return; }       // stale response guard
    // remember what this cabinet WAS LOADED AS — every action (submit,
    // withdraw, ack) targets the displayed cabinet, never live state
    cab._loadedEmp = empOverride || null;
    state.cabinet = cab;
    state.cabWindows = (cab.vacation.windows || []).map(function (w) {
      return [w[0], w[1]];
    });
    // keep the my-links select in sync (hidden while previewing: it lists
    // MY links, and the previewed instance may not be among them)
    var sel = $("cab-instance");
    if (!empOverride &&
        state.cabLinks.some(function (l) { return l.instance === id; })) {
      sel.value = id;
    }
    sel.classList.toggle("hidden",
      state.cabLinks.length <= 1 || !!empOverride);
    $("cab-body").classList.remove("hidden");
    $("cab-empty").classList.add("hidden");
    renderCabinet();
  }).catch(function (err) {
    if (state.cabToken !== token) { return; }
    state.cabinet = null;
    $("cab-body").classList.add("hidden");
    $("cab-empty").classList.remove("hidden");
    if (err.status !== 404) { apiErrorToast(err, t("app.cab.loadFail")); }
  });
}

function renderCabinet() {
  var cab = state.cabinet;
  if (!cab) { return; }

  // ---- identity + acknowledgement
  var box = $("cab-identity");
  box.textContent = "";
  var head = el("div", { class: "cab-id-head" }, [
    el("div", null, [
      el("div", { class: "cab-name", text: cab.me.name }),
      el("div", { class: "muted small", text:
        cab.me.employee_id + " · " + t("app.cab.role." +
          (cab.me.role === "Captain" ? "captain" :
           cab.me.role === "FirstOfficer" ? "fo" : "other")) +
        " · " + tf("app.cab.baseSen",
                   { base: fmt(cab.me.base), sen: fmt(cab.me.seniority) }) +
        " · " + (cab.me.type_ratings || []).join(", ") })
    ])
  ]);
  if (cab._loadedEmp) {          // what's DISPLAYED, not live click state
    head.appendChild(el("span", { class: "chip chip-neutral",
                                  text: t("app.cab.preview") }));
  }
  box.appendChild(head);
  var ackRow = el("div", { class: "form-row form-actions" });
  if (cab.roster_available) {
    var ack = cab.ack || {};
    if (ack.acknowledged && !ack.stale) {
      ackRow.appendChild(el("span", { class: "chip chip-ok",
        text: tf("app.cab.ackDone", { at: fmtTime(ack.at) }) }));
    } else {
      if (ack.stale) {
        ackRow.appendChild(el("span", { class: "chip chip-warn",
          text: t("app.cab.ackStale") }));
      }
      var btn = el("button", { class: "btn btn-primary", type: "button",
                               id: "btn-cab-ack",
                               text: t("app.cab.ackBtn") });
      btn.addEventListener("click", cabAckNow);
      ackRow.appendChild(btn);
    }
  } else {
    ackRow.appendChild(el("span", { class: "muted small",
                                    text: t("app.cab.noRoster") }));
  }
  box.appendChild(ackRow);

  // ---- tiles
  var tiles = $("cab-tiles");
  tiles.textContent = "";
  function tile(value, label, cls) {
    tiles.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: String(value) }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  var s = cab.schedule;
  if (s) {
    tile(s.n_pairings, t("app.cab.tile.pairings"));
    tile(s.block_hours + " " + t("app.cab.h"), t("app.cab.tile.block"));
    tile(s.duty_days, t("app.cab.tile.days"));
  }

  // ---- schedule cards
  var sched = $("cab-schedule");
  sched.textContent = "";
  if (!cab.roster_available) {
    sched.appendChild(el("p", { class: "muted",
                                text: t("app.cab.noRoster") }));
  } else if (!s.pairings.length) {
    sched.appendChild(el("p", { class: "banner-ok",
                                text: t("app.cab.freeMonth") }));
  } else {
    s.pairings.forEach(function (p) {
      var route = [];
      p.legs.forEach(function (leg, i) {
        if (i === 0) { route.push(leg.dep); }
        route.push(leg.arr);
      });
      var card = el("div", { class: "card cab-card cab-pairing" }, [
        el("div", { class: "cab-pairing-head" }, [
          el("span", { class: "mono cab-pid", text: p.pid }),
          el("span", { text: cabDT(p.dep_utc) + " → " + cabDT(p.arr_utc) }),
          el("span", { class: "muted small", text:
            tf("app.cab.legsAc", { n: p.nlegs,
                                   ac: (p.aircraft || []).join("/") }) })
        ]),
        el("div", { class: "muted small cab-route",
                    text: route.join(" → ") })
      ]);
      var tbl = demoLikeTable(
        [el("th", { text: t("app.cab.th.flight") }),
         el("th", { text: t("app.cab.th.from") }),
         el("th", { text: t("app.cab.th.dep") }),
         el("th", { text: t("app.cab.th.to") }),
         el("th", { text: t("app.cab.th.arr") })],
        p.legs.map(function (leg) {
          return el("tr", null, [
            el("td", { class: "mono small", text: leg.fn }),
            el("td", { class: "mono small", text: leg.dep }),
            el("td", { class: "small", text: cabDT(leg.dep_utc) }),
            el("td", { class: "mono small", text: leg.arr }),
            el("td", { class: "small", text: cabDT(leg.arr_utc) })
          ]);
        }));
      card.appendChild(tbl);
      sched.appendChild(card);
    });
    sched.appendChild(el("p", { class: "muted small",
                                text: t("app.cab.utcNote") }));
  }

  // ---- my clearances
  var qbox = $("cab-quals");
  qbox.textContent = "";
  var qs = cab.quals || {};
  if (!qs.records || !qs.records.length) {
    qbox.appendChild(el("p", { class: "muted",
                               text: t("app.cab.noQuals") }));
  } else {
    if (qs.first_off) {
      qbox.appendChild(el("p", { class: "banner-warn",
        text: tf("app.cab.grounded", { day: qs.first_off }) }));
    }
    qbox.appendChild(demoLikeTable(
      [el("th", { text: t("app.qual.th.kind") }),
       el("th", { text: t("app.qual.th.code") }),
       el("th", { text: t("app.qual.th.status") }),
       el("th", { text: t("app.qual.th.detail") })],
      qs.records.map(function (r) {
        return el("tr", null, [
          el("td", { class: "small", text: t("app.qual.kind." + r.kind) }),
          el("td", { class: "mono small", text: fmt(r.code) }),
          el("td", null, [qualStatusChip(r.status)]),
          el("td", { class: "mono small", text: fmt(r.detail) })
        ]);
      })));
  }

  // ---- vacation
  var v = cab.vacation || {};
  $("cab-vac-month").textContent = tf("app.cab.vacMonth", {
    from: v.month ? v.month.from : "—", to: v.month ? v.month.to : "—",
    quota: v.quota, maxw: v.max_windows, maxd: v.max_window_days });
  if (v.month) {                 // steer the pickers to the planning month
    $("cab-vac-start").min = v.month.from;
    $("cab-vac-start").max = v.month.to;
    $("cab-vac-end").min = v.month.from;
    $("cab-vac-end").max = v.month.to;
  }
  renderCabVacWindows();
  var awardBox = $("cab-vac-award");
  awardBox.textContent = "";
  if (v.award) {
    if (v.award.granted) {
      awardBox.appendChild(el("p", { class: "banner-ok",
        text: tf("app.cab.awardYes", {
          from: v.award.window[0], to: v.award.window[1],
          rank: v.award.choice_rank }) }));
    } else {
      awardBox.appendChild(el("p", { class: "banner-warn",
        text: t("app.cab.awardNo") }));
    }
  }
}

function renderCabVacWindows() {
  var box = $("cab-vac-windows");
  box.textContent = "";
  if (!state.cabWindows.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.cab.noWindows") }));
    return;
  }
  var ul = el("ul", { class: "cab-vac-list" });
  state.cabWindows.forEach(function (w, i) {
    var li = el("li", null, [
      el("span", { class: "mono", text: w[0] + " → " + w[1] }),
      el("span", { class: "muted small",
                   text: " (" + tf("app.cab.rank", { n: i + 1 }) + ") " })
    ]);
    var del = el("button", { class: "btn btn-ghost btn-mini", type: "button",
                             text: "✕", title: t("app.sug.delete"),
                             "aria-label": t("app.sug.delete") });
    del.addEventListener("click", function () {
      state.cabWindows.splice(i, 1);
      renderCabVacWindows();
    });
    li.appendChild(del);
    ul.appendChild(li);
  });
  box.appendChild(ul);
}

function cabVacAdd() {
  var a = $("cab-vac-start").value, b = $("cab-vac-end").value;
  if (!a || !b) { toast(t("app.cab.vacNeedDates"), "error"); return; }
  if (b < a) { toast(t("app.cab.vacBadOrder"), "error"); return; }
  var max = (state.cabinet && state.cabinet.vacation.max_windows) || 3;
  if (state.cabWindows.length >= max) {
    toast(tf("app.cab.vacTooMany", { max: max }), "error"); return;
  }
  state.cabWindows.push([a, b]);
  renderCabVacWindows();
}

/** Target of every cabinet action = the cabinet ON SCREEN: instance and
 *  employee-override are read off the loaded object, never off live
 *  preview state (which a pending click may already have changed). */
function _cabTarget() {
  var cab = state.cabinet;
  if (!cab) { return null; }
  return { id: cab.instance, emp: cab._loadedEmp || null,
           q: cab._loadedEmp
             ? "?employee=" + encodeURIComponent(cab._loadedEmp) : "" };
}

function cabVacSubmit() {
  var tgt = _cabTarget();
  if (!tgt) { return; }
  if (!state.cabWindows.length) {
    toast(t("app.cab.vacNeedWindow"), "error"); return;
  }
  var token = _cabToken(tgt.id, tgt.emp);
  $("btn-cab-vac-submit").disabled = true;
  api("/cabinet/" + encodeURIComponent(tgt.id) + "/vacation" + tgt.q, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ windows: state.cabWindows })
  }).then(function () {
    if (state.cabToken !== token) { return; }
    toast(t("app.cab.vacSaved"), "ok");
    cabVacStatusKey = "app.cab.vacSaved";
    $("cab-vac-status").textContent = t(cabVacStatusKey);
    loadCabinet(tgt.id, tgt.emp);
  }).catch(function (err) {
    if (state.cabToken !== token) { return; }
    apiErrorToast(err, t("app.cab.vacFail"));
  }).finally(function () { $("btn-cab-vac-submit").disabled = false; });
}

function cabVacWithdraw() {
  var tgt = _cabTarget();
  if (!tgt) { return; }
  var token = _cabToken(tgt.id, tgt.emp);
  api("/cabinet/" + encodeURIComponent(tgt.id) + "/vacation" + tgt.q, {
    method: "DELETE"
  }).then(function () {
    if (state.cabToken !== token) { return; }
    toast(t("app.cab.vacWithdrawn"), "ok");
    loadCabinet(tgt.id, tgt.emp);
  }).catch(function (err) {
    if (state.cabToken !== token) { return; }
    if (err.status === 404) { toast(t("app.cab.vacNothing"), "error"); }
    else { apiErrorToast(err, t("app.cab.vacFail")); }
  });
}

function cabAckNow() {
  var cab = state.cabinet;
  var tgt = _cabTarget();
  if (!cab || !cab.roster_sig || !tgt) { return; }
  var token = _cabToken(tgt.id, tgt.emp);
  api("/cabinet/" + encodeURIComponent(tgt.id) + "/ack" + tgt.q, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sig: cab.roster_sig })
  }).then(function () {
    if (state.cabToken !== token) { return; }
    toast(t("app.cab.ackSaved"), "ok");
    loadCabinet(tgt.id, tgt.emp);
  }).catch(function (err) {
    if (state.cabToken !== token) { return; }
    apiErrorToast(err, t("app.cab.ackFail"));
  });
}

/** i18n key of the vacation status line (a language switch relabels it). */
var cabVacStatusKey = null;

/* --- planner side: crew link management (admins / instance owners) ------ */

function refreshCabLinksCard() {
  var card = $("cab-links-card");
  // owners see only their instances in state.instances; users with none
  // have nothing to manage
  if (!state.instances.length) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  var sel = $("cablink-instance");
  var prev = sel.value;
  sel.textContent = "";
  state.instances.forEach(function (inst) {
    sel.appendChild(el("option", { value: inst.id,
      text: instLabel(inst) }));
  });
  if (prev && state.instances.some(function (i) { return i.id === prev; })) {
    sel.value = prev;
  }
  loadCabLinks();
}

function loadCabLinks() {
  var id = $("cablink-instance").value;
  if (!id) { return; }
  state.cabLinksInstance = id;
  api("/crew-links/" + encodeURIComponent(id)).then(function (res) {
    if (state.cabLinksInstance !== id) { return; }
    var esel = $("cablink-emp");
    esel.textContent = "";
    (res.employees || []).forEach(function (e) {
      esel.appendChild(el("option", { value: e.id,
        text: e.id + " (" + (e.base || "—") + ")" }));
    });
    var box = $("cablink-table");
    box.textContent = "";
    var entries = Object.keys(res.links || {}).sort();
    if (!entries.length) {
      box.appendChild(el("p", { class: "muted small",
                                text: t("app.cab.links.none") }));
      return;
    }
    box.appendChild(demoLikeTable(
      [el("th", { text: t("app.cab.links.email") }),
       el("th", { text: t("app.cab.links.emp") }),
       el("th", { text: "" })],
      entries.map(function (email) {
        var emp = res.links[email];
        var actions = el("td", { class: "small" });
        var prev = el("button", { class: "btn btn-ghost btn-mini",
                                  type: "button",
                                  text: t("app.cab.links.preview") });
        prev.addEventListener("click", function () {
          state.cabInstance = id;
          state.cabPreviewEmp = emp;
          loadCabinet(id, emp);
        });
        var del = el("button", { class: "btn btn-ghost btn-mini",
                                 type: "button", text: "✕",
                                 title: t("app.sug.delete"),
                                 "aria-label": t("app.sug.delete") });
        del.addEventListener("click", function () { cablinkDelete(email); });
        actions.appendChild(prev);
        actions.appendChild(del);
        return el("tr", null, [
          el("td", { class: "mono small", text: email }),
          el("td", { class: "mono small", text: emp }),
          actions
        ]);
      })));
  }).catch(function (err) {
    if (state.cabLinksInstance !== id) { return; }
    if (err.status !== 404) {
      apiErrorToast(err, t("app.cab.links.loadFail"));
    }
  });
}

function cablinkAdd() {
  var id = $("cablink-instance").value;
  var email = $("cablink-email").value.trim();
  var emp = $("cablink-emp").value;
  if (!id || !email || !emp) {
    toast(t("app.cab.links.needAll"), "error"); return;
  }
  api("/crew-links/" + encodeURIComponent(id), {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email, employee_id: emp })
  }).then(function () {
    toast(t("app.cab.links.added"), "ok");
    $("cablink-email").value = "";
    loadCabLinks();
  }).catch(function (err) {
    apiErrorToast(err, t("app.cab.links.addFail"));
  });
}

function cablinkDelete(email) {
  var id = $("cablink-instance").value;
  api("/crew-links/" + encodeURIComponent(id), {
    method: "DELETE", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email })
  }).then(function () {
    toast(t("app.cab.links.removed"), "ok");
    loadCabLinks();
  }).catch(function (err) {
    apiErrorToast(err, t("app.cab.links.delFail"));
  });
}

/* ------------------------------------------- help center: Ask AI (Помощь) */

function refreshAsk() {
  renderAskInstances();
  api("/ask/backends").then(function (b) {
    state.askBackends = b;
    var txt =
      t("app.ask.avail") + " " +
      (b.local ? t("app.ask.availLocal") : "") +
      (b.local && b.cloud ? " + " : "") +
      (b.cloud ? t("app.ask.availCloud") : "") +
      (!b.local && !b.cloud ? t("app.ask.availNone") : "");
    // Warn up front when the local model is not resident: the first request
    // pays the (tens-of-seconds) load into memory.
    if (b.local && !b.local_model_loaded) {
      txt += " · " + t("app.ask.modelCold");
    }
    $("ask-backends").textContent = txt;
  }).catch(function () {
    state.askBackends = null;
    $("ask-backends").textContent = "";
  });
}

/** Checkbox list of MY visible instances with their L/C contour chip; the
 *  routing preview under the button updates live. */
function renderAskInstances() {
  var box = $("ask-instances");
  var prev = new Set(_askChecked());
  box.textContent = "";
  state.instances.forEach(function (inst) {
    var cb = el("input", { type: "checkbox", value: inst.id,
                           id: "askck-" + inst.id });
    if (prev.has(inst.id)) { cb.checked = true; }
    cb.addEventListener("change", renderAskRouting);
    var scope = inst.llm_scope || "local-only";
    var lbl = el("label", { class: "ask-inst", for: "askck-" + inst.id }, [
      cb,
      el("span", { class: "mono small", text: inst.id }),
      el("span", { class: "chip " +
                   (scope === "any" ? "chip-neutral" : "chip-warn"),
                   text: scope === "any" ? t("app.scope.c")
                                         : t("app.scope.l") })
    ]);
    box.appendChild(lbl);
  });
  renderAskRouting();
}

function _askChecked() {
  return Array.from(
    document.querySelectorAll("#ask-instances input:checked")
  ).map(function (cb) { return cb.value; });
}

/** True when a ticked dataset is L (local-only). */
function _askAnyL() {
  var ids = new Set(_askChecked());
  return state.instances.some(function (i) {
    return ids.has(i.id) && (i.llm_scope || "local-only") === "local-only";
  });
}

/** Live routing hint: which model WILL answer given the ticked datasets
 *  and the cloud preference (mirrors the server rule: any L -> local;
 *  cloud preference against L -> refused). */
function renderAskRouting() {
  if (state.askBusy) { return; }
  var n = _askChecked().length;
  var anyL = _askAnyL();
  var pref = $("ask-prefer-cloud").checked;
  var box = $("ask-status");
  box.classList.toggle("ask-status-bad", anyL && pref);
  if (anyL && pref) { box.textContent = t("app.ask.routeConflict"); return; }
  if (anyL) { box.textContent = t("app.ask.routeLocal"); return; }
  if (pref) { box.textContent = t("app.ask.routeCloud"); return; }
  box.textContent = n ? t("app.ask.routeDefault") : "";
}

function askSubmit() {
  if (state.askBusy) { return; }
  var q = $("ask-question").value.trim();
  if (!q) { toast(t("app.ask.needQuestion"), "error"); return; }
  var pref = $("ask-prefer-cloud").checked;
  if (pref && _askAnyL()) {         // pre-empt the server's loud refusal
    toast(t("app.ask.routeConflict"), "error");
    return;
  }
  state.askBusy = true;
  $("btn-ask").disabled = true;
  $("ask-status").classList.remove("ask-status-bad");
  // A pure-cloud request happens only when "prefer cloud" is set and no
  // local-only dataset is involved; otherwise the local model answers. If it
  // is cold, warn that the first answer includes the model load time.
  var usesLocal = !(pref && !_askAnyL());
  var cold = !!(state.askBackends && state.askBackends.local &&
                !state.askBackends.local_model_loaded);
  $("ask-status").textContent = (usesLocal && cold)
    ? t("app.ask.loadingModel") : t("app.ask.thinking");
  api("/ask", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: q, instances: _askChecked(),
                           prefer_cloud: pref })
  }).then(function (res) {
    state.askResult = res;
    $("ask-result").classList.remove("hidden");
    renderAskAnswer();
    renderAskMeta();
    $("ask-status").textContent = "";
  }).catch(function (err) {
    $("ask-status").textContent = "";
    apiErrorToast(err, t("app.ask.fail"));
  }).finally(function () {
    state.askBusy = false;
    $("btn-ask").disabled = false;
  });
}

/** The answer box: a real answer as plain text, an OFF-TOPIC refusal in
 *  red with a label (user request 2026-07-15 — a refusal must not look
 *  like a hint), an LLM failure as the i18n error line. */
function renderAskAnswer() {
  var res = state.askResult;
  if (!res) { return; }
  var box = $("ask-answer");
  box.textContent = "";
  box.classList.toggle("ask-refused", !!(res.ok && res.refused));
  if (!res.ok) {
    box.textContent = tf("app.ask.llmFail", { error: res.error || "?" });
    return;
  }
  if (res.refused) {
    box.appendChild(el("div", { class: "ask-offtopic-label",
                                text: t("app.ask.offtopic") }));
    box.appendChild(el("div", { text: res.answer }));
    return;
  }
  box.textContent = res.answer;
}

function renderAskMeta() {
  var res = state.askResult;
  if (!res) { return; }
  if (!res.ok) {
    // nothing answered — a "answered by X" line would be a lie
    $("ask-meta").textContent = "";
    return;
  }
  var parts = [
    tf("app.ask.metaModel", { backend:
      res.backend === "local" ? t("app.ask.backendLocal")
                              : t("app.ask.backendCloud"),
      model: res.model || "?" })
  ];
  if (res.instances && res.instances.length) {
    parts.push(tf("app.ask.metaData", { list: res.instances.join(", ") }));
    if (res.scope === "local-only") {
      parts.push(t("app.ask.metaContour"));
    }
  }
  $("ask-meta").textContent = parts.join(" · ");
}

/* -------------------------------------- help center: suggestions (Помощь) */

function refreshSuggest() {
  api("/suggestions").then(function (res) {
    state.suggestions = res;
    renderSuggestions();
  }).catch(function (err) { apiErrorToast(err, t("app.sug.loadFail")); });
}

function renderSuggestions() {
  var res = state.suggestions;
  var box = $("sug-list");
  if (!res) { return; }
  box.textContent = "";
  var items = res.items || [];
  if (!items.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.sug.none") }));
    return;
  }
  var head = [el("th", { text: t("app.sug.th.at") }),
              el("th", { text: t("app.sug.th.cat") }),
              el("th", { text: t("app.sug.th.status") }),
              el("th", { text: t("app.sug.th.text") })];
  if (res.is_admin) {
    head.splice(3, 0, el("th", { text: t("app.sug.th.author") }));
  }
  head.push(el("th", { text: "" }));
  box.appendChild(demoLikeTable(head, items.map(function (r) {
    var st = r.status || "new";
    var stCls = { new: "chip-neutral", triaged: "chip-info",
                  cr_created: "chip-running", cr_accepted: "chip-info",
                  implemented: "chip-ok" }[st] || "chip-neutral";
    var cells = [
      el("td", { class: "small", text: fmtTime(r.at) }),
      el("td", { class: "small",
                 text: t("html.sug.cat." + (r.category || "other")) }),
      el("td", null, [el("span", { class: "chip " + stCls,
                                   text: t("app.sug.st." + st) })]),
      el("td", { class: "small sug-text", text: r.text })
    ];
    if (res.is_admin) {
      cells.splice(3, 0,
        el("td", { class: "mono small", text: r.email }));
    }
    var act = el("td", { class: "small" });
    var del = el("button", { class: "btn btn-ghost btn-mini",
                             type: "button", text: "✕",
                             title: t("app.sug.delete"),
                             "aria-label": t("app.sug.delete") });
    del.addEventListener("click", function () { sugDelete(r.id); });
    act.appendChild(del);
    cells.push(act);
    return el("tr", null, cells);
  })));
}

function sugSubmit() {
  var text = $("sug-text").value.trim();
  if (text.length < 5) { toast(t("app.sug.tooShort"), "error"); return; }
  $("btn-sug-send").disabled = true;
  api("/suggestions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text, category: $("sug-category").value })
  }).then(function () {
    $("sug-text").value = "";
    sugStatusKey = "app.sug.sent";
    $("sug-status").textContent = t(sugStatusKey);
    toast(t("app.sug.sent"), "ok");
    refreshSuggest();
  }).catch(function (err) {
    apiErrorToast(err, t("app.sug.fail"));
  }).finally(function () { $("btn-sug-send").disabled = false; });
}

function sugDelete(sid) {
  api("/suggestions/" + encodeURIComponent(sid), { method: "DELETE" })
    .then(function () {
      toast(t("app.sug.deleted"), "ok");
      refreshSuggest();
    }).catch(function (err) { apiErrorToast(err, t("app.sug.delFail")); });
}

/** i18n key of the suggestion status line (lang switch relabels it). */
var sugStatusKey = null;

/* ------------------------------------ loops overview («Контуры», Автоулучш.) */

/* Every self-improvement loop on one page. The loop LIST is client-side and
 * fixed: ALL loops of the «Автоулучшение» group — the seven that file
 * proposals into the CR queue, plus «Алгоритмы» and «База знаний», which
 * keep their own stores and their own screens. The server supplies only
 * measurements (last observation, metric, open proposals), so every visible
 * word here goes through t() in all five languages. */
var LOOP_ROWS = [
  { key: "feedback", title: "app.loops.t.feedback", obs: "app.loops.o.feedback" },
  { key: "regulatory", title: "app.loops.t.regulatory",
    obs: "app.loops.o.regulatory" },
  { key: "uiux", title: "app.loops.t.uiux", obs: "app.loops.o.uiux" },
  { key: "audit", title: "app.loops.t.audit", obs: "app.loops.o.audit" },
  { key: "datahealth", title: "app.loops.t.datahealth",
    obs: "app.loops.o.datahealth" },
  { key: "override", title: "app.loops.t.override",
    obs: "app.loops.o.override" },
  { key: "runtime", title: "app.loops.t.runtime", obs: "app.loops.o.runtime" },
  { key: "connbuf", title: "app.loops.t.connbuf",
    obs: "app.loops.o.connbuf" },
  { key: "lab", title: "app.loops.t.lab", obs: "app.loops.o.lab" },
  { key: "kb", title: "app.loops.t.kb", obs: "app.loops.o.kb" }
];

/* Контуры, чьи предложения живут не в очереди CR, а на своём экране: клик
 * по «Действию» уводит туда, а не запускает агента (агента с такими
 * именами и нет — loopRunAgent обратился бы к несуществующему ключу). */
var LOOP_OWN_TAB = { lab: "lab", kb: "kb" };

function refreshLoops() {
  api("/loops").then(function (res) {
    state.loops = res;
    renderLoops();
  }).catch(function (err) { apiErrorToast(err, t("app.loops.loadFail")); });
}

function renderLoops() {
  var tb = $("loops-tbody");
  if (!tb) { return; }
  tb.textContent = "";
  var res = state.loops;
  if (!res) { return; }
  var byKey = {};
  (res.items || []).forEach(function (r) { byKey[r.key] = r; });
  LOOP_ROWS.forEach(function (row) {
    var d = byKey[row.key] || {};
    var tr = el("tr", { class: "row-click" +
                        (state.loopOpen === row.key ? " active" : "") });
    tr.appendChild(el("td", { text: t(row.title) }));
    tr.appendChild(el("td", { class: "small", text: t(row.obs) }));
    tr.appendChild(el("td", { class: "small", text: fmtTime(d.last_at) }));
    tr.appendChild(el("td", { class: "small",
      text: d.metric ? tf(d.metric.i18n, { n: d.metric.n }) : "—" }));
    // null (а не 0) = «ожидающих решения сущностей у контура нет в
    // принципе»; ноль здесь читался бы как «всё разобрано»
    tr.appendChild(el("td", { class: "num",
      text: (d.open_crs === null || d.open_crs === undefined)
        ? "—" : fmt(d.open_crs) }));
    var act = el("td");
    act.appendChild(loopActionButton(row.key));
    tr.appendChild(act);
    // клик по строке (кроме кнопки действия) разворачивает подробности
    tr.addEventListener("click", function (ev) {
      if (ev.target.closest("button")) { return; }
      loopToggle(row.key);
    });
    tb.appendChild(tr);
  });
  renderLoopDetail();
}

function loopActionButton(key) {
  if (LOOP_OWN_TAB[key]) {
    var open = el("button", { class: "btn btn-ghost btn-small",
                              text: t("app.loops.open." + key) });
    open.addEventListener("click", function () { showTab(LOOP_OWN_TAB[key]); });
    return open;
  }
  if (key === "datahealth") {
    // у «Здоровья данных» ДВА действия: снимок собирает свежие дефекты, а
    // агент превращает ухудшения последнего снимка в предложения. Мануал
    // (частые ошибки) отсылает к «Запустить агента» — без этой кнопки
    // совет был невыполним: снимок предложений не заводит
    var snap = el("button", { class: "btn btn-primary btn-small",
                              text: t("app.loops.snapshot") });
    snap.addEventListener("click", function () {
      loopSnapshot(snap, "datahealth");
    });
    var dhRun = el("button", { class: "btn btn-ghost btn-small",
                               text: t("app.loops.run") });
    dhRun.addEventListener("click", function () {
      loopRunAgent("datahealth", dhRun);
    });
    return el("span", { class: "inline-controls" }, [snap, dhRun]);
  }
  if (key === "connbuf") {
    // те же два действия, что у «Здоровья данных»: снимок собирает
    // перепись, агент превращает УХУДШЕНИЕ последнего снимка в
    // предложения — снимок сам предложений не заводит
    var cbSnap = el("button", { class: "btn btn-primary btn-small",
                                text: t("app.loops.snapshot") });
    cbSnap.addEventListener("click", function () {
      loopSnapshot(cbSnap, "connbuf");
    });
    var cbRun = el("button", { class: "btn btn-ghost btn-small",
                               text: t("app.loops.run") });
    cbRun.addEventListener("click", function () {
      loopRunAgent("connbuf", cbRun);
    });
    return el("span", { class: "inline-controls" }, [cbSnap, cbRun]);
  }
  if (key === "audit") {
    // у «Аудита» нет запускаемого агента: отчёты приносит приёмник, и его
    // собственные кнопки живут на вкладке «Изменения»
    var go = el("button", { class: "btn btn-ghost btn-small",
                            text: t("app.loops.openAudit") });
    go.addEventListener("click", function () {
      crsView = "audit";
      showTab("crs");
    });
    return go;
  }
  var run = el("button", { class: "btn btn-ghost btn-small",
                           text: t("app.loops.run") });
  run.addEventListener("click", function () { loopRunAgent(key, run); });
  return run;
}

/** Reuses the CR agent-run endpoint: a loop key IS its agent name. */
function loopRunAgent(key, btn) {
  btn.disabled = true;
  api("/cr/agents/" + encodeURIComponent(key) + "/run", { method: "POST" })
    .then(function (out) {
      toast(tf("app.loops.runDone", { n: out.created }), "ok");
      refreshLoops();
    }).catch(function (err) {
      apiErrorToast(err, t("app.loops.runFail"));
    }).finally(function () { btn.disabled = false; });
}

/* Контуры со СНИМКАМИ: ключ строки -> базовый путь их эндпоинтов. Один
 * словарь на снимок и на подробности, чтобы пути не разъезжались. */
var LOOP_SNAPSHOT_PATH = {
  datahealth: "/loops/data-health",
  connbuf: "/loops/connection-buffer"
};

function loopSnapshot(btn, key) {
  var base = LOOP_SNAPSHOT_PATH[key];
  if (!base) { return; }
  btn.disabled = true;
  api(base + "/snapshot", { method: "POST" })
    .then(function (out) {
      toast(tf("app.loops.snapDone", { n: (out.taken || []).length }), "ok");
      refreshLoops();
      if (state.loopOpen === key) { loopLoadDetail(key); }
    }).catch(function (err) {
      apiErrorToast(err, t("app.loops.snapFail"));
    }).finally(function () { btn.disabled = false; });
}

function loopToggle(key) {
  if (state.loopOpen === key) {
    state.loopOpen = null;
    state.loopDetail = null;
    renderLoops();
    return;
  }
  state.loopOpen = key;
  state.loopDetail = null;
  renderLoops();
  loopLoadDetail(key);
}

/** Fetch the expanded loop's own details; loops without a detail endpoint
 *  (the three LLM agents + audit) just show their explanation text. */
function loopLoadDetail(key) {
  var url = null;
  if (key === "datahealth") {
    var inst = state.loopInstance ||
               (state.instances[0] && state.instances[0].id);
    if (!inst) { state.loopDetail = { empty: true }; renderLoopDetail(); return; }
    state.loopInstance = inst;
    url = "/loops/data-health/" + encodeURIComponent(inst);
  } else if (key === "connbuf") {
    var cbInst = state.loopInstance ||
                 (state.instances[0] && state.instances[0].id);
    if (!cbInst) { state.loopDetail = { empty: true }; renderLoopDetail(); return; }
    state.loopInstance = cbInst;
    url = "/loops/connection-buffer/" + encodeURIComponent(cbInst);
  } else if (key === "override") {
    url = "/loops/overrides";
  } else if (key === "runtime") {
    url = "/loops/runtime";
  }
  if (!url) { state.loopDetail = { info: true }; renderLoopDetail(); return; }
  api(url).then(function (res) {
    if (state.loopOpen !== key) { return; }     // строку успели свернуть
    state.loopDetail = res;
    renderLoopDetail();
  }).catch(function (err) { apiErrorToast(err, t("app.loops.loadFail")); });
}

function renderLoopDetail() {
  var box = $("loops-detail");
  if (!box) { return; }
  box.textContent = "";
  var key = state.loopOpen;
  if (!key) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  var card = el("div", { class: "card" });
  card.appendChild(el("h3", { text: t("app.loops.t." + key) }));
  var d = state.loopDetail;
  if (!d) {
    card.appendChild(el("p", { class: "muted small", text: t("html.loading") }));
    box.appendChild(card);
    return;
  }
  if (key === "datahealth") { loopDetailDataHealth(card, d); }
  else if (key === "connbuf") { loopDetailConnBuf(card, d); }
  else if (key === "override") { loopDetailOverrides(card, d); }
  else if (key === "runtime") { loopDetailRuntime(card, d); }
  else {
    card.appendChild(el("p", { class: "small", text: t("app.loops.d." + key) }));
  }
  box.appendChild(card);
}

/** Подпись для КОДА, пришедшего с сервера (вид дефекта, причина переделки).
 *  Сервер отдаёт коды и, для совместимости, свою карту подписей; человеческие
 *  названия обязаны жить в i18n, иначе интерфейс говорил бы по-русски при
 *  любом выбранном языке. Порядок: собственный ключ → серверная подпись
 *  (её тоже прогоняем через t(): сервер может прислать ключ i18n) → сам код,
 *  чтобы вид, добавленный на сервере раньше клиента, не исчезал из таблицы. */
function loopCodeLabel(prefix, code, serverLabels) {
  var key = prefix + code;
  var own = t(key);
  if (own !== key) { return own; }
  var given = (serverLabels || {})[code];
  return given ? t(given) : code;
}

function loopDetailDataHealth(card, d) {
  // выбор экземпляра: история дефектов всегда про конкретную выгрузку
  var sel = el("select");
  state.instances.forEach(function (inst) {
    var o = el("option", { value: inst.id, text: inst.label || inst.id });
    if (inst.id === state.loopInstance) { o.setAttribute("selected", "selected"); }
    sel.appendChild(o);
  });
  sel.addEventListener("change", function () {
    state.loopInstance = sel.value;
    state.loopDetail = null;
    renderLoopDetail();
    loopLoadDetail("datahealth");
  });
  card.appendChild(el("div", { class: "form-row inline-controls" },
                      [el("span", { class: "muted small",
                                    text: t("app.loops.instance") }), sel]));
  var trend = d.trend || {};
  var kinds = trend.kinds || {};
  var names = Object.keys(kinds);
  if (!names.length) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.dh.none") }));
    return;
  }
  if (!trend.hasBaseline) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.dh.baseline") }));
  }
  var tbl = el("table", { class: "data-table" });
  tbl.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: t("app.loops.dh.kind") }),
    el("th", { class: "num", text: t("app.loops.dh.prev") }),
    el("th", { class: "num", text: t("app.loops.dh.now") }),
    el("th", { class: "num", text: t("app.loops.dh.delta") })
  ])]));
  var body = el("tbody");
  names.sort().forEach(function (k) {
    var r = kinds[k];
    var delta = r.delta > 0 ? "+" + r.delta : String(r.delta);
    body.appendChild(el("tr", {}, [
      el("td", { text: loopCodeLabel("app.loops.dh.k.", k, d.kinds) }),
      el("td", { class: "num", text: String(r.prev) }),
      el("td", { class: "num", text: String(r.now) }),
      el("td", { class: "num " + (r.delta > 0 ? "bad" : (r.delta < 0 ? "ok" : "")),
                 text: delta })
    ]));
  });
  tbl.appendChild(body);
  card.appendChild(tbl);
  card.appendChild(el("p", { class: "muted small",
    text: tf("app.loops.dh.snaps", { n: (d.history || []).length }) }));
}

function loopDetailConnBuf(card, d) {
  var sel = el("select");
  state.instances.forEach(function (inst) {
    var o = el("option", { value: inst.id, text: inst.label || inst.id });
    if (inst.id === state.loopInstance) { o.setAttribute("selected", "selected"); }
    sel.appendChild(o);
  });
  sel.addEventListener("change", function () {
    state.loopInstance = sel.value;
    state.loopDetail = null;
    renderLoopDetail();
    loopLoadDetail("connbuf");
  });
  card.appendChild(el("div", { class: "form-row inline-controls" },
                      [el("span", { class: "muted small",
                                    text: t("app.loops.instance") }), sel]));
  var c = d.census || {};
  // Ростер, посчитанный по ДРУГИМ правилам, и вовсе отсутствующий ростер —
  // разные причины молчания, и обе надо назвать: иначе пустой экран
  // читается как «стыковок нет», то есть как хорошая новость.
  if (!c.counted) {
    card.appendChild(el("p", { class: "muted small",
      text: t("app.loops.cb.no." + (c.status === "stale" ? "stale" : "roster")) }));
    return;
  }
  card.appendChild(el("p", { class: "small",
    text: tf("app.loops.cb.head", { n: c.connections, t: c.tight,
                                    min: c.min_sit_min,
                                    max: c.min_sit_min + c.margin_min }) }));
  var trend = d.trend || {};
  if (trend.hasBaseline && trend.deltaTight !== null) {
    var dt = trend.deltaTight;
    card.appendChild(el("p", {
      class: "small " + (dt > 0 ? "bad" : (dt < 0 ? "ok" : "")),
      text: tf("app.loops.cb.delta", { n: dt > 0 ? "+" + dt : String(dt) }) }));
  } else {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.cb.baseline") }));
  }
  var ex = c.examples || [];
  if (!ex.length) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.cb.none") }));
  } else {
    var tbl = el("table", { class: "data-table" });
    tbl.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", { text: t("app.loops.cb.airport") }),
      el("th", { text: t("app.loops.cb.legs") }),
      el("th", { text: t("app.loops.cb.when") }),
      el("th", { class: "num", text: t("app.loops.cb.sit") })
    ])]));
    var body = el("tbody");
    ex.forEach(function (r) {
      body.appendChild(el("tr", {}, [
        el("td", { text: r.airport || "—" }),
        el("td", { class: "small", text: (r.in_fn || "—") + " → " + (r.out_fn || "—") }),
        el("td", { class: "small", text: fmtTime(r.at) }),
        el("td", { class: "num", text: String(r.sit_min) })
      ]));
    });
    tbl.appendChild(body);
    card.appendChild(tbl);
  }
  var slack = c.slack || {};
  if (slack.median !== null && slack.median !== undefined) {
    card.appendChild(el("p", { class: "muted small",
      text: tf("app.loops.cb.slack", { m: slack.median, p: slack.p10 }) }));
  }
  card.appendChild(el("p", { class: "muted small",
    text: tf("app.loops.dh.snaps", { n: (d.history || []).length }) }));
}

/** Срез кластера: «Сотрудник: 1042». У среза "systemic" значение ПУСТОЕ по
 *  построению (один общий ключ на причину), поэтому двоеточие с хвостом
 *  там не печатаем. */
function loopFacetLabel(key) {
  var name = loopCodeLabel("app.loops.ov.f.", key.facet, null);
  return key.value ? name + ": " + key.value : name;
}

function loopDetailOverrides(card, d) {
  card.appendChild(el("p", { class: "muted small",
    text: tf("app.loops.ov.total", { n: d.total_episodes || 0 }) }));
  var clusters = d.clusters || [];
  if (!clusters.length) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.ov.none") }));
    return;
  }
  var tbl = el("table", { class: "data-table" });
  tbl.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: t("app.loops.ov.reason") }),
    el("th", { text: t("app.loops.ov.facet") }),
    el("th", { class: "num", text: t("app.loops.ov.size") }),
    el("th", { text: t("app.loops.ov.instances") })
  ])]));
  var body = el("tbody");
  clusters.forEach(function (c) {
    body.appendChild(el("tr", {}, [
      el("td", { text: loopCodeLabel("app.loops.reason.", c.reason, d.reasons) }),
      el("td", { class: "small", text: loopFacetLabel(c.key) }),
      el("td", { class: "num", text: String(c.size) }),
      el("td", { class: "small", text: (c.instances || []).join(", ") })
    ]));
  });
  tbl.appendChild(body);
  card.appendChild(tbl);
}

function loopDetailRuntime(card, d) {
  var prof = d.profile || {};
  var byType = prof.by_type || {};
  var types = Object.keys(byType).sort();
  card.appendChild(el("p", { class: "muted small",
    text: tf("app.loops.rt.total", { n: prof.total_runs || 0 }) }));
  if (!types.length) {
    card.appendChild(el("p", { class: "muted small",
                               text: t("app.loops.rt.none") }));
    return;
  }
  var tbl = el("table", { class: "data-table" });
  tbl.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: t("app.loops.rt.jtype") }),
    el("th", { class: "num", text: t("app.loops.rt.runs") }),
    el("th", { class: "num", text: t("app.loops.rt.within") }),
    el("th", { class: "num", text: t("app.loops.rt.median") }),
    el("th", { class: "num", text: t("app.loops.rt.p90") }),
    el("th", { class: "num", text: t("app.loops.rt.failure") })
  ])]));
  var body = el("tbody");
  types.forEach(function (jt) {
    var a = byType[jt];
    body.appendChild(el("tr", {}, [
      el("td", { text: jt }),
      el("td", { class: "num", text: String(a.runs) }),
      el("td", { class: "num",
                 text: a.within_budget_pct === null ? "—"
                                                    : a.within_budget_pct + "%" }),
      el("td", { class: "num", text: fmt(a.median_wall_s) }),
      el("td", { class: "num", text: fmt(a.p90_wall_s) }),
      el("td", { class: "num", text: a.failure_pct + "%" })
    ]));
  });
  tbl.appendChild(body);
  card.appendChild(tbl);
}

/* --------------------------------- change-request pipeline (Автоулучшение) */

var CR_STATUS = {
  draft: "chip-neutral", accepted: "chip-info", implementing: "chip-running",
  implemented: "chip-ok", rejected: "chip-bad", needs_human: "chip-warn"
};

/* The Изменения tab is one INTERFACE PER AGENT (user feedback 2026-07-16):
 * a segmented switcher picks the agent's view — description, its own run
 * button, and the CR queue FILTERED to that agent. Default is Отзывы;
 * «Все» shows the unified queue for triage (no run button). */
var CRS_VIEWS = ["feedback", "regulatory", "uiux", "audit", "catalog",
                 "all"];
/* Вид «Каталог» (шаг 6 каталога шаблонов) сводит строки очереди с
 * ключом записи каталога и два исторических имени, под которыми такие
 * строки рождались раньше. Кнопки «Запустить агента» у него нет: строку
 * рождает решение владельца по заявке, принятое в службе каталога. */
var CATALOG_AGENTS = ["catalog-executor", "vmp-gap", "template-base"];
var crsView = "feedback";

function crsInView(cr, view) {
  if (view === "all") { return true; }
  if (view === "catalog") {
    return CATALOG_AGENTS.indexOf(cr.agent) >= 0;
  }
  return cr.agent === view;
}

function refreshCrs() {
  api("/cr").then(function (res) {
    state.crs = res;
    renderCrs();
  }).catch(function (err) { apiErrorToast(err, t("app.crs.loadFail")); });
  auditRefresh();
  if (crsView === "catalog") { catalogRefresh(); }
}

function renderCrs() {
  var res = state.crs;
  if (!res) { return; }
  // agent view switcher (rebuilt every render: labels are localized)
  var nav = $("crs-agents");
  nav.textContent = "";
  CRS_VIEWS.forEach(function (v) {
    var n = (res.items || []).filter(function (r) {
      return crsInView(r, v);
    }).length;
    var btn = el("button", {
      class: "tab" + (v === crsView ? " active" : ""),
      text: (v === "all" ? t("app.crs.view.all")
             : v === "catalog" ? t("app.crs.view.catalog")
                               : t("app.crs.agent." + v)) +
            (n ? " (" + n + ")" : "")
    });
    btn.addEventListener("click", function () {
      crsView = v;
      renderCrs();
    });
    nav.appendChild(btn);
  });
  // the selected agent's own description + run control («Все» = triage
  // only; «Аудит» has its own check/process controls instead of a run)
  $("crs-agent-desc").textContent =
    t(crsView === "all" ? "app.crs.desc.all" : "app.crs.desc." + crsView);
  $("btn-crs-run").classList.toggle(
    "hidden", crsView === "all" || crsView === "audit"
              || crsView === "catalog");
  $("crs-audit-box").classList.toggle("hidden", crsView !== "audit");
  if (crsView === "audit") { auditRender(); }
  catalogBox().classList.toggle("hidden", crsView !== "catalog");
  if (crsView === "catalog") { catalogRender(); }
  // quota line lives here so a language switch re-localizes it too
  $("crs-quota").textContent = tf("app.crs.quota",
                                  { n: res.impl_quota_left });
  var box = $("crs-list");
  box.textContent = "";
  var items = (res.items || []).filter(function (r) {
    return crsInView(r, crsView);
  });
  if (!items.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.crs.none") }));
    return;
  }
  items.forEach(function (cr) {
    var card = el("div", { class: "card cr-card" });
    var head = el("div", { class: "cr-head" }, [
      el("span", { class: "chip " + (CR_STATUS[cr.status] || "chip-neutral"),
                   text: t("app.crs.st." + cr.status) }),
      el("span", { class: "chip chip-neutral small",
                   text: t("app.crs.agent." + cr.agent) }),
      el("strong", { text: cr.title })
    ]);
    if (cr.country) {
      head.appendChild(el("span", { class: "chip chip-neutral small",
                                    text: cr.country }));
    }
    card.appendChild(head);
    card.appendChild(el("p", { class: "small", text: cr.summary }));
    var meta = [tf("app.crs.priority", { n: cr.priority }),
                tf("app.crs.effort", { n: cr.effort })];
    if (cr.created_at) {
      // дата появления заявки — симметрично колонке «Найдена» Алгоритмов
      meta.push(tf("app.crs.createdAt",
                   { d: String(cr.created_at).slice(0, 10) }));
    }
    if (cr.source) { meta.push(cr.source); }
    if (cr.suggestion_ids && cr.suggestion_ids.length) {
      meta.push(tf("app.crs.fromN", { n: cr.suggestion_ids.length }));
    }
    card.appendChild(el("p", { class: "muted small", text: meta.join(" · ") }));

    if (cr.reject_reason) {
      card.appendChild(el("p", { class: "banner-warn small",
        text: tf("app.crs.rejectedReason", { r: cr.reject_reason }) }));
    }
    if (cr.kind === "catalog-executor") { crCatalogBlock(card, cr); }
    // Предполётная проверка идёт ДО агента, потому и в карточке стоит
    // ПЕРЕД итогом его работы. Рисуется по наличию поля, а не по виду
    // строки: судьи добавляются, и поле может появиться у иной строки.
    crPreflightBlock(card, cr);
    if (cr.impl) {
      var im = cr.impl;
      var box2 = el("div", { class: "cr-impl" });
      if (im.branch) {
        box2.appendChild(el("p", { class: "mono small", text:
          tf("app.crs.implBranch", { b: im.branch, c: im.commit || "",
            tests: im.tests_ok ? t("app.crs.testsOk")
                               : t("app.crs.testsFail") }) }));
      }
      // «Нужен человек» до ветки — отказ по рамкам (общая фраза); после
      // работы агента это честный исход красного гейта, и причину
      // называют слова отчёта, а не общая фраза.
      if (im.needs_human) {
        box2.appendChild(el("p", { class: "banner-warn small",
          text: (im.branch && im.report) ? im.report.slice(0, 400)
                                         : t("app.crs.needsHuman") }));
      }
      if (im.diff_stat) {
        box2.appendChild(el("pre", { class: "cr-diff", text: im.diff_stat }));
      }
      // failure report without a branch (diff guard, internal error, CLI tail)
      if (im.report && !im.branch) {
        box2.appendChild(el("p", { class: "muted small",
                                   text: im.report.slice(0, 400) }));
      }
      card.appendChild(box2);
    }

    // admin actions per status
    var actions = el("div", { class: "form-row form-actions" });
    if (cr.status === "draft" || cr.status === "needs_human") {
      actions.appendChild(crBtn("app.crs.accept", "btn-primary",
        function () { crAction(cr.id, "accept"); }));
    }
    if (cr.status === "accepted") {
      var imBtn = crBtn("app.crs.implement", "btn-primary",
        function () { crImplement(cr.id, imBtn); });
      imBtn.disabled = res.impl_quota_left <= 0;
      // У строки каталога кнопка гаснет с ПРИЧИНОЙ словами: заявка не
      // называет ни одного вида исполнителя либо служба смотрит в другой
      // каталог (ключ открыт не по нему). Вид исполнителя кнопку не
      // гасит: исполнителей решателя (kotlin_*) пишет тот же агент тем
      // же CR, а красный гейт становится исходом «нужен человек».
      var why = crCatalogBlocked(cr);
      if (why) { imBtn.disabled = true; imBtn.title = why; }
      actions.appendChild(imBtn);
    }
    // CR каталога закрывается только решением владельца по заявке —
    // кнопки отклонения у него нет (сервер отказывает словами).
    if (cr.status !== "implemented" && cr.status !== "rejected"
        && cr.kind !== "catalog-executor") {
      actions.appendChild(crBtn("app.crs.reject", "btn-ghost",
        function () { crReject(cr.id); }));
    }
    if (actions.children.length) { card.appendChild(actions); }
    box.appendChild(card);
  });
}

function crBtn(key, cls, fn) {
  var b = el("button", { class: "btn " + cls + " btn-small", type: "button",
                         text: t(key) });
  b.addEventListener("click", fn);
  return b;
}

function crAction(id, action) {
  api("/cr/" + encodeURIComponent(id) + "/" + action, { method: "POST" })
    .then(function () { toast(t("app.crs.done"), "ok"); refreshCrs(); })
    .catch(function (err) { apiErrorToast(err, t("app.crs.fail")); });
}

function crReject(id) {
  appPrompt(t("app.crs.rejectPrompt")).then(function (reason) {
    if (reason === null) { return; }
    api("/cr/" + encodeURIComponent(id) + "/reject", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason })
    }).then(function () { toast(t("app.crs.done"), "ok"); refreshCrs(); })
      .catch(function (err) { apiErrorToast(err, t("app.crs.fail")); });
  });
}

function crImplement(id, btn) {
  appConfirm(t("app.crs.implConfirm")).then(function (yes) {
    if (!yes) { return; }
    if (btn) { btn.disabled = true; }     // minutes-long POST: no re-fire
    toast(t("app.crs.implRunning"), "ok");
    api("/cr/" + encodeURIComponent(id) + "/implement", { method: "POST" })
      .then(function (cr) {
        toast(cr.status === "implemented" ? t("app.crs.implDone")
                                          : t("app.crs.implNeedsHuman"),
              cr.status === "implemented" ? "ok" : "error");
        refreshCrs();
      }).catch(function (err) {
        if (btn) { btn.disabled = false; }
        apiErrorToast(err, t("app.crs.implFail"));
      });
  });
}

function crRunAgent() {
  var agent = crsView === "all" ? "feedback" : crsView;
  $("btn-crs-run").disabled = true;
  $("crs-agent-status").textContent = t("app.crs.agentRunning");
  api("/cr/agents/" + agent + "/run", { method: "POST" })
    .then(function (out) {
      $("crs-agent-status").textContent =
        tf("app.crs.agentDone", { n: out.created });
      refreshCrs();
    }).catch(function (err) {
      $("crs-agent-status").textContent = "";
      apiErrorToast(err, t("app.crs.agentFail"));
    }).finally(function () { $("btn-crs-run").disabled = false; });
}

/* ------------- вид «Каталог» вкладки «Изменения» (шаг 6 каталога)
 *
 * Заявки каталога × строки CR × кандидаты × очередь заявок COSMOS.
 * Кнопки «Запустить агента» здесь нет: строку CR рождает решение
 * владельца по заявке, принятое в СЛУЖБЕ каталога, а «Сверить с
 * каталогом» лишь зеркалит его в очередь — повтор ничего не меняет. */

var catalogState = { data: null };

/** Блок вида заводится РИСОВАНИЕМ (разметка экрана его не несёт) —
 *  идемпотентно, сразу за блоком аудита. */
function catalogBox() {
  var box = $("crs-catalog-box");
  if (box) { return box; }
  box = el("div", { id: "crs-catalog-box", class: "hidden" });
  var audit = $("crs-audit-box");
  if (audit && audit.parentNode) {
    audit.parentNode.insertBefore(box, audit.nextSibling);
  } else {
    ($("crs-list") || document.body).appendChild(box);
  }
  return box;
}

function catalogRefresh() {
  api("/cr/catalog").then(function (res) {
    catalogState.data = res;
    catalogRender();
  }).catch(function (err) {
    var d = err && err.body && err.body.detail;
    catalogState.data = { error: (typeof d === "string" ? d
                                  : (d && d.error) || String(err)) };
    catalogRender();
  });
}

function catalogRender() {
  var box = catalogBox();
  box.textContent = "";
  var d = catalogState.data;
  var line = el("div", { class: "form-row inline-controls" });
  var sync = el("button", { class: "btn btn-primary btn-small",
                            type: "button",
                            text: t("app.crs.catalog.sync") });
  sync.addEventListener("click", catalogSync);
  var deliver = el("button", { class: "btn btn-ghost btn-small",
                               type: "button",
                               text: t("app.crs.catalog.deliver") });
  deliver.addEventListener("click", catalogDeliver);
  line.appendChild(sync);
  line.appendChild(deliver);
  line.appendChild(el("span", { id: "crs-catalog-status",
                                class: "muted small" }));
  box.appendChild(line);
  if (!d) { return; }
  if (d.error) {
    box.appendChild(el("p", { class: "banner-warn small",
                              text: String(d.error) }));
    return;
  }
  var svc = d.service || {};
  if (!svc.ui_url && !svc.api_url) {
    sync.disabled = true;
    deliver.disabled = true;
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.crs.catalog.noService") }));
  }
  if (svc.same_catalog === false) {
    box.appendChild(el("p", { class: "banner-warn small",
                              text: t("app.crs.catalog.otherCatalog") }));
  }
  if (svc.mode === "view") {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.crs.catalog.keyNotWritten") }));
  }
  var ob = d.outbox || {};
  box.appendChild(el("p", { class: "muted small", text:
    (ob.words ? String(ob.words)
              : tf("app.crs.catalog.outbox",
                   { n: (ob.pending === null || ob.pending === undefined)
                        ? "—" : ob.pending, p: ob.path || "" }))
    + " · " + tf("app.crs.catalog.auto",
                 { v: d.auto ? t("flow.gap.auto.on")
                             : t("flow.gap.auto.off") }) }));
  box.appendChild(el("h4", { text: t("app.crs.catalog.h") }));
  var reqs = d.requests || [];
  if (!reqs.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.crs.catalog.none") }));
  } else {
    var head = ["request", "state", "record", "who", "source", "kinds",
                "crKey", "crStatus"].map(function (k) {
      return el("th", { text: t("app.crs.catalog.th." + k) });
    });
    var body = el("tbody", {}, reqs.map(function (r) {
      var num = r.link
        ? el("a", { href: r.link, target: "_blank", rel: "noopener",
                    class: "mono small", text: r.request_id || "" })
        : el("span", { class: "mono small", text: r.request_id || "" });
      return el("tr", {}, [
        el("td", {}, [num]),
        el("td", { class: "small", text: r.state || "" }),
        el("td", { class: "mono small",
                   text: (r.section || "") + "/" + (r.record_id || "") }),
        el("td", { class: "small", text: r.submitted_by || "" }),
        el("td", { class: "small",
                   text: [r.source_kind, r.ref].filter(Boolean).join(" · ") }),
        el("td", { class: "small", text: (r.kinds || []).join(", ") }),
        el("td", { class: r.cr_key_words ? "small" : "mono small",
                   text: r.cr_key || r.cr_key_expected
                         || r.cr_key_words || "" }),
        el("td", { class: "small", text: r.cr_status || "—" })
      ]);
    }));
    box.appendChild(el("div", { class: "vmp-table-wrap" }, [
      el("table", {}, [el("thead", {}, [el("tr", {}, head)]), body])]));
  }
  (d.candidates || []).forEach(function (c) {
    box.appendChild(el("p", { class: "muted small",
      text: tf("app.crs.catalog.candidate",
               { p: c.path, w: c.where || "" }) }));
  });
}

function catalogSync() {
  var st = $("crs-catalog-status");
  if (st) { st.textContent = t("app.crs.agentRunning"); }
  api("/cr/catalog/sync", { method: "POST" }).then(function (out) {
    if (st) {
      st.textContent = tf("app.crs.catalog.synced", {
        o: (out.opened || []).length, k: (out.keyed || []).length,
        r: (out.rejected || []).length });
    }
    (out.problems || []).forEach(function (p) { toast(String(p), "warn"); });
    refreshCrs();
    catalogRefresh();
  }).catch(function (err) {
    if (st) { st.textContent = ""; }
    apiErrorToast(err, t("app.crs.catalog.syncFail"));
  });
}

function catalogDeliver() {
  var st = $("crs-catalog-status");
  if (st) { st.textContent = t("app.crs.agentRunning"); }
  api("/cr/catalog/deliver", { method: "POST" }).then(function (out) {
    var n = ((out && out.delivered) || []).length;
    if (st) { st.textContent = tf("app.crs.catalog.delivered", { n: n }); }
    ((out && out.problems) || []).forEach(function (p) {
      toast(String(p), "warn");
    });
    catalogRefresh();
  }).catch(function (err) {
    if (st) { st.textContent = ""; }
    apiErrorToast(err, t("app.crs.catalog.syncFail"));
  });
}

/** Блок карточки CR каталога: запись, заявка, виды, гейты, ветка. */
function crCatalogBlock(card, cr) {
  var rec = cr.record || {};
  var spec = cr.spec || {};
  var im = cr.impl || {};
  var kinds = (spec.executors_requested || []).map(function (x) {
    return x.kind;
  });
  var deferred = (spec.executors_deferred || []).map(function (x) {
    return x.kind;
  });
  // Отложенных видов больше не бывает: исполнителей решателя (kotlin_*)
  // пишет тот же агент тем же CR. Поле осталось ради формы строки —
  // виды из него показываем наравне с запрошенными.
  var kindsText = kinds.concat(deferred).join(", ");
  card.appendChild(el("p", { class: "muted small",
    text: tf("app.crs.catalog.record", {
      r: (rec.section || "") + "/" + (rec.record_id || ""),
      q: rec.request_id || "", f: rec.request_form || "",
      k: kindsText || "—" }) }));
  var g = im.gates_report;
  if (g) {
    var s = g.stand || {};
    card.appendChild(el("p", { class: "mono small",
      text: tf("app.crs.catalog.gates", {
        paths: g.paths, files: g.executor_files, chain: g.chain_live,
        tests: g.pytest,
        stand: (s.passed || 0) + "/" + (s.failed || 0) + "/"
               + (s.skipped || 0),
        bindings: String(g.bindings_build) + " · " + String(g.bindings_gate),
        validate: g.catalog_validate_errors }) }));
  }
  if (im.candidate) {
    card.appendChild(el("p", { class: "muted small",
      text: tf("app.crs.catalog.candidate",
               { p: im.candidate, w: im.branch || "" }) }));
  }
  if (im.isolation) {
    card.appendChild(el("p", { class: "muted small",
      text: "isolation: " + im.isolation
            + (im.sync ? " · " + im.sync : "") }));
  }
}

/** Вердикт предполётной проверки → чип карточки (те же три слова, что
 *  у сервера: `webapp/change_requests.py`, PREFLIGHT_OK / PREFLIGHT_GAPS
 *  / PREFLIGHT_ASSUMPTION). Семантика цвета: ожидаемые пробелы —
 *  ПРЕДУПРЕЖДЕНИЕ (их закрывает агент, реализация идёт), допущение
 *  судей — СТОП (строка ушла «нужен человек» ДО агента). */
var PREFLIGHT_CHIP = {
  "ok": "chip-ok",
  "expected-gaps": "chip-warn",
  "judge-assumption": "chip-bad"
};

/** Вид находки (`_pf_kind` на сервере) → чип: ожидаемый пробел —
 *  предупреждение, негодная заготовка и допущение судьи — стоп. */
var PREFLIGHT_KIND_CHIP = {
  gap: "chip-warn", draft: "chip-bad", assumption: "chip-bad"
};

/** Сколько пробелов показывает карточка, не разрастаясь; остальные
 *  названы числом. Полный перечень едет в задание агенту разделом
 *  «ЧТО ОБЯЗАНО ЗАКРЫТЬСЯ». */
var PREFLIGHT_GAPS_SHOWN = 5;

/** Блок карточки CR «Предполётная проверка»: вердикт судей о ЗАГОТОВКЕ
 *  записи — ДО ветки, ДО квоты и ДО запуска агента (шаг 0-П,
 *  руководство 13.14). У строк прежних версий поля `preflight` нет, и
 *  блок им не рисуется вовсе: «неизвестно» не выдумываем.
 *
 *  Незнакомое слово вердикта (судьи ДОБАВЛЯЮТСЯ — это принцип контура)
 *  показывается как есть нейтральным чипом: молчать о нём хуже, чем
 *  показать сырое слово. */
function crPreflightBlock(card, cr) {
  var pf = cr.preflight;
  if (!pf || typeof pf !== "object" || !pf.verdict) { return; }
  var findings = pf.findings || [];
  var gaps = pf.gaps || [];
  var box = el("div", { class: "cr-preflight" });
  // «ожидаемые пробелы: N» считает находки судей, а не заготовку:
  // число в заголовке — это то, что судьи НАЗВАЛИ.
  var gapN = findings.filter(function (f) {
    return f && f.kind === "gap";
  }).length;
  var chip = PREFLIGHT_CHIP[pf.verdict];
  box.appendChild(el("div", { class: "cr-head" }, [
    el("strong", { class: "small", text: t("app.crs.pf.h") }),
    el("span", { class: "chip " + (chip || "chip-neutral") + " small",
                 text: chip ? tf("app.crs.pf.v." + pf.verdict, { n: gapN })
                            : String(pf.verdict) }),
    el("span", { class: "muted small", text: fmtTime(pf.at) })
  ]));
  if (findings.length) {
    var head = ["judge", "kind", "words"].map(function (k) {
      return el("th", { text: t("app.crs.pf.th." + k) });
    });
    var body = el("tbody", {}, findings.map(function (f) {
      var kc = PREFLIGHT_KIND_CHIP[f.kind];
      return el("tr", {}, [
        el("td", { class: "mono small", text: f.judge || "" }),
        el("td", {}, [el("span", {
          class: "chip " + (kc || "chip-neutral") + " small",
          text: kc ? t("app.crs.pf.kind." + f.kind)
                   : String(f.kind || "") })]),
        el("td", { class: "small", text: f.words || "" })
      ]);
    }));
    box.appendChild(el("div", { class: "vmp-table-wrap" }, [
      el("table", {}, [el("thead", {}, [el("tr", {}, head)]), body])]));
  }
  if (gaps.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.crs.pf.gaps") }));
    box.appendChild(el("ul", { class: "artifact-list small" },
      gaps.slice(0, PREFLIGHT_GAPS_SHOWN).map(function (g) {
        return el("li", { text: String(g) });
      })));
    if (gaps.length > PREFLIGHT_GAPS_SHOWN) {
      box.appendChild(el("p", { class: "muted small",
        text: tf("app.crs.pf.more",
                 { n: gaps.length - PREFLIGHT_GAPS_SHOWN }) }));
    }
  }
  card.appendChild(box);
}

/** Причина, по которой «Реализовать на Тест» у строки каталога гаснет;
 *  пустая строка — препятствий нет. Вид исполнителя причиной не бывает:
 *  виды решателя (kotlin_*) реализует тот же агент, а красный гейт —
 *  исход «нужен человек» со словами, а не запрет на кнопке. */
function crCatalogBlocked(cr) {
  if (cr.kind !== "catalog-executor") { return ""; }
  // заявка не называет ни одного вида исполнителя — реализовывать нечего
  if (!((cr.spec || {}).executors_requested || []).length) {
    return t("app.crs.catalog.manualOnly");
  }
  var svc = (catalogState.data || {}).service || {};
  if (svc.same_catalog === false) {
    return t("app.crs.catalog.otherCatalog");
  }
  return "";
}

/* -------------------------- third-party audit inbox (вид «Аудит») */

var auditBannerShown = {};      // file -> true: the toast fired this session

function auditRefresh() {
  api("/audits").then(function (res) {
    state.audits = res;
    // one-per-file session banner: a fresh audit deserves attention even
    // when the admin is on another view of the tab
    (res.files || []).forEach(function (f) {
      if (f.status === "new" && !f.missing && !auditBannerShown[f.file]) {
        auditBannerShown[f.file] = true;
        toast(tf("app.audit.newBanner", { f: f.file }), "ok");
      }
    });
    if (crsView === "audit") { auditRender(); }
  }).catch(function () { /* не-Test контур без директории — тихо */ });
}

// #audit-status carries localized, parameterized prose; remember the key
// + params so the crew:lang handler can re-localize it (house pattern,
// like sugStatusKey) instead of leaving stale text after a language switch
var auditStatusKey = null;
var auditStatusParams = null;

function setAuditStatus(key, params) {
  auditStatusKey = key;
  auditStatusParams = params || null;
  $("audit-status").textContent = !key ? ""
    : params ? tf(key, params) : t(key);
}

function relocalizeAuditStatus() {
  if (auditStatusKey) { setAuditStatus(auditStatusKey, auditStatusParams); }
}

function auditCheck() {
  $("btn-audit-check").disabled = true;
  setAuditStatus("app.audit.checking");
  api("/audits").then(function (res) {
    state.audits = res;
    var fresh = (res.new || []).length;
    var pending = (res.files || []).filter(function (f) {
      return f.status === "new" && !f.missing;
    }).length;
    var ignored = (res.ignored || []).length;
    if (fresh) { setAuditStatus("app.audit.foundN", { n: fresh }); }
    else if (pending) { setAuditStatus("app.audit.pendingN", { n: pending }); }
    else if (ignored) { setAuditStatus("app.audit.ignoredN", { n: ignored }); }
    else { setAuditStatus("app.audit.none"); }
    auditRender();
  }).catch(function (err) {
    setAuditStatus(null);
    apiErrorToast(err, t("app.audit.checkFail"));
  }).finally(function () { $("btn-audit-check").disabled = false; });
}

var AUDIT_KIND = { bug: "app.audit.kind.bug", flaw: "app.audit.kind.flaw",
                   remark: "app.audit.kind.remark",
                   suggestion: "app.audit.kind.suggestion" };

function auditRender() {
  var box = $("audit-files");
  box.textContent = "";
  var res = state.audits;
  if (!res) { return; }
  var files = res.files || [];
  if (!files.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.audit.empty") }));
  }
  files.forEach(function (f) {
    var card = el("div", { class: "card cr-card" });
    var chip = f.missing ? ["chip-bad", "app.audit.st.missing"]
      : f.modified_after_processing ? ["chip-warn", "app.audit.st.modified"]
      : f.status === "processed" ? ["chip-ok", "app.audit.st.processed"]
      : f.status === "processing" ? ["chip-running", "app.audit.st.processing"]
      : ["chip-info", "app.audit.st.new"];
    card.appendChild(el("div", { class: "cr-head" }, [
      el("span", { class: "chip " + chip[0], text: t(chip[1]) }),
      el("strong", { class: "mono", text: f.file })
    ]));
    var meta = [tf("app.audit.auditedAt", { d: f.audited_at || "?" })];
    if (f.processed_at) {
      meta.push(tf("app.audit.processedAt", { d: f.processed_at }));
    }
    card.appendChild(el("p", { class: "muted small",
                              text: meta.join(" · ") }));
    if (f.status === "new" && !f.missing) {
      var btn = el("button", { class: "btn btn-primary", type: "button",
                               text: t("app.audit.process") });
      btn.addEventListener("click", function () {
        auditProcess(f.file, btn);
      });
      card.appendChild(el("div", { class: "form-row" }, [btn]));
    }
    var a = f.analysis;
    if (a) {
      card.appendChild(el("p", { class: "small", text: a.summary || "" }));
      var counts = {};
      (a.findings || []).forEach(function (x) {
        counts[x.kind] = (counts[x.kind] || 0) + 1;
      });
      var bits = Object.keys(counts).map(function (k) {
        return t(AUDIT_KIND[k] || k) + ": " + counts[k];
      });
      bits.push(tf("app.audit.crsCreated",
                   { n: (f.cr_ids || []).length }));
      card.appendChild(el("p", { class: "muted small",
                                 text: bits.join(" · ") }));
    }
    box.appendChild(card);
  });
  // misnamed files the backend saw and rejected — surface, don't hide
  var ignored = (state.audits && state.audits.ignored) || [];
  if (ignored.length) {
    box.appendChild(el("p", { class: "muted small",
      text: tf("app.audit.ignoredList",
               { files: ignored.slice(0, 10).join(", ") }) }));
  }
}

function auditProcess(name, btn) {
  appConfirm(tf("app.audit.confirmProcess", { f: name })).then(function (y) {
    if (!y) { return; }
    // minutes-long POST: no re-fire (house pattern) — the server also
    // guards with a processing claim, but disabling avoids the duplicate
    // confirm entirely
    if (btn) { btn.disabled = true; }
    setAuditStatus("app.audit.processing");
    api("/audits/" + encodeURIComponent(name) + "/process",
        { method: "POST" })
      .then(function (rec) {
        toast(tf("app.audit.processed",
                 { n: (rec.cr_ids || []).length }), "ok");
        setAuditStatus(null);
        refreshCrs();
      }).catch(function (err) {
        if (btn) { btn.disabled = false; }
        setAuditStatus(null);
        apiErrorToast(err, t("app.audit.processFail"));
      });
  });
}

function qualDemo() {
  var id = state.qualInstance;
  if (!id) { return; }
  $("btn-qual-demo").disabled = true;
  api("/qualifications/" + encodeURIComponent(id) + "/demo", { method: "POST" })
    .then(function () {
      if (state.qualInstance !== id) { return; }
      toast(t("app.qual.demoDone"), "ok");
      loadCompliance(id);
    }).catch(function (err) {
      if (state.qualInstance !== id) { return; }
      apiErrorToast(err, t("app.qual.demoFail"));
    }).finally(function () { $("btn-qual-demo").disabled = false; });
}

function qualApply() {
  var id = state.qualInstance;
  if (!id) { return; }
  $("btn-qual-apply").disabled = true;
  api("/qualifications/" + encodeURIComponent(id) + "/apply", { method: "POST" })
    .then(function (resp) {
      toast(tf("app.qual.applied",
               { id: resp.id, n: resp.grounded_employees }), "ok");
      if (state.qualInstance === id) {
        $("btn-qual-apply").classList.add("hidden");
      }
      refreshInstances();
    }).catch(function (err) {
      apiErrorToast(err, t("app.qual.applyFail"));
    }).finally(function () { $("btn-qual-apply").disabled = false; });
}

function qualClear() {
  var id = state.qualInstance;
  if (!id) { return; }
  appConfirm(t("app.qual.confirmClear"), { danger: true }).then(function (yes) {
    if (!yes) { return; }
    api("/qualifications/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () {
        toast(t("app.qual.cleared"), "ok");
        state.qualComp = null;
        $("qual-summary").classList.add("hidden");
        refreshInstances();
        refreshQuals();
      }).catch(function (err) { apiErrorToast(err, t("app.qual.clearFail")); });
  });
}

/* -------------------------------------- tab: интерактивная доска (#4) */

var BOARD_VIOL = {
  uncovered: "app.board.v.uncovered",
  type_rating: "app.board.v.type",
  unavailable: "app.board.v.unavailable",
  overlap: "app.board.v.overlap",
  rest: "app.board.v.rest",
  unknown_crew: "app.board.v.unknown",
  skill: "app.board.v.skill",
  dup_crew: "app.board.v.dup"
};

function refreshBoard() {
  renderInstancePickers();
  var id = $("board-instance").value;
  if (!id) {
    $("board-body").classList.add("hidden");
    $("board-empty").classList.remove("hidden");
    return;
  }
  state.boardInstance = id;
  state.boardSelected = null;
  // Гонка смены набора (R6, P0-09): старая доска скрывается и её кэш
  // сбрасывается — ходы старого экрана не уедут в новый набор.
  state.board = null;
  $("board-body").classList.add("hidden");
  $("board-body").setAttribute("aria-busy", "true");
  api("/board/" + encodeURIComponent(id)).then(function (b) {
    if (state.boardInstance !== id) { return; }
    $("board-body").removeAttribute("aria-busy");
    b._loadedId = id;
    state.board = b;
    if (!b || b.available !== true) {
      $("board-body").classList.add("hidden");
      $("board-empty").classList.remove("hidden");
      return;
    }
    $("board-body").classList.remove("hidden");
    $("board-empty").classList.add("hidden");
    renderBoard();
    // открытая панель «Сбой» обязана следовать за сменой экземпляра —
    // иначе её события/кнопки бьют по НЕ тому экземпляру, что на экране
    if (!$("dz-box").classList.contains("hidden")) {
      dzFillEmployees();
      $("dz-plan").textContent = "";
      $("btn-dz-apply").classList.add("hidden");
      dzRefresh();
    }
  }).catch(function (err) {
    if (state.boardInstance !== id) { return; }
    if (err.status === 404) {
      $("board-body").classList.add("hidden");
      $("board-empty").classList.remove("hidden");
    } else { apiErrorToast(err, t("app.board.loadFail")); }
  });
}

function renderBoard() {
  var b = state.board;
  if (!b || !b.available) { return; }

  // KPI tiles
  var tiles = $("board-tiles");
  tiles.textContent = "";
  function tile(v, label, cls) {
    tiles.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: String(v) }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  tile(b.violation_count, t("app.board.tile.viol"),
       b.violation_count ? "kpi-warn" : "");
  tile(b.moves, t("app.board.tile.moves"));
  tile(b.pins, t("app.board.tile.pins"));
  tile((b.total_pairings != null ? b.total_pairings : b.pairings.length),
       t("app.board.tile.pairings"));

  $("btn-board-reset").disabled = !b.edited;
  var notes = [];
  if (b.edited) { notes.push(t("app.board.editedNote")); }
  if (b.crew_truncated || b.pairings_truncated) {
    notes.push(tf("app.board.truncated",
                  { crew: b.crew.length, total: b.total_crew }));
  }
  $("board-status").textContent = notes.join("  ");

  // violations summary
  var vbox = $("board-violations");
  vbox.textContent = "";
  if (b.violations.length) {
    vbox.appendChild(el("h4", { text: tf("app.board.violH",
                                         { n: b.violations.length }) }));
    vbox.appendChild(demoLikeTable(
      [el("th", { text: t("app.board.th.type") }),
       el("th", { text: t("app.board.th.pairing") }),
       el("th", { text: t("app.board.th.emp") }),
       el("th", { text: t("app.board.th.detail") })],
      b.violations.slice(0, 50).map(function (v) {
        return el("tr", null, [
          el("td", null, [el("span", { class: "chip chip-bad",
                                       text: t(BOARD_VIOL[v.type] || v.type) })]),
          el("td", { class: "mono small", text: fmt(v.pid) }),
          el("td", { class: "mono small", text: fmt(v.employee) }),
          el("td", { class: "mono small", text: fmt(v.detail) })
        ]);
      })));
  }

  renderBoardGrid();
  renderBoardEditor();
}

/** crew × day grid; each cell shows the pairing covering that day (clickable),
 *  red if that pairing has a violation. */
function renderBoardGrid() {
  var b = state.board;
  // map employee -> day -> [pid...]  (multiple pids/day = a clash to show)
  var byPid = {};
  var cover = {};
  var unassigned = [];
  b.pairings.forEach(function (p) {
    byPid[p.pid] = p;
    // P0-4: пейринг виден в строке КАЖДОГО занятого места (полный состав);
    // пустое место — чип в полосе «не назначено»
    var seats = (p.seats && p.seats.length)
      ? p.seats : [{ employee: p.employee }];
    var emps = {};
    var anyEmpty = false;
    seats.forEach(function (s) {
      if (s.employee) { emps[s.employee] = true; } else { anyEmpty = true; }
    });
    if (anyEmpty || !Object.keys(emps).length) { unassigned.push(p); }
    Object.keys(emps).forEach(function (e) {
      cover[e] = cover[e] || {};
      p.days.forEach(function (d) {
        (cover[e][d] = cover[e][d] || []).push(p.pid);
      });
    });
  });

  var head = $("board-head");
  head.textContent = "";
  head.appendChild(el("th", { class: "board-crewhead",
                              text: t("app.board.crew") }));
  b.days.forEach(function (d) {
    head.appendChild(el("th", { class: "board-dayhead",
                                text: String(parseInt(d.slice(8), 10)) }));
  });

  function chipFor(p) {
    var bad = p.violations && p.violations.length;
    var chip = el("div", {
      class: "board-chip" + (bad ? " board-bad" : "") +
             (p.locked ? " board-locked" : "") +
             (p.moved ? " board-moved" : "") +
             (state.boardSelected === p.pid ? " board-sel" : ""),
      title: p.pid + (p.locked ? " 🔒" : ""),
      "aria-label": p.pid +
        (p.locked ? " — " + t("app.board.ed.lockedNote") : "") +
        (p.moved ? " — " + t("app.board.moved") : ""),
      text: p.locked ? "🔒" : (p.moved ? "✎" : "•")
    });
    var select = function () {
      state.boardSelected = p.pid;
      renderBoardEditor(); renderBoardGrid();
    };
    chip.addEventListener("click", select);
    keyActivate(chip, function () {
      select();
      // the re-render just replaced this node — move focus to the newly
      // selected chip so keyboard position is not reset to <body>
      var again = document.querySelector(".board-chip.board-sel");
      if (again) { again.focus(); }
    });
    return chip;
  }

  var tb = $("board-tbody");
  tb.textContent = "";

  // an "unassigned" lane at the top so an unassign is reversible (audit
  // 2026-07-15) — one chip per unassigned pairing on its first day cell.
  if (unassigned.length) {
    var uMap = {};
    unassigned.forEach(function (p) {
      (uMap[p.days[0]] = uMap[p.days[0]] || []).push(p);
    });
    var utr = el("tr", null, [el("td", { class: "board-crew board-unassigned",
      text: t("app.board.unassigned") })]);
    b.days.forEach(function (d) {
      var cell = el("td", { class: "board-cell" });
      (uMap[d] || []).forEach(function (p) { cell.appendChild(chipFor(p)); });
      utr.appendChild(cell);
    });
    tb.appendChild(utr);
  }

  b.crew.forEach(function (c) {
    var tr = el("tr", null, [
      el("td", { class: "board-crew", title: (c.base || "") + " · " + c.role },
         [el("span", { class: "mono small", text: c.id }),
          el("span", { class: "muted small", text: " " + fmt(c.base) })])
    ]);
    var dayMap = cover[c.id] || {};
    b.days.forEach(function (d) {
      var pids = dayMap[d] || [];
      var cell = el("td", { class: "board-cell" +
                            (pids.length > 1 ? " board-clash" : "") });
      pids.forEach(function (pid) { cell.appendChild(chipFor(byPid[pid])); });
      tr.appendChild(cell);
    });
    tb.appendChild(tr);
  });
}

function renderBoardEditor() {
  var box = $("board-editor");
  box.textContent = "";
  var b = state.board;
  var pid = state.boardSelected;
  if (!pid) { box.classList.add("hidden"); return; }
  var p = b.pairings.find(function (x) { return x.pid === pid; });
  if (!p) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");

  var card = el("div", { class: "card board-editor" });
  card.appendChild(el("div", { class: "board-editor-head" }, [
    el("strong", { text: tf("app.board.ed.title", { pid: pid }) }),
    el("button", { class: "btn btn-ghost btn-small", text: "✕",
                   title: t("ui.close"), "aria-label": t("ui.close") })
  ]));
  card.querySelector("button").addEventListener("click", function () {
    state.boardSelected = null; renderBoardEditor(); renderBoardGrid();
  });

  // pairing facts
  var kv = el("dl", { class: "kv" });
  function row(k, v) {
    kv.appendChild(el("div", { class: "kv-row" },
                      [el("dt", { text: k }), el("dd", { text: v })]));
  }
  row(t("app.board.ed.crew"), fmt(p.employee) +
      (p.employee !== p.solver_employee
       ? " (" + tf("app.board.ed.was", { e: fmt(p.solver_employee) }) + ")"
       : ""));
  // P0-4: полный состав экипажа пейринга (место: роль → сотрудник)
  if (p.seats && p.seats.length > 1) {
    row(t("app.board.ed.seats"), p.seats.map(function (s) {
      return (s.requiredSkill || "—") + " → " + fmt(s.employee);
    }).join("; "));
  }
  row(t("app.board.ed.aircraft"), (p.aircraft || []).join(", ") || "—");
  row(t("app.board.ed.legs"), p.nlegs + " · " + fmtDay(p.days[0]) + " – " +
      fmtDay(p.days[p.days.length - 1]));
  if (p.violations && p.violations.length) {
    row(t("app.board.ed.viol"), p.violations.map(function (v) {
      return t(BOARD_VIOL[v] || v);
    }).join(", "));
  }
  card.appendChild(kv);

  // ПРИЧИНА переделки — обязательна (контур «Отклик планировщика»):
  // правка без причины как сигнал бесполезна, поэтому «Применить»
  // неактивно, пока причина не выбрана. Коды — закрытый набор
  // planner_feedback.REASONS; подписи здесь свои, чтобы экран оставался
  // пятиязычным.
  var reasonSel = el("select", { id: "board-reason" });
  reasonSel.appendChild(el("option", { value: "",
    text: t("app.board.ed.reasonPick") }));
  BOARD_REASONS.forEach(function (code) {
    reasonSel.appendChild(el("option", { value: code,
      text: t("app.loops.reason." + code) }));
  });
  var reasonText = el("input", { id: "board-reason-text", type: "text",
    maxlength: "200", placeholder: t("app.board.ed.reasonText") });
  var applyButtons = [];
  function syncApplyEnabled() {
    applyButtons.forEach(function (b) {
      b.disabled = !!p.locked || !reasonSel.value;
    });
  }
  reasonSel.addEventListener("change", syncApplyEnabled);
  var reasonRow = el("div", { class: "board-editor-reason" }, [
    el("span", { class: "muted small", text: t("app.board.ed.reason") }),
    reasonSel, reasonText
  ]);
  card.appendChild(reasonRow);

  // controls: reassign dropdown (ALL crew, incl. bench) + unassign + lock
  var controls = el("div", { class: "board-editor-ctrls" });
  var sel = el("select", { id: "board-reassign" });
  sel.appendChild(el("option", { value: "", text: t("app.board.ed.unassign") }));
  var pool = b.crew_all || b.crew;
  // P0-4: у multi-crew пейринга — редактор ПО МЕСТАМ: своя строка на
  // каждое место (роль → кандидаты с этой ролью → Apply шлёт slot);
  // single-crew путь остаётся прежним (плоский pid = первое место).
  function fillOptions(selNode, current, req) {
    var lp = b.crew_all || b.crew;
    if (req) {
      lp = lp.filter(function (c) {
        return c.id === current || (c.skills || []).indexOf(req) >= 0;
      });
    }
    var found = false;
    lp.forEach(function (c) {
      var o = el("option", { value: c.id,
        text: c.id + " (" + fmt(c.base) + ")" });
      if (c.id === current) { o.setAttribute("selected", "selected");
                              found = true; }
      selNode.appendChild(o);
    });
    // if the current employee is off the board (truncated), show them so
    // Apply does not silently default to unassign (audit 2026-07-15).
    if (current && !found) {
      var o2 = el("option", { value: current,
        text: current + " (" + t("app.board.ed.offboard") + ")" });
      o2.setAttribute("selected", "selected");
      selNode.appendChild(o2);
    }
  }
  var multiSeat = p.seats && p.seats.length > 1;
  if (multiSeat) {
    p.seats.forEach(function (s) {
      var srow = el("div", { class: "board-editor-seat" });
      srow.appendChild(el("span", { class: "muted small",
        text: (s.requiredSkill || "—") + ": " }));
      var ssel = el("select");
      ssel.appendChild(el("option", { value: "",
        text: t("app.board.ed.unassign") }));
      fillOptions(ssel, s.employee, s.requiredSkill);
      var sbtn = el("button", { class: "btn btn-primary btn-small",
                                text: t("app.board.ed.apply") });
      applyButtons.push(sbtn);
      sbtn.addEventListener("click", function () {
        boardMove(pid, ssel.value || null, s.slot,
                  reasonSel.value, reasonText.value);
      });
      srow.appendChild(ssel);
      srow.appendChild(sbtn);
      controls.appendChild(srow);
    });
  }
  var reqSkill = (p.seats && p.seats[0] && p.seats[0].requiredSkill) || null;
  if (!multiSeat) { fillOptions(sel, p.employee, reqSkill); }
  var applyBtn = el("button", { class: "btn btn-primary btn-small",
                                text: t("app.board.ed.apply") });
  applyButtons.push(applyBtn);
  applyBtn.addEventListener("click", function () {
    boardMove(pid, $("board-reassign").value || null, null,
              reasonSel.value, reasonText.value);
  });
  var lockBtn = el("button", { class: "btn btn-ghost btn-small",
    text: p.locked ? t("app.board.ed.unlock") : t("app.board.ed.lock") });
  lockBtn.addEventListener("click", function () {
    boardPin(pid, !p.locked);
  });
  if (!multiSeat) {
    controls.appendChild(sel);
    controls.appendChild(applyBtn);
  }
  controls.appendChild(lockBtn);
  if (p.locked) {
    controls.appendChild(el("span", { class: "muted small",
                                      text: t("app.board.ed.lockedNote") }));
  }
  card.appendChild(controls);
  syncApplyEnabled();       // все кнопки применения собраны — заблокировать
  box.appendChild(card);
}

/** Closed set of override reasons (mirrors planner_feedback.REASONS —
 *  the CODES are the contract, the labels live in i18n). */
var BOARD_REASONS = ["missing_rule", "wrong_cost", "crew_request",
                     "ops_decision", "data_error", "other"];

function boardMove(pid, emp, slot, reason, reasonText) {
  var id = state.boardInstance;
  // Ход отправляется только для доски, которая РЕАЛЬНО на экране (R6,
  // P0-09): pid из старой отрисовки не должен уехать в новый набор.
  if (!state.board || state.board._loadedId !== id) { return; }
  var body = { pid: pid, employee: emp };
  if (slot !== undefined && slot !== null) { body.slot = slot; }
  if (reason) { body.reason = reason; }
  if (reasonText) { body.reason_text = reasonText; }
  api("/board/" + encodeURIComponent(id) + "/move", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (b) {
    if (state.boardInstance !== id) { return; }
    state.board = b;
    renderBoard();
    toast(t("app.board.moved"), "ok");
  }).catch(function (err) { apiErrorToast(err, t("app.board.moveFail")); });
}

function boardPin(pid, locked) {
  var id = state.boardInstance;
  api("/board/" + encodeURIComponent(id) + "/pin", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pid: pid, locked: locked })
  }).then(function (b) {
    if (state.boardInstance !== id) { return; }
    state.board = b;
    renderBoard();
    toast(locked ? t("app.board.pinned") : t("app.board.unpinned"), "ok");
  }).catch(function (err) { apiErrorToast(err, t("app.board.pinFail")); });
}

function boardReset() {
  var id = state.boardInstance;
  if (!id) { return; }
  appConfirm(t("app.board.confirmReset"), { danger: true }).then(function (yes) {
    if (!yes) { return; }
    api("/board/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () {
        toast(t("app.board.reset"), "ok");
        state.boardSelected = null;
        refreshBoard();
      }).catch(function (err) { apiErrorToast(err, t("app.board.resetFail")); });
  });
}

/* -------------------------------------- disruption recovery (режим «Сбой») */

function dzFillEmployees() {
  // employee picker from the loaded board's crew
  var sel = $("dz-employee");
  sel.textContent = "";
  ((state.board && state.board.crew) || []).forEach(function (c) {
    // "id — имя", но без дубля, когда имя совпадает с идентификатором
    // (эталонные данные GERAD именуют экипаж по id)
    var label = c.id + (c.name && c.name !== c.id ? " — " + c.name : "");
    sel.appendChild(el("option", { value: c.id, text: label }));
  });
}

function dzToggle() {
  var box = $("dz-box");
  var show = box.classList.contains("hidden");
  box.classList.toggle("hidden", !show);
  if (show) {
    dzFillEmployees();
    // браузерное восстановление формы может вернуть dz-kind без события
    // change — пересинхронизировать видимый ряд с фактическим значением
    dzKindChange();
    dzRefresh();
  } else if (dzPollTimer) {
    clearTimeout(dzPollTimer);
    dzPollTimer = null;
  }
}

function dzRefresh() {
  var id = state.boardInstance;
  if (!id) { return; }
  api("/disruption/" + encodeURIComponent(id)).then(function (imp) {
    if (state.boardInstance !== id) { return; }
    dzRenderEvents(imp);
  }).catch(function (err) { apiErrorToast(err, t("app.dz.loadFail")); });
}

function dzKindChange() {
  var kind = $("dz-kind").value;
  $("dz-row-sick").classList.toggle("hidden", kind !== "sick");
  $("dz-row-flights").classList.toggle("hidden", kind !== "flights");
  $("dz-row-aircraft").classList.toggle("hidden", kind !== "aircraft");
  $("dz-row-delay").classList.toggle("hidden", kind !== "delay");
}

/** Событие уровня рейсов (фаза B): отмена / борт / сдвиг. */
function dzAddEvent(body) {
  var id = state.boardInstance;
  if (!id) { return; }
  if ((body.kind === "flights" || body.kind === "delay")
      && !String(body.fns || "").trim()) {
    toast(t("app.dz.needFields"), "error");
    return;
  }
  if (body.kind === "aircraft"
      && !(body.tail && body.day_from && body.day_to)) {
    toast(t("app.dz.needFields"), "error");
    return;
  }
  api("/disruption/" + encodeURIComponent(id) + "/event", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (imp) {
    toast(t("app.dz.marked"), "ok");
    $("dz-fns").value = ""; $("dz-delay-fns").value = "";
    $("dz-tail").value = "";
    dzRenderEvents(imp);
  }).catch(function (err) { apiErrorToast(err, t("app.dz.markFail")); });
}

/** Одна строка-чип на событие любого вида. */
// Трек occurrence-идентичности: addr события может быть flightLegId
// повторного номера — показываем человеку «номер · дата время».
var dzOccByAddr = {};

function dzAddrLabel(addr) {
  var o = dzOccByAddr[addr];
  if (!o) { return addr; }
  var dep = String(o.dep_utc || "").replace("T", " ").slice(5, 16);
  return o.fn + " · " + dep;
}

function dzFnsLabel(fns) {
  return (fns || []).map(dzAddrLabel).join(", ");
}

function dzEventRow(ev) {
  var parts;
  if (ev.kind === "flights") {
    parts = [el("span", { class: "chip chip-warn",
                          text: t("app.dz.ev.flights") }),
             el("span", { class: "mono small",
                          text: dzFnsLabel(ev.fns) })];
  } else if (ev.kind === "aircraft") {
    parts = [el("span", { class: "chip chip-warn",
                          text: t("app.dz.ev.aircraft") }),
             el("span", { class: "mono small", text: ev.tail }),
             el("span", { class: "small",
                          text: ev.day_from + " — " + ev.day_to }),
             el("span", { class: "muted small",
                          text: tf("app.dz.ev.nFlights",
                                   { n: (ev.fns || []).length }) })];
  } else if (ev.kind === "delay") {
    parts = [el("span", { class: "chip chip-warn",
                          text: t("app.dz.ev.delay") }),
             el("span", { class: "mono small",
                          text: dzFnsLabel(ev.fns) }),
             // dir=ltr: в RTL-раскладке знак у числа иначе уезжает
             // на другую сторону (+30 → 30+)
             el("span", { class: "small", dir: "ltr",
                          text: (ev.shift_min > 0 ? "+" : "")
                                + ev.shift_min + " " + t("app.dz.min") })];
  } else {
    parts = [el("span", { class: "chip chip-bad", text: t("app.dz.sick") }),
             el("span", { class: "mono small", text: ev.employee }),
             el("span", { class: "small",
                          text: ev.day_from + " — " + ev.day_to })];
  }
  parts.push(el("span", { class: "muted small", text: ev.note || "" }));
  var row = el("div", { class: "dz-event" }, parts);
  var del = el("button", { class: "btn btn-ghost btn-mini",
                           type: "button", text: "✕",
                           title: t("app.dz.remove"),
                           "aria-label": t("app.dz.remove") });
  del.addEventListener("click", function () { dzRemove(ev.id); });
  row.appendChild(del);
  return row;
}

function dzRenderEvents(imp) {
  var box = $("dz-events");
  box.textContent = "";
  // карта вхождений повторных номеров: addr(flightLegId) -> {fn, dep_utc}
  dzOccByAddr = {};
  ((imp && imp.occurrences) || []).forEach(function (o) {
    dzOccByAddr[o.addr] = o;
  });
  var occ = (imp && imp.occurrences) || [];
  var hint = $("dz-occ-hint");
  if (occ.length) {
    if (!hint) {
      hint = el("p", { class: "muted small", id: "dz-occ-hint" });
      box.parentNode.insertBefore(hint, box);
    }
    hint.textContent = t("app.dz.occHint") + " " + occ.map(function (o) {
      return dzAddrLabel(o.addr) + " → " + o.addr;
    }).join("; ");
  } else if (hint) {
    hint.remove();
  }
  var events = (imp && imp.events) || [];
  if (!events.length) {
    box.appendChild(el("p", { class: "muted small",
                              text: t("app.dz.noEvents") }));
  }
  events.forEach(function (ev) { box.appendChild(dzEventRow(ev)); });
  var broken = (imp && imp.broken) || [];
  $("dz-status").textContent =
    broken.length ? tf("app.dz.brokenN", { n: broken.length }) : "";
  dzRenderSurgery(imp);
}

/** «Было»-строка хирургии + кнопка пересборки + отчёт/поллинг. */
function dzRenderSurgery(imp) {
  var s = (imp && imp.surgery) || { active: false };
  var line = $("dz-surgery");
  var btn = $("btn-dz-rebuild");
  line.classList.toggle("hidden", !s.active);
  btn.classList.toggle("hidden", !s.active);
  if (s.active) {
    line.textContent = s.error
      ? s.error
      : tf("app.dz.surgery", {
          c: (s.cancelled || []).length,
          d: Object.keys(s.delayed || {}).length,
          b: (s.broken || []).length,
          r: s.recover_legs || 0 });
  }
  dzRenderRebuild((imp && imp.rebuild) || { status: "none" });
}

var dzPollTimer = null;

/** Один тик поллинга пересборки. Живучесть: временный сбой сети НЕ
 *  убивает цикл (переарм в catch); цикл сам останавливается при смене
 *  экземпляра и при скрытой панели (незачем опрашивать невидимое). */
function dzScheduleRebuildPoll() {
  var id = state.boardInstance;
  dzPollTimer = setTimeout(function () {
    if (state.boardInstance !== id) { return; }
    if ($("dz-box").classList.contains("hidden")) { return; }
    api("/disruption/" + encodeURIComponent(id) + "/rebuild")
      .then(function (st) {
        if (state.boardInstance !== id) { return; }
        dzRenderRebuild(st);
      })
      .catch(function () { dzScheduleRebuildPoll(); });
  }, 4000);
}

function dzRenderRebuild(rb) {
  if (dzPollTimer) { clearTimeout(dzPollTimer); dzPollTimer = null; }
  var box = $("dz-rebuild-report");
  box.textContent = "";
  if (!rb || rb.status === "none") { return; }
  if (rb.status === "running") {
    box.appendChild(el("p", { class: "small",
                              text: t("app.dz.rebuildRunning") }));
    dzScheduleRebuildPoll();
    return;
  }
  if (rb.status !== "succeeded" || !rb.report) {
    box.appendChild(el("p", { class: "banner-warn small",
                              text: t("app.dz.rebuildFailed")
                                    + (rb.job_id ? " · " + rb.job_id : "") }));
    return;
  }
  var rep = rb.report;
  var after = rep.after || {}, before = rep.before || {};
  box.appendChild(el("h4", { text: t("app.dz.rep.h") }));
  var modeKey = rep.mode === "timefold"
    ? "app.dz.rep.mode.timefold" : "app.dz.rep.mode.greedy";
  box.appendChild(el("p", { class: "small" }, [
    el("span", { class: "chip chip-ok", text: t(modeKey) }),
    el("span", { text: " " + tf("app.dz.rep.stats", {
        a: before.pairings_total || 0,
        b: after.pairings_total || 0,
        p: after.pinned || 0,
        n: (after.new_pairings || []).length }) })
  ]));
  var gate = rep.stage2 && rep.stage2.result_status;
  if (gate && gate !== "OK") {
    box.appendChild(el("p", { class: "banner-warn small",
      text: tf("app.dz.rep.gate", { s: gate }) }));
  }
  // фаза C: раунды расширения окрестности и размороженные пейринги
  var exp = rep.expansion || {};
  if ((exp.unfrozen || []).length) {
    box.appendChild(el("p", { class: "muted small",
      text: tf("app.dz.rep.expand", {
        r: exp.rounds || 1,
        n: exp.unfrozen.length,
        pids: exp.unfrozen.map(function (u) {
          return u.pid + (u.employee ? " (" + u.employee + ")" : "");
        }).join(", ") }) }));
  }
  var dissolved = (before.broken || []).map(function (b) {
    return b.pid + (b.employee ? " (" + b.employee + ")" : "");
  });
  if (dissolved.length) {
    box.appendChild(el("p", { class: "muted small",
      text: tf("app.dz.rep.dissolved",
               { n: dissolved.length, pids: dissolved.join(", ") }) }));
  }
  if ((after.new_pairings || []).length) {
    box.appendChild(demoLikeTable(
      [el("th", { text: t("app.board.th.pairing") }),
       el("th", { text: t("app.dz.th.legs") }),
       el("th", { text: t("app.dz.th.emp") })],
      after.new_pairings.map(function (p) {
        return el("tr", null, [
          el("td", { class: "mono small", text: p.pid }),
          el("td", { class: "mono small", text: (p.fns || []).join(", ") }),
          el("td", { class: "mono small",
                     text: p.employee || t("app.dz.rep.noCrew") })
        ]);
      })));
  }
  if ((after.uncovered_legs || []).length) {
    var proven = (after.proven_impossible || []).length;
    box.appendChild(el("p", { class: "banner-warn small",
      text: tf("app.dz.rep.uncovered",
               { n: after.uncovered_legs.length,
                 fns: after.uncovered_legs.join(", ") })
            + (proven ? " " + tf("app.dz.rep.proven", { n: proven }) : "") }));
  }
  if ((after.unassigned || []).length) {
    box.appendChild(el("p", { class: "banner-warn small",
      text: tf("app.dz.rep.unassigned",
               { n: after.unassigned.length,
                 pids: after.unassigned.join(", ") }) }));
  }
  (rep.crew_changes || []).forEach(function (c) {
    var bits = [];
    (c.lost || []).forEach(function (p) { bits.push("−" + p); });
    (c.gained || []).forEach(function (p) { bits.push("+" + p); });
    box.appendChild(el("p", { class: "muted small mono",
                              text: c.employee + ": " + bits.join(", ") }));
  });
  var open = el("button", { class: "btn btn-primary", type: "button",
                            text: t("app.dz.rep.open") });
  open.addEventListener("click", function () {
    var derivedId = rep.derived && rep.derived.id;
    var sel = $("board-instance");
    function hasOption() {
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === derivedId) { return true; }
      }
      return false;
    }
    if (hasOption()) {
      sel.value = derivedId;
      refreshBoard();
      return;
    }
    // список экземпляров ещё не знает о новом — обновить и выбрать;
    // если и после обновления его нет (сбой сети, экземпляр удалён) —
    // честная ошибка, а не тихий прыжок на первый попавшийся
    refreshInstances().then(function () {
      if (!hasOption()) {
        toast(t("app.dz.rep.openFail"), "error");
        return;
      }
      sel.value = derivedId;
      refreshBoard();
    });
  });
  box.appendChild(el("div", { class: "form-row" }, [open]));
}

/** Запуск пересборки (план Б). */
function dzRebuild() {
  var id = state.boardInstance;
  if (!id) { return; }
  appConfirm(t("app.dz.rebuildConfirm")).then(function (yes) {
    if (!yes) { return; }
    $("btn-dz-rebuild").disabled = true;
    api("/disruption/" + encodeURIComponent(id) + "/rebuild", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    }).then(function (st) {
      toast(t("app.dz.rebuildStarted"), "ok");
      dzRenderRebuild(st);
    }).catch(function (err) { apiErrorToast(err, t("app.dz.rebuildFail")); })
      .finally(function () { $("btn-dz-rebuild").disabled = false; });
  });
}

function dzMarkSick() {
  var id = state.boardInstance;
  if (!id) { return; }
  var body = { employee: $("dz-employee").value,
               day_from: $("dz-from").value, day_to: $("dz-to").value,
               note: $("dz-note").value.trim() };
  if (!body.employee || !body.day_from || !body.day_to) {
    toast(t("app.dz.needFields"), "error");
    return;
  }
  $("btn-dz-sick").disabled = true;
  api("/disruption/" + encodeURIComponent(id) + "/sick", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (imp) {
    toast(t("app.dz.marked"), "ok");
    $("dz-note").value = "";
    dzRenderEvents(imp);
    $("dz-plan").textContent = "";
    $("btn-dz-apply").classList.add("hidden");
    refreshBoard();                 // sickness shows up as violations
  }).catch(function (err) { apiErrorToast(err, t("app.dz.markFail")); })
    .finally(function () { $("btn-dz-sick").disabled = false; });
}

function dzRemove(evId) {
  var id = state.boardInstance;
  if (!id) { return; }
  api("/disruption/" + encodeURIComponent(id) + "/" +
      encodeURIComponent(evId), { method: "DELETE" })
    .then(function (imp) {
      dzRenderEvents(imp);
      $("dz-plan").textContent = "";
      $("btn-dz-apply").classList.add("hidden");
      refreshBoard();
    }).catch(function (err) { apiErrorToast(err, t("app.dz.removeFail")); });
}

var DZ_VIA = { direct: "app.dz.via.direct", swap_in: "app.dz.via.swap",
               swap_out: "app.dz.via.swapAux" };

function dzPlan() {
  var id = state.boardInstance;
  if (!id) { return; }
  $("btn-dz-plan").disabled = true;
  $("dz-status").textContent = t("app.dz.planning");
  api("/disruption/" + encodeURIComponent(id) + "/plan", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({})
  }).then(function (plan) {
    dzRenderPlan(plan);
  }).catch(function (err) { apiErrorToast(err, t("app.dz.planFail")); })
    .finally(function () {
      $("btn-dz-plan").disabled = false;
      $("dz-status").textContent = "";
    });
}

function dzRenderPlan(plan) {
  var box = $("dz-plan");
  box.textContent = "";
  if (!plan || plan.available !== true) { return; }
  var stats = [
    tf("app.dz.stat.broken", { n: plan.broken_count }),
    tf("app.dz.stat.changes", { n: plan.changes_count }),
    tf("app.dz.stat.crew", { n: plan.affected_crew }),
    tf("app.dz.stat.viol", { a: plan.violations_before,
                             b: plan.violations_after })
  ];
  box.appendChild(el("p", { class: "small", text: stats.join(" · ") }));
  if (plan.uncovered.length) {
    box.appendChild(el("p", { class: "banner-warn small",
      text: tf("app.dz.uncovered",
               { n: plan.uncovered.length,
                 pids: plan.uncovered.slice(0, 8).join(", ") }) }));
  }
  if (plan.repairs.length) {
    box.appendChild(demoLikeTable(
      [el("th", { text: t("app.board.th.pairing") }),
       el("th", { text: t("app.dz.th.from") }),
       el("th", { text: t("app.dz.th.to") }),
       el("th", { text: t("app.dz.th.via") }),
       el("th", { text: t("app.dz.th.credit") })],
      plan.repairs.map(function (m) {
        // P0-4: для multi-crew пейринга показываем место (slot); у
        // single-crew slot=0 не загромождаем колонку
        var pidTxt = m.pid + (m.slot ? " · " + t("app.board.ed.seat") +
                                       " " + m.slot : "");
        return el("tr", null, [
          el("td", { class: "mono small", text: pidTxt }),
          el("td", { class: "mono small", text: fmt(m.from) }),
          el("td", { class: "mono small", text: m.to }),
          el("td", { class: "small",
                     text: t(DZ_VIA[m.via] || m.via) +
                           (m.unpin ? " · " + t("app.dz.unpinned") : "") }),
          el("td", { class: "small", text: String(m.credit_h) })
        ]);
      })));
  }
  $("btn-dz-apply").classList.toggle("hidden", !plan.changes_count);
}

function dzApply() {
  var id = state.boardInstance;
  if (!id) { return; }
  appConfirm(t("app.dz.confirmApply")).then(function (yes) {
    if (!yes) { return; }
    $("btn-dz-apply").disabled = true;
    api("/disruption/" + encodeURIComponent(id) + "/apply", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    }).then(function (res) {
      toast(tf("app.dz.applied", { n: res.applied_moves }), "ok");
      $("dz-plan").textContent = "";
      $("btn-dz-apply").classList.add("hidden");
      refreshBoard();
      dzRefresh();
    }).catch(function (err) { apiErrorToast(err, t("app.dz.applyFail")); })
      .finally(function () { $("btn-dz-apply").disabled = false; });
  });
}

/* ------------------------------------------- tab 4: формат и конфигурация */

var CONFIG_PARAMS = [
  ["min_sit_min", "app.cfg.min_sit_min", 30],
  ["max_landings_per_duty", "app.cfg.max_landings_per_duty", 5],
  ["max_duty_span_min", "app.cfg.max_duty_span_min", 840],
  ["min_rest_min", "app.cfg.min_rest_min", 600],
  ["max_monthly_block_min", "app.cfg.max_monthly_block_min", 5100],
  ["max_pairing_span_min", "app.cfg.max_pairing_span_min", 5760],
  // Правила состава экипажа (аудит-2 P2-05): 0 = выключено (дефолт).
  ["comp_no_two_reserves", "app.cfg.comp_no_two_reserves", 0],
  ["comp_min_pair_seniority_weight", "app.cfg.comp_min_pair_seniority_weight", 0],
  ["comp_language_mix", "app.cfg.comp_language_mix", 0]
];

/** "default" / "changed" chip per parameter vs the built-in defaults
 *  (non-default values change the problem fingerprint — worth seeing). */
function updateConfigChips() {
  CONFIG_PARAMS.forEach(function (spec) {
    var key = spec[0], dflt = spec[2];
    var input = $("cfg-" + key);
    var chip = $("chip-" + key);
    if (!input || !chip) { return; }
    var isDefault = parseInt(input.value, 10) === dflt;
    chip.textContent = isDefault ? t("app.chip.default") : t("app.chip.changed");
    chip.className = "chip " + (isDefault ? "chip-neutral" : "chip-changed");
    chip.title = isDefault
      ? tf("app.chip.defaultTitle", { v: dflt })
      : tf("app.chip.changedTitle", { v: dflt });
  });
}

function renderConfigForm(parameters, enforcement) {
  var grid = $("config-grid");
  grid.textContent = "";
  CONFIG_PARAMS.forEach(function (spec) {
    var key = spec[0], label = t(spec[1]), dflt = spec[2];
    var value = (parameters && parameters[key] !== undefined && parameters[key] !== null)
      ? parameters[key] : dflt;
    var input = el("input", {
      type: "number", id: "cfg-" + key, value: String(value), step: "1", min: "0"
    });
    input.addEventListener("input", updateConfigChips);
    var chip = el("span", { class: "chip chip-neutral", id: "chip-" + key,
                            text: t("app.chip.default") });
    var hint = [el("span", { text: key + " " }), chip];
    // Правило, которое солвер НЕ ищет, а сертификат лишь проверяет:
    // включив его, оператор получит повторяющийся отказ вердикта без
    // автоматического пути к легальному ростеру. Без этой пометки отказ
    // читается как поломка солвера.
    if (enforcement && enforcement[key] === "certificate") {
      hint.push(el("span", { class: "chip chip-warn", id: "cert-" + key,
                             title: t("app.cfg.certOnly.hint"),
                             text: t("app.cfg.certOnly") }));
    }
    grid.appendChild(el("div", { class: "form-row" }, [
      el("label", { for: "cfg-" + key, text: label }),
      input,
      el("span", { class: "hint mono cfg-hint" }, hint)
    ]));
  });
  updateConfigChips();
}

/** i18n key of the current config-status line (re-rendered on language
 *  switch); null = show nothing or a verbatim server note. */
var configStatusKey = null;

function renderConfigStatus() {
  if (configStatusKey) { $("config-status").textContent = t(configStatusKey); }
}

function loadAirlineConfig() {
  api("/airline-config").then(function (cfg) {
    renderConfigForm(cfg && cfg.parameters, cfg && cfg.enforcement);
    // Known note codes are translated; unknown notes shown verbatim.
    if (cfg && cfg.note_code === "rules_env") {
      configStatusKey = "app.rulesNote";
      renderConfigStatus();
    } else if (cfg && cfg.note) {
      configStatusKey = null;
      $("config-status").textContent = cfg.note;
    }
  }).catch(function (err) {
    renderConfigForm(null); // show editable defaults even if GET failed
    apiErrorToast(err, t("app.t.cfgLoad"));
  });
}

function saveAirlineConfig(evt) {
  evt.preventDefault();
  var parameters = {};
  var bad = [];
  CONFIG_PARAMS.forEach(function (spec) {
    var key = spec[0];
    var input = $("cfg-" + key);
    var v = input ? parseInt(input.value, 10) : NaN;
    if (!isFinite(v) || v < 0) { bad.push(t(spec[1])); }
    else { parameters[key] = v; }
  });
  if (bad.length) {
    toast(t("app.t.badValues") + bad.join(", "), "error");
    return;
  }
  $("config-save").disabled = true;
  api("/airline-config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parameters: parameters })
  }).then(function () {
    configStatusKey = "app.cfg.saved";
    renderConfigStatus();
    toast(t("app.t.cfgSaved"), "ok");
    updateConfigChips();
  }).catch(function (err) {
    apiErrorToast(err, t("app.t.cfgSave"));
  }).finally(function () {
    $("config-save").disabled = false;
  });
}

function loadSchema() {
  api("/schema").then(function (schema) {
    // Drop the data-i18n marker: applyI18nDom() must never overwrite the
    // loaded schema with the "Loading…" placeholder on a language switch.
    $("schema-pre").removeAttribute("data-i18n");
    $("schema-pre").textContent = JSON.stringify(schema, null, 2);
  }).catch(function (err) {
    $("schema-pre").removeAttribute("data-i18n");
    $("schema-pre").textContent = t("app.schema.fail");
    apiErrorToast(err, t("app.t.schema"));
  });
}

/* -------------------------------------------- tab 5: Пользователи (admin) */

/** Admin-endpoint errors with a human message. 409 is the last-admin
 *  protection (or a duplicate on create) — explain instead of dumping JSON. */
function adminErrorToast(err, prefix, conflictMessage) {
  if (err.status === 409) {
    var detail = err.body && typeof err.body === "object" && err.body.detail
      ? (typeof err.body.detail === "string"
         ? err.body.detail : JSON.stringify(err.body.detail))
      : null;
    toast(prefix + ": " + (detail || conflictMessage), "error");
    return;
  }
  if (err.status === 404) {
    toast(prefix + ": " + t("app.admin.unavailable"), "error");
    return;
  }
  if (err.status === 403) {
    toast(prefix + ": " + t("app.admin.needAdmin"), "error");
    return;
  }
  apiErrorToast(err, prefix);
}

function usersEmptyRow(text) {
  var tbody = $("users-tbody");
  tbody.textContent = "";
  tbody.appendChild(el("tr", null, [
    el("td", { colspan: "9", class: "empty-row", text: text })
  ]));
}

/** Stats keys the backend may report -> i18n keys; unknown keys fall back
 *  to raw names. */
var STATS_KEYS = {
  users: "app.stats.users",
  users_total: "app.stats.users",
  active: "app.stats.active",
  active_users: "app.stats.active",
  admins: "app.stats.admins",
  disabled: "app.stats.disabled",
  logins: "app.stats.logins",
  logins_total: "app.stats.logins",
  login_count: "app.stats.logins",
  jobs: "app.stats.jobs",
  jobs_run: "app.stats.jobs",
  jobs_total: "app.stats.jobs",
  uploads: "app.stats.uploads",
  uploads_total: "app.stats.uploads",
  users_active: "app.stats.active",
  admins_active: "app.stats.admins"
};

function renderAdminStats(stats) {
  var holder = $("users-stats");
  holder.textContent = "";
  if (!stats || typeof stats !== "object") { return; }
  Object.keys(stats).forEach(function (key) {
    var value = stats[key];
    // Per-user breakdowns (jobs_by_user / uploads_by_user) are dicts —
    // not scalar totals; skip them here (the per-user columns live in the
    // table below), so the cards row stays clean.
    if (value !== null && typeof value === "object") { return; }
    holder.appendChild(el("div", { class: "stat-card" }, [
      el("div", { class: "stat-value", text: fmt(value) }),
      el("div", { class: "stat-label",
                  text: STATS_KEYS[key] ? t(STATS_KEYS[key]) : key })
    ]));
  });
}

function renderUsersTable(users) {
  var tbody = $("users-tbody");
  tbody.textContent = "";
  if (!users.length) {
    usersEmptyRow(t("app.users.none"));
    return;
  }
  users.forEach(function (u) {
    var roleSelect = el("select", { class: "inline-select" }, [
      el("option", { value: "user", text: t("html.role.user") }),
      el("option", { value: "admin", text: t("html.role.admin") })
    ]);
    roleSelect.value = u.role === "admin" ? "admin" : "user";
    roleSelect.addEventListener("change", function () {
      patchUser(u.email, { role: roleSelect.value },
                t("app.t.lastAdminDemote"));
    });

    // Catalog statuses are active|revoked (users.py STATUSES).
    var statusSelect = el("select", { class: "inline-select" }, [
      el("option", { value: "active", text: t("app.userStatus.active") }),
      el("option", { value: "revoked", text: t("app.userStatus.revoked") })
    ]);
    statusSelect.value = u.status === "revoked" ? "revoked" : "active";
    statusSelect.addEventListener("change", function () {
      patchUser(u.email, { status: statusSelect.value },
                t("app.t.lastAdminRevoke"));
    });

    var delBtn = el("button", { class: "btn btn-danger btn-small",
                                text: t("app.btn.deleteUser") });
    delBtn.addEventListener("click", function () {
      appConfirm(tf("app.t.confirmDelete", { e: u.email }), { danger: true })
        .then(function (ok) {
          if (!ok) { return; }
          api("/admin/users/" + encodeURIComponent(u.email), { method: "DELETE" })
            .then(function () {
              toast(t("app.t.userDeleted") + u.email, "ok");
              refreshUsers();
            })
            .catch(function (err) {
              adminErrorToast(err, t("app.t.deleting") + u.email,
                              t("app.t.lastAdminDelete"));
              refreshUsers();
            });
        });
    });

    tbody.appendChild(el("tr", null, [
      el("td", { class: "mono", text: fmt(u.email) }),
      el("td", null, [roleSelect]),
      el("td", null, [statusSelect]),
      el("td", { text: fmtTime(u.created_at) }),
      el("td", { text: fmtTime(u.last_login_at) }),
      el("td", { class: "num", text: fmt(u.login_count) }),
      el("td", { class: "num", text: fmt(u.jobs_run) }),
      el("td", { class: "num", text: fmt(u.uploads) }),
      el("td", null, [delBtn])
    ]));
  });
}

function patchUser(email, patch, conflictMessage) {
  api("/admin/users/" + encodeURIComponent(email), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch)
  }).then(function () {
    toast(t("app.t.saved") + email, "ok");
    refreshUsers();
  }).catch(function (err) {
    adminErrorToast(err, t("app.t.change") + email, conflictMessage);
    refreshUsers(); // re-render so the select snaps back to the server truth
  });
}

function refreshUsers() {
  api("/admin/users").then(function (resp) {
    // Endpoint shape: {users: [...], summary: {...}} (bare-array responses
    // are also tolerated for forward/backward compatibility).
    var list = Array.isArray(resp) ? resp
             : (resp && Array.isArray(resp.users) ? resp.users : []);
    renderUsersTable(list);
    if (resp && resp.summary) { renderAdminStats(resp.summary); }
  }).catch(function (err) {
    if (err.status === 404) {
      usersEmptyRow(t("app.admin.unavailable"));
    } else {
      usersEmptyRow(t("app.users.loadFail"));
      adminErrorToast(err, t("app.t.usersList"));
    }
  });
  api("/admin/stats").then(function (stats) {
    renderAdminStats(stats);
  }).catch(function () {
    // Stats are auxiliary: an absent endpoint must not break the tab.
    $("users-stats").textContent = "";
  });
}

function handleAddUserSubmit(evt) {
  evt.preventDefault();
  var email = $("user-add-email").value.trim().toLowerCase();
  var role = $("user-add-role").value;
  if (!email) { toast(t("app.t.needEmail"), "error"); return; }
  var submitBtn = $("user-add-submit");
  submitBtn.disabled = true;
  api("/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email, role: role })
  }).then(function () {
    toast(t("app.t.userAdded") + email, "ok");
    $("user-add-form").reset();
    refreshUsers();
  }).catch(function (err) {
    adminErrorToast(err, t("app.t.adding") + email, t("app.t.userExists"));
  }).finally(function () {
    submitBtn.disabled = false;
  });
}

/* ------------------------------------------- tab 7: Лаборатория (admin) */

/* Improvement statuses (webapp/evolution.py lifecycle) -> chip classes.
 * The lab is read-only: findings are triaged and promoted in Telegram. */
var EVO_STATUSES = [
  "found", "rejected",
  "approved_for_test", "testing", "test_improved", "test_no_gain",
  "test_failed",
  "approved_for_stage", "staging", "stage_passed", "stage_failed",
  "approved_for_prod", "deploying", "deployed", "deploy_failed"
];
var EVO_OK_STATUSES = ["deployed", "test_improved", "stage_passed"];
// «Отклонена» is amber, not red: it is a considered pause, not a failure —
// the admin can still re-test it later.
var EVO_WARN_STATUSES = ["rejected"];
var EVO_BAD_STATUSES = ["test_failed", "stage_failed", "deploy_failed"];
var EVO_RUNNING_STATUSES = ["testing", "staging", "deploying"];

// grade -> chip colour class (item 1: honest three-way verdict).
var EVO_GRADE_CLS = { full: "chip-ok", partial: "chip-warn", none: "chip-neutral" };

function evoStatusChip(status, grade) {
  // A finished-test row shows the honest grade (Улучшение / Частичное /
  // Нет улучшения) instead of the raw «Test: …» status.
  if (grade) {
    return el("span", { class: "chip " + (EVO_GRADE_CLS[grade] || "chip-neutral"),
                        text: t("app.ev.grade." + grade) });
  }
  var cls = "chip";
  if (EVO_OK_STATUSES.indexOf(status) >= 0) { cls += " chip-ok"; }
  else if (EVO_WARN_STATUSES.indexOf(status) >= 0) { cls += " chip-warn"; }
  else if (EVO_BAD_STATUSES.indexOf(status) >= 0) { cls += " chip-bad"; }
  else if (EVO_RUNNING_STATUSES.indexOf(status) >= 0) { cls += " chip-running"; }
  else { cls += " chip-neutral"; }
  var label = EVO_STATUSES.indexOf(status) >= 0
    ? t("app.evo." + status) : fmt(status);
  return el("span", { class: cls, text: label });
}

function labEmptyRow(text) {
  var tbody = $("lab-tbody");
  tbody.textContent = "";
  tbody.appendChild(el("tr", null, [
    el("td", { colspan: "7", class: "empty-row", text: text })
  ]));
}

/* Which pipeline buttons a finding offers in its current status — the
 * SAME transitions the backend gate allows (evolution.ACTION_VALID_FROM);
 * the server still re-validates, this only avoids dead buttons. */
function evoActionsFor(status) {
  var acts = [];
  if (["found", "rejected", "test_failed",
       "test_no_gain"].indexOf(status) >= 0) {
    acts.push("test");         // a rejected finding can be re-tested later
  }
  if (["test_improved", "test_no_gain",
       "stage_failed"].indexOf(status) >= 0) {
    acts.push("stage");
  }
  if (["stage_passed", "deploy_failed"].indexOf(status) >= 0) {
    acts.push("prod");
  }
  // Rejection: hidden for terminal and in-flight rows (the backend would
  // accept it mid-run, but the running phase cannot be stopped anyway).
  if (["rejected", "deployed", "testing", "staging", "deploying",
       "approved_for_test", "approved_for_stage",
       "approved_for_prod"].indexOf(status) < 0) {
    acts.push("reject");
  }
  return acts;
}

function evoAction(impId, action) {
  var needConfirm = (action === "prod" || action === "reject");
  var gate = needConfirm
    ? appConfirm(t("app.evo.confirm." + action), { danger: action === "reject" })
    : Promise.resolve(true);
  gate.then(function (ok) {
    if (!ok) { return; }
    // Продвижение (Stage/Prod) требует контрподписи человека: токен
    // выписывает оператор `python tools/countersign.py` по секрету,
    // которого нет у автоматики (пакет C). Без токена сервер откажет —
    // спрашиваем сразу.
    if (action === "stage" || action === "prod") {
      var token = window.prompt(t("app.evo.countersign.prompt"), "");
      if (!token || !token.trim()) {
        toast(t("app.evo.countersign.missing"), "warn");
        return;
      }
      evoActionSend(impId, action, false, token.trim());
      return;
    }
    evoActionSend(impId, action);
  });
}

function evoActionSend(impId, action, confirmUnisolated, countersign) {
  var payload = { action: action };
  if (confirmUnisolated) { payload.confirm_unisolated = true; }
  if (countersign) { payload.countersign = countersign; }
  api("/evolution/" + impId + "/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(function () {
    toast(t("app.evo.started." + action), "ok");
    refreshLab();
    if (action !== "reject") {
      // The phase writes the same live feed the scan uses — show it.
      state.labActivitySeq = 0;
      labClearFeed();
      startLabActivityPolling();
    }
  }).catch(function (err) {
    // Неизолированная среда — не отказ, а требование осознанного решения:
    // называем препятствия и спрашиваем подтверждение. Глухой отказ
    // (как было) просто выключал контур самоулучшения.
    var d = err.body && err.body.detail;
    if (err.status === 409 && d && typeof d === "object"
        && d.error === "isolation_unconfirmed") {
      appConfirm(t("app.evo.unisolated.msg"),
                 { details: d.problems || [], danger: true,
                   ok: t("app.evo.unisolated.ok") })
        .then(function (ok) {
          if (ok) { evoActionSend(impId, action, true); }
        });
      return;
    }
    if (err.status === 409 && d && typeof d === "object"
        && d.error === "countersign_required") {
      toast(t("app.evo.countersign.bad")
            + (d.message ? " — " + d.message : ""), "error");
      return;
    }
    if (err.status === 409) {
      toast(t("app.evo.stale"), "warn");
      refreshLab();
    } else {
      apiErrorToast(err, t("app.evo.btn." + action));
    }
  });
}

function renderLabTable(improvements) {
  var tbody = $("lab-tbody");
  tbody.textContent = "";
  state.labTestingId = null;
  if (!improvements.length) {
    labEmptyRow(t("app.lab.none"));
    return;
  }
  improvements.forEach(function (imp) {
    if (imp.status === "testing") { state.labTestingId = imp.id; }
    var reviewed = ["test_improved", "test_no_gain"].indexOf(imp.status) >= 0;
    // Название — в узкой колонке; обрезанное имя при наведении медленно
    // прокручивается по кругу (labMarqueeStart), полностью читаемо.
    var titleNode = imp.source_url
      ? el("a", { href: imp.source_url, target: "_blank", rel: "noopener",
                  text: fmt(imp.title) })
      : el("span", { text: fmt(imp.title) });
    var marqueeItem = el("span", { class: "lab-marquee-item" });
    marqueeItem.appendChild(titleNode);
    var marqueeInner = el("div", { class: "lab-marquee-inner" });
    marqueeInner.appendChild(marqueeItem);
    var marquee = el("div", { class: "lab-marquee" });
    marquee.appendChild(marqueeInner);
    marquee.addEventListener("mouseenter", function () {
      labMarqueeStart(marquee);
    });
    marquee.addEventListener("mouseleave", function () {
      labMarqueeStop(marquee);
    });
    var titleCell = el("td", { class: "lab-title" });
    titleCell.appendChild(marquee);
    var host = "—";
    if (imp.source_url) {
      try { host = new URL(imp.source_url).host; } catch (e) { host = "—"; }
    }
    var actionsCell = el("td", { class: "lab-actions" });
    if (reviewed) {
      var det = el("button", {
        class: "mini-btn evo-btn evo-btn-detail", type: "button",
        text: t("app.ev.detail.btn") });
      det.addEventListener("click", function () { evoShowDetail(imp.id); });
      actionsCell.appendChild(det);
    }
    evoActionsFor(imp.status).forEach(function (a) {
      var btn = el("button", {
        class: "mini-btn evo-btn evo-btn-" + a,
        type: "button",
        text: t("app.evo.btn." + a)
      });
      btn.addEventListener("click", function () { evoAction(imp.id, a); });
      actionsCell.appendChild(btn);
    });
    // Status cell: the state chip plus the analytical deploy recommendation.
    // For reviewed rows the chip itself opens the detail panel (item 3:
    // «по нажатию на результат … подробная информация») — same action as
    // the Детали button, which can sit off-screen in a scrolled table.
    var grade = (reviewed && imp.review) ? imp.review.grade : null;
    var chip = evoStatusChip(imp.status, grade);
    var statusCell = el("td", null, [chip]);
    if (reviewed) {
      // The status chip opens the detail panel; the analytical
      // recommendation lives there, not as a second chip in the row.
      chip.classList.add("chip-click");
      chip.title = t("app.ev.detail.title");
      var openDetail = function () { evoShowDetail(imp.id); };
      chip.addEventListener("click", openDetail);
      keyActivate(chip, openDetail);
    }
    var impactCell;
    if (imp.impact && imp.impact.source === "llm") {
      impactCell = el("td", {
        class: "mono",
        title: (imp.impact.rationale || "") +
          " [" + (imp.impact.confidence || "low") + "]",
        text: String(imp.impact.score)
      });
    } else if (imp.impact && imp.impact.source === "unscored") {
      // Ошибка оценки видима, не немой прочерк: причина — в подсказке.
      impactCell = el("td", {
        class: "muted",
        title: t("app.lab.unscored") + ": " + (imp.impact.error || "?"),
        text: "⚠"
      });
    } else {
      impactCell = el("td", { class: "muted", text: "—" });
    }
    // «Публикация» — дата ПУБЛИКАЦИИ первоисточника (или прочерк);
    // «Найдена» — дата, когда публикацию нашёл агент (created_at).
    var dateCell;
    if (imp.published_at) {
      dateCell = el("td", {
        class: "mono small", title: t("app.lab.pubDate"),
        text: imp.published_at
      });
    } else {
      dateCell = el("td", {
        class: "muted", title: t("app.lab.foundDate"), text: "—"
      });
    }
    var foundCell = el("td", {
      class: "mono small", title: t("app.lab.foundTitle"),
      text: String(imp.created_at || "").slice(0, 10)
    });
    tbody.appendChild(el("tr", null, [
      el("td", { class: "mono", text: fmt(imp.id) }),
      titleCell,
      statusCell,
      el("td", { class: "mono small", text: host }),
      impactCell,
      dateCell,
      foundCell,
      actionsCell
    ]));
  });
}

function labSorted() {
  var list = (state.labItems || []).slice();
  // dir = -1 — по убыванию (новые/большие выше, умолчание), 1 — по
  // возрастанию (повторный щелчок по активному признаку).
  var dir = state.labSortDir === "asc" ? 1 : -1;
  function byCreatedDesc(a, b) {
    return String(b.created_at || "").localeCompare(
      String(a.created_at || ""));
  }
  if (state.labSort === "impact") {
    // без оценки — всегда в конце, независимо от направления
    list.sort(function (a, b) {
      var sa = (a.impact && a.impact.source === "llm")
        ? a.impact.score : null;
      var sb = (b.impact && b.impact.source === "llm")
        ? b.impact.score : null;
      if (sa === null && sb === null) { return byCreatedDesc(a, b); }
      if (sa === null) { return 1; }
      if (sb === null) { return -1; }
      if (sa !== sb) { return dir * (sa - sb); }
      return byCreatedDesc(a, b);
    });
  } else if (state.labSort === "found") {
    // по дате НАХОДКИ (когда публикацию нашёл агент)
    list.sort(function (a, b) {
      var ca = String(a.created_at || "");
      var cb = String(b.created_at || "");
      return ca === cb ? 0 : dir * ca.localeCompare(cb);
    });
  } else {
    // по дате ПУБЛИКАЦИИ первоисточника; без известной даты — всегда в
    // конце, там по дате находки агентом
    list.sort(function (a, b) {
      var pa = a.published_at || "";
      var pb = b.published_at || "";
      if (!pa && !pb) { return byCreatedDesc(a, b); }
      if (!pa) { return 1; }
      if (!pb) { return -1; }
      if (pa !== pb) { return dir * pa.localeCompare(pb); }
      return byCreatedDesc(a, b);
    });
  }
  return list;
}

function labMarqueeStart(wrap) {
  var inner = wrap.firstChild;
  var item = inner.firstChild;
  // прокрутка нужна только обрезанным именам
  if (item.offsetWidth <= wrap.clientWidth + 1) { return; }
  if (!wrap._dup) {
    // второй круг того же имени — лента едет бесшовно
    wrap._dup = item.cloneNode(true);
    inner.appendChild(wrap._dup);
  }
  var dist = item.offsetWidth;               // ширина имени + зазор
  inner.style.setProperty("--marquee-shift", "-" + dist + "px");
  // ~35 пикселей в секунду: медленно, читается целиком; длинное имя
  // едет дольше, короткое — не быстрее 4 секунд на круг
  inner.style.setProperty("--marquee-dur",
                          Math.max(4, dist / 35).toFixed(1) + "s");
  inner.classList.add("scrolling");
}

function labMarqueeStop(wrap) {
  var inner = wrap.firstChild;
  inner.classList.remove("scrolling");
  if (wrap._dup) {
    inner.removeChild(wrap._dup);
    wrap._dup = null;
  }
}

function labSortIndicators() {
  // Активный признак подсвечен и несёт стрелку направления: ↓ — по
  // убыванию, ↑ — по возрастанию.
  document.querySelectorAll("#lab-sort [data-lab-sort]").forEach(
    function (b) {
      var active = b.getAttribute("data-lab-sort") === state.labSort;
      b.classList.toggle("btn-primary", active);
      var arrow = b.querySelector(".lab-sort-arrow");
      if (arrow) {
        arrow.textContent = active
          ? (state.labSortDir === "asc" ? "↑" : "↓") : "";
      }
    });
}

function startLabScorePolling() {
  var banner = $("lab-score-banner");
  var counter = $("lab-score-counter");
  var bar = $("lab-score-bar");
  var btn = $("btn-lab-score");
  if (banner) { banner.classList.remove("hidden"); }
  if (btn) { btn.disabled = true; }
  if (state.labScoreTimer) { clearInterval(state.labScoreTimer); }
  var ticks = 0;
  function tick() {
    api("/evolution/score-status").then(function (st) {
      if (!st) { return; }
      var done = (st.scored || 0) + (st.skipped || 0) + (st.failed || 0);
      var total = st.total || 0;
      if (counter) {
        counter.textContent = total ? (done + " / " + total) : "…";
      }
      if (bar && total) {
        bar.style.width = Math.round(100 * done / total) + "%";
      }
      // Таблица подживляется по ходу: свежие оценки видны сразу.
      ticks += 1;
      if (st.running && ticks % 3 === 0) { refreshLab(); }
      if (!st.running) {
        clearInterval(state.labScoreTimer);
        state.labScoreTimer = null;
        if (banner) { banner.classList.add("hidden"); }
        if (btn) { btn.disabled = false; }
        toast(st.error ? t("app.lab.scoreFail") : t("app.lab.scoreDone"),
              st.error ? "error" : "ok");
        refreshLab();
      }
    }).catch(function () {
      clearInterval(state.labScoreTimer);
      state.labScoreTimer = null;
      if (banner) { banner.classList.add("hidden"); }
      if (btn) { btn.disabled = false; }
    });
  }
  tick();
  state.labScoreTimer = setInterval(tick, 4000);
}

function refreshLab() {
  api("/evolution").then(function (resp) {
    var list = (resp && Array.isArray(resp.improvements))
      ? resp.improvements : [];
    state.labItems = list;
    // Оценку считает ровно один контур (внешний): на остальных кнопка
    // прячется, иначе нажатие затрёт синхронизированные баллы отказом
    // модели. Сервер отказывает независимо — это только подсказка глазу.
    var scoreBtn = $("btn-lab-score");
    if (scoreBtn) {
      var allowed = !resp || resp.scoring_allowed !== false;
      scoreBtn.style.display = allowed ? "" : "none";
      scoreBtn.title = allowed ? "" : (resp.scoring_refused_reason || "");
    }
    renderLabTable(labSorted());
  }).catch(function (err) {
    if (err.status === 404) {
      // Backend without the Evolution pipeline: degrade quietly.
      labEmptyRow(t("app.lab.none"));
      return;
    }
    labEmptyRow(t("app.lab.loadFail"));
    apiErrorToast(err, t("app.lab.loadFail"));
  });
}

/** «Начать R&D»: launch an off-schedule research scan and show the live
 *  activity feed. Findings also arrive in Telegram. */
function triggerLabScan() {
  var btn = $("btn-lab-scan");
  btn.disabled = true;
  api("/evolution/scan", { method: "POST" }).then(function () {
    toast(t("app.lab.scanStarted"), "ok");
    state.labActivitySeq = 0;
    labClearFeed();
    startLabActivityPolling();
  }).catch(function (err) {
    if (err.status === 409) {
      toast(t("app.lab.scanBusy"), "warn");
      startLabActivityPolling();   // a scan is already running — show it
    } else {
      apiErrorToast(err, t("html.lab.scan"));
    }
  }).finally(function () {
    btn.disabled = false;
  });
}

/* --- live activity feed ---------------------------------------------- */

/* kind -> icon + i18n label key. The detail (query/url/snippet) is raw
 * agent data and rendered verbatim. */
var LAB_ACT_KINDS = {
  start:    { icon: "▶", key: "app.evo.act.start" },
  note:     { icon: "•", key: "app.evo.act.note" },
  search:   { icon: "🔎", key: "app.evo.act.search" },
  fetch:    { icon: "🌐", key: "app.evo.act.fetch" },
  tool:     { icon: "🛠", key: "app.evo.act.tool" },
  thinking: { icon: "💭", key: "app.evo.act.thinking" },
  finding:  { icon: "✨", key: "app.evo.act.finding" },
  bench:    { icon: "📊", key: "app.evo.act.bench" },
  done:     { icon: "✅", key: "app.evo.act.done" },
  error:    { icon: "⚠", key: "app.evo.act.error" }
};

function renderActivityEvent(evt) {
  var meta = LAB_ACT_KINDS[evt.kind] || { icon: "•", key: null };
  var li = el("li", { class: "lab-act lab-act-" + evt.kind }, [
    el("span", { class: "lab-act-kind", text: meta.icon + " " +
                 (meta.key ? t(meta.key) : fmt(evt.kind)) })
  ]);
  if (evt.detail) {
    li.appendChild(el("span", { class: "lab-act-detail", text: evt.detail }));
  }
  return li;
}

/* --- localized rendering of the structured Test-phase events ---------- */

function evInstNum(inst) {
  var m = /inst(\d+)/.exec(inst || "");
  return m ? m[1] : (inst || "?");
}

function evPct(p) {
  return (p > 0 ? "+" : "") + Number(p).toFixed(1) + "%";
}

function evGroup(n) {   // space-grouped thousands (nbsp)
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/* Per-instance pinned summary from data (localized), or the RU detail as
 * a fallback. Returns {text, cls}. */
function evSummaryText(evt) {
  var d = evt.data;
  if (!d || d.t !== "inst_summary") {
    return { text: evt.detail || "", cls: "na" };
  }
  var head = tf("app.ev.inst", { n: evInstNum(d.inst) }) + " — ";
  if (d.cls === "error") {
    return { text: head + t("app.ev.sum.err"), cls: "error" };
  }
  var q;
  if (d.q_cls === "equal") { q = t("app.ev.sum.equal"); }
  else if (d.q_cls === "better") { q = tf("app.ev.sum.better", { pct: evPct(d.q_pct) }); }
  else if (d.q_cls === "worse") { q = tf("app.ev.sum.worse", { pct: evPct(d.q_pct) }); }
  else { q = t("app.ev.sum.na"); }
  var tm = "";
  if (d.t_pct !== null && d.t_pct !== undefined) {
    if (Math.abs(d.t_pct) < 1) { tm = tf("app.ev.time.noise", { pct: evPct(d.t_pct) }); }
    else if (d.t_pct < 0) { tm = tf("app.ev.time.faster", { pct: evPct(d.t_pct) }); }
    else { tm = tf("app.ev.time.slower", { pct: evPct(d.t_pct) }); }
  }
  var tech = [];
  if (d.obj !== null && d.obj !== undefined) {
    tech.push(t("app.ev.tech.obj") + " " + evGroup(d.obj));
  }
  if (d.wall !== null && d.wall !== undefined) {
    tech.push(Math.round(d.wall) + " " + t("app.ev.tech.sec")
              + (d.base_wall ? " (" + t("app.ev.tech.was") + " "
                 + Math.round(d.base_wall) + ")" : ""));
  }
  var techs = tech.length ? " (" + tech.join("; ") + ")" : "";
  return { text: head + q + tm + techs, cls: d.cls || "na" };
}

function evRecLabel(rec) { return t("app.ev.rec." + rec); }

function evVerdictText(evt) {
  var d = evt.data || {};
  var rec = d.recommendation || "hold";
  var key = d.stopped ? "app.ev.verdict.stopped" : "app.ev.verdict.done";
  var src = d.source === "fallback" ? " " + t("app.ev.verdict.fallback") : "";
  var grade = d.grade ? t("app.ev.grade." + d.grade) + " · " : "";
  return { text: grade + tf(key, { rec: evRecLabel(rec) }) + src,
           rec: rec, rationale: d.rationale || "" };
}

function labShowPinned() {
  $("lab-activity-pinned").classList.remove("hidden");
}

/* Reset the whole live feed (scrolling + pinned summaries/verdict). */
function labClearFeed() {
  $("lab-activity-feed").textContent = "";
  var s = $("lab-activity-summaries"); if (s) { s.textContent = ""; }
  var v = $("lab-activity-verdict"); if (v) { v.textContent = ""; }
  var p = $("lab-activity-pinned"); if (p) { p.classList.add("hidden"); }
}

/* --- stop a running Test phase (item 1) ------------------------------- */

function evoStop() {
  var id = state.labTestingId;
  if (!id) { return; }
  appConfirm(t("app.ev.stop.confirm"), { danger: true }).then(function (ok) {
    if (!ok) { return; }
    api("/evolution/" + id + "/stop", { method: "POST" }).then(function () {
      toast(t("app.ev.stopped"), "warn");
    }).catch(function (err) {
      if (err.status === 409) { toast(t("app.evo.stale"), "warn"); refreshLab(); }
      else { apiErrorToast(err, t("app.ev.stop")); }
    });
  });
}

/* --- detail panel: full comparison + LLM rationale (item 3) ----------- */

/* Human-readable value for a comparison cell. */
function evVal(v) {
  if (v === null || v === undefined) { return "—"; }
  if (typeof v === "boolean") { return v ? t("app.yes") : t("app.no"); }
  if (typeof v === "number") {
    var s = (v % 1 === 0) ? String(v) : v.toFixed(1);
    return evGroup(s);
  }
  return String(v);
}

function evClsBadge(cls) {
  return el("span", { class: "evo-cls evo-cls-" + cls,
                      text: t("app.ev.cls." + cls) });
}

/* One stage (phase) block: title, phase errors, metric table. */
function evStageBlock(stage) {
  var box = el("div", { class: "evo-stage" });
  box.appendChild(el("div", { class: "evo-stage-title",
                              text: t("app.ev." + stage.stage) }));
  if (stage.base_error || stage.cand_error) {
    var who = stage.base_error && stage.cand_error ? "both"
      : (stage.cand_error ? "cand" : "base");
    box.appendChild(el("div", { class: "evo-stage-err",
                                text: t("app.ev.phaseerr." + who) }));
  }
  if (!stage.rows.length) { return box; }
  var thead = el("thead", null, [el("tr", null, [
    el("th", { text: t("app.ev.col.metric") }),
    el("th", { class: "num", text: t("app.ev.col.base") }),
    el("th", { class: "num", text: t("app.ev.col.cand") }),
    el("th", { class: "num", text: "Δ" }),
    el("th", { text: "" })
  ])]);
  var tbody = el("tbody");
  stage.rows.forEach(function (r) {
    var delta = "—";
    if (r.delta_pct !== null && r.delta_pct !== undefined) {
      delta = (r.delta_pct > 0 ? "+" : "") + r.delta_pct.toFixed(1) + "%";
    }
    tbody.appendChild(el("tr", { class: "evo-row-" + r.cls }, [
      el("td", { text: t("app.ev.m." + r.metric) }),
      el("td", { class: "num mono", text: evVal(r.base) }),
      el("td", { class: "num mono evo-cand", text: evVal(r.cand) }),
      el("td", { class: "num mono", text: delta }),
      el("td", null, [evClsBadge(r.cls)])
    ]));
  });
  box.appendChild(el("table", { class: "evo-cmp-table" }, [thead, tbody]));
  return box;
}

function evoShowDetail(impId) {
  api("/evolution/" + impId + "/report").then(function (r) {
    var body = el("div", { class: "evo-detail-body" });
    var rev = r.review || {};
    if (rev.recommendation) {
      body.appendChild(el("div", { class: "evo-detail-rec evo-rec-" + rev.recommendation }, [
        el("span", { class: "evo-detail-lbl", text: t("app.ev.detail.rec") + ": " }),
        el("strong", { text: evRecLabel(rev.recommendation)
          + (rev.source === "fallback" ? " " + t("app.ev.verdict.fallback") : "") })
      ]));
      if (rev.rationale) {
        body.appendChild(el("div", { class: "evo-detail-why" }, [
          el("span", { class: "evo-detail-lbl", text: t("app.ev.detail.why") + ": " }),
          el("span", { text: rev.rationale })
        ]));
      }
      if (rev.stopped) {
        body.appendChild(el("div", { class: "evo-detail-stopped", text: t("app.ev.detail.stopped") }));
      }
    }
    // Column legend: what «было/стало» mean (best-before vs the new code).
    body.appendChild(el("div", { class: "evo-detail-legend",
                                 text: t("app.ev.detail.legend") }));
    (r.comparison || []).forEach(function (instBlock) {
      var card = el("div", { class: "evo-inst-card" });
      card.appendChild(el("div", { class: "evo-inst-title",
        text: tf("app.ev.inst", { n: evInstNum(instBlock.inst) }) }));
      instBlock.stages.forEach(function (st) {
        if (st.rows.length || st.base_error || st.cand_error) {
          card.appendChild(evStageBlock(st));
        }
      });
      body.appendChild(card);
    });
    if (!(r.comparison || []).length) {
      body.appendChild(el("div", { class: "muted",
                                   text: t("app.ev.detail.nocmp") }));
    }
    // Raw technical report for specialists, collapsed by default.
    if (r.report_md) {
      var det = el("details", { class: "evo-detail-raw" }, [
        el("summary", { text: t("app.ev.detail.raw") }),
        el("pre", { class: "evo-detail-report", text: r.report_md })
      ]);
      body.appendChild(det);
    }
    appModal(t("app.ev.detail.title") + " — " + fmt(r.title), body);
  }).catch(function (err) { apiErrorToast(err, t("app.ev.detail.title")); });
}

/* Generic scrollable modal (reuses the confirm-overlay styling). */
function appModal(titleText, contentNode) {
  var closeBtn = el("button", { class: "btn btn-primary", type: "button",
                                text: t("app.confirm.ok") });
  var dialog = el("div", { class: "confirm-dialog evo-modal", role: "dialog",
                           "aria-modal": "true" }, [
    el("h3", { class: "evo-modal-title", text: titleText }),
    contentNode,
    el("div", { class: "confirm-actions" }, [closeBtn])
  ]);
  var overlay = el("div", { class: "confirm-overlay" }, [dialog]);
  function close() {
    document.removeEventListener("keydown", onKey);
    overlay.remove();
  }
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("mousedown", function (e) {
    if (e.target === overlay) { close(); }
  });
  function onKey(e) { if (e.key === "Escape") { close(); } }
  document.addEventListener("keydown", onKey);
  document.body.appendChild(overlay);
  closeBtn.focus();
}

function pollLabActivity() {
  var since = state.labActivitySeq || 0;
  api("/evolution/scan-activity?since=" + since).then(function (a) {
    var feed = $("lab-activity-feed");
    (a.events || []).forEach(function (evt) {
      if (evt.kind === "summary") {
        // Pinned per-instance verdict — accumulates, always visible.
        var s = evSummaryText(evt);
        $("lab-activity-summaries").appendChild(
          el("div", { class: "lab-pin-item lab-pin-" + s.cls, text: s.text }));
        labShowPinned();
      } else if (evt.kind === "done" && evt.data && evt.data.t === "verdict") {
        // Final analytical verdict banner (pinned).
        var v = evVerdictText(evt);
        var box = $("lab-activity-verdict");
        box.textContent = "";
        box.appendChild(el("div", { class: "lab-verdict lab-verdict-" + v.rec }, [
          el("span", { class: "lab-verdict-line", text: v.text }),
          el("span", { class: "lab-verdict-why", text: v.rationale })
        ]));
        labShowPinned();
      } else {
        // Everything else scrolls in the intermediate feed.
        feed.appendChild(renderActivityEvent(evt));
      }
      state.labActivitySeq = evt.seq;
    });
    if (a.events && a.events.length) {
      feed.scrollTop = feed.scrollHeight;
    }
    // Show the panel only while a scan runs or once it has produced a feed.
    var pinned = $("lab-activity-summaries").children.length > 0
      || $("lab-activity-verdict").children.length > 0;
    var hasFeed = feed.children.length > 0 || pinned;
    $("lab-activity").classList.toggle("hidden", !(a.running || hasFeed));
    // Stop button: only while a Test phase (not a scan) is live.
    var stopBtn = $("lab-stop-btn");
    if (stopBtn) {
      stopBtn.classList.toggle("hidden", !(a.running && state.labTestingId));
      if (!stopBtn._wired) { stopBtn._wired = true;
        stopBtn.addEventListener("click", evoStop); }
    }
    var status = $("lab-activity-status");
    var spinner = $("lab-spinner");
    var title = $("lab-activity-title");
    if (a.running) {
      status.textContent = a.started_at ? fmtTime(a.started_at) : "";
      spinner.classList.remove("hidden");
      title.textContent = t("app.lab.activeTitle");
    } else {
      // Finished: final render, stop polling, refresh the findings table.
      spinner.classList.add("hidden");
      title.textContent = t("app.lab.doneTitle");
      status.textContent = a.finished_at ? fmtTime(a.finished_at) : "";
      stopLabActivityPolling();
      refreshLab();
    }
  }).catch(function (err) {
    if (err.status === 404) { stopLabActivityPolling(); }
    // transient errors: keep polling
  });
}

function startLabActivityPolling() {
  pollLabActivity();
  if (state.labActivityTimer) { return; }
  state.labActivityTimer = setInterval(pollLabActivity, POLL_MS);
}

/** On opening the Лаборатория tab: if a scan is already running, resume the
 *  live feed; otherwise leave the panel hidden. */
function resumeLabActivity() {
  api("/evolution/scan-activity?since=0").then(function (a) {
    if (a.running) {
      state.labActivitySeq = 0;
      labClearFeed();
      startLabActivityPolling();
    }
  }).catch(function () {});
}

function stopLabActivityPolling() {
  if (state.labActivityTimer) {
    clearInterval(state.labActivityTimer);
    state.labActivityTimer = null;
  }
}

/* -------------------------------------------------------- activity journal */

var _lastNavSent = null;

/** Fire-and-forget tab beacon for the admin activity journal. Sent only
 *  when the tab actually changes; NEVER surfaces errors (the journal is
 *  an observer, not a participant). */
function _navBeacon(name) {
  if (!state.me || _lastNavSent === name) { return; }
  _lastNavSent = name;
  api("/activity/nav", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tab: name })
  }).catch(function () {});
}

function refreshActivity() {
  var emailF = $("act-email") ? $("act-email").value.trim() : "";
  var eventF = $("act-event") ? $("act-event").value : "";
  var q = "?limit=200";
  if (emailF) { q += "&email=" + encodeURIComponent(emailF); }
  if (eventF) { q += "&event=" + encodeURIComponent(eventF); }
  api("/activity" + q).then(function (data) {
    renderActivity((data && data.events) || []);
  }).catch(function (err) {
    apiErrorToast(err, t("app.t.actLoad"));
  });
}

function renderActivity(rows) {
  var tbody = $("activity-tbody");
  if (!tbody) { return; }
  tbody.textContent = "";
  if (!rows.length) {
    tbody.appendChild(el("tr", {}, [
      el("td", { colspan: "4", class: "empty-row",
                 text: t("html.act.empty") })]));
    return;
  }
  rows.forEach(function (r) {
    var evKey = { login: "html.act.ev.login", logout: "html.act.ev.logout",
                  nav: "html.act.ev.nav" }[r.event];
    // nav detail is a tab id — show the human tab label when we know it.
    // hasOwnProperty guards against prototype keys ("constructor" etc.)
    // resolving to inherited Object members (audit 2026-07-16).
    var detail = r.event === "nav"
      && Object.prototype.hasOwnProperty.call(TAB_LABEL, r.detail)
      ? t(TAB_LABEL[r.detail]) : r.detail;
    // trim the UTC offset — the column header already says «(UTC)»
    var ts = fmt(r.ts).replace("T", " ").replace(/(\+00:00|Z)$/, "");
    tbody.appendChild(el("tr", {}, [
      el("td", { class: "mono small", text: ts }),
      el("td", { class: "mono", text: r.email }),
      el("td", {}, [el("span", {
        class: "chip small " + (r.event === "login" ? "chip-ok"
               : r.event === "logout" ? "chip-warn" : "chip-neutral"),
        text: evKey ? t(evKey) : r.event })]),
      el("td", { text: detail })
    ]));
  });
}

/* ------------------------------- tab: Студия формализации (Ф1/Ф5, admin) */

var studioState = { sessions: [], q: null, detail: null };

function refreshStudio() {
  api("/studio").then(function (data) {
    studioState.sessions = (data && data.sessions) || [];
    studioState.q = (data && data.questionnaires) || null;
    renderStudio();
    if (studioState.detail) { openStudio(studioState.detail.id); }
  }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
}

function renderStudio() {
  var tbody = $("fs-tbody");
  if (!tbody) { return; }
  tbody.textContent = "";
  if (!studioState.sessions.length) {
    tbody.appendChild(el("tr", {}, [
      el("td", { colspan: "6", class: "empty-row",
                 text: t("app.fs.empty") })]));
    return;
  }
  studioState.sessions.forEach(function (s) {
    var open = el("button", { class: "btn btn-ghost btn-small",
                              text: t("app.rp.open") });
    open.addEventListener("click", function () { openStudio(s.id); });
    var del = el("button", { class: "btn btn-ghost btn-small",
                             text: t("app.rp.del") });
    del.addEventListener("click", function () {
      appConfirm(tf("app.fs.confirmDel", { name: s.name }),
                 { danger: true }).then(function (yes) {
        if (!yes) { return; }
        api("/studio/" + s.id, { method: "DELETE" }).then(function () {
          if (studioState.detail && studioState.detail.id === s.id) {
            studioState.detail = null;
            $("fs-detail").classList.add("hidden");
          }
          refreshStudio();
        }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
      });
    });
    var pkg = el("button", { class: "btn btn-ghost btn-small",
                             text: t("app.fs.package") });
    pkg.addEventListener("click", function () { studioPackage(s.id); });
    var links = (s.instance_id ? "📄 " + s.instance_id + " " : "")
      + (s.rule_pack_id ? "📐 " + s.rule_pack_id : "");
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [el("div", { text: s.name }),
                    el("div", { class: "muted small",
                                text: "v" + s.version })]),
      el("td", { class: "mono small", text: s.country || "—" }),
      el("td", { class: "num",
                 text: s.inventory_done + " / " + s.inventory_total }),
      el("td", { class: "num",
                 text: s.glossary_done + " / " + s.glossary_total }),
      el("td", { class: "mono small", text: links || "—" }),
      el("td", {}, [open, pkg, del]),
    ]));
  });
}

function openStudio(id) {
  api("/studio/" + id).then(function (s) {
    studioState.detail = s;
    renderStudioDetail();
  }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
}

function _fsQuestionRow(sessionId, part, q, answers) {
  var rec = answers[q.id] || {};
  var ta = el("textarea", { rows: "2" });
  ta.value = rec.answer || "";
  ta.placeholder = q.hint || "";
  var meta = el("p", { class: "muted small" });
  function paintMeta() {
    meta.textContent = rec.by
      ? t("app.fs.decidedBy") + ": " + rec.by + " ("
        + fmt(rec.at).slice(0, 10) + ")"
      : "";
  }
  paintMeta();
  // Fix (audit): commit on blur but DO NOT re-render — a full refresh here
  // rebuilds every textarea and would wipe text the user is typing in the
  // next field. Update this field's local record + caption in place; list
  // counts refresh on the next natural render (tab switch / Обновить).
  ta.addEventListener("blur", function () {
    var v = ta.value.trim();
    if (v === (rec.answer || "")) { return; }
    api("/studio/" + sessionId + "/answer", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part: part, question_id: q.id, text: v })
    }).then(function (updated) {
      // reflect the saved answer locally so a later render is correct
      var store = (studioState.detail
                   && studioState.detail[part]
                   && studioState.detail[part].answers) || null;
      if (v) {
        rec = { answer: v, by: (state.me && state.me.email) || rec.by,
                at: new Date().toISOString() };
        if (store) { store[q.id] = rec; }
      } else if (store) {
        delete store[q.id];
        rec = {};
      }
      paintMeta();
      toast(t("app.fs.saved"), "ok");
    }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
  });
  return el("div", { class: "form-row" },
            [el("p", { class: "small", text: q.q }), ta, meta]);
}

function renderStudioDetail() {
  var box = $("fs-detail");
  var s = studioState.detail;
  if (!box || !s || !studioState.q) { return; }
  box.textContent = "";
  box.classList.remove("hidden");
  box.appendChild(el("div", { class: "section-head" }, [
    el("h3", { text: s.name + " · v" + s.version
               + (s.country ? " · " + s.country : "") })]));

  [["inventory", "app.fs.invH", studioState.q.inventory],
   ["glossary", "app.fs.gloH", studioState.q.glossary]
  ].forEach(function (triple) {
    var part = triple[0];
    var answers = (s[part] || {}).answers || {};
    var block = el("details", { class: "panel-help" }, [
      el("summary", { text: t(triple[1]) + " ("
                      + Object.keys(answers).length + " / "
                      + triple[2].length + ")" })]);
    triple[2].forEach(function (q) {
      block.appendChild(_fsQuestionRow(s.id, part, q, answers));
    });
    box.appendChild(block);
  });

  // mapping link: instance + notes
  var m = s.mapping || {};
  var instSel = el("select", {});
  instSel.appendChild(el("option", { value: "", text: "—" }));
  state.instances.forEach(function (i) {
    instSel.appendChild(el("option", { value: i.id, text: i.id }));
  });
  instSel.value = m.instance_id || "";
  var notes = el("textarea", { rows: "2",
                               placeholder: t("app.fs.notesPh") });
  notes.value = m.notes || "";
  var saveMap = el("button", { class: "btn btn-small",
                               text: t("app.fs.saveMapping") });
  saveMap.addEventListener("click", function () {
    api("/studio/" + s.id + "/mapping", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instance_id: instSel.value || null,
                             notes: notes.value })
    }).then(function () {
      toast(t("app.fs.saved"), "ok");
      refreshStudio();
    }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
  });
  box.appendChild(el("div", { class: "card form-card" }, [
    el("div", { class: "advisor-h", text: t("app.fs.mapH") }),
    el("div", { class: "form-row inline-controls" }, [instSel, saveMap]),
    el("div", { class: "form-row" }, [notes])]));

  // rule pack link
  var rpSel = el("select", {});
  rpSel.appendChild(el("option", { value: "", text: "—" }));
  api("/rule-packs").then(function (data) {
    ((data && data.items) || []).forEach(function (p) {
      rpSel.appendChild(el("option", { value: p.id,
                                       text: p.name + " (" + p.id + ")" }));
    });
    rpSel.value = s.rule_pack_id || "";
  });
  var saveRp = el("button", { class: "btn btn-small",
                              text: t("app.fs.saveRp") });
  saveRp.addEventListener("click", function () {
    api("/studio/" + s.id + "/rule-pack", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack_id: rpSel.value || null })
    }).then(function () {
      toast(t("app.fs.saved"), "ok");
      refreshStudio();
    }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
  });
  box.appendChild(el("div", { class: "card form-card" }, [
    el("div", { class: "advisor-h", text: t("app.fs.rpH") }),
    el("div", { class: "form-row inline-controls" }, [rpSel, saveRp])]));
}

function studioPackage(id) {
  api("/studio/" + id + "/package").then(function (pkg) {
    var blob = new Blob([JSON.stringify(pkg, null, 1)],
                        { type: "application/json" });
    var a = el("a", { href: URL.createObjectURL(blob),
                      download: "mapping-package-" + id + ".json" });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
}

/* ------------------------------------- tab: Пакеты правил (Rule Pack, Ф7) */

var rulePackState = { list: [], parameters: [], detail: null };

/* Область действия пакета (пятый аудит, R5-04). Отбор по стране изоляцией
   арендаторов не был: пакет одного заказчика попадал в постановку задачи
   другого заказчика той же страны. Администратор обязан видеть область в
   списке и менять её отсюда, иначе правило живёт только в запросе. */
function rpScopeLabel(scope) {
  var v = scope || "regulatory";
  if (v.indexOf("instance:") === 0) {
    return t("app.rp.scope.inst") + ": " + v.slice("instance:".length);
  }
  return t("app.rp.scope.reg");
}

function rpEditScope(p) {
  var current = p.scope || "regulatory";
  appPrompt(t("app.rp.scope.ask"), { value: current }).then(function (next) {
    if (next === null || next === undefined) { return; }
    next = String(next).trim();
    if (!next || next === current) { return; }
    /* Предупреждение обязательно: смена области снимает утверждение —
       решение принимает человек, и оно должно быть явным. */
    appConfirm(t("app.rp.scope.warn"), { danger: true }).then(function (yes) {
      if (!yes) { return; }
      api("/rule-packs/" + p.id + "/scope", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: next })
      }).then(function () {
        toast(t("app.rp.scope.done"), "ok");
        refreshRulePacks();
      }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
    });
  });
}

function refreshRulePacks() {
  api("/rule-packs").then(function (data) {
    rulePackState.list = (data && data.items) || [];
    rulePackState.parameters = (data && data.parameters) || [];
    renderRulePacks();
    if (rulePackState.detail) { openRulePack(rulePackState.detail.id); }
  }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
}

function rpStatusChip(status) {
  var ok = status === "approved";
  return el("span", { class: "chip small " + (ok ? "chip-ok" : "chip-warn"),
                      text: t(ok ? "app.rp.st.approved" : "app.rp.st.draft") });
}

function renderRulePacks() {
  var tbody = $("rp-tbody");
  if (!tbody) { return; }
  $("rp-create-card").classList.toggle("hidden", !isAdmin());
  tbody.textContent = "";
  if (!rulePackState.list.length) {
    tbody.appendChild(el("tr", {}, [
      el("td", { colspan: "7", class: "empty-row",
                 text: t("app.rp.empty") })]));
    return;
  }
  rulePackState.list.forEach(function (p) {
    var open = el("button", { class: "btn btn-ghost btn-small",
                              text: t("app.rp.open") });
    open.addEventListener("click", function () { openRulePack(p.id); });
    var cells = [
      el("td", { text: p.name }),
      el("td", { class: "mono small", text: p.country || "—" }),
      el("td", { class: "mono small", text: rpScopeLabel(p.scope) }),
      el("td", {}, [rpStatusChip(p.status)]),
      el("td", { class: "num", text: fmt(p.version) }),
      el("td", { class: "num", text: p.approved_rules + " / " + p.rules }),
    ];
    var actions = el("td", {}, [open]);
    if (isAdmin()) {
      var scopeBtn = el("button", { class: "btn btn-ghost btn-small",
                                    text: t("app.rp.scope.edit") });
      scopeBtn.addEventListener("click", function () { rpEditScope(p); });
      actions.appendChild(scopeBtn);
      var del = el("button", { class: "btn btn-ghost btn-small",
                               text: t("app.rp.del") });
      del.addEventListener("click", function () {
        appConfirm(tf("app.rp.confirmDelPack", { name: p.name }),
                   { danger: true }).then(function (yes) {
          if (!yes) { return; }
          api("/rule-packs/" + p.id, { method: "DELETE" }).then(function () {
            if (rulePackState.detail && rulePackState.detail.id === p.id) {
              rulePackState.detail = null;
              $("rp-detail").classList.add("hidden");
            }
            refreshRulePacks();
          }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
        });
      });
      actions.appendChild(del);
    }
    cells.push(actions);
    tbody.appendChild(el("tr", {}, cells));
  });
}

function openRulePack(packId) {
  api("/rule-packs/" + packId).then(function (pack) {
    rulePackState.detail = pack;
    renderRulePackDetail();
  }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
}

function _rpAction(path, okKey) {
  api(path, { method: "POST" }).then(function () {
    if (okKey) { toast(t(okKey), "ok"); }
    refreshRulePacks();
  }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
}

function _rpTestChips(rep) {
  var box = el("span", {});
  box.appendChild(el("span", { class: "chip small chip-ok",
                               text: "✓ " + rep.pass }));
  box.appendChild(el("span", { class: "chip small " +
                               (rep.fail ? "chip-bad" : "chip-neutral"),
                               text: "✗ " + rep.fail }));
  if (rep.manual) {
    box.appendChild(el("span", { class: "chip small chip-info",
                                 text: t("app.rp.manual") + " " + rep.manual }));
  }
  return box;
}

function renderRulePackDetail() {
  var box = $("rp-detail");
  var pack = rulePackState.detail;
  if (!box || !pack) { return; }
  box.textContent = "";
  box.classList.remove("hidden");

  var head = el("div", { class: "section-head" }, [
    el("h3", { text: pack.name + " · v" + pack.version +
               (pack.country ? " · " + pack.country : "") }),
  ]);
  head.appendChild(rpStatusChip(pack.status));
  box.appendChild(head);
  if (pack.approved_by) {
    box.appendChild(el("p", { class: "muted small",
      text: t("app.rp.approvedBy") + ": " + pack.approved_by +
            " (" + fmt(pack.approved_at) + ")" }));
  }

  // pack-level actions (admin)
  if (isAdmin()) {
    var row = el("div", { class: "advisor-actions" });
    var appr = el("button", { class: "btn btn-primary btn-small",
                              text: t("app.rp.approvePack") });
    appr.addEventListener("click", function () {
      _rpAction("/rule-packs/" + pack.id + "/approve", "app.rp.packApproved");
    });
    var apply = el("button", { class: "btn btn-small",
                               text: t("app.rp.apply") });
    apply.addEventListener("click", function () {
      appConfirm(t("app.rp.confirmApply")).then(function (yes) {
        if (yes) {
          _rpAction("/rule-packs/" + pack.id + "/apply-config",
                    "app.rp.applied");
        }
      });
    });
    var exp = el("button", { class: "btn btn-ghost btn-small",
                             text: t("app.rp.export") });
    exp.addEventListener("click", function () { rpExport(pack); });
    row.appendChild(appr); row.appendChild(apply); row.appendChild(exp);
    box.appendChild(row);
  }

  // compile preview: tier-1 parameters + conflicts + outside list
  var comp = pack.compile || {};
  var params = Object.keys(comp.parameters || {});
  if (params.length || (comp.outside_tier1 || []).length) {
    var cblock = el("div", { class: "advisor-block" });
    cblock.appendChild(el("div", { class: "advisor-h",
                                   text: t("app.rp.compile") }));
    if (params.length) {
      cblock.appendChild(el("p", { class: "mono small",
        text: params.map(function (k) {
          return k + " = " + comp.parameters[k]; }).join(";  ") }));
    }
    (comp.conflicts || []).forEach(function (c) {
      cblock.appendChild(el("p", { class: "small",
        text: t("app.rp.conflict") + ": " + c }));
    });
    if ((comp.outside_tier1 || []).length) {
      cblock.appendChild(el("p", { class: "muted small",
        text: t("app.rp.outside") + ": " + comp.outside_tier1.join(", ") }));
    }
    box.appendChild(cblock);
  }

  // rules table
  var rows = (pack.rules || []).map(function (r) {
    var c = r.constraint || {};
    var constraintTxt;
    if (c.kind === "parameter") {
      constraintTxt = c.parameter + " = " + c.value;
    } else if (c.kind === "resource") {
      var w = c.window || {};
      var bounds = [];
      if (w.min !== null && w.min !== undefined) { bounds.push(">= " + w.min); }
      if (w.max !== null && w.max !== undefined) { bounds.push("<= " + w.max); }
      constraintTxt = t("app.rp.kind.resource") + ": " + (c.resource || "") +
        " " + bounds.join(", ") + " (" + (c.unit || "") + ", " +
        (c.level || "") + ")";
    } else {
      constraintTxt = t("app.rp.kind.note") + (c.note ? ": " + c.note : "");
    }
    var src = r.source || {};
    var srcTxt = src.document + (src.section ? " § " + src.section : "");
    var actTd = el("td", {});
    if (isAdmin()) {
      if (r.status !== "approved") {
        var ap = el("button", { class: "btn btn-primary btn-small",
                                text: t("app.rp.approveRule") });
        ap.addEventListener("click", function () {
          _rpAction("/rule-packs/" + pack.id + "/rules/" + r.id +
                    "/approve", "app.rp.ruleApproved");
        });
        actTd.appendChild(ap);
      }
      var del = el("button", { class: "btn btn-ghost btn-small",
                               text: t("app.rp.del") });
      del.addEventListener("click", function () {
        appConfirm(tf("app.rp.confirmDelRule", { name: r.title }),
                   { danger: true }).then(function (yes) {
          if (!yes) { return; }
          api("/rule-packs/" + pack.id + "/rules/" + r.id,
              { method: "DELETE" }).then(refreshRulePacks)
            .catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
        });
      });
      actTd.appendChild(del);
    }
    return el("tr", {}, [
      el("td", {}, [
        el("div", { text: r.title }),
        el("div", { class: "muted small", text: r.statement })]),
      el("td", { class: "small", text: srcTxt }),
      el("td", { class: "mono small", text: constraintTxt }),
      el("td", {}, [_rpTestChips(r.test_report || {})]),
      el("td", {}, [r.status === "approved"
        ? el("span", { class: "chip small chip-ok",
                       text: t("app.rp.st.approved") +
                             (r.approved_by ? " · " + r.approved_by : "") })
        : rpStatusChip(r.status)]),
      actTd,
    ]);
  });
  box.appendChild(el("div", { class: "advisor-block" }, [
    el("div", { class: "advisor-h", text: t("app.rp.rulesH") }),
    rows.length ? demoLikeTable(
      [el("th", { class: "small", text: t("app.rp.rTitle") }),
       el("th", { class: "small", text: t("app.rp.src") }),
       el("th", { class: "small", text: t("app.rp.constraint") }),
       el("th", { class: "small", text: t("app.rp.tests") }),
       el("th", { class: "small", text: t("html.rp.th.status") }),
       el("th", { class: "small", text: t("html.lab.th.actions") })], rows)
      : el("p", { class: "muted small", text: t("app.rp.noRules") })
  ]));

  if (isAdmin()) {
    box.appendChild(rpRuleForm(pack));
    box.appendChild(rpExtractForm(pack));
  }
}

function rpExtractForm(pack) {
  // двухконтурный экстрактор: public -> облако допустимо,
  // confidential -> только локальная модель (сервер откажет громко)
  var card = el("div", { class: "card form-card" });
  card.appendChild(el("div", { class: "advisor-h",
                               text: t("app.rp.extractH") }));
  card.appendChild(el("p", { class: "muted small",
                             text: t("app.rp.extractHint") }));
  var doc = el("input", { type: "text",
                          placeholder: t("app.rp.rDoc") });
  var sens = el("select", {});
  sens.appendChild(el("option", { value: "public",
                                  text: t("app.rp.sens.public") }));
  sens.appendChild(el("option", { value: "confidential",
                                  text: t("app.rp.sens.confidential") }));
  var text = el("textarea", { rows: "6",
                              placeholder: t("app.rp.extractPh") });
  var btn = el("button", { class: "btn btn-primary btn-small",
                           text: t("app.rp.extractBtn") });
  btn.addEventListener("click", function () {
    btn.disabled = true;
    api("/rule-packs/" + pack.id + "/extract", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.value, document: doc.value.trim(),
                             sensitivity: sens.value })
    }).then(function (resp) {
      btn.disabled = false;
      toast(tf("app.rp.extracted", { n: resp.added }), "ok");
      (resp.errors || []).slice(0, 3).forEach(function (e) {
        toast(e, "warn");
      });
      // M03: warn when the model silently dropped content (document past the
      // char limit, or candidates past the cap) — and DON'T clear the text so
      // the reviewer can re-run on the remainder.
      if (resp.truncated || (resp.candidates_dropped || 0) > 0) {
        toast(tf("app.rp.extractTruncated",
                 { d: resp.candidates_dropped || 0 }), "warn");
      } else {
        text.value = "";
      }
      refreshRulePacks();
    }).catch(function (err) {
      btn.disabled = false;
      apiErrorToast(err, t("app.rp.load"));
    });
  });
  card.appendChild(el("div", { class: "form-row inline-controls" },
                      [doc, sens]));
  card.appendChild(el("div", { class: "form-row" }, [text]));
  card.appendChild(el("div", { class: "advisor-actions" }, [btn]));
  return card;
}

function rpRuleForm(pack) {
  var card = el("div", { class: "card form-card" });
  card.appendChild(el("div", { class: "advisor-h",
                               text: t("app.rp.addRule") }));
  var title = el("input", { type: "text",
                            placeholder: t("app.rp.rTitle") });
  var stmt = el("input", { type: "text",
                           placeholder: t("app.rp.rStatement") });
  var doc = el("input", { type: "text", placeholder: t("app.rp.rDoc") });
  var section = el("input", { type: "text",
                              placeholder: t("app.rp.rSection") });
  var kind = el("select", {});
  kind.appendChild(el("option", { value: "parameter",
                                  text: t("app.rp.kind.parameter") }));
  kind.appendChild(el("option", { value: "note",
                                  text: t("app.rp.kind.note") }));
  var param = el("select", {});
  rulePackState.parameters.forEach(function (p) {
    param.appendChild(el("option", { value: p, text: p }));
  });
  var value = el("input", { type: "number", step: "any",
                            placeholder: t("app.rp.rValue") });
  var pos = el("input", { type: "text", placeholder: t("app.rp.posPh") });
  var neg = el("input", { type: "text", placeholder: t("app.rp.negPh") });
  kind.addEventListener("change", function () {
    var isNote = kind.value === "note";
    param.disabled = isNote; value.disabled = isNote;
    pos.placeholder = t(isNote ? "app.rp.posNotePh" : "app.rp.posPh");
    neg.placeholder = t(isNote ? "app.rp.negNotePh" : "app.rp.negPh");
  });
  var btn = el("button", { class: "btn btn-primary btn-small",
                           text: t("app.rp.addRule") });
  btn.addEventListener("click", function () {
    var isNote = kind.value === "note";
    var tests = [];
    var parse = function (input, kindName) {
      input.value.split(isNote ? ";" : ",").forEach(function (s) {
        s = s.trim();
        if (!s) { return; }
        tests.push(isNote ? { kind: kindName, description: s }
                          : { kind: kindName, given: Number(s) });
      });
    };
    parse(pos, "positive"); parse(neg, "negative");
    var body = {
      title: title.value.trim(), statement: stmt.value.trim(),
      source: { document: doc.value.trim(), section: section.value.trim() },
      constraint: isNote ? { kind: "note" }
        : { kind: "parameter", parameter: param.value,
            value: Number(value.value) },
      tests: tests,
    };
    api("/rule-packs/" + pack.id + "/rules", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () {
      toast(t("app.rp.ruleAdded"), "ok");
      refreshRulePacks();
    }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
  });
  [title, stmt, doc, section].forEach(function (i) {
    card.appendChild(el("div", { class: "form-row" }, [i]));
  });
  card.appendChild(el("div", { class: "form-row inline-controls" },
                      [kind, param, value]));
  card.appendChild(el("div", { class: "form-row" }, [pos]));
  card.appendChild(el("div", { class: "form-row" }, [neg]));
  card.appendChild(el("div", { class: "advisor-actions" }, [btn]));
  return card;
}

function rpExport(pack) {
  // full pack JSON as a downloadable file (страновой шаблон)
  var data = JSON.stringify(pack, null, 1);
  var blob = new Blob([data], { type: "application/json" });
  var a = el("a", { href: URL.createObjectURL(blob),
                    download: "rulepack-" + pack.id + ".json" });
  document.body.appendChild(a);
  a.click();
  setTimeout(function () {
    URL.revokeObjectURL(a.href); a.remove();
  }, 0);
}

function rpImportFile() {
  var input = el("input", { type: "file", accept: ".json" });
  input.addEventListener("change", function () {
    if (!input.files || !input.files.length) { return; }
    input.files[0].text().then(function (text) {
      var body;
      try { body = JSON.parse(text); }
      catch (e) { toast(t("app.rp.badJson"), "error"); return; }
      api("/rule-packs/import", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (resp) {
        toast(tf("app.rp.imported", { n: resp.imported_rules }), "ok");
        (resp.errors || []).forEach(function (e) { toast(e, "warn"); });
        refreshRulePacks();
      }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
    });
  });
  input.click();
}

/* --------------------------------------- tab: ИИ-контур (реестр точек ИИ) */

var llmRegState = { items: [] };

function refreshLlmReg() {
  api("/llm-registry").then(function (data) {
    llmRegState.items = (data && data.items) || [];
    renderLlmReg();
  }).catch(function (err) {
    apiErrorToast(err, t("app.llmreg.load"));
  });
}

function renderLlmReg() {
  var tbody = $("llmreg-tbody");
  if (!tbody) { return; }
  tbody.textContent = "";
  if (!llmRegState.items.length) {
    tbody.appendChild(el("tr", {}, [
      el("td", { colspan: "5", class: "empty-row",
                 text: t("html.loading") })]));
    return;
  }
  llmRegState.items.forEach(function (r) {
    // routing chip: llm_scope (per-dataset) / contour-guard (per-checkout)
    // / external-only (refuses on the internal contour)
    var chipCls = r.routing === "llm_scope" ? "chip-ok"
      : r.routing === "contour-guard" ? "chip-info" : "chip-warn";
    var routKey = "app.llmreg.rt." + r.routing;
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [
        el("div", { text: r.purpose }),
        el("div", { class: "muted small mono", text: r.function })]),
      el("td", { class: "mono small", text: r.module }),
      el("td", {}, [el("span", { class: "chip small " + chipCls,
                                 text: t(routKey) })]),
      el("td", { class: "mono small", text: r.backends }),
      el("td", { class: "small", text: r.data })
    ]));
  });
}

/* ------------------- База шаблонов-исполнителей (P-N-SHD 6а, admin) */

var templateBaseState = { data: null };

function refreshTemplateBase() {
  api("/template-base").then(function (data) {
    templateBaseState.data = data || {};
    renderTemplateBase();
  }).catch(function (err) {
    apiErrorToast(err, t("app.tpl.load"));
  });
}

/* Правка и снятие записи (шаг 6 каталога, решение 0.8): не заявка
 * очереди, а СЛУЖБА каталога — правка становится черновиком аналитика,
 * снятие ставит статус «снята» словами владельца. Заявка каталога у
 * существующей записи бывает только на вид исполнителя (правило R02
 * читателя), поэтому «заявки на правку» не бывает по построению.
 * Кнопки экрана стали ссылками; без адреса службы они гаснут с
 * подсказкой, а не ведут в никуда. */
function tplServiceAction(section, id, keyLabel) {
  var link = tplRecordLink(section, id);
  if (!link) {
    var dead = el("button", { class: "btn btn-ghost small",
                              text: t(keyLabel),
                              title: t("app.tpl.catalogNotConfigured") });
    dead.disabled = true;
    return dead;
  }
  return el("a", { class: "btn btn-ghost small", href: link,
                   target: "_blank", rel: "noopener",
                   text: t(keyLabel) });
}

/* --- Привязки исполнителей (шаг 4 каталога): чипы, стенд, сводка ---
 *
 * Состояние клетки «запись × вид» приходит от двери уже свёрнутым по
 * продуктам ({вид: {продукт: состояние}}, лучшее из строк продукта).
 * Экран показывает состояния, не судит их: суд — гейт продукта
 * (`python -m vmp.bindings_gate`) и проверка читателя. */
var TPL_STATE_RANK = { "есть": 3, "заявлен": 2, "нет": 1 };
/* Карта цветов — ОДНА на три панели (контракт службы каталога §6):
 * есть — зелёный, заявлен — ЖЁЛТЫЙ (работа объявлена, но не сделана),
 * нет — серый, «вид никем не объявлен» — СИНИЙ (это не тревога, а
 * сведение: вид вне зоны обоих продуктов). */
var TPL_STATE_CHIP = { "есть": "chip-ok", "заявлен": "chip-warn",
                       "нет": "chip-neutral" };
var TPL_STATE_KEY = { "есть": "app.tpl.state.present",
                      "заявлен": "app.tpl.state.declared",
                      "нет": "app.tpl.state.absent" };

/** Лучшее состояние вида по всем продуктам: «есть» > «заявлен» > «нет».
 *  Ни одного продукта — "" («вид никем не объявлен»). */
function tplBestState(byProduct) {
  var best = "";
  Object.keys(byProduct || {}).forEach(function (product) {
    var st = byProduct[product];
    if ((TPL_STATE_RANK[st] || 0) > (TPL_STATE_RANK[best] || 0)) {
      best = st;
    }
  });
  return best;
}

/** Адрес службы каталога для ссылок экрана; службы нет — null. */
function tplServiceUrl() {
  var data = templateBaseState.data || {};
  var url = (data.catalog_service || {}).url;
  return url ? String(url).replace(/\/+$/, "") : null;
}

/** Ссылка на карточку записи в службе: версия и отпечаток снимка едут
 *  параметрами — открывший видит ТОТ ЖЕ каталог, что показал экран. */
function tplRecordLink(section, id) {
  var base = tplServiceUrl();
  if (!base) { return null; }
  var cat = (templateBaseState.data || {}).catalog || {};
  return base + "/ui/#/records/" + section + "/" + encodeURIComponent(id)
    + "?version=" + encodeURIComponent(cat.version || "")
    + "&fingerprint=" + encodeURIComponent(cat.fingerprint || "");
}

/** Ячейка «Привязки»: по чипу на объявленный вид, подсказка — продукты
 *  и (у семей) строки исполнителей «вид · символ · файл». Чип ведёт в
 *  таблицу исполнителей службы, чип «заявлен» — на КАРТОЧКУ заявки
 *  (адрес строит сервер, `executor_requests[вид].link`); заявка сервером
 *  не названа — прежний перечень заявок по записи. */
function tplBindingsCell(states, executors, section, id, requests) {
  var kinds = Object.keys(states || {}).sort();
  if (!kinds.length) {
    return el("span", { class: "muted small",
                        text: t("app.tpl.bind.none") });
  }
  var base = tplServiceUrl();
  return el("div", {}, kinds.map(function (kind) {
    var byProduct = states[kind] || {};
    var best = tplBestState(byProduct);
    var hint = Object.keys(byProduct).sort().map(function (product) {
      return product + ": " + byProduct[product];
    });
    (executors || []).forEach(function (line) {
      if (line.indexOf(kind + " ") === 0) { hint.push(line); }
    });
    var attrs = {
      class: "chip small mono " + (best ? TPL_STATE_CHIP[best]
                                        : "chip-info"),
      title: kind + " — " + (best ? t(TPL_STATE_KEY[best])
                                  : t("app.tpl.state.undeclared"))
             + (hint.length ? "\n" + hint.join("\n") : ""),
      text: kind };
    if (!base || !section || !id) { return el("span", attrs); }
    var asked = (requests || {})[kind] || {};
    if (best === "заявлен") {
      attrs.href = asked.link
        ? asked.link                       // карточка заявки (сервер)
        : base + "/ui/#/requests?section=" + section + "&record_id="
          + encodeURIComponent(id);
      if (asked.request_id) { attrs.title += "\n" + asked.request_id; }
    } else {
      attrs.href = base + "/ui/#/executors?product=COSMOS&section="
        + section + "&id=" + encodeURIComponent(id) + "&kind="
        + encodeURIComponent(kind);
    }
    attrs.title += "\n" + t("app.tpl.catalogExecutors");
    attrs.target = "_blank";
    attrs.rel = "noopener";
    return el("a", attrs);
  }));
}

/** Ячейка «Стенд»: passed / failed / skipped; failed > 0 — красным. */
function tplStandCell(stand) {
  var s = stand || {};
  var total = (s.passed || 0) + (s.failed || 0) + (s.skipped || 0);
  if (!total) {
    return el("span", { class: "muted small",
                        text: t("app.tpl.stand.none") });
  }
  return el("span", {
    class: "chip small " + (s.failed ? "chip-bad" : "chip-ok"),
    title: tf("app.tpl.stand.hint", { passed: s.passed || 0,
                                      failed: s.failed || 0,
                                      skipped: s.skipped || 0 }),
    text: (s.passed || 0) + " / " + (s.failed || 0) + " / "
          + (s.skipped || 0) });
}

/** Две колонки «Привязки» и «Стенд» в шапке таблицы — идемпотентно:
 *  разметка экрана их не несёт, ставит их рисование. Ключи data-i18n
 *  остаются на месте, поэтому переключение языка их переводит. */
function tplEnsureColumns(tableId, afterIndex) {
  var head = document.querySelector("#" + tableId + " thead tr");
  if (!head || head.dataset.bindCols) { return; }
  head.dataset.bindCols = "1";
  ["html.tpl.th.bindings", "html.tpl.th.stand"].forEach(function (key, i) {
    var th = el("th", { "data-i18n": key, text: t(key) });
    var at = head.children[afterIndex + 1 + i];
    if (at) { head.insertBefore(th, at); } else { head.appendChild(th); }
  });
}

/** Строка сводки привязок над таблицами: файл, каталог, состояние,
 *  клетки и строки «есть», итог стенда, объявлены ли привязки CDPM.
 *  Слова состояния и «объявлены | не объявлены» приходят от двери
 *  закрытым перечнем (§3 проекта шага 4); незнакомое слово печатается
 *  дословно — экран не выдумывает за дверь. */
function renderTemplateBindingsSummary(sum) {
  var box = $("templates-bindings");
  if (!box) {
    var table = $("templates-table");
    if (!table || !table.parentNode || !table.parentNode.parentNode) {
      return;
    }
    box = el("div", { id: "templates-bindings", class: "muted small" });
    table.parentNode.parentNode.insertBefore(box, table.parentNode);
  }
  box.textContent = "";
  if (!sum) { return; }
  var stateKey = { match: "app.tpl.bind.match",
                   "stale-draft": "app.tpl.bind.stale",
                   missing: "app.tpl.bind.missing" }[sum.state];
  var stand = sum.stand || {};
  // Слово о чужих привязках — из закрытого перечня двери (три слова:
  // объявлены / не объявлены / не читаются); причина нечитаемости —
  // свободные слова читателя, им место в подсказке, а не в строке.
  var cdpm = sum.cdpm === "объявлены" ? t("app.tpl.bind.cdpmYes")
    : sum.cdpm === "не объявлены" ? t("app.tpl.bind.cdpmNo")
      : sum.cdpm === "не читаются" ? t("app.tpl.bind.cdpmBad")
        : String(sum.cdpm === undefined || sum.cdpm === null ? "" : sum.cdpm);
  // Путь файла показываем хвостом «папка/файл»: полный путь машины —
  // в подсказке, чтобы строка сводки читалась, а не пряталась за ним.
  var tail = String(sum.file || "").replace(/\\/g, "/").split("/")
    .slice(-2).join("/");
  var parts = [
    t("app.tpl.bind.h") + ": " + tail,
    tf("app.tpl.bind.catalog", { version: sum.catalog_version || "" }),
    stateKey ? t(stateKey) : String(sum.state || ""),
    tf("app.tpl.bind.cells", { cells: sum.cells || 0,
                               rows: sum.present_rows || 0 }),
    tf("app.tpl.bind.stand", { passed: stand.passed || 0,
                               failed: stand.failed || 0,
                               skipped: stand.skipped || 0 }),
    tf("app.tpl.bind.cdpm", { value: cdpm })
  ];
  box.appendChild(el("span", {
    class: sum.state === "match" && sum.cdpm !== "не читаются"
      ? "" : "warn-hint",
    title: (sum.file || "") + (sum.cdpm_note ? "\n" + sum.cdpm_note : "")
      + "\n" + tf("app.tpl.bind.hint", {
        product: sum.product_version || "",
        commit: (sum.generated_from || "").slice(0, 12),
        at: sum.generated_at || "",
        fingerprint: (sum.fingerprint || "").slice(0, 12) }),
    text: parts.join(" · ") }));
}

/** Строка над таблицами: ссылка в службу каталога (записи · заявки ·
 *  исполнители · версии) и сводка заявок каталога. Блок заводится
 *  рисованием — идемпотентно, как и сводка привязок. */
function renderTemplateCatalogLine(data) {
  var box = $("templates-catalog-line");
  if (!box) {
    var table = $("templates-table");
    if (!table || !table.parentNode || !table.parentNode.parentNode) {
      return;
    }
    box = el("div", { id: "templates-catalog-line", class: "muted small" });
    table.parentNode.parentNode.insertBefore(box, table.parentNode);
  }
  box.textContent = "";
  var base = tplServiceUrl();
  if (!base) {
    box.appendChild(el("span", { class: "warn-hint",
                                 text: t("app.tpl.catalogNotConfigured") }));
  } else {
    var cat = data.catalog || {};
    box.appendChild(el("a", {
      href: base + "/ui/#/versions/" + encodeURIComponent(cat.version || ""),
      target: "_blank", rel: "noopener",
      text: t("app.tpl.catalogOpen") }));
  }
  var rq = data.requests;
  if (rq) {
    var states = Object.keys(rq.states || {}).sort().map(function (s) {
      return s + ": " + rq.states[s];
    }).join(", ");
    box.appendChild(el("span", { text: " · " + tf("app.tpl.requestsLine",
      { n: rq.open, s: states || "—", k: rq.cr_keys }) }));
  }
}

function renderTemplateBase() {
  var data = templateBaseState.data || {};
  var fams = data.rule_families || {};
  var tbody = $("templates-tbody");
  if (!tbody) { return; }
  renderTemplateBindingsSummary(data.bindings);
  renderTemplateCatalogLine(data);
  tplEnsureColumns("templates-table", 3);      // после «Исполнители»
  tplEnsureColumns("templates-crit-table", 3); // после «Исполнитель»
  tbody.textContent = "";
  Object.keys(fams).sort().forEach(function (name) {
    var f = fams[name];
    var mapped = [];
    if ((f.mapped_by || {}).family_field) {
      mapped.push("export_solver.family");
    }
    ((f.mapped_by || {}).params || []).forEach(function (p) {
      mapped.push(p);
    });
    var deps = f.dependents || [];
    var depCell = deps.length
      ? el("div", {}, deps.map(function (d) {
          return el("div", { class: "mono small",
                             text: d.rule_id + " (" +
                               (d.title || "") + ")" });
        }))
      : el("span", { class: "muted small", text: t("app.tpl.noDeps") });
    var clsChip = el("span", {
      class: "chip small " + (f["class"] === "core"
        ? "chip-ok" : "chip-info"),
      text: f["class"] });
    var nameLink = tplRecordLink("families", name);
    tbody.appendChild(el("tr", {}, [
      el("td", {}, [nameLink
        ? el("a", { class: "mono", href: nameLink, target: "_blank",
                    rel: "noopener", title: t("app.tpl.catalogRecord"),
                    text: name })
        : el("span", { class: "mono", text: name })]),
      el("td", {}, [clsChip]),
      el("td", { class: "small", text: f.title || "" }),
      el("td", {}, (f.executors || []).map(function (x) {
        return el("div", { class: "mono small", text: x });
      })),
      el("td", {}, [tplBindingsCell(f.executor_states, f.executors,
                                    "families", name,
                                    f.executor_requests)]),
      el("td", {}, [tplStandCell(f.stand)]),
      el("td", {}, mapped.map(function (m) {
        return el("div", { class: "mono small", text: m });
      })),
      el("td", {}, [depCell]),
      (function () {
        // Перечень зависимых правил действующего ВМП остаётся здесь
        // (подсказкой у ссылки снятия): службе каталога он неизвестен.
        var rm = tplServiceAction("families", name,
                                  "app.tpl.removeInService");
        if (deps.length) { rm.title = t("app.tpl.removeBlocked"); }
        var cells = [
          tplServiceAction("families", name, "app.tpl.editInService"), rm];
        var base2 = tplServiceUrl();
        if (base2) {
          // Заявка каталога у существующей записи бывает только на ВИД
          // исполнителя — форма службы это и предлагает.
          cells.push(el("a", { class: "btn btn-ghost small",
            href: base2 + "/ui/#/requests/new?section=families&record_id="
              + encodeURIComponent(name) + "&source="
              + encodeURIComponent("вручную"),
            target: "_blank", rel: "noopener",
            text: t("app.tpl.requestInCatalog") }));
        }
        return el("td", {}, cells);
      })()
    ]));
  });
  var crits = data.criteria || {};
  var ctbody = $("templates-crit-tbody");
  if (!ctbody) { return; }
  ctbody.textContent = "";
  Object.keys(crits).sort().forEach(function (kind) {
    var c = crits[kind];
    var critLink = tplRecordLink("criteria", kind);
    ctbody.appendChild(el("tr", {}, [
      el("td", {}, [critLink
        ? el("a", { class: "mono", href: critLink, target: "_blank",
                    rel: "noopener", title: t("app.tpl.catalogRecord"),
                    text: kind })
        : el("span", { class: "mono", text: kind })]),
      el("td", { class: "small", text: c.title || "" }),
      el("td", { class: "small", text: c["class"] || "" }),
      el("td", {}, [el("span", {
        class: "chip small " + (c.has_executor
          ? "chip-ok" : "chip-warn"),
        text: c.has_executor ? t("app.tpl.execYes")
          : t("app.tpl.execNo") })]),
      el("td", {}, [tplBindingsCell(c.executor_states, null, "criteria",
                                    kind, c.executor_requests)]),
      el("td", {}, [tplStandCell(c.stand)])
    ]));
  });
}

/* ------------------------------------- Установки (super admin only) */

var settingsState = { data: null };

function refreshSettings() {
  if (!isSuperAdmin()) { return; }
  api("/settings").then(function (data) {
    settingsState.data = data;
    renderSettings();
  }).catch(function (err) { apiErrorToast(err, t("app.settings.load")); });
}

function _settingsModelOptions(currentId) {
  var opts = [{ v: "", label: t("app.settings.modelDefault") }];
  var cache = (settingsState.data && settingsState.data.models_cache) || null;
  var seen = {};
  ((cache && cache.models) || []).forEach(function (m) {
    var lbl = (m.display_name && m.display_name !== m.id)
      ? (m.display_name + " · " + m.id) : m.id;
    opts.push({ v: m.id, label: lbl });
    seen[m.id] = true;
  });
  if (currentId && !seen[currentId]) {
    opts.push({ v: currentId, label: currentId });
  }
  return opts;
}

function _settingsSelect(id, options, current) {
  var sel = el("select", { id: id, class: "settings-select" });
  options.forEach(function (o) {
    var opt = el("option", { value: o.v, text: o.label });
    if (o.v === (current || "")) { opt.selected = true; }
    sel.appendChild(opt);
  });
  return sel;
}

function renderSettings() {
  var body = $("settings-body");
  if (!body) { return; }
  body.textContent = "";
  var d = settingsState.data;
  if (!d) {
    body.appendChild(el("p", { class: "muted", text: t("html.loading") }));
    return;
  }

  // Card 1 — Claude API key (secret; status only, never the value)
  var ak = d.api_key || {};
  var keyCard = el("div", { class: "card form-card" });
  keyCard.appendChild(el("h3", { text: t("app.settings.apiKey.h") }));
  keyCard.appendChild(el("p", { class: "muted small",
                                text: t("app.settings.apiKey.p") }));
  keyCard.appendChild(el("div", { class: "form-row inline-controls" }, [
    el("span", { class: "chip small " + (ak.configured ? "chip-ok"
                                                        : "chip-neutral"),
      text: ak.configured
        ? tf("app.settings.apiKey.set",
             { hint: ak.hint || "", src: ak.source || "" })
        : t("app.settings.apiKey.unset") })
  ]));
  keyCard.appendChild(el("div", { class: "form-row" }, [
    el("label", { for: "settings-api-key",
                  text: t("app.settings.apiKey.label") }),
    el("input", { type: "password", id: "settings-api-key",
                  autocomplete: "off", placeholder: "sk-ant-…" })
  ]));
  var saveKeyBtn = el("button", { class: "btn btn-primary", type: "button",
                                  text: t("app.settings.apiKey.save") });
  saveKeyBtn.addEventListener("click", saveSettingsApiKey);
  var clearKeyBtn = el("button", { class: "btn btn-danger", type: "button",
                                   text: t("app.settings.apiKey.clear") });
  clearKeyBtn.disabled = !ak.configured;
  clearKeyBtn.addEventListener("click", clearSettingsApiKey);
  keyCard.appendChild(el("div", { class: "form-row form-actions inline-controls" },
                        [saveKeyBtn, clearKeyBtn]));
  body.appendChild(keyCard);

  // Card 2 — model per use case + live discovery
  var modelCard = el("div", { class: "card form-card" });
  modelCard.appendChild(el("h3", { text: t("app.settings.models.h") }));
  modelCard.appendChild(el("p", { class: "muted small",
                                  text: t("app.settings.models.p") }));
  var refreshBtn = el("button", { class: "btn btn-ghost", type: "button",
                                  text: t("app.settings.models.refresh") });
  refreshBtn.addEventListener("click", refreshSettingsModels);
  var cache = d.models_cache;
  modelCard.appendChild(el("div", { class: "form-row inline-controls" }, [
    refreshBtn,
    el("span", { class: "muted small",
      text: (cache && cache.fetched_at)
        ? tf("app.settings.models.cached",
             { n: (cache.models || []).length, at: cache.fetched_at })
        : t("app.settings.models.noCache") })
  ]));
  (d.usages || []).forEach(function (u) {
    var lbl = el("label", { for: "settings-model-" + u.key }, [
      el("span", { text: t("app.settings.usage." + u.key) })
    ]);
    if (u.cli) {
      lbl.appendChild(el("span", { class: "chip small chip-info",
                                   text: t("app.settings.cli") }));
    }
    modelCard.appendChild(el("div", { class: "form-row" }, [
      lbl,
      _settingsSelect("settings-model-" + u.key,
        _settingsModelOptions((d.models || {})[u.key]),
        (d.models || {})[u.key])
    ]));
  });
  body.appendChild(modelCard);

  // Card 3 — contour / backend / local model / masking
  var eff = d.effective || {};
  var backCard = el("div", { class: "card form-card" });
  backCard.appendChild(el("h3", { text: t("app.settings.contour.h") }));
  backCard.appendChild(el("p", { class: "muted small",
                                 text: t("app.settings.contour.p") }));
  backCard.appendChild(el("div", { class: "form-row" }, [
    el("label", { for: "settings-default-backend",
                  text: t("app.settings.defaultBackend") }),
    _settingsSelect("settings-default-backend", [
      { v: "", label: tf("app.settings.effDefault",
                         { v: eff.default_backend || "local" }) },
      { v: "local", label: t("app.settings.backend.local") },
      { v: "cloud", label: t("app.settings.backend.cloud") }
    ], d.default_backend)
  ]));
  var omIn = el("input", { type: "text", id: "settings-ollama-model",
                           autocomplete: "off",
                           placeholder: eff.ollama_model || "" });
  if (d.ollama_model) { omIn.value = d.ollama_model; }
  backCard.appendChild(el("div", { class: "form-row" }, [
    el("label", { for: "settings-ollama-model",
                  text: t("app.settings.ollamaModel") }), omIn]));
  var ouIn = el("input", { type: "text", id: "settings-ollama-url",
                           autocomplete: "off",
                           placeholder: eff.ollama_url || "" });
  if (d.ollama_url) { ouIn.value = d.ollama_url; }
  backCard.appendChild(el("div", { class: "form-row" }, [
    el("label", { for: "settings-ollama-url",
                  text: t("app.settings.ollamaUrl") }), ouIn]));
  backCard.appendChild(el("div", { class: "form-row" }, [
    el("label", { for: "settings-mask-samples",
                  text: t("app.settings.mask") }),
    _settingsSelect("settings-mask-samples", [
      { v: "", label: t("app.settings.mask.default") },
      { v: "cloud", label: t("app.settings.mask.cloud") },
      { v: "always", label: t("app.settings.mask.always") }
    ], d.mask_samples)
  ]));
  body.appendChild(backCard);

  // Save the non-secret settings (models + backend + local + masking)
  var saveBtn = el("button", { class: "btn btn-primary", type: "button",
                               id: "settings-save", text: t("html.save") });
  saveBtn.addEventListener("click", saveSettings);
  body.appendChild(el("div", { class: "form-row form-actions inline-controls" },
    [saveBtn, el("span", { id: "settings-status", class: "muted" })]));
}

function saveSettings() {
  var d = settingsState.data;
  if (!d) { return; }
  var models = {};
  (d.usages || []).forEach(function (u) {
    var sel = $("settings-model-" + u.key);
    models[u.key] = (sel && sel.value) ? sel.value : null;
  });
  var payload = {
    models: models,
    default_backend: ($("settings-default-backend") || {}).value || null,
    ollama_model: (($("settings-ollama-model") || {}).value || "").trim()
      || null,
    ollama_url: (($("settings-ollama-url") || {}).value || "").trim() || null,
    mask_samples: ($("settings-mask-samples") || {}).value || null
  };
  var status = $("settings-status");
  if (status) { status.textContent = t("app.settings.saving"); }
  api("/settings", { method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload) }).then(function () {
    toast(t("app.settings.saved"), "ok");
    refreshSettings();
  }).catch(function (err) {
    if (status) { status.textContent = ""; }
    apiErrorToast(err, t("app.settings.saveFail"));
  });
}

function saveSettingsApiKey() {
  var input = $("settings-api-key");
  var key = input ? input.value.trim() : "";
  if (!key) { toast(t("app.settings.apiKey.empty"), "warn"); return; }
  api("/settings/api-key", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: key }) }).then(function () {
    toast(t("app.settings.apiKey.saved"), "ok");
    if (input) { input.value = ""; }
    refreshSettings();
  }).catch(function (err) {
    apiErrorToast(err, t("app.settings.apiKey.saveFail"));
  });
}

function clearSettingsApiKey() {
  api("/settings/api-key", { method: "DELETE" }).then(function () {
    toast(t("app.settings.apiKey.cleared"), "ok");
    refreshSettings();
  }).catch(function (err) {
    apiErrorToast(err, t("app.settings.apiKey.clearFail"));
  });
}

function refreshSettingsModels() {
  toast(t("app.settings.models.fetching"), "ok");
  api("/settings/models/refresh", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}) }).then(function (resp) {
    toast(tf("app.settings.models.fetched",
             { n: (resp.models || []).length }), "ok");
    refreshSettings();
  }).catch(function (err) {
    apiErrorToast(err, t("app.settings.models.fetchFail"));
  });
}

/* --------------------------------------- Ф9: приём дельт (данные) */

function deltaInstanceId() {
  var v = $("delta-instance") ? $("delta-instance").value : "";
  if (!v) { toast(t("app.delta.pick"), "warn"); return null; }
  return v;
}

var deltaLast = null;   // {kind:'summary'|'journal', data} for crew:lang redraw

function renderDeltaResult(node) {
  var box = $("delta-result");
  box.textContent = "";
  box.appendChild(node);
}

function deltaSummaryNode(resp) {
  var wrap = el("div", { class: "advisor-block" });
  wrap.appendChild(el("span", {
    class: "chip small " + (resp.applied ? "chip-ok" : "chip-neutral"),
    text: t(resp.applied ? "app.delta.applied" : "app.delta.previewOnly") }));
  if (resp.kind === "schedule") {
    var s = resp.summary || {};
    wrap.appendChild(el("p", { class: "small", text: tf(
      "app.delta.schedSummary",
      { c: s.cancelled, r: s.retimed, e: s.equipment, n: s.created,
        s: s.seats_created || 0, a: s.assignments_removed }) }));
    // P0-3: доштамповка идентичностей видна в предпросмотре
    if (s.leg_ids_stamped) {
      wrap.appendChild(el("p", { class: "muted small",
        text: "flightLegId: +" + s.leg_ids_stamped }));
    }
    (s.skipped || []).forEach(function (m) {
      wrap.appendChild(el("p", { class: "muted small", text: "• " + m }));
    });
  } else if (resp.kind === "crew") {
    var d = resp.diff || {};
    wrap.appendChild(el("p", { class: "small", text: tf(
      "app.delta.crewSummary",
      { a: (d.added || []).length, u: (d.updated || []).length,
        m: (d.missing_in_feed || []).length }) }));
    if ((d.missing_in_feed || []).length) {
      wrap.appendChild(el("p", { class: "muted small",
        text: t("app.delta.missing") + ": "
              + d.missing_in_feed.slice(0, 15).join(", ") }));
    }
  }
  return wrap;
}

function deltaSchedule(apply) {
  var id = deltaInstanceId();
  if (!id) { return; }
  var text = $("delta-text").value;
  if (!text.trim()) { toast(t("app.delta.needText"), "warn"); return; }
  api("/instances/" + id + "/deltas/schedule", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text, apply: !!apply })
  }).then(function (resp) {
    deltaLast = { kind: "summary", data: resp };
    renderDeltaResult(deltaSummaryNode(resp));
    if (resp.applied) { refreshInstances(); }
  }).catch(function (err) { apiErrorToast(err, t("app.delta.load")); });
}

function deltaCrew(apply) {
  var id = deltaInstanceId();
  if (!id) { return; }
  var input = $("delta-crew-file");
  if (!input.files || !input.files.length) {
    toast(t("app.delta.needFile"), "warn");
    return;
  }
  var f = input.files[0];
  f.text().then(function (text) {
    return api("/instances/" + id + "/deltas/crew", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: f.name, text: text, apply: !!apply })
    });
  }).then(function (resp) {
    deltaLast = { kind: "summary", data: resp };
    renderDeltaResult(deltaSummaryNode(resp));
    if (resp.applied) { refreshInstances(); }
  }).catch(function (err) { apiErrorToast(err, t("app.delta.load")); });
}

function deltaJournal() {
  var id = deltaInstanceId();
  if (!id) { return; }
  api("/instances/" + id + "/deltas").then(function (data) {
    var items = data.items || [];
    if (!items.length) {
      renderDeltaResult(el("p", { class: "muted small",
                                  text: t("app.delta.journalEmpty") }));
      return;
    }
    var rows = items.map(function (e) {
      return el("tr", {}, [
        el("td", { class: "mono small",
                   text: fmt(e.at).replace("T", " ") }),
        el("td", { class: "small", text: e.kind }),
        el("td", { class: "mono small", text: e.by }),
        el("td", { class: "small",
                   text: JSON.stringify(e.summary || {}) }),
        el("td", { class: "mono small",
                   text: (e.sha256 || "").slice(0, 12) })
      ]);
    });
    renderDeltaResult(demoLikeTable(
      [el("th", { class: "small", text: t("html.act.th.ts") }),
       el("th", { class: "small", text: t("app.delta.kind") }),
       el("th", { class: "small", text: "Email" }),
       el("th", { class: "small", text: t("app.delta.summary") }),
       el("th", { class: "small", text: "SHA-256" })], rows));
  }).catch(function (err) { apiErrorToast(err, t("app.delta.load")); });
}

/* --------------------------------- Ф8: сравнение ростеров (акт приёмки) */

var rcmpState = { report: null };

var RCMP_ROWS = [
  ["coverage_pct", "app.rcmp.coverage"],
  ["pairings", "app.rcmp.pairings"],
  ["employees_used", "app.rcmp.employees"],
  ["block_h", "app.rcmp.block"],
  ["credit_h", "app.rcmp.credit"],
  ["paid_h", "app.rcmp.paid"],
  ["cost_total", "app.rcmp.cost"],
  ["violations_total", "app.rcmp.violations"]
];

function rcmpBuild() {
  var id = selectedOpsInstance();
  if (!id) { toast(t("app.rcmp.pick"), "warn"); return; }
  api("/roster-compare/" + id).then(function (rep) {
    rcmpState.report = rep;
    renderRcmp();
  }).catch(function (err) { apiErrorToast(err, t("app.rcmp.load")); });
}

function renderRcmp() {
  var box = $("rcmp-box");
  var rep = rcmpState.report;
  if (!box || !rep) { return; }
  box.textContent = "";
  var sides = [["ours", "app.rcmp.ours"], ["historical", "app.rcmp.hist"]];
  sides.forEach(function (pair) {
    var s = rep[pair[0]];
    if (s && !s.available) {
      box.appendChild(el("p", { class: "muted small",
        text: t(pair[1]) + ": " + t("app.rcmp.na." + (s.reason || "x")) }));
    }
  });
  var rows = RCMP_ROWS.map(function (r) {
    var o = rep.ours || {}, h = rep.historical || {};
    var d = (rep.delta || {})[r[0]];
    return el("tr", {}, [
      el("td", { class: "small", text: t(r[1]) }),
      el("td", { class: "num", text: o.available ? fmt(o[r[0]]) : "—" }),
      el("td", { class: "num", text: h.available ? fmt(h[r[0]]) : "—" }),
      el("td", { class: "num", text: d === undefined || d === null
                 ? "—" : fmt(d) })
    ]);
  });
  box.appendChild(demoLikeTable(
    [el("th", { class: "small", text: "" }),
     el("th", { class: "small", text: t("app.rcmp.ours") }),
     el("th", { class: "small", text: t("app.rcmp.hist") }),
     el("th", { class: "small", text: "Δ" })], rows));
  (rep.notes || []).forEach(function (n) {
    box.appendChild(el("p", { class: "muted small", text: "• " + n }));
  });
}

function rcmpDownloadAct() {
  var id = selectedOpsInstance();
  if (!id) { toast(t("app.rcmp.pick"), "warn"); return; }
  api("/roster-compare/" + id + "/act.md").then(function (md) {
    var blob = new Blob([md], { type: "text/markdown" });
    var a = el("a", { href: URL.createObjectURL(blob),
                      download: "roster-compare-act-" + id + ".md" });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }).catch(function (err) { apiErrorToast(err, t("app.rcmp.load")); });
}

/* ------------------------------------------------- tab: Операция (план-факт) */

var opsState = { report: null, cursorMin: 1440 };

function selectedOpsInstance() { return $("ops-instance").value || null; }

function refreshOps() {
  var id = selectedOpsInstance();
  var tbody = $("ops-tbody");
  if (!id) {
    opsState.report = null;
    $("ops-tiles").textContent = "";
    if (tbody) {
      tbody.textContent = "";
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "7",
        class: "empty-row", text: t("html.ops.pickInstance") })]));
    }
    return;
  }
  // Поколение запроса (внешний аудит R6, P0-09): у «Операции» не было
  // вообще никакой защиты — поздний ответ прежнего набора перерисовывал
  // уже выбранный новый. Ответ применяется только если выбор не менялся.
  opsState.report = null;
  $("ops-tiles").textContent = "";
  // Старая таблица не должна пережить смену набора (внешний аудит R7,
  // P0-07): видимые строки прежнего набора рядом с активными кнопками —
  // это подтверждение разрушительной операции над НЕ тем объектом.
  if (tbody) {
    tbody.textContent = "";
    tbody.appendChild(el("tr", {}, [el("td", { colspan: "7",
      class: "empty-row", text: t("html.loading") })]));
  }
  var opsSec = $("ops-tbody") && $("ops-tbody").closest("section");
  if (opsSec) { opsSec.setAttribute("aria-busy", "true"); }
  api("/ops/" + encodeURIComponent(id)).then(function (data) {
    if (selectedOpsInstance() !== id) { return; }
    if (opsSec) { opsSec.removeAttribute("aria-busy"); }
    opsState.report = data || {};
    opsState.report._loadedId = id;
    renderOps();
  }).catch(function (err) {
    if (selectedOpsInstance() !== id) { return; }
    if (opsSec) { opsSec.removeAttribute("aria-busy"); }
    apiErrorToast(err, t("app.t.opsLoad"));
  });
}

function _hhmm(min) {
  var h = Math.floor(min / 60), m = min % 60;
  return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m;
}

function renderOps() {
  if (!opsState.report || !opsState.report.summary) { return; }
  var s = opsState.report.summary;
  var tiles = $("ops-tiles");
  tiles.textContent = "";
  function tile(value, label, cls) {
    tiles.appendChild(el("div", { class: "kpi-tile " + (cls || "") }, [
      el("div", { class: "kpi-value", text: value }),
      el("div", { class: "kpi-label", text: label })
    ]));
  }
  if (!s.available) {
    tiles.appendChild(el("p", { class: "muted", text: t("html.ops.noFlights") }));
    var tb = $("ops-tbody");
    tb.textContent = "";
    tb.appendChild(el("tr", {}, [el("td", { colspan: "7", class: "empty-row",
      text: t("html.ops.noFlights") })]));
    return;
  }
  var pct = function (v) { return v === null ? "—" : v + "%"; };
  tile(pct(s.completionFactorPct), t("html.ops.kpi.completion"),
       s.completionFactorPct !== null && s.completionFactorPct < 98 ? "kpi-warn" : "");
  tile(pct(s.otpDep15Pct), t("html.ops.kpi.otpDep"),
       s.otpDep15Pct !== null && s.otpDep15Pct < 80 ? "kpi-warn" : "");
  tile(pct(s.otpArr15Pct), t("html.ops.kpi.otpArr"),
       s.otpArr15Pct !== null && s.otpArr15Pct < 80 ? "kpi-warn" : "");
  tile(String(s.delayed), t("html.ops.kpi.delayed"), s.delayed ? "kpi-warn" : "");
  tile(String(s.cancelled), t("html.ops.kpi.cancelled"), s.cancelled ? "kpi-warn" : "");
  tile(s.avgArrDelayMin === null ? "—" : s.avgArrDelayMin + " " + t("html.ops.min"),
       t("html.ops.kpi.avgDelay"));
  tile(pct(s.coveragePct), t("html.ops.kpi.coverage"));
  // P0-2: легаси-факты под внутриминутно-коллизионным ключом игнорируются
  // план-фактом — честно показываем их число
  if (s.unaddressableFacts) {
    tiles.appendChild(el("p", { class: "muted small",
      text: t("html.ops.unaddressable").replace("{n}",
        String(s.unaddressableFacts)) }));
  }
  // Scrubber spans the operating day incl. UTC-rollover flights: set its max
  // to the latest dayMin so late flights don't fall off the end.
  var maxMin = 1440;
  (opsState.report.deviations || []).forEach(function (r) {
    if (typeof r.dayMin === "number" && r.dayMin > maxMin) { maxMin = r.dayMin; }
  });
  var sc = $("ops-scrubber");
  sc.max = String(maxMin);
  if (opsState.cursorMin > maxMin) { opsState.cursorMin = maxMin; }
  sc.value = String(opsState.cursorMin);
  $("ops-scrubber-val").textContent = opsState.cursorMin >= maxMin
    ? t("html.ops.allDay") : _hhmm(opsState.cursorMin);
  renderOpsTable();
}

var OPS_STATUS_KEY = {
  cancelled: "html.ops.st.cancelled", diverted: "html.ops.st.diverted",
  delayed: "html.ops.st.delayed", ontime: "html.ops.st.ontime",
  scheduled: "html.ops.st.scheduled"
};
var OPS_STATUS_CLS = {
  cancelled: "chip-bad", diverted: "chip-warn", delayed: "chip-warn",
  ontime: "chip-ok", scheduled: "chip-neutral"
};

function renderOpsTable() {
  var tbody = $("ops-tbody");
  if (!tbody || !opsState.report) { return; }
  tbody.textContent = "";
  var all = opsState.report.deviations || [];
  var rows = all.filter(function (r) {
    return (typeof r.dayMin === "number" ? r.dayMin : 0) <= opsState.cursorMin;
  });
  if (!rows.length) {
    // no flights YET by this time (rows exist overall) vs a genuinely empty
    // instance — distinct messages (audit 2026-07-17)
    tbody.appendChild(el("tr", {}, [el("td", { colspan: "7",
      class: "empty-row",
      text: t(all.length ? "html.ops.emptyAsOf" : "html.ops.empty") })]));
    return;
  }
  rows.forEach(function (r) {
    var causeTxt = r.cause
      ? t("html.ops.cause." + r.cause) : "";
    tbody.appendChild(el("tr", {}, [
      el("td", { class: "mono", text: r.flightNumber }),
      el("td", { class: "small", text: (r.departureAirport || "") + "→"
                 + (r.arrivalAirport || "") }),
      el("td", { class: "mono small",
                 text: String(r.schedDep || "").replace("T", " ") }),
      el("td", {}, [el("span", {
        class: "chip small " + (OPS_STATUS_CLS[r.status] || "chip-neutral"),
        text: t(OPS_STATUS_KEY[r.status] || r.status) })]),
      el("td", { class: "num", text: r.depDelayMin === null
                 || r.depDelayMin === undefined ? "" : String(r.depDelayMin) }),
      el("td", { class: "num", text: r.arrDelayMin === null
                 || r.arrDelayMin === undefined ? "" : String(r.arrDelayMin) }),
      el("td", { class: "small", text: causeTxt })
    ]));
  });
}

function opsSetAction(key, kind) {
  var box = $("ops-action");
  if (box) { box.textContent = t(key); box.className = "small "
    + (kind === "error" ? "bad" : "muted"); }
}

function handleOpsImport() {
  var id = selectedOpsInstance();
  if (!id) { return; }
  // Мутация уходит только в набор, который РЕАЛЬНО на экране (R7, P0-07)
  if (!opsState.report || opsState.report._loadedId !== id) { return; }
  opsSetAction("html.ops.importing");
  api("/ops/" + encodeURIComponent(id) + "/import-bts", { method: "POST" })
    .then(function (data) {
      toast(tf("html.ops.imported", { n: (data && data.imported) || 0 }), "ok");
      opsSetAction("html.ops.importDone");
      refreshOps();
    }).catch(function (err) {
      opsSetAction("html.ops.importFail", "error");
      apiErrorToast(err, t("html.ops.importFail"));
    });
}

function handleOpsGenerate() {
  var id = selectedOpsInstance();
  if (!id) { return; }
  // Мутация уходит только в набор, который РЕАЛЬНО на экране (R7, P0-07)
  if (!opsState.report || opsState.report._loadedId !== id) { return; }
  api("/ops/" + encodeURIComponent(id) + "/generate-disruptions",
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: "{}" })
    .then(function (r) {
      toast(tf("html.ops.generated",
               { c: (r && r.cancelledFlights) || 0,
                 d: (r && r.delayedFlights) || 0 }), "ok");
    }).catch(function (err) { apiErrorToast(err, t("html.ops.generateFail")); });
}

function handleOpsClear() {
  var id = selectedOpsInstance();
  if (!id) { return; }
  // Мутация уходит только в набор, который РЕАЛЬНО на экране (R7, P0-07)
  if (!opsState.report || opsState.report._loadedId !== id) { return; }
  api("/ops/" + encodeURIComponent(id) + "/clear", { method: "POST" })
    .then(function () { toast(t("html.ops.cleared"), "ok"); refreshOps(); })
    .catch(function (err) { apiErrorToast(err, t("app.t.opsLoad")); });
}

/* ----------------------------------------------------------------- logout */

function doLogout() {
  if (state.me && state.me.mode === "access") {
    // Cloudflare Access session: the real logout happens at the Access
    // edge, which the app never sees — fire the journal beacon first,
    // then redirect regardless of its outcome (1s fallback keeps the
    // logout responsive even if the beacon hangs).
    var go = function () { window.location.href = "/cdn-cgi/access/logout"; };
    var fallback = setTimeout(go, 1000);
    api("/activity/logout", { method: "POST" }).catch(function () {})
      .finally(function () { clearTimeout(fallback); go(); });
    return;
  }
  // Legacy shared-token mode (and dev): server-side session revocation.
  api("/logout", { method: "POST" }).then(function () {
    window.location.href = "/login";
  }).catch(function (err) {
    apiErrorToast(err, t("app.t.logout"));
  });
}

/* -------------------------------------------------------- language switch */

/** Re-render every dynamic view after a language change (static DOM is
 *  re-applied by setUiLang via applyI18nDom before this event fires).
 *  Views render from cached state; results/config re-fetch (cheap GETs). */
document.addEventListener("crew:lang", function () {
  renderIdentity();
  renderInstancesTable();
  renderInstancePickers();
  renderJobsTable();
  // Состояние документа постановки рисуется кодом, а не разметкой, и без
  // этого вызова оставалось бы на языке загрузки страницы.
  refreshRules();
  if (state.activeJobId && state.lastActiveJob) {
    renderActiveJob(state.lastActiveJob);
  }
  // Unconditionally: a hidden results panel would otherwise keep the
  // previous language until its next refresh.
  refreshResults();
  loadSchema();
  // Схема конвейера строится один раз: заголовки блоков и строка
  // подсказки приёмника файлов остались бы на прежнем языке. Пересборка
  // без сброса позиций и масштаба (как «Сбросить раскладку», но без
  // очистки хранилища); на другом экране — при следующем показе схемы.
  if (_flowDom) {
    _flowDom = null;
    if (_nav.tab === "flow" && _flowEnsureDom() && _flowState) {
      renderFlow(_flowState);
    }
  }
  // Прогоны COSMOS: список и чип текущего прогона в шапке конвейера
  // рисуются кодом — перерисовка из кэша, без запросов.
  renderCosmosRuns();
  if (_flowState) { _flowRunHeader(_flowState); }
  // The config form is relabeled IN PLACE — re-fetching would rebuild the
  // inputs and silently discard the user's unsaved edits.
  CONFIG_PARAMS.forEach(function (spec) {
    var label = document.querySelector('label[for="cfg-' + spec[0] + '"]');
    if (label) { label.textContent = t(spec[1]); }
    // пометка «только проверка» — тоже НА МЕСТЕ: перерисовка формы
    // выбросила бы несохранённые правки, ради чего вся эта ветка и
    // существует, а без перевода чип остался бы русским на пяти языках
    var cert = $("cert-" + spec[0]);
    if (cert) {
      cert.textContent = t("app.cfg.certOnly");
      cert.setAttribute("title", t("app.cfg.certOnly.hint"));
    }
  });
  updateConfigChips();
  renderConfigStatus();
  // Scenario views: relabel the rules form in place (keep unsaved edits),
  // re-render table + compare panel from cached state.
  CONFIG_PARAMS.forEach(function (spec) {
    var label = document.querySelector('label[for="scen-' + spec[0] + '"]');
    if (label) { label.textContent = t(spec[1]); }
  });
  renderScenariosTable();
  if (state.scenCompare) { renderScenCompare(); }
  if (state.vacMeta) { renderVacation(); }
  if (state.qualComp) { renderCompliance(); }
  if (state.board && state.board.available) { renderBoard(); }
  if (state.bases) { renderBases(); }
  if (state.cabinet) { renderCabinet(); }
  if (llmRegState.items.length) { renderLlmReg(); }
  // База шаблонов: чипы привязок, итог стенда и строка сводки — наша
  // проза, а не разметка, и без перерисовки остались бы на языке
  // загрузки страницы (шаг 4 каталога).
  if (templateBaseState.data) { renderTemplateBase(); }
  if (rulePackState.list.length) { renderRulePacks(); }
  if (rulePackState.detail) { renderRulePackDetail(); }
  if (rcmpState.report) { renderRcmp(); }
  if (studioState.sessions.length) { renderStudio(); }
  if (studioState.detail) { renderStudioDetail(); }
  if (deltaLast && deltaLast.kind === "summary") {
    renderDeltaResult(deltaSummaryNode(deltaLast.data));
  }
  // links-management card labels/table are built in JS — rebuild them too
  if (!$("cab-links-card").classList.contains("hidden")) {
    refreshCabLinksCard();
  }
  $("cab-vac-status").textContent =
    cabVacStatusKey ? t(cabVacStatusKey) : "";
  // help center: relabel the instance chips, routing hint, answer meta and
  // the suggestions table
  renderAskInstances();
  // UI-authored prose must not stay in the previous language (audit
  // 2026-07-15): the failure text, the off-topic label and the in-flight
  // status are ours (the LLM answer itself is content — left as is)
  if (state.askResult) { renderAskAnswer(); renderAskMeta(); }
  if (state.askBusy) { $("ask-status").textContent = t("app.ask.thinking"); }
  if (state.suggestions) { renderSuggestions(); }
  if (state.crs) { renderCrs(); }
  if (state.loops) { renderLoops(); }
  relocalizeAuditStatus();          // audit-status prose is ours (audit key)
  $("sug-status").textContent = sugStatusKey ? t(sugStatusKey) : "";
  if (costConfigStatusKey) {
    $("cost-config-status").textContent = t(costConfigStatusKey);
  }
  if (payConfigStatusKey) {
    $("pay-config-status").textContent = t(payConfigStatusKey);
  }
  if (baseConfigStatusKey) {
    $("bases-config-status").textContent = t(baseConfigStatusKey);
  }
  if (isAdmin()) { refreshUsers(); }
  if (isAdmin() && _nav.tab === "activity") { refreshActivity(); }
  if (_nav.tab === "ops" && opsState.report) { renderOps(); }
  if ($("ops-action")) { $("ops-action").textContent = ""; }
  if (isAdmin()) { refreshLab(); }
  // The rich «Прогоны» iframe is a separate document: reload it in the new
  // language, but only if it was already opened (don't open the websocket
  // early for someone who never visited the tab).
  if (runsDashMounted) { mountRunsDashboard(); }
  advisorScopeChip();
  if (state.advisorRec) { renderAdvisor(state.advisorRec); }
  if (state.kbSkill) { renderKbMeta(); }
  // SSIM base rows are created once at init — relabel them in place.
  document.querySelectorAll("#ssim-bases .bases-row").forEach(function (row) {
    var btn = row.querySelector("button");
    if (btn) { btn.textContent = t("app.btn.removeBase"); }
    var count = row.querySelector(".base-count");
    if (count) { count.setAttribute("placeholder", t("app.ph.baseCount")); }
  });
  // Action-result boxes keep prose in the previous language — hide them.
  $("upload-result").classList.add("hidden");
  $("ssim-result").classList.add("hidden");
});

/* ------------------------------------------------- sticky table scrollbar */

/* Реализация параллельной сессии на legion (2026-07-23), перенесена в
 * репозиторий как есть; добавлен только вызов из showTab — см. ниже.
 *
 * A tall table's native horizontal bar sits at the very bottom of the wrap,
 * off-screen until the page is scrolled to the table's end.  Each wrap gets
 * a slim proxy bar that sticks to the viewport bottom while the table is in
 * view; the wrap's own bar is hidden and the proxy is synced both ways
 * (assigning an unchanged scrollLeft fires no event, so no loop guard). */
function attachStickyHScroll(wrap) {
  if (wrap.dataset.hproxy) {
    // Уже обвешан — но пересчитать геометрию всё равно нужно: контейнер,
    // обвешанный ПОД display:none (вкладка была скрыта при init), имел
    // нулевую ширину, и наблюдатель размера не всегда будит его при
    // показе вкладки (проверено в браузере: полоса оставалась пустой до
    // ручного resize). Повторный вызов из showTab дешевле и надёжнее.
    if (wrap._hupdate) { wrap._hupdate(); }
    return;
  }
  // Wraps with their own vertical scroll (e.g. import preview) already show
  // a bar inside the visible box — leave them alone.
  if (wrap.closest(".import-result")) { return; }
  wrap.dataset.hproxy = "1";
  wrap.classList.add("hscroll-native-off");
  var proxy = el("div", { class: "hscroll-proxy hidden" });
  proxy.setAttribute("aria-hidden", "true");
  var spacer = el("div");
  proxy.appendChild(spacer);
  wrap.parentNode.insertBefore(proxy, wrap.nextSibling);
  function update() {
    spacer.style.width = wrap.scrollWidth + "px";
    proxy.classList.toggle("hidden",
      wrap.scrollWidth - wrap.clientWidth <= 1);
    proxy.scrollLeft = wrap.scrollLeft;
  }
  proxy.addEventListener("scroll", function () {
    wrap.scrollLeft = proxy.scrollLeft;
  });
  wrap.addEventListener("scroll", function () {
    proxy.scrollLeft = wrap.scrollLeft;
  });
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(update);
    ro.observe(wrap);
    var table = wrap.querySelector("table");
    if (table) { ro.observe(table); }
  }
  window.addEventListener("resize", update);
  wrap._hupdate = update;         // для пересчёта при показе вкладки
  update();
}

function initStickyHScroll() {
  document.querySelectorAll(".table-wrap, .table-scroll")
    .forEach(attachStickyHScroll);
}

/* ------------------------------------------------------------------- init */

function init() {
  renderNav();                 // build the two-tier section/sub-tab bars
  $("link-to-schema").addEventListener("click", function (evt) {
    evt.preventDefault();
    showTab("format");
  });

  $("btn-refresh-instances").addEventListener("click", refreshInstances);
  // Правила КОНКРЕТНОГО набора (P1-10)
  $("inst-rules-form").addEventListener("submit", saveInstRules);
  $("inst-rules-reset").addEventListener("click", resetInstRules);
  $("inst-rules-close").addEventListener("click", function () {
    $("inst-rules-panel").classList.add("hidden");
    state.instRulesOpen = null;
  });
  var importForm = $("import-form");
  if (importForm) { importForm.addEventListener("submit", handleImportSubmit); }
  $("upload-form").addEventListener("submit", handleUploadSubmit);
  var rulesForm = $("rules-form");
  if (rulesForm) {
    rulesForm.addEventListener("submit", handleRulesSubmit);
    refreshRules();
  }
  var auditBtn = $("audit-run");
  if (auditBtn) { auditBtn.addEventListener("click", handleAuditRun); }
  $("ssim-form").addEventListener("submit", handleSsimSubmit);
  $("meridian-form").addEventListener("submit", handleMeridianSubmit);
  $("rules-import-form").addEventListener("submit", handleRulesImportSubmit);
  $("rules-import-file").addEventListener("change", handleRulesFileChange);
  $("ssim-add-base").addEventListener("click", function () { addBaseRow(); });
  addBaseRow(); // one empty bases row to start with
  $("btn-logout").addEventListener("click", doLogout);

  $("job-instance").addEventListener("change", updateStageButtons);
  $("btn-stage1").addEventListener("click", function () { startJob("stage1"); });
  var plvl = $("stage1-pairing-level");
  if (plvl) {
    // Выбор человека сильнее умолчания по документу: отмеченный флаг
    // запрещает последующему обновлению состояния правил переставить
    // селектор под пользователем.
    plvl.addEventListener("input", function () {
      plvl.dataset.userChosen = "1";
    });
    plvl.addEventListener("change", function () {
      var on = !!plvl.value;
      var row = $("stage1-pairing-method-row");
      var hint = $("stage1-pairing-hint");
      if (row) row.hidden = !on;
      if (hint) hint.hidden = !on;
    });
  }
  $("btn-complete").addEventListener("click", function () { startJob("complete"); });
  $("btn-stage2").addEventListener("click", function () { startJob("stage2"); });
  $("btn-validate").addEventListener("click", function () { startJob("validate"); });
  $("btn-multirole").addEventListener("click", function () { startJob("multirole"); });
  $("btn-stop-job").addEventListener("click", stopActiveJob);
  $("btn-refresh-jobs").addEventListener("click", refreshJobs);

  $("results-instance").addEventListener("change", refreshResults);
  $("btn-refresh-results").addEventListener("click", refreshResults);

  $("btn-refresh-scenarios").addEventListener("click", refreshScenarios);
  $("scenario-form").addEventListener("submit", createScenario);

  $("vac-instance").addEventListener("change", refreshVacation);
  $("btn-refresh-vacation").addEventListener("click", refreshVacation);
  $("vac-quota-form").addEventListener("submit", saveVacQuota);
  $("btn-vac-demo").addEventListener("click", vacDemoBids);
  $("btn-vac-clear").addEventListener("click", vacClear);
  $("btn-vac-award").addEventListener("click", vacAward);
  $("btn-vac-apply").addEventListener("click", vacApply);

  $("qual-instance").addEventListener("change", refreshQuals);
  $("btn-refresh-quals").addEventListener("click", refreshQuals);
  $("qual-warn-form").addEventListener("submit", saveQualWarn);
  $("btn-qual-demo").addEventListener("click", qualDemo);
  $("btn-qual-clear").addEventListener("click", qualClear);
  $("btn-qual-apply").addEventListener("click", qualApply);

  $("board-instance").addEventListener("change", refreshBoard);
  $("btn-refresh-board").addEventListener("click", refreshBoard);
  $("btn-board-reset").addEventListener("click", boardReset);
  $("btn-dz-toggle").addEventListener("click", dzToggle);
  $("btn-dz-sick").addEventListener("click", dzMarkSick);
  $("btn-dz-plan").addEventListener("click", dzPlan);
  $("btn-dz-apply").addEventListener("click", dzApply);
  $("dz-kind").addEventListener("change", dzKindChange);
  $("btn-dz-flights").addEventListener("click", function () {
    dzAddEvent({ kind: "flights", fns: $("dz-fns").value });
  });
  $("btn-dz-aircraft").addEventListener("click", function () {
    dzAddEvent({ kind: "aircraft", tail: $("dz-tail").value.trim(),
                 day_from: $("dz-ac-from").value,
                 day_to: $("dz-ac-to").value });
  });
  $("btn-dz-delay").addEventListener("click", function () {
    dzAddEvent({ kind: "delay", fns: $("dz-delay-fns").value,
                 shift_min: parseInt($("dz-shift").value, 10) || 0 });
  });
  $("btn-dz-rebuild").addEventListener("click", dzRebuild);

  $("bases-instance").addEventListener("change", refreshBases);
  $("btn-refresh-bases").addEventListener("click", refreshBases);
  var bcForm = $("bases-config-form");
  if (bcForm) { bcForm.addEventListener("submit", saveBaseConfig); }

  $("cab-instance").addEventListener("change", function () {
    state.cabPreviewEmp = null;             // picking my link ends a preview
    loadCabinet($("cab-instance").value, null);
  });
  $("btn-refresh-cabinet").addEventListener("click", function () {
    state.cabPreviewEmp = null;
    refreshCabinet();
  });
  $("btn-cab-vac-add").addEventListener("click", cabVacAdd);
  $("btn-cab-vac-submit").addEventListener("click", cabVacSubmit);
  $("btn-cab-vac-withdraw").addEventListener("click", cabVacWithdraw);
  $("cablink-instance").addEventListener("change", loadCabLinks);
  $("btn-cablink-add").addEventListener("click", cablinkAdd);

  $("btn-ask").addEventListener("click", askSubmit);
  $("ask-prefer-cloud").addEventListener("change", renderAskRouting);
  $("btn-sug-send").addEventListener("click", sugSubmit);
  $("btn-refresh-suggest").addEventListener("click", refreshSuggest);
  $("btn-refresh-loops").addEventListener("click", refreshLoops);
  $("btn-refresh-crs").addEventListener("click", refreshCrs);
  $("btn-crs-run").addEventListener("click", crRunAgent);
  $("btn-audit-check").addEventListener("click", auditCheck);

  $("config-form").addEventListener("submit", saveAirlineConfig);
  var ccForm = $("cost-config-form");
  if (ccForm) { ccForm.addEventListener("submit", saveCostConfig); }
  var pcForm = $("pay-config-form");
  if (pcForm) { pcForm.addEventListener("submit", savePayConfig); }

  $("btn-refresh-users").addEventListener("click", refreshUsers);
  $("user-add-form").addEventListener("submit", handleAddUserSubmit);
  $("btn-refresh-activity").addEventListener("click", refreshActivity);
  $("act-apply").addEventListener("click", refreshActivity);
  $("btn-refresh-llmreg").addEventListener("click", refreshLlmReg);
  $("btn-refresh-templates").addEventListener("click",
                                              refreshTemplateBase);
  $("btn-refresh-rules").addEventListener("click", refreshRulePacks);
  $("rp-create").addEventListener("click", function () {
    var name = $("rp-new-name").value.trim();
    if (!name) { toast(t("app.t.needName"), "error"); return; }
    api("/rule-packs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name,
                             country: $("rp-new-country").value.trim() })
    }).then(function () {
      $("rp-new-name").value = ""; $("rp-new-country").value = "";
      toast(t("app.rp.created"), "ok");
      refreshRulePacks();
    }).catch(function (err) { apiErrorToast(err, t("app.rp.load")); });
  });
  $("rp-import").addEventListener("click", rpImportFile);
  $("btn-refresh-ops").addEventListener("click", refreshOps);
  $("ops-instance").addEventListener("change", refreshOps);
  $("rcmp-build").addEventListener("click", rcmpBuild);
  $("rcmp-act").addEventListener("click", rcmpDownloadAct);
  $("delta-sched-preview").addEventListener("click", function () {
    deltaSchedule(false);
  });
  $("delta-sched-apply").addEventListener("click", function () {
    appConfirm(t("app.delta.confirmApply")).then(function (yes) {
      if (yes) { deltaSchedule(true); }
    });
  });
  $("delta-crew-preview").addEventListener("click", function () {
    deltaCrew(false);
  });
  $("delta-crew-apply").addEventListener("click", function () {
    appConfirm(t("app.delta.confirmApply")).then(function (yes) {
      if (yes) { deltaCrew(true); }
    });
  });
  $("delta-journal").addEventListener("click", deltaJournal);
  $("btn-refresh-studio").addEventListener("click", refreshStudio);
  if ($("btn-refresh-settings")) {
    $("btn-refresh-settings").addEventListener("click", refreshSettings);
  }
  $("fs-create").addEventListener("click", function () {
    var name = $("fs-new-name").value.trim();
    if (!name) { toast(t("app.t.needName"), "error"); return; }
    api("/studio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name,
                             country: $("fs-new-country").value.trim() })
    }).then(function () {
      $("fs-new-name").value = ""; $("fs-new-country").value = "";
      toast(t("app.fs.created"), "ok");
      refreshStudio();
    }).catch(function (err) { apiErrorToast(err, t("app.fs.load")); });
  });
  $("btn-ops-import").addEventListener("click", handleOpsImport);
  $("btn-ops-generate").addEventListener("click", handleOpsGenerate);
  $("btn-ops-clear").addEventListener("click", handleOpsClear);
  $("ops-scrubber").addEventListener("input", function () {
    opsState.cursorMin = parseInt(this.value, 10) || 0;
    $("ops-scrubber-val").textContent = opsState.cursorMin >= 1440
      ? t("html.ops.allDay") : _hhmm(opsState.cursorMin);
    renderOpsTable();
  });
  document.querySelectorAll("#lab-sort [data-lab-sort]").forEach(
    function (b) {
      b.addEventListener("click", function () {
        var key = b.getAttribute("data-lab-sort");
        if (state.labSort === key) {
          // повторный щелчок по активному признаку — обратный порядок
          state.labSortDir = state.labSortDir === "asc" ? "desc" : "asc";
        } else {
          state.labSort = key;
          state.labSortDir = "desc";
        }
        labSortIndicators();
        renderLabTable(labSorted());
      });
    });
  labSortIndicators();
  $("btn-lab-score").addEventListener("click", function () {
    api("/evolution/score", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    })
      .then(function () { startLabScorePolling(); })
      .catch(function (err) {
        if (err.status === 409) { startLabScorePolling(); return; }
        apiErrorToast(err, t("app.lab.scoreFail"));
      });
  });
  $("btn-refresh-lab").addEventListener("click", refreshLab);
  var labScanBtn = $("btn-lab-scan");
  if (labScanBtn) { labScanBtn.addEventListener("click", triggerLabScan); }

  var adviseBtn = $("btn-advise");
  if (adviseBtn) { adviseBtn.addEventListener("click", advise); }
  var scopeChip = $("advisor-scope-chip");
  if (scopeChip) {
    scopeChip.addEventListener("click", toggleScope);
    keyActivate(scopeChip, toggleScope);
  }
  var kbBuildBtn = $("btn-kb-build");
  if (kbBuildBtn) { kbBuildBtn.addEventListener("click", kbBuild); }
  var kbRefreshBtn = $("btn-kb-refresh");
  if (kbRefreshBtn) { kbRefreshBtn.addEventListener("click", refreshKb); }

  initStickyHScroll();         // sticky horizontal bars for wide tables

  // Запрет обработки файла браузером — со старта страницы, до первого
  // ответа /api/flow (ревизия 07.09.2026): пока схема не построена или
  // сервер не ответил, файл, брошенный мимо приёмника, всё равно не
  // открывается вместо интерфейса. Повторный вызов из _flowEnsureDom
  // безвреден (идемпотентность по _flowDropGuardWired).
  _flowWireWindowDrop();

  _crunsInit();                // экран «Прогоны» и чип прогона в шапке

  showTab("flow");             // главный экран — конвейер (2026-08-28)
  loadMe();
  refreshInstances();
  loadSchema();
  loadAirlineConfig();
  loadCostConfig();
  loadPayConfig();
}

document.addEventListener("DOMContentLoaded", init);

/* ================= Главный экран: конвейер COSMOS ==================
 * Поручение владельца 2026-08-28 (ревизия 2 по пяти замечаниям):
 * визуальный язык — КОПИЯ CDPM (две части одного приложения):
 * тёмный холст, карточки с градиентом этапа и рамкой цвета этапа,
 * пульс активного блока тем же ключевым кадром cf-border-pulse.
 * Блоки ПЕРЕТАСКИВАЮТСЯ (раскладка хранится в localStorage, кнопка
 * сброса — в шапке); рёбра и рамки групп рисуются поверх SVG и
 * следуют за блоками. Щелчок по блоку открывает КАРТОЧКУ блока
 * (статус, параметры, переход на рабочий экран, у отбора и
 * ростеринга — выбор движка), а не уводит на другой экран.
 * Данные — GET /api/flow; опрос раз в 2 с пока вкладка видима. */
var FLOW_STYLE = {
  vmp:      { g1: "#475569", g2: "#334155", br: "#64748b",
              ico: "📄" },
  conform:  { g1: "#0891b2", g2: "#0369a1", br: "#22d3ee",
              ico: "⚖️" },
  "pairing.pool":     { g1: "#2563eb", g2: "#4338ca", br: "#60a5fa",
                        ico: "🧩" },
  "pairing.escalate": { g1: "#f59e0b", g2: "#ea580c", br: "#fbbf24",
                        ico: "🔁" },
  "pairing.select":   { g1: "#9333ea", g2: "#6d28d9", br: "#c084fc",
                        ico: "🎯" },
  "pairing.arbiter":  { g1: "#10b981", g2: "#0d9488", br: "#34d399",
                        ico: "🤝" },
  // Блоки проверок до итога — нейтральные (индиго / фиолетовый): зелёный,
  // жёлтый и красный зарезервированы за оценкой итога (FLOW_GRADE);
  // прежний малиновый у «Проверки ростера по ИДАК» читался как «красный =
  // нарушения» (замечание владельца на демо 06.09.2026).
  accept:   { g1: "#4f46e5", g2: "#3730a3", br: "#818cf8",
              ico: "✅" },
  roster:   { g1: "#0284c7", g2: "#0e7490", br: "#38bdf8",
              ico: "👥" },
  out:      { g1: "#65a30d", g2: "#15803d", br: "#a3e635",
              ico: "📅" },
  srccheck: { g1: "#4f46e5", g2: "#3730a3", br: "#818cf8",
              ico: "🔍" },
  "pair.src":   { g1: "#7c3aed", g2: "#5b21b6", br: "#a78bfa",
                  ico: "📚" },
  "roster.src": { g1: "#7c3aed", g2: "#5b21b6", br: "#a78bfa",
                  ico: "📚" },
  disrupt:  { g1: "#475569", g2: "#334155", br: "#64748b",
              ico: "⚡" }
};
/* Схема конвейера по пакету постановки (слово владельца 05.09.2026:
 * каждый блок обязан работать и мигать в свой момент, блоки без
 * своего момента исключены — «Эскалация», «Арбитраж», «Сбой»).
 * Моменты: Постановка — приём пакета; Проверка соответствия — гейт
 * пакета, покрытие исполнителями и матрица готовности (Ш-1…Ш-2и);
 * Пул цепочек — построитель запущен (Ш-2); Отбор — построитель до
 * каталога (Ш-2…Ш-3); Приёмка — сертификация пейринга (Ш-3…Ш-4г);
 * Ростеринг — решатель (Ш-2…Ш-2а); Сверка с документами — сертификация
 * ростера (Ш-3…Ш-4г); Решения — сертифицированный ростер. */
var FLOW_BLOCKS = {
  vmp:      { i18n: "flow.b.vmp",     go: ["data", "data"] },
  conform:  { i18n: "flow.b.conform", go: ["data", "rules"] },
  "pairing.pool":     { i18n: "flow.b.pool",     go: ["opt", "runs"] },
  "pairing.select":   { i18n: "flow.b.select",   go: ["opt", "jobs"] },
  accept:   { i18n: "flow.b.accept",  go: ["opt", "results"] },
  "pair.src":   { i18n: "flow.b.pair.src",   go: ["opt", "results"] },
  roster:   { i18n: "flow.b.roster",  go: ["opt", "jobs"] },
  srccheck: { i18n: "flow.b.srccheck", go: ["opt", "results"] },
  "roster.src": { i18n: "flow.b.roster.src", go: ["opt", "results"] },
  out:      { i18n: "flow.b.out",     go: ["crew", "board"] }
};
/* Приёмники файлов на блоках схемы (слово владельца 06.09.2026 — по
 * образцу единообразной загрузки на блок в панели CDPM): приёмник
 * объявляется данными, не условием по имени блока. Сегодня файл
 * принимает только «Постановка (ВМП)»: один zip пакета постановки, тем
 * же путём, что кнопка панели (POST /api/task-package/upload — гейт
 * производителя и матрица готовности; пакет становится текущим; расчёт
 * не стартует). Остальные блоки работают по артефактам текущего пакета
 * и нажатий, внешнего входа файлом у них нет: при наведении файла они
 * не реагируют, брошенный на них файл ловит запрет на window с отказом
 * словами. Что могло бы принимать файлы позже (серверных ручек сегодня
 * нет — включать только вместе с ними): «Пейринг» — утверждённый
 * каталог цепочек файлом; «Проверка … по ИДАК» — решение файлом для
 * слепого читателя; «Проверка соответствия» — документ постановки. */
var FLOW_DROP = {
  vmp: { accept: [".zip"], single: true }
};
/* Слово владельца 06.09.2026: сверка с исходными документами (ИДАК) —
 * отдельный блок у КАЖДОГО решения: после сертификата по ВМП идёт
 * блок по ИДАК; ряды пейринга и выхода — по четыре блока. */
var FLOW_POS0 = {
  vmp: [40, 60], conform: [330, 60],
  "pairing.pool": [40, 255], "pairing.select": [330, 255],
  accept: [620, 255], "pair.src": [910, 255],
  roster: [40, 460], srccheck: [330, 460], "roster.src": [620, 460],
  out: [910, 460]
};
/* [из, в, ключ подписи | null, пунктир 0/1] */
var FLOW_EDGES = [
  ["vmp", "conform", null, 0],
  ["conform", "pairing.pool", null, 0],
  ["pairing.pool", "pairing.select", null, 0],
  ["pairing.select", "accept", null, 0],
  ["accept", "pair.src", null, 0],
  ["pair.src", "roster", null, 0],
  ["roster", "srccheck", null, 0],
  ["srccheck", "roster.src", null, 0],
  ["roster.src", "out", null, 0]
];
var FLOW_FRAMES = [
  { i18n: "flow.f.input", ids: ["vmp", "conform"] },
  { i18n: "flow.f.pairing",
    ids: ["pairing.pool", "pairing.select", "accept", "pair.src"] },
  { i18n: "flow.f.out", ids: ["roster", "srccheck", "roster.src", "out"] }
];
var FLOW_PARAM_LABEL = {
  candidates: "flow.p.candidates", duties: "flow.p.duties",
  orphans: "flow.p.orphans", esc_step: "flow.p.esc_step",
  esc_covered: "flow.p.esc_covered", esc_proved: "flow.p.esc_proved",
  objective: "flow.p.objective", bound: "flow.p.bound",
  gap_pct: "flow.p.gap", elapsed_s: "flow.p.elapsed",
  engine: "flow.p.engine",
  rules: "flow.p.rules", instance: "flow.p.instance",
  source_run: "flow.p.source_run",
  run: "flow.p.run",
  status: "flow.p.status", created: "flow.p.created",
  checks: "flow.p.checks", violated: "flow.p.violated",
  not_checkable: "flow.p.notcheck", reader: "flow.p.reader",
  step: "flow.p.step", verdict: "flow.p.verdict", chains: "flow.p.chains"
};
// v3: добавлен блок «Сверка с документами а/к» (srccheck) — бамп
// ключа сбрасывает сохранённые раскладки, иначе новый блок ложился
// поверх прежней позиции «Решений». v4 (06.09.2026): блоки ИДАК
// pair.src / roster.src, ряды по четыре — прежние раскладки не годятся.
// v5 (06.09.2026, вечер, замечание владельца по Prod): холст растёт по
// содержимому и блоки больше не выдавливаются вверх фиксированной
// высотой — сохранённые раскладки v4 с «уехавшими» блоками ИДАК не годятся.
var _FLOW_POS_KEY = "cosmosFlowPos5";

function _flowLoadPos() {
  var pos = {};
  Object.keys(FLOW_POS0).forEach(function (k) {
    pos[k] = FLOW_POS0[k].slice();
  });
  try {
    var raw = localStorage.getItem(_FLOW_POS_KEY);
    if (raw) {
      var saved = JSON.parse(raw);
      Object.keys(saved).forEach(function (k) {
        if (pos[k] && Array.isArray(saved[k])) {
          pos[k] = [Number(saved[k][0]) || 0,
                    Number(saved[k][1]) || 0];
        }
      });
    }
  } catch (e) { /* повреждённое хранилище — раскладка по умолчанию */ }
  return pos;
}

function _flowSavePos() {
  try {
    localStorage.setItem(_FLOW_POS_KEY, JSON.stringify(_flowPos));
  } catch (e) { /* квота/приватный режим — раскладка не сохранится */ }
}

function _fmtFlowVal(key, val) {
  if (key === "gap_pct") { return val + "%"; }
  if (key === "elapsed_s") { return val + " с"; }
  return String(val);
}

function _svg(tag, attrs) {
  var n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.keys(attrs || {}).forEach(function (k) {
    n.setAttribute(k, attrs[k]);
  });
  return n;
}

var _flowPos = null;
var _flowDom = null;  // { inner, holder, svg, nodes, frameLabels, pop }

/* Масштаб схемы: кнопки на холсте + Ctrl+колесо; хранится рядом
 * с раскладкой. Позиции блоков всегда в НЕмасштабированных
 * координатах — масштаб только визуальный (transform). */
// Масштаб при загрузке — 80 % (слово владельца 06.09.2026: блоки на
// экране слишком велики, уменьшить на 20 %); ключ v2, чтобы сохранённые
// 100 % прежних сеансов не перебивали новое умолчание. «⌂» возвращает к
// этому умолчанию.
var FLOW_ZOOM_DEFAULT = 0.8;
var _FLOW_ZOOM_KEY = "cosmosFlowZoom2";
var _flowZoom = FLOW_ZOOM_DEFAULT;
//: минимальный размер холста (как прежняя фиксированная величина CSS)
var FLOW_INNER_MIN = [1180, 630];

/* Холст растёт по содержимому: правый/нижний край — по самому дальнему
 * блоку с полем, не меньше минимума. Блоки с длинными показаниями
 * (проверки по ИДАК) прежде упирались в фиксированные 630 px и
 * выдавливались вверх — рамки групп «разъезжались», блок казался
 * неподвижным (замечание владельца по Prod, 06.09.2026). */
function _flowFitInner() {
  if (!_flowDom || !_flowDom.inner) { return; }
  var maxX = 0, maxY = 0;
  Object.keys(_flowDom.nodes).forEach(function (id) {
    var r = _flowRect(id);
    maxX = Math.max(maxX, r.x + r.w);
    maxY = Math.max(maxY, r.y + r.h);
  });
  _flowDom.inner.style.width = Math.max(FLOW_INNER_MIN[0], maxX + 60) + "px";
  _flowDom.inner.style.height = Math.max(FLOW_INNER_MIN[1], maxY + 60) + "px";
}

function _flowApplyZoom() {
  if (!_flowDom || !_flowDom.holder) { return; }
  var w = _flowDom.inner.offsetWidth || FLOW_INNER_MIN[0];
  var h = _flowDom.inner.offsetHeight || FLOW_INNER_MIN[1];
  _flowDom.holder.style.width = Math.round(w * _flowZoom) + "px";
  _flowDom.holder.style.height = Math.round(h * _flowZoom) + "px";
  _flowDom.inner.style.transform = "scale(" + _flowZoom + ")";
  var pct = $("flow-zoom-pct");
  if (pct) { pct.textContent = Math.round(_flowZoom * 100) + "%"; }
}

function _flowSetZoom(z) {
  _flowZoom = Math.max(0.4, Math.min(2, z));
  try {
    localStorage.setItem(_FLOW_ZOOM_KEY, String(_flowZoom));
  } catch (e) { /* приватный режим — масштаб не сохранится */ }
  _flowApplyZoom();
}

function _flowRect(id) {
  var nd = _flowDom.nodes[id];
  return { x: _flowPos[id][0], y: _flowPos[id][1],
           w: nd.root.offsetWidth || 220,
           h: nd.root.offsetHeight || 140 };
}

/* Точка на границе прямоугольника по лучу из центра к цели. */
function _flowAnchor(r, tx, ty) {
  var cx = r.x + r.w / 2, cy = r.y + r.h / 2;
  var dx = tx - cx, dy = ty - cy;
  if (!dx && !dy) { return { x: cx, y: cy }; }
  var sx = dx ? (r.w / 2) / Math.abs(dx) : Infinity;
  var sy = dy ? (r.h / 2) / Math.abs(dy) : Infinity;
  var s = Math.min(sx, sy);
  return { x: cx + dx * s, y: cy + dy * s };
}

/* Перетаскивание семейства блоков целиком (замечание владельца по Prod,
 * 06.09.2026): за подпись рамки либо за пустое место внутри рамки — все
 * блоки группы сдвигаются на один вектор; слушатели на window, потому что
 * сама рамка перерисовывается на каждом шаге. Сдвиг ограничен так, чтобы
 * самый левый/верхний блок группы не ушёл за край холста. */
function _flowStartGroupDrag(fi, ev) {
  if (ev.button !== 0 || !_flowDom) { return; }
  var ids = FLOW_FRAMES[fi].ids;
  var start = {};
  ids.forEach(function (id) { start[id] = _flowPos[id].slice(); });
  var minX = Infinity, minY = Infinity;
  ids.forEach(function (id) {
    minX = Math.min(minX, start[id][0]); minY = Math.min(minY, start[id][1]);
  });
  var x0 = ev.clientX, y0 = ev.clientY, moved = false;
  var lab = _flowDom.frameLabels[fi];
  lab.classList.add("dragging");
  ids.forEach(function (id) { _flowDom.nodes[id].root.classList.add("dragging"); });
  _flowClosePop();
  function onMove(e) {
    var dx = Math.max(-minX, (e.clientX - x0) / _flowZoom);
    var dy = Math.max(-minY, (e.clientY - y0) / _flowZoom);
    if (Math.abs(dx) + Math.abs(dy) > 3) { moved = true; }
    ids.forEach(function (id) {
      _flowPos[id] = [start[id][0] + dx, start[id][1] + dy];
      var root = _flowDom.nodes[id].root;
      root.style.left = _flowPos[id][0] + "px";
      root.style.top = _flowPos[id][1] + "px";
    });
    _flowDraw();
  }
  function onUp() {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    window.removeEventListener("pointercancel", onUp);
    lab.classList.remove("dragging");
    ids.forEach(function (id) { _flowDom.nodes[id].root.classList.remove("dragging"); });
    if (moved) { _flowSavePos(); }
  }
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", onUp);
  ev.preventDefault();
  ev.stopPropagation();
}

function _flowDraw() {
  if (!_flowDom) { return; }
  _flowFitInner();
  var svg = _flowDom.svg;
  while (svg.lastChild && svg.lastChild.tagName !== "defs") {
    svg.removeChild(svg.lastChild);
  }
  // рамки групп — пунктир вокруг фактического охвата блоков; рамка
  // ловит указатель (пустое место внутри — перетаскивание семейства)
  FLOW_FRAMES.forEach(function (fr, i) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    fr.ids.forEach(function (id) {
      var r = _flowRect(id);
      x0 = Math.min(x0, r.x); y0 = Math.min(y0, r.y);
      x1 = Math.max(x1, r.x + r.w); y1 = Math.max(y1, r.y + r.h);
    });
    var rect = _svg("rect", {
      x: x0 - 16, y: y0 - 22, width: (x1 - x0) + 32,
      height: (y1 - y0) + 38, rx: 14, fill: "rgba(148,163,184,0.035)",
      stroke: "#475569", "stroke-dasharray": "7 5",
      "stroke-width": 1.5, "data-frame": String(i),
      style: "pointer-events: all; cursor: grab" });
    rect.addEventListener("pointerdown", function (ev) {
      _flowStartGroupDrag(i, ev);
    });
    svg.appendChild(rect);
    var lab = _flowDom.frameLabels[i];
    lab.style.left = (x0 - 4) + "px";
    lab.style.top = (y0 - 32) + "px";
  });
  _flowApplyZoom();
  // рёбра — из фактических границ блоков, стрелка у входа
  FLOW_EDGES.forEach(function (e) {
    var ra = _flowRect(e[0]), rb = _flowRect(e[1]);
    var ca = { x: ra.x + ra.w / 2, y: ra.y + ra.h / 2 };
    var cb = { x: rb.x + rb.w / 2, y: rb.y + rb.h / 2 };
    var loop = !!e[2];
    if (loop) {
      // возвратное ребро: дуга над блоками, подпись у вершины
      var a = { x: ca.x, y: ra.y }, b = { x: cb.x, y: rb.y };
      var mx = (a.x + b.x) / 2;
      var my = Math.min(a.y, b.y) - 64;
      svg.appendChild(_svg("path", {
        d: "M" + a.x + "," + a.y + " Q" + mx + "," + my + " " +
           b.x + "," + b.y,
        fill: "none", stroke: "#f59e0b", "stroke-width": 2,
        "stroke-dasharray": "6 4", "marker-end": "url(#fl-arr-amb)"
      }));
      var txt = _svg("text", {
        x: mx, y: my + 26, fill: "#fbbf24", "font-size": "11",
        "text-anchor": "middle" });
      txt.textContent = "↺ " + t(e[2]);
      svg.appendChild(txt);
    } else {
      var pa = _flowAnchor(ra, cb.x, cb.y);
      var pb = _flowAnchor(rb, ca.x, ca.y);
      svg.appendChild(_svg("line", {
        x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y,
        stroke: "#64748b", "stroke-width": 2,
        "stroke-dasharray": e[3] ? "6 4" : "none",
        "marker-end": "url(#fl-arr)" }));
    }
  });
}

function _flowClosePop() {
  if (_flowDom && _flowDom.pop) {
    _flowDom.pop.remove();
    _flowDom.pop = null;
  }
}

/* Селектор движка блока: зеркало селектора формы запуска.
 * null — у блока движок не настраивается. */
function _flowEngineControl(id) {
  var mirror = null, choices = null, labKey = null;
  if (id === "pairing.select") {
    mirror = $("stage1-select-engine");
    labKey = "html.label.selectEngine";
    choices = [["cpsat", "html.opt.engCpsat"],
               ["cplex", "html.opt.engCplex"],
               ["highs", "html.opt.engHighs"]];
  } else if (id === "roster") {
    mirror = $("stage2-roster-engine");
    labKey = "html.label.rosterEngine";
    choices = [["timefold", "html.opt.rengTf"],
               ["cpsat", "html.opt.rengCpsat"]];
  } else {
    return null;
  }
  var sel = el("select", {});
  choices.forEach(function (c) {
    sel.appendChild(el("option", { value: c[0], text: t(c[1]) }));
  });
  if (mirror && mirror.value) { sel.value = mirror.value; }
  sel.addEventListener("change", function () {
    if (mirror) { mirror.value = sel.value; }
  });
  return { labKey: labKey, sel: sel };
}

/* Диалог шестерёнки (замечание владельца 2026-08-28): выбор
 * движка — ТОЛЬКО отсюда, карточка блока движок не трогает. */
function _flowOpenGear(id) {
  _flowClosePop();
  var eng = _flowEngineControl(id);
  if (!eng) { return; }
  var pop = el("div", { class: "flow-pop" }, [
    el("span", { class: "flow-pop-close", text: "✕" }),
    el("h5", { text: t("flow.gear") + ": " +
               t(FLOW_BLOCKS[id].i18n) }),
    el("div", { class: "flow-pop-row" }, [
      el("span", { text: t(eng.labKey) })]),
    eng.sel
  ]);
  pop.querySelector(".flow-pop-close").addEventListener(
    "click", _flowClosePop);
  var r = _flowRect(id);
  var innerW = _flowDom.inner.offsetWidth || 1180;
  var px = r.x + r.w + 12;
  if (px + 270 > innerW) { px = Math.max(0, r.x - 272); }
  pop.style.left = px + "px";
  pop.style.top = Math.max(0, r.y) + "px";
  _flowDom.inner.appendChild(pop);
  _flowDom.pop = pop;
}

/* Общий каркас модального окна схемы: шапка с иконкой и крестиком,
 * закрытие по Escape и щелчку по подложке. Возвращает тело окна. */
function _flowModal(ico, title, wide) {
  _flowClosePop();
  var overlay = el("div", { class: "confirm-overlay" });
  var dlg = el("div", { class: "confirm-dialog evo-modal vmp-modal" +
                                (wide ? " vmp-modal-wide" : ""),
                        role: "dialog", "aria-modal": "true" });
  overlay.appendChild(dlg);
  // Escape закрывает только ВЕРХНЕЕ окно стопки: диалог подтверждения или
  // окно отчёта поверх карточки прогона гасят своё окно, а карточка под ними
  // остаётся (ревью 07.09: Escape в диалоге повтора закрывал и карточку).
  function onKey(e) {
    if (e.key !== "Escape") { return; }
    var overlays = document.querySelectorAll(".confirm-overlay");
    if (overlays.length && overlays[overlays.length - 1] !== overlay) { return; }
    close();
  }
  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  }
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("mousedown", function (e) {
    if (e.target === overlay) { close(); }
  });
  var x = el("button", { class: "vmp-x", type: "button", text: "✕",
                         title: t("html.close") });
  x.addEventListener("click", close);
  dlg.appendChild(el("div", { class: "vmp-head" }, [
    el("span", { class: "vmp-ico", text: ico }),
    el("h3", { text: title }), x]));
  var body = el("div", {});
  dlg.appendChild(body);
  document.body.appendChild(overlay);
  return { body: body, close: close };
}

/* Панель расчёта у блока схемы (слово владельца 05.09.2026): пейринг
 * запускается в блоке «Отбор (оптимизация)», ростеринг — в блоке
 * «Ростеринг», настройки задачи (движок / решатель, предел счёта) —
 * здесь же; сверху — короткая карточка текущего пакета. */
function flowOpenTaskPanel(task) {
  var sty = FLOW_STYLE[task === "rostering" ? "roster" : "pairing.select"];
  var m = _flowModal(sty.ico, t(task === "rostering"
    ? "flow.task.rostering.h" : "flow.task.pairing.h"), false);
  var brief = el("p", { class: "vmp-pkg-brief",
                        text: t("flow.task.loading") });
  m.body.appendChild(brief);
  var host = el("div", {});
  m.body.appendChild(host);
  function load() {
    host.textContent = "";
    api("/task-package/current").then(function (cd) {
      if (!cd || !cd.present) {
        brief.textContent = t("flow.task.nopkg");
        vmpRunBlock(host, false, load, { task: task });
        return;
      }
      var man = cd.manifest || {};
      brief.textContent = t("flow.task.pkg") + ": " +
        String(cd.sha256 || "").slice(0, 12) +
        (man.source_run ? " · " + String(man.source_run) : "") +
        (man.revision != null ? " · " + t("flow.vmp.cur.rev") + " " +
                                 String(man.revision) : "") +
        (man.customer ? " · " + String(man.customer) : "");
      vmpRunBlock(host, true, load, { task: task });
    }).catch(function (e) {
      brief.textContent = String((e && e.message) || e);
    });
  }
  load();
}

/* Окно «Карта сети и персонал» — отдельная кнопка на схеме (слово
 * владельца 05.09.2026): схема сети, карта мира, персонал по базам. */
function flowOpenNetPanel() {
  var m = _flowModal("🗺", t("flow.map.h"), true);
  var note = el("p", { class: "vmp-pkg-brief", text: t("flow.task.loading") });
  m.body.appendChild(note);
  var host = el("div", {});
  m.body.appendChild(host);
  api("/task-package/current/map").then(function (net) {
    var n = (net.nodes || []).length, a = (net.arcs || []).length;
    var flights = (net.arcs || []).reduce(function (s, x) {
      return s + (x.n || 0);
    }, 0);
    note.textContent = tf("flow.map.brief", {
      bases: n, arcs: a, flights: flights,
      people: (net.employees || []).length });
    vmpNetBlock(host, net);
  }).catch(function (e) {
    note.textContent = String((e && e.body && e.body.detail) ||
                              (e && e.message) || t("flow.map.none"));
  });
}

/* Выбор локального файла без формы в разметке (паттерн rpImportFile):
 * кнопка -> диалог выбора -> текст файла в обработчик. */
function _vmpFilePick(accept, cb) {
  var input = el("input", { type: "file", accept: accept });
  input.addEventListener("change", function () {
    var f = input.files && input.files[0];
    if (f) { f.text().then(function (txt) { cb(f, txt); }); }
  });
  input.click();
}

/* Панель постановки (клик по блоку «ВМП», поручение владельца
 * 2026-08-28): ввод данных прямо с конвейера. Четыре секции:
 * документ постановки (показ разобранных правил + загрузка),
 * наборы данных (сводка + загрузка канонического JSON / выгрузки
 * авиакомпании; остальные форматы — переход на экран «Данные»),
 * матрица готовности ВМП (верификация COSMOS, пометка расхождений),
 * пакет постановки CDPM — живая карточка /api/task-package/current.
 * Загрузки пакета здесь по-прежнему нет: пакет приходит файлом
 * (CREW_TASK_PACKAGE) или каналом приёма — граница двух систем. */
// The accepted package reader supplies these words. Never infer acceptance
// from a missing report, and never treat a projection check as permission to run.
function vmpProjectionBlock(host, report) {
  var box = el("div", { class: "vmp-projection" });
  var known = report && (report.judged === true || report.judged === false);
  box.appendChild(el("p", { class: "vmp-note", text: report && report.words
    ? String(report.words) : "Статус сверки проекции не установлен" }));
  if (known && report.judged === true) {
    var cat = report.catalog || {};
    box.appendChild(el("p", { class: "vmp-note", text:
      "Источник направления — каталог " + String(cat.version || "не установлен") +
      "; отпечаток: " + String(cat.fingerprint || "не установлен") }));
    (report.directions || []).forEach(function (row) {
      var words = String(row.task) + ": " + String(row.direction) +
        "; основание: " + String(row.basis || "не установлено");
      if (row.criterion) {
        words += "; критерий " + String(row.criterion.kind) +
          " → " + String(row.criterion.direction);
      }
      box.appendChild(el("p", { class: "vmp-note", text: words }));
    });
  }
  host.appendChild(box);
}

function flowOpenVmpPanel() {
  _flowClosePop();
  var overlay = el("div", { class: "confirm-overlay" });
  var dlg = el("div", { class: "confirm-dialog evo-modal vmp-modal",
                        role: "dialog", "aria-modal": "true" });
  overlay.appendChild(dlg);
  function onKey(e) { if (e.key === "Escape") { close(); } }
  function close() {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    // Панель закрыта — живого блока записи о прогоне больше нет: сигналы
    // блока расчёта не должны читать запись в снятую с экрана карточку.
    _vmpRecord = null;
    _vmpTree = null;
  }
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("mousedown", function (e) {
    if (e.target === overlay) { close(); }
  });
  var x = el("button", { class: "vmp-x", type: "button", text: "✕",
                         title: t("html.close") });
  x.addEventListener("click", close);
  dlg.appendChild(el("div", { class: "vmp-head" }, [
    el("span", { class: "vmp-ico", text: FLOW_STYLE.vmp.ico }),
    el("h3", { text: t("flow.vmp.title") }), x]));

  // --- секция 1: документ постановки --------------------------------
  var docBody = el("div", {});
  var docBtn = el("button", { class: "btn btn-primary",
                              type: "button",
                              text: t("flow.vmp.doc.upload") });
  docBtn.addEventListener("click", function () {
    _vmpFilePick(".md,.markdown,.json", function (f, txt) {
      docBtn.disabled = true;
      api("/rules/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: txt })
      }).then(function (st) {
        toast(t("flow.vmp.uploaded"), "ok");
        renderVmpDoc(st);
        renderRules(st);
      }).catch(function (e) {
        toast((e && e.message) ? String(e.message) : String(e),
              "error");
      }).finally(function () { docBtn.disabled = false; });
    });
  });
  // Слово владельца 05.09.2026: в блоке «Постановка (ВМП)» — только приём
  // пакета постановки; документ .md, наборы данных и матрица готовности
  // здесь не показываются (матрица — в блоке «Проверка соответствия»,
  // наборы — на экране «Данные»).
  var VMP_PACKAGE_ONLY = true;
  if (!VMP_PACKAGE_ONLY) dlg.appendChild(el("div", { class: "vmp-sec" }, [
    el("h4", { text: t("flow.vmp.doc") }), docBody,
    el("div", { class: "vmp-actions" }, [docBtn])]));
  function renderVmpDoc(st) {
    docBody.textContent = "";
    if (!st || !st.loaded || st.error) {
      docBody.appendChild(el("p", { class: "vmp-note",
        text: st && st.error ? String(st.error)
                             : t("flow.vmp.doc.none") }));
      return;
    }
    docBody.appendChild(el("div", { class: "vmp-row" }, [
      el("span", { text: "SHA-256" }),
      el("span", { class: "mono",
                   text: String(st.sha || "").slice(0, 12) })]));
    docBody.appendChild(el("div", { class: "vmp-row" }, [
      el("span", { text: t("flow.vmp.doc.rules") }),
      el("span", { text: String(Object.keys(st.rules || {}).length)
      })]));
  }
  api("/rules").then(renderVmpDoc).catch(function () {
    renderVmpDoc(null);
  });

  // --- секция 2: наборы данных --------------------------------------
  var instBody = el("div", {});
  function upInstance(format) {
    _vmpFilePick(".json", function (f, txt) {
      var payload;
      try { payload = JSON.parse(txt); }
      catch (e) { toast(String(e), "error"); return; }
      var name = String(f.name || "set").replace(/\.[^.]+$/, "");
      api("/instances/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, payload: payload,
                               format: format })
      }).then(function () {
        toast(t("flow.vmp.instok"), "ok");
        refreshInstances().then(renderVmpInst);
      }).catch(function (e) { apiErrorToast(e, t("flow.err")); });
    });
  }
  var upCanon = el("button", { class: "btn btn-primary",
                               type: "button",
                               text: t("flow.vmp.up.canonical") });
  upCanon.addEventListener("click", function () {
    upInstance("canonical");
  });
  var upAir = el("button", { class: "btn btn-ghost", type: "button",
                             text: t("flow.vmp.up.airline") });
  upAir.addEventListener("click", function () {
    upInstance("airline");
  });
  var goData = el("button", { class: "btn btn-ghost", type: "button",
                              text: t("flow.vmp.go.more") });
  goData.addEventListener("click", function () {
    close();
    showGroup("data"); showTab("data");
  });
  if (!VMP_PACKAGE_ONLY) dlg.appendChild(el("div", { class: "vmp-sec" }, [
    el("h4", { text: t("flow.vmp.inst") }), instBody,
    el("div", { class: "vmp-actions" }, [upCanon, upAir, goData])]));
  function renderVmpInst() {
    instBody.textContent = "";
    var list = state.instances || [];
    if (!list.length) {
      instBody.appendChild(el("p", { class: "vmp-note",
                                     text: t("flow.vmp.inst.none") }));
      return;
    }
    var rows = list.map(function (inst) {
      return el("tr", null, [
        el("td", { class: "mono", text: instLabel(inst) }),
        el("td", { class: "num", text: fmt(inst.legs) }),
        el("td", { class: "num", text: fmt(inst.employees) }),
        el("td", { class: "num", text: fmt(inst.slots) }),
        el("td", null, [verdictBadge(inst.last_gate_status)])]);
    });
    instBody.appendChild(el("div", { class: "vmp-table-wrap" }, [
      el("table", null, [
        el("thead", null, [el("tr", null, [
          el("th", { text: t("html.th.id") }),
          el("th", { class: "num", text: t("html.th.legs") }),
          el("th", { class: "num", text: t("html.th.employees") }),
          el("th", { class: "num", text: t("html.th.slots") }),
          el("th", { text: t("flow.vmp.verdict") })])]),
        el("tbody", null, rows)])]));
  }
  if ((state.instances || []).length) { renderVmpInst(); }
  refreshInstances().then(renderVmpInst).catch(function () {});

  // --- секция 3: матрица готовности ВМП -----------------------------
  // (директива владельца 2026-08-29: формирует CDPM, верифицирует
  // COSMOS; расхождение клетки = остановка запуска)
  var rdBody = el("div", {});
  if (!VMP_PACKAGE_ONLY) dlg.appendChild(el("div", { class: "vmp-sec" }, [
    el("h4", { text: t("flow.ready.h") }), rdBody]));
  function renderReady(rd) {
    rdBody.textContent = "";
    if (!rd || !rd.present) {
      rdBody.appendChild(el("p", { class: "vmp-note",
        text: (rd && rd.error) ? String(rd.error)
                               : t("flow.ready.none") }));
      return;
    }
    var rows = [], mism = [];
    ["pairing", "rostering"].forEach(function (task) {
      var c = (rd.columns || {})[task];
      if (!c) { return; }
      var why = (c.why_blocked || []).join("; ");
      // Примечания клетки от CDPM (например, «критерия оптимизации
      // нет — возможен только feasibility-soft») — рядом с
      // основаниями, и при разрешённом запуске тоже.
      var notes = (c.notes || []).join("; ");
      var whyCell = [why, notes].filter(Boolean).join(" · ");
      // пометка расхождения: клетка манифеста CDPM не совпала с
      // пересчётом COSMOS (match === false — поле даёт flow.py)
      var stCell = [el("span", {
        class: "flow-ready-chip rd-" + c.state +
          (c.launch_allowed ? "" : " rd-locked"),
        text: t("flow.ready.st." + c.state) })];
      if (c.match === false) {
        mism.push(t("flow.ready.col." + task));
        stCell.push(el("span", { class: "chip chip-bad", text: "⚠",
                                 title: t("flow.ready.mismatch") }));
      }
      rows.push(el("tr", null, [
        el("td", { text: t("flow.ready.col." + task) }),
        el("td", null, stCell),
        el("td", { text: t(c.launch_allowed
          ? "flow.ready.allowed" : "flow.ready.blocked"),
          title: why }),
        el("td", { class: "num",
                   text: fmt(c.zone_total) }),
        el("td", { class: "num",
                   text: fmt(c.registry_gaps) }),
        el("td", { class: "num", text: fmt(c.strict_interim) }),
        el("td", { text: whyCell || "—" })]));
    });
    rdBody.appendChild(el("div", { class: "vmp-table-wrap" }, [
      el("table", null, [
        el("thead", null, [el("tr", null, [
          el("th", { text: "" }),
          el("th", { text: t("flow.ready.state") }),
          el("th", { text: t("flow.ready.launch") }),
          el("th", { class: "num", text: t("flow.ready.zone") }),
          el("th", { class: "num", text: t("flow.ready.gaps") }),
          el("th", { class: "num", text: t("flow.ready.strict") }),
          el("th", { text: t("flow.ready.why") })])]),
        el("tbody", null, rows)])]));
    if (mism.length) {
      rdBody.appendChild(el("p", { class: "vmp-note", text:
        "⚠ " + mism.join(", ") + " — " + t("flow.ready.mismatch") }));
    }
    // Пробелы реестра ПОИМЁННО (шаг 6 каталога): рядом с числом —
    // правило, форма пробела и заявка каталога с её состоянием.
    var gapRows = [];
    ["pairing", "rostering"].forEach(function (task) {
      var c = (rd.columns || {})[task];
      ((c && c.registry_gap_rows) || []).forEach(function (g) {
        gapRows.push(g);
      });
    });
    if (gapRows.length) { renderGapRows(rdBody, gapRows); }
  }

  /** Таблица пробелов реестра и кнопка подачи заявок на вид. */
  function renderGapRows(host, rows) {
    host.appendChild(el("h4", { text: t("flow.gap.h") }));
    var head = ["rule", "gap", "form", "request", "state"].map(
      function (k) { return el("th", { text: t("flow.gap.th." + k) }); });
    var body = el("tbody", {}, rows.map(function (g) {
      var rq = g.request || null;
      var formKey = "flow.gap.form." + String(g.form || "");
      var cells = [
        el("td", { class: "mono small", text: g.rule_id || "" }),
        el("td", { class: "small", text: g.kind || g.note || "" }),
        el("td", { class: "small",
                   text: g.form ? t(formKey) : "—" }),
        el("td", {}, [rq && rq.link
          ? el("a", { href: rq.link, target: "_blank", rel: "noopener",
                      class: "mono small", text: rq.request_id || "" })
          : el("span", { class: "mono small",
                         text: (rq && rq.request_id) || "—" })]),
        el("td", { class: "small", text: (rq && rq.state) || "—" })
      ];
      return el("tr", {}, cells);
    }));
    host.appendChild(el("div", { class: "vmp-table-wrap" }, [
      el("table", {}, [el("thead", {}, [el("tr", {}, head)]), body])]));
    var btn = el("button", { class: "btn btn-ghost btn-small",
                             type: "button", text: t("flow.gap.submit") });
    var note = el("span", { class: "muted small" });
    btn.addEventListener("click", function () {
      btn.disabled = true;
      api("/task-package/current/press/catalog-requests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attempt: null, rule_ids: null })
      }).then(function (out) {
        note.textContent = out && out.words
          ? String(out.words)
          : tf("flow.gap.requested", {
              q: Object.keys((out && out.rows) || {}).length,
              s: t("flow.gap.auto.on") });
      }).catch(function (err) {
        apiErrorToast(err, t("app.crs.catalog.syncFail"));
      }).finally(function () { btn.disabled = false; });
    });
    host.appendChild(el("div", { class: "form-row inline-controls" },
                       [btn, note]));
  }
  if (_flowState && _flowState.readiness) {
    renderReady(_flowState.readiness);
  } else {
    api("/flow").then(function (st) {
      renderReady(st.readiness);
    }).catch(function () { renderReady(null); });
  }

  // --- секция 4: запись о прогоне -----------------------------------
  // Прогон — отдельная сущность, и её карточка стоит НАД блоком пакета и
  // попыток (проект «Запись о прогоне», раздел 10, п. 4): сначала видно,
  // какой прогон объявлен и чем, потом — что в нём нажимали. Пока прогон
  // не объявлен, здесь же форма «Новый прогон от пакета»: без объявления
  // нажатия расчёта не разрешены.
  vmpRecordBlock(dlg);

  // --- секция 4-бис: дерево прогонов ---------------------------------
  // База прогонов — дерево, а не список (проект, раздел 7): путь от
  // корня входов, соседние ветви того же корня и сравнение — по
  // умолчанию с золотым прогоном. Секция стоит сразу под записью: запись
  // говорит, ЧТО объявлено в этом прогоне, дерево — ГДЕ он среди своих.
  vmpTreeBlock(dlg);

  // --- секция 5: пакет постановки CDPM ------------------------------
  // Живая карточка текущего пакета (GET /api/task-package/current):
  // sha байтов zip, манифест, источник получения (файл руками /
  // канал приёма + время), правила по зонам, критерий с версией
  // ставок, карты правил и хвост журнала приёмов канала. Пакета
  // нет — честная строка «постановка не назначена» + прежний текст
  // границы двух систем.
  var pkgBody = el("div", {});
  dlg.appendChild(el("div", { class: "vmp-sec" }, [
    el("h4", { text: t("flow.vmp.pkg") }), pkgBody]));
  function pkgRow(label, value, mono) {
    return el("div", { class: "vmp-row" }, [
      el("span", { text: label }),
      el("span", mono ? { class: "mono", text: value }
                      : { text: value })]);
  }
  function pkgVerdictChip(v, repeat) {
    var cls = { OK: "chip-ok", MISMATCH: "chip-warn",
                GATE_RED: "chip-bad", REFUSED: "chip-bad"
              }[v] || "chip-neutral";
    return el("span", { class: "chip " + cls,
      text: fmt(v) + (repeat ? " · " + t("flow.vmp.cur.repeat")
                             : "") });
  }
  function renderVmpPkg(cd) {
    pkgBody.textContent = "";
    if (!cd || !cd.present) {
      pkgBody.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.cur.none") }));
      if (cd && cd.note_code) {
        // Пакета нет по причине (утерян файл, запись текущего прогона не
        // читается) — причина словами и номер прогона, не молчание.
        pkgBody.appendChild(el("p", { class: "vmp-note", text:
          (cd.run && cd.run.run ? cd.run.run + " · " : "") +
          cosmosNoteWords(cd.note_code, cd.error) }));
      }
      pkgBody.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.pkg.text") }));
      // Загрузка пакета человеком — и без текущего пакета (решение
      // владельца 04.09: расчёт по пакету живёт в COSMOS).
      vmpRunBlock(pkgBody, false, function () {
        api("/task-package/current").then(renderVmpPkg);
      }, { task: "package" });
      return;
    }
    var src = cd.source || {};
    pkgBody.appendChild(pkgRow(t("flow.vmp.cur.sha"),
      String(cd.sha256 || "").slice(0, 12), true));
    pkgBody.appendChild(pkgRow(t("flow.vmp.cur.src"),
      t(src.kind === "channel" ? "flow.vmp.cur.src.channel"
        : src.kind === "upload" ? "flow.vmp.cur.src.upload"
                                : "flow.vmp.cur.src.file") +
      (src.at ? " · " + fmtTime(src.at) : "")));
    if (cd.error) {
      // пакет назначен, но не читается — честная причина, не догадка
      pkgBody.appendChild(el("p", { class: "vmp-note",
                                    text: String(cd.error) }));
    } else {
      var man = cd.manifest || {};
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.spec"),
                                 fmt(man.spec_version)));
      vmpProjectionBlock(pkgBody, cd.objective_projection);
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.rev"),
                                 fmt(man.revision)));
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.srcrun"),
                                 fmt(man.source_run)));
      var z = cd.rules_by_zone || {};
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.zones"),
        t("flow.ready.col.pairing") + " " + fmt(z.PAIRING) + " · " +
        t("flow.ready.col.rostering") + " " + fmt(z.ROSTERING) +
        " · " + t("flow.vmp.cur.z.both") + " " + fmt(z.BOTH) +
        " · " + t("flow.vmp.cur.z.out") + " " + fmt(z.OUT)));
      var crit = cd.criterion;
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.crit"),
        crit ? fmt(crit.label || crit.source_rule) : "—"));
      var rv = crit && crit.rates_version;
      if (rv) {
        pkgBody.appendChild(pkgRow(t("flow.vmp.cur.critver"),
          fmt(rv.version) + (rv.effective_from
            ? " · " + rv.effective_from : "")));
      }
      pkgBody.appendChild(pkgRow(t("flow.vmp.cur.files"),
        String((man.files || []).length)));
      if (cd.rule_maps) {
        // карты правил (манифест 1.14+): имя подсекции + счёт записей
        var mapChips = Object.keys(cd.rule_maps).map(function (k) {
          return el("span", { class: "chip chip-neutral",
            text: k + " · " + cd.rule_maps[k] });
        });
        pkgBody.appendChild(el("div", { class: "vmp-row" }, [
          el("span", { text: t("flow.vmp.cur.maps") }),
          el("span", { class: "vmp-chips" }, mapChips)]));
      }
      // Карта сети ПОСТАНОВКИ (пакет «Карта на гео-подложке»,
      // 2026-08-30): по клику — сеть мира видна до всякого счёта;
      // тяжёлые данные не грузятся вместе с карточкой.
      var netWrap = el("div", {});
      var netBtn = el("button", { class: "rv-seg", type: "button",
                                  text: t("flow.vmp.map.open") });
      netBtn.onclick = function () {
        netBtn.disabled = true;
        api("/task-package/current/map").then(function (net) {
          netBtn.remove();
          vmpNetBlock(netWrap, net);
        }).catch(function (e) {
          netBtn.disabled = false;
          netWrap.appendChild(el("p", { class: "vmp-note",
            text: String((e && e.message) || e) }));
        });
      };
      netWrap.appendChild(netBtn);
      pkgBody.appendChild(netWrap);
      // Расчёт по пакету: загрузка, движок отбора, «Пейринг» /
      // «Ростеринг», состояние, ссылки выдачи, карта ростера.
      vmpRunBlock(pkgBody, true, function () {
        api("/task-package/current").then(renderVmpPkg);
      }, { task: "package" });
    }
    var rc = cd.receipts || [];
    if (rc.length) {
      // хвост журнала приёмов канала — свежие сверху
      pkgBody.appendChild(el("p", { class: "vmp-note",
        text: t("flow.vmp.cur.receipts") }));
      rc.slice().reverse().forEach(function (r) {
        pkgBody.appendChild(el("div", { class: "vmp-row" }, [
          el("span", { text: fmtTime(r.at) }),
          el("span", { class: "vmp-chips" }, [
            el("span", { class: "mono",
                         text: String(r.sha || "").slice(0, 12) }),
            pkgVerdictChip(r.verdict, r.repeat)])]));
      });
    }
  }
  api("/task-package/current").then(renderVmpPkg)
    .catch(function () { renderVmpPkg(null); });

  document.body.appendChild(overlay);
}

/* ============ Запись о прогоне (СХУП, шаг 2, половина COSMOS) =============
 * Проект final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md: разделы 2 «Три части
 * записи», 6 «Решения владельца», 10 (пп. 3–4 — ручка карточки и сама
 * карточка) и 11.4 (карточка панели CDPM — слова взяты те же, чтобы две
 * системы говорили о прогоне одинаково).
 *
 * Прогон — отдельная сущность: объявление до старта (голова, после старта
 * неизменна), журнал этапов ОБЕИХ систем и итоги. Здесь только показ и две
 * кнопки: «Объявить прогон» (частичный прогон от пакета) и «Повторить
 * сшивку» (очередь записей хозяину). Ничего не досочиняется: чего сервер
 * не прислал — «—», отказ — словами сервера с кодом причины.
 *
 * Один рисовальщик на два места (единообразие панелей, слово владельца
 * 06.09.2026): панель постановки (`GET /api/run/current`) и карточка
 * прогона во вкладке «Прогоны» (`GET /api/cosmos-runs/<прогон>/record`
 * отдаёт тот же ответ). */

/* Слова значений записи. Словарь `vmp/run_record.py` называет цель, исход
 * и состояние русскими словами — они же лежат в файле записи и приходят с
 * сервера. Интерфейс живёт на пяти языках, поэтому известное слово
 * переводится по коду, а незнакомое показывается как есть: честно, а не
 * «неизвестно». */
var RUN_REC_PURPOSES = {
  "приёмка": "acceptance", "демонстрация": "demo", "регресс": "regress",
  "исследование": "research", "рабочий": "work"
};
var RUN_REC_OUTCOMES = {
  "успех": "ok", "предупреждение": "warn", "отказ по правилу": "rule",
  "сбой": "failure", "остановлен человеком": "human", "не начат": "not_started"
};
var RUN_REC_STATES = {
  "объявлен": "declared", "идёт": "running", "завершён": "done",
  "остановлен по правилу": "rule", "сбой": "failure",
  "остановлен человеком": "human", "не объявлен": "undeclared"
};
/* Хозяин записи (ручка отдаёт одно из двух слов контракта раздела 10). */
var RUN_REC_OWNERS = {
  "cosmos": "cosmos", "панель/раннер по манифесту": "holder"
};
var RUN_REC_OUTCOME_CLS = {
  ok: "chip-ok", warn: "chip-warn", rule: "chip-bad", failure: "chip-bad",
  human: "chip-warn", not_started: "chip-neutral"
};

/** Слово по словарю значений: известное — переводом, незнакомое — как
 *  есть, пустое — прочерком. */
function _runRecWords(map, prefix, value) {
  if (value === null || value === undefined || value === "") { return "—"; }
  var code = map[String(value)];
  return code ? t(prefix + code) : String(value);
}

function _runRecPurposeWords(v) {
  return _runRecWords(RUN_REC_PURPOSES, "flow.vmp.record.purpose.", v);
}

function _runRecOutcomeWords(v) {
  return _runRecWords(RUN_REC_OUTCOMES, "flow.vmp.record.outcome.", v);
}

function _runRecStateWords(v) {
  return _runRecWords(RUN_REC_STATES, "flow.vmp.record.state.", v);
}

function _runRecOwnerWords(v) {
  return _runRecWords(RUN_REC_OWNERS, "flow.vmp.record.owner.", v);
}

/** Слово к коду площадки (local / test / stage / prod) и к имени системы
 *  журнала (cdpm / runner / cosmos); неизвестный код — как есть. */
function _runRecKeyWords(prefix, value) {
  if (!value) { return "—"; }
  var key = prefix + String(value);
  var w = t(key);
  return w === key ? String(value) : w;
}

function _runRecContourWords(v) {
  return _runRecKeyWords("flow.vmp.record.contour.", v);
}

function _runRecSysWords(v) {
  return _runRecKeyWords("flow.vmp.record.sys.", v);
}

function _runRecReasonWords(code) {
  return _runRecKeyWords("flow.vmp.record.reason.", code);
}

/** Короткий вид отпечатка — как в записи (`vmp/run_record.short`): человек
 *  читает первые знаки, полный отпечаток остаётся в подсказке строки. */
function _runRecShort(v) {
  var s = (v === null || v === undefined) ? "" : String(v);
  return s ? s.slice(0, 12) : "—";
}

/** Число счётчика или хронометража: пусто — прочерк, не выдуманный нуль. */
function _runRecNum(v) {
  return (v === null || v === undefined || v === "") ? "—" : String(v);
}

/** Строка «поле — значение» карточки записи (оформление соседних секций
 *  панели постановки: .vmp-row). */
function _runRecRow(host, label, value, mono) {
  var right = mono ? el("span", { class: "mono", text: value })
                   : el("span", { text: value });
  var row = el("div", { class: "vmp-row" }, [el("span", { text: label }), right]);
  host.appendChild(row);
  return row;
}

/** Строка со списком значений (модели, решатели, выдачи). */
function _runRecRows(host, label, items) {
  var right;
  if (items.length) {
    right = el("ul", { class: "run-rec-list" });
    items.forEach(function (s) { right.appendChild(el("li", { text: s })); });
  } else {
    right = el("span", { text: "—" });
  }
  host.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: label }), right]));
}

/** Модель головы словами: роль, провайдер с моделью, канал, усилие и —
 *  СИСТЕМА, чью роль называет строка (`declaration.models[].system`,
 *  договор двух систем от 11.09.2026): голову чеканит хозяин записи, и
 *  до договора модели COSMOS (сертификатор, читатель ИДАК) в неё не
 *  попадали вовсе. Система названа тем же словарём, что и система
 *  единицы журнала (`flow.vmp.record.sys.*`) — второго перечня имён
 *  систем в интерфейсе нет. Поля НЕТ — модель CDPM, и строка выглядит
 *  как прежде: все записи, объявленные раньше, остаются годными. */
function _runRecModelWords(m) {
  m = m || {};
  return cosmosRoleWords(m.role) + ": " + (m.provider ? m.provider + "/" : "") +
    fmt(m.model) + (m.channel ? " · " + m.channel : "") +
    (m.effort ? " · " + m.effort : "") +
    (m.system ? " [" + _runRecSysWords(m.system) + "]" : "");
}

function _runRecSolverWords(s) {
  s = s || {};
  var parts = [_crunsTaskLabel(s.task) + ": " + fmt(s.engine) +
               (s.version ? " " + s.version : "")];
  if (s.build_sha256) {
    parts.push(t("flow.vmp.record.build") + " " + _runRecShort(s.build_sha256));
  }
  if (s.spent_limit_s !== undefined && s.spent_limit_s !== null) {
    parts.push(tf("flow.vmp.record.limit", { n: s.spent_limit_s }));
  }
  if (s.moves) { parts.push(String(s.moves)); }
  if (s.seed !== undefined && s.seed !== null) { parts.push("seed " + s.seed); }
  return parts.join(" · ");
}

/* Голова записи словами: номер, хозяин, кто объявил, цель, площадка,
 * авиакомпания, пакет, корень дерева (отпечатки ИДАК, данных и ответов),
 * каталог и привязки, модели по ролям, решатели, код обеих систем, план
 * этапов. */
function _runRecHead(host, doc, rec) {
  var d = (rec && rec.declaration) || {};
  var head = (doc && doc.head) || {};
  var box = el("div", { class: "run-rec-head" });
  box.appendChild(el("p", { class: "run-rec-sub", text: t("flow.vmp.record.head") }));
  _runRecRow(box, t("flow.vmp.record.run_id"),
             fmt(d.run_id || head.record_id), true);
  _runRecRow(box, t("flow.vmp.record.owner"), _runRecOwnerWords(head.owner));
  _runRecRow(box, t("flow.vmp.record.by"), fmt(d.created_by) +
             (d.created_at ? " · " + fmtTime(d.created_at) : ""));
  _runRecRow(box, t("flow.vmp.record.purpose"), _runRecPurposeWords(d.purpose));
  _runRecRow(box, t("flow.vmp.record.contour"),
             _runRecContourWords(d.contour && d.contour.name));
  _runRecRow(box, t("flow.vmp.record.airline"),
             fmt((d.airline && d.airline.name) || head.customer));
  _runRecRow(box, t("flow.vmp.record.pkg"), _runRecShort(doc && doc.package_sha), true);
  var ln = d.lineage || {};
  var rootRow = _runRecRow(box, t("flow.vmp.record.root"),
    tf("flow.vmp.record.root.words", { rules: _runRecShort(ln.root_sha256),
                                       answers: _runRecShort(ln.root_answers_sha256) }),
    true);
  if (ln.root_sha256) { rootRow.title = String(ln.root_sha256); }
  _runRecRow(box, t("flow.vmp.record.lineage"),
    tf("flow.vmp.record.lineage.words", { pkg: _runRecShort(ln.package_sha256),
                                          src: fmt(ln.source_run) }), true);
  var cat = d.catalog || {};
  _runRecRow(box, t("flow.vmp.record.catalog"),
    (cat.name ? cat.name + " " : "") + fmt(cat.version) +
    (cat.fingerprint ? " · " + _runRecShort(cat.fingerprint) : ""));
  var bind = d.bindings || {};
  _runRecRow(box, t("flow.vmp.record.bindings"), fmt(bind.catalog_version) +
    (bind.catalog_fingerprint ? " · " + _runRecShort(bind.catalog_fingerprint) : ""));
  _runRecRows(box, t("flow.vmp.record.models"),
              (d.models || []).map(_runRecModelWords));
  // Неполнота раскладки — ПРЕДУПРЕЖДЕНИЕМ под перечнем моделей
  // (`declaration.models_note`, руководство 09, раздел 6д): хозяин
  // записи мог не дозваться COSMOS при объявлении, и роль, которой в
  // голове нет, названа словами, а не теряется молча. Поля нет —
  // раскладка полна, и строки тоже нет.
  if (d.models_note) {
    box.appendChild(el("p", { class: "vmp-note run-rec-bad",
      text: t("flow.vmp.record.models_note") + ": " + String(d.models_note) }));
  }
  _runRecRows(box, t("flow.vmp.record.solvers"),
              (d.solvers || []).map(_runRecSolverWords));
  var code = d.code || {};
  _runRecRow(box, t("flow.vmp.record.code"), tf("flow.vmp.record.code.words", {
    cdpm: _runRecShort(code.cdpm_commit),
    cosmos: _runRecShort(code.cosmos_commit) }), true);
  var stages = (d.planned_stages || []).join(" → ");
  _runRecRow(box, t("flow.vmp.record.stages"), (stages || "—") +
    (d.stop_after ? " · " + tf("flow.vmp.record.stop_after",
                               { stage: d.stop_after }) : ""));
  host.appendChild(box);
}

/** Журнал обеих систем по времени: записи головной записи (панель CDPM и
 *  раннер наборов) и ещё не сшитый фрагмент COSMOS — одной таблицей.
 *  Повтор по ключу (система, этап, подэтап, начало) не задваивается: одна
 *  и та же запись приходит и от хозяина, и из местного фрагмента, пока
 *  очередь сшивки не разобрана. */
function _runRecEntries(doc, rec) {
  var out = [], seen = {};
  function add(entry, fallbackSystem) {
    if (!entry) { return; }
    var sys = entry.system || fallbackSystem || "";
    var key = [sys, entry.stage || "", entry.substage || "",
               entry.started_at || ""].join("|");
    if (seen[key]) { return; }
    seen[key] = true;
    out.push({ e: entry, system: sys });
  }
  ((rec && rec.journal) || []).forEach(function (e) { add(e, ""); });
  (((doc && doc.cosmos) || {}).entries || []).forEach(function (e) {
    add(e, "cosmos");
  });
  out.sort(function (a, b) {
    var ta = Date.parse(a.e.started_at || ""), tb = Date.parse(b.e.started_at || "");
    return (isNaN(ta) ? 0 : ta) - (isNaN(tb) ? 0 : tb);
  });
  return out;
}

/** Длительность этапа: время машины и время ОЖИДАНИЯ ЧЕЛОВЕКА порознь —
 *  они меряют разное (раздел 3.2 проекта). */
function _runRecDuration(e) {
  var m = e.duration_machine_s, h = e.duration_waiting_human_s;
  if ((m === null || m === undefined) && (h === null || h === undefined)) {
    return "—";
  }
  return tf("flow.vmp.record.dur.words", { m: _runRecNum(m), h: _runRecNum(h) });
}

/** Расход моделей — В ТОКЕНАХ по каждой модели (решение владельца № 1 от
 *  10.09.2026): цена на момент прогона и на момент просмотра различаются,
 *  поэтому денег здесь нет. Канал, который расхода не отдаёт, говорит
 *  «не считается» и называет причину, а не показывает нуль.
 *
 *  Причина (`note`) стоит у ЛЮБОЙ строки, которая её несёт, — не только у
 *  несосчитанной. Сосчитанный ноль бывает измеренным: читатель ИДАК на
 *  проверках из кэша даёт «вход 0 → выход 0, вызовов 0», и без причины
 *  такая строка читалась бы «расход потеряли», а не «модель не звали». */
function _runRecTokens(e) {
  var res = (e && e.resources) || {};
  var parts = (res.models || []).map(function (m) {
    m = m || {};
    var why = m.note ? " (" + m.note + ")" : "";
    if (m.counted === false) {
      return cosmosRoleWords(m.role) + ": " + fmt(m.model) + " — " +
        t("flow.vmp.record.tokens.none") + why;
    }
    return tf("flow.vmp.record.tokens.words", {
      role: cosmosRoleWords(m.role), model: fmt(m.model),
      input: _runRecNum(m.input_tokens), output: _runRecNum(m.output_tokens),
      calls: _runRecNum(m.calls) }) + why;
  });
  if (res.solver_seconds !== undefined && res.solver_seconds !== null) {
    parts.push(tf("flow.vmp.record.solver_s", { n: res.solver_seconds }));
  }
  return parts.join(" · ") || "—";
}

function _runRecJournal(host, doc, rec) {
  var box = el("div", { class: "run-rec-journal" });
  box.appendChild(el("p", { class: "run-rec-sub",
                            text: t("flow.vmp.record.journal") }));
  var rows = _runRecEntries(doc, rec);
  if (!rows.length) {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.record.journal.none") }));
    host.appendChild(box);
    return;
  }
  var wrap = el("div", { class: "table-wrap" });
  var table = el("table", { class: "data-table run-rec-table" });
  var hr = el("tr");
  ["stage", "substage", "started", "duration", "outcome", "words", "tokens",
   "system"].forEach(function (k) {
    hr.appendChild(el("th", { text: t("flow.vmp.record.j." + k) }));
  });
  table.appendChild(el("thead", {}, [hr]));
  var tbody = el("tbody");
  rows.forEach(function (row) {
    var e = row.e, tr = el("tr");
    tr.appendChild(el("td", { text: fmt(e.stage) }));
    tr.appendChild(el("td", { text: fmt(e.substage) }));
    tr.appendChild(el("td", { text: fmtTime(e.started_at) }));
    tr.appendChild(el("td", { text: _runRecDuration(e) }));
    var cell = el("td");
    var cls = RUN_REC_OUTCOME_CLS[RUN_REC_OUTCOMES[String(e.outcome || "")]];
    cell.appendChild(el("span", { class: "chip small " + (cls || "chip-neutral"),
                                  text: _runRecOutcomeWords(e.outcome) }));
    tr.appendChild(cell);
    var words = [];
    if (e.reason_code) { words.push(String(e.reason_code)); }
    if (e.words) { words.push(String(e.words)); }
    if (e.actor) {
      words.push(t("flow.vmp.record.j.actor") + ": " + String(e.actor));
    }
    tr.appendChild(el("td", { text: words.join(" — ") || "—" }));
    tr.appendChild(el("td", { text: _runRecTokens(e) }));
    tr.appendChild(el("td", { text: _runRecSysWords(row.system) }));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  box.appendChild(wrap);
  host.appendChild(box);
}

/* Итоги: состояние, вердикты задач, выдачи с отпечатками, хронометраж и
 * признак прогона-образца — ТОЛЬКО показ. Признак «золотой» ставится
 * словом владельца после приёмки на Prod (решение № 3 от 10.09.2026), и
 * кнопки, которая ставила бы его отсюда, здесь нет и не будет. */
function _runRecResults(host, rec) {
  var r = (rec && rec.results) || {};
  var box = el("div", { class: "run-rec-results" });
  box.appendChild(el("p", { class: "run-rec-sub",
                            text: t("flow.vmp.record.results") }));
  _runRecRow(box, t("flow.vmp.record.state"), _runRecStateWords(r.state));
  var verdicts = Object.keys(r.verdicts || {}).map(function (k) {
    return _crunsTaskLabel(k) + ": " + fmt(r.verdicts[k]);
  });
  _runRecRow(box, t("flow.vmp.record.verdicts"), verdicts.join(" · ") || "—");
  _runRecRows(box, t("flow.vmp.record.deliveries"),
    (r.deliveries || []).map(function (x) {
      x = x || {};
      return fmt(x.kind) + " · " + _runRecShort(x.sha256);
    }));
  var tm = r.timing || {};
  _runRecRow(box, t("flow.vmp.record.timing"),
    tf("flow.vmp.record.timing.words", { m: _runRecNum(tm.machine_s),
      h: _runRecNum(tm.waiting_human_s), total: _runRecNum(tm.total_s) }));
  var g = r.golden || {};
  _runRecRow(box, t("flow.vmp.record.golden"), g.confirmed
    ? tf("flow.vmp.record.golden.yes", { by: fmt(g.by),
        at: g.at ? fmtTime(g.at) : "—" }) + (g.words ? " — " + String(g.words) : "")
    : t("flow.vmp.record.golden.no"));
  host.appendChild(box);
}

/* Состояние сшивки: сколько записей этапов ушло хозяину, сколько ждёт в
 * очереди площадки и сколько ОТКАЗАНО, слова последнего отказа, слова
 * последней несостоявшейся отправки и повтор очереди. «Ждёт» и
 * «отказано» — разные строки (разбор 11.09.2026): таймаут занятой
 * панели и обрыв сети вердиктом не были, запись у хозяина могла уже
 * лечь целиком, и называть это отказом значило бы обвинять хозяина в
 * том, чего он не говорил. Кнопка повтора живёт там, где ручка
 * действует: `POST /api/run/stitch` работает с ТЕКУЩИМ прогоном,
 * поэтому на карточке чужого прогона её нет — это отказ по смыслу, а
 * не пропуск. */
function _runRecStitch(host, doc, opts) {
  var s = (doc && doc.stitch) || {};
  var box = el("div", { class: "run-rec-stitch" });
  box.appendChild(el("p", { class: "run-rec-sub",
                            text: t("flow.vmp.record.stitch") }));
  var mode = (s.mode === "remote" || s.mode === "local") ? s.mode : "none";
  _runRecRow(box, t("flow.vmp.record.stitch.mode"),
    tf("flow.vmp.record.stitch.mode." + mode, { api: fmt(s.record_api) }));
  _runRecRow(box, t("flow.vmp.record.stitch.counts"),
    tf("flow.vmp.record.stitch.words", { sent: _runRecNum(s.sent),
      waiting: _runRecNum(s.waiting), refused: _runRecNum(s.refused) }));
  if (s.last_error) {
    box.appendChild(el("p", { class: "vmp-note run-rec-bad",
      text: t("flow.vmp.record.stitch.err") + ": " + String(s.last_error) }));
  }
  if (s.last_wait) {
    box.appendChild(el("p", { class: "vmp-note",
      text: t("flow.vmp.record.stitch.wait") + ": " + String(s.last_wait) }));
  }
  if (opts && opts.stitch && mode === "remote") {
    var btn = el("button", { class: "rv-seg run-rec-stitch-btn", type: "button",
                             text: t("flow.vmp.record.stitch.retry") });
    btn.onclick = function () {
      btn.disabled = true;
      api("/run/stitch", { method: "POST" }).then(function (res) {
        res = res || {};
        toast(tf("flow.vmp.record.stitch.done", { sent: _runRecNum(res.sent),
          waiting: _runRecNum(res.waiting), refused: _runRecNum(res.refused) }), "ok");
        if (opts.reload) { opts.reload(); }
      }).catch(function (e) {
        btn.disabled = false;
        toast(_crunsErrWords(e), "error");
      });
    };
    box.appendChild(btn);
  }
  host.appendChild(box);
}

/* Скачивание записи: файл собирается в браузере из уже полученного
 * ответа — второй ручки для того же текста не нужно. */
function _runRecDownload(doc) {
  var rec = (doc && doc.record) || {};
  var head = (doc && doc.head) || {};
  var id = (rec.declaration && rec.declaration.run_id) || head.record_id || "run";
  var blob = new Blob([JSON.stringify(rec, null, 2)],
                      { type: "application/json" });
  var a = el("a", { href: URL.createObjectURL(blob),
                    download: "run-record-" + String(id) + ".json" });
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
}

function _runRecFoot(host, doc) {
  var acts = el("div", { class: "vmp-actions run-rec-foot" });
  var btn = el("button", { class: "rv-seg run-rec-download", type: "button",
                           text: t("flow.vmp.record.download") });
  btn.onclick = function () { _runRecDownload(doc); };
  acts.appendChild(btn);
  host.appendChild(acts);
}

/** Слова отказа ручки: код причины — переводом (`flow.vmp.record.err.<код>`),
 *  слова сервера — как есть, каждое своей строкой. Тело отказа читается и
 *  впрямую, и завёрнутым в `detail` (форма отказов служб COSMOS). */
function _runRecErrLines(host, err) {
  host.textContent = "";
  var b = err && err.body;
  if (b && typeof b === "object" && b.detail !== undefined && b.detail !== null) {
    b = b.detail;
  }
  var code = (b && typeof b === "object") ? b.code : null;
  if (code) {
    host.appendChild(el("p", { class: "vmp-note run-rec-bad",
      text: _runRecKeyWords("flow.vmp.record.err.", code) }));
  }
  var words = (b && typeof b === "object") ? b.words : null;
  var list = (words === null || words === undefined) ? []
    : (Object.prototype.toString.call(words) === "[object Array]" ? words : [words]);
  list.forEach(function (w) {
    host.appendChild(el("p", { class: "vmp-note run-rec-bad", text: String(w) }));
  });
  if (!code && !list.length) {
    host.appendChild(el("p", { class: "vmp-note run-rec-bad",
                               text: _crunsErrWords(err) }));
  }
}

/* Прогон не объявлен: причина словами и форма «Новый прогон от пакета».
 * Модели, решатели и план этапов предзаполнены средой площадки и
 * ПОДТВЕРЖДАЮТСЯ человеком — умолчаний «из дерева» у прогона нет
 * (требование 2 раздела 8 проекта). */
function _runRecDeclare(host, doc, opts) {
  var box = el("div", { class: "run-rec-declare" });
  box.appendChild(el("p", { class: "vmp-note run-rec-reason",
    text: _runRecReasonWords(doc.declare_reason_code || "run-not-declared") }));
  if (!opts || !opts.declare) {
    // Карточка чужого прогона: объявление идёт от ТЕКУЩЕГО пакета панели
    // постановки — здесь называется, где кнопка, а не рисуется вторая.
    box.appendChild(el("p", { class: "vmp-note",
      text: t("flow.vmp.record.declare.only_panel") }));
    host.appendChild(box);
    return;
  }
  var form = doc.form || {};
  box.appendChild(el("p", { class: "run-rec-sub",
                            text: t("flow.vmp.record.declare.h") }));
  var purposeSel = el("select", { class: "run-rec-input run-rec-purpose" });
  (form.purposes || []).forEach(function (p) {
    purposeSel.appendChild(el("option", { value: p,
                                          text: _runRecPurposeWords(p) }));
  });
  box.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: t("flow.vmp.record.purpose") }), purposeSel]));
  var contourSel = el("select", { class: "run-rec-input run-rec-contour" });
  (form.contours || []).forEach(function (c) {
    contourSel.appendChild(el("option", { value: c,
                                          text: _runRecContourWords(c) }));
  });
  box.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: t("flow.vmp.record.contour") }), contourSel]));
  var byInput = el("input", { type: "text", maxlength: "120",
    class: "confirm-input run-rec-input run-rec-by",
    placeholder: t("flow.vmp.record.declare.by.ph") });
  box.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: t("flow.vmp.record.declare.by") }), byInput]));
  _runRecRows(box, t("flow.vmp.record.declare.models"),
              (form.models || []).map(_runRecModelWords));
  _runRecRows(box, t("flow.vmp.record.declare.solvers"),
              (form.solvers || []).map(_runRecSolverWords));
  _runRecRow(box, t("flow.vmp.record.stages"),
             (form.planned_stages || []).join(" → ") || "—");
  var check = el("input", { type: "checkbox", class: "run-rec-confirm-box" });
  var label = el("label", { class: "run-rec-confirm" }, [check,
    el("span", { text: t("flow.vmp.record.declare.confirm") })]);
  box.appendChild(label);
  var errBox = el("div", { class: "run-rec-err" });
  var btn = el("button", { class: "btn btn-primary run-rec-declare-btn",
                           type: "button",
                           text: t("flow.vmp.record.declare.btn") });
  btn.onclick = function () {
    errBox.textContent = "";
    if (!byInput.value.trim()) {
      errBox.appendChild(el("p", { class: "vmp-note run-rec-bad",
        text: t("flow.vmp.record.declare.need_by") }));
      return;
    }
    if (!check.checked) {
      errBox.appendChild(el("p", { class: "vmp-note run-rec-bad",
        text: t("flow.vmp.record.declare.need_confirm") }));
      return;
    }
    btn.disabled = true;
    api("/run/declare", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose: purposeSel.value,
        contour: contourSel.value, created_by: byInput.value.trim(),
        models: form.models || [], solvers: form.solvers || [],
        planned_stages: form.planned_stages || [] })
    }).then(function (res) {
      var d = res && res.record && res.record.declaration;
      toast(tf("flow.vmp.record.declare.ok",
               { run: (d && d.run_id) || "—" }), "ok");
      if (opts.reload) { opts.reload(); }
    }).catch(function (e) {
      btn.disabled = false;
      _runRecErrLines(errBox, e);
    });
  };
  box.appendChild(el("div", { class: "vmp-actions" }, [btn]));
  box.appendChild(errBox);
  host.appendChild(box);
}

/** Секция «Запись о прогоне» целиком. opts: declare — можно объявлять
 *  прогон (панель постановки текущего пакета), stitch — можно повторять
 *  сшивку (текущий прогон), reload — перечитать секцию. */
function runRecordRender(host, doc, opts) {
  opts = opts || {};
  doc = doc || {};
  host.textContent = "";
  (doc.words || []).forEach(function (w) {
    host.appendChild(el("p", { class: "vmp-note", text: String(w) }));
  });
  if (!doc.present) {
    // Пакета нет — запись рождаться не от чего; это не ошибка чтения.
    host.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.record.nopkg") }));
    return;
  }
  vmpProjectionBlock(host, doc.objective_projection);
  if (!doc.declared) {
    _runRecDeclare(host, doc, opts);
    return;
  }
  if (!doc.record) {
    // Объявление есть, а текст записи сюда не пришёл: хозяин недоступен.
    // Честно называем это и показываем состояние сшивки — по нему видно,
    // где запись и почему её здесь нет.
    host.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.record.norecord") }));
    _runRecStitch(host, doc, opts);
    return;
  }
  _runRecHead(host, doc, doc.record);
  _runRecJournal(host, doc, doc.record);
  _runRecResults(host, doc.record);
  _runRecStitch(host, doc, opts);
  _runRecFoot(host, doc);
}

/* Живой блок записи на панели постановки. Держатель один: панель
 * открыта — блок есть, закрыта — нет (закрытие снимает ссылку). */
var _vmpRecord = null;

function vmpRecordBlock(host) {
  var box = el("div", { class: "vmp-sec run-rec-sec" });
  box.appendChild(el("h4", { text: t("flow.vmp.record.h"),
                             title: t("flow.vmp.record.title") }));
  var body = el("div", { class: "run-rec-body" });
  box.appendChild(body);
  host.appendChild(box);
  var ctl = { sig: null, busy: false };
  ctl.reload = function () {
    if (ctl.busy) { return; }
    ctl.busy = true;
    api("/run/current").then(function (doc) {
      runRecordRender(body, doc, { declare: true, stitch: true,
                                   reload: ctl.reload });
    }).catch(function (e) {
      body.textContent = "";
      body.appendChild(el("p", { class: "vmp-note run-rec-bad",
        text: t("flow.vmp.record.fail") + ": " + _crunsErrWords(e) }));
    }).finally(function () { ctl.busy = false; });
  };
  _vmpRecord = ctl;
  ctl.reload();
  return ctl;
}

/** Сигнал блока расчёта: состав или состояние попыток изменились — запись
 *  перечитывается. Опрос блока расчёта идёт раз в 3 с; запись читается не
 *  по каждому тику, а только на изменение подписи — иначе карточка
 *  дёргалась бы у каждого опроса. */
function vmpRecordSignal(st) {
  if (!_vmpRecord) { return; }
  var atts = (st && st.attempts) || [];
  var top = atts.length ? atts[0] : null;
  var sig = [st && st.state, st && st.progress, atts.length,
             top ? [top.id, top.state, top.verdict].join(":") : ""].join("|");
  if (sig === _vmpRecord.sig) { return; }
  _vmpRecord.sig = sig;
  _vmpRecord.reload();
  // Дерево перечитывается той же подписью: попытка закончилась — у узла
  // сменились вердикты, время и токены, и соседние ветви сравниваются
  // уже с новыми числами.
  if (_vmpTree) { _vmpTree.reload(); }
}

/** Отказ нажатия «прогон не объявлен» (гейт от пакета, раздел 10 п. 1):
 *  HTTP 409 с кодом причины и словами. Тело читается и впрямую, и
 *  завёрнутым в `detail`. */
function _runRecRefusal(err) {
  var b = err && err.body;
  if (b && typeof b === "object" && b.detail !== undefined && b.detail !== null) {
    b = b.detail;
  }
  if (!b || typeof b !== "object" || b.code !== "run-not-declared") { return null; }
  var w = b.words;
  if (Object.prototype.toString.call(w) === "[object Array]") { w = w.join(" "); }
  return { code: b.code, words: w ? String(w) : "" };
}

/** Прокрутка к секции записи на панели постановки. Панель закрыта (расчёт
 *  запущен из блока схемы) — панель открывается, и прокрутка повторяется
 *  один раз: молчаливой ссылки «в никуда» быть не должно. */
function _runRecScrollTo(retry) {
  var sec = document.querySelector(".run-rec-sec");
  if (!sec) {
    if (retry) { return; }
    flowOpenVmpPanel();
    setTimeout(function () { _runRecScrollTo(true); }, 350);
    return;
  }
  sec.scrollIntoView({ block: "start" });
  sec.classList.add("run-rec-flash");
  setTimeout(function () { sec.classList.remove("run-rec-flash"); }, 1600);
}

/* ===================== Дерево прогонов (СХУП, шаг 3) ===================
 * База прогонов имеет вид дерева, а не списка (проект
 * «Запись о прогоне», раздел 7): корень — отпечатки исходных документов,
 * данных и ОТВЕТОВ на анкеты; ветвь — тот же корень с другим объявлением
 * (модели, решатели, параметры решателей); ребро «продолжение» — частный
 * прогон от готового ВМП. Секция показывает путь от корня, соседние
 * ветви и сравнение — по умолчанию с золотым прогоном.
 *
 * Слова дерева живут в ОДНОМ месте — `vmp/run_tree.py` (`EDGES` и
 * `DIFFERS`): сервер отдаёт их по-русски, интерфейс переводит известное
 * по коду и показывает незнакомое как есть. Второго перечня рёбер и
 * различий здесь нет и быть не должно.
 *
 * Признак золотого прогона ставится ТОЛЬКО подтверждением владельца
 * после приёмки на Prod (решение владельца 6.3 от 10.09.2026), поэтому
 * кнопка живёт лишь у завершённого прогона площадки `prod`, а слова
 * подтверждения обязательны. */

/* Вид ребра дерева: слова `vmp/run_tree.EDGES` → код перевода. */
var RUN_TREE_EDGES = {
  "корень": "root", "соседняя ветвь": "sibling",
  "изменены ответы": "answers", "продолжение": "continuation"
};

/* Чем ветвь отличается от соседней: слова `vmp/run_tree.DIFFERS`. */
var RUN_TREE_DIFFERS = {
  "модели": "models", "решатели": "solvers",
  "параметры решателей": "solver_params", "каталог": "catalog",
  "привязки": "bindings", "код": "code", "библиотеки": "libraries",
  "цель": "purpose", "площадка": "contour", "план": "plan"
};

/* Вид сравнения: слова `vmp/run_tree.COMPARE_*`. */
var RUN_TREE_KINDS = {
  "пара": "pair", "золотой": "golden", "предыдущий": "previous"
};

/* Крупная часть объявления по первому слову пути поля сравнения
 * (`vmp/run_tree.COMPARED_DECLARATION` и `_PART_WORDS`):
 * «models.certifier.model» → «модели · certifier.model». Слова те же,
 * что у колонки «чем отличается»: разойдись они — таблица сравнения
 * называла бы часть иначе, чем таблица соседей. */
var RUN_TREE_FIELDS = {
  "purpose": "purpose", "contour": "contour", "catalog": "catalog",
  "catalog_extension": "catalog", "bindings": "bindings", "code": "code",
  "libraries": "libraries", "models": "models", "solvers": "solvers",
  "planned_stages": "plan", "stop_after": "plan"
};

function _runTreeEdgeWords(v) {
  return _runRecWords(RUN_TREE_EDGES, "flow.vmp.tree.edge.", v);
}

function _runTreeDifferWords(v) {
  return _runRecWords(RUN_TREE_DIFFERS, "flow.vmp.tree.differs.", v);
}

function _runTreeKindWords(v) {
  return _runRecWords(RUN_TREE_KINDS, "flow.vmp.tree.cmp.kind.", v);
}

/** Слова «чем отличается» через запятую; различий нет — так и сказано. */
function _runTreeDiffersList(list) {
  var arr = (Object.prototype.toString.call(list) === "[object Array]")
    ? list : [];
  return arr.length ? arr.map(_runTreeDifferWords).join(", ")
                    : t("flow.vmp.tree.differs.none");
}

function _runTreeFieldWords(field) {
  var s = (field === null || field === undefined) ? "" : String(field);
  if (!s) { return "—"; }
  var head = s.split(".")[0];
  var code = RUN_TREE_FIELDS[head];
  if (!code) { return s; }
  var rest = s.slice(head.length + 1);
  return t("flow.vmp.tree.differs." + code) + (rest ? " · " + rest : "");
}

/** Узел дерева одной строкой: номер, цель, площадка, состояние. */
function _runTreeNodeWords(n) {
  n = n || {};
  return tf("flow.vmp.tree.node.words", {
    run: fmt(n.run_id), purpose: _runRecPurposeWords(n.purpose),
    contour: _runRecContourWords(n.contour),
    state: _runRecStateWords(n.state) });
}

/** Значение поля сравнения словами: пусто — прочерк, перечень —
 *  перечислением, словарь — плоской строкой. JSON человеку в лицо не
 *  печатается: сравнение показывает, ЧТО различается. */
function _runTreeVal(v) {
  if (v === null || v === undefined || v === "") { return "—"; }
  if (typeof v === "boolean") { return v ? t("app.yes") : t("app.no"); }
  if (Object.prototype.toString.call(v) === "[object Array]") {
    return v.length ? v.map(function (x) { return String(x); }).join(", ") : "—";
  }
  if (typeof v === "object") {
    return Object.keys(v).map(function (k) {
      return k + ": " + String(v[k]);
    }).join(" · ") || "—";
  }
  return String(v);
}

/** Сдвиг числа: пусто — прочерк, рост — со знаком. */
function _runTreeDelta(v) {
  if (v === null || v === undefined) { return "—"; }
  return (typeof v === "number" && v > 0) ? "+" + v : String(v);
}

/** Расход моделей узла СУММОЙ (вход и выход по всем ролям). Хоть одна
 *  строка канала без учёта — сумма помечается «не считается»: половина
 *  расхода, выданная за целое, хуже честного отказа (решение владельца
 *  № 1 от 10.09.2026 — расход в токенах, не в деньгах). */
function _runTreeTokens(n) {
  var tk = (n && n.tokens) || {};
  var keys = Object.keys(tk);
  if (!keys.length) { return "—"; }
  var input = 0, output = 0, counted = true;
  keys.forEach(function (k) {
    var row = tk[k] || {};
    input += Number(row.input_tokens) || 0;
    output += Number(row.output_tokens) || 0;
    if (row.counted === false) { counted = false; }
  });
  var words = tf("flow.vmp.tree.tok.words", { input: input, output: output });
  return counted ? words : words + " · " + t("flow.vmp.tree.tok.partial");
}

/** Время машины узла: секунды словами; нет числа — прочерк. */
function _runTreeMachine(n) {
  var v = ((n && n.timing) || {}).machine_s;
  return (v === null || v === undefined) ? "—"
    : tf("flow.vmp.tree.time.words", { m: v });
}

function _runTreeVerdicts(n) {
  var v = (n && n.verdicts) || {};
  var keys = Object.keys(v);
  if (!keys.length) { return "—"; }
  return keys.map(function (k) {
    return _crunsTaskLabel(k) + ": " + fmt(v[k]);
  }).join(" · ");
}

function _runTreeGoldenMark(n) {
  return ((n && n.golden) || {}).confirmed ? t("app.yes") : "—";
}

/** Числа дерева: ручка отдаёт либо индекс целиком (`roots[]`,
 *  `orphans[]`, `refusals[]`), либо готовые числа. Читается и то и
 *  другое — своего счёта интерфейс не выдумывает. */
function _runTreeCounts(res) {
  var src = (res && res.tree) ? res.tree : (res || {});
  var roots = src.roots;
  if (Object.prototype.toString.call(roots) === "[object Array]") {
    var runs = 0;
    roots.forEach(function (r) { runs += (((r || {}).runs) || []).length; });
    return { roots: roots.length, runs: runs + ((src.orphans || []).length),
             refusals: (src.refusals || []).length };
  }
  var num = function (v) { return (typeof v === "number") ? v : null; };
  return { roots: num(src.roots), runs: num(src.runs),
           refusals: num(src.refusals) };
}

/** Узла нет — сказать, ПОЧЕМУ: записей о прогонах нет вовсе (дерево
 *  пусто) либо запись этого прогона не объявлена. Молчания и общего
 *  «нет данных» здесь быть не должно. */
function _runTreeEmptyKey(doc) {
  if (doc && doc.declared === false) { return "flow.vmp.tree.norecord"; }
  return (_runTreeCounts(doc).runs === 0) ? "flow.vmp.tree.empty"
                                          : "flow.vmp.tree.norecord";
}

function _runTreeSub(host, words) {
  host.appendChild(el("p", { class: "run-tree-sub", text: words }));
}

function _runTreeRow(host, label, value) {
  host.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: label }), el("span", { text: value })]));
}

/** Слова отказа ручки дерева: код причины — переводом
 *  (`flow.vmp.tree.err.<код>`), слова сервера — как есть, каждое своей
 *  строкой. Тело читается и впрямую, и завёрнутым в `detail`. */
function _runTreeErrLines(host, err) {
  host.textContent = "";
  var b = err && err.body;
  if (b && typeof b === "object" && b.detail !== undefined && b.detail !== null) {
    b = b.detail;
  }
  var code = (b && typeof b === "object") ? b.code : null;
  if (code) {
    host.appendChild(el("p", { class: "vmp-note run-tree-bad",
      text: _runRecKeyWords("flow.vmp.tree.err.", code) }));
  }
  var words = (b && typeof b === "object") ? b.words : null;
  var list = (words === null || words === undefined) ? []
    : (Object.prototype.toString.call(words) === "[object Array]" ? words : [words]);
  list.forEach(function (w) {
    host.appendChild(el("p", { class: "vmp-note run-tree-bad", text: String(w) }));
  });
  if (!code && !list.length) {
    host.appendChild(el("p", { class: "vmp-note run-tree-bad",
                               text: _crunsErrWords(err) }));
  }
}

function _runTreeErrLine(host, words) {
  host.textContent = "";
  host.appendChild(el("p", { class: "vmp-note run-tree-bad",
                             text: String(words) }));
}

/* Путь от корня: входы (авиакомпания, короткие отпечатки правил с
 * данными и ответов, сколько документов) → родители → сам прогон.
 * Полные шестидесятичетырёхзначные отпечатки не печатаются: человек
 * читает первые знаки. */
function _runTreePath(host, doc) {
  var p = (doc && doc.path) || {};
  var root = p.root || {};
  var box = el("div", { class: "run-tree-path-box" });
  _runTreeSub(box, t("flow.vmp.tree.path"));
  _runTreeRow(box, t("flow.vmp.tree.root"), tf("flow.vmp.tree.root.words", {
    airline: fmt(root.airline), root: _runRecShort(root.root_sha256),
    answers: _runRecShort(root.root_answers_sha256) }));
  var docs = root.documents || {};
  _runTreeRow(box, t("flow.vmp.tree.root.docs"),
    tf("flow.vmp.tree.root.docs.words", { rules: (docs.rules || []).length,
                                          data: (docs.data || []).length }));
  var runs = p.runs || [];
  if (!runs.length) {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.tree.path.none") }));
    host.appendChild(box);
    return;
  }
  var self = ((doc && doc.node) || {}).run_id;
  var list = el("ol", { class: "run-tree-path" });
  runs.forEach(function (n) {
    n = n || {};
    var edge = n.edge || {};
    var words = _runTreeEdgeWords(edge.kind) + " — " + _runTreeNodeWords(n);
    if (edge.differs && edge.differs.length) {
      words += " (" + _runTreeDiffersList(edge.differs) + ")";
    }
    if (n.run_id && n.run_id === self) {
      words += " — " + t("flow.vmp.tree.self");
    }
    list.appendChild(el("li", { text: words }));
  });
  box.appendChild(list);
  host.appendChild(box);
}

/* Соседние ветви — прогоны того же корня. Колонка «чем отличается» —
 * слова `DIFFERS` через запятую; выбор строки говорит, с кем сравнивать
 * кнопкой «Сравнить с соседом». */
function _runTreeSiblings(host, doc, state) {
  var rows = (doc && doc.siblings) || [];
  var box = el("div", { class: "run-tree-sib-box" });
  _runTreeSub(box, t("flow.vmp.tree.siblings"));
  if (!rows.length) {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.tree.siblings.none") }));
    host.appendChild(box);
    return;
  }
  var wrap = el("div", { class: "table-wrap" });
  var table = el("table", { class: "data-table run-tree-table" });
  var hr = el("tr", {}, [el("th", { text: "" })]);
  ["run", "who", "contour", "differs", "verdicts", "timing", "tokens",
   "golden"].forEach(function (k) {
    hr.appendChild(el("th", { text: t("flow.vmp.tree.s." + k) }));
  });
  table.appendChild(el("thead", {}, [hr]));
  var tbody = el("tbody");
  rows.forEach(function (n) {
    n = n || {};
    var pick = el("input", { type: "radio", name: "run-tree-sib",
                             class: "run-tree-pick",
                             "aria-label": fmt(n.run_id) });
    if (state.sibling === n.run_id) { pick.checked = true; }
    pick.addEventListener("change", function () { state.sibling = n.run_id; });
    var tr = el("tr", {}, [el("td", {}, [pick])]);
    tr.appendChild(el("td", { class: "mono", text: fmt(n.run_id) }));
    tr.appendChild(el("td", { text: fmt(n.created_by) + " · " +
                                    _runRecPurposeWords(n.purpose) }));
    tr.appendChild(el("td", { text: _runRecContourWords(n.contour) }));
    tr.appendChild(el("td", { text: _runTreeDiffersList((n.edge || {}).differs) }));
    tr.appendChild(el("td", { text: _runTreeVerdicts(n) }));
    tr.appendChild(el("td", { text: _runTreeMachine(n) }));
    tr.appendChild(el("td", { text: _runTreeTokens(n) }));
    tr.appendChild(el("td", { text: _runTreeGoldenMark(n) }));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  box.appendChild(wrap);
  host.appendChild(box);
}

/* Что менялось в объявлении: таблица «поле — этот прогон — с кем
 * сравниваем». Сравнение приходит от `vmp/run_tree.compare`, где `a` —
 * образец (золотой либо предыдущий, либо выбранный сосед), `b` — этот
 * прогон. */
function _runTreeCmpDecl(host, cmp) {
  var d = (cmp && cmp.declaration) || {};
  var changed = d.changed || [];
  _runTreeSub(host, t("flow.vmp.tree.cmp.decl"));
  if (!changed.length) {
    host.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.tree.cmp.decl.none") }));
  } else {
    var wrap = el("div", { class: "table-wrap" });
    var table = el("table", { class: "data-table run-tree-table" });
    var hr = el("tr");
    ["field", "this", "other"].forEach(function (k) {
      hr.appendChild(el("th", { text: t("flow.vmp.tree.cmp." + k) }));
    });
    table.appendChild(el("thead", {}, [hr]));
    var tbody = el("tbody");
    changed.forEach(function (row) {
      row = row || {};
      tbody.appendChild(el("tr", {}, [
        el("td", { text: _runTreeFieldWords(row.field) }),
        el("td", { text: _runTreeVal(row.b) }),
        el("td", { text: _runTreeVal(row.a) })]));
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    host.appendChild(wrap);
  }
  host.appendChild(el("p", { class: "vmp-note",
    text: tf("flow.vmp.tree.cmp.same", { n: (d.same || []).length }) }));
}

/* Что изменилось в итогах: вердикты, хронометраж, расход моделей в
 * токенах и счётчики. Счётчики помечены «справочно»: их числа гуляют по
 * чтению модели (раздел 3.3 проекта), и расхождение счётчика — повод
 * посмотреть, а не вердикт. */
function _runTreeCmpResults(host, cmp) {
  var r = (cmp && cmp.results) || {};
  _runTreeSub(host, t("flow.vmp.tree.cmp.results"));
  var wrap = el("div", { class: "table-wrap" });
  var table = el("table", { class: "data-table run-tree-table" });
  var hr = el("tr");
  ["field", "this", "other", "delta"].forEach(function (k) {
    hr.appendChild(el("th", { text: t("flow.vmp.tree.cmp." + k) }));
  });
  table.appendChild(el("thead", {}, [hr]));
  var tbody = el("tbody");
  function line(name, b, a, delta) {
    tbody.appendChild(el("tr", {}, [
      el("td", { text: name }), el("td", { text: b }),
      el("td", { text: a }), el("td", { text: delta })]));
  }
  var verdicts = r.verdicts || {};
  Object.keys(verdicts).forEach(function (task) {
    var v = verdicts[task] || {};
    line(t("flow.vmp.tree.cmp.verdicts") + " · " + _crunsTaskLabel(task),
         fmt(v.b), fmt(v.a),
         v.same ? t("flow.vmp.tree.cmp.equal") : t("flow.vmp.tree.cmp.differs"));
  });
  var timing = r.timing || {};
  Object.keys(timing).forEach(function (f) {
    var v = timing[f] || {};
    line(t("flow.vmp.tree.cmp.timing") + " · " +
         _runRecKeyWords("flow.vmp.tree.t.", f),
         _runRecNum(v.b), _runRecNum(v.a), _runTreeDelta(v.delta));
  });
  var tokens = r.tokens || {};
  Object.keys(tokens).forEach(function (key) {
    var v = tokens[key] || {}, b = v.b || {}, a = v.a || {}, dl = v.delta || {};
    var note = v.only_in ? " (" + t("flow.vmp.tree.cmp.only") + ")"
      : (v.counted === false ? " (" + t("flow.vmp.tree.tok.partial") + ")" : "");
    var row = function (x) {
      return tf("flow.vmp.tree.tok.row", { input: _runRecNum(x.input_tokens),
        output: _runRecNum(x.output_tokens), calls: _runRecNum(x.calls) });
    };
    line(t("flow.vmp.tree.cmp.tokens") + " · " + key + note, row(b), row(a),
         _runTreeDelta(dl.input_tokens) + " / " + _runTreeDelta(dl.output_tokens));
  });
  var counters = r.counters || {};
  var names = Object.keys(counters);
  names.forEach(function (name) {
    var v = counters[name] || {};
    line(t("flow.vmp.tree.cmp.counters") + " · " + name + " (" +
         t("flow.vmp.tree.cmp.ref") + ")",
         _runRecNum(v.b), _runRecNum(v.a), _runTreeDelta(v.delta));
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
  if (names.length) {
    host.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.tree.cmp.counters.note") }));
  }
}

function _runTreeCompare(host, cmp) {
  cmp = cmp || {};
  host.appendChild(el("p", { class: "vmp-note",
    text: tf("flow.vmp.tree.cmp.head", { kind: _runTreeKindWords(cmp.kind),
      a: fmt(cmp.a), b: fmt(cmp.b) }) }));
  if (cmp.same_root === false) {
    host.appendChild(el("p", { class: "vmp-note run-tree-warn",
                               text: t("flow.vmp.tree.cmp.other_root") }));
  }
  _runTreeCmpDecl(host, cmp);
  _runTreeCmpResults(host, cmp);
  (cmp.words || []).forEach(function (w) {
    host.appendChild(el("p", { class: "vmp-note", text: String(w) }));
  });
}

/* Сравнение: по умолчанию с ЗОЛОТЫМ прогоном (раздел 7 проекта — новые
 * ветви сравниваются с золотым), по выбору — с предыдущим прогоном того
 * же корня либо с выбранным соседом (`GET /api/run-tree/compare`). */
function _runTreeCompareBox(host, doc, state) {
  var box = el("div", { class: "run-tree-cmp-box" });
  _runTreeSub(box, t("flow.vmp.tree.cmp"));
  var acts = el("div", { class: "run-tree-actions" });
  [["golden", "flow.vmp.tree.pick.golden"],
   ["previous", "flow.vmp.tree.pick.previous"]].forEach(function (pair) {
    var b = el("button", { type: "button",
      class: "rv-seg" + (state.mode === pair[0] ? " on" : ""),
      text: t(pair[1]) });
    b.onclick = function () {
      state.mode = pair[0];
      state.pair = null;
      state.render();
    };
    acts.appendChild(b);
  });
  var cmpBtn = el("button", { class: "rv-seg run-tree-cmp-btn", type: "button",
                              text: t("flow.vmp.tree.pick.sibling") });
  cmpBtn.onclick = function () {
    if (!state.sibling) {
      toast(t("flow.vmp.tree.pick.need"), "error");
      return;
    }
    cmpBtn.disabled = true;
    // `a` — сосед (образец сравнения), `b` — этот прогон: тот же
    // порядок, что у сравнения с золотым, иначе колонки таблицы
    // менялись бы местами от кнопки к кнопке.
    var query = "?a=" + encodeURIComponent(state.sibling) +
                "&b=" + encodeURIComponent(state.run);
    api("/run-tree/compare" + query).then(function (cmp) {
      state.mode = "pair";
      state.pair = cmp;
      state.render();
    }).catch(function (e) {
      cmpBtn.disabled = false;
      toast(_crunsErrWords(e), "error");
    });
  };
  acts.appendChild(cmpBtn);
  box.appendChild(acts);
  var body = el("div", { class: "run-tree-cmp" });
  box.appendChild(body);
  var cmp = (state.mode === "pair") ? state.pair
    : (state.mode === "previous" ? doc.compare_previous : doc.compare_golden);
  if (cmp) {
    _runTreeCompare(body, cmp);
  } else {
    // Сравнивать не с чем — сказать, с чем именно: золотого на корне нет
    // либо эта ветвь на корне первая.
    body.appendChild(el("p", { class: "vmp-note",
      text: t("flow.vmp.tree.cmp.none." +
              (state.mode === "previous" ? "previous" : "golden")) }));
  }
  host.appendChild(box);
}

/* Сравнение с ожиданиями набора (раздел 12.4 проекта): состав полей
 * объявляет раннер при закрытии записи (`results.comparison.expectations`),
 * поэтому поля показываются как есть — своего второго словаря ожиданий
 * интерфейс не заводит. */
function _runTreeExpect(host, doc) {
  var e = doc && doc.expectations;
  var box = el("div", { class: "run-tree-expect" });
  _runTreeSub(box, t("flow.vmp.tree.expect"));
  var keys = (e && typeof e === "object") ? Object.keys(e) : [];
  if (!keys.length) {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.tree.expect.none") }));
    host.appendChild(box);
    return;
  }
  keys.forEach(function (k) { _runTreeRow(box, String(k), _runTreeVal(e[k])); });
  host.appendChild(box);
}

/** Ручка золотого прогона: `POST /api/cosmos-runs/<прогон>/golden` и
 *  `…/golden/unmark`. Личность подтвердившего ставит СЕРВЕР по сессии:
 *  интерфейс её не выдумывает и полем `by` не шлёт. */
function _runTreeGoldenCall(run, tail, body, errBox, opts, okWords, btn) {
  var options = { method: "POST" };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  api("/cosmos-runs/" + encodeURIComponent(run) + tail, options)
    .then(function (res) {
      if (res && res.ok === false) {
        if (btn) { btn.disabled = false; }
        _runTreeErrLines(errBox, { body: res });
        return;
      }
      toast(okWords, "ok");
      if (opts && opts.reload) { opts.reload(); }
    }).catch(function (e) {
      if (btn) { btn.disabled = false; }
      _runTreeErrLines(errBox, e);
    });
}

/* Короткая форма подтверждения: слова обязательны — подпись без слов не
 * подпись (`vmp/run_tree.mark_golden` отказывает пустым словам). */
function _runTreeGoldenForm(host, node, errBox, opts) {
  var form = el("div", { class: "run-tree-golden-form" });
  _runTreeSub(form, t("flow.vmp.tree.golden.h"));
  var inp = el("input", { type: "text", class: "run-tree-input",
                          maxlength: "400",
                          placeholder: t("flow.vmp.tree.golden.words.ph") });
  form.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: t("flow.vmp.tree.golden.words") }), inp]));
  var send = el("button", { class: "rv-seg run-tree-golden-send",
                            type: "button",
                            text: t("flow.vmp.tree.golden.send") });
  send.onclick = function () {
    var words = String(inp.value || "").trim();
    if (!words) {
      _runTreeErrLine(errBox, t("flow.vmp.tree.golden.need_words"));
      return;
    }
    send.disabled = true;
    _runTreeGoldenCall(node.run_id, "/golden", { words: words }, errBox, opts,
      tf("flow.vmp.tree.golden.ok", { run: fmt(node.run_id) }), send);
  };
  form.appendChild(el("div", { class: "run-tree-actions" }, [send]));
  host.appendChild(form);
}

/* Признак золотого прогона. Решение владельца 6.3 (10.09.2026): после
 * приёмки на Prod система ЗАПРАШИВАЕТ владельца, и признак ставится
 * только по его подтверждению. Поэтому кнопка живёт лишь у ЗАВЕРШЁННОГО
 * прогона площадки `prod`; у прочих сказано словами, почему её нет. */
function _runTreeGolden(host, doc, opts) {
  var node = (doc && doc.node) || {};
  var g = node.golden || {};
  var box = el("div", { class: "run-tree-golden" });
  _runTreeSub(box, t("flow.vmp.tree.golden"));
  _runTreeRow(box, t("flow.vmp.tree.golden.state"), g.confirmed
    ? tf("flow.vmp.tree.golden.yes", { by: fmt(g.by),
        at: g.at ? fmtTime(g.at) : "—" }) +
      (g.words ? " — " + String(g.words) : "")
    : t("flow.vmp.tree.golden.no"));
  var errBox = el("div", { class: "run-tree-err" });
  var acts = el("div", { class: "run-tree-actions" });
  // Состояние судится словарём записи (`RUN_REC_STATES`), а не сырым
  // словом: второго перечня состояний в интерфейсе быть не должно.
  var done = RUN_REC_STATES[String(node.state || "")] === "done";
  if (g.confirmed) {
    var off = el("button", { class: "rv-seg run-tree-golden-off",
                             type: "button",
                             text: t("flow.vmp.tree.golden.unmark") });
    off.onclick = function () {
      off.disabled = true;
      _runTreeGoldenCall(node.run_id, "/golden/unmark", null, errBox, opts,
        tf("flow.vmp.tree.golden.unmarked", { run: fmt(node.run_id) }), off);
    };
    acts.appendChild(off);
  } else if (node.contour === "prod" && done) {
    var btn = el("button", { class: "rv-seg run-tree-golden-btn",
                             type: "button",
                             text: t("flow.vmp.tree.golden.btn") });
    btn.onclick = function () {
      btn.disabled = true;
      _runTreeGoldenForm(box, node, errBox, opts);
    };
    acts.appendChild(btn);
  } else {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.tree.golden.only_prod") }));
  }
  box.appendChild(acts);
  box.appendChild(errBox);
  host.appendChild(box);
}

/* Перестройка индекса дерева (`POST /api/run-tree/rebuild`) — только
 * показ: записей ручка не меняет, она заново обходит папки прогонов и
 * кладёт `run-tree.json`. */
function _runTreeFoot(host, opts) {
  var acts = el("div", { class: "run-tree-actions run-tree-foot" });
  var btn = el("button", { class: "rv-seg run-tree-rebuild", type: "button",
                           text: t("flow.vmp.tree.rebuild") });
  btn.onclick = function () {
    btn.disabled = true;
    api("/run-tree/rebuild", { method: "POST" }).then(function (res) {
      var c = _runTreeCounts(res);
      toast(tf("flow.vmp.tree.rebuild.done", { roots: _runRecNum(c.roots),
        runs: _runRecNum(c.runs), refusals: _runRecNum(c.refusals) }), "ok");
      if (opts && opts.reload) { opts.reload(); }
    }).catch(function (e) {
      btn.disabled = false;
      toast(_crunsErrWords(e), "error");
    });
  };
  acts.appendChild(btn);
  host.appendChild(acts);
}

function _runTreeDraw(host, doc, state, opts) {
  host.textContent = "";
  (doc.words || []).forEach(function (w) {
    host.appendChild(el("p", { class: "vmp-note", text: String(w) }));
  });
  if (!doc.node) {
    host.appendChild(el("p", { class: "vmp-note",
                               text: t(_runTreeEmptyKey(doc)) }));
    _runTreeFoot(host, opts);
    return;
  }
  _runTreePath(host, doc);
  _runTreeSiblings(host, doc, state);
  _runTreeCompareBox(host, doc, state);
  _runTreeExpect(host, doc);
  _runTreeGolden(host, doc, opts);
  _runTreeFoot(host, opts);
}

/** Секция «Дерево прогонов» целиком. `doc` — ответ ручки дерева
 *  (`{node, path, siblings, golden, compare_golden, compare_previous,
 *  expectations}`); opts.reload — перечитать секцию. Выбор сравнения и
 *  выбранный сосед живут в состоянии секции: перерисовка идёт своими
 *  силами, второй ручки для переключателя не нужно. */
function runTreeRender(host, doc, opts) {
  opts = opts || {};
  doc = doc || {};
  var state = { mode: "golden", sibling: null, pair: null,
                run: (doc.node || {}).run_id };
  state.render = function () { _runTreeDraw(host, doc, state, opts); };
  state.render();
  return state;
}

/* --------------------------------------------------- отбор прогонов
 * Отбор по полям головы записи (слово владельца 11.09.2026): набор и
 * заказчик, площадка, состояние записи, кто объявил, образец, период по
 * дате объявления, корень дерева и текстовый поиск по номеру записи и
 * номеру прогона. Отбирает СЕРВЕР (`GET /api/run-tree`, `GET
 * /api/runs-all`): здесь строится только строка запроса — второй разбор
 * тех же слов в браузере разошёлся бы с первым на первой же правке.
 * Выбранное запоминается в этом браузере (localStorage) и служит обоим
 * местам отбора — дереву прогонов и общему экрану обеих систем. */

var RUN_FILTER_KEY = "cosmos.runs.filters.v1";
/* Имена параметров — те же, что у ручек (порядок — порядок полей на
 * экране). */
var RUN_FILTER_FIELDS = ["customer", "contour", "state", "declared_by",
                         "golden", "since", "until", "root", "q"];
/* Площадки и состояния — слова словаря записи (`vmp/run_record.py`): их
 * же принимает сервер, а человеку они показываются переводом по коду. */
var RUN_FILTER_CONTOURS = ["local", "test", "stage", "prod"];
var RUN_FILTER_STATES = ["объявлен", "идёт", "завершён",
                         "остановлен по правилу", "сбой",
                         "остановлен человеком"];
/* Кто объявил: три вида, которые называет сервер. */
var RUN_FILTER_DECLARED = ["runner", "panel", "human"];

/** Пустой отбор: все поля пусты. */
function runFiltersEmpty() {
  var out = {};
  RUN_FILTER_FIELDS.forEach(function (f) { out[f] = ""; });
  return out;
}

/** Отбор из хранилища браузера; хранилище недоступно или запись кривая —
 *  пустой отбор (экран от этого не рушится). */
function runFiltersLoad() {
  var out = runFiltersEmpty();
  try {
    var got = JSON.parse(localStorage.getItem(RUN_FILTER_KEY) || "{}");
    if (got && typeof got === "object") {
      RUN_FILTER_FIELDS.forEach(function (f) {
        if (typeof got[f] === "string") { out[f] = got[f]; }
      });
    }
  } catch (e) { /* хранилище недоступно — отбор не запоминается */ }
  return out;
}

function runFiltersSave(f) {
  try {
    localStorage.setItem(RUN_FILTER_KEY, JSON.stringify(f || {}));
  } catch (e) { /* хранилище недоступно — отбор живёт до перезагрузки */ }
}

/** Сколько полей отбора заполнено: по числу человек видит, что список
 *  неполон не «сам по себе», а по его же выбору. */
function runFiltersCount(f) {
  var n = 0;
  RUN_FILTER_FIELDS.forEach(function (k) {
    if (String((f || {})[k] || "").trim()) { n += 1; }
  });
  return n;
}

/** Строка запроса отбора: пустое поле не шлётся вовсе — «все» и
 *  «параметра нет» для сервера одно и то же. */
function runFiltersQuery(f, extra) {
  var parts = [];
  RUN_FILTER_FIELDS.forEach(function (k) {
    var v = String((f || {})[k] || "").trim();
    if (v) {
      parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    }
  });
  Object.keys(extra || {}).forEach(function (k) {
    parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(extra[k]));
  });
  return parts.length ? "?" + parts.join("&") : "";
}

/** Слово о том, кто объявил прогон: код сервера — переводом, пусто —
 *  «не назван» (выдумывать за запись, кто её объявил, нельзя). */
function _runFilterByWords(kind) {
  if (!kind) { return t("flow.runs.by.none"); }
  return RUN_FILTER_DECLARED.indexOf(kind) >= 0
    ? t("flow.runs.by." + kind) : String(kind);
}

function _runFilterField(label, node) {
  return el("label", { class: "run-filter-field" },
            [el("span", { text: label }), node]);
}

function _runFilterSelect(f, name, values, prefix, onChange) {
  var sel = el("select", { class: "run-filter-input" });
  sel.appendChild(el("option", { value: "", text: t("flow.runs.f.any") }));
  values.forEach(function (v) {
    var words = prefix ? t(prefix + v) : String(v);
    sel.appendChild(el("option", { value: String(v),
                                   text: words === prefix + v ? String(v)
                                                              : words }));
  });
  sel.value = String(f[name] || "");
  sel.addEventListener("change", function () {
    f[name] = sel.value;
    onChange();
  });
  return sel;
}

function _runFilterText(f, name, type, phKey, onChange) {
  var inp = el("input", { type: type, class: "run-filter-input" });
  if (phKey) { inp.setAttribute("placeholder", t(phKey)); }
  inp.value = String(f[name] || "");
  var fire = function () {
    if (String(f[name] || "") === inp.value) { return; }
    f[name] = inp.value;
    onChange();
  };
  inp.addEventListener("change", fire);
  inp.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); fire(); }
  });
  return inp;
}

/** Полоса отбора: одна на оба места (дерево прогонов и общий экран).
 *  `f` — словарь отбора (он же пишется в хранилище браузера),
 *  `onChange` — перечитать список у сервера. */
function runFiltersBar(host, f, onChange) {
  var box = el("div", { class: "run-filters",
                        title: t("flow.runs.filters.title") });
  var apply = function () {
    runFiltersSave(f);
    onChange();
  };
  box.appendChild(el("span", { class: "run-filters-h",
                               text: t("flow.runs.filters") }));
  box.appendChild(_runFilterField(t("flow.runs.f.customer"),
    _runFilterText(f, "customer", "search", "flow.runs.f.customer.ph", apply)));
  box.appendChild(_runFilterField(t("flow.vmp.record.contour"),
    _runFilterSelect(f, "contour", RUN_FILTER_CONTOURS,
                     "flow.vmp.record.contour.", apply)));
  var states = el("select", { class: "run-filter-input" });
  states.appendChild(el("option", { value: "", text: t("flow.runs.f.any") }));
  RUN_FILTER_STATES.forEach(function (v) {
    states.appendChild(el("option", { value: v, text: _runRecStateWords(v) }));
  });
  states.value = String(f.state || "");
  states.addEventListener("change", function () {
    f.state = states.value;
    apply();
  });
  box.appendChild(_runFilterField(t("flow.vmp.record.state"), states));
  box.appendChild(_runFilterField(t("flow.vmp.record.by"),
    _runFilterSelect(f, "declared_by", RUN_FILTER_DECLARED, "flow.runs.by.",
                     apply)));
  var gold = el("select", { class: "run-filter-input" });
  gold.appendChild(el("option", { value: "", text: t("flow.runs.f.any") }));
  gold.appendChild(el("option", { value: "1", text: t("flow.runs.golden.yes") }));
  gold.appendChild(el("option", { value: "0", text: t("flow.runs.golden.no") }));
  gold.value = String(f.golden || "");
  gold.addEventListener("change", function () {
    f.golden = gold.value;
    apply();
  });
  box.appendChild(_runFilterField(t("flow.vmp.tree.s.golden"), gold));
  box.appendChild(_runFilterField(t("flow.runs.f.since"),
    _runFilterText(f, "since", "date", null, apply)));
  box.appendChild(_runFilterField(t("flow.runs.f.until"),
    _runFilterText(f, "until", "date", null, apply)));
  box.appendChild(_runFilterField(t("flow.runs.f.root"),
    _runFilterText(f, "root", "search", "flow.runs.f.root.ph", apply)));
  box.appendChild(_runFilterField(t("flow.runs.f.q"),
    _runFilterText(f, "q", "search", "flow.runs.f.q.ph", apply)));
  var reset = el("button", { class: "rv-seg run-filter-reset", type: "button",
                             text: t("flow.runs.f.reset") });
  reset.addEventListener("click", function () {
    RUN_FILTER_FIELDS.forEach(function (k) { f[k] = ""; });
    apply();
  });
  box.appendChild(reset);
  var n = runFiltersCount(f);
  if (n) {
    box.appendChild(el("span", { class: "run-filters-on",
      text: tf("flow.runs.f.on", { n: String(n) }) }));
  }
  host.appendChild(box);
  return box;
}

/* ------------------------------------------- прогоны дерева с отбором
 * Секция дерева показывает ОДИН прогон среди своих; этот блок — все
 * ветви всех корней с отбором. Отбирает сервер (`GET /api/run-tree` с
 * теми же параметрами), интерфейс лишь передаёт их. */

function _runTreeIndexRow(node) {
  var tr = el("tr", {});
  tr.appendChild(el("td", { class: "mono", text: node.run_id || "—" }));
  tr.appendChild(el("td", { text: node.airline || "—" }));
  tr.appendChild(el("td", {
    text: _runRecKeyWords("flow.vmp.record.contour.", node.contour) }));
  tr.appendChild(el("td", { text: _runFilterByWords(_runTreeDeclaredBy(node)),
                            title: node.created_by || "" }));
  tr.appendChild(el("td", { text: fmtTime(node.created_at) }));
  tr.appendChild(el("td", { text: _runRecStateWords(node.state) }));
  tr.appendChild(el("td", { text: _runTreeGoldenMark(node) }));
  return tr;
}

/** Кто объявил прогон по узлу дерева — теми же приметами, что у сервера
 *  (`webapp/run_tree_api.declared_by`): в индексе лежит слово
 *  `created_by`, а вид по нему нужен и показу. */
function _runTreeDeclaredBy(node) {
  var who = String((node || {}).created_by || "").toLowerCase();
  if (!who.trim()) { return null; }
  if (who.indexOf("раннер") >= 0 || who.indexOf("runner") >= 0) {
    return "runner";
  }
  if (who.indexOf("панель") >= 0 || who.indexOf("panel") >= 0 ||
      who.indexOf("cdpm") >= 0) { return "panel"; }
  return "human";
}

function _runTreeIndexTable(host, nodes) {
  var table = el("table", { class: "data-table run-tree-table" });
  var head = el("tr", {});
  [t("flow.vmp.record.run_id"), t("flow.runs.f.customer"),
   t("flow.vmp.record.contour"), t("flow.vmp.record.by"),
   t("flow.runs.f.created"), t("flow.vmp.record.state"),
   t("flow.vmp.tree.s.golden")].forEach(function (w) {
    head.appendChild(el("th", { text: w }));
  });
  table.appendChild(el("thead", {}, [head]));
  var body = el("tbody", {});
  nodes.forEach(function (n) { body.appendChild(_runTreeIndexRow(n)); });
  table.appendChild(body);
  host.appendChild(el("div", { class: "table-wrap" }, [table]));
}

function _runTreeIndexDraw(body, doc, f, reload) {
  body.textContent = "";
  runFiltersBar(body, f, reload);
  var roots = (doc && doc.roots) || [];
  var orphans = (doc && doc.orphans) || [];
  var runs = orphans.length;
  roots.forEach(function (r) { runs += ((r.runs || []).length); });
  body.appendChild(el("p", { class: "run-tree-sub",
    text: tf("flow.vmp.tree.index.counts", { roots: String(roots.length),
                                             runs: String(runs) }) }));
  if (!runs) {
    body.appendChild(el("p", { class: "vmp-note",
      text: t(runFiltersCount(f) ? "flow.vmp.tree.index.none"
                                 : "flow.vmp.tree.empty") }));
    return;
  }
  roots.forEach(function (r) {
    body.appendChild(el("p", { class: "run-tree-sub",
      text: tf("flow.vmp.tree.index.root", {
        root: String(r.root_sha256 || "").slice(0, 12) || "—",
        airline: r.airline || "—" }) }));
    _runTreeIndexTable(body, r.runs || []);
  });
  if (orphans.length) {
    body.appendChild(el("p", { class: "run-tree-sub",
                               text: t("flow.vmp.tree.index.orphans") }));
    _runTreeIndexTable(body, orphans);
  }
}

/** Блок «Прогоны дерева»: полоса отбора и таблицы ветвей по корням.
 *  Возврат — `{reload}`: его же зовёт кнопка «Перестроить дерево». */
function runTreeIndexBlock(host) {
  var box = el("div", { class: "run-tree-index-box" });
  box.appendChild(el("h5", { text: t("flow.vmp.tree.index"),
                             title: t("flow.vmp.tree.index.title") }));
  var body = el("div", { class: "run-tree-index" });
  box.appendChild(body);
  host.appendChild(box);
  var f = runFiltersLoad();
  var ctl = { busy: false, filters: f };
  ctl.reload = function () {
    if (ctl.busy) { return; }
    ctl.busy = true;
    body.textContent = "";
    body.appendChild(el("p", { class: "vmp-note",
                               text: t("flow.vmp.tree.loading") }));
    api("/run-tree" + runFiltersQuery(f)).then(function (doc) {
      _runTreeIndexDraw(body, doc, f, ctl.reload);
    }).catch(function (e) {
      body.textContent = "";
      runFiltersBar(body, f, ctl.reload);
      body.appendChild(el("p", { class: "vmp-note run-tree-bad",
        text: t("flow.vmp.tree.index.fail") + ": " + _crunsErrWords(e) }));
    }).finally(function () { ctl.busy = false; });
  };
  ctl.reload();
  return ctl;
}

/* Живой блок дерева на панели постановки — под записью о прогоне.
 * Держатель один: панель открыта — блок есть, закрыта — нет. */
var _vmpTree = null;

function vmpTreeBlock(host) {
  var box = el("div", { class: "vmp-sec run-tree-sec" });
  box.appendChild(el("h4", { text: t("flow.vmp.tree.h"),
                             title: t("flow.vmp.tree.title") }));
  var body = el("div", { class: "run-tree-body" });
  box.appendChild(body);
  host.appendChild(box);
  // Блок «Прогоны дерева» с отбором — под путём и соседями: сперва этот
  // прогон среди своих, потом все ветви всех корней.
  var index = runTreeIndexBlock(box);
  var ctl = { busy: false, index: index };
  // Перестройка дерева и признак образца меняют и путь, и список ветвей:
  // по действию человека перечитываются ОБА блока, при открытии — каждый
  // своим запросом (лишнего похода к серверу не бывает).
  var both = function () { ctl.reload(); index.reload(); };
  ctl.reload = function () {
    if (ctl.busy) { return; }
    ctl.busy = true;
    api("/run/current/tree").then(function (doc) {
      runTreeRender(body, doc, { reload: both });
    }).catch(function (e) {
      body.textContent = "";
      body.appendChild(el("p", { class: "vmp-note run-tree-bad",
        text: t("flow.vmp.tree.fail") + ": " + _crunsErrWords(e) }));
    }).finally(function () { ctl.busy = false; });
  };
  _vmpTree = ctl;
  ctl.reload();
  return ctl;
}

/* Блок карты сети постановки на карточке ВМП: общий рисовальщик
 * netDrawMap + переключатель подложки + прокручиваемая таблица
 * персонала (кнопкой; полный экран экипажа — своя вкладка, здесь
 * только контекст постановки — решение владельца 2026-08-30). */
/* Блок «Расчёт по пакету» на карточке ВМП (решение владельца
 * 04.09.2026: работа CDPM заканчивается выпуском и экспортом ВМП; всё,
 * что связано с решением задач, — в COSMOS). Загрузка пакета человеком,
 * выбор движка отбора пейринга (только здесь, чистый проброс), кнопки
 * «Пейринг» и «Ростеринг», состояние с опросом, ссылки выдачи каждой
 * выполненной задачи, карта ростера общим движком карты. */
function vmpRunBlock(host, hasPkg, onPkgChange, opts) {
  // Слово владельца 05.09.2026 (репетиция): расчёт запускается из
  // блоков схемы — opts.task = "pairing" (блок «Отбор») либо
  // "rostering" (блок «Ростеринг»); в панели постановки остаётся
  // только приём пакета (opts.task = "package"). Без opts — прежний
  // общий блок (легаси, тесты).
  opts = opts || {};
  var task = opts.task || "all";
  var showP = task === "all" || task === "pairing";
  var showR = task === "all" || task === "rostering";
  var showUpload = task === "all" || task === "package";
  var box = el("div", { class: "vmp-sec" });
  box.appendChild(el("h4", { text: t(
    task === "pairing" ? "flow.task.pairing.h"
    : task === "rostering" ? "flow.task.rostering.h"
    : "flow.vmp.run.h") }));
  // --- загрузка пакета (zip из CDPM)
  var up = el("input", { type: "file", accept: ".zip",
                         class: "hidden" });
  var upBtn = el("button", { class: "rv-seg", type: "button",
                             text: t("flow.vmp.run.upload") });
  var upNote = el("span", { class: "vmp-note", text: t("flow.drop.hint") });
  upBtn.onclick = function () { up.click(); };
  up.onchange = function () {
    if (!up.files || !up.files[0]) { return; }
    // Единый вход с перетаскиванием на блок «Постановка (ВМП)»: та же
    // проверка до запроса и та же функция загрузки (flowUploadPackage).
    var problem = _flowDropProblem("vmp", up.files);
    if (problem) { upNote.textContent = problem; up.value = ""; return; }
    upBtn.disabled = true;
    flowUploadPackage(up.files[0])
      .then(function (brief) {
        upNote.textContent = t("flow.vmp.run.uploaded") + " · " +
          String(brief && brief.verdict || "");
        if (onPkgChange) { onPkgChange(); }
      })
      .catch(function (e) {
        upNote.textContent = _flowUploadErrorText(e);
      })
      .then(function () { upBtn.disabled = false; up.value = ""; });
  };
  if (showUpload) {
    // Строка загрузки — тот же приёмник, что блок «Постановка (ВМП)» на
    // схеме: панель закрывает холст оверлеем, поэтому zip принимается и
    // здесь (та же проверка, та же функция, та же ручка).
    var upRow = el("div", { class: "vmp-row vmp-droprow" },
                   [upBtn, upNote, up]);
    _flowWireDrop("vmp", upRow, upNote, onPkgChange);
    box.appendChild(upRow);
  }
  host.appendChild(box);
  if (!hasPkg) {
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.run.needpkg") }));
    return;
  }
  if (task === "package") {
    // В панели постановки расчёт не запускается: подсказка, где
    // кнопки (блоки схемы «Отбор (оптимизация)» и «Ростеринг»).
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.task.where") }));
    return;
  }
  // --- движок отбора и кнопки
  var engSel = el("select", {});
  ["cpsat", "cplex", "highs"].forEach(function (e) {
    engSel.appendChild(el("option", { value: e,
      text: t("flow.vmp.run.engine." + e) }));
  });
  // Решатель ростеринга (вопрос владельца 05.09.2026 «почему нет
  // выбора»): по пакету ростеринг считает Timefold; CPLEX для
  // ростеринга — только экспорт модели (MPS/LP) после пейринга, вариант
  // показан честно отключённым.
  var rsSel = el("select", {});
  rsSel.appendChild(el("option", { value: "timefold",
    text: t("flow.vmp.run.rsolver.timefold") }));
  var rsCplex = el("option", { value: "cplex",
    text: t("flow.vmp.run.rsolver.cplex") });
  rsCplex.disabled = true;
  rsSel.appendChild(rsCplex);
  var limit = el("input", { type: "number", value: "240", min: "1",
                            step: "1", style: "width:5em" });
  // Кнопки запуска в панели — заметные, как «▶ Весь конвейер» в шапке
  // (замечание владельца на демо 06.09.2026: «внутри блока нет кнопки
  // «▶ Пейринг», как сказано в инструкции» — серая «Пейринг» терялась).
  var btnP = el("button", { class: "btn btn-primary", type: "button",
                            text: "▶ " + t("flow.vmp.run.pairing") });
  var btnR = el("button", { class: "btn btn-primary", type: "button",
                            text: "▶ " + t("flow.vmp.run.rostering") });
  // Шаг 3 плана 05.09.2026: ростеринг НА утверждённом пейринге — явное
  // третье нажатие с pairingRunId из выдачи пейринга этого пакета;
  // молчаливой подстановки нет, кнопка живёт только при утверждённом
  // пейринге и показывает, какой прогон назовёт.
  var btnRP = el("button", { class: "btn btn-primary", type: "button",
                             text: "▶ " + t("flow.vmp.run.onpairing") });
  btnRP.disabled = true;
  var pairLine = el("span", { class: "vmp-note" });
  // W-11 (единая версия 06.09.2026, GPT-011): ВИДИМЫЙ выбор попытки
  // пейринга для ростеринга «на пейринге» — любая сохранённая попытка
  // пакета (st.pairing_candidates), умолчание — последний утверждённый
  // пейринг (st.pairing_run; умолчание на последний совместимый
  // допустимо). Порядок списка выбором не является: кнопка шлёт ровно
  // выбранное значение; при смене пакета выбор сбрасывается. Холодная
  // кнопка «Ростеринг» (btnR) выбором не пользуется и не запрещается.
  var pairingSel = el("select", { "aria-label": t("flow.vmp.run.selectedPairing") });
  var selectedPairing = "", selectedPackage = null;
  pairingSel.onchange = function () {
    selectedPairing = pairingSel.value || "";
    btnRP.dataset.run = selectedPairing;
    btnRP.disabled = pairingSel.disabled || !selectedPairing;
  };
  // W-12: подсказка после неудачи на выбранном каталоге (st.selection_note)
  // и следующие шаги (st.next_actions): «пересмотреть выбор» — к списку,
  // «расширить каталог» — новое нажатие «Пейринг».
  var selectionLine = el("p", { class: "vmp-note" });
  var reselectBtn = el("button", { class: "rv-seg", type: "button",
                                  text: t("flow.vmp.run.reselectPairing") });
  reselectBtn.hidden = true;
  reselectBtn.onclick = function () { pairingSel.focus(); };
  var rebuildBtn = el("button", { class: "rv-seg", type: "button",
                                 text: t("flow.vmp.run.rebuildPairing") });
  rebuildBtn.hidden = true;
  rebuildBtn.onclick = function () { press("pairing"); };
  if (showP) {
    box.appendChild(el("div", { class: "vmp-row" }, [
      el("span", { text: t("flow.vmp.run.engine") }), engSel]));
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.run.engine.note") }));
  }
  if (showR) {
    box.appendChild(el("div", { class: "vmp-row" }, [
      el("span", { text: t("flow.vmp.run.rsolver") }), rsSel]));
    box.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.vmp.run.rsolver.note") }));
  }
  box.appendChild(el("div", { class: "vmp-row" }, [
    el("span", { text: t("flow.vmp.run.limit") }), limit]));
  var btnRow = [];
  if (showP) { btnRow.push(btnP); }
  if (showR) { btnRow.push(btnR); }
  box.appendChild(el("div", { class: "vmp-row" }, btnRow));
  if (showR) {
    box.appendChild(el("div", { class: "vmp-row" }, [btnRP, pairingSel, pairLine]));
  }
  var stateLine = el("p", { class: "vmp-note" });
  // Режим последней задачи словами (Ш-1к кнопки): без критерия в
  // пакете человек читает здесь, что искалось допустимое решение с
  // целевой из штрафов, а не оптимизация.
  var modeLine = el("p", { class: "vmp-note" });
  // A7 (очередь 3 аудита 04.09.2026): допущение о непереданной истории
  // сотрудника — словами и с перечнем правил, рядом с режимом.
  var assumeLine = el("p", { class: "vmp-note" });
  // A8 (аудит 04.09.2026): три статуса результата словами —
  // допустимость / стоимость / оптимизация, полнота покрытия и
  // перечень непроверенных правил. Одно слово «допустимо» в состоянии
  // было сильнее доказательства: сертификат с непроверенными
  // жёсткими правилами показывался как допустимый.
  var resultLine = el("p", { class: "vmp-note" });
  function resultText(st) {
    var r = st && st.result;
    if (!r || !r.feasibility) { return ""; }
    var parts = [
      t("flow.vmp.run.res.feasibility") + ": " +
        t("flow.vmp.run.res.feasibility." + r.feasibility),
      t("flow.vmp.run.res.cost") + ": " +
        t("flow.vmp.run.res.cost." + r.cost),
      t("flow.vmp.run.res.optimality") + ": " +
        t("flow.vmp.run.res.optimality." + r.optimality)];
    if (r.coverage_complete === true) {
      parts.push(t("flow.vmp.run.res.coverage.full"));
    } else if (r.coverage_complete === false) {
      parts.push(t("flow.vmp.run.res.coverage.partial") + " (" +
        (r.uncovered || []).length + ")");
    }
    if (r.search_incomplete) { parts.push(t("flow.vmp.run.res.incomplete")); }
    var line = t("flow.vmp.run.result") + ": " + parts.join("; ");
    var unc = (st.uncovered || r.uncovered || []);
    if (unc.length) {
      line += " — " + t("flow.vmp.run.res.uncovered") + ": " + unc.join(", ");
    }
    var gate = st.certificate_gate || [];
    if (gate.length) {
      line += " — " + t("flow.vmp.run.res.gate") + ": " + gate.length;
    }
    return line;
  }
  // Заметное предупреждение ДО нажатия (решение владельца 04.09):
  // у задачи нет критерия оптимизации — кнопки поведут её в режиме
  // допустимости с целевой из взвешенных штрафов. Признак — из
  // матрицы готовности пакета, той же, что судит запуск.
  var warnLine = el("div", { class: "banner-warn" });
  warnLine.hidden = true;
  var links = el("div", {});
  var mapWrap = el("div", {});
  box.appendChild(warnLine);
  box.appendChild(stateLine);
  box.appendChild(resultLine);
  box.appendChild(selectionLine);
  box.appendChild(el("div", { class: "vmp-row" }, [reselectBtn, rebuildBtn]));
  box.appendChild(modeLine);
  box.appendChild(assumeLine);
  box.appendChild(links);
  box.appendChild(mapWrap);
  var timer = null;
  function fileLink(alias) {
    return el("a", { href: API + "/task-package/current/press/file/" +
                              encodeURIComponent(alias),
                     target: "_blank", class: "chip chip-neutral",
                     text: t("flow.vmp.run.f." + alias) });
  }
  function render(st) {
    links.textContent = "";
    mapWrap.textContent = "";
    if (!st || !st.present) { stateLine.textContent = ""; warnLine.hidden = true; return; }
    var noCrit = st.no_criterion || {};
    var shownTasks = [];
    if (showP) { shownTasks.push("pairing"); }
    if (showR) { shownTasks.push("rostering"); }
    var noCritTasks = shownTasks.filter(function (task) {
      return noCrit[task] === true;
    });
    if (noCritTasks.length) {
      warnLine.textContent = t("flow.vmp.run.nocriterion") + " (" +
        noCritTasks.map(function (task) {
          return t("flow.vmp.run." + task);
        }).join(", ") + ")";
      warnLine.hidden = false;
    } else {
      warnLine.hidden = true;
    }
    var running = st.done === false;
    btnP.disabled = running;
    btnR.disabled = running;
    var pr = st.pairing_run || null;
    // W-11: список попыток пейринга; при смене пакета выбор
    // сбрасывается; без выбора человека — последний утверждённый.
    if (selectedPackage !== st.package_sha) {
      selectedPairing = "";
      selectedPackage = st.package_sha;
    }
    var approvedRun = pr && pr.approved && pr.solver_run_id
      ? String(pr.solver_run_id) : "";
    var seenRun = {};
    pairingSel.textContent = "";
    pairingSel.appendChild(el("option", { value: "",
                                          text: t("flow.vmp.run.choosePairing") }));
    if (approvedRun) {
      seenRun[approvedRun] = true;
      pairingSel.appendChild(el("option", { value: approvedRun,
        text: approvedRun + " · " + t("flow.vmp.run.pairing_run") +
          (pr.chains != null ? " · " + String(pr.chains) : "") }));
    }
    (st.pairing_candidates || []).forEach(function (c) {
      if (!c || !c.pairing_run_id || c.approved === false ||
          seenRun[c.pairing_run_id]) { return; }
      seenRun[c.pairing_run_id] = true;
      pairingSel.appendChild(el("option", { value: String(c.pairing_run_id),
        text: String(c.pairing_run_id) +
          (c.engine ? " · " + String(c.engine) : "") +
          (c.finished_at ? " · " + String(c.finished_at) : "") +
          (c.chains != null ? " · " + String(c.chains) : "") }));
    });
    if (!selectedPairing || !seenRun[selectedPairing]) {
      selectedPairing = approvedRun;
    }
    pairingSel.value = selectedPairing;
    selectedPairing = pairingSel.value || "";
    pairingSel.disabled = running;
    btnRP.disabled = running || !selectedPairing;
    btnRP.dataset.run = selectedPairing;
    // W-12: неудача на выбранном каталоге — не доказательство
    // невозможности; подсказка и следующие шаги — словами и кнопками.
    selectionLine.textContent = st.selection_note ? String(st.selection_note) : "";
    var acts = st.next_actions || [];
    reselectBtn.hidden = !showR || acts.indexOf("reselect_pairing") < 0;
    rebuildBtn.hidden = !showP || acts.indexOf("expand_catalog") < 0;
    reselectBtn.disabled = running;
    rebuildBtn.disabled = running;
    pairLine.textContent = pr
      ? t("flow.vmp.run.pairing_run") + ": " + String(pr.solver_run_id || "—") +
        (pr.verdict ? " · " + String(pr.verdict) : "") +
        (pr.chains != null ? " · " + String(pr.chains) : "")
      : t("flow.vmp.run.pairing_run.none");
    // Панель одной задачи (блок схемы): если последнее нажатие было по
    // другой задаче, состояние берётся из последней попытки ЭТОЙ задачи,
    // а итог/режим/допущение чужой задачи не показываются.
    var foreign = task !== "all" && st.task && st.task !== task;
    if (foreign) {
      var att = (st.attempts || []).filter(function (a) {
        return a && a.task === task; })[0];
      stateLine.textContent = t("flow.vmp.run.state") + ": " +
        (att ? String(att.state || "") +
               (att.verdict ? " · " + String(att.verdict) : "")
             : t("flow.vmp.run.none"));
      modeLine.textContent = "";
      assumeLine.textContent = "";
      resultLine.textContent = "";
    } else {
    // Ход идущего расчёта — отдельный ключ progress (аудит A10,
    // 04.09.2026): состояние остаётся «кнопка: идёт», шаг — рядом.
    stateLine.textContent = t("flow.vmp.run.state") + ": " +
      (st.state ? String(st.state) : t("flow.vmp.run.none")) +
      (st.progress ? " · " + String(st.progress) : "") +
      (st.engine ? " · " + st.engine : "") +
      // Кто нажал и не держит ли линию другой живой процесс службы
      // (10.09.2026): при чужом держателе страница не показывает просто
      // «идёт», а называет держателя словами сервера. Актор — при
      // нажатии: без нажатий сервер отдаёт его словами «неизвестен:
      // вызов без личности», и рядом с «нажатий ещё не было» это была
      // бы неправда о несуществующем вызове.
      (st.state && st.actor ? " · " + String(st.actor) : "") +
      (st.error ? " — " + String(st.error) : "") +
      (st.holder_note ? " — " + String(st.holder_note) : "");
    }
    modeLine.textContent = st.mode
      ? t("flow.vmp.run.mode") + ": " + String(st.mode) +
        (st.mode_note ? " — " + String(st.mode_note) : "")
      : "";
    var hist = st.assumptions && st.assumptions.history;
    assumeLine.textContent = hist && hist.note
      ? t("flow.vmp.run.history") + ": " + String(hist.note)
      : "";
    resultLine.textContent = resultText(st);
    var files = st.files || {};
    shownTasks.forEach(function (task) {
      var al = files[task] || [];
      if (!al.length) { return; }
      links.appendChild(el("p", { class: "vmp-note",
        text: t("flow.vmp.run.links." + task) }));
      links.appendChild(el("div", { class: "vmp-chips" },
                           al.map(fileLink)));
    });
    // Экспорт точной модели для внешнего CPLEX — после пейринга.
    if (showP && (files.pairing || []).length) {
      var xb = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.vmp.run.mps") });
      var xs = st.export_mps_state;
      xb.disabled = xs === "экспорт идёт";
      xb.onclick = function () {
        xb.disabled = true;
        api("/task-package/current/export-mps", { method: "POST" })
          .then(render)
          .catch(function (e) {
            xb.disabled = false;
            stateLine.textContent = String((e && e.body && e.body.detail)
                                           || (e && e.message) || e);
          });
      };
      var xrow = [xb];
      if (xs) {
        xrow.push(el("span", { class: "vmp-note",
          text: String(xs) + (st.export_mps_error
                              ? " — " + String(st.export_mps_error)
                              : "") }));
      }
      links.appendChild(el("div", { class: "vmp-row" }, xrow));
      if ((st.export_mps_files || []).length) {
        links.appendChild(el("div", { class: "vmp-chips" },
          st.export_mps_files.map(function (n) {
            return el("a", { href: API + "/task-package/current/export-mps/"
                                    + "file/" + encodeURIComponent(n),
                             target: "_blank", class: "chip chip-neutral",
                             text: n });
          })));
      }
      if (xs === "экспорт идёт" && !timer) {
        timer = setInterval(poll, 3000);
      }
    }
    // Визуализация решения в панели задачи (слово владельца
    // 05.09.2026): схема цепочек — у пейринга, доска ростера — у
    // ростеринга; сертификат — в блоках «Проверка … по ВМП».
    if (showP && (files.pairing || []).indexOf("каталог-цепочек") >= 0) {
      var gb = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.task.chains") });
      var gWrap = el("div", {});
      gb.onclick = function () {
        gb.disabled = true;
        api("/task-package/current/press/file/" +
            encodeURIComponent("каталог-цепочек")).then(function (cat) {
          gb.remove();
          vmpChainsGantt(gWrap, cat);
        }).catch(function (e) {
          gb.disabled = false;
          gWrap.appendChild(el("p", { class: "vmp-note",
            text: String((e && e.message) || e) }));
        });
      };
      var cb = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.cert.open.pairing") });
      cb.onclick = function () { flowOpenCertPanel("pairing"); };
      mapWrap.appendChild(el("div", { class: "vmp-row" }, [gb, cb]));
      mapWrap.appendChild(gWrap);
    }
    var rAl = _flowRosterAliases(st);
    if (showR && rAl.files.indexOf(rAl.roster) >= 0) {
      var bb = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.task.board") });
      var bWrap = el("div", {});
      bb.onclick = function () {
        bb.disabled = true;
        api("/task-package/current/press/file/" +
            encodeURIComponent(rAl.roster)).then(function (sol) {
          bb.remove();
          vmpRosterBoard(bWrap, sol);
        }).catch(function (e) {
          bb.disabled = false;
          bWrap.appendChild(el("p", { class: "vmp-note",
            text: String((e && e.message) || e) }));
        });
      };
      var rc = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.cert.open.rostering") });
      rc.onclick = function () { flowOpenCertPanel("rostering"); };
      mapWrap.appendChild(el("div", { class: "vmp-row" }, [bb, rc]));
      mapWrap.appendChild(bWrap);
    }
    if (showR && st.roster_map) {
      var mb = el("button", { class: "rv-seg", type: "button",
                              text: t("flow.vmp.run.map") });
      mb.onclick = function () {
        mb.disabled = true;
        api("/task-package/current/roster-map").then(function (net) {
          mb.remove();
          vmpNetBlock(mapWrap, net);
        }).catch(function (e) {
          mb.disabled = false;
          mapWrap.appendChild(el("p", { class: "vmp-note",
            text: String((e && e.body && e.body.detail) ||
                         (e && e.message) || e) }));
        });
      };
      mapWrap.appendChild(mb);
    }
    var busy = running || st.export_mps_state === "экспорт идёт";
    if (busy && !timer) {
      timer = setInterval(poll, 3000);
    } else if (!busy && timer) {
      clearInterval(timer);
      timer = null;
    }
    // Запись о прогоне идёт следом за кнопками: после нажатия и после
    // итога попытки карточка записи перечитывается вместе с опросом
    // блока расчёта (проект «Запись о прогоне», раздел 10, п. 4).
    vmpRecordSignal(st);
  }
  function poll() {
    api("/task-package/current/press").then(render)
      .catch(function () {});
  }
  function press(task, pairingRunId) {
    var q = "?task=" + task + "&spentLimitSeconds=" +
      encodeURIComponent(limit.value || "60");
    if (task === "pairing") {
      q += "&engine=" + encodeURIComponent(engSel.value);
    }
    if (pairingRunId) {
      // начальное решение по цепочкам (см. flowActionRun)
      q += "&pairingRunId=" + encodeURIComponent(pairingRunId) +
           "&moves=chain-seed";
    }
    btnP.disabled = true;
    btnR.disabled = true;
    btnRP.disabled = true;
    _flowSrcEpoch++;
    api("/task-package/current/press" + q, { method: "POST" })
      .then(render)
      .catch(function (e) {
        btnP.disabled = false;
        btnR.disabled = false;
        btnRP.disabled = !btnRP.dataset.run;
        // Гейт «от пакета» (проект, раздел 10, п. 1): отказ нажатия у
        // необъявленного прогона — это НАЗВАННАЯ причина и ссылка на
        // форму объявления, а не безымянная строка ошибки.
        var refusal = _runRecRefusal(e);
        if (refusal) {
          stateLine.textContent = refusal.words ||
            _runRecReasonWords(refusal.code);
          var link = el("button", { class: "run-rec-goto", type: "button",
                                    text: t("flow.vmp.record.goto") });
          link.onclick = function () { _runRecScrollTo(false); };
          stateLine.appendChild(document.createTextNode(" — "));
          stateLine.appendChild(link);
          return;
        }
        stateLine.textContent = _crunsErrWords(e);
      });
  }
  btnP.onclick = function () { press("pairing"); };
  btnR.onclick = function () { press("rostering"); };
  btnRP.onclick = function () {
    if (btnRP.dataset.run) { press("rostering", btnRP.dataset.run); }
  };
  poll();
}

/* Схема цепочек пейринга по пакету (слово владельца 05.09.2026:
 * визуализация пейринга): строки — цепочки каталога построителя,
 * полосы — смены (busyWindows, минуты эпохи), подписи — номера рейсов
 * по времени вылета из идентификатора «PLxxx|дата-время». */
function vmpChainsGantt(host, cat) {
  host.textContent = "";
  var chains = (cat && cat.selected) || [];
  if (!chains.length) {
    host.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.task.chains.none") }));
    return;
  }
  function legT(fn) {
    var p = String(fn).split("|");
    return { num: p[0], at: p[1] ? new Date(p[1]).getTime() : NaN };
  }
  var rows = chains.map(function (c, i) {
    var legs = (c.fns || []).map(legT).filter(function (l) { return !isNaN(l.at); });
    var wins = (c.busyWindows || []).map(function (w) {
      return [w[0] * 60000, w[1] * 60000];
    });
    var t0 = Math.min.apply(null, legs.map(function (l) { return l.at; })
                            .concat(wins.map(function (w) { return w[0]; })));
    return { i: i, legs: legs, wins: wins, t0: t0,
             nights: c.price && c.price.nights, cost: c.cost,
             seats: (c.seats || []).length };
  }).sort(function (a, b) { return a.t0 - b.t0; });
  var tmin = Infinity, tmax = -Infinity;
  rows.forEach(function (r) {
    r.legs.forEach(function (l) {
      tmin = Math.min(tmin, l.at); tmax = Math.max(tmax, l.at + 3600000);
    });
    r.wins.forEach(function (w) {
      tmin = Math.min(tmin, w[0]); tmax = Math.max(tmax, w[1]);
    });
  });
  var day0 = new Date(tmin); day0.setHours(0, 0, 0, 0);
  var dayN = new Date(tmax); dayN.setHours(24, 0, 0, 0);
  var span = dayN.getTime() - day0.getTime();
  // левая колонка шире: подпись ряда «цепочка NN: рейсов N, ночёвок N»
  // при 150 px обрезалась слева (замечание владельца на демо 06.09.2026)
  var W = 960, L = 215, RH = 22, H = 34 + rows.length * RH;
  var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("class", "vmp-net-svg vmp-gantt");
  svg.setAttribute("height", H);
  function X(ms) { return L + (ms - day0.getTime()) / span * (W - L - 10); }
  function sv(tag, attrs, txt) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (txt != null) { e.textContent = txt; }
    return e;
  }
  // сутки — сетка и подписи
  for (var d = new Date(day0); d < dayN; d.setDate(d.getDate() + 1)) {
    var x = X(d.getTime());
    svg.appendChild(sv("line", { x1: x, y1: 24, x2: x, y2: H,
      stroke: "var(--border)", "stroke-width": 1 }));
    svg.appendChild(sv("text", { x: x + 4, y: 16, "font-size": 11,
      fill: "var(--muted)" }, d.toLocaleDateString(undefined,
        { day: "2-digit", month: "2-digit" })));
  }
  rows.forEach(function (r, k) {
    var y = 30 + k * RH;
    var rowLab = sv("text", { x: 4, y: y + 15, "font-size": 11.5,
      fill: "var(--ink)" }, tf("flow.task.chains.row", {
        n: r.i + 1, legs: r.legs.length, nights: r.nights || 0 }));
    svg.appendChild(rowLab);
    r.wins.forEach(function (w) {
      svg.appendChild(sv("rect", { x: X(w[0]), y: y + 4,
        width: Math.max(3, X(w[1]) - X(w[0])), height: RH - 8, rx: 4,
        fill: "var(--rv-duty, #60a5fa)", opacity: ".55" }));
    });
    // Подписи рейсов не налезают друг на друга (замечание владельца на
    // демо 06.09.2026): каждая следующая подпись начинается не раньше
    // конца предыдущей (ширина оценена по числу знаков), точка рейса
    // остаётся на своём времени; полный список рейсов — в подсказке ряда.
    var lastEnd = -Infinity;
    var CH = 6;  // ширина знака при шрифте 10
    r.legs.forEach(function (l) {
      var x = X(l.at);
      svg.appendChild(sv("circle", { cx: x, cy: y + RH / 2, r: 3.5,
        fill: "var(--accent, #2563eb)" }));
      var lx = Math.max(x + 5, lastEnd + 4);
      var txt = sv("text", { x: lx, y: y + RH / 2 + 4,
        "font-size": 10, fill: "var(--ink)" }, l.num);
      txt.appendChild(sv("title", {}, l.num + " · " +
        new Date(l.at).toLocaleString()));
      svg.appendChild(txt);
      lastEnd = lx + String(l.num).length * CH;
    });
    rowLab.appendChild(sv("title", {},
      r.legs.map(function (l) { return l.num; }).join(" → ")));
  });
  host.appendChild(el("p", { class: "vmp-note",
    text: tf("flow.task.chains.brief", { n: chains.length,
      days: Math.round(span / 86400000) }) }));
  host.appendChild(svg);
}

/* Доска ростера по пакету (слово владельца 05.09.2026: визуализация
 * ростеринга): сотрудники по базам × сутки горизонта, в клетке — номера
 * рейсов смены; пустая клетка — выходной или резерв. */
function vmpRosterBoard(host, sol) {
  host.textContent = "";
  var asg = (sol && sol.assignments) || [];
  if (!asg.length) {
    host.appendChild(el("p", { class: "vmp-note",
                              text: t("flow.task.board.none") }));
    return;
  }
  var days = {}, emps = {};
  asg.forEach(function (a) {
    var day = String(a.date || a.dep_time || "").slice(0, 10);
    if (!day) { return; }
    days[day] = true;
    var e = emps[a.employee] || (emps[a.employee] = {
      id: a.employee, base: a.base || "", role: a.role || "", cells: {} });
    if (a.role && !e.role) { e.role = a.role; }
    (e.cells[day] = e.cells[day] || []).push(
      String(a.flight).split("|")[0] + (a.deadhead ? "*" : ""));
  });
  var dayList = Object.keys(days).sort();
  var empList = Object.keys(emps).map(function (k) { return emps[k]; })
    .sort(function (a, b) {
      return (a.base + a.id).localeCompare(b.base + b.id);
    });
  var table = el("table", { class: "table vmp-board" });
  var thead = el("thead", {});
  var hr = el("tr", {}, [el("th", { text: t("flow.vmp.crew.id") }),
                         el("th", { text: t("flow.vmp.crew.base") }),
                         el("th", { text: t("flow.vmp.crew.roles") })]);
  dayList.forEach(function (d) {
    hr.appendChild(el("th", { text: d.slice(8, 10) + "." + d.slice(5, 7) }));
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = el("tbody", {});
  empList.forEach(function (e) {
    var tr = el("tr", {}, [el("td", { text: e.id }),
                           el("td", { text: e.base }),
                           el("td", { text: e.role })]);
    dayList.forEach(function (d) {
      var c = e.cells[d];
      tr.appendChild(el("td", { class: c ? "vmp-board-on" : "vmp-board-off",
                                text: c ? c.join(" ") : "" }));
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  host.appendChild(el("p", { class: "vmp-note",
    text: tf("flow.task.board.brief", { people: empList.length,
      days: dayList.length, seats: asg.length }) }));
  var wrap = el("div", { class: "table-wrap" });
  wrap.appendChild(table);
  host.appendChild(wrap);
}

/* Сертификат на экране — проверка решения по ВМП (слово владельца
 * 05.09.2026: главная фича, не ссылка на файл): вердикт, три статуса
 * результата, счётчики, таблица «правило → проверка → статус», находки. */
function vmpCertPanel(host, doc) {
  host.textContent = "";
  var cert = (doc && doc.certificate) || {};
  var res = cert.result || {};
  var checks = (doc && doc.checks) || [];
  var findings = (doc && doc.findings) || [];
  var ok = cert.certified === true;
  var head = el("div", { class: "banner-" + (ok ? "ok" : "warn") });
  head.appendChild(el("b", { text: t("flow.cert.verdict") + ": " +
    t(ok ? "flow.cert.certified" : "flow.cert.notcertified") }));
  var parts = [];
  if (res.feasibility) {
    parts.push(t("flow.vmp.run.res.feasibility") + ": " +
      t("flow.vmp.run.res.feasibility." + res.feasibility));
  }
  if (res.cost) {
    parts.push(t("flow.vmp.run.res.cost") + ": " +
      t("flow.vmp.run.res.cost." + res.cost));
  }
  if (res.optimality) {
    parts.push(t("flow.vmp.run.res.optimality") + ": " +
      t("flow.vmp.run.res.optimality." + res.optimality));
  }
  if (res.coverage_complete === true) {
    parts.push(t("flow.vmp.run.res.coverage.full"));
  } else if (res.coverage_complete === false) {
    parts.push(t("flow.vmp.run.res.coverage.partial") + " (" +
      (res.uncovered || []).length + ")");
  }
  head.appendChild(el("div", { text: parts.join("; ") }));
  host.appendChild(head);
  var run = checks.filter(function (c) { return c.status === "прогнана"; }).length;
  var crit = findings.filter(function (f) {
    return String(f.severity || "").indexOf("БЛОК") === 0 ||
      String(f.severity || "").indexOf("КРИТ") === 0; }).length;
  host.appendChild(el("p", { class: "vmp-note", text:
    t("flow.cert.rules") + ": " + String(cert.rules_considered != null
      ? cert.rules_considered : "—") +
    " · " + t("flow.cert.checks") + ": " + run + " / " +
    (checks.length - run) +
    " · " + t("flow.cert.findings") + ": " + findings.length +
    (crit ? " (" + t("flow.cert.blocking") + " " + crit + ")" : "") +
    ((doc.certifier && doc.certifier.model)
      ? " · " + t("flow.cert.certifier") + ": " + doc.certifier.model : "") }));
  var uncovered = cert.rules_uncovered || res.uncovered || [];
  if (uncovered.length) {
    host.appendChild(el("p", { class: "vmp-note", text:
      t("flow.vmp.run.res.uncovered") + ": " + uncovered.join(", ") }));
  }
  var table = el("table", { class: "table vmp-cert" });
  table.appendChild(el("thead", {}, [el("tr", {}, [
    el("th", { text: t("flow.cert.col.rule") }),
    el("th", { text: t("flow.cert.col.check") }),
    el("th", { text: t("flow.cert.col.status") }),
    el("th", { text: t("flow.cert.col.rows") }),
    el("th", { text: t("flow.cert.col.note") })])]));
  var tb = el("tbody", {});
  checks.forEach(function (c) {
    var rule = (c.basis && c.basis.rule) || "";
    var st = String(c.status || "");
    var bad = findings.some(function (f) { return f.check === c.id &&
      String(f.severity || "").indexOf("СПРАВ") !== 0; });
    var tr = el("tr", { class: bad ? "vmp-cert-bad"
      : (st === "прогнана" ? "vmp-cert-ok" : "vmp-cert-skip") }, [
      el("td", { text: rule }),
      el("td", { text: String(c.statement || c.template || c.id).slice(0, 160) }),
      el("td", { text: st + (bad ? " · " + t("flow.cert.violated") : "") }),
      el("td", { text: c.rows_checked != null ? String(c.rows_checked) : "" }),
      el("td", { text: String(c.reason || c.note || "").slice(0, 160) })]);
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  var wrap = el("div", { class: "table-wrap" });
  wrap.appendChild(table);
  host.appendChild(wrap);
  if (findings.length) {
    host.appendChild(el("h4", { text: t("flow.cert.findings") }));
    var ft = el("table", { class: "table vmp-cert" });
    ft.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", { text: "ID" }), el("th", { text: t("flow.cert.col.severity") }),
      el("th", { text: t("flow.cert.col.text") }),
      el("th", { text: t("flow.cert.col.evidence") })])]));
    var fb = el("tbody", {});
    findings.slice(0, 200).forEach(function (f) {
      fb.appendChild(el("tr", {}, [
        el("td", { text: String(f.id || "") }),
        el("td", { text: String(f.severity || "") }),
        el("td", { text: String(f.text || "") }),
        el("td", { text: String(f.evidence || "") })]));
    });
    ft.appendChild(fb);
    var fw = el("div", { class: "table-wrap" });
    fw.appendChild(ft);
    host.appendChild(fw);
  }
}

/* Панель «Проверка … по ВМП» блока схемы: сертификат задачи на экране
 * (json той же кнопки), ссылка на текстовый сертификат; у ростера —
 * ещё сверка с документами а/к (читатель ФПР по номеру прогона). */
function flowOpenCertPanel(task) {
  var isR = task === "rostering";
  var sty = FLOW_STYLE[isR ? "srccheck" : "accept"];
  var m = _flowModal(sty.ico, t(isR ? "flow.cert.h.rostering"
                                    : "flow.cert.h.pairing"), true);
  var note = el("p", { class: "vmp-pkg-brief", text: t("flow.task.loading") });
  m.body.appendChild(note);
  var host = el("div", {});
  m.body.appendChild(host);
  var alias = "сертификат-кнопки-пейринга";
  var aliasTxt = alias + "-текст";
  api("/task-package/current/press").then(function (st) {
    var files = (st && st.files) || {};
    var have;
    if (isR) {
      var ra = _flowRosterAliases(st);
      alias = ra.cert;
      aliasTxt = ra.certTxt;
      have = ra.files.indexOf(alias) >= 0;
    } else {
      have = (files[task] || []).indexOf(alias) >= 0;
    }
    if (!have) {
      note.textContent = t(isR ? "flow.cert.none.rostering"
                               : "flow.cert.none.pairing");
      return;
    }
    note.textContent = "";
    var row = el("div", { class: "vmp-chips" }, [
      el("a", { href: API + "/task-package/current/press/file/" +
                      encodeURIComponent(aliasTxt), target: "_blank",
                class: "chip chip-neutral",
                text: t("flow.vmp.run.f." + aliasTxt) }),
      el("a", { href: API + "/task-package/current/press/file/" +
                      encodeURIComponent(alias), target: "_blank",
                class: "chip chip-neutral",
                text: t("flow.vmp.run.f." + alias) })]);
    m.body.insertBefore(row, host);
    api("/task-package/current/press/file/" + encodeURIComponent(alias))
      .then(function (doc) { vmpCertPanel(host, doc); })
      .catch(function (e) {
        note.textContent = String((e && e.message) || e);
      });
    // Сверка с документами а/к — свой блок схемы («Проверка … по
    // ИДАК», слово владельца 06.09.2026), здесь только указатель.
    m.body.appendChild(el("p", { class: "vmp-note",
      text: t(isR ? "flow.src.see.rostering" : "flow.src.see.pairing") }));
  }).catch(function (e) {
    note.textContent = String((e && e.message) || e);
  });
}

/* Сверка решения с документами а/к (читатель ФПР) по номеру прогона
 * ростеринга из выдачи кнопки — без ввода руками. */
/* Панель «Проверка … по ИДАК» (слово владельца 06.09.2026: сверка с
 * исходными документами — отдельный блок у каждого решения). Слепой
 * читатель получает только клаузы замороженных документов, код
 * исполняет проверки детерминированно; у пейринга правила о людях
 * откладываются на ростеринг поимённо. Состояние — снимок задачи
 * (GET /api/source-check/last?task=…), запуск — POST /api/source-check/run
 * с task; у ростеринга номер прогона решателя берётся из выдачи. */
var _flowSrcEpoch = 0;
var _flowSrcStarted = {};
function _flowSrcStamp() {
  return String(_flowRunCur || "") + ":" + _flowSrcEpoch;
}
function _flowSrcFetch(task, key, stamp) {
  return api("/source-check/last?task=" + task +
    (key ? "&context_key=" + encodeURIComponent(key) : "")).then(function (snap) {
    if (stamp !== _flowSrcStamp() || !snap ||
        (key && snap.context_key !== key) ||
        (snap.status === "done" && !snap.context_key) ||
        (snap.context && snap.context.task !== task) ||
        (snap.context && _flowRunCur && snap.context.cosmos_run !== _flowRunCur)) {
      throw new Error("Выбранное решение изменилось; откройте проверку заново");
    }
    return snap;
  });
}
function _flowSrcStart(task, key) {
  var stamp = _flowSrcStamp(), pinned;
  return _flowSrcFetch(task, key, stamp).then(function (snap) {
    if (!snap.context_key || !snap.context) {
      throw new Error("Точное выбранное решение для сверки не подтверждено");
    }
    pinned = snap.context_key;
    return api("/source-check/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: task, context_key: pinned,
                            solverRunId: snap.context.solver_run_id || "" })
    });
  }).then(function (reply) {
    if (stamp !== _flowSrcStamp() || !reply || reply.context_key !== pinned) {
      throw new Error("Выбранное решение изменилось; откройте проверку заново");
    }
    _flowSrcStarted[task] = { key: pinned, stamp: stamp };
    return reply;
  });
}
function flowOpenSrcPanel(task) {
  var isR = task === "rostering";
  var sty = FLOW_STYLE[isR ? "roster.src" : "pair.src"];
  var m = _flowModal(sty.ico, t(isR ? "flow.src.h.rostering"
                                    : "flow.src.h.pairing"), true);
  m.body.appendChild(el("p", { class: "vmp-note", text: t("flow.src.desc") }));
  _flowSrcCheckBlock(m.body, task);
}

function _flowSrcCheckBlock(host, task) {
  var isR = task === "rostering";
  var box = el("div", { class: "vmp-sec" });
  var line = el("p", { class: "vmp-note" });
  // кнопка запуска сверки — заметная, как «▶ Пейринг» в панели задачи
  // (замечание владельца на демо 06.09.2026: «внутри блоков нет кнопок»)
  var btn = el("button", { class: "btn btn-primary", type: "button",
                           text: "▶ " + t("flow.task.srccheck.run") });
  box.appendChild(el("div", { class: "vmp-row" }, [btn, line]));
  var brief = el("p", { class: "vmp-pkg-brief" });
  var table = el("div", {});
  box.appendChild(brief);
  box.appendChild(table);
  host.appendChild(box);
  var timer = null;
  var stamp = _flowSrcStamp(), contextKey = null, requestNo = 0;
  function vClass(v) {
    return v === "violated" ? "chip chip-bad"
      : v === "ok" ? "chip chip-ok"
      : v === "not_checkable" ? "chip chip-warn" : "chip chip-neutral";
  }
  function render(snap) {
    var st = snap.status || "idle";
    var counts = snap.counts || {};
    line.textContent = t("flow.st." + (st === "running" ? "working" : st)) +
      (snap.at ? " · " + String(snap.at).replace("T", " ") : "") +
      (snap.reader ? " · " + t("flow.p.reader") + " " + snap.reader : "") +
      (snap.error ? " — " + snap.error : "");
    brief.textContent = "";
    table.textContent = "";
    if (st !== "done") {
      if (st === "idle" && !snap.error) {
        brief.textContent = t(isR ? "flow.src.none.rostering"
                                  : "flow.src.none.pairing");
      }
      return;
    }
    var total = 0;
    Object.keys(counts).forEach(function (k) { total += Number(counts[k]) || 0; });
    brief.textContent = t("flow.p.checks") + " " + total +
      " · " + t("flow.src.v.ok") + " " + (counts.ok || 0) +
      " · " + t("flow.src.v.violated") + " " + (counts.violated || 0) +
      " · " + t("flow.src.v.soft_info") + " " + (counts.soft_info || 0) +
      " · " + t("flow.src.v.not_checkable") + " " + (counts.not_checkable || 0) +
      (snap.problems ? " · " + t("flow.src.problems") + " " + snap.problems : "");
    var bad = (counts.violated || 0) > 0;
    table.appendChild(bad
      ? el("p", {}, [el("span", { class: "chip chip-bad",
                                  text: t("flow.src.verdict.bad") })])
      : el("div", { class: "banner-ok", text: t("flow.src.verdict.ok") }));
    var rows = snap.rows || [];
    if (rows.length) {
      var tb = el("tbody", {});
      rows.forEach(function (r) {
        tb.appendChild(el("tr", {}, [
          el("td", { text: String(r.id || "") }),
          el("td", { text: String(r.kind || "") }),
          el("td", { class: "mono", text: String(r.anchor || "") }),
          el("td", {}, [el("span", { class: vClass(r.verdict),
                                     text: t("flow.src.v." + (r.verdict || "not_checkable")) })]),
          el("td", { text: String(r.detail || "") })]));
      });
      var tbl = el("table", { class: "table vmp-cert" }, [
        el("thead", {}, [el("tr", {}, [
          el("th", { text: t("flow.src.col.id") }),
          el("th", { text: t("flow.src.col.kind") }),
          el("th", { text: t("flow.src.col.anchor") }),
          el("th", { text: t("flow.src.col.verdict") }),
          el("th", { text: t("flow.src.col.detail") })])]), tb]);
      table.appendChild(el("div", { class: "table-wrap" }, [tbl]));
    }
    var skipped = snap.skipped_people || [];
    if (!isR && skipped.length) {
      table.appendChild(el("h4", { text: t("flow.src.deferred") }));
      table.appendChild(el("p", { class: "vmp-note", text:
        skipped.map(function (s) {
          return String(s.id || "") + " (" + String(s.kind || "") + ")";
        }).join(", ") }));
    }
    table.appendChild(el("div", { class: "vmp-chips" }, [
      el("a", { href: API + "/source-check/protocol?task=" + task +
                "&context_key=" + encodeURIComponent(snap.context_key),
                target: "_blank", class: "chip chip-neutral",
                text: t("flow.src.protocol") })]));
  }
  function showState() {
    var seq = ++requestNo;
    _flowSrcFetch(task, contextKey, stamp).then(function (snap) {
      if (seq !== requestNo || !box.isConnected) { return; }
      contextKey = snap.context_key;
      render(snap || { status: "idle" });
      if (snap && snap.status === "running") {
        timer = setTimeout(showState, 3000);
      }
    }).catch(function (e) {
      if (seq !== requestNo || !box.isConnected) { return; }
      render({ status: "idle", error: String((e && e.message) || e) });
    });
  }
  btn.onclick = function () {
    btn.disabled = true;
    _flowSrcStart(task, contextKey).then(function (reply) {
      contextKey = reply.context_key;
      toast(t("flow.srccheck.started"), "ok");
      setTimeout(showState, 1500);
    }).catch(function (e) {
      var d = e && e.body && e.body.detail;
      line.textContent = typeof d === "string" ? d : t("flow.srccheck.err");
    }).then(function () { btn.disabled = false; });
  };
  showState();
}

/* Панель «Проверка соответствия» — пакет против ВМП: правила по зонам,
 * матрица готовности, покрытие исполнителями. */
function flowOpenConformPanel() {
  var m = _flowModal(FLOW_STYLE.conform.ico, t("flow.conform.h"), false);
  var host = el("div", {});
  m.body.appendChild(host);
  api("/task-package/current").then(function (cd) {
    if (!cd || !cd.present) {
      host.appendChild(el("p", { class: "vmp-note", text: t("flow.task.nopkg") }));
      return;
    }
    var z = cd.rules_by_zone || {};
    function zn(k) {
      var v = z[k];
      return Array.isArray(v) ? v.length : (typeof v === "number" ? v : 0);
    }
    host.appendChild(el("p", { class: "vmp-pkg-brief", text:
      t("flow.task.pkg") + ": " + String(cd.sha256 || "").slice(0, 12) +
      " · " + t("flow.vmp.cur.zones") + ": " +
      t("flow.vmp.run.pairing") + " " + zn("PAIRING") + " · " +
      t("flow.vmp.run.rostering") + " " + zn("ROSTERING") + " · " +
      t("flow.vmp.cur.z.both") + " " + zn("BOTH") +
      (cd.mg && cd.mg.verdict ? " · " + String(cd.mg.verdict) : "") }));
    var mx = ((cd.mg || {}).columns) || {};
    var table = el("table", { class: "table" });
    table.appendChild(el("thead", {}, [el("tr", {}, [
      el("th", { text: t("flow.conform.task") }),
      el("th", { text: t("flow.conform.state") }),
      el("th", { text: t("flow.conform.launch") }),
      el("th", { text: t("flow.conform.grounds") })])]));
    var tb = el("tbody", {});
    ["pairing", "rostering"].forEach(function (task) {
      var col = mx[task] || {};
      var g = col.grounds || {};
      var why = [];
      if (g.criterion && g.criterion.defined === false) {
        why.push(t("flow.vmp.run.nocriterion").split(":")[0]);
      }
      if (g.questions) {
        why.push(t("flow.conform.questions") + " " +
          (g.questions.open_blocking || 0) + "/" + (g.questions.zone_total || 0));
      }
      tb.appendChild(el("tr", {}, [
        el("td", { text: t("flow.vmp.run." + task) }),
        el("td", { text: String(col.state || "—") }),
        el("td", { text: col.launch_allowed === false ? t("flow.conform.no")
                                                        : t("flow.conform.yes") }),
        el("td", { text: why.join("; ") })]));
    });
    table.appendChild(tb);
    host.appendChild(el("h4", { text: t("flow.conform.matrix") }));
    host.appendChild(table);
    host.appendChild(el("p", { class: "vmp-note", text: t("flow.conform.note") }));
  }).catch(function (e) {
    host.appendChild(el("p", { class: "vmp-note", text: String((e && e.message) || e) }));
  });
}

/* Панель «Пул цепочек» — вход построителя пейринга и его каталог. */
function flowOpenPoolPanel() {
  var m = _flowModal(FLOW_STYLE["pairing.pool"].ico, t("flow.pool.h"), true);
  var host = el("div", {});
  m.body.appendChild(host);
  api("/task-package/current/press").then(function (st) {
    var have = ((st && st.files) || {}).pairing || [];
    if (have.indexOf("каталог-цепочек") < 0) {
      host.appendChild(el("p", { class: "vmp-note", text: t("flow.pool.none") }));
      return;
    }
    return api("/task-package/current/press/file/" +
               encodeURIComponent("каталог-цепочек")).then(function (cat) {
      var sel = cat.selected || [];
      var legs = 0, nights = 0;
      sel.forEach(function (c) {
        legs += (c.fns || []).length;
        nights += (c.price && c.price.nights) || 0;
      });
      host.appendChild(el("p", { class: "vmp-pkg-brief", text:
        tf("flow.pool.brief", { chains: sel.length, legs: legs, nights: nights,
          status: String(cat.ip_status || "—"),
          profile: String((cat.profile && cat.profile.name) || cat.profile || "—") }) }));
      var cov = cat.coverage || {};
      if (Object.keys(cov).length) {
        var covParts = [];
        if (cov.legs_total != null) {
          covParts.push(t("flow.pool.cov.legs") + " " + cov.legs_total);
        }
        if (cov.uncovered != null) {
          covParts.push(t("flow.pool.cov.uncovered") + " " + cov.uncovered);
        }
        if (cov.not_built != null) {
          covParts.push(t("flow.pool.cov.notbuilt") + " " + cov.not_built);
        }
        if (cov.complete != null) {
          covParts.push(cov.complete ? t("flow.pool.cov.complete")
                                     : t("flow.pool.cov.incomplete"));
        }
        host.appendChild(el("p", { class: "vmp-note", text:
          t("flow.pool.coverage") + ": " + covParts.join(" · ") }));
      }
      var g = el("div", {});
      host.appendChild(g);
      vmpChainsGantt(g, cat);
    });
  }).catch(function (e) {
    host.appendChild(el("p", { class: "vmp-note", text: String((e && e.message) || e) }));
  });
}

/* Панель «Решения» — итог по пакету: доска ростера, карта, выгрузки. */
function flowOpenOutPanel() {
  var m = _flowModal(FLOW_STYLE.out.ico, t("flow.out.h"), true);
  var host = el("div", {});
  m.body.appendChild(host);
  api("/task-package/current/press").then(function (st) {
    var files = (st && st.files) || {};
    // Ростер есть и у штатного пути (rostering), и у пути «на утверждённом
    // пейринге» (rostering_on_pairing) — блок «Решения» не должен говорить
    // «решений ещё нет» при зелёном ростере второго пути (Prod 06.09.2026).
    // 07.09.2026: и при одном пейринге панель не уходит в «решений ещё
    // нет» — ряд «Выгрузить» доступен любому решению; доска и карта —
    // только при ростере.
    var hasRoster = !!((files.rostering || []).length ||
                       (files.rostering_on_pairing || []).length);
    var hasPairing = !!(files.pairing || []).length;
    if (!hasRoster && !hasPairing) {
      host.appendChild(el("p", { class: "vmp-note", text: t("flow.out.none") }));
      return;
    }
    host.appendChild(el("p", { class: "vmp-pkg-brief", text:
      t("flow.vmp.run.state") + ": " + String(st.state || "") +
      (st.verdict ? " · " + String(st.verdict) : "") }));
    var chips = el("div", { class: "vmp-chips" });
    ["pairing", "rostering", "rostering_on_pairing"].forEach(function (task) {
      (files[task] || []).forEach(function (alias) {
        chips.appendChild(el("a", { href: API + "/task-package/current/press/file/" +
          encodeURIComponent(alias), target: "_blank", class: "chip chip-neutral",
          text: t("flow.vmp.run.f." + alias) }));
      });
    });
    host.appendChild(chips);
    // Ряд «Выгрузить»: доступность форматов — по указателю сервера
    // (/task-package/current/export), причины недоступности — его словами.
    var expHost = el("div", {});
    host.appendChild(el("h4", { text: t("flow.out.export") }));
    host.appendChild(expHost);
    api("/task-package/current/export").then(function (ix) {
      flowExportRows(expHost, ix);
    }).catch(function (e) {
      expHost.appendChild(el("p", { class: "vmp-note",
                                    text: String((e && e.message) || e) }));
    });
    if (!hasRoster) { return; }
    var board = el("div", {});
    host.appendChild(el("h4", { text: t("flow.task.board") }));
    host.appendChild(board);
    api("/task-package/current/press/file/" +
        encodeURIComponent(_flowRosterAliases(st).roster)).then(function (sol) {
      vmpRosterBoard(board, sol);
    }).catch(function () {});
    if (st.roster_map) {
      var mapWrap = el("div", {});
      host.appendChild(el("h4", { text: t("flow.vmp.run.map") }));
      host.appendChild(mapWrap);
      api("/task-package/current/roster-map").then(function (net) {
        vmpNetBlock(mapWrap, net);
      }).catch(function () {});
    }
  }).catch(function (e) {
    host.appendChild(el("p", { class: "vmp-note", text: String((e && e.message) || e) }));
  });
}

/* Ряд «Выгрузить» панели «Решения» (слово владельца 06.09.2026: пейринг и
 * ростеринг — в известных форматах, в том числе Meridian). Доступный формат
 * — ссылка на файл; несертифицированное решение — жёлтый чип с подсказкой;
 * Meridian несертифицированного — только после явного подтверждения
 * (allow_uncertified=1); недоступный формат — серый чип с причиной сервера
 * в подсказке; Meridian-ростер всегда подписан «проект формы». */
function flowExportRows(host, ix) {
  var sols = (ix && ix.solutions) || {};
  [["pairing", "flow.out.export.pairing"],
   ["roster", "flow.out.export.roster"]].forEach(function (pair) {
    var kind = pair[0];
    var sol = sols[kind] || {};
    var row = el("div", { class: "vmp-export-row" });
    row.appendChild(el("span", { class: "vmp-export-label",
                                 text: t(pair[1]) + ":" }));
    var fmts = sol.formats || {};
    Object.keys(fmts).forEach(function (fmt) {
      var info = fmts[fmt] || {};
      var draft = (kind === "roster" && fmt === "meridian");
      var label = draft ? t("flow.out.export.meridianRoster")
                        : t("flow.out.export.fmt." + fmt);
      var draftTitle = draft ? t("flow.out.export.meridianRosterDraft") : "";
      // Ссылка сервера приходит уже с префиксом /api (package_runs.export_index)
      // — клиент её не префиксует; запасной путь (сервер href не прислал) —
      // собственный, с API.
      var href = info.href ||
        (API + "/task-package/current/export/" + kind + "/" + fmt);
      var node;
      if (info.ok) {
        node = el("a", { class: "chip " + (sol.certified ? "chip-info" : "chip-warn"),
                         href: href, download: "", text: label });
        node.title = [sol.certified ? "" : t("flow.out.export.uncert"), draftTitle]
          .filter(Boolean).join(" · ");
      } else if (info.needs_confirm) {
        node = el("a", { class: "chip chip-warn chip-click", href: "#", text: label });
        node.title = [t("flow.out.export.uncert"), draftTitle]
          .filter(Boolean).join(" · ");
        node.onclick = function (ev) {
          ev.preventDefault();
          if (window.confirm(t("flow.out.export.meridianConfirm"))) {
            window.location.href = href + "?allow_uncertified=1";
          }
        };
      } else {
        node = el("span", { class: "chip chip-neutral chip-off", text: label,
                            "aria-disabled": "true" });
        node.title = [tf("flow.out.export.na", { reason: info.reason || "" }),
                      draftTitle].filter(Boolean).join(" · ");
      }
      row.appendChild(node);
    });
    if (kind === "roster") { flowExportIcsPick(row, sol, fmts.ics); }
    host.appendChild(row);
  });
  host.appendChild(el("p", { class: "vmp-note", text: t("flow.out.export.utc.hint") }));
}

/* Календарь одного сотрудника (iCal для рассылки): список табельных — из
 * указателя сервера (roster.employees), выбор скачивает
 * …/roster/ics?employee=<табельный>; ручной правки адреса не требуется.
 * Показывается только при доступном iCal и непустом штате. */
function flowExportIcsPick(row, sol, icsInfo) {
  var emps = Array.isArray(sol.employees) ? sol.employees : [];
  if (!icsInfo || !icsInfo.ok || !emps.length) { return; }
  var href = icsInfo.href || (API + "/task-package/current/export/roster/ics");
  var pick = el("select", { class: "vmp-export-pick",
                            "aria-label": t("flow.out.export.ics.pick") });
  pick.appendChild(el("option", { value: "", text: t("flow.out.export.ics.pick") }));
  emps.forEach(function (id) {
    pick.appendChild(el("option", { value: String(id), text: String(id) }));
  });
  if (!sol.certified) { pick.title = t("flow.out.export.uncert"); }
  pick.onchange = function () {
    if (!pick.value) { return; }
    window.location.href = href + "?employee=" + encodeURIComponent(pick.value);
    pick.value = "";
  };
  row.appendChild(pick);
}

/* Щелчок по блоку схемы → панель его этапа (слово владельца 05.09.2026:
 * демонстрация всего конвейера, не одного блока). */
function flowOpenBlockPanel(id, block) {
  if (id === "vmp") { flowOpenVmpPanel(); return; }
  if (id === "conform") { flowOpenConformPanel(); return; }
  if (id === "pairing.pool") { flowOpenPoolPanel(); return; }
  if (id === "pairing.select") { flowOpenTaskPanel("pairing"); return; }
  if (id === "accept") { flowOpenCertPanel("pairing"); return; }
  if (id === "pair.src") { flowOpenSrcPanel("pairing"); return; }
  if (id === "roster") { flowOpenTaskPanel("rostering"); return; }
  if (id === "srccheck") { flowOpenCertPanel("rostering"); return; }
  if (id === "roster.src") { flowOpenSrcPanel("rostering"); return; }
  if (id === "out") { flowOpenOutPanel(); return; }
  _flowOpenPop(id, block || { status: "idle" });
}

function vmpNetBlock(host, net) {
  var mode = "schema";
  var bar = el("div", { class: "vmp-row" });
  var svg = document.createElementNS("http://www.w3.org/2000/svg",
                                     "svg");
  svg.setAttribute("class", "vmp-net-svg");
  var note = el("div", { class: "vmp-note" });
  function draw() {
    netDrawMap({ svg: svg, note: note, data: net, sel: null,
                 mode: mode, seed: net.source_run || "vmp",
                 redraw: draw });
  }
  function seg(m, label) {
    var b = el("button", { class: "rv-seg", type: "button",
                           text: label });
    b.setAttribute("aria-pressed", String(mode === m));
    b.onclick = function () {
      mode = m;
      bar.textContent = "";
      fillBar();
      draw();
    };
    return b;
  }
  var crewWrap = el("div", {});
  var crewShown = false;
  function crewBtn() {
    var n = (net.employees || []).length;
    var b = el("button", { class: "rv-seg", type: "button",
      text: t("flow.vmp.map.people") + " (" + n + ")" });
    b.setAttribute("aria-pressed", String(crewShown));
    b.onclick = function () {
      crewShown = !crewShown;
      crewWrap.textContent = "";
      if (crewShown) { crewWrap.appendChild(vmpCrewTable(net)); }
      bar.textContent = "";
      fillBar();
    };
    return b;
  }
  function fillBar() {
    if (net.geo && net.geo.complete) {
      bar.appendChild(seg("schema", t("app.pviz.mode.schema")));
      bar.appendChild(seg("world", t("app.pviz.mode.world")));
    }
    bar.appendChild(crewBtn());
  }
  fillBar();
  host.appendChild(bar);
  // Откуда география (принцип координат, ночь 05→06.09.2026): книга
  // а/к первична, справочник по коду — только у классов данных
  // open/proprietary; у синтетического набора без координат в книге
  // «карта мира» честно недоступна.
  var g = net.geo || {};
  var basisKey = g.basis === "book" ? "flow.map.geo.book"
    : g.basis === "reference" ? "flow.map.geo.reference"
    : g.basis === "book+reference" ? "flow.map.geo.mixed"
    : (g.data_class === "synthetic" ? "flow.map.geo.none.synthetic"
                                    : "flow.map.geo.none");
  var basisTxt = t(basisKey);
  if (g.missing && g.missing.length && g.basis !== "none") {
    basisTxt += " · " + t("flow.map.geo.missing") + ": "
      + g.missing.join(", ");
  }
  host.appendChild(el("div", { class: "vmp-note vmp-note-geo",
                               text: basisTxt }));
  host.appendChild(svg);
  host.appendChild(note);
  host.appendChild(crewWrap);
  draw();
}

/* Прокручиваемая таблица основных данных персонала постановки. */
function vmpCrewTable(net) {
  var rows = (net.employees || []).map(function (p) {
    return el("tr", null, [
      el("td", { class: "mono", text: p.id }),
      el("td", { text: p.name || "" }),
      el("td", { class: "mono", text: p.base || "" }),
      el("td", { text: (p.roles || []).join(", ") }),
      el("td", { class: "num", text: String(p.off_days || 0) })]);
  });
  return el("div", { class: "vmp-crew-scroll" }, [
    el("table", { class: "tbl" }, [
      el("thead", null, [el("tr", null, [
        el("th", { text: t("flow.vmp.crew.id") }),
        el("th", { text: t("flow.vmp.crew.name") }),
        el("th", { text: t("flow.vmp.crew.base") }),
        el("th", { text: t("flow.vmp.crew.roles") }),
        el("th", { class: "num",
                   text: t("flow.vmp.crew.off") })])]),
      el("tbody", null, rows)])]);
}

/* Карточка блока: статус, параметры, переход на рабочий экран. */
function _flowOpenPop(id, block) {
  _flowClosePop();
  var meta = FLOW_BLOCKS[id];
  var rows = [];
  rows.push(el("div", { class: "flow-pop-row" }, [
    el("span", { text: t("flow.p.status") }),
    el("span", { text: t("flow.st." + (block.status || "idle")) })
  ]));
  Object.keys(block.params || {}).forEach(function (k) {
    var v = block.params[k];
    if (v === null || v === undefined || v === "") { return; }
    rows.push(el("div", { class: "flow-pop-row" }, [
      el("span", { text: t(FLOW_PARAM_LABEL[k] || k) }),
      el("span", { text: _fmtFlowVal(k, v) })
    ]));
  });
  var pop = el("div", { class: "flow-pop" }, [
    el("span", { class: "flow-pop-close", text: "✕" }),
    el("h5", { text: t(meta.i18n) })
  ].concat(rows));
  pop.querySelector(".flow-pop-close").addEventListener(
    "click", _flowClosePop);
  if (meta.go) {
    var btn = el("button", { class: "btn btn-ghost",
                             text: t("flow.open") });
    btn.addEventListener("click", function () {
      _flowClosePop();
      showGroup(meta.go[0]); showTab(meta.go[1]);
    });
    pop.appendChild(btn);
  }
  // Вход в схему цепочек: у блоков пейринга и приёмки с готовым
  // результатом — вторая кнопка к Ганту и карте сети на «Результатах».
  // Личность набора — из params блока (когда бэкенд её кладёт) либо из
  // блока ВМП того же снимка конвейера.
  if ((id.indexOf("pairing.") === 0 || id === "accept") &&
      block.status === "done") {
    var pvInst = (block.params || {}).instance ||
      (((_flowLast.vmp || {}).params) || {}).instance;
    if (pvInst) {
      var pvGo = el("button", { class: "btn btn-ghost",
                                text: t("app.pviz.openBtn") });
      pvGo.addEventListener("click", function () {
        _flowClosePop();
        var sel = $("results-instance");
        if (sel && state.instances.some(function (i) {
          return i.id === pvInst;
        })) {
          sel.value = pvInst;
        }
        showGroup("opt"); showTab("results");
        // refreshResults асинхронен — подводка к заголовку схемы
        // после первого рендера (заголовок есть при любом исходе).
        setTimeout(function () {
          var h = $("pairing-viz-head");
          if (h) { h.scrollIntoView({ behavior: "smooth", block: "start" }); }
        }, 450);
      });
      pop.appendChild(pvGo);
    }
  }
  var r = _flowRect(id);
  var innerW = _flowDom.inner.offsetWidth || 1180;
  var px = r.x + r.w + 12;
  if (px + 270 > innerW) { px = Math.max(0, r.x - 272); }
  pop.style.left = px + "px";
  pop.style.top = Math.max(0, r.y) + "px";
  _flowDom.inner.appendChild(pop);
  _flowDom.pop = pop;
}

/* ------------------------------------------------------------------
 * Панель действий над правым верхним краем блока — общий контракт
 * интерфейса конвейеров CDPM/COSMOS (слово владельца 06.09.2026:
 * «сделать то же самое… интерфейс конвейеров единообразным»):
 * ▶ выполнить этот блок, ⏭ выполнить отсюда до конца, ⏸/⏹ во время
 * работы, ⚙ настройки блока. Те же обработчики и условия, что у
 * кнопок панели; недоступное — с причиной в подсказке. Панель видна
 * всегда у ближайшего доступного шага и у идущей стадии, у прочих —
 * при наведении; клик по блоку по-прежнему открывает панель деталей.
 * Шаги, у которых есть свой запуск, в порядке конвейера: */
var FLOW_TB_SEQ = ["pairing.select", "pair.src", "roster", "roster.src"];
var FLOW_TB_NA = {
  vmp: "flow.tb.na.vmp", conform: "flow.tb.na.conform",
  "pairing.pool": "flow.tb.na.pool", accept: "flow.tb.na.accept",
  srccheck: "flow.tb.na.srccheck", out: "flow.tb.na.out"
};
/* Блоки без собственного запуска действуют через шаг-производитель
 * (замечание владельца на демо 06.09.2026: «в блоке проверки ростера по
 * ВМП не нажать кнопки»): сертификат пейринга выдаёт нажатие «Пейринг»,
 * сертификат ростера — «Ростеринг»; ▶ и ⏭ на таких блоках запускают
 * производителя, ⚙ открывает панель самого блока. */
var FLOW_TB_PRODUCER = {
  "pairing.pool": "pairing.select", accept: "pairing.select",
  srccheck: "roster", out: "roster"
};
var _flowTbState = { seq: null, busy: false };
var _FLOW_OPTS_KEY = "cosmosPressOpts";

/* Настройки нажатий (движок, предел счёта), общие для панели и панели
 * действий: панель пишет их при смене, кнопка ▶ берёт последние. */
function flowPressOpts(task) {
  var d = { engine: "cpsat", limit: 240 };
  try {
    var raw = localStorage.getItem(_FLOW_OPTS_KEY);
    var all = raw ? JSON.parse(raw) : {};
    var o = all[task] || {};
    if (o.engine) { d.engine = String(o.engine); }
    if (o.limit) { d.limit = Number(o.limit) || d.limit; }
  } catch (e) { /* хранилище недоступно — умолчания */ }
  return d;
}
function flowSavePressOpts(task, opts) {
  try {
    var raw = localStorage.getItem(_FLOW_OPTS_KEY);
    var all = raw ? JSON.parse(raw) : {};
    all[task] = Object.assign({}, all[task] || {}, opts);
    localStorage.setItem(_FLOW_OPTS_KEY, JSON.stringify(all));
  } catch (e) { /* приватный режим — настройки не запомнятся */ }
}

/* Номер прогона решателя из выдачи нажатия (пейринг либо ростеринг)
 * — без ввода руками; null, если выдачи нет. */
function _flowSolverRunId(task) {
  if (task === "pairing") {
    return api("/task-package/current/press/file/" +
               encodeURIComponent("выдача-кнопки-пейринга"))
      .then(function (d) {
        return (d && d.schedule && d.schedule.solver_run_id) || null;
      }).catch(function () { return null; });
  }
  // Ростеринг: последняя выдача — штатная либо «на утверждённом
  // пейринге» (roster_path из состояния кнопки).
  return api("/task-package/current/press").then(function (st) {
    var alias = (st && st.roster_path === "on-pairing")
      ? "выдача-кнопки-на-пейринге" : "выдача-кнопки";
    return api("/task-package/current/press/file/" +
               encodeURIComponent(alias));
  }).then(function (d) {
    return (d && d.schedule && d.schedule.solver_run_id) || null;
  }).catch(function () { return null; });
}

/* Алиасы артефактов ростеринга по последней выдаче. */
function _flowRosterAliases(st) {
  var onp = st && st.roster_path === "on-pairing";
  return {
    files: (st && st.files && (onp ? st.files.rostering_on_pairing
                                   : st.files.rostering)) || [],
    cert: onp ? "сертификат-кнопки-на-пейринге" : "сертификат-кнопки",
    certTxt: onp ? "сертификат-кнопки-на-пейринге-текст"
                 : "сертификат-кнопки-текст",
    roster: onp ? "решение-кнопки-ростер-на-пейринге" : "решение-кнопки-ростер",
    delivery: onp ? "выдача-кнопки-на-пейринге" : "выдача-кнопки"
  };
}

/* Выполнить блок — тот же запрос, что у кнопок панели. Ростеринг с
 * панели действий идёт «на утверждённом пейринге», когда пейринг
 * есть (демонстрационный путь), иначе штатно по рейсо-местам. */
function flowActionRun(id) {
  id = FLOW_TB_PRODUCER[id] || id;
  if (id === "pairing.select") {
    _flowSrcEpoch++;
    var po = flowPressOpts("pairing");
    return api("/task-package/current/press?task=pairing&engine=" +
      encodeURIComponent(po.engine) + "&spentLimitSeconds=" +
      encodeURIComponent(po.limit), { method: "POST" });
  }
  if (id === "roster") {
    _flowSrcEpoch++;
    var ro = flowPressOpts("rostering");
    return _flowSolverRunId("pairing").then(function (rid) {
      // На утверждённом пейринге — с начальным решением по цепочкам
      // (moves=chain-seed): цепочки раздаются людям целиком до поиска,
      // локальный поиск дошлифовывает (06.09.2026: без посева за 240 с
      // оставались разрывы маршрута).
      var q = "?task=rostering&spentLimitSeconds=" +
        encodeURIComponent(ro.limit) +
        (rid ? "&pairingRunId=" + encodeURIComponent(rid) +
               "&moves=chain-seed" : "");
      return api("/task-package/current/press" + q, { method: "POST" });
    });
  }
  if (id === "pair.src" || id === "roster.src") {
    var task = id === "pair.src" ? "pairing" : "rostering";
    return _flowSrcStart(task);
  }
  return Promise.reject(new Error(t(FLOW_TB_NA[id] || "flow.tb.na.out")));
}

/* Дождаться итога блока: нажатие — по состоянию кнопки, сверка — по
 * снимку задачи. Разрешается {ok, why}. */
function flowActionWait(id) {
  var isPress = id === "pairing.select" || id === "roster";
  var task = (id === "pairing.select" || id === "pair.src") ? "pairing"
                                                             : "rostering";
  var source = _flowSrcStarted[task];
  if (!isPress && !source) {
    return Promise.resolve({ ok: false, why: "Нет привязки запущенной проверки" });
  }
  return new Promise(function (resolve) {
    function tick() {
      var p = isPress
        ? api("/task-package/current/press?task=" + task)
        : _flowSrcFetch(task, source.key, source.stamp);
      p.then(function (d) {
        if (isPress) {
          if (d && d.done === false) { setTimeout(tick, 3000); return; }
          var v = String((d && d.verdict) || "");
          var ok = /допустим|построен|сертифицирован/.test(v) &&
                   !/недопустим/.test(v);
          resolve({ ok: ok, why: v || String((d && d.error) || "") });
          return;
        }
        var st = d && d.status;
        if (st === "running") { setTimeout(tick, 3000); return; }
        if (st === "done") {
          var c = (d && d.counts) || {};
          resolve({ ok: !(c.violated > 0),
                    why: c.violated > 0 ? t("flow.src.verdict.bad") : "" });
          return;
        }
        resolve({ ok: false, why: String((d && d.error) || st || "") });
      }).catch(function (e) {
        resolve({ ok: false, why: String((e && e.message) || e) });
      });
    }
    setTimeout(tick, 1500);
  });
}

/* ⏭ Выполнить отсюда до конца: цепочка шагов конвейера по порядку;
 * отказ шага останавливает цепочку словами. */
function flowRunFrom(id) {
  id = FLOW_TB_PRODUCER[id] || id;
  var start = FLOW_TB_SEQ.indexOf(id);
  if (start < 0) { return; }
  if (_flowTbState.busy) { toast(t("flow.tb.na.busy"), "warn"); return; }
  var steps = FLOW_TB_SEQ.slice(start);
  _flowTbState.busy = true;
  _flowTbState.seq = steps.slice();
  toast(tf("flow.tb.seq.start", { n: steps.length }), "ok");
  function next(i) {
    if (i >= steps.length) {
      _flowTbState.busy = false;
      _flowTbState.seq = null;
      toast(t("flow.tb.seq.done"), "ok");
      return;
    }
    var sid = steps[i];
    var name = t(FLOW_BLOCKS[sid].i18n);
    flowActionRun(sid).then(function () {
      return flowActionWait(sid);
    }).then(function (r) {
      if (!r.ok) {
        _flowTbState.busy = false;
        _flowTbState.seq = null;
        toast(tf("flow.tb.seq.fail", { name: name, why: r.why }), "warn");
        return;
      }
      toast(tf("flow.tb.seq.step", { name: name }), "ok");
      next(i + 1);
    }).catch(function (e) {
      _flowTbState.busy = false;
      _flowTbState.seq = null;
      var d = e && e.body && e.body.detail;
      toast(tf("flow.tb.seq.fail", { name: name,
        why: typeof d === "string" ? d : String((e && e.message) || e) }),
        "warn");
    });
  }
  next(0);
}

/* Доступность действий блока по его текущему состоянию. */
function _flowTbAvail(id, st) {
  var status = (st && st.status) || "idle";
  var runnable = FLOW_TB_SEQ.indexOf(id) >= 0;
  var out = { run: null, runFrom: null, pause: t("flow.tb.na.pause"),
              stop: t("flow.tb.na.stop"), settings: null };
  if (!runnable) {
    var prod = FLOW_TB_PRODUCER[id];
    if (prod) {
      // доступность — как у производителя; подсказка называет его
      var pa = _flowTbAvail(prod, _flowLast[prod] || { status: "idle" });
      var via = tf("flow.tb.via", { x: t(FLOW_BLOCKS[prod].i18n) });
      out.run = pa.run ? (pa.run + " — " + via) : null;
      out.runFrom = pa.runFrom ? (pa.runFrom + " — " + via) : null;
      out.viaLabel = via;
      return out;
    }
    out.run = t(FLOW_TB_NA[id] || "flow.tb.na.out");
    out.runFrom = out.run;
    return out;
  }
  if (status === "working" || _flowTbState.busy) {
    out.run = t("flow.tb.na.busy");
    out.runFrom = out.run;
  }
  var last = _flowLast || {};
  var need = (id === "pair.src") ? "accept"
           : (id === "roster.src") ? "srccheck" : null;
  if (need && (!last[need] || last[need].status !== "done")) {
    out.run = out.run || t(id === "pair.src" ? "flow.tb.na.nopairing"
                                             : "flow.tb.na.noroster");
    out.runFrom = out.runFrom || out.run;
  }
  if (id === "pair.src" || id === "roster.src") {
    out.settings = t("flow.tb.na.nosettings");
  }
  return out;
}

function _flowMakeToolbar(id) {
  var tb = el("div", { class: "flow-tb" });
  function btn(cls, icon, key) {
    var b = el("button", { class: "flow-tb-btn " + cls, type: "button",
                           text: icon, title: t(key) });
    b.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    return b;
  }
  var bRun = btn("run", "▶", "flow.tb.run");
  var bFrom = btn("from", "⏭", "flow.tb.runFrom");
  var bPause = btn("pause", "⏸", "flow.tb.pause");
  var bStop = btn("stop", "⏹", "flow.tb.stop");
  var bGear = btn("gear", "⚙", "flow.tb.settings");
  bRun.addEventListener("click", function (ev) {
    ev.stopPropagation();
    if (bRun.disabled) { return; }
    bRun.disabled = true;
    flowActionRun(id).then(function () {
      toast(t("flow.tb.started"), "ok");
    }).catch(function (e) {
      var d = e && e.body && e.body.detail;
      toast(typeof d === "string" ? d : String((e && e.message) || e), "warn");
      bRun.disabled = false;
    });
  });
  bFrom.addEventListener("click", function (ev) {
    ev.stopPropagation();
    if (bFrom.disabled) { return; }
    flowRunFrom(id);
  });
  bGear.addEventListener("click", function (ev) {
    ev.stopPropagation();
    if (bGear.disabled) { return; }
    flowOpenBlockPanel(id, _flowLast[id] || { status: "idle" });
  });
  [bPause, bStop].forEach(function (b) {
    b.addEventListener("click", function (ev) { ev.stopPropagation(); });
  });
  tb.appendChild(bRun); tb.appendChild(bFrom);
  tb.appendChild(bPause); tb.appendChild(bStop); tb.appendChild(bGear);
  function apply(st) {
    var a = _flowTbAvail(id, st);
    function set(b, reason, base) {
      b.disabled = !!reason;
      b.title = reason ? (t(base) + " — " + reason) : t(base);
      b.classList.toggle("na", !!reason);
    }
    set(bRun, a.run, "flow.tb.run");
    set(bFrom, a.runFrom, "flow.tb.runFrom");
    set(bPause, a.pause, "flow.tb.pause");
    set(bStop, a.stop, "flow.tb.stop");
    set(bGear, a.settings, "flow.tb.settings");
  }
  apply({ status: "idle" });
  return { root: tb, apply: apply };
}

/* Ближайший доступный шаг — первый по порядку конвейера блок с
 * запуском, который ещё не выполнен, при выполненном предшественнике. */
function _flowNextStep() {
  var last = _flowLast || {};
  var prevDone = last.vmp && last.vmp.status === "done";
  for (var i = 0; i < FLOW_TB_SEQ.length; i++) {
    var id = FLOW_TB_SEQ[i];
    var st = (last[id] || {}).status || "idle";
    if (st === "working") { return id; }
    if (st !== "done" && prevDone) { return id; }
    prevDone = st === "done";
  }
  return null;
}

/* ---- Перетаскивание файлов на блоки схемы (слово владельца 06.09.2026,
 * образец — зона загрузки блока в панели CDPM: cdpm-ui FilesNode.tsx и
 * запрет dragover/drop на window в main.tsx). Один вход загрузки для
 * кнопки панели ВМП и для блока; сервер остаётся судьёй (400/413/422). */
var _flowUploadMax = null;   // предел байт от сервера (/api/flow)
var _flowUploadBusy = false; // загрузка идёт — повтор отказывается словами
var _flowDropGuardWired = false;

function _fmtMb(bytes) { return (bytes / 1048576).toFixed(1); }
/* Целое с разрядами через пробел (67 108 864): одинаково на пяти языках,
 * без зависимости от локали браузера. */
function _fmtInt(n) {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

function _flowDropTargets() {
  return Object.keys(FLOW_DROP).map(function (id) {
    return "«" + t(FLOW_BLOCKS[id].i18n) + "»";
  }).join(", ");
}

/* Проверка файлов ДО обращения к серверу: блок-приёмник, один файл,
 * расширение из FLOW_DROP, размер в пределах предела сервера (пока
 * предел не известен — размер судит сервер, 413). Возвращает текст
 * отказа либо null (принять). */
function _flowDropProblem(id, files) {
  var spec = FLOW_DROP[id];
  if (!spec) {
    return tf("flow.drop.refuse", { block: t(FLOW_BLOCKS[id].i18n),
                                    target: _flowDropTargets() });
  }
  if (!files || !files.length) { return t("flow.drop.nofile"); }
  if (spec.single && files.length > 1) {
    return tf("flow.drop.one", { n: files.length });
  }
  var f = files[0];
  var name = String(f.name || "");
  var low = name.toLowerCase();
  var okExt = spec.accept.some(function (a) {
    return low.length > a.length && low.slice(-a.length) === a;
  });
  if (!okExt) { return tf("flow.drop.notzip", { name: name || "?" }); }
  if (_flowUploadMax > 0 && f.size > _flowUploadMax) {
    var size = _fmtMb(f.size), max = _fmtMb(_flowUploadMax);
    if (size === max) {
      // округление до 0,1 МБ дало бы «64.0 больше предела 64.0» —
      // тогда честнее байтами
      return tf("flow.drop.big.bytes", { name: name, size: _fmtInt(f.size),
                                         max: _fmtInt(_flowUploadMax) });
    }
    return tf("flow.drop.big", { name: name, size: size, max: max });
  }
  return null;
}

/* Текст отказа сервера: 413 — на языке интерфейса с пределом из
 * /api/flow (пока предел не известен — слова сервера); прочее —
 * .body.detail (HTTPException 400/503 и brief 422) либо message
 * (обрыв связи, status 0). */
function _flowUploadErrorText(e) {
  if (e && e.status === 413 && _flowUploadMax > 0) {
    return tf("flow.drop.limit", { max: _fmtMb(_flowUploadMax) });
  }
  var d = e && e.body && typeof e.body === "object" && e.body.detail;
  return String(d || (e && e.message) || e);
}

/* Единый вход загрузки пакета постановки: кнопка панели ВМП и
 * перетаскивание на блок зовут одну функцию и одну ручку
 * POST /api/task-package/upload (гейт производителя + матрица
 * готовности; пакет становится текущим; расчёт не стартует). Ответ —
 * краткий вердикт brief; отказ сервера — ошибка api() с .body.detail
 * (HTTPException 400/413/503) либо .body = brief (422 REFUSED/GATE_RED). */
function flowUploadPackage(file) {
  if (_flowUploadBusy) {
    return Promise.reject(new Error(t("flow.drop.busy")));
  }
  var fd = new FormData();
  fd.append("file", file, file.name);
  _flowUploadBusy = true;
  return api("/task-package/upload", { method: "POST", body: fd })
    .then(function (brief) {
      _flowUploadBusy = false;
      // схема обновляется сразу, минуя демпфер опроса
      _flowBusyUntil = 0;
      if (_nav.tab === "flow") { refreshFlow(); }
      return brief;
    }, function (e) {
      _flowUploadBusy = false;
      throw e;
    });
}

/* Причины MISMATCH — из списка brief.mismatch (первые три, дальше «…»);
 * слова сервера detail — лишь запасной вариант: они повторяют сам
 * ключ («матрица готовности … запуск запрещён») и причин не несут. */
function _flowMismatchReasons(brief) {
  var list = (brief && brief.mismatch) || [];
  if (!list.length) { return String((brief && brief.detail) || ""); }
  var top = Array.prototype.slice.call(list, 0, 3).map(String);
  return top.join("; ") + (list.length > 3 ? "; …" : "");
}

/* Итог приёма словами: OK — принят; MISMATCH — принят, но матрица
 * готовности запрещает запуск (причины — из brief.mismatch, подробно —
 * блок «Проверка соответствия»); повтор того же пакета — пометка
 * «повтор». */
function _flowDropReport(brief) {
  var v = String((brief && brief.verdict) || "");
  var repeat = brief && brief.already_accepted
    ? " · " + t("flow.vmp.cur.repeat") : "";
  if (v === "MISMATCH") {
    toast(tf("flow.drop.mismatch", { detail: _flowMismatchReasons(brief) || v })
          + repeat, "warn");
  } else {
    toast(tf("flow.drop.ok", { verdict: v }) + repeat, "ok");
  }
}

function _flowHasFiles(ev) {
  var types = ev && ev.dataTransfer && ev.dataTransfer.types;
  if (!types) { return false; }
  // DOMStringList в старых движках — без includes
  return Array.prototype.indexOf.call(types, "Files") >= 0;
}

/* Обработчики приёмника файлов (FLOW_DROP[id]) на элементе root — блок
 * схемы либо строка загрузки в панели блока: подсветка при наведении
 * файла (счётчик dragenter/dragleave — дочерние элементы дают ложные
 * dragleave), приём по drop с индикацией на root; hint — строка
 * подсказки (на время загрузки сменяется на ход), onDone(brief) — вызов
 * после успешного приёма (панель обновляет карточку пакета). Блок без
 * записи в FLOW_DROP на наведение не реагирует (dragenter/dragover не
 * получает), а брошенный на него файл отклоняет словами с именем блока —
 * не молча и не общим отказом холста. */
function _flowWireDrop(id, root, hint, onDone) {
  if (!FLOW_DROP[id]) {
    root.addEventListener("drop", function (ev) {
      if (!_flowHasFiles(ev)) { return; }
      ev.preventDefault();
      ev.stopPropagation();
      toast(_flowDropProblem(id, ev.dataTransfer.files), "warn");
    });
    return;
  }
  var depth = 0;
  function clear() {
    depth = 0;
    root.classList.remove("drop-ok");
  }
  root.addEventListener("dragenter", function (ev) {
    if (!_flowHasFiles(ev)) { return; }
    ev.preventDefault();
    ev.stopPropagation();
    depth += 1;
    root.classList.add("drop-ok");
  });
  root.addEventListener("dragover", function (ev) {
    if (!_flowHasFiles(ev)) { return; }
    ev.preventDefault();
    ev.stopPropagation();
    ev.dataTransfer.dropEffect = "copy";
  });
  root.addEventListener("dragleave", function () {
    depth -= 1;
    if (depth <= 0) { clear(); }
  });
  root.addEventListener("drop", function (ev) {
    if (!_flowHasFiles(ev)) { return; }
    ev.preventDefault();
    ev.stopPropagation();
    clear();
    var files = ev.dataTransfer.files;
    var problem = _flowDropProblem(id, files);
    if (problem) { toast(problem, "error"); return; }
    if (_flowUploadBusy) { toast(t("flow.drop.busy"), "warn"); return; }
    root.classList.add("uploading");
    if (hint) { hint.textContent = t("flow.drop.uploading"); }
    function done() {
      root.classList.remove("uploading");
      if (hint) { hint.textContent = t("flow.drop.hint"); }
    }
    flowUploadPackage(files[0]).then(function (brief) {
      done();
      _flowDropReport(brief);
      if (onDone) { onDone(brief); }
    }, function (e) {
      done();
      toast(tf("flow.drop.fail", { detail: _flowUploadErrorText(e) }),
            "error");
    });
  });
}

/* Файл брошен на штатное поле выбора файла <input type="file"> (экраны
 * данных и правил): туда файл кладёт сам браузер, запрет на window его
 * не касается. Образец cdpm-ui main.tsx — приложение без таких полей,
 * там запрет безусловный; здесь безусловный запрет ломал бы восемь
 * форм импорта. */
function _flowDropOnFileInput(ev) {
  var tgt = ev && ev.target;
  return !!(tgt && tgt.closest && tgt.closest('input[type="file"]'));
}

function _flowVisibleFileInput() {
  var inputs = document.querySelectorAll('input[type="file"]');
  return Array.prototype.some.call(inputs, function (i) {
    return i.offsetParent !== null;
  });
}

/* Куда файл примут — словами по месту броска: панель блока со строкой
 * загрузки (оверлей — не холст), экран с полем выбора файла, иначе —
 * блок-приёмник схемы. */
function _flowStrayDropText(ev) {
  var tgt = ev && ev.target;
  var overlay = tgt && tgt.closest && tgt.closest(".confirm-overlay");
  if (overlay && overlay.querySelector(".vmp-droprow")) {
    return t("flow.drop.panel");
  }
  if (_nav.tab !== "flow" && _flowVisibleFileInput()) {
    return t("flow.drop.form");
  }
  return tf("flow.drop.canvas", { target: _flowDropTargets() });
}

/* Запрет обработки файла браузером (образец cdpm-ui main.tsx): файл,
 * уроненный мимо приёмника, не открывается вместо интерфейса — и не
 * пропадает молча: отказ словами по месту броска. */
function _flowWireWindowDrop() {
  if (_flowDropGuardWired) { return; }
  _flowDropGuardWired = true;
  window.addEventListener("dragover", function (ev) {
    if (!_flowHasFiles(ev) || _flowDropOnFileInput(ev)) { return; }
    ev.preventDefault();
  });
  window.addEventListener("drop", function (ev) {
    if (!_flowHasFiles(ev) || _flowDropOnFileInput(ev)) { return; }
    ev.preventDefault();
    toast(_flowStrayDropText(ev), "warn");
  });
}

function _flowMakeNode(id) {
  var sty = FLOW_STYLE[id];
  var head = el("div", { class: "flow-node-head" }, [
    el("div", { class: "flow-node-ico", text: sty.ico }),
    el("h4", { text: t(FLOW_BLOCKS[id].i18n) })
  ]);
  var toolbar = _flowMakeToolbar(id);
  var stTxt = el("span", { text: "" });
  var stRow = el("div", { class: "flow-node-status flow-st-idle" }, [
    el("span", { class: "dot" }), stTxt]);
  var chips = el("div", { class: "flow-node-chips hidden" });
  // подсказка блока-приёмника файлов (FLOW_DROP): постоянная строка на
  // блоке, во время загрузки — индикация хода
  var hint = FLOW_DROP[id]
    ? el("div", { class: "flow-node-drophint", text: t("flow.drop.hint") })
    : null;
  // чип матрицы готовности (директива владельца 2026-08-29):
  // состояние столбца видно на блоке ДО нажатия
  var ready = null;
  if (id === "pairing.select" || id === "roster") {
    ready = el("span", { class: "flow-ready-chip hidden" });
  }
  var root = el("div", { class: "flow-node" },
    [el("div", { class: "cf-glow-overlay" }), toolbar.root, head]
      .concat([stRow]).concat(hint ? [hint] : [])
      .concat(ready ? [ready] : []).concat([chips]));
  _flowWireDrop(id, root, hint);
  root.style.left = _flowPos[id][0] + "px";
  root.style.top = _flowPos[id][1] + "px";
  root.style.borderColor = sty.br;
  root.style.background =
    "linear-gradient(135deg," + sty.g1 + "," + sty.g2 + ")";
  // перетаскивание: как в CDPM; короткий щелчок — карточка блока
  var drag = null;
  root.addEventListener("pointerdown", function (ev) {
    if (ev.button !== 0) { return; }
    drag = { x0: ev.clientX, y0: ev.clientY,
             px: _flowPos[id][0], py: _flowPos[id][1], moved: false };
    root.setPointerCapture(ev.pointerId);
    root.classList.add("dragging");
    _flowClosePop();
  });
  root.addEventListener("pointermove", function (ev) {
    if (!drag) { return; }
    var rawDx = ev.clientX - drag.x0, rawDy = ev.clientY - drag.y0;
    if (Math.abs(rawDx) + Math.abs(rawDy) > 5) { drag.moved = true; }
    // экранные пиксели -> координаты холста с учётом масштаба; правого
    // и нижнего предела нет — холст растёт по содержимому (_flowFitInner)
    var dx = rawDx / _flowZoom, dy = rawDy / _flowZoom;
    _flowPos[id] = [Math.max(0, drag.px + dx), Math.max(0, drag.py + dy)];
    root.style.left = _flowPos[id][0] + "px";
    root.style.top = _flowPos[id][1] + "px";
    _flowDraw();
  });
  root.addEventListener("pointerup", function (ev) {
    root.classList.remove("dragging");
    if (!drag) { return; }
    var moved = drag.moved;
    drag = null;
    if (moved) { _flowSavePos(); }
    else { flowOpenBlockPanel(id, _flowLast[id] || { status: "idle" }); }
  });
  return { root: root, stRow: stRow, stTxt: stTxt, chips: chips,
           ready: ready, toolbar: toolbar };
}

/* Чип матрицы готовности на блоке: состояние столбца + замок, если
 * запуск запрещён верификацией. */
function _flowReadyChip(nd, col) {
  if (!nd.ready) { return; }
  if (!col || !col.state) {
    nd.ready.classList.add("hidden");
    return;
  }
  nd.ready.classList.remove("hidden");
  nd.ready.className = "flow-ready-chip rd-" + col.state +
    (col.launch_allowed ? "" : " rd-locked");
  nd.ready.textContent =
    (col.launch_allowed ? "" : "🔒 ") +
    t("flow.ready.st." + col.state);
  nd.ready.title = t("flow.ready.launch") + ": " +
    t(col.launch_allowed ? "flow.ready.allowed"
                         : "flow.ready.blocked") +
    ((col.why_blocked || []).length
      ? " — " + col.why_blocked.join("; ") : "");
}

var _flowLast = {};       // последнее состояние блоков для карточки

/* Раскраска блока проверки по оценке итога: зелёный / жёлтый / красный
 * (слово владельца на демо 06.09.2026); null — цвет этапа из FLOW_STYLE. */
var FLOW_GRADE = {
  ok:   { g1: "#16a34a", g2: "#047857", br: "#4ade80", key: "flow.grade.ok" },
  warn: { g1: "#d97706", g2: "#b45309", br: "#fbbf24", key: "flow.grade.warn" },
  bad:  { g1: "#dc2626", g2: "#991b1b", br: "#f87171", key: "flow.grade.bad" }
};
function _flowApplyGrade(nd, id, grade) {
  var g = grade && FLOW_GRADE[grade];
  var sty = g || FLOW_STYLE[id];
  nd.root.style.background =
    "linear-gradient(135deg," + sty.g1 + "," + sty.g2 + ")";
  nd.root.style.borderColor = sty.br;
  ["grade-ok", "grade-warn", "grade-bad"].forEach(function (c) {
    nd.root.classList.remove(c);
  });
  if (g) {
    nd.root.classList.add("grade-" + grade);
    nd.stTxt.textContent += " · " + t(g.key);
  }
}

function _flowEnsureDom() {
  var canvas = $("flow-canvas");
  if (!canvas) { return false; }
  // Холст уже построен: после появления держателя масштаба родитель
  // inner — держатель, а держателя — сам холст (ошибка этой проверки
  // пересобирала весь холст каждый опрос — «моргание» у владельца).
  if (_flowDom && _flowDom.holder.parentNode === canvas) {
    return true;
  }
  canvas.textContent = "";
  canvas.classList.add("flow-wrap");
  _flowWireWindowDrop();
  _flowPos = _flowLoadPos();
  try {
    var z0 = parseFloat(localStorage.getItem(_FLOW_ZOOM_KEY));
    if (z0 > 0) { _flowZoom = Math.max(0.4, Math.min(2, z0)); }
  } catch (e) { /* нет хранилища — масштаб 100% */ }
  var inner = el("div", { class: "flow-inner" });
  var svg = _svg("svg", { class: "flow-edges" });
  var defs = _svg("defs", {});
  [["fl-arr", "#94a3b8"], ["fl-arr-amb", "#f59e0b"]]
    .forEach(function (d) {
      var mk = _svg("marker", {
        id: d[0], viewBox: "0 0 10 10", refX: 9, refY: 5,
        markerWidth: 7, markerHeight: 7,
        orient: "auto-start-reverse" });
      mk.appendChild(_svg("path", { d: "M0,0L10,5L0,10z",
                                    fill: d[1] }));
      defs.appendChild(mk);
    });
  svg.appendChild(defs);
  inner.appendChild(svg);
  var holder = el("div", { class: "flow-scale-holder" });
  _flowDom = { inner: inner, holder: holder, svg: svg, nodes: {},
               frameLabels: [], pop: null };
  FLOW_FRAMES.forEach(function (fr, i) {
    var lab = el("div", { class: "flow-frame-label",
                          text: t(fr.i18n), title: t("flow.frame.drag") });
    // подпись рамки — ручка перетаскивания семейства целиком
    lab.addEventListener("pointerdown", function (ev) {
      _flowStartGroupDrag(i, ev);
    });
    inner.appendChild(lab);
    _flowDom.frameLabels.push(lab);
  });
  Object.keys(FLOW_BLOCKS).forEach(function (id) {
    var nd = _flowMakeNode(id);
    _flowDom.nodes[id] = nd;
    inner.appendChild(nd.root);
  });
  holder.appendChild(inner);
  canvas.appendChild(holder);
  _flowApplyZoom();
  // панель масштаба поверх холста (аналог панели React Flow в CDPM)
  var zb = $("flow-zoombar");
  if (zb && !zb.dataset.wired) {
    zb.dataset.wired = "1";
    var zin = el("button", { type: "button", text: "+" });
    var zout = el("button", { type: "button", text: "−" });
    var zfit = el("button", { type: "button", text: "⌂",
                              title: Math.round(FLOW_ZOOM_DEFAULT * 100) + "%" });
    var zpct = el("div", { class: "flow-zoom-pct",
                           id: "flow-zoom-pct", text: "100%" });
    zin.addEventListener("click", function () {
      _flowSetZoom(_flowZoom * 1.15);
    });
    zout.addEventListener("click", function () {
      _flowSetZoom(_flowZoom / 1.15);
    });
    zfit.addEventListener("click", function () {
      _flowSetZoom(FLOW_ZOOM_DEFAULT);
    });
    [zin, zout, zfit, zpct].forEach(function (x) {
      zb.appendChild(x);
    });
  }
  if (!canvas.dataset.zoomWired) {
    canvas.dataset.zoomWired = "1";
    canvas.addEventListener("wheel", function (ev) {
      if (!ev.ctrlKey) { return; }
      ev.preventDefault();
      _flowSetZoom(_flowZoom * (ev.deltaY < 0 ? 1.1 : 1 / 1.1));
    }, { passive: false });
  }
  // «Прогон целиком» — одна кнопка в шапке (контракт интерфейса
  // конвейеров CDPM/COSMOS, 06.09.2026): цепочка шагов с первого.
  var allBtn = $("btn-flow-all");
  if (allBtn) {
    allBtn.onclick = function () {
      if (!window.confirm(t("flow.tb.confirmAll"))) { return; }
      flowRunFrom(FLOW_TB_SEQ[0]);
    };
  }
  var mapBtn = $("btn-flow-map");
  if (mapBtn && !mapBtn.dataset.wired) {
    mapBtn.dataset.wired = "1";
    mapBtn.addEventListener("click", flowOpenNetPanel);
  }
  var reset = $("btn-flow-reset");
  if (reset && !reset.dataset.wired) {
    reset.dataset.wired = "1";
    reset.addEventListener("click", function () {
      // «Сбросить раскладку» — позиции и масштаб к умолчанию
      try {
        localStorage.removeItem(_FLOW_POS_KEY);
        localStorage.removeItem(_FLOW_ZOOM_KEY);
      } catch (e) {}
      _flowZoom = FLOW_ZOOM_DEFAULT;
      _flowDom = null;
      _flowEnsureDom();
      _flowDraw();
      if (_flowState) { renderFlow(_flowState); }
    });
  }
  _flowDraw();
  return true;
}

var _flowState = null;

var _flowSeen = {};
var _flowBlinkTimers = {};
function renderFlow(stateFlow) {
  if (!_flowEnsureDom()) { return; }
  _flowState = stateFlow;
  // предел размера пакета для проверки до запроса (CREW_CHANNEL_MAX_MB)
  if (stateFlow.upload_max_bytes > 0) {
    _flowUploadMax = stateFlow.upload_max_bytes;
  }
  (stateFlow.blocks || []).forEach(function (b) {
    _flowLast[b.id] = b;
    var nd = _flowDom.nodes[b.id];
    if (!nd) { return; }
    var st = b.status || "idle";
    // Быстрые шаги (гейт пакета, пул цепочек, приёмка) укладываются
    // между двумя опросами схемы; чтобы блок всё же «мигнул» в свой
    // момент (слово владельца 05.09.2026), переход «не работал →
    // выполнен» показывается сначала как работа на полторы секунды.
    var prev = _flowSeen[b.id];
    _flowSeen[b.id] = st;
    if (st === "done" && prev && prev !== "done" && prev !== "working"
        && !_flowBlinkTimers[b.id]) {
      st = "working";
      _flowBlinkTimers[b.id] = setTimeout(function () {
        delete _flowBlinkTimers[b.id];
        if (_flowState) { renderFlow(_flowState); }
      }, 1500);
    } else if (st === "done" && _flowBlinkTimers[b.id]) {
      st = "working";
    }
    var working = st === "working";
    nd.root.classList.toggle("working", working);
    nd.root.classList.toggle("cf-force-motion", working);
    // завершившие работу блоки — зелёным контуром (пакет трёх работ
    // 2026-08-30, симметрично панели CDPM)
    nd.root.classList.toggle("done", st === "done");
    nd.root.classList.toggle("dimmed",
      b.id === "disrupt" && st === "idle");
    nd.stRow.className = "flow-node-status flow-st-" + st;
    nd.stTxt.textContent = t("flow.st." + st);
    // Цвет блока проверки по итогу (слово владельца на демо 06.09.2026):
    // «ok» — зелёный (нарушений нет), «warn» — жёлтый (некритичные
    // находки), «bad» — красный (критичные); до итога — цвет этапа.
    var grade = st === "done" ? (b.params || {}).grade : null;
    _flowApplyGrade(nd, b.id, grade);
    nd.chips.textContent = "";
    var any = false;
    Object.keys(b.params || {}).forEach(function (k) {
      var v = b.params[k];
      if (k === "grade") { return; }
      if (v === null || v === undefined || v === "") { return; }
      any = true;
      nd.chips.appendChild(el("div", { class: "flow-node-chip" }, [
        el("b", { text: _fmtFlowVal(k, v) }),
        el("span", { text: t(FLOW_PARAM_LABEL[k] || k) })]));
    });
    nd.chips.classList.toggle("hidden", !any);
  });
  var rd = (stateFlow.readiness || {}).columns || {};
  _flowReadyChip(_flowDom.nodes["pairing.select"], rd.pairing);
  _flowReadyChip(_flowDom.nodes["roster"], rd.rostering);
  // Панель действий: доступность по состоянию; закреплена у идущей
  // стадии и у ближайшего доступного шага (контракт CDPM/COSMOS).
  var nextStep = _flowNextStep();
  Object.keys(_flowDom.nodes).forEach(function (id) {
    var nd = _flowDom.nodes[id];
    if (!nd || !nd.toolbar) { return; }
    var b = _flowLast[id] || { status: "idle" };
    nd.toolbar.apply(b);
    nd.root.classList.toggle("tb-pinned",
      b.status === "working" || id === nextStep ||
      (_flowTbState.seq || []).indexOf(id) === 0);
  });
  _flowDraw();
  var chip = $("flow-active-chip");
  if (chip) {
    var act = stateFlow.active;
    chip.classList.toggle("hidden", !act);
    if (act) {
      var meta = FLOW_BLOCKS[act];
      chip.textContent = t("flow.active") + ": " +
        t(meta ? meta.i18n : act);
    }
  }
  // текущий прогон COSMOS: чип, переключатель, подсказка над схемой
  _flowRunHeader(stateFlow);
}

var _flowTimer = null;
var _flowBusyUntil = 0;
function refreshFlow() {
  // Демпфер: при загрузке showTab дергается много раз подряд —
  // чаще чем раз в секунду состояние не спрашиваем.
  var now = Date.now();
  if (now < _flowBusyUntil) { return; }
  _flowBusyUntil = now + 1000;
  api("/flow").then(function (st) {
    if (_nav.tab === "flow") { renderFlow(st); }
  }).catch(function (e) { apiErrorToast(e, t("flow.err")); });
  _flowRunSelectRefresh();     // список прогонов для переключателя (20 с)
  if (_flowTimer === null) {
    _flowTimer = setInterval(function () {
      if (_nav.tab !== "flow") { return; }
      api("/flow").then(function (st) {
        if (_nav.tab === "flow") { renderFlow(st); }
      }).catch(function () {});
    }, 2000);
  }
}

/* ================= Прогоны COSMOS (слово владельца 07.09.2026) =============
 * Список прогонов в форме списка CDPM (создать / сделать текущим / отметить /
 * архив / сортировка по дате и имени / ручной порядок вверх-вниз), карточка
 * прогона с паспортом-рецептом, попытками и двумя кнопками повтора у каждой
 * попытки, чип и переключатель текущего прогона в шапке конвейера. Данные —
 * ручки /api/cosmos-runs* по проекту final-report/COSMOS-PROGONY-PROEKT-CLD.md
 * (разделы 9-11, 17.4-17.5): сервер отдаёт коды состояний и причин, слова —
 * здесь по ключам cruns.*. Ответ, которого нет (ручка ещё не готова, 404/500,
 * обрыв связи), показывается словами ошибки — ничего не домысливается. */

var CRUNS_STATE_KINDS = ["new", "accepted", "blocked", "lost", "working",
                         "done", "stalled", "refused", "interrupted"];
var CRUNS_BUCKETS = ["all", "new", "work", "done", "fail"];
var CRUNS_SORT_KEYS = ["order", "created", "name"];
var CRUNS_REPLAY_VERDICTS = ["same", "same-outcome", "differs",
                             "not-comparable"];
var _CRUNS_SORT_KEY = "cosmos.cruns.sort.v1";
var _CRUNS_POLL_MS = 20000;        // список — пока у какого-то прогона busy
var _CRUNS_CARD_POLL_MS = 5000;    // открытая карточка — пока прогон busy

var _cruns = {
  list: null,          // последний GET /api/cosmos-runs → runs[]
  current: null,       // текущий прогон (имя) по списку
  storage: null,       // {state, legacy}
  envPackage: false, inboxDisabled: false,
  error: null,         // ошибка последнего чтения списка (честно, словами)
  query: "", bucket: "all", sort: null,
  view: "runs",        // подраздел вкладки: список COSMOS | все прогоны
  markMode: false, marked: {},
  busy: false,         // идёт запрос, меняющий список (порядок, архив)
  timer: null, wired: false,
  card: null           // открытая карточка: {run, host, doc, timer}
};

/* ------------------------------------------------ чистые функции (стенд) */

/** Корзина пилюль-фильтров по коду состояния прогона (раздел 9.3 проекта):
 *  «Новые» = new + accepted + blocked; «В работе» = working; «Готово» =
 *  done + stalled; «Отказ» = refused + interrupted + lost. Неизвестный код
 *  — ни в одну корзину (виден только под «Все»). */
function cosmosRunsBucket(kind) {
  if (kind === "new" || kind === "accepted" || kind === "blocked") {
    return "new";
  }
  if (kind === "working") { return "work"; }
  if (kind === "done" || kind === "stalled") { return "done"; }
  if (kind === "refused" || kind === "interrupted" || kind === "lost") {
    return "fail";
  }
  return null;
}

/** Подпись состояния по коду; неизвестный код показывается как есть. */
function cosmosRunsStateLabel(kind) {
  if (CRUNS_STATE_KINDS.indexOf(kind) >= 0) {
    return t("cruns.state." + kind);
  }
  return kind ? String(kind) : "—";
}

/** Слова к коду пробела паспорта / отказа сервера: ключ cruns.note.<код>;
 *  код без ключа показывается как есть (честно, не молча); уточнение
 *  сервера (reason_words) — после тире. Уточнение, которое само начинается
 *  словами кода (сервер повторяет фразу: «параметры нажатия не записаны:
 *  mode, …»), показывается один раз — без дубля перед тире. */
function cosmosNoteWords(code, words) {
  var s = "";
  if (code) {
    var key = "cruns.note." + code;
    var v = t(key);
    s = v === key ? String(code) : v;
  }
  if (words) {
    var w = String(words);
    s = !s || w.indexOf(s) === 0 ? w : s + " — " + w;
  }
  return s;
}

/** Происхождение прогона словами по коду origin (manual | auto-receive |
 *  auto-upload | adoption); неизвестный код — как есть. */
function cosmosOriginWords(origin) {
  if (!origin) { return "—"; }
  var key = "cruns.origin." + origin;
  var v = t(key);
  return v === key ? String(origin) : v;
}

/** Роль модели паспорта словами: certifier, source_check_reader, cdpm:<роль
 *  CDPM> (роль CDPM — идентификатор её стадии, показывается в скобках);
 *  неизвестная роль — как есть. */
function cosmosRoleWords(role) {
  var r = String(role || "");
  if (!r) { return "?"; }
  if (r.indexOf("cdpm:") === 0) {
    return t("cruns.role.cdpm") + " (" + r.slice(5) + ")";
  }
  var key = "cruns.role." + r;
  var v = t(key);
  return v === key ? r : v;
}

/** Сравнение записей списка: ручной порядок (position) / дата создания
 *  (Date.parse, NaN → 0) / имя (title || run, без регистра, по правилам
 *  языка интерфейса); при равенстве — по номеру прогона. */
function cosmosRunsSortCompare(a, b, sort) {
  var key = sort && CRUNS_SORT_KEYS.indexOf(sort.key) >= 0 ? sort.key : "order";
  var mul = sort && sort.dir === "desc" ? -1 : 1;
  var c = 0;
  if (key === "created") {
    var ta = a.created_at ? Date.parse(a.created_at) : NaN;
    var tb = b.created_at ? Date.parse(b.created_at) : NaN;
    c = (isNaN(ta) ? 0 : ta) - (isNaN(tb) ? 0 : tb);
  } else if (key === "name") {
    c = String(a.title || a.run || "").toLowerCase().localeCompare(
      String(b.title || b.run || "").toLowerCase(), uiLang());
  } else {
    c = (a.position || 0) - (b.position || 0);
  }
  if (c === 0) {
    c = String(a.run || "").localeCompare(String(b.run || ""));
  }
  return c * mul;
}

/** Строка поиска по записи списка: номер, название, состояние (код и
 *  слова), пакет (sha, прогон CDPM, спецификация, вердикт), паспорт. */
function cosmosRunsHay(r) {
  var p = r.package || {};
  var ps = r.passport_summary || {};
  return [r.run, r.title, r.state, r.state_kind,
          cosmosRunsStateLabel(r.state_kind), p.sha, p.sha12, p.source_run,
          p.spec_version, p.verdict, ps.code, ps.catalog]
    .filter(function (x) { return x !== null && x !== undefined && x !== ""; })
    .map(String).join(" ").toLowerCase();
}

/** Фильтр списка: корзина и подстрока поиска (без регистра). */
function cosmosRunsFilter(list, query, bucket) {
  var q = String(query || "").trim().toLowerCase();
  return (list || []).filter(function (r) {
    if (bucket && bucket !== "all" &&
        cosmosRunsBucket(r.state_kind) !== bucket) { return false; }
    return !q || cosmosRunsHay(r).indexOf(q) >= 0;
  });
}

/** Чип отчёта повтора: класс same / differs / none и текст (у «расходится»
 *  — первые 90 знаков words, целиком — в title). rep — сводка
 *  replay.of.report либо отчёт целиком; без отчёта — «несравнимо». */
function cosmosReplayVerdictLabel(rep) {
  var v = rep && rep.verdict;
  if (v === "same" || v === "same-outcome") {
    return { cls: "same", text: t("cruns.replay.verdict." + v), title: "" };
  }
  if (v === "differs") {
    var w = rep.words ? String(rep.words) : "";
    var short = w.length > 90 ? w.slice(0, 90) + "…" : w;
    return { cls: "differs",
             text: t("cruns.replay.verdict.differs") + (short ? ": " + short : ""),
             title: w };
  }
  var words = rep && rep.words ? String(rep.words) : "";
  return { cls: "none",
           text: t("cruns.replay.verdict.not-comparable") + (words ? ": " + words : ""),
           title: words };
}

/** Состояние кнопки повтора по доступности сервера (replay.journal /
 *  replay.fresh строки попытки): недоступна — причина словами по коду плюс
 *  уточнение сервера; занятый прогон гасит обе; доступная «из журнала»
 *  предупреждает, что полнота журнала проверяется при запуске. */
function cosmosReplayReason(avail, runBusy, mode) {
  if (runBusy) { return { enabled: false, title: cosmosNoteWords("run-busy") }; }
  if (!avail || typeof avail !== "object") {
    return { enabled: false, title: cosmosNoteWords("no-availability") };
  }
  if (!avail.available) {
    return { enabled: false,
             title: cosmosNoteWords(avail.reason_code || "no-availability",
                                    avail.reason_words) };
  }
  return { enabled: true,
           title: mode === "journal" ? t("cruns.replay.journal.hint") : "" };
}

/** Сводка различий назначений ростера по сотрудникам словами и числами
 *  (раздел 17.3 проекта) по форме roster_diff отчёта
 *  (vmp/run/replay_compare.roster_diff): {employees_total, unchanged,
 *  changed, lost, gained, seats: {total, same_employee, other_employee,
 *  unassigned_before, unassigned_after, …}, words, employees: [строки]};
 *  ключа нет — прочерк. */
function cosmosReplayRosterWords(rd) {
  if (!rd || typeof rd !== "object") { return ""; }
  var seats = rd.seats && typeof rd.seats === "object" ? rd.seats : {};
  function num(v) {
    return typeof v === "number" ? v : (v === undefined || v === null ? "—" : v);
  }
  return tf("cruns.replay.roster.summary", {
    n: num(rd.employees_total), same: num(rd.unchanged),
    changed: num(rd.changed), lost: num(rd.lost), gained: num(rd.gained),
    seats: num(seats.total), seats_same: num(seats.same_employee),
    seats_other: num(seats.other_employee),
    un_before: num(seats.unassigned_before),
    un_after: num(seats.unassigned_after) });
}

/** Различия среды отчёта повтора словами: каждая запись environment.differs
 *  (ключ либо {key, source, replay}) относится к одной из категорий
 *  cruns.replay.env.*; пусто — «среда: без различий». */
function cosmosReplayEnvWords(differs) {
  if (!differs || !differs.length) { return [t("cruns.replay.env.none")]; }
  return differs.map(function (d) {
    var obj = d && typeof d === "object" ? d : null;
    var k = String(obj ? (obj.key !== undefined ? obj.key : obj.field) : d);
    var cat = /^code\./.test(k) ? "code"
      : /^template_catalog/.test(k) ? "catalog"
      : /engine_versions/.test(k) ? "engines"
      : /solver/.test(k) ? "solver"
      : /^certifier/.test(k) ? "certifier"
      : /^registry/.test(k) ? "registry"
      : /data_root/.test(k) ? "data_root"
      : /platform/.test(k) ? "platform"
      : /python/.test(k) ? "python"
      : /class|reproducib/.test(k) ? "class" : null;
    var words = (cat ? t("cruns.replay.env." + cat) : k) + " (" + k + ")";
    if (obj && (obj.source !== undefined || obj.replay !== undefined)) {
      words += ": " + fmt(obj.source) + " → " + fmt(obj.replay);
    }
    return words;
  });
}

/* ------------------------------------------------------------- помощники */

function _crunsTaskLabel(task) {
  if (task === "pairing" || task === "rostering" ||
      task === "rostering_on_pairing") { return t("cruns.task." + task); }
  return task ? String(task) : "—";
}

function _crunsSrcWords(src) {
  if (src === "channel" || src === "upload" || src === "file" ||
      src === "adoption") { return t("cruns.pkg.src." + src); }
  return src ? String(src) : "—";
}

/* «дд.мм ЧЧ:ММ» в строках карточки, «дд.мм.гггг ЧЧ:ММ» в часах (образец
 * fmtCreated списка CDPM); непонятная дата — как есть. */
function _crunsFmtDate(iso, withYear) {
  if (!iso) { return "—"; }
  var ms = Date.parse(iso);
  if (isNaN(ms)) { return String(iso); }
  var d = new Date(ms);
  function two(n) { return (n < 10 ? "0" : "") + n; }
  return two(d.getDate()) + "." + two(d.getMonth() + 1) +
    (withYear ? "." + d.getFullYear() : "") + " " +
    two(d.getHours()) + ":" + two(d.getMinutes());
}

/* Слова ошибки ответа: без связи — сообщение; detail сервера — как есть;
 * иное тело — код HTTP и начало текста (ручка, которой ещё нет, отвечает
 * 404 страницей — это тоже показывается, а не прячется). */
function _crunsErrWords(err) {
  if (!err) { return ""; }
  if (err.status === 0) { return String(err.message || err); }
  var b = err.body;
  if (b && typeof b === "object") {
    if (typeof b.detail === "string") { return b.detail; }
    if (b.detail !== undefined) { return JSON.stringify(b.detail); }
  }
  if (typeof b === "string" && b) {
    return "HTTP " + err.status + " — " + b.replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ").trim().slice(0, 160);
  }
  return String(err.message || err);
}

function _crunsErrCode(err) {
  var b = err && err.body;
  if (b && typeof b === "object" && b.reason_code) {
    return String(b.reason_code);
  }
  var d = b && typeof b === "object" && typeof b.detail === "string"
    ? b.detail : "";
  var m = /^(storage-off|env-package|current-busy)\b/.exec(d);
  return m ? m[1] : null;
}

function _crunsErrToast(err, prefixKey) {
  var code = _crunsErrCode(err);
  var key = code ? "cruns.err." + code : null;
  // storage-off по причине пустого CREW_COSMOS_RUNS_DIR (cause сервера) —
  // своя строка: слова про CREW_TASK_INBOX здесь лгали бы о причине
  if (code === "storage-off" && err && err.body && typeof err.body === "object" &&
      err.body.cause === "runs-dir-empty") {
    key = "cruns.err.storage-off.runs-dir";
  }
  var words = key && t(key) !== key ? t(key) : _crunsErrWords(err);
  // ошибка ввода-вывода хранилища: слова площадки плюс текст ОС от сервера
  // (какой файл, какая ошибка) — без них отказ был бы общим местом
  if (code === "storage-io" && _crunsErrWords(err)) {
    words += " — " + _crunsErrWords(err);
  }
  toast((prefixKey ? t(prefixKey) + ": " : "") + words, "error");
}

function _crunsLoadSort() {
  try {
    var raw = localStorage.getItem(_CRUNS_SORT_KEY);
    if (raw) {
      var got = JSON.parse(raw);
      if (got && CRUNS_SORT_KEYS.indexOf(got.key) >= 0) {
        return { key: got.key, dir: got.dir === "desc" ? "desc" : "asc" };
      }
    }
  } catch (e) { /* хранилище недоступно — умолчание */ }
  return { key: "order", dir: "asc" };
}

function _crunsSaveSort() {
  try {
    localStorage.setItem(_CRUNS_SORT_KEY, JSON.stringify(_cruns.sort));
  } catch (e) { /* хранилище недоступно — порядок не запоминается */ }
}

function _crunsRunBusy(r) {
  return !!(r && (r.busy || r.state_kind === "working"));
}

function _crunsById(run) {
  var list = _cruns.list || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].run === run) { return list[i]; }
  }
  return null;
}

function _crunsLink(href, text, title) {
  var a = el("a", { class: "chip chip-click cruns-link", href: href,
                    target: "_blank", rel: "noopener", text: text });
  if (title) { a.title = title; }
  return a;
}

/* ------------------------------------------------------- чтение списка */

/** GET /api/cosmos-runs → состояние экрана; ошибка запоминается и
 *  показывается словами в строке хранилища. Никогда не отклоняется. */
function refreshCosmosRuns() {
  if (!_cruns.sort) { _cruns.sort = _crunsLoadSort(); }
  return api("/cosmos-runs").then(function (data) {
    _cruns.error = null;
    _cruns.list = (data && data.runs) || [];
    _cruns.current = data ? (data.current || null) : null;
    _cruns.storage = (data && data.storage) || null;
    _cruns.envPackage = !!(data && data.env_package);
    _cruns.inboxDisabled = !!(data && data.inbox_disabled);
    // отметки на исчезнувших прогонах не держим
    var ids = {};
    _cruns.list.forEach(function (r) { ids[r.run] = true; });
    Object.keys(_cruns.marked).forEach(function (k) {
      if (!ids[k]) { delete _cruns.marked[k]; }
    });
    renderCosmosRuns();
    _crunsPollAdjust();
    return data;
  }).catch(function (err) {
    _cruns.error = err;
    if (_cruns.list === null) { _cruns.list = []; }
    renderCosmosRuns();
    return null;
  });
}

function startCosmosRunsPolling() {
  if (_cruns.timer !== null) { return; }
  _cruns.timer = setInterval(function () {
    if (_nav.tab !== "cruns") { return; }
    var anyBusy = (_cruns.list || []).some(_crunsRunBusy);
    if (anyBusy) { refreshCosmosRuns(); }
  }, _CRUNS_POLL_MS);
}

function stopCosmosRunsPolling() {
  if (_cruns.timer !== null) {
    clearInterval(_cruns.timer);
    _cruns.timer = null;
  }
}

function _crunsPollAdjust() {
  if (_nav.tab === "cruns") { startCosmosRunsPolling(); }
}

/* --------------------------------------------------------- рисование */

function _crunsOrdered() {
  var all = (_cruns.list || []).slice();
  var sort = _cruns.sort || _crunsLoadSort();
  all.sort(function (a, b) { return cosmosRunsSortCompare(a, b, sort); });
  return all;
}

function _crunsCanReorder() {
  var s = _cruns.sort || _crunsLoadSort();
  return s.key === "order" && s.dir === "asc" && _cruns.bucket === "all" &&
    String(_cruns.query || "").trim() === "";
}

/* ------------------------------- подраздел «Все прогоны» обеих систем
 * Слово владельца 11.09.2026. Строки — записи, которые знает COSMOS
 * (индекс дерева), и прогоны хранителя-панели CDPM, которых в индексе
 * ещё нет (`GET /api/runs-all`). ТОЛЬКО ЧТЕНИЕ: удаления и правок здесь
 * нет — архивирование остаётся у каждой системы в своём списке. Отбор
 * тот же, что у дерева, и идёт на сервере. */

/* Узлы подраздела «Прогоны COSMOS»: их показ переключается вместе. */
var CRUNS_LIST_NODES = ["cruns-storage", "cruns-new-form", "cruns-toolbar",
                        "cruns-list", "cruns-footer"];
var CRUNS_VIEWS = ["runs", "all"];
var _CRUNS_VIEW_KEY = "cosmos.cruns.view.v1";

var _runsAll = {
  doc: null,          // последний ответ ручки
  error: null,        // ошибка чтения (честно, словами)
  filters: null,      // отбор (общий с деревом, хранится в браузере)
  limit: 0,           // сколько строк просим («показать ещё»)
  busy: false, wired: false
};

function _crunsViewLoad() {
  try {
    var got = localStorage.getItem(_CRUNS_VIEW_KEY);
    if (CRUNS_VIEWS.indexOf(got) >= 0) { return got; }
  } catch (e) { /* хранилище недоступно — умолчание */ }
  return "runs";
}

function _crunsViewSave(view) {
  try { localStorage.setItem(_CRUNS_VIEW_KEY, view); }
  catch (e) { /* хранилище недоступно — подраздел не запоминается */ }
}

/** Показать подраздел: список прогонов COSMOS либо общий экран. */
function crunsShowView(view) {
  view = CRUNS_VIEWS.indexOf(view) >= 0 ? view : "runs";
  _cruns.view = view;
  _crunsViewSave(view);
  CRUNS_LIST_NODES.forEach(function (id) {
    var n = $(id);
    if (n) { n.classList.toggle("cruns-off", view !== "runs"); }
  });
  var box = $("runs-all");
  if (box) { box.classList.toggle("hidden", view !== "all"); }
  [["btn-cruns-view-runs", "runs"], ["btn-cruns-view-all", "all"]]
    .forEach(function (pair) {
      var b = $(pair[0]);
      if (!b) { return; }
      b.classList.toggle("active", view === pair[1]);
      b.setAttribute("aria-pressed", view === pair[1] ? "true" : "false");
    });
  if (view === "all") { refreshRunsAll(); } else { renderCosmosRuns(); }
}

/** GET /api/runs-all → состояние общего экрана. Никогда не отклоняется:
 *  ошибка запоминается и показывается словами. */
function refreshRunsAll(more) {
  if (_runsAll.busy) { return Promise.resolve(null); }
  if (!_runsAll.filters) { _runsAll.filters = runFiltersLoad(); }
  if (!more) { _runsAll.limit = 0; }
  _runsAll.busy = true;
  var extra = _runsAll.limit ? { limit: String(_runsAll.limit) } : {};
  return api("/runs-all" + runFiltersQuery(_runsAll.filters, extra))
    .then(function (doc) {
      _runsAll.error = null;
      _runsAll.doc = doc || null;
      return doc;
    }).catch(function (e) {
      _runsAll.error = e;
      return null;
    }).then(function (doc) {
      _runsAll.busy = false;
      renderRunsAll();
      return doc;
    });
}

/** Ячейка со ссылкой на карточку прогона COSMOS: номера нет — прочерк,
 *  а не кнопка, которой некуда вести. */
function _runsAllCosmosCell(item) {
  if (!item.cosmos_run) { return el("td", { text: "—" }); }
  var b = el("button", { class: "chip chip-click", type: "button",
                         text: item.cosmos_run,
                         title: t("cruns.all.link.cosmos") });
  b.addEventListener("click", function () {
    openCosmosRunCard(item.cosmos_run);
  });
  return el("td", {}, [b]);
}

/** Ячейка с прогоном панели: со ссылкой, когда адрес панели объявлен. */
function _runsAllCdpmCell(item) {
  if (!item.cdpm_run) { return el("td", { text: "—" }); }
  if (!item.cdpm_url) {
    return el("td", { class: "mono", text: item.cdpm_run });
  }
  return el("td", {}, [_crunsLink(item.cdpm_url, item.cdpm_run,
                                  t("cruns.all.link.cdpm"))]);
}

function _runsAllSystems(item) {
  var list = (item.systems || []).map(function (s) {
    return _runRecKeyWords("flow.vmp.record.sys.", s);
  });
  return list.length ? list.join(" · ") : "—";
}

function _runsAllRow(item) {
  var tr = el("tr", { class: "runs-all-row runs-all-" + (item.source || "") });
  tr.appendChild(el("td", { class: "mono", text: item.record_id || "—",
    title: item.source === "cdpm" ? t("cruns.all.src.cdpm") : "" }));
  tr.appendChild(_runsAllCdpmCell(item));
  tr.appendChild(_runsAllCosmosCell(item));
  tr.appendChild(el("td", { text: item.customer || "—" }));
  tr.appendChild(el("td", {
    text: _runRecKeyWords("flow.vmp.record.contour.", item.contour) }));
  tr.appendChild(el("td", { text: _runFilterByWords(item.declared_by),
                            title: item.created_by || "" }));
  tr.appendChild(el("td", { text: fmtTime(item.created_at) }));
  tr.appendChild(el("td", { text: _runRecStateWords(item.state) }));
  tr.appendChild(el("td", { text: _runsAllSystems(item) }));
  // Признак образца ручка отдаёт признаком, а не словарём: у строки
  // панели его нет вовсе, и «нет» здесь честнее выдуманного «нет».
  tr.appendChild(el("td", { text: item.golden ? t("app.yes") : "—" }));
  return tr;
}

function renderRunsAll() {
  var box = $("runs-all");
  if (!box) { return; }
  box.textContent = "";
  box.appendChild(el("h3", { text: t("cruns.all.h") }));
  box.appendChild(el("p", { class: "hint", text: t("cruns.all.sub") }));
  if (!_runsAll.filters) { _runsAll.filters = runFiltersLoad(); }
  runFiltersBar(box, _runsAll.filters, function () { refreshRunsAll(); });
  if (_runsAll.error) {
    box.appendChild(el("p", { class: "vmp-note run-tree-bad",
      text: t("cruns.all.fail") + ": " + _crunsErrWords(_runsAll.error) }));
    return;
  }
  var doc = _runsAll.doc;
  if (!doc) {
    box.appendChild(el("p", { class: "muted", text: t("cruns.all.loading") }));
    return;
  }
  // Слова сервера — как есть: недоступная панель называется словами, а
  // не молчанием (иначе человек решил бы, что прогонов панели нет).
  (doc.words || []).forEach(function (w) {
    box.appendChild(el("p", { class: "vmp-note run-tree-warn",
                              text: String(w) }));
  });
  var items = doc.items || [];
  if (!items.length) {
    box.appendChild(el("p", { class: "vmp-note",
      text: t(runFiltersCount(_runsAll.filters) ? "cruns.all.none"
                                                : "cruns.all.empty") }));
    return;
  }
  var table = el("table", { class: "data-table runs-all-table" });
  var head = el("tr", {});
  [t("flow.vmp.record.run_id"), t("cruns.all.a.cdpm"),
   t("cruns.all.a.cosmos"), t("flow.runs.f.customer"),
   t("flow.vmp.record.contour"), t("flow.vmp.record.by"),
   t("flow.runs.f.created"), t("flow.vmp.record.state"),
   t("cruns.all.a.systems"), t("flow.vmp.tree.s.golden")]
    .forEach(function (w) { head.appendChild(el("th", { text: w })); });
  table.appendChild(el("thead", {}, [head]));
  var body = el("tbody", {});
  items.forEach(function (i) { body.appendChild(_runsAllRow(i)); });
  table.appendChild(body);
  box.appendChild(el("div", { class: "table-wrap" }, [table]));
  var total = doc.total || items.length;
  box.appendChild(el("p", { class: "hint", text: tf("cruns.all.counts", {
    shown: String(items.length), total: String(total) }) }));
  if (items.length < total) {
    var more = el("button", { class: "rv-seg runs-all-more", type: "button",
                              text: t("cruns.all.more") });
    more.addEventListener("click", function () {
      more.disabled = true;
      _runsAll.limit = items.length + (doc.limit || items.length);
      refreshRunsAll(true);
    });
    box.appendChild(more);
  }
}

function renderCosmosRuns() {
  var listBox = $("cruns-list");
  if (!listBox) { return; }
  if (!_cruns.sort) { _cruns.sort = _crunsLoadSort(); }
  _crunsRenderStorage();
  _crunsRenderToolbar();
  listBox.textContent = "";
  var list = _cruns.list;
  if (list === null) {
    listBox.appendChild(el("p", { class: "muted", text: t("html.loading") }));
    return;
  }
  var ordered = _crunsOrdered();
  if (!ordered.length) {
    if (!_cruns.error) {
      var box = el("div", { class: "cruns-empty" });
      box.appendChild(el("p", { text: t("cruns.empty") }));
      var b = el("button", { class: "btn btn-primary", type: "button",
                             id: "cruns-empty-btn", text: t("cruns.empty.btn") });
      b.addEventListener("click", function () { _crunsNewForm(true); });
      box.appendChild(b);
      listBox.appendChild(box);
    }
    return;
  }
  var shown = cosmosRunsFilter(ordered, _cruns.query, _cruns.bucket);
  if (!shown.length) {
    listBox.appendChild(el("div", { class: "cruns-empty" }, [
      el("p", { text: t("cruns.filtered") }),
      el("p", { class: "muted", text: t("cruns.filtered.hint") })]));
    return;
  }
  var canReorder = _crunsCanReorder();
  shown.forEach(function (r) {
    listBox.appendChild(_crunsCardNode(r, ordered, canReorder));
  });
  var foot = $("cruns-footer");
  if (foot) { foot.classList.remove("hidden"); }
}

/* Строка хранилища: ошибка ответа словами; инбокс выключен; пакет файлом;
 * хранилище не заведено — счётчики легаси и кнопка «Оформить прогоны». */
function _crunsRenderStorage() {
  var box = $("cruns-storage");
  if (!box) { return; }
  box.textContent = "";
  box.className = "cruns-storage";
  var st = _cruns.storage || {};
  if (_cruns.error) {
    box.classList.add("bad");
    box.appendChild(el("span", { text: t("cruns.load.fail") + ": " +
                                       _crunsErrWords(_cruns.error) }));
    return;
  }
  if (_cruns.list === null) { box.classList.add("hidden"); return; }
  if (st.state === "off" || _cruns.inboxDisabled) {
    box.classList.add("warn");
    // причина выключения — по cause сервера: инбокс (CREW_TASK_INBOX) либо
    // пустой CREW_COSMOS_RUNS_DIR при живом инбоксе
    box.appendChild(el("span", { text: t(st.cause === "runs-dir-empty"
      ? "cruns.runs_dir_off" : "cruns.inbox_off") }));
    return;
  }
  if (_cruns.envPackage) {
    box.classList.add("warn");
    box.appendChild(el("span", { text: t("cruns.env") }));
    return;
  }
  if (st.state === "uninitialized") {
    var lg = st.legacy || {};
    box.appendChild(el("span", { text: tf("cruns.storage.uninit", {
      n: lg.packages !== undefined && lg.packages !== null ? lg.packages : "—",
      sha: lg.current_sha12 || "—" }) }));
    var b = el("button", { class: "btn btn-primary btn-small", type: "button",
                           id: "btn-cruns-init", text: t("cruns.storage.init") });
    b.addEventListener("click", _crunsInitStorage);
    box.appendChild(b);
    return;
  }
  box.classList.add("hidden");
}

function _crunsRenderToolbar() {
  var bar = $("cruns-toolbar");
  if (!bar) { return; }
  var list = _cruns.list || [];
  bar.classList.toggle("hidden", !list.length);
  var search = $("cruns-search");
  if (search && search.value !== _cruns.query) { search.value = _cruns.query; }
  // пилюли-фильтры со счётчиками
  var filters = $("cruns-filters");
  if (filters) {
    filters.textContent = "";
    CRUNS_BUCKETS.forEach(function (b) {
      var count = b === "all" ? list.length : list.filter(function (r) {
        return cosmosRunsBucket(r.state_kind) === b;
      }).length;
      var btn = el("button", { class: "cruns-filter" + (b === _cruns.bucket ? " active" : ""),
                               type: "button",
                               text: tf("cruns.filter." + b, { n: count }) });
      btn.setAttribute("aria-pressed", b === _cruns.bucket ? "true" : "false");
      btn.dataset.bucket = b;
      btn.addEventListener("click", function () {
        _cruns.bucket = b;
        renderCosmosRuns();
      });
      filters.appendChild(btn);
    });
  }
  var s = _cruns.sort || _crunsLoadSort();
  var sortBtn = $("btn-cruns-sort");
  if (sortBtn) {
    sortBtn.textContent = "⇅ " + t("cruns.sort." + s.key);
    sortBtn.title = t("cruns.sort.title");
    sortBtn.setAttribute("aria-label", t("cruns.sort." + s.key));
  }
  var dirBtn = $("btn-cruns-dir");
  if (dirBtn) {
    dirBtn.textContent = (s.dir === "asc" ? "↑ " : "↓ ") + t("cruns.dir." + s.dir);
    dirBtn.title = t("cruns.dir.title." + s.dir);
  }
  var markBtn = $("btn-cruns-mark");
  if (markBtn) {
    markBtn.textContent = _cruns.markMode ? t("cruns.mark.off") : t("cruns.mark");
    markBtn.title = _cruns.markMode ? t("cruns.mark.off.title") : t("cruns.mark.title");
    markBtn.setAttribute("aria-pressed", _cruns.markMode ? "true" : "false");
  }
  var allBtn = $("btn-cruns-mark-all"), noneBtn = $("btn-cruns-mark-none"),
      delBtn = $("btn-cruns-delete");
  [allBtn, noneBtn, delBtn].forEach(function (b) {
    if (b) { b.classList.toggle("hidden", !_cruns.markMode); }
  });
  if (allBtn) { allBtn.textContent = t("cruns.mark.all"); }
  if (noneBtn) { noneBtn.textContent = t("cruns.mark.none"); }
  if (delBtn) {
    var ids = Object.keys(_cruns.marked).sort();
    var busyIds = ids.filter(function (id) { return _crunsRunBusy(_crunsById(id)); });
    var curIds = ids.filter(function (id) { return id === _cruns.current; });
    delBtn.textContent = tf("cruns.mark.delete", { n: ids.length });
    var reason = null;
    if (!ids.length) { reason = t("cruns.mark.delete.title.none"); }
    else if (busyIds.length) {
      reason = tf("cruns.mark.delete.title.busy", { runs: busyIds.join(", ") });
    } else if (curIds.length) {
      reason = tf("cruns.mark.delete.title.current", { run: curIds[0] });
    }
    delBtn.disabled = !!reason || _cruns.busy;
    delBtn.title = reason || t("cruns.mark.delete.title.ok");
  }
}

function _crunsPkgLine(r) {
  var p = r.package;
  if (!p) { return t("cruns.pkg.none"); }
  var parts = [];
  if (p.source_run) { parts.push(String(p.source_run)); }
  if (p.sha12 || p.sha) { parts.push(p.sha12 || String(p.sha).slice(0, 12)); }
  if (p.spec_version) { parts.push(String(p.spec_version)); }
  parts.push(_crunsSrcWords(p.source));
  if (p.received_at) { parts.push(_crunsFmtDate(p.received_at, false)); }
  var l = p.launch;
  if (l) {
    parts.push(t("cruns.pkg.launch") + ": " +
      t("cruns.task.pairing") + " " + (l.pairing ? "✓" : "✗") + " · " +
      t("cruns.task.rostering") + " " + (l.rostering ? "✓" : "✗") +
      (l.as_of === "received" ? " (" + t("cruns.pkg.as_received") + ")" : ""));
  }
  var head = r.state_kind === "lost" ? t("cruns.pkg.lost") : t("cruns.pkg");
  return head + ": " + parts.join(" · ");
}

function _crunsAttLine(r) {
  var a = r.attempts;
  if (!a || !a.total) { return t("cruns.attempts.none"); }
  var parts = [t("cruns.task.pairing") + " " + (a.pairing || 0),
               t("cruns.task.rostering") + " " + (a.rostering || 0)];
  if (a.running) { parts.push(t("cruns.attempts.running") + " " + a.running); }
  if (a.last) {
    var l = a.last;
    var what = l.error || l.verdict || l.progress || "";
    var lab = t("cruns.attempts.last") + ": " + _crunsTaskLabel(l.task) +
      (what ? " — " + what : "");
    if (l.replay_of) {
      var mode = l.replay_of.mode === "fresh" ? "fresh" : "journal";
      lab += " (" + tf("cruns.replay.of." + mode, { id: l.replay_of.source_attempt || "—" }) +
        (l.replay_of.verdict
          ? " · " + cosmosReplayVerdictLabel({ verdict: l.replay_of.verdict }).text : "") + ")";
    }
    parts.push(lab);
  }
  return t("cruns.attempts") + ": " + parts.join(" · ");
}

function _crunsMark(v) {
  return v === "ok" ? "✓" : v === "bad" ? "✗" : v === "warn" ? "!" : "—";
}

function _crunsChecksLine(r) {
  var c = r.checks || {};
  var v = c.vmp || {}, i = c.idak || {};
  return t("cruns.checks") + ": " + t("cruns.att.vmp") + " " +
    t("cruns.task.pairing") + " " + _crunsMark(v.pairing) + " · " +
    t("cruns.task.rostering") + " " + _crunsMark(v.rostering) + " · " +
    t("cruns.att.idak") + " " + t("cruns.task.pairing") + " " + _crunsMark(i.pairing) +
    " · " + t("cruns.task.rostering") + " " + _crunsMark(i.rostering);
}

function _crunsPassLine(r) {
  var ps = r.passport_summary;
  if (!ps) { return ""; }
  if (ps.state_code === "no-attempts") { return t("cruns.pass.none"); }
  if (ps.state_code === "pre-passport") { return t("cruns.pass.old"); }
  var parts = [];
  var m = ps.models || {};
  if (m.certifier) { parts.push(t("cruns.pass.certifier") + ": " + m.certifier); }
  if (m.source_check) { parts.push(t("cruns.pass.source_check") + ": " + m.source_check); }
  if (m.cdpm) { parts.push(t("cruns.pass.cdpm") + ": " + m.cdpm); }
  // решатели — слова по кодам сервера ({task, engine, version, version_of,
  // version_code}), на языке интерфейса; прежняя строковая форма — как есть
  (ps.solvers || []).forEach(function (s) {
    if (!s) { return; }
    if (typeof s === "string") { parts.push(s); return; }
    var words = _crunsTaskLabel(s.task) + ": " + fmt(s.engine);
    if (s.version) {
      words += " · " + (s.version_of ? s.version_of + " " : "") + s.version;
    }
    if (s.version_code) { words += " (" + cosmosNoteWords(s.version_code) + ")"; }
    parts.push(words);
  });
  if (ps.code) { parts.push(t("cruns.pass.code") + ": " + ps.code); }
  if (ps.catalog) { parts.push(t("cruns.pass.catalog") + ": " + ps.catalog); }
  return parts.join(" · ");
}

/* Карточка списка (раздел 11.3 проекта). */
function _crunsCardNode(r, ordered, canReorder) {
  var isCur = r.run === _cruns.current || r.current === true;
  var busy = _crunsRunBusy(r);
  var row = el("div", { class: "cruns-row" });
  if (_cruns.markMode) {
    var chk = el("input", { type: "checkbox", class: "cruns-check",
                            "aria-label": tf("cruns.mark.one", { run: r.run }) });
    chk.checked = !!_cruns.marked[r.run];
    if (busy) { chk.title = t("cruns.busy"); }
    chk.addEventListener("change", function () {
      if (chk.checked) { _cruns.marked[r.run] = true; }
      else { delete _cruns.marked[r.run]; }
      _crunsRenderToolbar();
      card.classList.toggle("marked", chk.checked);
    });
    row.appendChild(el("label", { class: "cruns-check-cell" }, [chk]));
  }
  var card = el("div", { class: "cruns-card" + (isCur ? " current" : "") +
                                (_cruns.marked[r.run] ? " marked" : "") });
  card.dataset.run = r.run;
  var head = el("div", { class: "cruns-card-head" });
  var title = el("span", { class: "cruns-title", text: r.title || r.run,
                           title: r.title ? r.title + " (" + r.run + ")" : r.run });
  title.addEventListener("click", function () { openCosmosRunCard(r.run); });
  keyActivate(title, function () { openCosmosRunCard(r.run); });
  head.appendChild(title);
  if (r.title) { head.appendChild(el("span", { class: "cruns-run mono", text: r.run })); }
  if (isCur) {
    head.appendChild(el("span", { class: "chip chip-ok small", text: t("cruns.current") }));
  }
  if (r.error_code) {
    // нечитаемый run.json: список не падает, запись названа кодом и
    // словами сервера в title
    head.appendChild(el("span", { class: "chip chip-bad small",
                                  text: cosmosNoteWords(r.error_code),
                                  title: r.error ? String(r.error) : "" }));
  } else {
    var bucket = cosmosRunsBucket(r.state_kind) || "none";
    head.appendChild(el("span", {
      class: "cruns-pill " + bucket + (r.state_kind === "stalled" ? " stalled" : ""),
      text: cosmosRunsStateLabel(r.state_kind), title: r.state || "" }));
  }
  if (r.busy) {
    head.appendChild(el("span", { class: "chip chip-running small",
      text: _crunsTaskLabel(r.busy.task) + (r.busy.progress ? " — " + r.busy.progress : "") +
            (r.busy.sha12 ? " · " + r.busy.sha12 : "") }));
  }
  card.appendChild(head);
  card.appendChild(el("div", { class: "cruns-line", text: _crunsPkgLine(r) }));
  card.appendChild(el("div", { class: "cruns-line", text: _crunsAttLine(r) }));
  if (r.checks) {
    card.appendChild(el("div", { class: "cruns-line", text: _crunsChecksLine(r) }));
  }
  var pass = _crunsPassLine(r);
  if (pass) {
    card.appendChild(el("div", { class: "cruns-line cruns-pass", text: pass,
                                 title: t("cruns.pass.title") }));
  }
  var foot = el("div", { class: "cruns-card-foot" });
  foot.appendChild(el("span", { class: "cruns-clock muted",
                                text: _crunsFmtDate(r.created_at, true),
                                title: t("cruns.card.created") + " " + fmt(r.created_at) }));
  var actions = el("span", { class: "cruns-actions" });
  var mainBtn = el("button", { class: "btn btn-small " + (isCur ? "btn-primary" : "btn-ghost"),
                               type: "button",
                               text: isCur ? t("cruns.open") : t("cruns.setCurrent") });
  mainBtn.addEventListener("click", function () {
    if (isCur) { showTab("flow"); return; }
    _crunsSetCurrent(r.run);
  });
  actions.appendChild(mainBtn);
  var cardBtn = el("button", { class: "btn btn-ghost btn-small", type: "button",
                               text: t("cruns.card") });
  cardBtn.addEventListener("click", function () { openCosmosRunCard(r.run); });
  actions.appendChild(cardBtn);
  foot.appendChild(actions);
  card.appendChild(foot);
  row.appendChild(card);
  if (canReorder) {
    var idx = -1;
    for (var i = 0; i < ordered.length; i++) {
      if (ordered[i].run === r.run) { idx = i; break; }
    }
    var arrows = el("div", { class: "cruns-arrows" });
    var up = el("button", { class: "btn btn-ghost btn-small", type: "button", text: "↑",
                            "aria-label": tf("cruns.up", { run: r.run }),
                            title: tf("cruns.up", { run: r.run }) });
    up.disabled = _cruns.busy || idx <= 0;
    up.addEventListener("click", function () { _crunsMove(r.run, -1); });
    var down = el("button", { class: "btn btn-ghost btn-small", type: "button", text: "↓",
                              "aria-label": tf("cruns.down", { run: r.run }),
                              title: tf("cruns.down", { run: r.run }) });
    down.disabled = _cruns.busy || idx < 0 || idx >= ordered.length - 1;
    down.addEventListener("click", function () { _crunsMove(r.run, 1); });
    arrows.appendChild(up);
    arrows.appendChild(down);
    row.appendChild(arrows);
  }
  return row;
}

/* ------------------------------------------------------------ действия */

/* Перестановка соседей в ручном порядке → PUT /api/cosmos-runs/order всем
 * порядком; сосед мог быть скрыт фильтром — поэтому стрелки только без
 * фильтра и поиска (_crunsCanReorder). */
function _crunsMove(run, delta) {
  var ids = _crunsOrdered().map(function (r) { return r.run; });
  var i = ids.indexOf(run), j = i + delta;
  if (i < 0 || j < 0 || j >= ids.length) { return; }
  var next = ids.slice();
  next[i] = ids[j];
  next[j] = ids[i];
  _cruns.busy = true;
  renderCosmosRuns();
  api("/cosmos-runs/order", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order: next })
  }).then(function () {
    return refreshCosmosRuns();
  }).catch(function (e) {
    _crunsErrToast(e);
  }).finally(function () {
    _cruns.busy = false;
    renderCosmosRuns();
  });
}

function _crunsMarkShown() {
  var shown = cosmosRunsFilter(_crunsOrdered(), _cruns.query, _cruns.bucket);
  shown.forEach(function (r) { _cruns.marked[r.run] = true; });
  renderCosmosRuns();
}

function _crunsMarkNone() {
  _cruns.marked = {};
  renderCosmosRuns();
}

/* Удаление = перенос папок прогонов в архив площадки (fail-closed на
 * сервере). Перед архивом список перечитывается: занятость и текущий могли
 * измениться с момента открытия экрана. */
function _crunsDelete() {
  var ids = Object.keys(_cruns.marked).sort();
  if (!ids.length) { return; }
  appConfirm(tf("cruns.dlg.title", { n: ids.length }), {
    ok: t("cruns.dlg.ok"), cancel: t("cruns.dlg.cancel"), danger: true,
    details: [tf("cruns.dlg.msg", { runs: ids.join(", ") })]
  }).then(function (yes) {
    if (!yes) { return; }
    _cruns.busy = true;
    renderCosmosRuns();
    return refreshCosmosRuns().then(function (data) {
      if (!data) {
        toast(t("cruns.load.fail") + ": " + _crunsErrWords(_cruns.error), "error");
        return;
      }
      var nowBusy = ids.filter(function (id) { return _crunsRunBusy(_crunsById(id)); });
      if (nowBusy.length) {
        toast(tf("cruns.mark.delete.title.busy", { runs: nowBusy.join(", ") }), "error");
        return;
      }
      var cur = ids.filter(function (id) { return id === _cruns.current; });
      if (cur.length) {
        toast(tf("cruns.mark.delete.title.current", { run: cur[0] }), "error");
        return;
      }
      return api("/cosmos-runs/archive", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ runs: ids })
      }).then(function (got) {
        var arch = (got && got.archived) || [];
        toast(tf("cruns.archived", { n: arch.length, runs: arch.join(", "),
                                     left: got && got.left !== undefined ? got.left : "—" }), "ok");
        _cruns.marked = {};
        _cruns.markMode = false;
        return refreshCosmosRuns();
      });
    }).catch(function (e) {
      _crunsErrToast(e);
    }).finally(function () {
      _cruns.busy = false;
      renderCosmosRuns();
    });
  });
}

/* Форма нового прогона: поле названия + «Создать». */
function _crunsNewForm(show) {
  var form = $("cruns-new-form");
  if (!form) { return; }
  form.classList.toggle("hidden", !show);
  if (show) {
    var inp = $("cruns-new-title");
    if (inp) { inp.value = ""; inp.focus(); }
  }
}

/* POST /api/cosmos-runs. opts.toFlow — после успеха перейти на «Конвейер».
 * 409 current-busy (в текущем прогоне идёт нажатие) — предложение создать
 * без переключения; прочие отказы — словами сервера. */
function _crunsCreate(title, makeCurrent, opts) {
  opts = opts || {};
  var body = { make_current: !!makeCurrent };
  if (title) { body.title = title; }
  return api("/cosmos-runs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (doc) {
    var run = (doc && doc.run) || "—";
    toast(tf(doc && doc.current ? "cruns.created" : "cruns.created.keep", { run: run }), "ok");
    _crunsNewForm(false);
    refreshCosmosRuns();
    _flowRunsAt = 0;
    _flowBusyUntil = 0;
    if (doc && doc.current && opts.toFlow !== false) {
      showTab("flow");
    } else if (_nav.tab === "flow") {
      refreshFlow();
    }
    return doc;
  }).catch(function (e) {
    if (makeCurrent && _crunsErrCode(e) === "current-busy") {
      return appConfirm(t("cruns.err.current-busy"), { ok: t("cruns.create.keep") })
        .then(function (yes) {
          if (yes) { return _crunsCreate(title, false, { toFlow: false }); }
          return null;
        });
    }
    _crunsErrToast(e);
    return null;
  });
}

/* PUT /api/cosmos-runs/current → схема конвейера переключается сразу. */
function _crunsSetCurrent(run) {
  _flowSrcEpoch++;
  return api("/cosmos-runs/current", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run: run })
  }).then(function (res) {
    toast(tf("cruns.setCurrent.done", { run: run }), "ok");
    _flowRunsAt = 0;
    _flowBusyUntil = 0;
    if (_nav.tab === "flow") { refreshFlow(); }
    refreshCosmosRuns();
    return res;
  }).catch(function (e) {
    _crunsErrToast(e);
    throw e;
  });
}

/* POST /api/cosmos-runs/init — оформление принятых пакетов прогонами. */
function _crunsInitStorage() {
  var b = $("btn-cruns-init");
  if (b) { b.disabled = true; }
  api("/cosmos-runs/init", { method: "POST" }).then(function (res) {
    var ad = (res && res.adoption) || {};
    var n = ad.runs_created !== undefined ? ad.runs_created
      : ad.runs !== undefined ? ad.runs
      : ad.created !== undefined ? ad.created : "—";
    toast(tf("cruns.storage.done", { n: n }), "ok");
    _flowRunsAt = 0;
    return refreshCosmosRuns();
  }).catch(function (e) {
    _crunsErrToast(e);
  }).finally(function () {
    if (b) { b.disabled = false; }
  });
}

/* ------------------------------------------------------ карточка прогона */

function openCosmosRunCard(run) {
  var m = _flowModal("▤", tf("cruns.card.h", { run: run }), true);
  var host = el("div", { class: "cruns-cardview" });
  m.body.appendChild(host);
  host.appendChild(el("p", { class: "vmp-pkg-brief", text: t("html.loading") }));
  var ctx = { run: run, host: host, modal: m, doc: null, timer: null,
              editing: false, error: null };
  _cruns.card = ctx;
  _crunsCardLoad(ctx);
  ctx.timer = setInterval(function () {
    if (!document.body.contains(host)) {
      clearInterval(ctx.timer);
      ctx.timer = null;
      if (_cruns.card === ctx) { _cruns.card = null; }
      return;
    }
    if (ctx.editing) { return; }
    if (_crunsCardBusy(ctx.doc)) { _crunsCardLoad(ctx); }
  }, _CRUNS_CARD_POLL_MS);
}

function _crunsCardBusy(doc) {
  if (!doc) { return false; }
  if (doc.busy || doc.state_kind === "working") { return true; }
  return (doc.attempts || []).some(function (a) { return a && a.done === false; });
}

function _crunsCardLoad(ctx) {
  return api("/cosmos-runs/" + encodeURIComponent(ctx.run)).then(function (doc) {
    ctx.doc = doc;
    ctx.error = null;
    if (!ctx.editing) { _crunsCardRender(ctx); }
    else { _crunsCardErrorLine(ctx, null); }
    return doc;
  }).catch(function (e) {
    ctx.error = e;
    if (!ctx.doc) {
      ctx.host.textContent = "";
      ctx.host.appendChild(el("p", { class: "cruns-storage bad",
                                     text: t("cruns.load.fail") + ": " + _crunsErrWords(e) }));
    } else {
      // Опрос открытой карточки (раз в 5 с, пока прогон занят): ошибка —
      // одной строкой в карточке, обновляемой при повторе, а не тостом на
      // каждый цикл (ревью 07.09: дюжина одинаковых тостов за минуту).
      _crunsCardErrorLine(ctx, t("cruns.load.fail") + ": " + _crunsErrWords(e));
    }
    return null;
  });
}

/* Строка ошибки чтения над содержимым карточки: words — показать или
 * обновить, null — убрать (следующее чтение удалось). */
function _crunsCardErrorLine(ctx, words) {
  var line = ctx.host.querySelector(".cruns-card-error");
  if (!words) {
    if (line) { line.remove(); }
    return;
  }
  if (!line) {
    line = el("p", { class: "cruns-storage bad cruns-card-error" });
    ctx.host.insertBefore(line, ctx.host.firstChild);
  }
  line.textContent = words;
}

function _crunsCardRender(ctx) {
  var doc = ctx.doc || {};
  var host = ctx.host;
  host.textContent = "";
  host.appendChild(_crunsTitleRow(ctx));
  var meta = [];
  meta.push(t("cruns.card.created") + " " + _crunsFmtDate(doc.created_at, true));
  if (doc.origin) { meta.push(t("cruns.card.origin") + ": " + cosmosOriginWords(doc.origin)); }
  if (doc.state_kind) { meta.push(cosmosRunsStateLabel(doc.state_kind)); }
  if (doc.current) { meta.push(t("cruns.current")); }
  host.appendChild(el("p", { class: "vmp-pkg-brief", text: meta.join(" · ") }));
  var secP = el("div", { class: "vmp-sec cruns-sec" });
  secP.appendChild(el("h4", { text: t("cruns.card.pkgs") }));
  _crunsRenderPackages(ctx, secP);
  host.appendChild(secP);
  var secA = el("div", { class: "vmp-sec cruns-sec" });
  secA.appendChild(el("h4", { text: t("cruns.card.atts") }));
  _crunsRenderAttempts(ctx, secA);
  host.appendChild(secA);
  // Запись о прогоне — рядом с паспортом (проект «Запись о прогоне»,
  // раздел 11.4): паспорт говорит, ЧЕМ сделан прогон, запись — ЧТО в нём
  // объявлено, пройдено и чем кончилось.
  var secRec = el("div", { class: "vmp-sec cruns-sec cruns-record" });
  secRec.appendChild(el("h4", { text: t("cruns.record.h"),
                                title: t("cruns.record.title") }));
  _crunsRenderRecord(ctx, secRec);
  host.appendChild(secRec);
  // Дерево прогонов — рядом с записью (проект, раздел 12.3): запись
  // говорит, ЧТО объявлено и чем кончилось, дерево — где этот прогон
  // среди прогонов того же корня и чем он от них отличается.
  var secTree = el("div", { class: "vmp-sec cruns-sec cruns-tree" });
  secTree.appendChild(el("h4", { text: t("cruns.tree.h"),
                                 title: t("cruns.tree.title") }));
  _crunsRenderTree(ctx, secTree);
  host.appendChild(secTree);
  var secPass = el("div", { class: "vmp-sec cruns-sec cruns-passport" });
  secPass.appendChild(el("h4", { text: t("cruns.pass.h"), title: t("cruns.pass.title") }));
  _crunsRenderPassport(ctx, secPass);
  host.appendChild(secPass);
}

/* Запись о прогоне на карточке прогона: та же секция, что на панели
 * постановки. Ручка `/api/cosmos-runs/<прогон>/record` отдаёт тот же
 * ответ, что `/api/run/current` (проект, раздел 10, п. 3). Объявить
 * прогон отсюда нельзя — объявление идёт от ТЕКУЩЕГО пакета панели;
 * повтор сшивки — только у текущего прогона, потому что ручка
 * `/api/run/stitch` работает с текущим. */
function _crunsRenderRecord(ctx, host) {
  var body = el("div", { class: "run-rec-body" });
  host.appendChild(body);
  _crunsRecordLoad(ctx, body);
}

function _crunsRecordLoad(ctx, body) {
  body.textContent = "";
  body.appendChild(el("p", { class: "vmp-note", text: t("flow.vmp.record.loading") }));
  api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/record").then(function (doc) {
    runRecordRender(body, doc, {
      declare: false,
      stitch: !!(ctx.doc && ctx.doc.current),
      reload: function () { _crunsRecordLoad(ctx, body); } });
  }).catch(function (e) {
    // Ручки ещё нет / прогон без записи / обрыв — строкой в секции и
    // общей строкой ошибки карточки, а не тостом на каждый опрос.
    var words = t("cruns.record.fail") + ": " + _crunsErrWords(e);
    body.textContent = "";
    body.appendChild(el("p", { class: "vmp-note run-rec-bad", text: words }));
    _crunsCardErrorLine(ctx, words);
  });
}

/* Дерево прогонов на карточке прогона: та же секция, что на панели
 * постановки. Ручка `/api/cosmos-runs/<прогон>/tree` отдаёт тот же
 * ответ, что `/api/run/current/tree` (проект, раздел 12.2). */
function _crunsRenderTree(ctx, host) {
  var body = el("div", { class: "run-tree-body" });
  host.appendChild(body);
  // Тот же блок «Прогоны дерева» с отбором, что на панели постановки:
  // карточка показывает один прогон среди своих, блок — все ветви.
  ctx.treeIndex = runTreeIndexBlock(host);
  _crunsTreeLoad(ctx, body);
}

function _crunsTreeLoad(ctx, body) {
  body.textContent = "";
  body.appendChild(el("p", { class: "vmp-note",
                             text: t("flow.vmp.tree.loading") }));
  api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/tree").then(function (doc) {
    runTreeRender(body, doc, {
      reload: function () {
        _crunsTreeLoad(ctx, body);
        if (ctx.treeIndex) { ctx.treeIndex.reload(); }
      } });
  }).catch(function (e) {
    // Ручки ещё нет / прогона нет в дереве / обрыв — строкой в секции и
    // общей строкой ошибки карточки, а не тостом на каждый опрос.
    var words = t("cruns.tree.fail") + ": " + _crunsErrWords(e);
    body.textContent = "";
    body.appendChild(el("p", { class: "vmp-note run-tree-bad", text: words }));
    _crunsCardErrorLine(ctx, words);
  });
}

/* Название с карандашом: Enter — сохранить, Escape — отменить, blur —
 * сохранить; пустое возвращает номер (сервер снимает ключ). */
function _crunsTitleRow(ctx) {
  var doc = ctx.doc || {};
  var row = el("div", { class: "cruns-card-title" });
  var h = el("h3", { text: doc.title || tf("cruns.title.ph", { run: ctx.run }) });
  row.appendChild(h);
  var pen = el("button", { class: "btn btn-ghost btn-small", type: "button", text: "✎",
                           title: t("cruns.title.edit"),
                           "aria-label": t("cruns.title.edit") });
  row.appendChild(pen);
  pen.addEventListener("click", function () {
    var inp = el("input", { type: "text", class: "confirm-input cruns-title-input",
                            placeholder: tf("cruns.title.ph", { run: ctx.run }),
                            maxlength: "120" });
    inp.value = doc.title || "";
    var done = false;
    function finish(save) {
      if (done) { return; }
      done = true;
      ctx.editing = false;
      if (!save) { _crunsCardRender(ctx); return; }
      var val = inp.value.trim();
      api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/title", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: val })
      }).then(function (res) {
        doc.title = res && res.title ? res.title : null;
        toast(tf("cruns.title.saved", { run: ctx.run }), "ok");
        _crunsCardRender(ctx);
        refreshCosmosRuns();
        _flowRunsAt = 0;
      }).catch(function (e) {
        _crunsErrToast(e);
        _crunsCardRender(ctx);
      });
    }
    inp.addEventListener("keydown", function (e) {
      // Enter/Escape — дело поля, не окна: без stopPropagation Escape
      // всплывал до document и закрывал всю карточку (проект 11.4:
      // «Escape — отменить» переименование).
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); finish(true); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    inp.addEventListener("blur", function () { finish(true); });
    ctx.editing = true;
    row.textContent = "";
    row.appendChild(inp);
    inp.focus();
    inp.select();
  });
  return row;
}

function _crunsMadeByWords(mb) {
  if (!mb) { return t("cruns.pkg.madeby.none"); }
  var parts = [];
  if (mb.code) { parts.push(t("cruns.pkg.madeby.code") + " " + String(mb.code).slice(0, 7)); }
  if (mb.spec_version) { parts.push(String(mb.spec_version)); }
  (mb.models || []).forEach(function (m) {
    parts.push((m.role || "?") + ": " + (m.provider ? m.provider + "/" : "") +
      (m.model || "—") + (m.channel ? " · " + m.channel : ""));
  });
  return parts.length ? parts.join(" · ") : t("cruns.pkg.madeby.none");
}

function _crunsLaunchWords(mg, asReceived) {
  if (!mg) { return "—"; }
  var p = mg.pairing || {}, r = mg.rostering || {};
  return t("cruns.pkg.launch") + ": " + t("cruns.task.pairing") + " " +
    (p.launch_allowed ? "✓" : "✗") + " · " + t("cruns.task.rostering") + " " +
    (r.launch_allowed ? "✓" : "✗") +
    (asReceived ? " (" + t("cruns.pkg.as_received") + ")" : "");
}

function _crunsRenderPackages(ctx, host) {
  var doc = ctx.doc || {};
  var pkgs = doc.packages || [];
  if (!pkgs.length) {
    host.appendChild(el("p", { class: "vmp-note", text: t("cruns.pkg.none") }));
    return;
  }
  var wrap = el("div", { class: "table-wrap" });
  var table = el("table", { class: "data-table cruns-pkgs" });
  var thead = el("thead");
  var hr = el("tr");
  ["cruns.pkg.th.sha", "cruns.pkg.th.src_run", "cruns.pkg.th.spec",
   "cruns.pkg.th.source", "cruns.pkg.th.received", "cruns.pkg.th.verdict",
   "cruns.pkg.th.made_by", "cruns.pkg.th.current"].forEach(function (k) {
    hr.appendChild(el("th", { text: t(k) }));
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = el("tbody");
  pkgs.forEach(function (p) {
    var tr = el("tr");
    var sha = String(p.sha || "");
    var shaCell = el("td", { class: "mono", text: sha.slice(0, 12), title: sha });
    if (p.note_code) {
      shaCell.appendChild(el("span", { class: "chip chip-bad small",
                                       text: cosmosNoteWords(p.note_code) }));
    }
    tr.appendChild(shaCell);
    tr.appendChild(el("td", { text: fmt(p.source_run) }));
    tr.appendChild(el("td", { text: fmt(p.spec_version) +
      (p.revision !== undefined && p.revision !== null ? " · " + p.revision : "") +
      (p.customer ? " · " + p.customer : "") }));
    tr.appendChild(el("td", { text: _crunsSrcWords(p.source) }));
    var rec = _crunsFmtDate(p.received_at, false);
    if (p.last_received_at && p.last_received_at !== p.received_at) {
      rec += " → " + _crunsFmtDate(p.last_received_at, false);
    }
    if (p.receipts && p.receipts > 1) {
      rec += " · " + tf("cruns.pkg.receipts", { n: p.receipts });
    }
    tr.appendChild(el("td", { text: rec }));
    var isCurPkg = doc.current_package && p.sha === doc.current_package;
    // live_mg — flow.readiness_state(): {present, verdict, columns: {pairing:
    // {launch_allowed}, rostering: {…}}} (так же читает сервер в _launch_of);
    // mg приёма — {pairing: {launch_allowed}, …}: обе формы дают
    // <задача>.launch_allowed, у живой — под columns
    var cols = isCurPkg && p.live_mg && p.live_mg.columns;
    var mg = cols || p.mg;
    tr.appendChild(el("td", { text: fmt(p.verdict) + " · " +
      _crunsLaunchWords(mg, !cols) }));
    tr.appendChild(el("td", { class: "cruns-madeby", text: _crunsMadeByWords(p.made_by) }));
    tr.appendChild(el("td", { text: isCurPkg ? "✓" : "" }));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
}

function _crunsParamsWords(rec) {
  var p = rec.params;
  var parts = [];
  if (!p) {
    parts.push(cosmosNoteWords("pre-passport"));
  } else {
    if (p.spent_limit_s !== undefined && p.spent_limit_s !== null) {
      parts.push(tf("cruns.att.p.limit", { s: p.spent_limit_s }));
    }
    if (p.mode) { parts.push(tf("cruns.att.p.mode", { v: p.mode })); }
    if (p.objective_id) { parts.push(tf("cruns.att.p.objective", { v: p.objective_id })); }
    if (p.moves) { parts.push(tf("cruns.att.p.moves", { v: p.moves })); }
  }
  var rp = rec.reproducibility;
  if (rp && rp.class === "deterministic") { parts.push(t("cruns.att.d1")); }
  else if (rp && rp.class === "wall-clock") { parts.push(t("cruns.att.wallclock")); }
  else if (rp && rp.reason_code) { parts.push(cosmosNoteWords(rp.reason_code)); }
  return parts.join(" · ") || "—";
}

function _crunsEngineWords(rec) {
  var s = rec.solver || {};
  var eng = s.select_engine || s.engine || rec.engine ||
    (rec.task === "rostering" ? "timefold" : "");
  var parts = [];
  if (eng) { parts.push(String(eng)); }
  if (s.url) { parts.push(String(s.url)); }
  if (rec.engine_versions && typeof rec.engine_versions === "object") {
    Object.keys(rec.engine_versions).forEach(function (k) {
      if (rec.engine_versions[k]) { parts.push(k + " " + rec.engine_versions[k]); }
    });
  }
  return parts.join(" · ") || "—";
}

function _crunsVmpWords(rec) {
  var s = rec.summary;
  if (!s) { return "—"; }
  var parts = [t(s.certified ? "cruns.att.cert.ok" : "cruns.att.cert.no")];
  if (s.findings !== undefined && s.findings !== null) {
    parts.push(tf("cruns.att.findings", { n: s.findings }));
  }
  return parts.join(" · ");
}

function _crunsIdakWords(rec) {
  var sc = rec.source_check;
  if (!sc) { return t("cruns.att.idak.none"); }
  var c = sc.counts || {};
  var checks = c.checks !== undefined ? c.checks
    : c.total !== undefined ? c.total : "—";
  return tf("cruns.att.idak.counts", { checks: checks,
    violated: c.violated !== undefined ? c.violated : "—" });
}

function _crunsPassChip(rec) {
  var c = rec.certifier || {};
  var code = rec.code || {};
  var cat = rec.template_catalog || {};
  var parts = [];
  if (c.model) { parts.push(c.model); }
  if (c.channel) { parts.push(c.channel); }
  if (code.commit_short) { parts.push(code.commit_short); }
  if (cat.version) { parts.push(t("cruns.pass.catalog") + " " + cat.version); }
  else if (cat.reason_code) { parts.push(cosmosNoteWords(cat.reason_code)); }
  return parts.length ? parts.join(" · ") : cosmosNoteWords("pre-passport");
}

function _crunsVerdictChip(rec) {
  var cls = "chip chip-neutral small";
  var text;
  if (rec.done === false) {
    cls = "chip chip-running small";
    text = rec.state || t("cruns.attempts.running");
  } else if (rec.error) {
    cls = "chip chip-bad small";
    text = String(rec.error);
  } else {
    text = rec.verdict || rec.state || "—";
    if (rec.summary && rec.summary.certified) { cls = "chip chip-ok small"; }
  }
  return el("span", { class: cls, text: text, title: rec.state || "" });
}

function _crunsRenderAttempts(ctx, host) {
  var doc = ctx.doc || {};
  var atts = doc.attempts || [];
  if (!atts.length) {
    host.appendChild(el("p", { class: "vmp-note", text: t("cruns.attempts.none") }));
    return;
  }
  var runBusy = _crunsCardBusy(doc);
  var wrap = el("div", { class: "table-wrap" });
  var table = el("table", { class: "data-table cruns-attempts" });
  var thead = el("thead");
  var hr = el("tr");
  ["cruns.att.id", "cruns.att.task", "cruns.att.engine", "cruns.att.params",
   "cruns.att.verdict", "cruns.att.vmp", "cruns.att.idak", "cruns.att.passport",
   "cruns.att.files", "cruns.att.replay"].forEach(function (k) {
    hr.appendChild(el("th", { text: t(k) }));
  });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = el("tbody");
  atts.forEach(function (rec) {
    var tr = el("tr");
    // попытка: идентификатор, время, пилюля повтора и чип отчёта
    var idCell = el("td", { class: "cruns-att-id" });
    idCell.appendChild(el("span", { class: "mono", text: rec.id || "—" }));
    idCell.appendChild(el("div", { class: "muted small",
                                   text: _crunsFmtDate(rec.started_at, true) }));
    // Кто нажал (10.09.2026): актор — строкой ПОД идентификатором, а не
    // одиннадцатой колонкой: шапка таблицы не переносится по словам
    // (.cruns-attempts th { white-space: nowrap }), а ячейка попытки уже
    // собирает подстроки — время, «повтор чего», чип вердикта повтора.
    if (rec.actor) {
      idCell.appendChild(el("div", { class: "muted small",
        text: t("cruns.att.actor") + ": " + String(rec.actor) }));
    }
    var of = rec.replay && rec.replay.of;
    if (of) {
      var mode = of.mode === "fresh" ? "fresh" : "journal";
      idCell.appendChild(el("div", { class: "cruns-replay-of",
        text: tf("cruns.replay.of." + mode, { id: of.source_attempt || "—" }) }));
      var v = cosmosReplayVerdictLabel(of.report);
      var chip = el("span", { class: "cruns-replay-verdict " + v.cls + " chip-click",
                              text: v.text, title: v.title || t("cruns.att.report") });
      chip.addEventListener("click", function () { openCosmosReplayReport(ctx, rec); });
      keyActivate(chip, function () { openCosmosReplayReport(ctx, rec); });
      idCell.appendChild(chip);
    }
    tr.appendChild(idCell);
    tr.appendChild(el("td", { text: _crunsTaskLabel(rec.kind || rec.task) }));
    tr.appendChild(el("td", { text: _crunsEngineWords(rec) }));
    tr.appendChild(el("td", { text: _crunsParamsWords(rec) }));
    var vc = el("td");
    vc.appendChild(_crunsVerdictChip(rec));
    tr.appendChild(vc);
    tr.appendChild(el("td", { text: _crunsVmpWords(rec) }));
    tr.appendChild(el("td", { text: _crunsIdakWords(rec) }));
    tr.appendChild(el("td", {}, [el("span", { class: "chip chip-neutral small",
                                              text: _crunsPassChip(rec) })]));
    // артефакты: ссылки области прогона
    var fc = el("td", { class: "cruns-files" });
    (rec.files || []).forEach(function (f) {
      if (f && f.href) { fc.appendChild(_crunsLink(f.href, f.alias || f.href)); }
    });
    if (rec.exports && rec.exports.index_href) {
      fc.appendChild(_crunsLink(rec.exports.index_href, t("cruns.att.exports")));
    }
    if (rec.exchanges_href && rec.exchanges && rec.exchanges.present !== false) {
      fc.appendChild(_crunsLink(rec.exchanges_href,
        tf("cruns.att.exchanges", { n: rec.exchanges.count !== undefined ? rec.exchanges.count : "—" })));
    } else if (rec.exchanges && rec.exchanges.present === false && rec.exchanges.reason_code) {
      fc.appendChild(el("span", { class: "muted small",
                                  text: cosmosNoteWords(rec.exchanges.reason_code) }));
    }
    if (rec.replay_report_href) {
      fc.appendChild(_crunsLink(rec.replay_report_href, t("cruns.att.report")));
    }
    if (!fc.childNodes.length) { fc.textContent = "—"; }
    tr.appendChild(fc);
    // кнопки повтора
    var rc = el("td", { class: "cruns-replay-cell" });
    ["journal", "fresh"].forEach(function (mode) {
      var av = rec.replay ? rec.replay[mode] : null;
      var st = cosmosReplayReason(av, runBusy, mode);
      var b = el("button", { class: "btn btn-small cruns-replay-" + mode, type: "button",
                             text: t("cruns.replay." + mode) });
      b.disabled = !st.enabled;
      if (st.title) { b.title = st.title; }
      b.addEventListener("click", function () { _crunsReplay(ctx, rec, mode); });
      rc.appendChild(b);
    });
    tr.appendChild(rc);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
}

function _crunsKv(dl, label, valueNode, title) {
  var row = el("div", { class: "kv-row" });
  var dt = el("dt", { text: label });
  if (title) { dt.title = title; }
  row.appendChild(dt);
  var dd = el("dd");
  if (typeof valueNode === "string") { dd.textContent = valueNode; }
  else if (valueNode) { dd.appendChild(valueNode); }
  row.appendChild(dd);
  dl.appendChild(row);
}

/* Паспорт прогона — только чтение с диска; чего нет — код и слова к нему
 * (раздел 8 проекта, образец RunPage.tsx CDPM). */
function _crunsRenderPassport(ctx, host) {
  var doc = ctx.doc || {};
  var ps = doc.passport;
  if (!ps) {
    host.appendChild(el("p", { class: "vmp-note", text: cosmosNoteWords("pre-passport") }));
    return;
  }
  var env = ps.created || {};
  var dl = el("dl", { class: "kv cruns-passport-kv" });
  // код COSMOS
  var code = env.code || {};
  var codeWords = code.commit_short || code.commit
    ? String(code.commit_short || String(code.commit).slice(0, 7)) +
      (code.tree_dirty ? " · " + tf("cruns.pass.dirty", { n: code.dirty_paths !== undefined ? code.dirty_paths : "—" }) : "")
    : cosmosNoteWords(code.reason_code || "no-git");
  _crunsKv(dl, t("cruns.pass.code"), codeWords);
  // решатели
  var sv = env.solver || {};
  var solverParts = [];
  var build = sv.build || {};
  if (sv.engine) {
    solverParts.push(t("cruns.task.rostering") + ": " + sv.engine +
      (build.timefold_version ? " " + build.timefold_version : "") +
      (sv.url ? " · " + sv.url : "") +
      (sv.version_source_code === "no-build-files" ? " · " + cosmosNoteWords("no-build-files") : ""));
  }
  var pe = env.pairing_engines || {};
  if (pe.available && pe.available.length) {
    solverParts.push(t("cruns.task.pairing") + ": " + pe.available.join(" / "));
  }
  var pk = env.packages || {};
  Object.keys(pk).forEach(function (k) {
    if (pk[k] && (k === "ortools" || k === "highspy")) { solverParts.push(k + " " + pk[k]); }
  });
  if (env.python) { solverParts.push(t("cruns.pass.python") + " " + env.python); }
  _crunsKv(dl, t("cruns.pass.solvers"), solverParts.join(" · ") || "—");
  // каталог шаблонов
  var cat = env.catalog || {};
  _crunsKv(dl, t("cruns.pass.catalog"), cat.version
    ? cat.version + (cat.fingerprint ? " · " + String(cat.fingerprint).slice(0, 12) + "…" : "")
    : cosmosNoteWords(cat.reason_code || "catalog-unavailable", cat.error));
  // входы (SHA-256)
  var inputs = ps.inputs || [];
  var inNode = el("ul", { class: "cruns-inputs" });
  inputs.forEach(function (x) {
    var sha = String(x.sha256 || x.sha || "");
    var line = [x.source_run, sha.slice(0, 12) + (sha ? "…" : ""),
                x.manifest_sha256 ? "manifest " + String(x.manifest_sha256).slice(0, 12) + "…" : null,
                x.spec_version,
                x.files && x.files.length !== undefined
                  ? tf("cruns.pass.files", { n: x.files.length }) : null,
                x.verdict].filter(Boolean).join(" · ");
    inNode.appendChild(el("li", { class: "mono", text: line || "—", title: sha }));
  });
  _crunsKv(dl, t("cruns.pass.inputs"), inputs.length ? inNode : "—");
  // модели по попыткам (роли)
  var models = [];
  (ps.models_summary || []).forEach(function (m) {
    if (!m) { return; }
    if (typeof m === "string") { models.push(m); return; }
    models.push((m.role_title || cosmosRoleWords(m.role)) + ": " + (m.provider ? m.provider + "/" : "") +
      (m.model || "—") + (m.channel ? " · " + m.channel : "") +
      (m.attempts !== undefined ? " (×" + m.attempts + ")" : ""));
  });
  if (!models.length) {
    var seen = {};
    (ps.attempts || []).forEach(function (a) {
      var c = a && a.certifier;
      if (!c || !c.model) { return; }
      var k = (c.provider ? c.provider + "/" : "") + c.model + (c.channel ? " · " + c.channel : "");
      seen[k] = (seen[k] || 0) + 1;
    });
    Object.keys(seen).forEach(function (k) {
      models.push(t("cruns.pass.certifier") + ": " + k + " (×" + seen[k] + ")");
    });
  }
  var mNode = el("ul", { class: "cruns-models" });
  models.forEach(function (s) { mNode.appendChild(el("li", { text: s })); });
  _crunsKv(dl, t("cruns.pass.models"), models.length ? mNode : t("cruns.pass.none"));
  // журналы
  var j = ps.journals || {};
  _crunsKv(dl, t("cruns.pass.journals"), tf("cruns.pass.journals.words", {
    ex: j.attempts_with_exchanges !== undefined ? j.attempts_with_exchanges : "—",
    n: j.model_exchanges !== undefined ? j.model_exchanges : "—",
    idak: j.source_check_snapshots !== undefined ? j.source_check_snapshots : "—" }));
  if (ps.attempts_trimmed) {
    _crunsKv(dl, t("cruns.pass.trimmed").split(":")[0],
      tf("cruns.pass.trimmed", { n: ps.attempts_trimmed,
                                 keep: env.attempts_keep !== undefined ? env.attempts_keep : "—" }));
  }
  host.appendChild(dl);
  var gaps = ps.gaps || [];
  if (gaps.length) {
    host.appendChild(el("p", { class: "vmp-note cruns-gaps",
      text: t("cruns.pass.gaps") + ": " + gaps.map(function (g) {
        return cosmosNoteWords(g && g.code ? g.code : g, g && g.words);
      }).join("; ") }));
  }
}

/* ------------------------------------------------------------- повтор */

/* Диалог → POST …/attempts/{id}/replay {mode} → 202 → тост; карточка
 * перечитывается сразу и раз в 5 с, пока прогон busy; 409 — словами
 * сервера, кнопки остаются в прежнем состоянии. */
function _crunsReplay(ctx, rec, mode) {
  mode = mode === "fresh" ? "fresh" : "journal";
  var d1 = rec.reproducibility && rec.reproducibility.class === "deterministic";
  var msg = tf("cruns.replay.dlg." + mode + ".msg", { run: ctx.run });
  if (mode === "fresh") {
    msg += " " + t(d1 ? "cruns.replay.dlg.fresh.d1" : "cruns.replay.dlg.fresh.wc");
  }
  appConfirm(tf("cruns.replay.dlg." + mode + ".title", { id: rec.id }), {
    ok: t("cruns.replay.dlg.ok"), details: [msg]
  }).then(function (yes) {
    if (!yes) { return; }
    return api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/attempts/" +
               encodeURIComponent(rec.id) + "/replay", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: mode })
    }).then(function (res) {
      // 202: возврат press в режиме повтора (attempt_id) либо форма ручки
      // раздела 17.4 (attempt) — оба честно называют новую попытку
      toast(tf("cruns.replay.started", { run: ctx.run, id: rec.id,
        new: (res && (res.attempt || res.attempt_id)) || "—",
        mode: t("cruns.replay.mode." + mode) }), "ok");
      _crunsCardLoad(ctx);
      refreshCosmosRuns();
    }).catch(function (e) {
      toast(_crunsErrWords(e) || cosmosNoteWords(_crunsErrCode(e)), "error");
    });
  });
}

function _crunsReportLinkOf(rec, prefix) {
  var files = rec.files || [];
  for (var i = 0; i < files.length; i++) {
    if (files[i] && files[i].href && String(files[i].alias || "").indexOf(prefix) === 0) {
      return files[i].href;
    }
  }
  return null;
}

/* Окно отчёта повтора (раздел 17.5): вердикт и слова, первое различие,
 * таблица итогов, сводка назначений, цепочки, среда, ссылки. */
function openCosmosReplayReport(ctx, rec) {
  api("/cosmos-runs/" + encodeURIComponent(ctx.run) + "/attempts/" +
      encodeURIComponent(rec.id) + "/replay-report").then(function (res) {
    var rep = (res && res.report) || {};
    var m = _flowModal("↺", tf("cruns.replay.report.h", { id: rec.id }), true);
    var box = el("div", { class: "cruns-replay-report" });
    m.body.appendChild(box);
    var v = cosmosReplayVerdictLabel(rep);
    var vKey = CRUNS_REPLAY_VERDICTS.indexOf(rep.verdict) >= 0 ? rep.verdict : "not-comparable";
    box.appendChild(el("p", { class: "cruns-replay-line" }, [
      el("span", { class: "cruns-replay-verdict " + v.cls,
                   text: t("cruns.replay.verdict." + vKey) }),
      el("span", { text: " " + (rep.words || "") })]));
    if (rep.files && rep.files.compared !== undefined) {
      box.appendChild(el("p", { class: "muted small", text: tf("cruns.replay.report.files", {
        n: rep.files.compared, same: rep.files.same !== undefined ? rep.files.same : "—" }) }));
    }
    var fd = rep.first_difference;
    if (fd) {
      var where = fd.path !== undefined ? fd.path : fd.line !== undefined ? fd.line
        : fd.offset !== undefined ? fd.offset : "";
      box.appendChild(el("p", {}, [
        el("b", { text: t("cruns.replay.report.first") + ": " }),
        el("span", { class: "mono", text: fmt(fd.file) + (where !== "" ? " · " + where : "") +
          (fd.reason_code ? " · " + cosmosNoteWords(fd.reason_code) : "") +
          (fd.source !== undefined || fd.replay !== undefined
            ? " · " + fmt(fd.source) + " → " + fmt(fd.replay) : "") })]));
    }
    if (rep.outcomes && rep.outcomes.length) {
      box.appendChild(el("h4", { text: t("cruns.replay.report.outcomes") }));
      var table = el("table", { class: "data-table" });
      var hr = el("tr");
      [t("cruns.replay.report.th.name"), t("cruns.replay.report.th.source"),
       t("cruns.replay.report.th.replay"), ""].forEach(function (s) {
        hr.appendChild(el("th", { text: s }));
      });
      table.appendChild(el("thead", {}, [hr]));
      var tb = el("tbody");
      rep.outcomes.forEach(function (o) {
        var tr = el("tr");
        tr.appendChild(el("td", { text: fmt(o.name), title: o.source_file || "" }));
        tr.appendChild(el("td", { class: "mono", text: fmt(o.source) }));
        tr.appendChild(el("td", { class: "mono", text: fmt(o.replay) }));
        // законное различие (legit_difference сервера, replay_compare._row:
        // прогон решателя) — «≠ законно», не ✗: вердикт «итоги совпадают»
        // его не считает расхождением, и окно не противоречит себе
        var mark;
        if (o.legit_difference) {
          mark = (o.same === false ? "≠ " : o.same === true ? "✓ " : "— ") +
            t("cruns.replay.outcome.legit");
        } else {
          mark = o.same === true ? "✓" : o.same === false ? "✗" : "—";
        }
        if (o.note_code) { mark += " " + cosmosNoteWords(o.note_code); }
        tr.appendChild(el("td", { text: mark }));
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      box.appendChild(el("div", { class: "table-wrap" }, [table]));
    }
    if (rep.roster_diff) {
      box.appendChild(el("h4", { text: t("cruns.replay.report.roster") }));
      box.appendChild(el("p", { text: cosmosReplayRosterWords(rep.roster_diff) }));
      var emps = (rep.roster_diff.employees || []).slice(0, 10);
      if (emps.length) {
        var ul = el("ul", { class: "cruns-emps" });
        emps.forEach(function (e) {
          function cnt(x) { return Array.isArray(x) ? x.length : (x === undefined || x === null ? 0 : x); }
          ul.appendChild(el("li", { text: tf("cruns.replay.roster.emp", {
            emp: e.employee || e.id || e.emp || "?", lost: cnt(e.lost), gained: cnt(e.gained) }) }));
        });
        box.appendChild(ul);
      }
    }
    if (rep.pairing_diff) {
      var pd = rep.pairing_diff;
      function cntp(x) { return Array.isArray(x) ? x.length : (x === undefined || x === null ? "—" : x); }
      box.appendChild(el("h4", { text: t("cruns.replay.report.pairing") }));
      // дельта стоимости — pairing_diff.total_cost: {source, replay, delta}
      var cost = pd.total_cost && typeof pd.total_cost === "object" ? pd.total_cost : {};
      box.appendChild(el("p", { text: tf("cruns.replay.pairing.summary", {
        common: cntp(pd.common), src: cntp(pd.only_in_source), new: cntp(pd.only_in_replay) }) +
        (cost.delta !== undefined && cost.delta !== null
          ? " · " + tf("cruns.replay.pairing.cost", { delta: cost.delta }) : "") }));
    }
    var rp = rep.reproducibility || {};
    if (rp.explanation_code || rp.class) {
      var ck = rp.explanation_code === "deterministic-expected-same" ? "deterministic"
        : rp.explanation_code === "wall-clock-legit-difference" ? "wall-clock"
        : rp.class === "deterministic" ? "deterministic" : "wall-clock";
      // гейт Д1 сервера — {files, same: bool, words}: same === false —
      // решение НЕ воспроизведено байт в байт, слова несут перечень файлов
      var det = rp.deterministic_gate;
      var detWords = "";
      if (det && det.same === false) {
        ck = "deterministic-failed";
        detWords = det.words ? " — " + det.words : "";
      }
      box.appendChild(el("p", { class: "muted small", text: t("cruns.replay.class." + ck) + detWords }));
    }
    box.appendChild(el("h4", { text: t("cruns.replay.report.env") }));
    var envUl = el("ul", { class: "cruns-env" });
    cosmosReplayEnvWords((rep.environment || {}).differs || rep.environment_differs)
      .forEach(function (s) { envUl.appendChild(el("li", { text: s })); });
    box.appendChild(envUl);
    var links = el("p", { class: "cruns-replay-links" });
    links.appendChild(el("b", { text: t("cruns.replay.report.links") + ": " }));
    var repHref = rec.replay_report_href || _crunsReportLinkOf(rec, "отчёт-повтора");
    if (repHref) { links.appendChild(_crunsLink(repHref, t("cruns.replay.report.json"))); }
    var jHref = _crunsReportLinkOf(rec, "журнал-внешних-вызовов");
    if (jHref) { links.appendChild(_crunsLink(jHref, t("cruns.replay.report.journal"))); }
    if (rec.exchanges_href) {
      links.appendChild(_crunsLink(rec.exchanges_href, tf("cruns.att.exchanges", {
        n: rec.exchanges && rec.exchanges.count !== undefined ? rec.exchanges.count : "—" })));
    }
    box.appendChild(links);
  }).catch(function (e) {
    var code = _crunsErrCode(e);
    toast(code ? cosmosNoteWords(code, _crunsErrWords(e)) : _crunsErrWords(e), "error");
  });
}

/* --------------------------------------------- шапка конвейера: прогон */

var _flowRunsList = null;   // список прогонов для переключателя
var _flowRunsAt = 0;        // время последнего чтения списка (демпфер 20 с)
var _flowRunCur = null;     // текущий прогон по /api/flow

/* Чип «прогон: run-0003 · название», переключатель и кнопка «Новый прогон»
 * (раздел 10.3 проекта); без прогона — подсказка по runs_hint_code и полоса
 * над схемой с кнопками; сервер без ключей run/runs_hint_code — ничего не
 * показывается (нечего утверждать). */
function _flowRunHeader(st) {
  var chip = $("flow-run-chip"), sel = $("flow-run-select"),
      btn = $("btn-flow-run-new"), hint = $("flow-run-hint");
  if (!chip || !sel || !btn || !hint) { return; }
  var has = st && typeof st === "object" &&
    (Object.prototype.hasOwnProperty.call(st, "run") ||
     Object.prototype.hasOwnProperty.call(st, "runs_hint_code"));
  if (!has) {
    [chip, sel, btn, hint].forEach(function (n) { n.classList.add("hidden"); });
    return;
  }
  var run = st.run || null;
  var code = st.runs_hint_code || null;
  // прогон с отказом чтения (error_code run-unreadable): номер есть,
  // состояния нет — чип предупреждает и несёт слова отказа
  var unreadable = run && run.error_code ? run : null;
  var nextRun = run ? run.run : null;
  if (_flowRunCur !== nextRun) { _flowSrcEpoch++; }
  _flowRunCur = nextRun;
  chip.classList.remove("hidden");
  chip.classList.toggle("chip-warn", !run || !!unreadable);
  // чип — кнопка всегда: с прогоном открывает карточку, без прогона ведёт
  // на «Прогоны» (нажатие клавиатурой не бывает пустым)
  chip.classList.add("chip-click");
  if (run) {
    chip.textContent = t("flow.run.chip") + ": " + run.run +
      (run.title ? " · " + run.title : "");
    chip.title = unreadable
      ? cosmosNoteWords(run.error_code, run.error)
      : cosmosRunsStateLabel(run.state_kind) +
        (run.package_sha12 ? " · " + run.package_sha12 : "") +
        (run.implicit ? " · " + t("flow.run.implicit") : "");
  } else {
    var key = { "no-current-run": "none", "storage-not-initialized": "uninit",
                "storage-off": "off", "env-package": "file" }[code] || "none";
    chip.textContent = t("flow.run." + key);
    chip.title = "";
  }
  var showHint = !run && (code === "no-current-run" || code === "storage-not-initialized");
  var busyPrev = run && run.busy_previous;
  hint.textContent = "";
  if (showHint || busyPrev || unreadable) {
    hint.classList.remove("hidden");
    if (unreadable) {
      hint.appendChild(el("span", { text: run.run + ": " +
        cosmosNoteWords(run.error_code, run.error) }));
      var b0 = el("button", { class: "btn btn-ghost btn-small", type: "button",
                              text: t("flow.run.all") });
      b0.addEventListener("click", function () { showTab("cruns"); });
      hint.appendChild(b0);
    } else if (busyPrev) {
      hint.appendChild(el("span", { text: tf("flow.run.busy_prev", {
        sha: busyPrev.sha12 || "", task: _crunsTaskLabel(busyPrev.task),
        progress: busyPrev.progress || "" }) }));
    } else {
      hint.appendChild(el("span", { text: chip.textContent }));
      var b1 = el("button", { class: "btn btn-primary btn-small", type: "button",
                              text: t("flow.run.new") });
      b1.addEventListener("click", function () { _crunsCreate("", true, { toFlow: false }); });
      var b2 = el("button", { class: "btn btn-ghost btn-small", type: "button",
                              text: t("flow.run.all") });
      b2.addEventListener("click", function () { showTab("cruns"); });
      hint.appendChild(b1);
      hint.appendChild(b2);
    }
  } else {
    hint.classList.add("hidden");
  }
  btn.classList.toggle("hidden", code === "storage-off");
  var selOff = code === "storage-off" || code === "env-package";
  sel.title = t("flow.run.select");
  sel.setAttribute("aria-label", t("flow.run.select"));
  _flowRunSelectFill();
  sel.classList.toggle("hidden", selOff || !_flowRunsList || !_flowRunsList.length);
}

function _flowRunSelectFill() {
  var sel = $("flow-run-select");
  if (!sel) { return; }
  sel.textContent = "";
  var list = (_flowRunsList || []).slice().sort(function (a, b) {
    return cosmosRunsSortCompare(a, b, { key: "order", dir: "asc" });
  });
  var found = false;
  list.forEach(function (r) {
    var o = el("option", { value: r.run, text: r.run + (r.title ? " · " + r.title : "") });
    if (r.run === _flowRunCur) { o.selected = true; found = true; }
    sel.appendChild(o);
  });
  if (!found) {
    var ph = el("option", { value: "", text: "— " + t("flow.run.select") + " —" });
    ph.selected = true;
    sel.insertBefore(ph, sel.firstChild);
  }
}

/* Список для переключателя — при открытии схемы и раз в 20 с; ошибка
 * чтения прячет переключатель (слова ошибки — на экране «Прогоны»). */
function _flowRunSelectRefresh() {
  var now = Date.now();
  if (now - _flowRunsAt < _CRUNS_POLL_MS) { return; }
  _flowRunsAt = now;
  api("/cosmos-runs").then(function (data) {
    _flowRunsList = (data && data.runs) || [];
    if (_flowState) { _flowRunHeader(_flowState); }
  }).catch(function () {
    _flowRunsList = null;
    var sel = $("flow-run-select");
    if (sel) { sel.classList.add("hidden"); }
  });
}

/* ------------------------------------------------------ подключение */

function _crunsInit() {
  if (_cruns.wired) { return; }
  _cruns.wired = true;
  _cruns.sort = _crunsLoadSort();
  var on = function (id, ev, fn) {
    var n = $(id);
    if (n) { n.addEventListener(ev, fn); }
  };
  on("btn-cruns-refresh", "click", function () {
    if (_cruns.view === "all") { refreshRunsAll(); } else { refreshCosmosRuns(); }
  });
  on("btn-cruns-view-runs", "click", function () { crunsShowView("runs"); });
  on("btn-cruns-view-all", "click", function () { crunsShowView("all"); });
  on("btn-cruns-new", "click", function () {
    var form = $("cruns-new-form");
    _crunsNewForm(!form || form.classList.contains("hidden"));
  });
  on("btn-cruns-create", "click", function () {
    var inp = $("cruns-new-title");
    _crunsCreate(inp ? inp.value.trim() : "", true, { toFlow: true });
  });
  on("btn-cruns-create-cancel", "click", function () { _crunsNewForm(false); });
  on("cruns-new-title", "keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      _crunsCreate(e.target.value.trim(), true, { toFlow: true });
    } else if (e.key === "Escape") { _crunsNewForm(false); }
  });
  on("cruns-search", "input", function (e) {
    _cruns.query = e.target.value;
    renderCosmosRuns();
  });
  on("btn-cruns-sort", "click", function () {
    var s = _cruns.sort || _crunsLoadSort();
    var i = CRUNS_SORT_KEYS.indexOf(s.key);
    _cruns.sort = { key: CRUNS_SORT_KEYS[(i + 1) % CRUNS_SORT_KEYS.length], dir: s.dir };
    _crunsSaveSort();
    renderCosmosRuns();
  });
  on("btn-cruns-dir", "click", function () {
    var s = _cruns.sort || _crunsLoadSort();
    _cruns.sort = { key: s.key, dir: s.dir === "asc" ? "desc" : "asc" };
    _crunsSaveSort();
    renderCosmosRuns();
  });
  on("btn-cruns-mark", "click", function () {
    _cruns.markMode = !_cruns.markMode;
    if (!_cruns.markMode) { _cruns.marked = {}; }
    renderCosmosRuns();
  });
  on("btn-cruns-mark-all", "click", _crunsMarkShown);
  on("btn-cruns-mark-none", "click", _crunsMarkNone);
  on("btn-cruns-delete", "click", _crunsDelete);
  window.addEventListener("focus", function () {
    if (_nav.tab === "cruns") { refreshCosmosRuns(); }
  });
  // шапка конвейера
  on("btn-flow-run-new", "click", function () {
    _crunsCreate("", true, { toFlow: false });
  });
  on("flow-run-select", "change", function (e) {
    var run = e.target.value;
    if (!run || run === _flowRunCur) { return; }
    _crunsSetCurrent(run).catch(function () { _flowRunSelectFill(); });
  });
  var chip = $("flow-run-chip");
  if (chip) {
    // с прогоном — карточка; без прогона (подсказка uninit/none/off/file)
    // — экран «Прогоны»: кнопка с role=button никогда не молчит
    var openCur = function () {
      if (_flowRunCur) { openCosmosRunCard(_flowRunCur); }
      else { showTab("cruns"); }
    };
    chip.addEventListener("click", openCur);
    keyActivate(chip, openCur);
  }
}
