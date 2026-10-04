/* Onboarding tour (loads after i18n.js and app.js; reuses their globals:
 * $, el, api, state, showTab, t, uiLang, setUiLang, applyI18nDom).
 *
 * Responsibilities:
 *   1. the intro tour: a sequence of cards over the real UI (texts live in
 *      i18n.js under tour.*);
 *   2. auto-start on first login (server flag tour_seen from /api/me;
 *      localStorage fallback for the token mode's shared identity);
 *   3. the header RU/EN and Tour buttons.
 */
"use strict";

var TOUR_LS_KEY = "crewTourSeen";

/* --------------------------------------------------------------- tour engine */

/* Tour v3 (2026-07-16): one step per top-level SECTION of the two-level
 * navigation. `group` switches/highlights the section; `items`+`leafs` are
 * the sub-tour — each list entry is clickable and opens its sub-tab under
 * the card, so the user can peek at every screen without leaving the tour.
 * Admin-only sections render the card but skip the switch for plain users. */
var TOUR_STEPS = [
  { key: "welcome", paras: ["p1", "p2"] },
  // 2026-08-28: главный экран-конвейер — первый раздел панели, тур
  // обязан начинать знакомство с него (порядок тура = порядок панели)
  // 2026-09-10: второй абзац — карточка «Запись о прогоне» в панели
  // постановки (СХУП, шаг 2): тур уже описывает панель ВМП и кнопки
  // расчёта, а прогон стал отдельной сущностью с объявлением до старта
  { key: "gflow", group: "flow", paras: ["p1", "p2"] },
  // 2026-09-07: прогоны COSMOS — второй раздел панели (слово владельца:
  // список прогонов как в CDPM), тур идёт в порядке панели
  // 2026-09-10: второй абзац — секция «Дерево прогонов» на карточке
  // прогона (СХУП, шаг 3): база прогонов имеет вид дерева, и тур,
  // описывающий вкладку «Прогоны», обязан это назвать
  { key: "gcruns", group: "cruns", paras: ["p1", "p2"] },
  { key: "gdata", group: "data", paras: ["p1"],
    items: ["i1", "i2"], leafs: ["data", "format"] },
  { key: "gopt", group: "opt", paras: ["p1"],
    items: ["i1", "i2", "i3", "i4"],
    leafs: ["jobs", "runs", "results", "scenarios"] },
  { key: "gcrew", group: "crew", paras: ["p1"],
    items: ["i1", "i2", "i3", "i4"],
    leafs: ["board", "vacation", "quals", "bases"] },
  // last-minute disruption recovery: its own card — the capability is a
  // headline for operations people (user request 2026-07-16)
  { key: "gdz", group: "crew", paras: ["p1", "p2", "p3"] },
  { key: "gme", group: "me", paras: ["p1", "p2"] },
  // 2026-07-16: Автоулучшение follows Кабинет to mirror the nav-bar order
  // (evo moved between me and demo); Помощь comes right after it.
  // 2026-07-20: «Контуры» — первая вкладка группы и единственный экран,
  // где виден весь конвейер самоусовершенствования сразу; тур обязан
  // начинать группу с неё, иначе знакомство идёт с середины (с очереди
  // предложений), и непонятно, откуда предложения берутся
  { key: "gevo", group: "evo", admin: true, paras: ["p1"],
    items: ["i1", "i2", "i3", "i4"],
    leafs: ["loops", "crs", "lab", "kb"], tail: "p2" },
  { key: "ghelp", group: "help", paras: ["p1"],
    items: ["i1", "i2"], leafs: ["ask", "suggest"] },
  { key: "finish", group: "demo", paras: ["p1", "p2"], last: true }
];

var tour = { idx: -1, backdrop: null, card: null, highlighted: null };

function tourActive() { return tour.idx >= 0; }

function tourStart() {
  if (tourActive()) { return; }
  tour.idx = 0;
  tour.backdrop = el("div", { class: "tour-backdrop" });
  tour.card = el("div", { class: "tour-card", role: "dialog" });
  document.body.appendChild(tour.backdrop);
  document.body.appendChild(tour.card);
  tourRender();
}

function tourEnd(markSeen) {
  if (!tourActive()) { return; }
  tour.idx = -1;
  if (tour.backdrop) { tour.backdrop.remove(); tour.backdrop = null; }
  if (tour.card) { tour.card.remove(); tour.card = null; }
  tourUnhighlight();
  if (markSeen) {
    localStorage.setItem(TOUR_LS_KEY, "1");
    if (state.me) { state.me.tour_seen = true; }
    // Best effort: the token mode's shared identity has no catalog row.
    api("/me/tour-seen", { method: "POST" }).catch(function () {});
  }
}

