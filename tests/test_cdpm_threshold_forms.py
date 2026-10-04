# -*- coding: utf-8 -*-
"""Ревизия 1.10: машинные формы порогов (threshold-forms.json).

Охраняемые контракты: сборка ДЕТЕРМИНИРОВАННА из машинных ячеек
модели (минуты, якоря); окна — полуинтервалы [from, to) в минутах
от полуночи (табличное «06:00–14:59» → [360, 900)); калибровка
колонок ТОЛЬКО по ячейке с явными секторами — группа без неё честно
не собирается; снятые носители (OUT) не участвуют; файл пакета
объявляется манифестом с суммой и сверяется гейтом пересборкой."""
import io
import json
import zipfile

from cdpm import model as m
from cdpm import threshold_forms as tf


def _cell(parent, row, col, window, minutes, extra=""):
    return {"id": f"{parent}-строка-{row}-кол-{col}",
            "title": "Ячейка матрицы",
            "kind": "HARD", "zone": "ROSTERING",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "statement": (f"Максимальная продолжительность смены "
                          f"составляет {minutes} минут при начале "
                          f"в окне {window}{extra}."),
            "params": [{"name": "max_duty_duration",
                        "value": minutes,
                        "unit": "duration_minutes",
                        "as_written": f"{minutes} минут",
                        "relation": "max"}]}


_PARENT = "R-S4-табл-2-строка-29-кол-2-вл-табл-1"


def _doc_with_matrix():
    return {"rules": [
        _cell(_PARENT, 4, 2, "06:00–14:59", 780,
              extra=" (верхний предел, 1–2 сектора)"),
        _cell(_PARENT, 4, 3, "06:00–14:59", 750),
        _cell(_PARENT, 4, 4, "06:00–14:59", 720),
        _cell(_PARENT, 5, 2, "15:00–16:59", 750),
        _cell(_PARENT, 5, 3, "15:00–16:59", 720),
    ]}


def test_matrix_assembled_with_calibration_and_half_intervals():
    mats = tf.assemble_matrices(_doc_with_matrix())
    assert len(mats) == 1
    mat = mats[0]
    assert mat["threshold"] == "max_duty_span_min"
    assert mat["tz"] == "base-local"
    r0 = mat["rows"][0]
    assert (r0["start_from_min"], r0["start_to_min"]) == (360, 900)
    assert (r0["sectors_from"], r0["sectors_to"]) == (1, 2)
    assert r0["value_min"] == 780
    # Колонка 3 → одиночное значение секторов 3 (калибровка 2→(1,2)).
    r1 = mat["rows"][1]
    assert (r1["sectors_from"], r1["sectors_to"]) == (3, 3)
    # Вторая строка окна [900, 1020).
    r3 = mat["rows"][3]
    assert (r3["start_from_min"], r3["start_to_min"]) == (900, 1020)
    assert all("as_written" in r and r["rule"] for r in mat["rows"])


def test_matrix_without_calibration_cell_is_not_assembled():
    doc = _doc_with_matrix()
    doc["rules"][0]["statement"] = (
        "Максимальная продолжительность смены составляет X при "
        "начале в окне 06:00–14:59.")   # сектора никем не названы
    assert tf.assemble_matrices(doc) == []


def test_out_cells_do_not_participate():
    doc = _doc_with_matrix()
    for r in doc["rules"]:
        r["kind"] = "OUT"
    assert tf.assemble_matrices(doc) == []


def test_conditional_floor_from_active_rule_only():
    rule = {"id": "R-TZ", "kind": "HARD",
            "statement": "При нахождении вне базы и разнице в "
                         "часовых поясах >= 4 часов минимальное "
                         "время отдыха не менее 16 часов.",
            "params": [{"name": "min_rest_outstation", "value": 960,
                        "unit": "duration_minutes",
                        "as_written": "16 часов",
                        "relation": "min"}]}
    floors = tf.assemble_conditional_floors({"rules": [rule]})
    assert floors == [{
        "threshold": "min_rest_outstation_min",
        "condition": {"kind": "tz_diff_hours", "relation": "min",
                      "value": 4},
        "value_min": 960,
        "as_written": rule["statement"],
        "rule": "R-TZ"}]
    rule["kind"] = "OUT"
    assert tf.assemble_conditional_floors({"rules": [rule]}) == []


