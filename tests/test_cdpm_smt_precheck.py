# -*- coding: utf-8 -*-
"""Предпроверка SMT-решателя перед стартом «Проверить непротиворечивость».

Дефект с демо 06.09.2026 (Prod run-9013): на площадке без пакета
z3-solver кнопка запускала фоновую нить, которая падала с
«No module named 'z3'», — отказ читался только из статуса. Теперь
наличие решателя проверяется ДО старта: ответ 409 словами, статус
прогона получает «отказ SMT-проверки» с той же причиной, состояние
прогона (state) не занимается.
"""
import importlib.util
import json

import pytest
from fastapi.testclient import TestClient

from cdpm import webui
from test_cdpm_extract import fake_llm


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    yield


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def _plant_extracted_run(tmp_path, run_id="run-0007"):
    """Прогон с построенной моделью (extract/model.json) — то, что
    требуется ручке smt-verify до всякой проверки решателя."""
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "src").mkdir(parents=True)
    (run_dir / "extract").mkdir()
    (run_dir / "status.json").write_text(json.dumps(
        {"state": "извлечено", "files": ["RULES-A.md"]},
        ensure_ascii=False), encoding="utf-8")
    (run_dir / "extract" / "model.json").write_text(
        json.dumps({"rules": [], "questions": []}), encoding="utf-8")
    return run_dir


def test_smt_verify_refuses_in_words_when_z3_missing(client, tmp_path,
                                                     monkeypatch):
    run_dir = _plant_extracted_run(tmp_path)
    real_find_spec = importlib.util.find_spec

    def no_z3(name, *args, **kwargs):
        if name == "z3":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", no_z3)
    r = client.post(f"/api/runs/{run_dir.name}/smt-verify")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "z3-solver" in detail and "не установлен" in detail
    assert "pip install" in detail
    st = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert st["smt_state"] == "отказ SMT-проверки"
    assert "z3-solver" in st["smt_report"]["status"]
    # Состояние прогона не тронуто, нить не стартовала.
    assert st["state"] == "извлечено"
    assert not (run_dir / "extract" / "smt-report.json").exists()


def test_smt_verify_starts_when_z3_present(client, tmp_path):
    """Счастливый путь ручки: z3 в окружении есть (локально — да) — ответ
    200, статус переходит в «SMT-проверка идёт» (нить с поддельным
    каналом завершится отказом или отчётом — здесь важна только
    предпроверка, а не перевод)."""
    # Пакет может стоять, но не грузиться (libz3.dll под политикой
    # машины — см. conftest): тогда счастливого пути здесь нет.
    try:
        import z3  # noqa: F401
    except Exception as e:                                  # noqa: BLE001
        pytest.skip(f"z3 недоступен в этом окружении: {e}")
    run_dir = _plant_extracted_run(tmp_path, "run-0009")
    r = client.post(f"/api/runs/{run_dir.name}/smt-verify")
    assert r.status_code == 200, r.text
    st = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert st["smt_state"] in ("SMT-проверка идёт", "SMT-проверка готова",
                               "отказ SMT-проверки")
    assert "z3-solver" not in json.dumps(st.get("smt_report") or {},
                                         ensure_ascii=False)


def test_smt_verify_still_requires_model_first(client, tmp_path):
    run_dir = tmp_path / "runs" / "run-0008"
    (run_dir / "src").mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps(
        {"state": "заморожено", "files": []}), encoding="utf-8")
    r = client.post("/api/runs/run-0008/smt-verify")
    assert r.status_code == 409
    assert "модель ещё не построена" in r.json()["detail"]


# ── Рубеж непротиворечивости у ручек выпуска пакета (11.09.2026) ──────
# Выпуск пакета опирается на тот же отчёт и тот же канал роли, что
# кнопка выше; отказ рубежа приходит наружу 409 со СЛОВАМИ и машинным
# КОДОМ отказа.

def _smt_reply(asserts):
    return json.dumps({"decls": ["(declare-fun x () Int)"],
                       "asserts": asserts}, ensure_ascii=False)


