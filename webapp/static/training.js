/* Interactive training engine (Помощь → Обучение). Loads after i18n.js,
 * app.js, onboarding.js, demo.js and reuses their globals ($, el, t, tf,
 * state, showTab, showGroup, openDemoView, isAdmin).
 *
 * Two modes over the SAME chapter scripts:
 *   Автопилот  — the engine performs every step itself: highlights the
 *                target, narrates, runs the action, pauses, moves on;
 *   Интерактив — the engine instructs and waits: the user must perform
 *                the action; a `done` predicate (polled) advances the
 *                lesson; steps without a predicate advance manually.
 *
 * Steps only ever NAVIGATE and TYPE INTO local inputs — no submits, no
 * agent runs, no jobs: training must be safe to replay on prod data.
 * Admin-only chapters are visible to everyone but locked for non-admins
 * (the screens they walk simply do not exist for plain users). */
"use strict";

var LEARN_POLL_MS = 500;          // interactive-mode completion poll

/* ------------------------------------------------------------- chapters
 * Step fields:
 *   key    — i18n key of the narration/instruction text;
 *   target — CSS selector to highlight (optional);
 *   go     — action the autopilot performs (also "сделать за меня");
 *   done   — interactive-mode completion predicate (optional: manual). */

var LEARN_CHAPTERS = [
  { id: "c_data", admin: false, steps: [
    { key: "learn.c_data.s1",
      target: "#panel-data",
      go: function () { showTab("data"); },
      done: function () { return _learnTabOn("data"); } },
    { key: "learn.c_data.s2",
      target: "#instances-table" },
    { key: "learn.c_data.s3",
      target: "#instances-table th[data-i18n=\"html.th.dataScope\"]" },
    { key: "learn.c_data.s4",
      target: "#upload-form" },
    { key: "learn.c_data.s5",
      target: "#upload-submit" },
    { key: "learn.c_data.s6",
      target: "#instances-table" },
    { key: "learn.c_data.s7",
      target: null },
    { key: "learn.c_data.s8",
      target: null },
  ]},
  { id: "c_format", admin: false, steps: [
    { key: "learn.c_format.s1",
      target: "#subtabs",
      go: function () { showTab("format"); },
      done: function () { return _learnTabOn("format"); } },
    { key: "learn.c_format.s2",
      target: "#config-form" },
    { key: "learn.c_format.s3",
      target: "#panel-format .banner-ok" },
    { key: "learn.c_format.s4",
      target: "#schema-pre" },
    { key: "learn.c_format.s5",
      target: "#upload-form",
      go: function () { showTab("data"); },
      done: function () { return _learnTabOn("data"); } },
    { key: "learn.c_format.s6",
      target: "#ssim-form" },
    { key: "learn.c_format.s7",
      target: "#import-form" },
    { key: "learn.c_format.s8",
      target: null },
  ]},
  { id: "c_opt", admin: false, steps: [
    { key: "learn.c_opt.s1",
      target: "#panel-jobs",
      go: function () { showTab("jobs"); },
      done: function () { return _learnTabOn("jobs"); } },
    { key: "learn.c_opt.s2",
      target: "#job-instance" },
    { key: "learn.c_opt.s3",
      target: "#panel-jobs .stage-grid" },
    { key: "learn.c_opt.s4",
      target: "#advisor-box" },
    { key: "learn.c_opt.s5",
      target: "#panel-runs",
      go: function () { showTab("runs"); },
      done: function () { return _learnTabOn("runs"); } },
    { key: "learn.c_opt.s6",
      target: "#active-job" },
    { key: "learn.c_opt.s7",
      target: "#jobs-table" },
    { key: "learn.c_opt.s8",
      target: null },
  ]},
  { id: "c_results", admin: false, steps: [
    { key: "learn.c_results.s1",
      target: null,
      go: function () { showTab("results"); },
      done: function () { return _learnTabOn("results"); } },
    { key: "learn.c_results.s2",
      target: "#results-instance",
      // pick the first instance so the results body (and every later
      // anchor of this chapter) becomes visible with real data — a pure
      // read: selecting only GETs the stored artifacts
      go: function () {
        showTab("results");
        var sel = $("results-instance");
        if (sel && !sel.value && sel.options.length) {
          sel.selectedIndex = sel.options[0].disabled && sel.options.length > 1
            ? 1 : 0;
          sel.dispatchEvent(new Event("change"));
        }
      },
      done: function () {
        var sel = $("results-instance");
        return !!(sel && sel.value);
      } },
    { key: "learn.c_results.s3",
      target: "#results-body .cards-grid" },
    { key: "learn.c_results.s4",
      target: "#roster-viz" },
    { key: "learn.c_results.s5",
      target: "#costing-card" },
    { key: "learn.c_results.s6",
      target: "#pay-card" },
    { key: "learn.c_results.s7",
      target: "#pay-body" },
    { key: "learn.c_results.s8",
      target: "#artifacts-list" },
  ]},
  { id: "c_scen", admin: false, steps: [
    { key: "learn.c_scen.s1",
      target: "#panel-scenarios",
      go: function () { showTab("scenarios"); },
      done: function () { return _learnTabOn("scenarios"); } },
    { key: "learn.c_scen.s2",
      target: "#scenario-form" },
    { key: "learn.c_scen.s3",
      target: "#scen-rules-grid" },
    { key: "learn.c_scen.s4",
      target: "#scenario-create" },
    { key: "learn.c_scen.s5",
      target: "#scenarios-table" },
    { key: "learn.c_scen.s6",
      target: "#scenarios-table" },
    { key: "learn.c_scen.s7",
      target: null },
  ]},
  { id: "c_board", admin: false, steps: [
    { key: "learn.c_board.s1",
      target: "#board-instance",
      go: function () { showTab("board"); },
      done: function () { return _learnTabOn("board"); } },
    { key: "learn.c_board.s2",
      target: "#board-table" },
    { key: "learn.c_board.s3",
      target: "#board-tbody" },
    { key: "learn.c_board.s4",
      target: "#board-violations" },
    { key: "learn.c_board.s5",
      target: "#board-tiles" },
    // 2026-07-20: причина правки обязательна. Без этого шага ученик
    // упирается в неактивную кнопку «Применить» и не понимает почему;
    // цель — редактор целиком, т.к. поле причины появляется только
    // после выбора плитки и селектора в разметке заранее нет
    { key: "learn.c_board.s5b",
      target: "#board-editor" },
    { key: "learn.c_board.s6",
      target: "#btn-board-reset" },
    { key: "learn.c_board.s7",
      target: "#pay-card",
      go: function () { showTab("results"); },
      done: function () { return _learnTabOn("results"); } },
    { key: "learn.c_board.s8",
      target: null },
  ]},
  { id: "c_dz", admin: false, steps: [
    // disruption recovery: the lesson NAVIGATES and highlights only — it
    // never registers a sickness or applies a plan (safe on prod data)
    { key: "learn.c_dz.s1",
      target: "#board-instance",
      go: function () {
        showTab("board");
        var sel = $("board-instance");
        if (sel && !sel.value && sel.options.length) {
          for (var i = 0; i < sel.options.length; i++) {
            if (!sel.options[i].disabled) { sel.selectedIndex = i; break; }
          }
          sel.dispatchEvent(new Event("change"));
        }
      },
      done: function () { return _learnTabOn("board"); } },
    { key: "learn.c_dz.s2",
      target: "#btn-dz-toggle",
      go: function () {
        showTab("board");
        var box = $("dz-box");
        if (box && box.classList.contains("hidden")) { dzToggle(); }
      },
      done: function () {
        var box = $("dz-box");
        return !!(box && !box.classList.contains("hidden"));
      } },
    { key: "learn.c_dz.s3",
      target: "#dz-employee" },
    { key: "learn.c_dz.s4",
      target: "#btn-dz-plan" },
    { key: "learn.c_dz.s5",
      target: "#btn-board-reset" },
    // Phase B: flight-level events + neighbourhood rebuild. Navigation
    // only, as above — the rebuild job is never launched from the lesson.
    { key: "learn.c_dz.s6",
      target: "#dz-kind" },
    { key: "learn.c_dz.s7",
      target: "#btn-dz-rebuild" },
    { key: "learn.c_dz.s8",
      target: null },
  ]},
  { id: "c_crewres", admin: false, steps: [
    { key: "learn.c_crewres.s1",
      target: "#panel-vacation",
      go: function () { showTab("vacation"); },
      done: function () { return _learnTabOn("vacation"); } },
    { key: "learn.c_crewres.s2",
      target: "#vac-quota-form" },
    { key: "learn.c_crewres.s3",
      target: "#btn-vac-award" },
    { key: "learn.c_crewres.s4",
      target: "#panel-quals",
      go: function () { showTab("quals"); },
      done: function () { return _learnTabOn("quals"); } },
    { key: "learn.c_crewres.s5",
      target: "#qual-warn-form" },
    { key: "learn.c_crewres.s6",
      target: "#panel-bases",
      go: function () { showTab("bases"); },
      done: function () { return _learnTabOn("bases"); } },
    { key: "learn.c_crewres.s7",
      target: "#bases-config-form" },
    { key: "learn.c_crewres.s8",
      target: null },
  ]},
  { id: "c_cab", admin: false, steps: [
    { key: "learn.c_cab.s1",
      target: "#panel-cabinet",
      go: function () { showTab("cabinet"); },
      done: function () { return _learnTabOn("cabinet"); } },
    { key: "learn.c_cab.s2",
      target: "#cab-identity" },
    { key: "learn.c_cab.s3",
      target: "#cab-schedule" },
    { key: "learn.c_cab.s4",
      target: "#cab-quals" },
    { key: "learn.c_cab.s5",
      target: "#cab-vacation" },
    { key: "learn.c_cab.s6",
      target: "#cab-identity" },
    { key: "learn.c_cab.s7",
      target: "#cab-links-card" },
    { key: "learn.c_cab.s8",
      target: null },
  ]},
  { id: "c_help", admin: false, steps: [
    { key: "learn.c_help.s1",
      target: "#panel-ask",
      go: function () { showTab("ask"); },
      done: function () { return _learnTabOn("ask"); } },
    { key: "learn.c_help.s2",
      target: "#ask-question" },
    { key: "learn.c_help.s3",
      target: "#ask-instances" },
    { key: "learn.c_help.s4",
      target: ".ask-prefer" },
    { key: "learn.c_help.s5",
      target: "#panel-suggest",
      go: function () { showTab("suggest"); },
      done: function () { return _learnTabOn("suggest"); } },
    { key: "learn.c_help.s6",
      target: "#sug-text",
      go: function () {
        showTab("suggest");
        var box = $("sug-text");
        if (box && !box.value) { box.value = t("learn.te.sample"); }
        if (box) { box.focus(); }
      } },
    { key: "learn.c_help.s7",
      target: "#sug-list" },
    { key: "learn.c_help.s8",
      target: "#panel-demo",
      go: function () { showTab("demo"); openDemoView("pipeline"); },
      done: function () { return _learnTabOn("demo"); } },
  ]},
  // Автоулучшение «фильм»: источники идей + База знаний + конвейер
  // (запрос пользователя 2026-07-16). Навигация и подсветка; ни один агент не
  // запускается и ни один CR не реализуется из урока — только показ.
  { id: "c_evo", admin: true, steps: [
    // 2026-07-20: глава начинается с «Контуров» — первой вкладки группы.
    // Раньше она стартовала с очереди предложений, то есть с середины
    // конвейера: ученик видел карточки, но не то, откуда они берутся.
    { key: "learn.c_evo.s0",
      target: "#loops-table",
      go: function () { showTab("loops"); },
      done: function () { return _learnTabOn("loops"); } },
    { key: "learn.c_evo.s0b",
      target: "#loops-tbody tr:last-child" },
    { key: "learn.c_evo.s1",
      target: "#panel-crs h2",
      go: function () { showTab("crs"); },
      done: function () { return _learnTabOn("crs"); } },
    { key: "learn.c_evo.s2",
      target: "#crs-agents" },
    { key: "learn.c_evo.s3",           // Отзывы
      target: "#crs-agent-desc",
      go: function () {
        showTab("crs");
        if (typeof crsView !== "undefined") { crsView = "feedback"; renderCrs(); }
      } },
    { key: "learn.c_evo.s4",           // Регуляторика
      target: "#crs-agent-desc",
      go: function () {
        if (typeof crsView !== "undefined") { crsView = "regulatory"; renderCrs(); }
      } },
    { key: "learn.c_evo.s5",           // UI/UX идеи
      target: "#crs-agent-desc",
      go: function () {
        if (typeof crsView !== "undefined") { crsView = "uiux"; renderCrs(); }
      } },
    { key: "learn.c_evo.s6",           // Аудиты (новый источник)
      target: "#btn-audit-check",
      go: function () {
        if (typeof crsView !== "undefined") { crsView = "audit"; renderCrs(); }
      } },
    { key: "learn.c_evo.s7",           // очередь CR + квота
      target: "#crs-list",
      go: function () {
        if (typeof crsView !== "undefined") { crsView = "all"; renderCrs(); }
      } },
    { key: "learn.c_evo.s8",           // Алгоритмы (Лаборатория)
      target: "#btn-lab-scan",
      go: function () { showTab("lab"); },
      done: function () { return _learnTabOn("lab"); } },
    { key: "learn.c_evo.s9",           // База знаний
      target: "#btn-kb-build",
      go: function () { showTab("kb"); },
      done: function () { return _learnTabOn("kb"); } },
    { key: "learn.c_evo.s10",          // итог + демо
      target: null },
  ]},
  { id: "c_admin", admin: true, steps: [
    { key: "learn.c_admin.s1",
      target: "#panel-users .section-head",
      go: function () { showTab("users"); },
      done: function () { return _learnTabOn("users"); } },
    { key: "learn.c_admin.s2",
      target: "#users-table" },
    { key: "learn.c_admin.s3",
      target: "#user-add-form" },
    { key: "learn.c_admin.s4",
      target: "#panel-users p.muted" },
    { key: "learn.c_admin.s5",
      target: "#panel-lab details.panel-help",
      go: function () { showTab("lab"); },
      done: function () { return _learnTabOn("lab"); } },
    { key: "learn.c_admin.s6",
      target: "#lab-table" },
    { key: "learn.c_admin.s7",
      target: null },
  ]},
];