def test_forms_file_exported_declared_and_gated(tmp_path):
    """Файл пакета: собирается при экспорте, объявлен манифестом с
    суммой, гейт сверяет пересборкой; подмена файла — красная."""
    import tests.test_cdpm_task_package as tpk
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package as gate
    doc = tpk._ready_doc()
    doc["rules"] += _doc_with_matrix()["rules"]
    run = tpk._run_dir(tmp_path, doc)
    body = tp.export_zip(run)
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
        forms = json.loads(z.read("threshold-forms.json"))
    assert "threshold-forms.json" in names
    assert any(f["name"] == "threshold-forms.json"
               for f in manifest["files"])
    assert forms["spec"] == "1.10" and len(forms["matrices"]) == 1
    pkg = tmp_path / "p.zip"
    pkg.write_bytes(body)
    problems, _ = gate.check(pkg)
    assert problems == []
    # Подмена файла форм — красная (детерминизм сборки нарушен).
    files = {}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        for n in z.namelist():
            files[n] = z.read(n)
    forms["matrices"][0]["rows"][0]["value_min"] = 999
    files["threshold-forms.json"] = json.dumps(
        forms, ensure_ascii=False).encode("utf-8")
    bad = tmp_path / "bad.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    bad.write_bytes(buf.getvalue())
    problems2, _ = gate.check(bad)
    assert any("threshold-forms" in p or "сумм" in p.lower()
               for p in problems2)


# ------------------- ревизия 1.13: версионированные байты warning

def _doc_fleet():
    return {"rules": [
        {"id": "R-F", "zone": "PAIRING", "kind": "DATA",
         "title": "перечень",
         "statement": "Все леги на воздушных судах типов "
                      "'Q7|Q9|QX1' маркируются как deadhead "
                      "(перегон/заброска).",
         "sources": [{"sid": "S6", "anchor": "т4с18"}]}],
        "objectives": [], "questions": []}


def test_форма_флота_текущая_версия_обезличена():
    """Ревизия 1.13 (зачистка): в новых байтах warning нет терминов
    конкретного заказчика; слово «ЗАПРЕЩЕНО» и носитель истины
    остаются."""
    from cdpm import threshold_forms as tf
    fleet = tf.assemble_fleet_dh_marked(_doc_fleet())
    assert "ЗАПРЕЩЕНО" in fleet["warning"]
    assert "Перегон" in fleet["warning"]
    assert "АЭ" not in fleet["warning"]
    # Версия 1.13 явно — те же новые байты.
    fleet13 = tf.assemble_fleet_dh_marked(_doc_fleet(), "1.13")
    assert fleet13["warning"] == fleet["warning"]


def test_форма_флота_легаси_байты_из_носителя_контура(tmp_path,
                                                      monkeypatch):
    """Пакеты ≤1.12 заморожены со старыми байтами; легаси-текст —
    данные заказчика, живёт в носителе контура. Синтетический
    носитель подтверждает закон версии; отсутствие носителя —
    честный отказ (замороженный пакет судится только на машине
    своего контура)."""
    import json as _j
    from cdpm import threshold_forms as tf
    carrier = tmp_path / "legacy-form-texts.json"
    carrier.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика",
        "fleet_dh_marked_warning": "старый текст предупреждения"},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_LEGACY_FORM_TEXTS", str(carrier))
    old = tf.assemble_fleet_dh_marked(_doc_fleet(), "1.12")
    assert old["warning"] == "старый текст предупреждения"
    new = tf.assemble_fleet_dh_marked(_doc_fleet(), "1.13")
    assert "старый текст" not in new["warning"]
    # Отказ без носителя: изоляция набора задаёт пустую переменную.
    monkeypatch.setenv("CDPM_LEGACY_FORM_TEXTS", "")
    import pytest as _pytest
    with _pytest.raises(ValueError, match="носитель контура"):
        tf.assemble_fleet_dh_marked(_doc_fleet(), "1.12")


def _doc_ladder():
    return {"rules": [
        {"id": "R-L1", "zone": "BOTH", "kind": "HARD",
         "title": "лестница +1",
         "statement": "Максимальное служебное время с отдыхом в "
                      "полете (доп. член экипажа): до 840 мин "
                      "(класс 3), до 900 мин (класс 2), до 960 мин "
                      "(класс 1).",
         "sources": [{"sid": "S2", "anchor": "п.1"}]},
        {"id": "R-L2", "zone": "PAIRING", "kind": "HARD",
         "title": "обе ступени",
         "statement": "Может быть увеличено до 840/900/960 минут "
                      "(или 900/960/1020 для расширенного экипажа) "
                      "в зависимости от класса места отдыха.",
         "sources": [{"sid": "S5", "anchor": "т.2"}]}],
        "objectives": [], "questions": []}