def _plant_ready_run(tmp_path):
    """Прогон, готовый к выпуску пакета (фикстура моста А)."""
    from test_cdpm_task_package import _ready_doc, _run_dir
    run_dir = _run_dir(tmp_path / "runs", _ready_doc())
    (run_dir / "status.json").write_text(json.dumps(
        {"state": "извлечено", "kit": {"data_class": "synthetic"}},
        ensure_ascii=False), encoding="utf-8")
    return run_dir


def test_release_refusal_carries_words_and_machine_code(client, tmp_path,
                                                        monkeypatch):
    """Противоречие свода останавливает выпуск: 409, `detail` — СЛОВА
    строкой (их читают панель и раннер наборов), `code` — машинный код
    отказа рядом."""
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    # 12.09.2026 (A01, REPLY-AUDITOR-01/02): явный «false» — законный
    # перевод невыполнимого правила и отрицательный контроль выпуска:
    # конфликт с ядром из этого правила.
    monkeypatch.setattr(
        etp, "smt_channel_factory",
        lambda rd: (lambda prompt: _smt_reply([{"rule": "R-H",
                                                "smt": "false"}])))
    for fmt in ("task-package", "task-package-send"):
        r = client.get(f"/api/runs/{run_dir.name}/export/{fmt}")
        assert r.status_code == 409, r.text
        body = r.json()
        assert isinstance(body["detail"], str)
        assert "противоречие правил" in body["detail"]
        assert "R-H" in body["detail"]
        assert body["code"] == "smt-contradiction"
    # Проверка СОСТОЯЛАСЬ (вердикт получен) — в журнале она одно
    # закрытое действие на обе ручки: выпуск остановлен вердиктом, а не
    # бедой проверки, и второй выпуск берёт тот же отчёт.
    from cdpm import actions_journal
    acts = [a for a in actions_journal.actions(run_dir)
            if a.get("action") == "smt-verify"]
    assert len(acts) == 1 and acts[0]["done"] and not acts[0]["refused"]
    assert json.loads((run_dir / "extract" / "smt-report.json")
                      .read_text(encoding="utf-8"))["verdict"] == "unsat"


def test_release_refusal_incomplete_translation_carries_items(
        client, tmp_path, monkeypatch):
    """A01 (12.09.2026): неполный перевод останавливает выпуск кодом
    `smt-incomplete`, а тело 409 несёт перечень непереведённых правил
    ключом `items` (контракт П0 §1.4) рядом со словами и кодом."""
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    calls = []

    def translator(prompt):
        calls.append(prompt)
        return _smt_reply([])                    # ни одного утверждения
    monkeypatch.setattr(etp, "smt_channel_factory", lambda rd: translator)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "smt-incomplete"
    assert body["items"] == ["R-H"]
    assert "переведено 0 из 1" in body["detail"] and "R-H" in body["detail"]
    assert len(calls) == 2                       # один повтор с перечнем
    report = json.loads((run_dir / "extract" / "smt-report.json")
                        .read_text(encoding="utf-8"))
    assert report["verdict"] == "none" and report["untranslated"] == ["R-H"]
    # Проверка СОСТОЯЛАСЬ (отчёт получен, пусть и неполный) — действие
    # «smt-verify» в журнале закрыто, а не отказано: выпуск остановлен
    # вердиктом рубежа, как и при противоречии (закон 11.09.2026).
    from cdpm import actions_journal
    acts = [a for a in actions_journal.actions(run_dir)
            if a.get("action") == "smt-verify"]
    assert len(acts) == 1 and acts[0]["done"] and not acts[0]["refused"]
    # прежние отказы без перечня несут пустой `items`
    monkeypatch.setattr(
        etp, "smt_channel_factory",
        lambda rd: (lambda prompt: _smt_reply([
            {"rule": "R-H", "smt": "(and (<= x 1) (>= x 2))"}])))
    (run_dir / "extract" / "smt-report.json").unlink()
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409 and r.json()["code"] == "smt-contradiction"
    assert r.json()["items"] == []


