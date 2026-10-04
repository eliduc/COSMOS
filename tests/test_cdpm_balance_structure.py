# -*- coding: utf-8 -*-
"""Ядро «прямого хода», шаг 1 (без моделей): стадия структуры кодом,
баланс потребления величин с типизированными носителями, лексический
контракт семьи, ремонт по находкам кода с подставной моделью, замки
на пути ответов."""
import copy
import json
from pathlib import Path

from cdpm import balance as bal, family_lexicon as lex, model as m
from cdpm import repair as rep, structure as st, vmp_form as vf, form_text as ft
from cdpm import freeze as fz

REPO = Path(__file__).resolve().parent.parent
KIT = REPO / "open-bench" / "polyaris-h0"
ETALON = KIT / "etalon" / "VMP-H0.json"


def _kit_entries() -> list:
    manifest = json.loads((KIT / "kit-manifest.json").read_text(encoding="utf-8"))
    docx, xlsx, jpg = [KIT / p for p in manifest["rules_files"]]
    jpg_lines = json.loads((KIT / "etalon" / "jpg-lines.json").read_text(encoding="utf-8"))
    return [
        {"sid": "S1", "file": docx.name, "sha256": fz.sha256_of(docx),
         "clauses": fz._extract_docx(docx)},
        {"sid": "S2", "file": xlsx.name, "sha256": fz.sha256_of(xlsx),
         "clauses": fz._extract_xlsx(xlsx)},
        {"sid": "S3", "file": jpg.name, "sha256": fz.sha256_of(jpg),
         "clauses": [{"anchor": f"стр-1-абз-{n}", "kind": "p", "text": t}
                     for n, t in enumerate(jpg_lines, start=1)]},
    ]


def _reader_model(entries):
    form, rep_ = ft.read_form_text(entries)
    assert rep_["unrecognized"] == []
    return ft.model_from_form_text(form)


def test_structure_code_verdicts_on_canonical_and_prose():
    entries = _kit_entries()
    v = st.code_verdicts(entries)
    reasons = {k: x["reason"] for k, x in v.items()}
    assert len(v) == 11
    assert reasons[("S1", "абз.1")].startswith("титул")
    assert reasons[("S1", "абз.2")].startswith("валюта")
    assert reasons[("S1", "абз.3")].startswith("канон композиции")
    assert sum(1 for r in reasons.values() if r == "заголовок раздела формы") == 8
    meta = st.meta_from_verdicts(v)
    # Отпечаток базы шаблонов — ПРАВДА ДОКУМЕНТА, а не текущего кода:
    # титул комплекта несёт отпечаток каталога, под который комплект
    # выпущен, и стадия структуры обязана вернуть именно его. Источник
    # ожидания — сам комплект (блок `catalog` манифеста, который пишет
    # сборщик вместе с шапкой документов; перевыпуск эталонов под
    # каталог 0.3.0 — d6073c83, 09.09.2026); у комплекта до переезда на
    # каталог блока нет — там титул нёс ревизию своего времени (12 hex).
    # Сверка с кодом сделала бы тест заложником чужого артефакта.
    manifest = json.loads((KIT / "kit-manifest.json").read_text(encoding="utf-8"))
    want_rev = (manifest.get("catalog") or {}).get("fingerprint") or "bc78f37804ec"
    assert meta == {"customer": "Полярис-H0 (ВМП H0 комплекта)",
                    "currency": "условные единицы",
                    "registry_rev": want_rev}
    from cdpm import catalog_bridge as bridge
    if bridge.stamp():
        # Расхождение ревизии комплекта с отпечатком живого каталога
        # (64 hex) законно: это предупреждение (manifest.fits_catalog),
        # а не порок комплекта; форма ревизии — 12 hex у замороженных
        # комплектов прежней базы, 64 hex у выпущенных под каталог.
        assert len(meta["registry_rev"]) in (12, 64)
        assert len(m.registry_fingerprint()) == 64
    # Проза: заголовок «Раздел N.» — кодом; таблица и норма — кандидаты.
    prose = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64, "clauses": [
        {"anchor": "абз.1", "text": "Раздел 2. Полётные смены, стыковки и отдых"},
        {"anchor": "абз.2", "text": "Минимальный отдых дома — 12 часов."},
        {"anchor": "табл.1, строка 1, кол 1", "text": "Норма"},
        {"anchor": "абз.3", "text": ""}]}]
    vp = st.code_verdicts(prose)
    assert {k[1]: x["reason"] for k, x in vp.items()} == {
        "абз.1": "заголовок раздела документа", "абз.3": "пусто"}


def test_values_recognizer_classes():
    vals = bal.values_of("Превышение базового предела 12:00 (720 минут) — штраф "
                         "15 условных единиц за смену; потолок 13:00; правило VN-011; "
                         "версия M-036/demo-1, действует с 2026-08-01; не менее двух "
                         "экипажей; 25 %; в конце недели; в течение суток")
    by = {}
    for v in vals:
        by.setdefault(v["class"], []).append(v["text"])
    assert by["clock"] == ["12:00", "13:00"]
    assert "720 минут" in by["duration"] and "суток" in by["duration"]
    assert by["amount"] == ["15 условных единиц"]
    assert by["reference"] == ["VN-011"]
    assert by["version"] == ["M-036/demo-1"] and by["date"] == ["2026-08-01"]
    assert by["number_word"] == ["двух"] and by["percent"] == ["25 %"]
    assert "недели" not in sum(by.values(), [])          # «в конце недели» — не величина
    assert bal.operators_of("не менее двух") == ["не менее"]


