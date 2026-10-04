/* Demos tab (loads after i18n.js, app.js, onboarding.js and reuses their
 * globals: t, tf, $, el, api, toast, fmt, fmtBytes, verdictBadge).
 *
 * Two scenarios rendered inside #demo-root of #panel-demo:
 *   1. "pipeline"   — a six-step replay of a real month's end-to-end run
 *                     (input -> validation -> stage 1 -> gate -> stage 2 ->
 *                     results), read from GET /api/demo/pipeline;
 *   2. "comparison" — measured solver-set vs single-solver numbers and the
 *                     positioning vs GENCOL/Jeppesen, read from
 *                     GET /api/demo/comparison.
 *
 * Both payloads are cached in demoState after the first fetch; nothing is
 * recomputed on the server — the endpoints replay stored artifacts. Every
 * scalar may be null (the artifacts evolve), so all renders guard through
 * fmt() and skip whole blocks when their object is missing. */
"use strict";

/* ------------------------------------------------------------------ state */

var demoState = { pipeline: null, comparison: null, view: "launcher", step: 0 };

var DEMO_STEP_COUNT = 6;

/* ---------------------------------------------------------------- helpers */

/** el() twin for SVG: elements need the SVG namespace and setAttribute. */
function svgEl(tag, attrs, children) {
  var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      if (k === "text") { node.textContent = attrs[k]; }
      else { node.setAttribute(k, attrs[k]); }
    });
  }
  (children || []).forEach(function (c) { node.appendChild(c); });
  return node;
}

/** Append a dt/dd pair to a .demo-kv definition list. */
function demoKvRow(dl, label, value, ddClass) {
  dl.appendChild(el("dt", { text: label }));
  var attrs = { text: fmt(value) };
  if (ddClass) { attrs.class = ddClass; }
  dl.appendChild(el("dd", attrs));
}

/** Same, but the dd wraps a ready-made element (e.g. a verdict badge). */
function demoKvRowNode(dl, label, node) {
  dl.appendChild(el("dt", { text: label }));
  dl.appendChild(el("dd", null, [node]));
}

/** Raw-key variant: the dt shows a technical field name in mono. */
function demoKvRowRaw(dl, key, value) {
  dl.appendChild(el("dt", { class: "mono", text: key }));
  dl.appendChild(el("dd", { text: fmt(value) }));
}

/** .table-wrap > table.data-table from ready-made th cells and tr rows. */
function demoDataTable(headCells, bodyRows) {
  var table = el("table", { class: "data-table" }, [
    el("thead", null, [el("tr", null, headCells)]),
    el("tbody", null, bodyRows)
  ]);
  return el("div", { class: "table-wrap" }, [table]);
}

function demoTh(text, cls) {
  var attrs = { text: text };
  if (cls) { attrs.class = cls; }
  return el("th", attrs);
}

function demoTd(text, cls) {
  var attrs = { text: text };
  if (cls) { attrs.class = cls; }
  return el("td", attrs);
}

/** Human text for a stage-1 optimality gap: proven / measured / no cert. */
function demoGapText(stage1) {
  if (!stage1) { return "—"; }
  if (stage1.lp_bound_valid && stage1.gap_vs_lp_pct === 0) {
    return t("dc.proven");
  }
  if (typeof stage1.gap_vs_lp_pct === "number" && isFinite(stage1.gap_vs_lp_pct)) {
    return stage1.gap_vs_lp_pct + "%";
  }
  return t("dc.noCert");
}

function demoIsNum(v) { return typeof v === "number" && isFinite(v); }

/* ------------------------------------------------------------- navigation */

function renderDemo() {
  var root = $("demo-root");
  if (!root) { return; }
  root.textContent = "";
  if (demoState.view === "pipeline" && demoState.pipeline) {
    renderDemoPipeline(root);
  } else if (demoState.view === "comparison" && demoState.comparison) {
    renderDemoComparison(root);
  } else if (demoState.view === "evolution") {
    renderDemoEvolution(root);
  } else if (demoState.view === "advisor") {
    renderDemoAdvisor(root);
  } else if (DEMO_STORIES[demoState.view]) {
    renderDemoStory(root, demoState.view);
  } else {
    demoState.view = "launcher";
    renderDemoLauncher(root);
  }
}

/** Open a view; fetch its payload once, cache it, show a loading note
 *  meanwhile, and fall back to the launcher on error. */
function openDemoView(view) {
  if (view === "evolution" || view === "advisor" || DEMO_STORIES[view]) {
    // Storyboards: narrative from i18n, no artifact fetch.
    demoState.view = view;
    demoState.step = 0;
    renderDemo();
    return;
  }
  if (demoState[view]) {
    demoState.view = view;
    demoState.step = 0;
    renderDemo();
    return;
  }
  var root = $("demo-root");
  root.textContent = "";
  root.appendChild(el("p", { class: "muted", text: t("ui.loading") }));
  api("/demo/" + view).then(function (data) {
    demoState[view] = data || {};
    demoState.view = view;
    demoState.step = 0;
    renderDemo();
  }).catch(function (err) {
    toast(t("demo.loadFail") + ": " + err.message, "error");
    demoState.view = "launcher";
    renderDemo();
  });
}

function demoBackButton() {
  var btn = el("button", {
    type: "button", class: "btn btn-ghost btn-small", text: t("ui.backToList")
  });
  btn.addEventListener("click", function () {
    demoState.view = "launcher";
    renderDemo();
  });
  return btn;
}

/* ------------------------------------- data-driven storyboards (demos 5-8) */

/* Each story step is declarative: `feed` renders the evolution-style event
 * list, `flow` a chip arrow-flow (key holds "a → b → c"), `note` a muted
 * footnote, `kv` a definition list of [labelKey, valueKey] pairs. All text
 * comes from i18n under the story's key prefix — no artifact fetches. */