def test_release_gate_failure_is_journaled_as_refused_action(
        client, tmp_path, monkeypatch):
    """Беда самой проверки (канал переводчика не построился) — действие
    прогона записано ОТКАЗОМ 409 теми же словами, что уходят наружу:
    проверка не состоялась, и это видно в журнале, а не только в
    ответе ручки."""
    from cdpm import actions_journal
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)

    def refuse(rd):
        raise RuntimeError("раскладка моделей ролей не объявлена")
    monkeypatch.setattr(etp, "smt_channel_factory", refuse)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "smt-unavailable"
    acts = [a for a in actions_journal.actions(run_dir)
            if a.get("action") == "smt-verify"]
    assert len(acts) == 1 and acts[0]["refused"] and acts[0]["status"] == 409
    assert "раскладка моделей ролей не объявлена" in acts[0]["detail"]
    assert not (run_dir / "extract" / "smt-report.json").exists()
    # 12.09.2026 (контракт П0 §1.4): запись отказа несёт машинный код и
    # перечень (здесь перечня нет — пустой).
    rec = [r for r in actions_journal.read(run_dir)
           if r.get("kind") == "refused"][-1]
    assert rec["code"] == "smt-unavailable" and rec["items"] == []


def test_old_refusals_keep_empty_code(client, tmp_path):
    """Прежние отказы выпуска кода не несут: ключ `code` пустой, и
    читатель, который кода не знает, читает ответ как раньше."""
    run_dir = tmp_path / "runs" / "run-0011"
    (run_dir / "extract").mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps(
        {"state": "заморожено"}), encoding="utf-8")
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    assert "модель прогона не построена" in r.json()["detail"]
    assert r.json()["code"] is None


def test_release_gate_uses_run_channel_and_is_counted_in_journal(
        client, tmp_path, monkeypatch):
    """Рубеж берёт переводчика ТОЙ ЖЕ горловиной каналов, что кнопка
    панели (роль `smt_translator` прогона), и вызов ложится в журнал
    вызовов прогона — в записи о прогоне он сочтён наравне с
    остальными."""
    from cdpm import call_journal
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    # Шов рубежа — умолчание (канал панели), подменяется только сама
    # модель: тот же шов, что у прочих стадий набора.
    monkeypatch.setattr(etp, "smt_channel_factory", etp._panel_smt_channel)
    monkeypatch.setattr(
        webui, "exchange_llm_factory",
        lambda scope, requested: (
            lambda prompt: _smt_reply([{"rule": "R-H", "smt": "(<= x 1)"}])))
    before = call_journal.count(run_dir)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert call_journal.count(run_dir) == before + 1
    report = json.loads((run_dir / "extract" / "smt-report.json")
                        .read_text(encoding="utf-8"))
    assert report["verdict"] == "sat"
    # Второе нажатие берёт вердикт из свежего отчёта — модель не зовётся.
    assert client.get(
        f"/api/runs/{run_dir.name}/export/task-package").status_code == 200
    assert call_journal.count(run_dir) == before + 1