def test_balance_zero_on_reader_model_and_finds_missing_rate():
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    ref = _reader_model(entries)
    rep_ref = bal.report(entries, ref, verdicts)
    assert rep_ref["findings"] == [], rep_ref["findings"]
    assert rep_ref["values"] > 100 and rep_ref["consumed"] == rep_ref["values"]
    # Потеря ставки немашинного критерия и «за что» у машинного — ловится.
    broken = copy.deepcopy(ref)
    o_rost = next(o for o in broken["objectives"] if o["id"] == "O-ROST")
    o_rost.pop("rate")
    o_pair = next(o for o in broken["objectives"] if o["id"] == "O-PAIR")
    o_pair["criterion"].pop("per")
    rep_b = bal.report(entries, broken, verdicts)
    classes = sorted((f["class"], f["anchor"]) for f in rep_b["findings"])
    assert ("amount", "стр-1-абз-4") in classes and ("rate_per", "стр-1-абз-2") in classes
    # Числовое совпадение с полем чужого класса — не потребление
    # (ярус 1 против ставки 1).
    assert all(f["class"] != "tier" for f in rep_b["findings"])
    # Потеря веса мягкого правила — ловится как сумма без слота.
    lost_w = copy.deepcopy(ref)
    vn011 = next(r for r in lost_w["rules"] if r["id"] == "VN-011")
    vn011.pop("weight")
    rep_w = bal.report(entries, lost_w, verdicts)
    # Якорь берётся из САМОЙ строки: номера абзацев набора сдвигаются
    # при каждом расширении H0, и литерал делал бы тест хранителем
    # номера, а не свойства (расширение 03.09: парк, должности, допуск).
    anchor_011 = vn011["sources"][0]["anchor"]
    assert any(f["class"] == "amount" and f["anchor"] == anchor_011
               for f in rep_w["findings"])
    # Клауза с вердиктом «не правило», но с суммой — находка (семантический класс).
    no_row = copy.deepcopy(ref)
    vn030 = next(r for r in no_row["rules"] if r["id"] == "VN-030")
    anchor_030 = vn030["sources"][0]["anchor"]
    verdicts2 = dict(verdicts)
    verdicts2[("S1", anchor_030)] = {"verdict": "не правило",
                                     "reason": "пример", "round": 0}
    no_row["rules"] = [r for r in no_row["rules"] if r["id"] != "VN-030"]
    rep_n = bal.report(entries, no_row, verdicts2)
    assert any(f["anchor"] == anchor_030 and "вердиктом" in f["note"]
               for f in rep_n["findings"])


def test_definitional_row_consumes_only_codes_not_measurable_values():
    """К-3 проверки плана: определительная строка потребляет коды перечня
    и словарные знаки, но не измеримую величину (ночное окно, «72 часа»)
    — та обязана быть потреблена слотом или стать находкой."""
    entries = [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64, "clauses": [
        {"anchor": "абз.1", "text": "Базы: SVG (Северогорск), PLY (Полярный)."},
        {"anchor": "абз.2", "text": "Ночное время — окно 22:00–06:00 местного времени."},
        {"anchor": "абз.3", "text": "Акклиматизация наступает через 72 часа."}]}]
    doc = {"rules": [
        {"id": "VN-001", "kind": "DEF", "zone": "BOTH", "statement": "Базы: SVG, PLY.",
         "sources": [{"sid": "S1", "anchor": "абз.1"}]},
        {"id": "VN-002", "kind": "DEF", "zone": "BOTH",
         "statement": "Ночное время — окно 22:00–06:00.",
         "sources": [{"sid": "S1", "anchor": "абз.2"}]},
        {"id": "VN-003", "kind": "OUT", "zone": "BOTH",
         "statement": "Акклиматизация 72 часа — вне ВМП (нет данных).",
         "sources": [{"sid": "S1", "anchor": "абз.3"}]}],
        "objectives": [], "questions": []}
    rep_ = bal.report(entries, doc, {})
    anchors = {(f["anchor"], f["class"]): f["note"] for f in rep_["findings"]}
    assert ("абз.1", "code") not in anchors                     # коды — судьба (е)
    assert ("абз.2", "clock") in anchors                        # окно — не потреблено
    assert "определительной строки" in anchors[("абз.2", "clock")]
    assert not any(a == "абз.3" for a, _ in anchors)            # OUT — исключено решением
    # Тот же текст с параметрами окна у строки — потреблено слотом.
    doc["rules"][1]["kind"] = "DATA"
    doc["rules"][1]["template_fit"] = {"category": "A", "family": "time_scale",
                                       "params": {"night_start": {"value": "22:00"},
                                                  "night_end": {"value": "06:00"}}}
    assert not any(f["anchor"] == "абз.2" for f in bal.report(entries, doc, {})["findings"])


def test_family_lexicon_blocks_family_without_subject_word():
    assert lex.subject_hit("rest_floors", "Минимальный отдых между сменами дома — 12:00") == "отдых"
    assert lex.subject_hit("duty_limits", "Минимальный отдых между сменами дома — 12:00") is None


def test_family_lexicon_pairing_days_is_duty_limits_subject():
    """Живой случай набора КН0-ДЕМО (аудитор GPT, 05.09.2026): клауза о
    длительности пейринга в календарных днях несёт предмет семьи
    duty_limits (max_pairing_days); слово «календарного» соседней семьи
    calendar_windows конфликтом чтения не считается — посадка A законна."""
    clause = "Длительность пейринга — не более 1 календарного дня."
    assert lex.subject_hit("duty_limits", clause) == "длительность пейринг"
    assert lex.subject_conflict("duty_limits", clause) is None
    # «продолжительност» — уже общая основа семьи; фраза добавлена для полноты.
    assert lex.subject_hit("duty_limits", "Продолжительность пейринга — до 3 дней.") \
        in ("продолжительност", "продолжительность пейринг")
    assert lex.subject_hit("duty_limits", "Предел длительности пейринга — 1 день.") \
        == "длительности пейринг"
    # Без слова-носителя семьи слово соседней семьи — по-прежнему спор.
    assert lex.subject_conflict("duty_limits", "Не более 1 календарного дня.") == (
        "calendar_windows", "календарн")