function tourUnhighlight() {
  if (tour.highlighted) {
    tour.highlighted.classList.remove("tour-highlight");
    tour.highlighted = null;
  }
}

function tourRender() {
  if (!tourActive()) { return; }
  var step = TOUR_STEPS[tour.idx];
  tourUnhighlight();
  // Admin-only sections: plain users still read the card (the product has
  // these capabilities), but nothing is switched or highlighted for them.
  var gated = step.admin && !(state.me && state.me.role === "admin");
  if (step.group && !gated) {
    showGroup(step.group);
    var gbtn = document.querySelector(
      '#groups .tab[data-group="' + step.group + '"]');
    if (gbtn) {
      gbtn.classList.add("tour-highlight");
      tour.highlighted = gbtn;
    }
  }
  var card = tour.card;
  card.textContent = "";

  var langSel = el("select", { class: "lang-select", "aria-label": "Язык / Language" });
  buildLangOptions(langSel);
  langSel.addEventListener("change", function () { setUiLang(langSel.value); });
  card.appendChild(el("div", { class: "tour-progress" }, [
    el("span", { text: (tour.idx + 1) + " " + t("ui.of") + " " +
                       TOUR_STEPS.length }),
    langSel
  ]));
  card.appendChild(el("h3", { class: "tour-title",
                              text: t("tour." + step.key + ".title") }));
  var body = el("div", { class: "tour-body" });
  (step.paras || []).forEach(function (p) {
    body.appendChild(el("p", { text: t("tour." + step.key + "." + p) }));
  });
  if (step.items) {
    var ul = el("ul", { class: step.leafs ? "tour-subtour" : "" });
    step.items.forEach(function (i, n) {
      var li = el("li", { text: t("tour." + step.key + "." + i) });
      // Sub-tour: clicking a list entry opens that sub-tab UNDER the card,
      // so every screen of the section can be peeked at without leaving.
      var leaf = step.leafs && step.leafs[n];
      if (leaf && !gated) {
        li.classList.add("tour-leaf");
        li.setAttribute("role", "button");
        li.setAttribute("tabindex", "0");
        li.addEventListener("click", function () { showTab(leaf); });
        li.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            showTab(leaf);
          }
        });
      }
      ul.appendChild(li);
    });
    body.appendChild(ul);
    if (step.leafs && !gated) {
      body.appendChild(el("p", { class: "tour-hint",
                                 text: t("tour.subtourHint") }));
    }
    if (step.tail) {
      body.appendChild(
        el("p", { text: t("tour." + step.key + "." + step.tail) }));
    }
  }
  card.appendChild(body);

  var actions = el("div", { class: "tour-actions" });
  if (tour.idx > 0) {
    var back = el("button", { class: "btn btn-ghost", type: "button",
                              text: t("ui.back") });
    back.addEventListener("click", function () {
      tour.idx -= 1; tourRender();
    });
    actions.appendChild(back);
  }
  var next = el("button", { class: "btn btn-primary", type: "button",
                            text: step.last ? t("ui.done") : t("ui.next") });
  next.addEventListener("click", function () {
    if (step.last) { tourEnd(true); }
    else { tour.idx += 1; tourRender(); }
  });
  actions.appendChild(next);
  if (!step.last) {
    var skip = el("button", { class: "btn btn-ghost tour-skip", type: "button",
                              text: t("ui.skip") });
    skip.addEventListener("click", function () { tourEnd(true); });
    actions.appendChild(skip);
  }
  card.appendChild(actions);
}

/* ------------------------------------------------- header buttons + autostart */

/** Reflect the active language in the header picker. */
function updateLangButton() {
  var sel = $("lang-select");
  if (sel) { sel.value = uiLang(); }
}

document.addEventListener("crew:lang", function () {
  updateLangButton();
  if (tourActive()) { tourRender(); }
});

var tourAutoChecked = false;

document.addEventListener("crew:me", function () {
  if (tourAutoChecked) { return; }
  tourAutoChecked = true;
  var me = state.me;
  var seen;
  if (me && me.tour_seen === false) { seen = false; }
  else if (me && me.tour_seen === true) { seen = true; }
  else { seen = !!localStorage.getItem(TOUR_LS_KEY); }
  if (!seen) { tourStart(); }
});

document.addEventListener("DOMContentLoaded", function () {
  // The static DOM ships in Russian; re-apply for returning EN users.
  applyI18nDom();
  updateLangButton();
  var tourBtn = $("btn-tour");
  if (tourBtn) { tourBtn.addEventListener("click", tourStart); }
  var langSel = $("lang-select");
  if (langSel) {
    buildLangOptions(langSel);
    langSel.addEventListener("change", function () {
      setUiLang(langSel.value);
    });
  }
});
