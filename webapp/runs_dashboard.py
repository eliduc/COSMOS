"""«Прогоны» — rich comparison view, a Panel dashboard mounted in-process
into the FastAPI app (see app.py: add_applications). Offline by construction
(pn.config.inline bundles BokehJS/Tabulator locally — no CDN, internal
contour). Read-only over webapp/runs.py; admin scope (gated in app.py).

Verified before adoption on the exact prod stack (Python 3.14.4, fastapi
0.136.1 / starlette 1.0.0 / uvicorn 0.46.0, panel 1.9.3, bokeh-fastapi
0.1.8): clean install, no dep conflicts, in-process mount, zero external
hosts, websocket render.

Language: fully localized in all five UI languages (ru/en/uz/kk/ar). BokehJS
renders inside an iframe, isolated from webapp/static/i18n.js, so this document
carries its own string table (_STRINGS below). The SPA loads the iframe as
/runs-dashboard?lang=<uiLang()> (app.js mountRunsDashboard); make_dashboard()
reads that query arg through pn.state.session_args (bokeh-fastapi threads the
request query into the session) and selects the matching row, falling back to
Russian outside a session or on an unknown value. Hint texts reference the SPA
tab labels through {jobs}/{fmt} placeholders, substituted with the localized
labels so they track the visible tab names in every language.
"""
from __future__ import annotations

import panel as pn

# OFFLINE: bundle BokehJS + Tabulator assets inline; never fetch from a CDN.
pn.config.inline = True

# --- i18n -----------------------------------------------------------------
_LANGS = ("ru", "en", "uz", "kk", "ar")

# SPA tab labels referenced inside hints, kept in sync with
# webapp/static/i18n.js (html.tab.jobs / html.tab.format). Substituted into
# the {jobs}/{fmt} placeholders of the note strings per language, so a hint
# points at the same tab name the user actually sees.
_TAB_JOBS = {"ru": "Расчёты", "en": "Compute", "uz": "Hisoblashlar",
             "kk": "Есептеулер", "ar": "الحسابات"}
_TAB_FORMAT = {"ru": "Формат и правила", "en": "Format & rules",
               "uz": "Format va qoidalar", "kk": "Формат және ережелер",
               "ar": "التنسيق والقواعد"}