def test_примечание_лестницы_версия_1_14(tmp_path, monkeypatch):
    """Ревизия 1.14: note лестницы обезличено в новых байтах (без
    чисел ответа анкеты и ссылки на приложение); легаси ≤1.13 — из
    носителя контура; без носителя — честный отказ."""
    import json as _j
    from cdpm import threshold_forms as tf
    new = tf.assemble_extended_ladder(_doc_ladder())
    assert "18 ч" not in new["note"] and "прил" not in new["note"]
    assert tf.assemble_extended_ladder(
        _doc_ladder(), "1.14")["note"] == new["note"]
    carrier = tmp_path / "legacy.json"
    carrier.write_text(_j.dumps({
        "form_version": 1, "customer": "синтетика",
        "extended_ladder_note": "старое примечание лестницы"},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_LEGACY_FORM_TEXTS", str(carrier))
    old = tf.assemble_extended_ladder(_doc_ladder(), "1.13")
    assert old["note"] == "старое примечание лестницы"
    monkeypatch.setenv("CDPM_LEGACY_FORM_TEXTS", "")
    import pytest as _pytest
    with _pytest.raises(ValueError, match="носитель контура"):
        tf.assemble_extended_ladder(_doc_ladder(), "1.13")


def test_форма_календарного_окна_из_мягкого_правила():
    """Демо-трафарет 2026-08-29 (семья calendar_windows): SOFT-правило
    с именами дней недели, ночёвкой и ЯВНЫМ поясом базовой станции
    даёт форму calendar_window_penalties; дни — закрытым словарём
    имён в порядке недели; вес — из применённого ответа (без ответа —
    null); правило без явного пояса форму не даёт (не выдумываем).
    Контракт P-N-SHD 2026-08-30: форма — проекция семьи, отбор
    и дни читаются из export_solver классификатора."""
    from cdpm import threshold_forms as tf
    doc = {"rules": [
        {"id": "R-W7", "zone": "PAIRING", "kind": "SOFT",
         "title": "Ночёвки в конце недели",
         "statement": "Ночёвка вне базовой станции, календарный день "
                      "которой (по местному времени базовой станции) "
                      "приходится на пятницу или субботу, "
                      "штрафуется.",
         "sources": [{"sid": "S1", "anchor": "т3с7"}]},
        {"id": "R-NOTZ", "zone": "PAIRING", "kind": "SOFT",
         "title": "Без пояса",
         "statement": "Ночёвка в воскресенье нежелательна.",
         "sources": [{"sid": "S1", "anchor": "т3с8"}]}],
        "objectives": [], "questions": []}
    m.assign_soft_families(doc)
    got = tf.assemble_calendar_window_penalties(doc)
    assert len(got) == 1, "правило без явного пояса не выдумывается"
    f = got[0]
    assert f["days"] == ["friday", "saturday"]
    assert f["tz"] == "base-local"
    assert f["action"] == "layover_penalty"
    assert f["weight"] is None and f["rule"] == "R-W7"
    forms = tf.build_forms(doc)
    assert forms["calendar_window_penalties"] == got


def test_окно_ловит_родительный_падеж_ночёвок():
    # Живой пропуск ночи 30.08 (перевыпуск v2): «число ночёвок вне
    # базовой станции» — беглая гласная «ночёвОк» не ловилась
    # корнем «ночёвк», форма окон не рождалась и threshold-forms.json
    # выпадал из пакета целиком.
    doc = {"meta": {"revision": 2}, "rules": [{
        "id": "R-W7", "zone": "PAIRING", "kind": "SOFT",
        "title": "Ночёвки конца недели", "weight": 25,
        "statement": "penalty += 25 × (число ночёвок вне базовой "
                     "станции, календарный день которых по местному "
                     "времени базовой станции приходится на пятницу "
                     "или субботу)"}], "questions": []}
    m.assign_soft_families(doc)
    got = tf.assemble_calendar_window_penalties(doc)
    assert len(got) == 1
    assert got[0]["days"] == ["friday", "saturday"]
    assert got[0]["weight"] == 25 and got[0]["tz"] == "base-local"