function _learnTabOn(name) {
  var p = $("panel-" + name);
  return !!(p && !p.classList.contains("hidden"));
}

/* --------------------------------------------------------------- engine */

var learn = { chapter: null, mode: null, idx: -1, timer: null,
              poll: null, paused: false, bubble: null, highlighted: null,
              gen: 0 };

function learnActive() { return learn.idx >= 0; }

function learnStart(chapterId, mode) {
  var ch = null;
  LEARN_CHAPTERS.forEach(function (c) {
    if (c.id === chapterId) { ch = c; }
  });
  if (!ch) { return; }
  learnStop();
  learn.chapter = ch;
  learn.mode = mode;
  learn.idx = 0;
  learn.paused = false;
  learn.bubble = el("div", { class: "learn-bubble", role: "dialog" });
  document.body.appendChild(learn.bubble);
  learnRenderStep();
}

function learnStop() {
  learn.gen += 1;                 // invalidate every outstanding callback
  if (learn.timer) { clearTimeout(learn.timer); learn.timer = null; }
  if (learn.poll) { clearInterval(learn.poll); learn.poll = null; }
  // sweep ALL bubbles (incl. an untracked finish bubble still on its
  // 3.5s self-removal timer) so a new lesson never stacks on an old one
  document.querySelectorAll(".learn-bubble").forEach(function (b) {
    b.remove();
  });
  learn.bubble = null;
  _learnUnhighlight();
  learn.idx = -1;
  learn.chapter = null;
}