def test_release_gate_check_is_journaled_as_action_and_counted_in_record(
        client, tmp_path, monkeypatch):
    """Проверка рубежа — ДЕЙСТВИЕ прогона «smt-verify» (то же имя, что у
    кнопки панели): вызов переводчика получает номер действия, и запись
    о прогоне считает его стадией SMT-проверки.

    Живая находка run-0163 (11.09.2026): ручки выпуска пакета сами
    действием не журналируются, и вызов рубежа ложился в журнал вызовов
    «ничьим» (`action_n: null`) — запись о прогоне насчитывала 26
    вызовов CDPM из 27, а сверка площадки показывала not_counted=1."""
    from cdpm import actions_journal, call_journal
    from cdpm import export_task_package as etp
    from cdpm import llm_usage
    run_dir = _plant_ready_run(tmp_path)
    monkeypatch.setattr(etp, "smt_channel_factory", etp._panel_smt_channel)

    def _translator(prompt):
        llm_usage.report("cloud", "m-smt", input_tokens=11, output_tokens=3)
        return _smt_reply([{"rule": "R-H", "smt": "(<= x 1)"}])
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: _translator)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 200, r.text
    # Действие журнала: одно, закрытое, названо тем же именем, что у
    # кнопки, и честно говорит, кто его начал.
    acts = [a for a in actions_journal.actions(run_dir)
            if a.get("action") == "smt-verify"]
    assert len(acts) == 1 and acts[0]["done"] and not acts[0]["refused"]
    assert acts[0]["kind"] == "sync"
    assert acts[0]["params"]["origin"] == "гейт выпуска пакета"
    n = acts[0]["n"]
    assert acts[0]["calls"] == [0, 1]
    # Вызов переводчика — этого действия, а не «ничей».
    calls = [c for c in call_journal.read_index(run_dir)
             if c.get("kind") != "end"]
    assert [(c["role"], c["action_n"]) for c in calls] == [
        ("smt_translator", n)]
    # …и стадия у него названа: рубеж линию статуса не занимает, стадию
    # вызову даёт словарь действий записи — теми же словами, какими
    # зовёт себя проверка у кнопки панели («SMT-проверка идёт»).
    # Живая находка run-0166 (11.09.2026): запись вызов считала, а
    # индекс журнала называл стадию null.
    from cdpm import run_record as rec_mod
    assert [c["stage"] for c in calls] == ["SMT-проверка"]
    assert calls[0]["stage"] == rec_mod.action_stage_words("smt-verify")
    # Сводка расхода моделей записи знает стадию вызова (роль × провайдер
    # × модель × стадия): строка без стадии слилась бы с чужими.
    summary = rec_mod.models_summary(run_dir)
    assert [(r["role"], r["stage"], r["calls"]) for r in summary["models"]] \
        == [("smt_translator", "SMT-проверка", 1)]
    # Запись о прогоне: стадия SMT-проверки с одним сочтённым вызовом
    # роли переводчика, и общий счёт вызовов записи равен числу вызовов
    # журнала (сверка площадок calls == counted).
    rec = client.get(f"/api/runs/{run_dir.name}/record")
    assert rec.status_code == 200, rec.text
    entries = [e for e in rec.json()["journal"]
               if e["substage"] == "smt-verify"]
    assert len(entries) == 1
    assert entries[0]["stage"] == "smt_verify"
    assert entries[0]["system"] == "cdpm" and entries[0]["outcome"] == "успех"
    assert entries[0]["resources"]["models"] == [
        {"role": "smt_translator", "model": "m-smt", "provider": "cloud",
         "calls": 1, "input_tokens": 11, "output_tokens": 3,
         "counted": True, "note": None}]
    counted = sum(m["calls"] for e in rec.json()["journal"]
                  for m in e["resources"]["models"])
    assert counted == len(calls) == 1


def test_release_with_fresh_report_adds_no_action_and_no_calls(
        client, tmp_path, monkeypatch):
    """Свежий отчёт — рубеж НИЧЕГО не делает: второй выпуск того же
    свода не рождает ни действия, ни вызова, а пакет байт в байт
    прежний (Ф-1.7). Учёт проверки действием цену рубежа не меняет:
    один вызов на свод, а не на нажатие."""
    from cdpm import actions_journal, call_journal
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    monkeypatch.setattr(etp, "smt_channel_factory", etp._panel_smt_channel)
    monkeypatch.setattr(
        webui, "exchange_llm_factory",
        lambda scope, requested: (
            lambda prompt: _smt_reply([{"rule": "R-H", "smt": "(<= x 1)"}])))
    first = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert first.status_code == 200, first.text
    acts_after_first = actions_journal.count(run_dir)
    calls_after_first = call_journal.count(run_dir)
    assert acts_after_first == 1 and calls_after_first == 1
    second = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert second.status_code == 200, second.text
    assert actions_journal.count(run_dir) == acts_after_first
    assert call_journal.count(run_dir) == calls_after_first
    assert second.content == first.content