_STRINGS = {
    "ru": {
        "col.id": "Прогон", "col.type": "Тип", "col.instance": "Экземпляр",
        "col.status": "Статус", "col.verdict": "Вердикт",
        "col.objective": "Целевая функция", "col.lp_bound": "LP-граница",
        "col.gap_pct": "Зазор, %", "col.unassigned": "Не назначено",
        "col.wall_s": "Время, с", "col.iters": "Итераций",
        "col.started_at": "Начало",
        "conv.title": "Кривые сходимости (этап 1: LP-граница по итерациям)",
        "conv.x": "итерация", "conv.y": "LP-граница",
        "conv.empty": "Кривые доступны для прогонов этапа 1 (генерация колонок)",
        "kpi.instance": "Экземпляр", "kpi.total_cost": "Стоимость, всего",
        "kpi.unit_cost_block_h": "Стоимость блок-часа",
        "kpi.productivity_pct": "Продуктивность, %", "kpi.block_h": "Блок-часы",
        "kpi.pairings": "Пейрингов",
        "kpi.avg_block_per_pairing_h": "Блок-ч/пейринг",
        "kpi.nights_per_pairing": "Ночёвок/пейринг",
        "kpi.coverage_pct": "Покрытие, %", "kpi.unassigned": "Не назначено",
        "kpi.crew_utilization_block_h": "Блок-ч/чел",
        "kpi.gate_status": "Вердикт",
        "mix.title": "Структура затрат по экземплярам", "mix.pay": "оплата",
        "mix.perdiem": "суточные", "mix.hotel": "гостиницы",
        "mix.yaxis": "% от стоимости",
        "kpi.heading": "КПЭ флота — стоимость и утилизация",
        "kpi.note": ("Стоимости — по ставкам из «{fmt}» (лёгкая модель: "
                     "оплата блок-часов + суточные + гостиницы). Продуктивность "
                     "= доля блок-часов во времени вне базы. План/факт появится "
                     "с приёмом операционных данных."),
        "note.empty": ("Прогонов пока нет — история задач пуста. Запустите "
                       "этап во вкладке «{jobs}»."),
        "note.no_stage1": ("Кривые сходимости строятся по журналу генерации "
                           "колонок и доступны только для прогонов **этапа 1** "
                           "— в истории таких пока нет. Запустите этап 1 во "
                           "вкладке «{jobs}», и его кривую можно будет наложить "
                           "здесь."),
        "note.default": ("Отметьте один или несколько прогонов **этапа 1** "
                         "слева, чтобы наложить их кривые сходимости "
                         "(LP-граница по итерациям)."),
        "refresh": "Обновить", "runs.heading": "Прогоны — сравнение",
        "tab.runs": "Прогоны", "tab.kpi": "КПЭ флота",
    },
    "en": {
        "col.id": "Run", "col.type": "Type", "col.instance": "Instance",
        "col.status": "Status", "col.verdict": "Verdict",
        "col.objective": "Objective", "col.lp_bound": "LP bound",
        "col.gap_pct": "Gap, %", "col.unassigned": "Unassigned",
        "col.wall_s": "Time, s", "col.iters": "Iterations",
        "col.started_at": "Started",
        "conv.title": "Convergence curves (stage 1: LP bound by iteration)",
        "conv.x": "iteration", "conv.y": "LP bound",
        "conv.empty": "Curves are available for stage-1 runs (column generation)",
        "kpi.instance": "Instance", "kpi.total_cost": "Total cost",
        "kpi.unit_cost_block_h": "Cost per block hour",
        "kpi.productivity_pct": "Productivity, %", "kpi.block_h": "Block hours",
        "kpi.pairings": "Pairings",
        "kpi.avg_block_per_pairing_h": "Block h / pairing",
        "kpi.nights_per_pairing": "Nights / pairing",
        "kpi.coverage_pct": "Coverage, %", "kpi.unassigned": "Unassigned",
        "kpi.crew_utilization_block_h": "Block h / crew",
        "kpi.gate_status": "Verdict",
        "mix.title": "Cost structure by instance", "mix.pay": "pay",
        "mix.perdiem": "per-diem", "mix.hotel": "hotels",
        "mix.yaxis": "% of cost",
        "kpi.heading": "Fleet KPIs — cost and utilization",
        "kpi.note": ("Costs use the rates from «{fmt}» (light model: "
                     "block-hour pay + per-diem + hotels). Productivity = the "
                     "share of block hours in time away from base. Plan/actual "
                     "will appear once operational data is ingested."),
        "note.empty": ("No runs yet — the job history is empty. Start a stage "
                       "in the «{jobs}» tab."),
        "note.no_stage1": ("Convergence curves are built from the "
                           "column-generation log and are available only for "
                           "**stage-1** runs — there are none in the history "
                           "yet. Start stage 1 in the «{jobs}» tab and its "
                           "curve can be overlaid here."),
        "note.default": ("Tick one or more **stage-1** runs on the left to "
                         "overlay their convergence curves (LP bound by "
                         "iteration)."),
        "refresh": "Refresh", "runs.heading": "Runs — comparison",
        "tab.runs": "Runs", "tab.kpi": "Fleet KPIs",
    },
    "uz": {
        "col.id": "Ishga tushirish", "col.type": "Turi", "col.instance": "Nusxa",
        "col.status": "Holat", "col.verdict": "Xulosa",
        "col.objective": "Maqsad funksiyasi", "col.lp_bound": "LP chegarasi",
        "col.gap_pct": "Farq, %", "col.unassigned": "Tayinlanmagan",
        "col.wall_s": "Vaqt, s", "col.iters": "Iteratsiyalar",
        "col.started_at": "Boshlanishi",
        "conv.title": "Yaqinlashuv egri chiziqlari (1-bosqich: LP chegarasi iteratsiyalar boʻyicha)",
        "conv.x": "iteratsiya", "conv.y": "LP chegarasi",
        "conv.empty": "Egri chiziqlar 1-bosqich ishga tushirishlari uchun mavjud (ustunlar generatsiyasi)",
        "kpi.instance": "Nusxa", "kpi.total_cost": "Jami xarajat",
        "kpi.unit_cost_block_h": "Blok-soat narxi",
        "kpi.productivity_pct": "Samaradorlik, %", "kpi.block_h": "Blok-soatlar",
        "kpi.pairings": "Peyringlar",
        "kpi.avg_block_per_pairing_h": "Blok-soat/peyring",
        "kpi.nights_per_pairing": "Tunash/peyring",
        "kpi.coverage_pct": "Qamrov, %", "kpi.unassigned": "Tayinlanmagan",
        "kpi.crew_utilization_block_h": "Blok-soat/kishi",
        "kpi.gate_status": "Xulosa",
        "mix.title": "Nusxalar boʻyicha xarajatlar tarkibi", "mix.pay": "toʻlov",
        "mix.perdiem": "kunlik pul", "mix.hotel": "mehmonxonalar",
        "mix.yaxis": "xarajatning %-i",
        "kpi.heading": "Flot KPIʼlari — xarajat va utilizatsiya",
        "kpi.note": ("Xarajatlar — «{fmt}»dagi stavkalar boʻyicha (yengil "
                     "model: blok-soatlar toʻlovi + kunlik pul + mehmonxonalar). "
                     "Samaradorlik = bazadan tashqari vaqtdagi blok-soatlar "
                     "ulushi. Reja/fakt operatsion maʼlumotlar qabul qilingach "
                     "paydo boʻladi."),
        "note.empty": ("Hozircha ishga tushirishlar yoʻq — vazifalar tarixi "
                       "boʻsh. «{jobs}» varagʻida bosqichni ishga tushiring."),
        "note.no_stage1": ("Yaqinlashuv egri chiziqlari ustunlar generatsiyasi "
                           "jurnali asosida quriladi va faqat **1-bosqich** "
                           "ishga tushirishlari uchun mavjud — tarixda hozircha "
                           "bunday yoʻq. «{jobs}» varagʻida 1-bosqichni ishga "
                           "tushiring, shunda uning egri chizigʻini shu yerda "
                           "ustma-ust koʻrsatish mumkin boʻladi."),
        "note.default": ("Ularning yaqinlashuv egri chiziqlarini (LP chegarasi "
                         "iteratsiyalar boʻyicha) ustma-ust koʻrsatish uchun "
                         "chapdagi bir yoki bir nechta **1-bosqich** ishga "
                         "tushirishini belgilang."),
        "refresh": "Yangilash", "runs.heading": "Ishga tushirishlar — solishtirish",
        "tab.runs": "Ishga tushirishlar", "tab.kpi": "Flot KPIʼlari",
    },
    "kk": {
        "col.id": "Орындалым", "col.type": "Түрі", "col.instance": "Дана",
        "col.status": "Күйі", "col.verdict": "Вердикт",
        "col.objective": "Мақсатты функция", "col.lp_bound": "LP-шек",
        "col.gap_pct": "Алшақтық, %", "col.unassigned": "Тағайындалмаған",
        "col.wall_s": "Уақыт, с", "col.iters": "Итерациялар",
        "col.started_at": "Басталуы",
        "conv.title": "Жинақталу қисықтары (1-кезең: итерациялар бойынша LP-шек)",
        "conv.x": "итерация", "conv.y": "LP-шек",
        "conv.empty": "Қисықтар 1-кезең орындалымдары үшін қолжетімді (бағандар генерациясы)",
        "kpi.instance": "Дана", "kpi.total_cost": "Жалпы құн",
        "kpi.unit_cost_block_h": "Блок-сағат құны",
        "kpi.productivity_pct": "Өнімділік, %", "kpi.block_h": "Блок-сағаттар",
        "kpi.pairings": "Пейрингтер",
        "kpi.avg_block_per_pairing_h": "Блок-сағ/пейринг",
        "kpi.nights_per_pairing": "Түнеу/пейринг",
        "kpi.coverage_pct": "Қамту, %", "kpi.unassigned": "Тағайындалмаған",
        "kpi.crew_utilization_block_h": "Блок-сағ/адам",
        "kpi.gate_status": "Вердикт",
        "mix.title": "Даналар бойынша шығын құрылымы", "mix.pay": "төлем",
        "mix.perdiem": "тәуліктік", "mix.hotel": "қонақүйлер",
        "mix.yaxis": "Құннан %",
        "kpi.heading": "Флот көрсеткіштері — құн мен жүктеме",
        "kpi.note": ("Құндар — «{fmt}» мөлшерлемелері бойынша (жеңіл модель: "
                     "блок-сағаттарға төлем + тәуліктік + қонақүйлер). Өнімділік "
                     "= базадан тыс уақыттағы блок-сағаттардың үлесі. Жоспар/факт "
                     "операциялық деректер қабылданған соң пайда болады."),
        "note.empty": ("Әзірге орындалымдар жоқ — тапсырмалар тарихы бос. "
                       "«{jobs}» қойындысында кезеңді іске қосыңыз."),
        "note.no_stage1": ("Жинақталу қисықтары бағандар генерациясы журналы "
                           "бойынша құрылады және тек **1-кезең** орындалымдары "
                           "үшін қолжетімді — тарихта әзірге ондайлар жоқ. "
                           "«{jobs}» қойындысында 1-кезеңді іске қосыңыз, сонда "
                           "оның қисығын осында қабаттастыруға болады."),
        "note.default": ("Жинақталу қисықтарын (итерациялар бойынша LP-шек) "
                         "қабаттастыру үшін сол жақтан бір немесе бірнеше "
                         "**1-кезең** орындалымын белгілеңіз."),
        "refresh": "Жаңарту", "runs.heading": "Орындалымдар — салыстыру",
        "tab.runs": "Орындалымдар", "tab.kpi": "Флот көрсеткіштері",
    },
    "ar": {
        "col.id": "التشغيل", "col.type": "النوع", "col.instance": "المثيل",
        "col.status": "الحالة", "col.verdict": "الحكم",
        "col.objective": "دالة الهدف", "col.lp_bound": "حدّ LP",
        "col.gap_pct": "الفجوة، %", "col.unassigned": "غير معيَّن",
        "col.wall_s": "الزمن، ث", "col.iters": "التكرارات",
        "col.started_at": "البدء",
        "conv.title": "منحنيات التقارب (المرحلة 1: حدّ LP حسب التكرار)",
        "conv.x": "تكرار", "conv.y": "حدّ LP",
        "conv.empty": "المنحنيات متاحة لعمليات تشغيل المرحلة 1 (توليد الأعمدة)",
        "kpi.instance": "المثيل", "kpi.total_cost": "التكلفة الإجمالية",
        "kpi.unit_cost_block_h": "تكلفة ساعة البلوك",
        "kpi.productivity_pct": "الإنتاجية، %", "kpi.block_h": "ساعات البلوك",
        "kpi.pairings": "التزاوجات",
        "kpi.avg_block_per_pairing_h": "س بلوك/تزاوج",
        "kpi.nights_per_pairing": "ليالٍ/تزاوج",
        "kpi.coverage_pct": "التغطية، %", "kpi.unassigned": "غير معيَّن",
        "kpi.crew_utilization_block_h": "س بلوك/فرد",
        "kpi.gate_status": "الحكم",
        "mix.title": "بنية التكاليف حسب المثيل", "mix.pay": "الأجر",
        "mix.perdiem": "البدل اليومي", "mix.hotel": "الفنادق",
        "mix.yaxis": "% من التكلفة",
        "kpi.heading": "مؤشرات أداء الأسطول — التكلفة والاستغلال",
        "kpi.note": ("التكاليف محسوبة وفق المعدلات من «{fmt}» (نموذج مبسّط: أجر "
                     "ساعات البلوك + البدل اليومي + الفنادق). الإنتاجية = نسبة "
                     "ساعات البلوك من الوقت خارج القاعدة. الخطة/الفعلي سيظهران "
                     "عند استيعاب البيانات التشغيلية."),
        "note.empty": ("لا توجد عمليات تشغيل بعد — سجل المهام فارغ. ابدأ مرحلة "
                       "في تبويب «{jobs}»."),
        "note.no_stage1": ("تُبنى منحنيات التقارب من سجل توليد الأعمدة وتتاح فقط "
                           "لعمليات تشغيل **المرحلة 1** — ولا يوجد منها في السجل "
                           "بعد. ابدأ المرحلة 1 في تبويب «{jobs}»، وعندها يمكن "
                           "تركيب منحناها هنا."),
        "note.default": ("حدّد عملية تشغيل واحدة أو أكثر من **المرحلة 1** على "
                         "اليسار لتركيب منحنيات تقاربها (حدّ LP حسب التكرار)."),
        "refresh": "تحديث", "runs.heading": "عمليات التشغيل — مقارنة",
        "tab.runs": "عمليات التشغيل", "tab.kpi": "مؤشرات أداء الأسطول",
    },
}