function _learnUnhighlight() {
  if (learn.highlighted) {
    learn.highlighted.classList.remove("tour-highlight");
    learn.highlighted = null;
  }
}

function _learnClearTimers() {
  if (learn.timer) { clearTimeout(learn.timer); learn.timer = null; }
  if (learn.poll) { clearInterval(learn.poll); learn.poll = null; }
}

function learnNext() {
  _learnClearTimers();
  if (!learnActive()) { return; }
  if (learn.idx + 1 >= learn.chapter.steps.length) {
    learnFinish();
    return;
  }
  learn.idx += 1;
  learnRenderStep();
}

function learnFinish() {
  var done = el("div", { class: "learn-bubble", role: "dialog" }, [
    el("p", { class: "learn-say", text: t("learn.finished") })
  ]);
  learnStop();
  // A lesson navigates across screens; when it ends, land the user back on
  // Помощь → Обучение (the hub) rather than stranding them on the last
  // screen the lesson walked to (user feedback 2026-07-16).
  showTab("learn");
  document.body.appendChild(done);
  setTimeout(function () { done.remove(); }, 3500);
  renderLearn();
}

function learnRenderStep() {
  if (!learnActive()) { return; }
  // Re-entrant by design (crew:lang re-render, pause toggle): ALWAYS drop
  // any pending timer/poll first, or a re-render would orphan them — an
  // orphaned poll chain-advances the lesson forever (audit 2026-07-16).
  _learnClearTimers();
  var gen = ++learn.gen;          // staleness guard for the async callbacks
  var step = learn.chapter.steps[learn.idx];
  _learnUnhighlight();

  // Autopilot performs the action up front; interactive waits for the user.
  if (learn.mode === "auto" && step.go && !learn.paused) { step.go(); }

  // highlight AFTER go() so the target's panel is visible. Some anchors
  // exist but are hidden until data is loaded or the user is linked (e.g.
  // the results body before an instance pick, cabinet cards without a
  // crew link) — fall back to the nearest VISIBLE ancestor so the user
  // always sees where the narration points.
  if (step.target) {
    var node = document.querySelector(step.target);
    while (node && node !== document.body &&
           node.offsetParent === null && !node.getClientRects().length) {
      node = node.parentElement;
    }
    if (node && node !== document.body) {
      node.classList.add("tour-highlight");
      learn.highlighted = node;
      if (node.scrollIntoView) {
        node.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }

  var b = learn.bubble;
  b.textContent = "";
  b.appendChild(el("div", { class: "learn-head" }, [
    el("span", { class: "chip chip-info small",
                 text: t(learn.mode === "auto" ? "learn.mode.auto"
                                               : "learn.mode.inter") }),
    el("span", { class: "muted small",
                 text: (learn.idx + 1) + " " + t("ui.of") + " " +
                       learn.chapter.steps.length })
  ]));
  b.appendChild(el("p", { class: "learn-say", text: t(step.key) }));

  var actions = el("div", { class: "learn-actions" });
  // Interactive + a `done` predicate = the step has a real action to perform.
  // The engine then WAITS for that action (poll) and offers NO manual «Далее»:
  // clicking past the action defeats the whole point of interactive mode
  // (user feedback 2026-07-16). «Сделать за меня» is always present so the
  // user is never stuck. Autopilot (which runs `go` itself) and interactive
  // read-only steps (no predicate) advance manually with «Далее»/«Готово».
  var gateOnAction = (learn.mode !== "auto" && !!step.done);
  if (gateOnAction) {
    learn.poll = setInterval(function () {
      if (learn.gen !== gen) { return; }     // stale closure: never advance
      if (step.done()) {
        _learnClearTimers();
        learnNext();
      }
    }, LEARN_POLL_MS);
    if (step.go) {
      var doIt = el("button", { class: "btn btn-ghost btn-small",
                                type: "button",
                                text: t("learn.doForMe") });
      doIt.addEventListener("click", function () { step.go(); });
      actions.appendChild(doIt);
    }
  } else {
    var next = el("button", { class: "btn btn-primary btn-small",
                              type: "button",
                              text: learn.idx + 1 >= learn.chapter.steps.length
                                    ? t("ui.done") : t("learn.next") });
    next.addEventListener("click", learnNext);
    actions.appendChild(next);
  }
  var stop = el("button", { class: "btn btn-ghost btn-small", type: "button",
                            text: t("learn.stop") });
  stop.addEventListener("click", function () {
    learnStop(); showTab("learn"); renderLearn();
  });
  actions.appendChild(stop);
  b.appendChild(actions);
}

/* ------------------------------------------------------------ launcher UI */

function renderLearn() {
  var box = $("learn-chapters");
  if (!box) { return; }
  box.textContent = "";
  var admin = isAdmin();
  LEARN_CHAPTERS.forEach(function (ch) {
    var locked = ch.admin && !admin;
    var card = el("div", { class: "card demo-launch-card" });
    var head = el("h3", { text: t("learn.ch." + ch.id + ".title") });
    card.appendChild(head);
    card.appendChild(el("p", { text: t("learn.ch." + ch.id + ".desc") }));
    card.appendChild(el("p", { class: "muted small",
      text: tf("learn.stepsCount", { n: ch.steps.length }) }));
    if (locked) {
      card.appendChild(el("p", { class: "banner-warn small",
                                 text: t("learn.adminOnly") }));
    } else {
      var row = el("div", { class: "form-row inline-controls" });
      var auto = el("button", { class: "btn btn-primary", type: "button",
                                text: t("learn.mode.auto") });
      auto.addEventListener("click", function () {
        learnStart(ch.id, "auto");
      });
      var inter = el("button", { class: "btn btn-ghost", type: "button",
                                 text: t("learn.mode.inter") });
      inter.addEventListener("click", function () {
        learnStart(ch.id, "inter");
      });
      row.appendChild(auto);
      row.appendChild(inter);
      card.appendChild(row);
    }
    box.appendChild(card);
  });
}

document.addEventListener("crew:lang", function () {
  renderLearn();
  if (learnActive()) { learnRenderStep(); }
});