def test_release_refusal_names_malformed_translator_record(
        client, tmp_path, monkeypatch):
    """A01-F2 (вердикт аудитора 12.09.2026): запись вне формы в ответе
    переводчика (`null` рядом с верным переводом) — не молчаливое
    отбрасывание: один повтор, затем сквозной 409 `smt-incomplete` с
    позицией и видом записи в `items` и в словах."""
    from cdpm import export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    calls = []

    def translator(prompt):
        calls.append(prompt)
        return json.dumps({"decls": ["(declare-fun x () Int)"],
                           "asserts": [{"rule": "R-H", "smt": "(<= x 1)"},
                                       None]}, ensure_ascii=False)
    monkeypatch.setattr(etp, "smt_channel_factory", lambda rd: translator)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "smt-incomplete"
    assert body["items"] == ["asserts[1]"]
    assert "asserts[1]: не объект (null)" in body["detail"]
    assert len(calls) == 2
    report = json.loads((run_dir / "extract" / "smt-report.json")
                        .read_text(encoding="utf-8"))
    assert report["verdict"] == "none"
    assert report["malformed"] == [{"field": "asserts", "index": 1,
                                    "kind": "null", "text": "null"}]


def _plant_promotion_run(tmp_path, with_existing_hard):
    """Прогон контрпримера аудитора A01-F1: правило с устаревшим ярлыком
    SOFT посажено на жёсткую семью каталога — сборка пакета делает его
    жёстким, и выпускаемый свод отличается от канонического."""
    from test_cdpm_a01_smt_complete import _promotion_doc
    from test_cdpm_task_package import _run_dir
    run_dir = _run_dir(tmp_path / "runs", _promotion_doc(with_existing_hard))
    (run_dir / "status.json").write_text(json.dumps(
        {"state": "извлечено", "kit": {"data_class": "synthetic"}},
        ensure_ascii=False), encoding="utf-8")
    return run_dir


def _arith_reply(prompt):
    """Переводчик по заданию рубежа: R-A → x ≤ 1, прочие → x ≥ 2."""
    import re
    ids = re.findall(r"(?m)^(R-[A-Z]+) \| ", prompt)
    return _smt_reply([{"rule": r, "smt": "(<= x 1)" if r == "R-A"
                        else "(>= x 2)"} for r in ids])


def test_release_of_a_promoted_rule_is_refused_over_http(client, tmp_path,
                                                         monkeypatch):
    """A01-F1 сквозь ручку: свежий отчёт кнопки «sat 1/1» о каноническом
    своде не выпускает пакет, в котором сборка сделала второе правило
    жёстким. Ответ 409 со словами, кодом и перечнем; отчёт кнопки не
    переписан, отчёт ПАКЕТА записан отдельным файлом."""
    from cdpm import export_task_package as etp, smt_verify as sv
    run_dir = _plant_promotion_run(tmp_path, with_existing_hard=True)
    canon = (run_dir / "extract" / "model.json").read_bytes()
    button = sv.verify(json.loads(canon.decode("utf-8")),
                       lambda p: _smt_reply([{"rule": "R-A",
                                              "smt": "(<= x 1)"}]),
                       model_sha256=sv.model_fingerprint(canon))
    assert button["verdict"] == "sat" and button["n_translated"] == 1
    sv.write_report(run_dir, button)
    monkeypatch.setattr(etp, "smt_channel_factory", lambda rd: _arith_reply)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "smt-contradiction"
    assert "R-PROMOTED — стало жёстким при сборке (SOFT→HARD)" in body["detail"]
    assert "R-PROMOTED" in body["detail"] and "R-A" in body["detail"]
    # Отчёт кнопки не тронут; вердикт вынесен отдельным отчётом пакета.
    assert sv.read_report(run_dir, sv.CANONICAL) == button
    pkg_report = sv.read_report(run_dir, sv.PACKAGE)
    assert pkg_report["verdict"] == "unsat"
    assert set(pkg_report["core"]) == {"R-A", "R-PROMOTED"}