var DEMO_STORIES = {
  costing: { prefix: "dco", steps: [
    { flow: "dco.s1.flow",
      feed: [{ icon: "🏨", kind: "tool", key: "dco.s1.e1" },
             { icon: "🧾", kind: "bench", key: "dco.s1.e2" },
             { icon: "💰", kind: "finding", key: "dco.s1.e3" }] },
    { kv: [["dco.s2.k1", "dco.s2.v1"], ["dco.s2.k2", "dco.s2.v2"],
           ["dco.s2.k3", "dco.s2.v3"], ["dco.s2.k4", "dco.s2.v4"]],
      note: "dco.s2.note" },
    { flow: "dco.s3.flow",
      feed: [{ icon: "🧪", kind: "search", key: "dco.s3.e1" },
             { icon: "⚖", kind: "note", key: "dco.s3.e2" },
             { icon: "✅", kind: "done", key: "dco.s3.e3" }] },
    { feed: [{ icon: "📉", kind: "bench", key: "dco.s4.e1" },
             { icon: "🔁", kind: "tool", key: "dco.s4.e2" },
             { icon: "💵", kind: "finding", key: "dco.s4.e3" }],
      note: "dco.s4.note" }
  ]},
  board: { prefix: "db", steps: [
    { feed: [{ icon: "📆", kind: "search", key: "db.s1.e1" },
             { icon: "🧑‍✈️", kind: "tool", key: "db.s1.e2" },
             { icon: "📌", kind: "note", key: "db.s1.e3" }] },
    { flow: "db.s2.flow",
      feed: [{ icon: "🖱", kind: "tool", key: "db.s2.e1" },
             { icon: "🚫", kind: "bad", key: "db.s2.e2" },
             { icon: "✅", kind: "done", key: "db.s2.e3" }],
      note: "db.s2.note" },
    { feed: [{ icon: "⚖", kind: "bench", key: "db.s3.e1" },
             { icon: "🔁", kind: "tool", key: "db.s3.e2" },
             { icon: "💾", kind: "done", key: "db.s3.e3" }] }
  ]},
  cabinet: { prefix: "dk", steps: [
    { feed: [{ icon: "📱", kind: "search", key: "dk.s1.e1" },
             { icon: "🗓", kind: "tool", key: "dk.s1.e2" },
             { icon: "🔔", kind: "note", key: "dk.s1.e3" }] },
    { flow: "dk.s2.flow",
      feed: [{ icon: "🏖", kind: "tool", key: "dk.s2.e1" },
             { icon: "📨", kind: "bench", key: "dk.s2.e2" }] },
    { feed: [{ icon: "🧮", kind: "bench", key: "dk.s3.e1" },
             { icon: "🏅", kind: "finding", key: "dk.s3.e2" },
             { icon: "🛡", kind: "done", key: "dk.s3.e3" }],
      note: "dk.s3.note" }
  ]},
  disruption: { prefix: "ddz", steps: [
    { flow: "ddz.s1.flow",
      feed: [{ icon: "🤒", kind: "bad", key: "ddz.s1.e1" },
             { icon: "📋", kind: "search", key: "ddz.s1.e2" },
             { icon: "🔴", kind: "bad", key: "ddz.s1.e3" }] },
    { feed: [{ icon: "🛡", kind: "tool", key: "ddz.s2.e1" },
             { icon: "🧮", kind: "bench", key: "ddz.s2.e2" },
             { icon: "🔁", kind: "tool", key: "ddz.s2.e3" }],
      note: "ddz.s2.note" },
    { flow: "ddz.s3.flow",
      feed: [{ icon: "🎯", kind: "finding", key: "ddz.s3.e1" },
             { icon: "👥", kind: "bench", key: "ddz.s3.e2" },
             { icon: "⚠️", kind: "note", key: "ddz.s3.e3" }] },
    { feed: [{ icon: "✅", kind: "done", key: "ddz.s4.e1" },
             { icon: "↩️", kind: "tool", key: "ddz.s4.e2" },
             { icon: "📈", kind: "note", key: "ddz.s4.e3" }],
      note: "ddz.s4.note" },
    { flow: "ddz.s5.flow",
      feed: [{ icon: "🛬", kind: "bad", key: "ddz.s5.e1" },
             { icon: "🧩", kind: "search", key: "ddz.s5.e2" },
             { icon: "🧮", kind: "bench", key: "ddz.s5.e3" }] },
    { feed: [{ icon: "📌", kind: "tool", key: "ddz.s6.e1" },
             { icon: "🤖", kind: "bench", key: "ddz.s6.e2" },
             { icon: "📊", kind: "done", key: "ddz.s6.e3" }],
      note: "ddz.s6.note" },
    { flow: "ddz.s7.flow",
      feed: [{ icon: "🧊", kind: "search", key: "ddz.s7.e1" },
             { icon: "🔁", kind: "tool", key: "ddz.s7.e2" },
             { icon: "✅", kind: "done", key: "ddz.s7.e3" }],
      note: "ddz.s7.note" }
  ]},
  // 2026-07-26: круг с системой действующей авиакомпании — от её выгрузки
  // до решения в её же формате и сверки по её же меркам
  vendor: { prefix: "dvn", steps: [
    { flow: "dvn.s1.flow",
      feed: [{ icon: "📥", kind: "tool", key: "dvn.s1.e1" },
             { icon: "🔗", kind: "note", key: "dvn.s1.e2" },
             { icon: "🧩", kind: "finding", key: "dvn.s1.e3" }] },
    { feed: [{ icon: "🔍", kind: "search", key: "dvn.s2.e1" },
             { icon: "⚖", kind: "bench", key: "dvn.s2.e2" },
             { icon: "🚧", kind: "note", key: "dvn.s2.e3" }],
      note: "dvn.s2.note" },
    { flow: "dvn.s3.flow",
      feed: [{ icon: "🛠", kind: "tool", key: "dvn.s3.e1" },
             { icon: "📤", kind: "done", key: "dvn.s3.e2" },
             { icon: "🔄", kind: "bench", key: "dvn.s3.e3" }] },
    { kv: [["dvn.s4.k1", "dvn.s4.v1"], ["dvn.s4.k2", "dvn.s4.v2"],
           ["dvn.s4.k3", "dvn.s4.v3"]],
      feed: [{ icon: "📊", kind: "bench", key: "dvn.s4.e1" },
             { icon: "📋", kind: "finding", key: "dvn.s4.e2" }],
      note: "dvn.s4.note" }
  ] },
  // 2026-08-24: двухфазный поток заказчика и сравнение способов —
  // все числа из живых машинных выдач кнопки на мире «Полярис»
  // (нано run-0042, мини run-0045; этап Г, протокол приёмки).
  twophase: { prefix: "dtp", steps: [
    { flow: "dtp.s1.flow",
      feed: [{ icon: "📄", kind: "search", key: "dtp.s1.e1" },
             { icon: "🧩", kind: "tool", key: "dtp.s1.e2" },
             { icon: "✅", kind: "done", key: "dtp.s1.e3" }] },
    { flow: "dtp.s2.flow",
      feed: [{ icon: "🔒", kind: "tool", key: "dtp.s2.e1" },
             { icon: "🧾", kind: "bench", key: "dtp.s2.e2" },
             { icon: "✅", kind: "done", key: "dtp.s2.e3" }],
      note: "dtp.s2.note" },
    { kv: [["dtp.s3.k1", "dtp.s3.v1"], ["dtp.s3.k2", "dtp.s3.v2"],
           ["dtp.s3.k3", "dtp.s3.v3"], ["dtp.s3.k4", "dtp.s3.v4"]],
      note: "dtp.s3.note" },
    { feed: [{ icon: "🐞", kind: "bad", key: "dtp.s4.e1" },
             { icon: "🔍", kind: "search", key: "dtp.s4.e2" },
             { icon: "✅", kind: "done", key: "dtp.s4.e3" }],
      note: "dtp.s4.note" }
  ]},
  crpipe: { prefix: "dcr", steps: [
    { flow: "dcr.s1.flow",
      feed: [{ icon: "💬", kind: "search", key: "dcr.s1.e1" },
             { icon: "🤖", kind: "tool", key: "dcr.s1.e2" },
             { icon: "🗂", kind: "finding", key: "dcr.s1.e3" }] },
    { feed: [{ icon: "🌍", kind: "search", key: "dcr.s2.e1" },
             { icon: "🎨", kind: "search", key: "dcr.s2.e2" },
             { icon: "🧑‍⚖️", kind: "note", key: "dcr.s2.e3" }],
      note: "dcr.s2.note" },
    { flow: "dcr.s3.flow",
      feed: [{ icon: "🌿", kind: "tool", key: "dcr.s3.e1" },
             { icon: "🧪", kind: "bench", key: "dcr.s3.e2" },
             { icon: "🛡", kind: "bad", key: "dcr.s3.e3" },
             { icon: "✅", kind: "done", key: "dcr.s3.e4" }],
      note: "dcr.s3.note" }
  ]},
  // Unified Автоулучшение tour: all five improvement sources + the KB and
  // the human-gated implementer/pipeline (user request 2026-07-16). The
  // per-agent demos (evolution, advisor, crpipe) stay; this ties them
  // together and adds the new external-audit inbox.
  autoimprove: { prefix: "dai", steps: [
    { flow: "dai.s1.flow",
      feed: [{ icon: "🧭", kind: "search", key: "dai.s1.e1" },
             { icon: "🗂", kind: "finding", key: "dai.s1.e2" },
             { icon: "🧑‍✈️", kind: "note", key: "dai.s1.e3" }],
      note: "dai.s1.note" },
    { flow: "dai.s2.flow",
      feed: [{ icon: "🔬", kind: "search", key: "dai.s2.e1" },
             { icon: "📚", kind: "bench", key: "dai.s2.e2" },
             { icon: "💡", kind: "finding", key: "dai.s2.e3" }] },
    { flow: "dai.s3.flow",
      feed: [{ icon: "🌍", kind: "search", key: "dai.s3.e1" },
             { icon: "⚖️", kind: "tool", key: "dai.s3.e2" },
             { icon: "🗂", kind: "finding", key: "dai.s3.e3" }] },
    { flow: "dai.s4.flow",
      feed: [{ icon: "💬", kind: "search", key: "dai.s4.e1" },
             { icon: "🧲", kind: "tool", key: "dai.s4.e2" },
             { icon: "🗂", kind: "finding", key: "dai.s4.e3" }] },
    { flow: "dai.s5.flow",
      feed: [{ icon: "🎨", kind: "search", key: "dai.s5.e1" },
             { icon: "🧑‍🔧", kind: "tool", key: "dai.s5.e2" },
             { icon: "🗂", kind: "finding", key: "dai.s5.e3" }] },
    { flow: "dai.s6.flow",
      feed: [{ icon: "🕵️", kind: "search", key: "dai.s6.e1" },
             { icon: "🤖", kind: "tool", key: "dai.s6.e2" },
             { icon: "🗂", kind: "finding", key: "dai.s6.e3" }],
      note: "dai.s6.note" },
    { flow: "dai.s7.flow",
      feed: [{ icon: "📖", kind: "search", key: "dai.s7.e1" },
             { icon: "🧠", kind: "bench", key: "dai.s7.e2" },
             { icon: "✅", kind: "done", key: "dai.s7.e3" }] },
    { flow: "dai.s8.flow",
      feed: [{ icon: "🌿", kind: "tool", key: "dai.s8.e1" },
             { icon: "🧪", kind: "bench", key: "dai.s8.e2" },
             { icon: "🚦", kind: "note", key: "dai.s8.e3" },
             { icon: "🚀", kind: "done", key: "dai.s8.e4" }],
      note: "dai.s8.note" }
  ]}
};