def test_pairing_calendar_day_duration_keeps_duty_limits_fit():
    """Патч аудитора GPT (05.09.2026): календарные дни здесь измеряют
    длительность пейринга, а не окно дня недели — посадка A на duty_limits
    проходит гейт целиком (accept_fit_candidate), значение параметра
    сохраняется."""
    for days, wording in ((1, "1 календарного дня"), (3, "3 календарных дней")):
        quote = "не более " + wording
        clause = "Длительность пейринга — " + quote + "."
        assert lex.subject_conflict("duty_limits", clause) is None
        fit, _conf, note = m.accept_fit_candidate(
            {"kind": "HARD", "template_fit": {
                "category": "A", "family": "duty_limits",
                "params": {"max_pairing_days": {"value": days, "quote": quote}}}},
            clause_text=clause)
        assert fit["category"] == "A" and fit["family"] == "duty_limits"
        assert fit["params"]["max_pairing_days"]["value"] == days
        assert note is None


def test_pairing_word_alone_does_not_authorize_duty_limits_fit():
    """Патч аудитора GPT (05.09.2026): одно слово «пейринг» посадку на
    duty_limits не разрешает — клаузы об отдыхе между пейрингами и о дне
    недели вылета остаются предметом своих семей (гейт против неверного
    чтения сохранён; широкое слово «пейринг» в словарь не берётся)."""
    for clause, family in (
        ("Минимальный отдых между пейрингами — 1 календарный день.", "rest_floors"),
        ("Пейринги с вылетом в субботу запрещены.", "calendar_windows"),
    ):
        assert lex.subject_hit("duty_limits", clause) is None
        assert lex.subject_conflict("duty_limits", clause)[0] == family
    fit, conf, note = m.accept_fit_candidate(
        {"kind": "HARD", "template_fit": {"category": "A", "family": "duty_limits",
                                          "params": {"max_duty_duration": {
                                              "value": 720, "quote": "12:00"}}}},
        clause_text="Минимальный отдых между сменами дома — 12:00")
    assert fit["category"] == "C" and "предмет семьи 'rest_floors'" in fit["gap"]
    # Клауза без слов-носителей вовсе — лексика не судит (другие слои).
    fit0, _c0, _n0 = m.accept_fit_candidate(
        {"kind": "HARD", "template_fit": {"category": "A", "family": "duty_limits",
                                          "params": {"max_duty_duration": {
                                              "value": 720, "quote": "12:00"}}}},
        clause_text="Не более 12:00.")
    assert fit0["category"] == "A"
    fit2, _c, note2 = m.accept_fit_candidate(
        {"kind": "HARD", "template_fit": {"category": "A", "family": "rest_floors",
                                          "params": {"min_rest_home": {
                                              "value": 720, "quote": "12:00"}}}},
        clause_text="Минимальный отдых между сменами дома — 12:00")
    assert fit2["category"] == "A" and note2 is None
    doc = json.loads(ETALON.read_text(encoding="utf-8"))
    assert lex.lexicon_problems(doc) == []


def test_repair_fills_missing_rate_with_fake_model():
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc = _reader_model(entries)
    o_rost = next(o for o in doc["objectives"] if o["id"] == "O-ROST")
    o_rost.pop("rate")
    findings = bal.report(entries, doc, verdicts)["findings"]
    assert findings
    calls = []

    def fake(prompt: str) -> str:
        calls.append(prompt)
        assert "Законные слоты" in prompt and "- rate:" in prompt
        assert "непотреблённая величина" in prompt.lower() or "Непотреблённая величина" in prompt
        if len(calls) == 1:
            return json.dumps({"slot": "rate", "value": 7, "per": "час",
                               "quote": "ставка 7 условных единиц"})   # цитата не из клаузы
        return json.dumps({"slot": "rate", "value": 1, "per": "час разброса кредитных часов",
                           "quote": "ставка 1 условная единица за час разброса кредитных часов"},
                          ensure_ascii=False)

    out = rep.repair(doc, entries, findings, fake)
    assert out["fixed"] and out["attempts"] >= 2
    assert o_rost["rate"] == {"value": 1, "per": "час разброса кредитных часов",
                              "as_written": "ставка 1 условная единица за час разброса "
                                            "кредитных часов"}
    assert bal.report(entries, doc, verdicts)["findings"] == []


def test_quote_in_text_accepts_ordered_fragments():
    clause = ("O-PAIR. Стоимость пейрингов. Минимум суммарной стоимости пейрингов; "
              "машинный критерий MIN_PERSON_DAYS, ставка 100 условных единиц за "
              "календарный день командировки (версия M-036/demo-1). Направление: "
              "minimize; метрика: total_pairing_cost; ярус 1. Действует с 2026-08-01.")
    assert m.quote_in_text("ставка 100 условных единиц за календарный день", clause)
    assert m.quote_in_text("машинный критерий MIN_PERSON_DAYS, ставка 100 условных "
                           "единиц ... действует с 2026-08-01", clause)
    assert m.quote_in_text("критерий MIN_PERSON_DAYS … ярус 1", clause)
    assert not m.quote_in_text("действует с 2026-08-01 ... машинный критерий", clause)
    assert not m.quote_in_text("ставка 100 условных единиц ... за месяц", clause)
    assert not m.quote_in_text("", clause)
    block, why = m.accept_objective_criterion(
        {"kind": "MIN_PERSON_DAYS", "rate_per_duty": 100, "version": "M-036/demo-1",
         "effective_from": "2026-08-01", "per": "календарный день командировки",
         "as_written": "машинный критерий MIN_PERSON_DAYS, ставка 100 условных единиц "
                       "за календарный день командировки (версия M-036/demo-1)... "
                       "действует с 2026-08-01"},
        clause_text=clause)
    assert why is None and block["kind"] == "min_person_days" and block["rate_per_duty"] == 100