def _lang() -> str:
    """UI language for the current Bokeh session, from the ?lang= query arg the
    SPA appends to the iframe src (bokeh-fastapi threads it into session_args).
    Falls back to Russian (the source language) outside a session or on any
    unknown value."""
    try:
        raw = (pn.state.session_args or {}).get("lang")
        if raw:
            val = raw[0]
            code = val.decode() if isinstance(val, (bytes, bytearray)) else str(val)
            if code in _LANGS:
                return code
    except Exception:                       # noqa: BLE001 — i18n must never break the view
        pass
    return "ru"


def _strings(lang: str | None = None) -> dict:
    """The string table for `lang` (default: the current session's language),
    with the {jobs}/{fmt} tab-label placeholders resolved for that language."""
    lang = lang if lang in _LANGS else _lang()
    src = _STRINGS.get(lang, _STRINGS["ru"])
    jobs = _TAB_JOBS.get(lang, _TAB_JOBS["ru"])
    fmt = _TAB_FORMAT.get(lang, _TAB_FORMAT["ru"])
    return {k: v.replace("{jobs}", jobs).replace("{fmt}", fmt)
            for k, v in src.items()}


_COLS = ["id", "type", "instance", "status", "verdict", "objective",
         "lp_bound", "gap_pct", "unassigned", "wall_s", "iters", "started_at"]