def test_release_of_a_promoted_rule_ships_the_package_report_over_http(
        client, tmp_path, monkeypatch):
    """Положительный контроль того же пути: канонический свод без единого
    жёсткого правила («none 0/0» прежней сборки), выпускаемый — с одним.
    Пакет выходит только с переводом этого правила: манифест называет
    предмет и отчёт пакета, копия отчёта едет в архиве."""
    import io
    import zipfile
    from cdpm import export_task_package as etp, smt_verify as sv
    run_dir = _plant_promotion_run(tmp_path, with_existing_hard=False)
    monkeypatch.setattr(etp, "smt_channel_factory", lambda rd: _arith_reply)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        exported = json.loads(z.read("model.json"))
        shipped = z.read("smt-report.json")
    cons = manifest["consistency"]
    assert cons["verdict"] == "sat" and (cons["n_rules"],
                                         cons["n_translated"]) == (1, 1)
    assert cons["subject"] == "package"
    assert cons["report"] == sv.PACKAGE_REPORT_REL
    assert cons["subject_sha256"] == sv.subject_fingerprint(exported)
    assert shipped == (run_dir / "extract"
                       / "smt-report-package.json").read_bytes()
    assert not (run_dir / "extract" / "smt-report.json").exists()


def test_release_refuses_over_http_when_the_report_path_is_occupied(
        client, tmp_path, monkeypatch):
    """Беда САМОЙ проверки (находка неавторского ревью 12.09.2026): на
    месте `extract/smt-report.json` — каталог. Ручка отвечает 409 со
    словами и кодом `smt-unavailable` (прежде наружу уходил HTTP 500
    трассировкой PermissionError), вызов модели не тратится, а журнал
    действий с ответом не расходится: несостоявшейся проверки в нём
    нет вовсе — тем же законом, что у площадки без решателя."""
    from cdpm import actions_journal, export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    (run_dir / "extract" / "smt-report.json").mkdir()
    built = []

    def factory(rd):
        built.append(str(rd))
        return lambda prompt: _smt_reply([{"rule": "R-H", "smt": "(<= x 1)"}])
    monkeypatch.setattr(etp, "smt_channel_factory", factory)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "smt-unavailable"
    assert "некуда положить" in body["detail"]
    assert "smt-report.json" in body["detail"]
    assert body["items"] == []
    assert built == []                     # канал переводчика не строился
    rows = actions_journal.read(run_dir)
    # Заранее видная беда судится ДО действия — как «решателя нет»:
    # в журнале нет ни действия «smt-verify», ни записи об отказе
    # вовсе (второе ревью: утверждение усилено до пустого журнала —
    # прежде журнал писал «refused 409, code null», а наружу шло 500).
    assert rows == [], rows


def test_journal_and_answer_agree_when_the_report_write_fails(
        client, tmp_path, monkeypatch):
    """Та же беда, но заранее НЕ видная: путь отчёта свободен, а запись
    падает уже ПОСЛЕ вердикта (кончился диск). Отказ выносится ВНУТРИ
    области действия — и журнал действий говорит ровно то же, что
    ответ: «отказ 409» с кодом `smt-unavailable`."""
    import pathlib
    from cdpm import actions_journal, export_task_package as etp
    run_dir = _plant_ready_run(tmp_path)
    calls = []

    def translator(prompt):
        calls.append(prompt)
        return _smt_reply([{"rule": "R-H", "smt": "(<= x 1)"}])
    monkeypatch.setattr(etp, "smt_channel_factory", lambda rd: translator)
    real_write_text = pathlib.Path.write_text

    def no_space(self, *a, **k):
        if self.name.startswith("smt-report"):
            raise OSError(28, "No space left on device")
        return real_write_text(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "write_text", no_space)
    r = client.get(f"/api/runs/{run_dir.name}/export/task-package")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "smt-unavailable"
    assert "не записан" in body["detail"]
    assert len(calls) == 1                 # вердикт был получен
    refused = [x for x in actions_journal.read(run_dir)
               if x.get("kind") == "refused"]
    assert len(refused) == 1
    assert refused[0]["status"] == 409
    assert refused[0]["code"] == body["code"] == "smt-unavailable"
    assert refused[0]["items"] == body["items"] == []
    assert refused[0]["detail"][:100] == body["detail"][:100]