def test_repair_offers_criterion_slot_for_machine_criterion():
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc = _reader_model(entries)
    o_pair = next(o for o in doc["objectives"] if o["id"] == "O-PAIR")
    crit = o_pair.pop("criterion")
    clause = next(c["text"] for e in entries if e["sid"] == "S3"
                  for c in e["clauses"] if c["anchor"] == "стр-1-абз-2")
    findings = bal.report(entries, doc, verdicts)["findings"]
    assert any(f["class"] == "amount" and f["anchor"] == "стр-1-абз-2" for f in findings)

    def fake(prompt: str) -> str:
        assert "- criterion:" in prompt and "- rate:" in prompt
        return json.dumps({"slot": "criterion", "kind": "MIN_PERSON_DAYS",
                           "rate_per_duty": 100, "per": "календарный день командировки",
                           "version": crit["version"], "effective_from": crit["effective_from"],
                           "quote": "ставка 100 условных единиц за календарный день "
                                    "командировки (версия M-036/demo-1)"},
                          ensure_ascii=False)

    out = rep.repair(doc, entries, findings, fake)
    assert out["fixed"] and out["left"] == []
    assert o_pair["criterion"]["kind"] == "min_person_days"
    assert o_pair["criterion"]["rate_per_duty"] == 100
    assert "rate" not in o_pair
    assert "ставка 100" in clause
    assert bal.report(entries, doc, verdicts)["findings"] == []


def test_repair_restores_rejected_parametrised_fit_by_param():
    """Проход №6 H0 (в редакции реестра 2712cf6): VN-050 → C «семья со
    словарными параметрами без params»; ремонт предлагает слот
    param:<параметр>, значение с цитатой из клаузы восстанавливает
    посадку A, и порог перестаёт быть «неформализованной» величиной.
    До 03.09 семья разброса была беспараметрной и путь шёл через
    fit_quote; решение владельца «пилот без рейсов входит в разброс»
    сделало порог машинным слотом."""
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc = _reader_model(entries)
    vn050 = next(r for r in doc["rules"] if r["id"] == "VN-050")
    good_fit = vn050["template_fit"]
    assert good_fit["category"] == "A" and good_fit["family"] == "balanced_workload"
    assert good_fit["params"]["workload_spread_threshold"]["value"] == 10
    vn050["template_fit"] = {"category": "C", "registry_rev": good_fit["registry_rev"],
                             "gap": "посадка модели отброшена: семья "
                                    "'balanced_workload' со словарными параметрами "
                                    "— params обязателен"}
    assert rep.rejected_family(vn050["template_fit"]) == "balanced_workload"
    findings = bal.report(entries, doc, verdicts)["findings"]
    assert any(f["rows"] == ["VN-050"] for f in findings), findings
    src = vn050["sources"][0]
    clause = next(c["text"] for e in entries if e["sid"] == src["sid"]
                  for c in e["clauses"] if c["anchor"] == src["anchor"])
    quote = "сверх порога существенности 10 кредитных часов"
    assert quote in clause

    def fake(prompt: str) -> str:
        assert "- param:workload_spread_threshold:" in prompt
        assert "fit_quote:balanced_workload" not in prompt
        return json.dumps({"slot": "param:workload_spread_threshold",
                           "value": 10, "quote": quote}, ensure_ascii=False)

    out = rep.repair(doc, entries, findings, fake)
    assert out["fixed"], out
    fit = vn050["template_fit"]
    assert fit["category"] == "A" and fit["family"] == "balanced_workload"
    assert fit["params"]["workload_spread_threshold"]["value"] == 10
    after = bal.report(entries, doc, verdicts)["findings"]
    assert not any(f["rows"] == ["VN-050"] for f in after), after


def test_repair_recovery_branch_refuses_a_crooked_landing_too():
    """С-1 в ремонте — ТРЕТЬЕ место того же закона (находка неавторского
    ревью 13.09.2026).

    Ветвь ВОССТАНОВЛЕНИЯ отброшенной посадки (`repair.py`, ветка
    `rejected_family`) стоит ВЫШЕ рубежа `_apply` и возвращает ДО него,
    поэтому тот её не прикрывал. А `dict(x or {})` спасает только
    ЛОЖНЫЕ формы: `dict("нет")` роняет `ValueError`, `dict(5)` и
    `dict([1, 2])` — `TypeError`.

    Судятся все формы порознь, и ключа нет отличается от `null` тем же
    законом, что у `_cells_writable`: ключа нет — заводим пустые, ключ
    есть со значением не-словарём — кривая форма, отказ ТЕМИ ЖЕ
    словами, что у двух соседних половин. Один закон — одни слова."""
    from cdpm import repair as rep_mod
    основа = {"category": "C", "registry_rev": "x" * 12,
              "gap": "посадка модели отброшена: семья 'rest_floors' со "
                     "словарными параметрами — params обязателен"}

    for имя, форма in (("null", None), ("строка", "нет"), ("число", 5),
                       ("правда", True), ("список", [1, 2]), ("ноль", 0)):
        row = {"id": "R-K", "kind": "HARD",
               "sources": [{"sid": "S1", "anchor": "п.1"}],
               "template_fit": dict(основа, params=форма)}
        было = json.dumps(row, ensure_ascii=False)
        err = rep_mod._apply(row, False, "param:min_rest_outstation",
                             {"value": 600, "quote": "10:00"},
                             "не менее 10:00")
        assert err is not None, (имя, row)
        assert "записать некуда" in err, (имя, err)
        # кривую форму не затираем: восстановление строит НОВУЮ посадку,
        # и молча выбросить при этом чужую форму значило бы её затереть
        assert json.dumps(row, ensure_ascii=False) == было, имя

    # ПОЛОЖИТЕЛЬНЫЙ контроль: ключа НЕТ — ветвь работает как работала,
    # иначе рубеж отказывал бы всем и восстановление умерло бы целиком.
    row = {"id": "R-K", "kind": "HARD",
           "sources": [{"sid": "S1", "anchor": "п.1"}],
           "template_fit": dict(основа)}
    err = rep_mod._apply(row, False, "param:min_rest_outstation",
                         {"value": 600, "quote": "10:00"}, "не менее 10:00")
    assert err is None, err
    assert row["template_fit"]["category"] == "A"
    assert row["template_fit"]["params"]["min_rest_outstation"]["value"] == 600