function renderDemoStory(root, view) {
  var story = DEMO_STORIES[view];
  var count = story.steps.length;
  root.appendChild(demoBackButton());
  root.appendChild(el("p", { class: "muted",
                             text: t(story.prefix + ".intro") }));

  var nav = el("div", { class: "demo-steps-nav" });
  var prevBtn = el("button", { type: "button", class: "btn btn-ghost",
                               text: t("ui.back") });
  prevBtn.disabled = demoState.step === 0;
  prevBtn.addEventListener("click", function () {
    if (demoState.step > 0) { demoState.step -= 1; renderDemo(); }
  });
  nav.appendChild(prevBtn);
  var i;
  for (i = 0; i < count; i += 1) {
    (function (idx) {
      var dot = el("button", {
        type: "button",
        class: "demo-dot" + (idx === demoState.step ? " active" : ""),
        "aria-label": t("ui.step") + " " + (idx + 1)
      });
      dot.addEventListener("click", function () {
        demoState.step = idx; renderDemo();
      });
      nav.appendChild(dot);
    })(i);
  }
  var nextBtn = el("button", { type: "button", class: "btn btn-primary",
                               text: t("ui.next") });
  nextBtn.disabled = demoState.step === count - 1;
  nextBtn.addEventListener("click", function () {
    if (demoState.step < count - 1) { demoState.step += 1; renderDemo(); }
  });
  nav.appendChild(nextBtn);
  nav.appendChild(el("span", {
    class: "demo-step-counter",
    text: t("ui.step") + " " + (demoState.step + 1) + " " + t("ui.of") +
          " " + count
  }));
  root.appendChild(nav);

  var idx2 = Math.min(demoState.step, count - 1);
  var spec = story.steps[idx2];
  var stepKey = story.prefix + ".s" + (idx2 + 1);
  var card = el("div", { class: "card" }, [
    el("h3", { text: t(stepKey + ".title") }),
    el("p", { text: t(stepKey + ".body") })
  ]);
  if (spec.flow) { card.appendChild(demoEvoChipFlow(t(spec.flow))); }
  if (spec.kv) {
    var dl = el("dl", { class: "demo-kv" });
    spec.kv.forEach(function (pair) {
      demoKvRow(dl, t(pair[0]), t(pair[1]));
    });
    card.appendChild(dl);
  }
  if (spec.feed) { card.appendChild(demoEvoFeed(spec.feed)); }
  if (spec.note) {
    card.appendChild(el("p", { class: "muted small", text: t(spec.note) }));
  }
  root.appendChild(card);
}

/* ----------------------------------------------------------- view: launcher */

function renderDemoLauncher(root) {
  var launcher = el("div", { class: "demo-launcher" });
  [
    { title: "demo.launch1.title", body: "demo.launch1.body", view: "pipeline" },
    { title: "demo.launch2.title", body: "demo.launch2.body", view: "comparison" },
    { title: "demo.launch3.title", body: "demo.launch3.body", view: "evolution" },
    { title: "demo.launch4.title", body: "demo.launch4.body", view: "advisor" },
    { title: "demo.launch5.title", body: "demo.launch5.body", view: "costing" },
    { title: "demo.launch6.title", body: "demo.launch6.body", view: "board" },
    { title: "demo.launch7.title", body: "demo.launch7.body", view: "cabinet" },
    { title: "demo.launch8.title", body: "demo.launch8.body", view: "crpipe" },
    { title: "demo.launch9.title", body: "demo.launch9.body", view: "disruption" },
    { title: "demo.launch10.title", body: "demo.launch10.body", view: "autoimprove" },
    { title: "demo.launch11.title", body: "demo.launch11.body", view: "vendor" },
    { title: "demo.launch12.title", body: "demo.launch12.body", view: "twophase" }
  ].forEach(function (spec) {
    var openBtn = el("button", {
      type: "button", class: "btn btn-primary", text: t("ui.open")
    });
    openBtn.addEventListener("click", function () { openDemoView(spec.view); });
    launcher.appendChild(el("div", { class: "card demo-launch-card" }, [
      el("h3", { text: t(spec.title) }),
      el("p", { text: t(spec.body) }),
      openBtn
    ]));
  });
  root.appendChild(launcher);
}