def _titles(S):
    return {c: S["col." + c] for c in _COLS}


def _dataframe():
    import pandas as pd
    from . import runs
    recs = runs.list_runs()
    if not recs:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(recs)[_COLS]


def _convergence_figure(run_ids, S):
    """Overlay LP-bound-by-iteration for the selected stage-1 runs."""
    from bokeh.models import Legend
    from bokeh.palettes import Category10_10
    from bokeh.plotting import figure
    from . import runs
    # Log y-axis: base-anchored runs start from the big-M artificial cost
    # (~1e8) and land at ~1e5 — on a linear axis the whole curve collapses
    # into one spike.
    fig = figure(height=300, sizing_mode="stretch_width",
                 title=S["conv.title"],
                 x_axis_label=S["conv.x"], y_axis_label=S["conv.y"],
                 y_axis_type="log")
    plotted = 0
    items = []
    for i, rid in enumerate(run_ids):
        series = runs.run_series(rid)
        pts = series.get("points") or []
        if not pts:
            continue
        xs = [p["it"] for p in pts]
        ys = [p["lp"] for p in pts]
        color = Category10_10[i % 10]
        ln = fig.line(xs, ys, line_width=2, color=color)
        fig.scatter(xs, ys, size=6, color=color)
        items.append((rid, [ln]))
        plotted += 1
    if items:
        fig.add_layout(Legend(items=items, location="top_right",
                              label_text_font_size="9pt"), "center")
    fig.title.text = fig.title.text if plotted else S["conv.empty"]
    return fig