def test_repair_path_survives_a_truthy_non_container_params():
    """С-1, ТРЕТЬЕ место (находка аудитора GPT174). Проба прошлого круга
    обошла дыру ДВАЖДЫ, и оба обхода стоит назвать.

    Во-первых, она брала `0`, `[]`, `None`, `"нет"` — а `fit.get("params")
    or {}` спасает только ЛОЖНЫЕ формы: `0` даёт `{}`, тогда как `1`,
    `7.5`, `True` остаются собой. Из всех чисел я взял единственное,
    которое проскакивает.

    Во-вторых, она звала `_apply` НАПРЯМУЮ, а настоящий путь ремонта
    идёт `balance.report` → находки → `repair`, и падало РАНЬШЕ рубежа:
    сперва `.items()` в балансе (`AttributeError`), затем `pname not in
    1` в `_slots_for` (`TypeError`). Рубеж в `_apply` до этих форм не
    доживал.

    Эта проба идёт НАСТОЯЩИМ путём и судит три вещи разом: расчёт не
    роняется, кривая форма сохранена буква в букву, ремонт честно
    отчитывается «не починено» (fail-closed со словами, а не
    трассировка)."""
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}

    # Цитата ГОДНАЯ — из клаузы источника. Прежде здесь стояло "x", и
    # `_apply` отказывал РАНЬШЕ, за цитату (`repair.py:161`, «цитата не
    # из клаузы»): проба зеленела на ЧУЖОМ отказе и о рубеже С-1 не
    # говорила ничего. Находка аудитора GPT175 §2.
    цитата = "сверх порога существенности 10 кредитных часов"

    def fake(prompt: str) -> str:
        return json.dumps({"slot": "param:workload_spread_threshold",
                           "value": 10, "quote": цитата}, ensure_ascii=False)

    for форма in (1, 7.5, True):
        doc = _reader_model(entries)
        row = next(r for r in doc["rules"] if r["id"] == "VN-050")
        row["template_fit"]["params"] = форма
        было = json.dumps(doc["rules"], ensure_ascii=False)

        findings = bal.report(entries, doc, verdicts)["findings"]
        out = rep.repair(doc, entries, findings, fake)

        assert out["fixed"] == [], (форма, out["fixed"])
        assert out["left"], (форма, out)
        # ОТКАЗ ИМЕННО ТОТ: слова рубежа С-1, а не чужая причина.
        причины = "; ".join(str(x.get("repair") or "") for x in out["left"])
        assert "записать некуда" in причины, (форма, причины[:200])
        assert "цитата не из клаузы" not in причины, (форма, причины[:200])
        assert json.dumps(doc["rules"], ensure_ascii=False) == было, форма
        assert row["template_fit"]["params"] == форма, форма

    # ПОЛОЖИТЕЛЬНЫЙ контроль: здоровая посадка чинится как чинилась —
    # иначе правка формы могла бы тихо отключить ремонт целиком.
    doc = _reader_model(entries)
    vn = next(r for r in doc["rules"] if r["id"] == "VN-050")
    good = vn["template_fit"]
    vn["template_fit"] = {"category": "C", "registry_rev": good["registry_rev"],
                          "gap": "посадка модели отброшена: семья "
                                 "'balanced_workload' со словарными параметрами "
                                 "— params обязателен"}
    src = vn["sources"][0]
    clause = next(c["text"] for e in entries if e["sid"] == src["sid"]
                  for c in e["clauses"] if c["anchor"] == src["anchor"])
    quote = "сверх порога существенности 10 кредитных часов"

    def fake_ok(prompt: str) -> str:
        return json.dumps({"slot": "param:workload_spread_threshold",
                           "value": 10, "quote": quote}, ensure_ascii=False)

    findings = bal.report(entries, doc, verdicts)["findings"]
    out = rep.repair(doc, entries, findings, fake_ok)
    assert out["fixed"], out
    assert vn["template_fit"]["params"]["workload_spread_threshold"]["value"] == 10


def test_repair_refuses_a_crooked_landing_in_words_instead_of_crashing():
    """С-1 в РЕМОНТЕ: тот же корень стоял и здесь (находка неавторского
    ревью 13.09.2026). `setdefault` у существующего ключа отдаёт прежнее
    значение — и ремонт ронял расчёт `TypeError` и на `params: null`, и
    на `params: []`, вопреки закону C-1, объявленному в продукте общо:
    кривая форма ячеек «не даёт, но и НЕ РОНЯЕТ РАСЧЁТ трассировкой».

    Дорога живая: слоты такой строке предлагаются (`_slots_for` читает
    `fit.get("params") or {}`), а `_apply` зовётся без `try/except` из
    `repair.repair`, который зовёт извлечение.

    Каждая кривая форма судится отдельно: проверка на одной доказывает
    одну."""
    отказ = {}
    for форма in (None, [], "нет", 0):
        row = {"id": "R-K", "kind": "HARD",
               "sources": [{"sid": "S1", "anchor": "п.1"}],
               "template_fit": {"category": "A", "family": "rest_floors",
                                "params": форма}}
        было = json.dumps(row, ensure_ascii=False)
        err = rep._apply(row, False, "param:min_rest_outstation",
                         {"value": 600, "quote": "10:00"}, "не менее 10:00")
        отказ[str(форма)] = err
        assert err is not None, (форма, row)
        assert "записать некуда" in err, (форма, err)
        # отказавший проход не оставил за собой НИ ОДНОЙ буквы
        assert json.dumps(row, ensure_ascii=False) == было, форма

    # ПОЛОЖИТЕЛЬНЫЙ контроль: ключа нет — слот вносится штатно, иначе
    # рубеж отказывал бы всем подряд и проба была бы беззубой.
    row = {"id": "R-K", "kind": "HARD",
           "sources": [{"sid": "S1", "anchor": "п.1"}],
           "template_fit": {"category": "A", "family": "rest_floors"}}
    err = rep._apply(row, False, "param:min_rest_outstation",
                     {"value": 600, "quote": "10:00"}, "не менее 10:00")
    assert err is None, (err, отказ)
    assert row["template_fit"]["params"]["min_rest_outstation"]["value"] == 600