/* ------------------------------------------- view: self-improvement (Lab) */

var EVO_STEP_COUNT = 4;

function demoEvoFeed(events) {
  var ul = el("ul", { class: "demo-evo-feed" });
  events.forEach(function (ev) {
    ul.appendChild(el("li", { class: "demo-evo-ev demo-evo-" + ev.kind }, [
      el("span", { class: "demo-evo-icon", text: ev.icon }),
      el("span", { text: t(ev.key) })
    ]));
  });
  return ul;
}

function demoEvoChipFlow(text) {
  var row = el("div", { class: "demo-evo-flow" });
  // RTL translations write the flow with "←"; split on either arrow and
  // rejoin with the direction the page actually renders in.
  var arrow = document.documentElement.getAttribute("dir") === "rtl"
    ? "←" : "→";
  text.split(/\s*[→←]\s*/).forEach(function (part, i) {
    if (i > 0) { row.appendChild(el("span", { class: "demo-evo-arrow", text: arrow })); }
    row.appendChild(el("span", { class: "chip chip-neutral", text: part.trim() }));
  });
  return row;
}

function demoEvoContours() {
  var wrap = el("div", { class: "demo-evo-contours" });
  [{ k: "de.s4.test", cls: "ext", n: "Test" },
   { k: "de.s4.stage", cls: "int", n: "Stage" },
   { k: "de.s4.prod", cls: "int", n: "Prod" }
  ].forEach(function (c, i) {
    if (i > 0) { wrap.appendChild(el("span", { class: "demo-evo-arrow", text: "→" })); }
    wrap.appendChild(el("div", { class: "demo-evo-contour " + c.cls }, [
      el("div", { class: "demo-evo-cname", text: c.n }),
      el("div", { class: "demo-evo-cbody small", text: t(c.k) })
    ]));
  });
  return wrap;
}

function evoStep1() {
  return [demoEvoFeed([
    { icon: "🔎", kind: "search", key: "de.s1.e1" },
    { icon: "🔎", kind: "search", key: "de.s1.e2" },
    { icon: "🌐", kind: "fetch", key: "de.s1.e3" },
    { icon: "🌐", kind: "fetch", key: "de.s1.e4" },
    { icon: "✨", kind: "finding", key: "de.s1.e5" }
  ])];
}
function evoStep2() {
  return [demoEvoFeed([
    { icon: "📊", kind: "bench", key: "de.s2.e1" },
    { icon: "🛠", kind: "tool", key: "de.s2.e2" },
    { icon: "📊", kind: "bench", key: "de.s2.e3" },
    { icon: "✅", kind: "done", key: "de.s2.e4" },
    { icon: "📊", kind: "bench", key: "de.s2.e5" },
    { icon: "⚖", kind: "note", key: "de.s2.e6" }
  ])];
}
function evoStep3() {
  return [demoEvoChipFlow(t("de.s3.flow")),
          el("p", { class: "muted small", text: t("de.s3.note") })];
}
function evoStep4() {
  return [demoEvoContours()];
}

function renderDemoEvolution(root) {
  root.appendChild(demoBackButton());
  root.appendChild(el("p", { class: "muted", text: t("de.intro") }));

  var nav = el("div", { class: "demo-steps-nav" });
  var prevBtn = el("button", { type: "button", class: "btn btn-ghost", text: t("ui.back") });
  prevBtn.disabled = demoState.step === 0;
  prevBtn.addEventListener("click", function () {
    if (demoState.step > 0) { demoState.step -= 1; renderDemo(); }
  });
  nav.appendChild(prevBtn);
  var i;
  for (i = 0; i < EVO_STEP_COUNT; i += 1) {
    (function (idx) {
      var dot = el("button", {
        type: "button",
        class: "demo-dot" + (idx === demoState.step ? " active" : ""),
        "aria-label": t("ui.step") + " " + (idx + 1)
      });
      dot.addEventListener("click", function () { demoState.step = idx; renderDemo(); });
      nav.appendChild(dot);
    })(i);
  }
  var nextBtn = el("button", { type: "button", class: "btn btn-primary", text: t("ui.next") });
  nextBtn.disabled = demoState.step === EVO_STEP_COUNT - 1;
  nextBtn.addEventListener("click", function () {
    if (demoState.step < EVO_STEP_COUNT - 1) { demoState.step += 1; renderDemo(); }
  });
  nav.appendChild(nextBtn);
  nav.appendChild(el("span", {
    class: "demo-step-counter",
    text: t("ui.step") + " " + (demoState.step + 1) + " " + t("ui.of") + " " + EVO_STEP_COUNT
  }));
  root.appendChild(nav);

  var builders = [evoStep1, evoStep2, evoStep3, evoStep4];
  var idx = Math.min(demoState.step, EVO_STEP_COUNT - 1);
  var stepKey = "de.s" + (idx + 1);
  var card = el("div", { class: "card" }, [
    el("h3", { text: t(stepKey + ".title") }),
    el("p", { text: t(stepKey + ".body") })
  ]);
  builders[idx]().forEach(function (node) { card.appendChild(node); });
  root.appendChild(card);
}

/* ------------------------------------ view: knowledge base + solver advisor */

var ADV_STEP_COUNT = 3;

/** Rounded box with centered multi-line text (SVG has no text wrapping). */
function daNode(x, y, w, h, lines, cls) {
  var g = svgEl("g", { class: "da-node " + (cls || "") });
  g.appendChild(svgEl("rect", { x: x, y: y, width: w, height: h, rx: 8 }));
  var lineH = 15;
  var startY = y + h / 2 - ((lines.length - 1) * lineH) / 2 + 4;
  lines.forEach(function (ln, i) {
    g.appendChild(svgEl("text", {
      x: x + w / 2, y: startY + i * lineH, "text-anchor": "middle", text: ln
    }));
  });
  return g;
}

function daArrow(x1, y1, x2, y2, dashed) {
  return svgEl("line", {
    class: "da-arrow" + (dashed ? " dashed" : ""),
    x1: x1, y1: y1, x2: x2, y2: y2, "marker-end": "url(#da-arr)"
  });
}

/** Wrap an i18n string into <=n-char lines on word boundaries (SVG text). */
function daWrap(key, n) {
  var words = t(key).split(" ");
  var lines = [], cur = "";
  words.forEach(function (w) {
    if (cur && (cur + " " + w).length > n) { lines.push(cur); cur = w; }
    else { cur = cur ? cur + " " + w : w; }
  });
  if (cur) { lines.push(cur); }
  return lines;
}

/** The contour-separation diagram: external (Test, cloud model builds the
 *  knowledge base from public benchmarks) vs internal (Stage/Prod, the local
 *  model consults the same local document; L datasets never leave). */