_KPI_COLS = ["instance", "total_cost", "unit_cost_block_h",
             "productivity_pct", "block_h", "pairings",
             "avg_block_per_pairing_h", "nights_per_pairing",
             "coverage_pct", "unassigned", "crew_utilization_block_h",
             "gate_status"]


def _kpi_titles(S):
    return {c: S["kpi." + c] for c in _KPI_COLS}


def _kpi_dataframe():
    import pandas as pd
    from . import kpi
    rows = kpi.fleet_kpi()          # admin scope (the mount is admin-gated)
    if not rows:
        return pd.DataFrame(columns=_KPI_COLS)
    return pd.DataFrame(rows)[_KPI_COLS]


def _cost_mix_figure(rows, S):
    """Stacked bars: pay / per-diem / hotel share of each instance's cost."""
    from bokeh.models import Legend
    from bokeh.plotting import figure
    insts = [r["instance"] for r in rows]
    fig = figure(x_range=insts, height=280, sizing_mode="stretch_width",
                 title=S["mix.title"])
    if not insts:
        return fig
    pay, per, hot = S["mix.pay"], S["mix.perdiem"], S["mix.hotel"]
    src = {
        "_x": insts,
        pay: [r.get("cost_mix_pay_pct") or 0 for r in rows],
        per: [r.get("cost_mix_perdiem_pct") or 0 for r in rows],
        hot: [r.get("cost_mix_hotel_pct") or 0 for r in rows],
    }
    renderers = fig.vbar_stack(
        [pay, per, hot], x="_x", width=0.7,
        color=["#1565c0", "#f9a825", "#2e7d32"], source=src)
    fig.add_layout(Legend(items=[
        (pay, [renderers[0]]), (per, [renderers[1]]),
        (hot, [renderers[2]])], location="top_right",
        label_text_font_size="9pt"), "center")
    fig.y_range.start = 0
    fig.yaxis.axis_label = S["mix.yaxis"]
    fig.xaxis.major_label_orientation = 0.9
    return fig


