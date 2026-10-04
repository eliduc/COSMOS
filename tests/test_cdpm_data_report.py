# -*- coding: utf-8 -*-
"""Контракт отчёта 3.3 — качество операционных данных (ДЛ-1).

Охраняемое: машинная модель — единый источник истины (md рендерится
детерминированно, правка мимо модели ловится побайтно); статусы
проверок честные (fail-closed: «не прогнана» — с причиной, «прогнана»
— со счётом записей У-05); находка несёт адрес и доказательство;
основания трассируются к строкам свода, блокированные строки не
проверяются до ответа авиакомпании; счётчики сходятся с пересчётом."""
import copy
import json

import pytest

from cdpm import data_report as dr
from cdpm.gates import gate_data_report
from cdpm.sample import sample_model


@pytest.fixture()
def svod():
    return sample_model()


@pytest.fixture()
def clean():
    return dr.sample_data_report()


def test_sample_passes_contract_with_svod_trace(clean, svod):
    assert dr.validate_report(clean, svod) == []
    # Без свода контракт тоже консистентен (трассировка — гейтом).
    assert dr.validate_report(clean) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["findings"][0].update(evidence=""),
     "без доказательства"),
    (lambda d: d["findings"][0].update(text=" "),
     "пустая формулировка"),
    (lambda d: d["findings"][0].update(check="C-нет-такой"),
     "несуществующую проверку"),
    (lambda d: d["findings"][0].update(check="C-R-S1-П-1-1-0"),
     "не «прогнана»"),
    (lambda d: d["findings"][1].update(**{"class": "дубль"}),
     "не согласован с основанием"),
    (lambda d: d["findings"][0].update(severity="СТРАШНОЕ"),
     "вне перечня"),
    (lambda d: d["findings"][0].update(rule_refs=[]),
     "без ссылки на строку свода"),
    (lambda d: d["findings"][0]["target"].update(sid="D9"),
     "не объявлен"),
    (lambda d: d["findings"][0]["target"].update(row=0),
     "нет номера строки"),
    (lambda d: d["checks"][0].update(rows_checked=0),
     "У-05"),
    (lambda d: d["checks"][1].update(reason=""),
     "без названной причины"),
    (lambda d: d["checks"][2].update(
        basis={"builtin": "красота"}),
     "вне перечня"),
    (lambda d: d["checks"][0].update(basis={}),
     "РОВНО одно"),
    (lambda d: d["sources"][0].update(sha256=""),
     "не заморожены"),
    (lambda d: d["counters"].update(rows_total=999),
     "не сходятся с пересчётом"),
])
def test_controlled_defect_is_caught(clean, svod, mutate, needle):
    doc = copy.deepcopy(clean)
    mutate(doc)
    problems = dr.validate_report(doc, svod)
    assert any(needle in p for p in problems), problems


def test_svod_trace_catches_unknown_and_blocked_rules(clean, svod):
    doc = copy.deepcopy(clean)
    doc["checks"][0]["basis"] = {"rule": "R-НЕТ-ТАКОЙ"}
    assert any("нет в своде" in p
               for p in dr.validate_report(doc, svod))
    doc = copy.deepcopy(clean)
    doc["checks"][1].update(status="прогнана", rows_checked=40)
    assert any("блокирована вопросом" in p
               for p in dr.validate_report(doc, svod))
    # SOFT-строка — не основание проверки допустимости данных.
    doc = copy.deepcopy(clean)
    doc["checks"][0]["basis"] = {"rule": "R-S1-П-3-2"}
    doc["checks"][0].update(status="не прогнана", rows_checked=0,
                            reason="проверка типа не породилась")
    doc["findings"] = [f for f in doc["findings"]
                       if f["check"] != "C-R-S2-DATA-QUAL-0"]
    doc["counters"] = dr.metrics_of(doc)
    assert any("генерируются из" in p
               for p in dr.validate_report(doc, svod))


def test_render_is_deterministic_and_complete(clean):
    md1 = dr.render_data_report(clean)
    md2 = dr.render_data_report(copy.deepcopy(clean))
    assert md1 == md2                       # побайтно воспроизводим
    for needle in ("POLARIS-ROSTER.xlsx", "F-0001", "F-0002",
                   "R-S2-DATA-QUAL", "встроенная: формат",
                   "блокирована вопросом",   # причина не прогнанной
                   "лист «Ростер» · строка 17 · колонка C",
                   "около 9 утра"):
        assert needle in md1, needle


def test_counters_recompute(clean):
    m = dr.metrics_of(clean)
    assert m["files"] == 2 and m["rows_total"] == 52
    assert m["checks_run"] == 2 and m["checks_skipped"] == 2
    assert m["by_class"] == {"нарушение правила": 1, "формат": 1}
    assert m["by_severity"] == {"КРИТИЧЕСКОЕ": 1, "СЕРЬЁЗНОЕ": 1}


def test_gate_green_on_sample_and_red_on_tampered_md(tmp_path, capsys):
    from cdpm import model as m
    dr.dump_report(dr.sample_data_report(),
                   tmp_path / "data-report.json")
    (tmp_path / "data-report.md").write_text(
        dr.render_data_report(dr.sample_data_report()),
        encoding="utf-8")
    m.dump_model(sample_model(), tmp_path / "model.json")

    rc = gate_data_report.run(str(tmp_path / "data-report.json"),
                              str(tmp_path / "model.json"),
                              str(tmp_path / "data-report.md"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "рассмотрено: источников 2, записей 52, проверок 4 " \
           "(прогнано 2), находок 2" in out
    assert "самотест пройден" in out

    # Правка md мимо модели — красный гейт.
    md = (tmp_path / "data-report.md").read_text(encoding="utf-8")
    (tmp_path / "data-report.md").write_text(
        md.replace("строка 17", "строка 18"), encoding="utf-8")
    rc = gate_data_report.run(str(tmp_path / "data-report.json"),
                              str(tmp_path / "model.json"),
                              str(tmp_path / "data-report.md"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "правка мимо модели" in out

    # Без свода гейт честно называет непроверенную трассировку.
    rc = gate_data_report.run(str(tmp_path / "data-report.json"),
                              None, None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "свод не передан" in out


def test_cli_writes_sample_and_renders(tmp_path, capsys):
    assert dr.main(["--sample", str(tmp_path / "s")]) == 0
    got = json.loads((tmp_path / "s" / "data-report.json")
                     .read_text(encoding="utf-8"))
    assert got == dr.sample_data_report()

    out_dir = tmp_path / "r"
    rc = dr.main([str(tmp_path / "s" / "data-report.json"),
                  "--out-dir", str(out_dir)])
    assert rc == 0
    md = (out_dir / "data-report.md").read_text(encoding="utf-8")
    assert md == dr.render_data_report(dr.sample_data_report())