function daDiagram() {
  var W = 960, H = 500;
  var svg = svgEl("svg", {
    class: "da-diagram", viewBox: "0 0 " + W + " " + H,
    xmlns: "http://www.w3.org/2000/svg", role: "img",
    "aria-label": t("da.s1.title")
  });
  var defs = svgEl("defs");
  var marker = svgEl("marker", {
    id: "da-arr", viewBox: "0 0 10 10", refX: 9, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"
  });
  marker.appendChild(svgEl("path", { d: "M 0 0 L 10 5 L 0 10 z" }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  // --- external contour (left) ---
  var ext = svgEl("g", { class: "da-contour ext" });
  ext.appendChild(svgEl("rect", { x: 16, y: 40, width: 400, height: 300, rx: 12 }));
  ext.appendChild(svgEl("text", { class: "da-ctitle", x: 36, y: 68, text: t("da.d.extTitle") }));
  ext.appendChild(svgEl("text", { class: "da-csub", x: 36, y: 88, text: t("da.d.test") }));
  svg.appendChild(ext);
  svg.appendChild(daNode(40, 110, 160, 62, daWrap("da.d.bench", 18)));
  svg.appendChild(daNode(232, 110, 160, 62, daWrap("da.d.cloudModel", 18), "da-model"));
  svg.appendChild(daArrow(200, 141, 230, 141));
  svg.appendChild(daNode(126, 240, 180, 62, daWrap("da.d.kbDoc", 20), "da-kb"));
  svg.appendChild(daArrow(312, 174, 240, 238));

  // --- internal contour (right) ---
  var int_ = svgEl("g", { class: "da-contour int" });
  int_.appendChild(svgEl("rect", { x: 520, y: 40, width: 424, height: 444, rx: 12 }));
  int_.appendChild(svgEl("text", { class: "da-ctitle", x: 540, y: 68, text: t("da.d.intTitle") }));
  int_.appendChild(svgEl("text", { class: "da-csub", x: 540, y: 88, text: t("da.d.stageprod") }));
  svg.appendChild(int_);
  svg.appendChild(daNode(544, 110, 180, 62, daWrap("da.d.kbLocal", 20), "da-kb"));
  svg.appendChild(daNode(760, 110, 160, 62, daWrap("da.d.localModel", 16), "da-model"));
  // KB copy travels as a FILE with the ordinary deploy — not data.
  svg.appendChild(daArrow(308, 271, 542, 141, true));
  svg.appendChild(svgEl("text", {
    class: "da-lbl", x: 428, y: 196, "text-anchor": "middle",
    text: t("da.d.deployArrow")
  }));
  // dataset chips: L stays inside; C may also go to the cloud model.
  svg.appendChild(daNode(544, 208, 180, 54, daWrap("da.d.dataL", 20), "da-data-l"));
  svg.appendChild(daNode(544, 278, 180, 54, daWrap("da.d.dataC", 20), "da-data-c"));
  // C -> cloud (dashed, back to the external model): descriptor only.
  svg.appendChild(daArrow(544, 300, 320, 176, true));
  svg.appendChild(svgEl("text", {
    class: "da-lbl", x: 420, y: 268, "text-anchor": "middle",
    text: t("da.d.cOut")
  }));
  // descriptor -> local model; KB -> local model
  svg.appendChild(daNode(760, 208, 160, 54, daWrap("da.d.descriptor", 16), "da-desc"));
  svg.appendChild(daArrow(726, 235, 758, 235));
  svg.appendChild(daArrow(840, 174, 840, 206));
  svg.appendChild(daArrow(724, 141, 758, 141));
  // model -> recommendation -> human confirms
  svg.appendChild(daNode(760, 300, 160, 54, daWrap("da.d.recommendation", 16), "da-rec"));
  svg.appendChild(daArrow(840, 264, 840, 298));
  svg.appendChild(daNode(620, 396, 240, 62, daWrap("da.d.human", 24), "da-human"));
  svg.appendChild(daArrow(830, 356, 780, 394));
  return svg;
}

function advStep1() {
  return [el("div", { class: "da-diagram-box" }, [daDiagram()]),
          el("p", { class: "muted small", text: t("da.s1.note") })];
}

function advStep2() {
  var chips = el("div", { class: "da-scope-cards cards-grid" }, [
    el("div", { class: "card" }, [
      el("p", null, [el("span", { class: "chip chip-warn",
                                  text: t("app.advisor.scope.local") })]),
      el("p", { class: "small", text: t("da.s2.l") })
    ]),
    el("div", { class: "card" }, [
      el("p", null, [el("span", { class: "chip chip-neutral",
                                  text: t("app.advisor.scope.any") })]),
      el("p", { class: "small", text: t("da.s2.c") })
    ])
  ]);
  var desc = el("pre", { class: "mono small da-desc-sample",
    text: '{\n "source": "gerad", "legs": 1013, "employees": 173,\n' +
          ' "n_airports": 30, "n_home_bases": 3,\n' +
          ' "horizon": {"from": "2007-01-01", "to": "2007-01-31"},\n' +
          ' "rules": {"min_sit_min": 30, "max_landings_per_duty": 5}\n}' });
  return [chips,
          el("p", { class: "muted", text: t("da.s2.desc") }),
          desc];
}

function advStep3() {
  var kv = el("dl", { class: "demo-kv" });
  demoKvRow(kv, t("app.advisor.stage1"), t("da.s3.m.stage1"));
  demoKvRow(kv, t("app.advisor.stage2"), t("da.s3.m.stage2"));
  demoKvRow(kv, t("app.advisor.params"),
            "pricer=duty · mp=10 · budget_s=600 · seed=7");
  demoKvRow(kv, t("app.advisor.warnings"), t("da.s3.m.warn"));
  var apply = el("button", { class: "btn btn-primary btn-small", type: "button",
                             text: t("app.advisor.apply") });
  apply.addEventListener("click", function () {
    toast(t("da.s3.toast"), "ok");
  });
  return [kv,
          el("div", { class: "advisor-actions" }, [apply]),
          el("p", { class: "muted small", text: t("da.s3.note") })];
}

function renderDemoAdvisor(root) {
  root.appendChild(demoBackButton());
  root.appendChild(el("p", { class: "muted", text: t("da.intro") }));

  var nav = el("div", { class: "demo-steps-nav" });
  var prevBtn = el("button", { type: "button", class: "btn btn-ghost", text: t("ui.back") });
  prevBtn.disabled = demoState.step === 0;
  prevBtn.addEventListener("click", function () {
    if (demoState.step > 0) { demoState.step -= 1; renderDemo(); }
  });
  nav.appendChild(prevBtn);
  var i;
  for (i = 0; i < ADV_STEP_COUNT; i += 1) {
    (function (idx) {
      var dot = el("button", {
        type: "button",
        class: "demo-dot" + (idx === demoState.step ? " active" : ""),
        "aria-label": t("ui.step") + " " + (idx + 1)
      });
      dot.addEventListener("click", function () { demoState.step = idx; renderDemo(); });
      nav.appendChild(dot);
    })(i);
  }
  var nextBtn = el("button", { type: "button", class: "btn btn-primary", text: t("ui.next") });
  nextBtn.disabled = demoState.step === ADV_STEP_COUNT - 1;
  nextBtn.addEventListener("click", function () {
    if (demoState.step < ADV_STEP_COUNT - 1) { demoState.step += 1; renderDemo(); }
  });
  nav.appendChild(nextBtn);
  nav.appendChild(el("span", {
    class: "demo-step-counter",
    text: t("ui.step") + " " + (demoState.step + 1) + " " + t("ui.of") + " " + ADV_STEP_COUNT
  }));
  root.appendChild(nav);

  var builders = [advStep1, advStep2, advStep3];
  var idx = Math.min(demoState.step, ADV_STEP_COUNT - 1);
  var stepKey = "da.s" + (idx + 1);
  var card = el("div", { class: "card" }, [
    el("h3", { text: t(stepKey + ".title") }),
    el("p", { text: t(stepKey + ".body") })
  ]);
  builders[idx]().forEach(function (node) { card.appendChild(node); });
  root.appendChild(card);
}

/* ----------------------------------------------------- view: pipeline replay */

function renderDemoPipeline(root) {
  var data = demoState.pipeline;
  root.appendChild(demoBackButton());
  root.appendChild(el("p", { class: "muted", text: t("dp.intro") }));

  var nav = el("div", { class: "demo-steps-nav" });

  var prevBtn = el("button", {
    type: "button", class: "btn btn-ghost", text: t("ui.back")
  });
  prevBtn.disabled = demoState.step === 0;
  prevBtn.addEventListener("click", function () {
    if (demoState.step > 0) { demoState.step -= 1; renderDemo(); }
  });
  nav.appendChild(prevBtn);

  var i;
  for (i = 0; i < DEMO_STEP_COUNT; i += 1) {
    (function (idx) {
      var dot = el("button", {
        type: "button",
        class: "demo-dot" + (idx === demoState.step ? " active" : ""),
        "aria-label": t("ui.step") + " " + (idx + 1)
      });
      dot.addEventListener("click", function () {
        demoState.step = idx;
        renderDemo();
      });
      nav.appendChild(dot);
    })(i);
  }

  var nextBtn = el("button", {
    type: "button", class: "btn btn-primary", text: t("ui.next")
  });
  nextBtn.disabled = demoState.step === DEMO_STEP_COUNT - 1;
  nextBtn.addEventListener("click", function () {
    if (demoState.step < DEMO_STEP_COUNT - 1) { demoState.step += 1; renderDemo(); }
  });
  nav.appendChild(nextBtn);

  nav.appendChild(el("span", {
    class: "demo-step-counter",
    text: t("ui.step") + " " + (demoState.step + 1) + " " + t("ui.of") + " " +
          DEMO_STEP_COUNT
  }));

  root.appendChild(nav);

  var builders = [demoStep1, demoStep2, demoStep3, demoStep4, demoStep5, demoStep6];
  var stepKey = "dp.s" + (demoState.step + 1);
  var card = el("div", { class: "card" }, [
    el("h3", { text: t(stepKey + ".title") }),
    el("p", { text: t(stepKey + ".body") })
  ]);
  builders[demoState.step](data).forEach(function (node) {
    card.appendChild(node);
  });
  root.appendChild(card);
}

/* --- step 1: input data --------------------------------------------------- */

function demoStep1(data) {
  var input = data.input;
  var nodes = [];
  if (!input) { return nodes; }

  var kv = el("dl", { class: "demo-kv" });
  demoKvRow(kv, t("dp.flights"), input.flights_total);
  demoKvRow(kv, t("dp.employees"), input.employees_total);
  demoKvRow(kv, t("dp.airports"), input.airports_total);
  nodes.push(kv);

  if (Array.isArray(input.flights_sample) && input.flights_sample.length) {
    nodes.push(el("p", { class: "muted", text: t("dp.sampleFlights") }));
    nodes.push(demoDataTable(
      [
        demoTh(t("dp.th.flight")),
        demoTh(t("dp.th.from")),
        demoTh(t("dp.th.dep")),
        demoTh(t("dp.th.to")),
        demoTh(t("dp.th.arr"))
      ],
      input.flights_sample.map(function (f) {
        return el("tr", null, [
          demoTd(fmt(f.flightNumber), "mono"),
          demoTd(fmt(f.departureAirport), "mono"),
          demoTd(fmt(f.departureUTCDateTime)),
          demoTd(fmt(f.arrivalAirport), "mono"),
          demoTd(fmt(f.arrivalUTCDateTime))
        ]);
      })
    ));
  }

  if (Array.isArray(input.employees_sample) && input.employees_sample.length) {
    nodes.push(el("p", { class: "muted", text: t("dp.sampleEmployees") }));
    nodes.push(demoDataTable(
      [
        demoTh(t("dp.th.emp")),
        demoTh(t("dp.th.base")),
        demoTh(t("dp.th.skills"))
      ],
      input.employees_sample.map(function (e) {
        var skills = Array.isArray(e.skills) ? e.skills.join(", ") : fmt(e.skills);
        return el("tr", null, [
          demoTd(fmt(e.id), "mono"),
          demoTd(fmt(e.homeAirport), "mono"),
          demoTd(skills)
        ]);
      })
    ));
  }
  return nodes;
}

/* --- step 2: input validation --------------------------------------------- */

function demoStep2(data) {
  var v = data.validation;
  var nodes = [];
  if (!v) { return nodes; }

  nodes.push(el("p", null, [
    v.ok
      ? el("span", { class: "chip chip-ok", text: t("dp.valOk") })
      : el("span", { class: "chip chip-bad", text: t("dp.valFail") })
  ]));

  if (Array.isArray(v.errors) && v.errors.length) {
    nodes.push(el("ul", null, v.errors.map(function (msg) {
      return el("li", { class: "mono small", text: String(msg) });
    })));
  }

  if (v.stats && typeof v.stats === "object") {
    var kv = el("dl", { class: "demo-kv" });
    Object.keys(v.stats).forEach(function (key) {
      demoKvRowRaw(kv, key, v.stats[key]);
    });
    nodes.push(kv);
  }
  return nodes;
}

/* --- step 3: stage 1 (column generation) ----------------------------------- */

function demoStep3(data) {
  var s = data.stage1;
  if (!s) { return []; }
  var kv = el("dl", { class: "demo-kv" });
  demoKvRow(kv, t("dp.pool"), s.pool_size);
  demoKvRow(kv, t("dp.selected"), s.selected);
  demoKvRow(kv, t("dp.ip"), s.ip_objective);
  demoKvRow(kv, t("dp.lp"), s.lp_bound);
  demoKvRow(kv, t("dp.gap"), demoGapText(s));
  demoKvRow(kv, t("dp.wall"), s.wall_s);
  demoKvRow(kv, t("dp.status"), s.ip_status);
  return [kv];
}

/* --- step 4: quality gate --------------------------------------------------- */

function demoStep4(data) {
  var g = data.gate;
  var nodes = [];

  nodes.push(el("ul", { class: "gate-list" }, [
    el("li", { text: t("dp.gate.ok") }),
    el("li", { text: t("dp.gate.input") }),
    el("li", { text: t("dp.gate.incumbent") })
  ]));

  if (g) {
    var kv = el("dl", { class: "demo-kv" });
    demoKvRowNode(kv, t("dp.verdict"), verdictBadge(g.result_status));
    demoKvRow(kv, t("app.f.trajectory_violations"), g.trajectory_violations);
    demoKvRow(kv, t("app.f.missing_legs"), g.missing_legs);
    nodes.push(kv);
  }
  return nodes;
}

/* --- step 5: stage 2 (rostering) -------------------------------------------- */

function demoStep5(data) {
  var s = data.stage2;
  if (!s) { return []; }
  var kv = el("dl", { class: "demo-kv" });
  demoKvRow(kv, t("app.f.stage2_score"), s.stage2_score);
  demoKvRow(kv, t("app.f.pref_hits"), s.pref_hits);
  demoKvRow(kv, t("app.f.pref_attainable"), s.pref_attainable);
  demoKvRow(kv, t("app.f.vacation_violations"), s.vacation_violations);
  demoKvRow(kv, t("app.f.unassigned"), s.unassigned);
  demoKvRow(kv, t("app.f.wall_s"), s.wall_s);
  return [kv];
}

/* --- step 6: results ---------------------------------------------------------- */

function demoStep6(data) {
  var r = data.results;
  var nodes = [];
  if (!r) { return nodes; }

  if (r.roster && Array.isArray(r.roster.columns)) {
    nodes.push(demoDataTable(
      r.roster.columns.map(function (c) { return demoTh(String(c), "mono"); }),
      (Array.isArray(r.roster.rows) ? r.roster.rows : []).map(function (row) {
        return el("tr", null, row.map(function (cell) {
          return demoTd(fmt(cell));
        }));
      })
    ));
    nodes.push(el("p", {
      class: "muted",
      text: tf("dp.rosterRows", { n: fmt(r.roster.total_rows) })
    }));
  }

  if (Array.isArray(r.artifacts) && r.artifacts.length) {
    nodes.push(el("p", { class: "muted", text: t("dp.artifacts") }));
    nodes.push(el("ul", null, r.artifacts.map(function (a) {
      return el("li", null, [
        el("a", {
          href: "/api/artifacts/" + encodeURIComponent(a.name || ""),
          text: fmt(a.name)
        }),
        el("span", { class: "muted small", text: " — " + fmtBytes(a.bytes) })
      ]);
    })));
  }
  return nodes;
}

/* --------------------------------------------------- view: solver comparison */

function renderDemoComparison(root) {
  var c = demoState.comparison;
  root.appendChild(demoBackButton());
  root.appendChild(el("p", { class: "muted", text: t("dc.intro") }));

  var sections = [
    demoCompS1(c),
    demoCompS2(c),
    demoCompS3(c),
    demoCompS4(c),
    demoCompS5(c)
  ];
  // Which comparison axis each section belongs to, so the two questions —
  // "the solver set vs a single solver" and "vs a Jeppesen-class system" —
  // read explicitly, not implicitly.
  var axes = ["single", "single", "jeppesen", "scale", "jeppesen"];
  sections.forEach(function (node, i) {
    if (!node) { return; }
    var eb = el("div", { class: "dc-axis dc-axis-" + axes[i],
                         text: t("dc.axis." + axes[i]) });
    node.insertBefore(eb, node.firstChild);
    root.appendChild(node);
  });
}

/* --- S1: 29 classic pairing-selection benchmarks ---------------------------- */

function demoCompS1(c) {
  var spp = c.spp;
  if (!spp) { return null; }
  var tfSide = spp.tf || {};
  var ortSide = spp.ort || {};

  var grid = el("div", { class: "cards-grid" }, [
    el("div", { class: "card" }, [
      el("h3", { text: t("dc.s1.tf.h") }),
      el("ul", null, [
        el("li", { text: tf("dc.s1.tf.l1",
                            { f: fmt(tfSide.feasible), n: fmt(spp.instances) }) }),
        el("li", { text: tf("dc.s1.tf.l2",
                            { x: fmt(tfSide.median_excess_pct) }) })
      ])
    ]),
    el("div", { class: "card" }, [
      el("h3", { text: t("dc.s1.ort.h") }),
      el("ul", null, [
        el("li", { text: tf("dc.s1.ort.l1",
                            { o: fmt(ortSide.optimal), n: fmt(spp.instances) }) }),
        el("li", { text: tf("dc.s1.ort.l2", { s: fmt(ortSide.median_s) }) })
      ])
    ])
  ]);

  return el("div", { class: "card" }, [
    el("h3", { text: t("dc.s1.title") }),
    el("p", { text: t("dc.s1.body") }),
    grid
  ]);
}

/* --- S2: uncrewed-share chart + per-month table ------------------------------ */

/** Grouped bar chart: per month, direct vs pipeline unassigned percentage. */
function demoUnassignedChart(rows) {
  var W = 760, H = 260;
  var M = { left: 40, right: 10, top: 14, bottom: 26 };
  var plotW = W - M.left - M.right;
  var plotH = H - M.top - M.bottom;
  var yMax = 80;

  function yPos(pct) {
    var clamped = Math.max(0, Math.min(yMax, pct));
    return M.top + plotH - (clamped / yMax) * plotH;
  }

  var svg = svgEl("svg", {
    class: "demo-chart",
    viewBox: "0 0 " + W + " " + H,
    xmlns: "http://www.w3.org/2000/svg",
    role: "img",
    "aria-label": t("dc.s2.title")
  });

  // Y axis: gridlines and tick labels at 0/20/40/60/80 percent.
  var axis = svgEl("g", { class: "axis" });
  [0, 20, 40, 60, 80].forEach(function (tick) {
    var y = yPos(tick);
    axis.appendChild(svgEl("line", {
      x1: M.left, x2: M.left + plotW, y1: y, y2: y
    }));
    axis.appendChild(svgEl("text", {
      x: M.left - 6, y: y + 4, "text-anchor": "end", text: String(tick)
    }));
  });

  var n = rows.length;
  var groupW = plotW / n;
  var barW = Math.min(30, groupW * 0.32);
  var innerGap = 8;
  var pairW = 2 * barW + innerGap;
  var baseline = M.top + plotH;

  rows.forEach(function (row, i) {
    var startX = M.left + i * groupW + (groupW - pairW) / 2;
    var ro = row.rostering || {};
    var series = [
      { rec: ro.direct, cls: "bar-direct", x: startX },
      { rec: ro.two_stage, cls: "bar-two", x: startX + barW + innerGap }
    ];
    series.forEach(function (s) {
      var pct = s.rec ? s.rec.unassigned_pct : null;
      var cx = s.x + barW / 2;
      if (demoIsNum(pct)) {
        var y = yPos(pct);
        svg.appendChild(svgEl("rect", {
          class: s.cls, x: s.x, y: y, width: barW, height: baseline - y
        }));
        svg.appendChild(svgEl("text", {
          x: cx, y: y - 3, "text-anchor": "middle", text: pct.toFixed(1) + "%"
        }));
      } else {
        svg.appendChild(svgEl("text", {
          x: cx, y: baseline - 4, "text-anchor": "middle", text: "—"
        }));
      }
    });
    // Group label (month number) under the x axis.
    axis.appendChild(svgEl("text", {
      x: M.left + i * groupW + groupW / 2,
      y: baseline + 16,
      "text-anchor": "middle",
      text: String(row.inst !== null && row.inst !== undefined ? row.inst : i + 1)
    }));
  });

  svg.appendChild(axis);
  return svg;
}

/** "N (P%)" for an unassigned count with its share; dash when absent. */
function demoUnassignedText(rec) {
  if (!rec || rec.unassigned === null || rec.unassigned === undefined) {
    return "—";
  }
  var s = String(rec.unassigned);
  if (demoIsNum(rec.unassigned_pct)) {
    s += " (" + rec.unassigned_pct + "%)";
  }
  return s;
}

function demoPrefText(rec) {
  if (!rec || !demoIsNum(rec.pref_pct)) { return "—"; }
  return rec.pref_pct + "%";
}

function demoCompS2(c) {
  var rows = Array.isArray(c.gerad) ? c.gerad : [];
  if (!rows.length) { return null; }

  var legend = el("div", { class: "demo-legend" }, [
    el("span", null, [
      el("span", { class: "swatch direct" }),
      el("span", { text: " " + t("dc.s2.direct") })
    ]),
    el("span", null, [
      el("span", { class: "swatch two" }),
      el("span", { text: " " + t("dc.s2.pipeline") })
    ])
  ]);

  var chartBox = el("div", { class: "demo-chart-box" }, [
    demoUnassignedChart(rows)
  ]);

  var table = demoDataTable(
    [
      demoTh(t("dc.s2.month")),
      demoTh(t("dc.s2.th.direct")),
      demoTh(t("dc.s2.th.two")),
      demoTh(t("dc.s2.th.prefD")),
      demoTh(t("dc.s2.th.prefT")),
      demoTh(t("dc.s2.th.vacD")),
      demoTh(t("dc.s2.th.vacT"))
    ],
    rows.map(function (row) {
      var ro = row.rostering || {};
      var direct = ro.direct || null;
      var two = ro.two_stage || null;
      return el("tr", null, [
        demoTd(fmt(row.inst), "num"),
        demoTd(demoUnassignedText(direct), "num"),
        demoTd(demoUnassignedText(two), "num"),
        demoTd(demoPrefText(direct), "num"),
        demoTd(demoPrefText(two), "num"),
        demoTd(fmt(direct && direct.vacation_violations), "num"),
        demoTd(fmt(two && two.vacation_violations), "num")
      ]);
    })
  );

  return el("div", { class: "card" }, [
    el("h3", { text: t("dc.s2.title") }),
    el("p", { text: t("dc.s2.body") }),
    legend,
    chartBox,
    table
  ]);
}

/* --- S3: monthly structure vs GENCOL ----------------------------------------- */

function demoCompS3(c) {
  var rows = Array.isArray(c.gerad) ? c.gerad : [];
  if (!rows.length) { return null; }

  var table = demoDataTable(
    [
      demoTh(t("dc.s2.month")),
      demoTh(t("html.th.legs")),
      demoTh(t("dc.s3.th.our")),
      demoTh(t("dc.s3.th.ourGap")),
      demoTh(t("dc.s3.th.gencolGap")),
      demoTh(t("dc.s3.th.ourTime")),
      demoTh(t("dc.s3.th.gencolTime"))
    ],
    rows.map(function (row) {
      var s1 = row.stage1 || null;
      var g = row.gencol || null;
      var ourTime = (s1 && demoIsNum(s1.wall_s))
        ? (Math.round((s1.wall_s / 60) * 10) / 10) + " " + t("dc.min")
        : "—";
      var gencolGap = (g && demoIsNum(g.gap_pct)) ? g.gap_pct + "%" : "—";
      var gencolTime = (g && demoIsNum(g.pairing_cpu_min))
        ? g.pairing_cpu_min + " " + t("dc.min")
        : "—";
      return el("tr", null, [
        demoTd(fmt(row.inst), "num"),
        demoTd(fmt(row.legs), "num"),
        demoTd(fmt(s1 && s1.ip_objective), "num"),
        demoTd(demoGapText(s1), "num"),
        demoTd(gencolGap, "num"),
        demoTd(ourTime, "num"),
        demoTd(gencolTime, "num")
      ]);
    })
  );

  return el("div", { class: "card" }, [
    el("h3", { text: t("dc.s3.title") }),
    el("p", { text: t("dc.s3.body") }),
    table,
    el("div", { class: "demo-note", text: t("dc.s3.note") })
  ]);
}

/* --- S4: extreme scale (public 8.1M / 12.6M column catalogs) ------------------ */

function demoCompS4(c) {
  var scale = c.scale;
  if (!scale) { return null; }
  var cards = [];

  var a = scale.a320;
  if (a) {
    var aItems = [];
    if (demoIsNum(a.sifting_s) && demoIsNum(a.baseline_lp_s_min)) {
      aItems.push(el("li", { text: tf("dc.s4.a320.l1", {
        s: a.sifting_s,
        b: a.baseline_lp_s_min,
        x: Math.round(a.baseline_lp_s_min / a.sifting_s)
      }) }));
    }
    if (a.lp_matches_published === true) {
      aItems.push(el("li", { text: t("dc.s4.a320.l2") }));
    }
    if (a.bp && a.bp.status === "optimal" && demoIsNum(a.bp.objective)) {
      aItems.push(el("li", { text: tf("dc.s4.a320.l3", {
        o: Math.round(a.bp.objective * 10) / 10,
        g: fmt(a.bp.gap_pct)
      }) }));
    }
    if (aItems.length) {
      cards.push(el("div", { class: "card" }, [
        el("h3", { text: t("dc.s4.a320.h") }),
        el("ul", null, aItems)
      ]));
    }
  }

  var b = scale.b727;
  if (b) {
    var bItems = [];
    if (demoIsNum(b.baselines_failed) && b.baselines_failed === b.baselines_total) {
      bItems.push(el("li", { text: tf("dc.s4.b727.l1", { n: b.baselines_failed }) }));
    }
    if (demoIsNum(b.sifting_working_cols) && demoIsNum(b.sifting_s)) {
      bItems.push(el("li", { text: tf("dc.s4.b727.l2", {
        c: Math.round(b.sifting_working_cols / 1000),
        s: b.sifting_s
      }) }));
    }
    if (bItems.length) {
      cards.push(el("div", { class: "card" }, [
        el("h3", { text: t("dc.s4.b727.h") }),
        el("ul", null, bItems)
      ]));
    }
  }

  if (!cards.length) { return null; }
  return el("div", { class: "card" }, [
    el("h3", { text: t("dc.s4.title") }),
    el("div", { class: "cards-grid" }, cards)
  ]);
}

/* --- S5: published Jeppesen-side reference points ------------------------------ */

function demoCompS5(c) {
  // Static literature rows straight from the dictionary; the payload only
  // confirms the section exists (reference_note), texts are i18n-owned.
  var rows = ["r1", "r2", "r3", "r4", "r5"].map(function (rk) {
    return el("tr", null, [
      demoTd(t("dc.lit." + rk + ".src")),
      demoTd(t("dc.lit." + rk + ".what")),
      demoTd(t("dc.lit." + rk + ".res"))
    ]);
  });

  var table = demoDataTable(
    [
      demoTh(t("dc.s5.th.source")),
      demoTh(t("dc.s5.th.what")),
      demoTh(t("dc.s5.th.result"))
    ],
    rows
  );

  return el("div", { class: "card" }, [
    el("h3", { text: t("dc.s5.title") }),
    el("p", { text: t("dc.s5.body") }),
    table,
    el("p", { text: t("dc.s5.our") }),
    el("div", { class: "demo-note", text: t("dc.s5.note") })
  ]);
}

/* ------------------------------------------------------------------ wiring */

document.addEventListener("crew:tab", function (evt) {
  if (evt && evt.detail && evt.detail.tab === "demo") { renderDemo(); }
});

document.addEventListener("crew:lang", function () {
  // Re-render the current view in the new language, but only when visible.
  var panel = $("panel-demo");
  if (panel && !panel.classList.contains("hidden")) { renderDemo(); }
});