def _kpi_view(S=None):
    S = S if isinstance(S, dict) else _strings()
    from . import kpi
    rows = kpi.fleet_kpi()
    df = _kpi_dataframe()
    table = pn.widgets.Tabulator(
        df, disabled=True, pagination="local", page_size=12,
        sizing_mode="stretch_width", height=380, titles=_kpi_titles(S),
        show_index=False, header_filters=True)
    note = pn.pane.Markdown(S["kpi.note"], sizing_mode="stretch_width")
    return pn.Column(
        pn.pane.Markdown("## " + S["kpi.heading"]),
        table, pn.pane.Bokeh(_cost_mix_figure(rows, S),
                             sizing_mode="stretch_width", height=300),
        note, sizing_mode="stretch_width")


def _note_text(df, S) -> str:
    """Hint under the table. Found on prod 2026-07-15: with no stage-1 runs
    in the history the curve area is a blank dead end — say WHY explicitly
    instead of showing an empty chart."""
    if df.empty:
        return S["note.empty"]
    if not (df["type"] == "stage1").any():
        return S["note.no_stage1"]
    return S["note.default"]


def make_dashboard():
    pn.extension("tabulator")
    S = _strings()
    df = _dataframe()
    table = pn.widgets.Tabulator(
        df, selectable="checkbox", disabled=True, pagination="local",
        page_size=15, sizing_mode="stretch_width", height=430,
        titles=_titles(S), show_index=False,
        header_filters=True,
    )
    curve = pn.pane.Bokeh(sizing_mode="stretch_width", height=320)
    note = pn.pane.Markdown(_note_text(df, S), sizing_mode="stretch_width")

    def _update(*_):
        sel = table.selection or []
        if not sel:
            curve.object = None
            return
        cur = table.value
        ids = [cur.iloc[i]["id"] for i in sel if i < len(cur)]
        curve.object = _convergence_figure(ids, S)

    table.param.watch(_update, "selection")

    def _refresh(*_):
        table.value = _dataframe()
        table.selection = []
        curve.object = None
        note.object = _note_text(table.value, S)

    refresh = pn.widgets.Button(label=S["refresh"])
    refresh.on_click(_refresh)

    header = pn.Row(
        pn.pane.Markdown("## " + S["runs.heading"], sizing_mode="stretch_width"),
        refresh, sizing_mode="stretch_width")
    runs_view = pn.Column(header, table, note, curve,
                          sizing_mode="stretch_width", min_height=780)
    return pn.Tabs((S["tab.runs"], runs_view), (S["tab.kpi"], _kpi_view(S)),
                   sizing_mode="stretch_width")