def test_complementary_families_are_not_a_contradiction():
    from cdpm import extract as ex_mod
    from cdpm import family_lexicon as lex_mod
    assert lex_mod.complementary_families("duty_limits", "duty_extension_penalty")
    assert not lex_mod.complementary_families("duty_limits", "rest_floors")
    asm = ex_mod._Assembler("т", [{"sid": "S1", "file": "a", "sha256": "0" * 64}])
    asm.doc["rules"] = [
        {"id": "R-1", "kind": "HARD", "zone": "PAIRING", "statement": "≤ 720",
         "sources": [{"sid": "S1", "anchor": "абз.10"}],
         "template_fit": {"category": "A", "family": "duty_limits",
                          "params": {"max_duty_duration": {"value": 720, "quote": "12:00"}}}},
        {"id": "R-2", "kind": "SOFT", "zone": "PAIRING", "statement": "до 780, штраф 15",
         "sources": [{"sid": "S1", "anchor": "абз.13"}], "weight": 15,
         "template_fit": {"category": "A", "family": "duty_extension_penalty",
                          "params": {"max_extended_duty": {"value": 780, "quote": "13:00"}}}}]
    why = asm.add_contradiction({"rule_ids": ["R-1", "R-2"], "title": "720 или 780",
                                 "text": "?", "options": [{"key": "а", "text": "720"},
                                                          {"key": "б", "text": "780"}]})
    assert why and "дополняют друг друга" in why
    assert asm.doc["questions"] == []


def test_empty_params_object_with_quote_is_a_valid_parameterless_fit():
    """Проход №8 H0: модель прислала params: {} вместе с цитатой у
    беспараметрной семьи — пустой объект не несёт сведений и не ошибка."""
    clause = "VN-030. Ночёвки вне базы. Штраф 10 условных единиц за каждую ночёвку вне базы приписки."
    fit, _c, note = m.accept_fit_candidate(
        {"kind": "SOFT", "template_fit": {"category": "A", "family": "layover_penalty",
                                          "params": {}, "confidence": 90,
                                          "quote": "Штраф 10 условных единиц за каждую "
                                                   "ночёвку вне базы приписки"}},
        clause_text=clause)
    assert fit["category"] == "A" and fit["family"] == "layover_penalty" and note is None
    assert "params" not in fit


def test_value_anchored_quote_tolerates_inflection_but_not_glued_phrases():
    clause = ("VN-011. Продление смены. Превышение базового предела 12:00 (720 минут) — мягкое "
              "предпочтение со штрафом 15 условных единиц за смену с превышением; потолок "
              "продления — 13:00 (780 минут).")
    assert not m.quote_in_text("штраф 15 условных единиц за смену с превышением", clause)
    assert m.value_quote_in_text("штраф 15 условных единиц за смену с превышением", 15, clause)
    assert not m.value_quote_in_text("штраф 15 монет за смену", 15, clause)
    assert not m.value_quote_in_text("штраф 25 условных единиц", 25, clause)
    assert not m.value_quote_in_text("предпочтение со штрафом", 15, clause)   # числа нет в цитате
    # Склейка ДВУХ фраз клаузы цитатой величины не считается (живая
    # находка кругов 10.09.2026): первые слова после числа сходятся, а
    # хвост цитаты взят из другого места — измеритель отказал бы ею на
    # выпуске пакета. Два места ОДНОЙ клаузы законны через многоточие.
    assert not m.value_quote_in_text(
        "штраф 15 условных единиц за смену потолок продления 13:00", 15, clause)
    assert m.value_quote_in_text(
        "штраф 15 условных единиц за смену … потолок продления — 13:00", 15, clause)
    # Закон дословности у приёма и у измерителя ОДИН (cdpm.measure):
    # приём не строже гейта выпуска — фрагменты через запасной шов «;»
    # (живой случай на Test 09.09.2026) проходят обоими, а склейка без
    # шва не проходит ни тем, ни другим.
    from cdpm import measure as ms
    seam = "со штрафом 15 условных единиц за смену с превышением; 13:00 (780 минут)"
    assert m.value_quote_in_text(seam, 15, clause)
    assert ms.quote_verbatim(seam, clause)
    glued = "штраф 15 условных единиц за смену потолок продления 13:00"
    assert not ms.quote_verbatim(glued, clause)


def test_repair_takes_hints_from_second_reading_before_asking_model():
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc = _reader_model(entries)
    vn011 = next(r for r in doc["rules"] if r["id"] == "VN-011")
    vn011.pop("weight")
    findings = bal.report(entries, doc, verdicts)["findings"]
    assert any(f["rows"] == ["VN-011"] for f in findings)
    calls = []

    def never(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"slot": None, "reason": "модель не должна была понадобиться"})

    hints = {("S1", vn011["sources"][0]["anchor"]): [{"weight": 15}]}
    out = rep.repair(doc, entries, findings, never, hints=hints)
    assert vn011["weight"] == 15 and calls == []
    assert any("второму чтению" in x["repair"] for x in out["fixed"])
    assert bal.report(entries, doc, verdicts)["findings"] == []


def test_quote_misplaced_into_params_is_normalized_to_top_level():
    """Проход №10 H0: модель положила цитату-носитель в params: {"quote": …}
    у беспараметрной семьи — код нормализует носитель, а не роняет
    посадку в C («параметр 'quote' вне словаря PARAMS»)."""
    clause = "VN-030. Ночёвки вне базы. Штраф 10 условных единиц за каждую ночёвку вне базы приписки."
    fit, _c, note = m.accept_fit_candidate(
        {"kind": "SOFT", "template_fit": {"category": "A", "family": "layover_penalty",
                                          "params": {"quote": "Штраф 10 условных единиц за "
                                                              "каждую ночёвку вне базы приписки"},
                                          "confidence": 95}},
        clause_text=clause)
    assert fit["category"] == "A" and fit["family"] == "layover_penalty"
    assert fit.get("quote", "").startswith("Штраф 10") and "params" not in fit


def test_second_reading_fit_is_adopted_for_unfitted_row():
    """Проход №10 H0: первое чтение потеряло посадку за форму
    (params.quote), второе несёт A с тем же ядром — код принимает посадку
    второго чтения по клаузе; величины клаузы после этого потреблены."""
    from cdpm import second_reading as sr
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc2 = _reader_model(entries)                 # «второе чтение» — эталонное
    doc = _reader_model(entries)
    vnd91 = next(r for r in doc["rules"] if r["id"] == "VN-D91")
    rev = vnd91["template_fit"]["registry_rev"]
    vnd91["template_fit"] = {"category": "C", "registry_rev": rev,
                             "gap": "посадка модели отброшена: параметр 'quote' вне словаря PARAMS"}
    clause_texts = {(str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
                    for e in entries for c in e.get("clauses") or []}
    adopted = sr.adopt_stronger_fits(doc, doc2, clause_texts)
    assert [a["row"] for a in adopted] == ["VN-D91"]
    assert vnd91["template_fit"]["category"] == "A"
    assert vnd91["template_fit"]["family"] == "calendar_windows"
    assert "из второго чтения" in vnd91["note"]
    assert bal.report(entries, doc, verdicts)["findings"] == []
    # Ядро иное (другой вес у второго чтения) — посадка не принимается.
    vnd91["template_fit"] = {"category": "C", "registry_rev": rev,
                             "gap": "посадка модели отброшена: params — непустой объект"}
    next(r for r in doc2["rules"] if r["id"] == "VN-D91")["weight"] = 99
    assert sr.adopt_stronger_fits(doc, doc2, clause_texts) == []
    next(r for r in doc2["rules"] if r["id"] == "VN-D91")["weight"] = vnd91["weight"]
    # Спор о чтении (лексический конфликт) посадкой второго чтения не гасится.
    vnd91["template_fit"] = {"category": "C", "registry_rev": rev,
                             "gap": "посадка модели отброшена: … — спор о чтении, не посадка"}
    assert sr.adopt_stronger_fits(doc, doc2, clause_texts) == []


def test_second_reading_rows_adopted_for_anchors_missed_by_first_reading():
    """Живой случай 06.09.2026 (run-0084, демо Н0): первое чтение дало 4
    кандидата из 11 по документу, второе — все 11; носители без строки
    первого чтения получают строки второго чтения с пометкой, носители
    с любой записью первого чтения не трогаются, идентификаторы не
    дублируются."""
    from cdpm import second_reading as sr
    entries = _kit_entries()
    doc2 = _reader_model(entries)                 # «второе чтение» — полное
    doc = _reader_model(entries)
    other = next(r["id"] for r in doc["rules"] if r["id"] != "VN-D91")
    lost = {"VN-D91", other}
    doc["rules"] = [r for r in doc["rules"] if r["id"] not in lost]
    before = len(doc["rules"])
    adopted = sr.adopt_missing_rows(doc, doc2)
    assert sorted(a["row"] for a in adopted) == sorted(lost)
    assert len(doc["rules"]) == before + 2
    back = {r["id"]: r for r in doc["rules"]}
    assert "из второго чтения" in back["VN-D91"]["note"]
    src = next(r for r in doc2["rules"] if r["id"] == other)
    assert {k: v for k, v in back[other].items() if k != "note"} \
        == {k: v for k, v in src.items() if k != "note"}
    assert len({r["id"] for r in doc["rules"]}) == len(doc["rules"])
    # Повторный вызов — ничего нового; носители с записью не трогаются.
    assert sr.adopt_missing_rows(doc, doc2) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_mixed_script_words_are_normalized_with_trace():
    """Проход №9 H0: модель написала «субботa» с латинской «a» — аудитор
    ловил редакционным замечанием; код нормализует омоглифы в
    кириллическом слове сам, со следом."""
    text, fixed = m.normalize_mixed_script("ночёвка в субботa или пятницу; SVG; M-036/demo-1; 12:00")
    assert text == "ночёвка в суббота или пятницу; SVG; M-036/demo-1; 12:00"
    assert fixed == ["субботa→суббота"]
    # Чисто латинские слова и коды не трогаются; слово без омоглифов — тоже.
    assert m.normalize_mixed_script("PLY, duty_limits, total_pairing_cost") == \
        ("PLY, duty_limits, total_pairing_cost", [])
    # Латинское слово с кириллической «о» — нормализуется в латиницу.
    assert m.normalize_mixed_script("metric: tоtal") == ("metric: total", ["tоtal→total"])


def test_form_quotes_judged_against_clause_texts_when_given():
    """План 2.3: формулировка строки — формулировка ВМП, биекцию несёт
    цитата из клаузы. Пакет прохода №9 нёс 11 замечаний «цитата не
    фрагмент формулировки» при чистом гейте — форма судила цитаты по
    формулировке модели, а не по клаузе-источнику."""
    entries = _kit_entries()
    doc = _reader_model(entries)
    vn030 = next(r for r in doc["rules"] if r["id"] == "VN-030")
    vn030["statement"] = "Штраф за каждую ночёвку вне базы приписки (формулировка ВМП)."
    form = vf.to_form(doc)
    probs = vf.validate_form(form, strict=True)
    assert any("VN-030" in p and "фрагментом" in p for p in probs), probs
    clause_texts = {(str(e["sid"]), str(c["anchor"])): str(c.get("text") or "")
                    for e in entries for c in e.get("clauses") or []}
    assert vf.validate_form(form, strict=True, clause_texts=clause_texts) == []
    # Цитата вне и формулировки, и клаузы — по-прежнему находка.
    vn030["template_fit"]["quote"] = "фраза из другого документа"
    form2 = vf.to_form(doc)
    assert any("VN-030" in p and "ни формулировки" in p
               for p in vf.validate_form(form2, strict=True, clause_texts=clause_texts))


def test_balance_sees_distorted_parameter_despite_preserved_quote():
    """Проверка плана №11: судьба «слот» — только по машинной сверке
    значения; вхождение величины в цитату при искажённом значении
    параметра — не потребление."""
    entries = _kit_entries()
    verdicts = {k: dict(v, round=0) for k, v in st.code_verdicts(entries).items()}
    doc = _reader_model(entries)
    base = bal.report(entries, doc, verdicts)
    assert base["findings"] == [] and base["consumed"] == base["values"]
    vn011 = next(r for r in doc["rules"] if r["id"] == "VN-011")
    cell = vn011["template_fit"]["params"]["duty_extension_cap"]
    assert cell["value"] == 780
    cell["value"] = 790                       # цитата «13:00 (780 минут)» сохранена
    rep_ = bal.report(entries, doc, verdicts)
    assert any(f["rows"] == ["VN-011"] and f["class"] in ("clock", "duration")
               for f in rep_["findings"]), rep_["findings"]
    cell["value"] = 780
    # Порог разброса (10 кредитных часов) с реестра 2712cf6 — машинный
    # слот семьи balanced_workload, а не величина «держится текстом»:
    # искажение значения при сохранённой цитате баланс видит так же.
    thr = vn050_cell = next(r for r in doc["rules"] if r["id"] == "VN-050"
                            )["template_fit"]["params"]["workload_spread_threshold"]
    assert thr["value"] == 10 and "10" in thr["quote"]
    rep2 = bal.report(entries, doc, verdicts)
    assert not [h for h in rep2["held_by_text"] if "VN-050" in h["rows"]]
    vn050_cell["value"] = 12                  # цитата «10 кредитных часов» сохранена
    rep3 = bal.report(entries, doc, verdicts)
    assert any(f["rows"] == ["VN-050"] for f in rep3["findings"]), rep3["findings"]
    vn050_cell["value"] = 10


def test_model_not_rule_verdict_blocked_on_clauses_with_norm_signs():
    from cdpm import extract as ex_mod
    assert ex_mod._not_rule_blocker("Настоящий документ утверждён приказом от 2026-08-01.") is None
    why = ex_mod._not_rule_blocker("Отдых между сменами дома — не менее 12 часов.")
    assert why and ("12 часов" in why or "отдых" in why)
    why2 = ex_mod._not_rule_blocker("Допуск на тип воздушного судна обязателен для каждого пилота.")
    assert why2 and "предмет" in why2


def test_registry_one_live_finding_per_key():
    from cdpm import exchange as ex_mod
    rm = {"id": "A1-001", "severity": "КРИТИЧЕСКОЕ", "defect_class": "искажение",
          "target": {"rule": "R-1", "question": None},
          "source_refs": [{"sid": "S1", "anchor": "абз.3"}], "text": "утрачен признак"}
    registry = {"cycle": 1, "findings": []}
    pack = {"remarks": [rm]}
    resp = {"answers": [{"remark": "A1-001", "verdict": "ПРИНЯТО", "basis": "x"}]}
    ex_mod.registry_update(registry, 1, pack, resp, patch_rejected=[{"answer": "A1-001",
                                                                    "reasons": ["брак"]}])
    assert [f["state"] for f in registry["findings"]] == ["патч отбракован"]
    # Повтор того же ключа в круге 2 с принятой заплатой — та же запись, не вторая.
    rm2 = dict(rm, id="A2-001")
    ex_mod.registry_update(registry, 2, {"remarks": [rm2]},
                           {"answers": [{"remark": "A2-001", "verdict": "ПРИНЯТО", "basis": "y"}]})
    assert len(registry["findings"]) == 1 and registry["findings"][0]["state"] == "исправлено"
    # Молчание аудитора в круге 3 закрывает её.
    ex_mod.registry_update(registry, 3, {"remarks": []}, {"answers": []})
    assert registry["findings"][0]["state"] == "закрыто"


def test_fit_integrity_weight_change_without_clause_texts_is_not_blocked():
    from cdpm import exchange as ex_mod
    prev = {"id": "R-1", "kind": "SOFT", "weight": 10, "sources": [{"sid": "S1", "anchor": "а"}]}
    new = dict(prev, weight=20)
    assert ex_mod.fit_integrity_problems(prev, new, None) == []          # судить нечем
    assert ex_mod.fit_integrity_problems(prev, new, {("S1", "а"): "штраф 10"})   # число 20 не в клаузе
    assert ex_mod.fit_integrity_problems(prev, new, {("S1", "а"): "штраф 20 по ответу"}) == []


def test_answers_path_rejects_kind_change_against_convention():
    assert m.kind_change_allowed("DEF", "HARD", None) is False
    assert m.kind_change_allowed("HARD", "SOFT", None) is True
    assert m.kind_change_allowed("DEF", "HARD",
                                 {"category": "A", "family": "duty_limits"}) is True
    assert m.kind_change_allowed("DEF", "SOFT",
                                 {"category": "A", "family": "duty_limits"}) is False
