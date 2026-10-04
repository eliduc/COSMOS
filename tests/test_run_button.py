# -*- coding: utf-8 -*-
"""Г-C1: оркестратор кнопки «документы → расписание».

Охраняемое: каждый шов отказывает СВОИМИ словами и выдача пишется
всегда (остановка — тоже выдача); вопрос анкеты — законная остановка
«ожидает ответа человека» с текстами вопросов; «упал» решателя не
превращается в вердикт допустимости; показания чужого прогона —
отказ; недопустимость сертификата — verdict «недопустимо», не
«успех»; трассировка (прогон cdpm + runId решателя + отпечаток
пакета) — в выдаче."""
import json
from pathlib import Path

import pytest

from vmp.run import button as btn
from cdpm import model as m
from vmp.run import roster_certify as rc
from test_run_certify import _compiler, _solution, _svod


class FakeSolver:
    base = "http://fake:9098"

    def __init__(self, healthy=True, states=("считает", "готов"),
                 failure=None, applied_run_id=None, status_task=None,
                 status_chains=None):
        self.healthy = healthy
        self.states = list(states)
        self.failure = failure
        self.applied_run_id = applied_run_id
        self.status_task = status_task
        self.status_chains = status_chains
        self.started = None
        self.starts = []
        self.breakdown_calls = []

    def health(self):
        return self.healthy

    def start(self, payload, zip_b64, mode, objective_id, limit_s,
              task="rostering", pairing_run_id=None, moves=None):
        self.started = {"mode": mode, "objective": objective_id,
                        "limit": limit_s, "task": task,
                        "pairingRunId": pairing_run_id,
                        "moves": moves}
        self.starts.append(self.started)
        return {"runId": "r-77", "state": "считает",
                "packageFingerprint": "fp-1", "mode": mode}

    def score_breakdown(self, run_id, problem):
        # Как настоящий /run/{id}/score-breakdown: верхнее поле
        # score строкой (рецепт решателя: брать его целиком) +
        # карта constraints с вкладами литеральными весами.
        # Аудит A8 (04.09.2026): разбор согласован со статусом прогона
        # (0hard/0soft) и сводом БЕЗ мягких правил — прежний разбор
        # (−80 soft по ограничению вне ВМП) был законным «разошлось»
        # штрафной сверки, которое кнопка молча клала после вердикта;
        # теперь сверка пересчитывает флаг, и зелёный путь обязан быть
        # согласован по всем частям.
        self.breakdown_calls.append(run_id)
        return {"score": "0hard/0soft", "soft": 0, "hard": 0,
                "constraints": {},
                "packageFingerprint": "fp-1"}

    def status(self, run_id):
        state = self.states.pop(0) if len(self.states) > 1 \
            else self.states[0]
        out = {"state": state}
        if state == "упал":
            out["failure"] = self.failure or "причина не названа"
        if state == "готов":
            out["score"] = "0hard/0soft"
            if self.status_task:
                # Как настоящий решатель @ 750ec18: задача едет в
                # статусе прогона.
                out["task"] = self.status_task
            if self.status_chains is not None:
                # Как настоящий решатель @ 0156b31: фиксация пейринга
                # подтверждается статусом (pairingRunId + chains).
                out["pairingRunId"] = "r-пейринг"
                out["chains"] = self.status_chains
        return out

    def result(self, run_id):
        return {"solved": True}

    def applied(self, run_id):
        # Как настоящий /run/{id}/applied-rules: БЕЗ lastRun.score
        # (счёт живёт в статусе прогона) — живая мелочь кнопки.
        return {"runId": self.applied_run_id or run_id}

    def pairing_catalog(self, payload, zip_b64, catalog_b64, sha):
        # Г-П4: как настоящая ручка /schedules/pairing-catalog.
        self.catalog_call = {"sha": sha}
        return {"runId": "r-каталог", "packageFingerprint": "fp-1"}


def _doc():
    """Свод сертификационных тестов БЕЗ мягкого правила: мягкое без
    веса законно блокирует гейт пакета, а кнопочным сценариям нужен
    экспортируемый свод. Аудит A8 (04.09.2026): «допустимо» выдаётся
    только при полном покрытии, поэтому R-QUAL (справочника допусков у
    фейкового компилятора нет) исключено по решению авиакомпании;
    стенд с непокрытым R-QUAL — tests/test_audit_a8_certify.py."""
    doc = _svod()
    doc["rules"] = [r for r in doc["rules"] if r["kind"] != "SOFT"]
    for r in doc["rules"]:
        if r["id"] == "R-QUAL":
            r["excluded_until_data"] = {"question": "Q-3b-ДАННЫЕ",
                                        "revision": 1}
    return doc


def _run_dir(tmp_path, doc):
    (tmp_path / "extract").mkdir(parents=True)
    m.dump_model(doc, tmp_path / "extract" / "model.json")
    return tmp_path


REAL_PRESS_REGISTRY = btn.press_registry
H0_ETALON = (Path(btn.__file__).resolve().parent.parent.parent / "open-bench"
             / "polyaris-h0" / "etalon" / "VMP-H0.json")


def _h0_doc():
    """Настоящая модель набора H0 (эталон): посадки A/B на семьи базы
    шаблонов — вывод реестра закрывает все правила расчётных зон."""
    return json.loads(H0_ETALON.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _catalog_requests_env(monkeypatch, tmp_path):
    """Заявки каталога шаблонов (шаг 6): очередь продукта и машинный
    канал — носители контура. Без изоляции нажатие в тестах писало бы в
    живой каталог данных машины и стучалось в живую службу (правило
    изоляции носителей 05.09). Автоподача остаётся включённой: её и
    судят тесты; тест выключения задаёт «0» сам."""
    from vmp import catalog_requests as vcr
    monkeypatch.setenv(vcr.ENV_OUTBOX, str(tmp_path / "catalog-queue"))
    monkeypatch.delenv(vcr.ENV_AUTO, raising=False)
    monkeypatch.delenv(vcr.ENV_API_URL, raising=False)
    monkeypatch.delenv(vcr.ENV_TOKEN, raising=False)


@pytest.fixture(autouse=True)
def _vmp_registry(monkeypatch):
    """Ш-1п2 (единая проверка соответствия ВМП, 2026-08-28): кнопочным
    сценариям на ЛЕГАСИ-своде (правила без посадок) даётся реестр,
    закрывающий все правила расчётных зон свода, с живыми отпечатками
    кода — иначе fail-closed гейт законно останавливал бы каждый
    зелёный путь. С 2026-09-03 кнопка выводит реестр сама при нажатии
    (press_registry), поэтому фикстура подменяет именно вывод; тесты
    самого вывода возвращают настоящий (REAL_PRESS_REGISTRY)."""
    from pathlib import Path as _P
    from vmp import registry_check as _regc
    root = _P(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {
            "created": "тестовая фикстура", "scope": "кнопка: тест",
            "code_fingerprints": _regc.code_fingerprints(root),
            "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                       "title": r.get("title") or ""}
                      for r in (model or {}).get("rules") or []],
        }
        path = _P(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path

    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)
    yield


def _fakes(good=True):
    """Подмены швов пакета и сертификации для зелёного пути."""
    def load_pkg(path):
        assert path.is_file()
        return {"manifest": {
            "allowed_modes": {"rostering": ["feasibility-hard"]},
            "spec_version": "1.6"}}

    def to_payload(pkg):
        return {"employees": []}

    def build_input(pkg, payload, solved, applied, name):
        return _solution(good, good), {"дома": "12:00"}

    def certify_fn(doc, solution):
        return rc.certify(doc, solution, _compiler)

    return dict(load_pkg=load_pkg, to_payload=to_payload,
                build_input=build_input, certify_fn=certify_fn)


def test_button_green_path_delivers_with_trace(tmp_path):
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    d = btn.orchestrate(run, solver, sleep=lambda *_: None,
                        **_fakes(True))
    assert d["verdict"] == "допустимо", d
    assert d["certified"] is True
    # A8: трёхчастный результат в выдаче — допустимость подтверждена
    # полным покрытием, гейт сертификата зелёный.
    assert d["result"]["feasibility"] == "confirmed"
    assert d["result"]["coverage_complete"] is True
    assert d["result"]["optimality"] == "feasible_found"
    assert d["uncovered"] == [] and d["certificate_gate"] == []
    # solver_hard при допустимости — явный ноль, не null (шапка
    # прогона уходит в сертификацию из показаний решателя).
    assert d["solver_hard"] == 0, d.get("solver_hard")
    assert d["counted_share"]["of"] >= 1
    # H11-6: область учёта ночёвок и определённость стоимости — в
    # выдаче (мягких правил у свода нет → оценки нет, стоимость full).
    assert "cost_assessment" in d and d["cost_assessment"] is None
    assert d["cost_certainty"] == "full" and d["cost_uncertain_events"] == 0
    # C-6: покрытие окон проверок допустимости — в выдаче, те же числа,
    # что в шапке сертификата (у стенда проверок окон нет — нули и
    # пустой перечень, а не отсутствие ключа).
    cert = json.loads((run / "button" / "certificate.json")
                      .read_text(encoding="utf-8"))["certificate"]
    assert d["window_coverage"] == cert["window_coverage"]
    assert set(d["window_coverage"]) == {"expected", "evaluated", "unknown", "checks"}
    assert d["window_coverage"]["expected"] == (
        d["window_coverage"]["evaluated"] + d["window_coverage"]["unknown"])
    delivery = json.loads((run / "button" / "delivery.json")
                          .read_text(encoding="utf-8"))
    assert delivery["window_coverage"] == d["window_coverage"]
    assert d["trace"] == {"cdpm_run": run.name,
                          "solver_run_id": "r-77",
                          "task": "rostering",
                          "package_fingerprint": "fp-1",
                          "spec_version": "1.6",
                          "coverage_authorization": None}
    assert solver.started["mode"] == "feasibility-hard"
    assert solver.started["task"] == "rostering"
    for name in ("delivery.json", "log.json", "certificate.json",
                 "certificate.md", "package.zip"):
        assert (run / "button" / name).is_file(), name
    log = json.loads((run / "button" / "log.json")
                     .read_text(encoding="utf-8"))
    steps = [e["step"] for e in log]
    for step in ("Ш-0", "Ш-1", "Ш-2", "Ш-2а", "Ш-3", "Ш-4"):
        assert step in steps, steps


def test_button_infeasible_is_not_success(tmp_path):
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                        **_fakes(False))
    assert d["verdict"] == "недопустимо"
    assert d["certified"] is False
    assert d["findings"] > 0


def test_button_solver_down_stops_before_export(tmp_path):
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(healthy=False),
                        sleep=lambda *_: None, **_fakes())
    assert d["verdict"] == "остановка: решатель недоступен"
    assert "fake:9098" in d["detail"]
    assert not (run / "button" / "package.zip").exists()


def test_button_open_question_waits_for_human(tmp_path):
    doc = _doc()
    doc["questions"] = [{
        "id": "Q-2-X", "part": "2", "title": "Развилка",
        "text": "как читать?", "answer_format": {
            "type": "choice", "options": [
                {"key": "а", "text": "т"}, {"key": "б", "text": "у"}]},
        "rule_refs": ["R-REST"],
        "source_refs": [{"sid": "S1", "anchor": "п.1"}]}]
    run = _run_dir(tmp_path, doc)
    d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                        **_fakes())
    assert d["verdict"] == "ожидает ответа человека"
    assert d["questions"] == [{"id": "Q-2-X", "title": "Развилка",
                               "text": "как читать?"}]
    assert "не дублируются" in d["note"]


_CATALOG_OK = {"selected": [{"fns": ["G-1"], "duty_fns": [["G-1"]]}],
               "coverage": {"verdict": "OK"}}
_APPLIED = {"min_sit_home_min": 60, "max_duty_span_min": 720,
            "min_rest_home_min": 720, "min_rest_outstation_min": 600}


def _builder_fakes(fakes, catalog=None):
    """Швы Г-П4: пейринг строит ortools-построитель (директива
    владельца 2026-08-26); пороги — из applied отчёта покрытия."""
    fakes["to_payload"] = lambda pkg: {
        "employees": [{"id": "E1", "base": "SVG"}],
        "flights": [{"departureUTCDateTime": "2026-09-01T08:00:00Z",
                     "arrivalUTCDateTime": "2026-09-02T10:00:00Z"}]}
    fakes["coverage_fn"] = lambda solver, zb, task: {
        "uncovered": [], "objectives": [],
        "applied": dict(_APPLIED)}
    fakes["pairing_builder"] = (
        lambda payload, args, out:
        json.dumps(catalog or _CATALOG_OK).encode("utf-8"))
    return fakes


def test_button_pairing_first_press(tmp_path):
    """Двухфазный поток, первое нажатие — пейринг: Г-П4 — строит
    ortools-построитель (пороги из applied со следом), каталог
    регистрируется ручкой решателя, Timefold-прогон НЕ стартует;
    артефакты — в button-pairing/."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    fakes = _builder_fakes(_fakes(True))

    def load_pkg(path):
        return {"manifest": {
            "allowed_modes": {"pairing": ["feasibility-hard"],
                              "rostering": ["optimization"]},
            "objectives": {"rostering": ["G-1"]},
            "spec_version": "1.6"}}

    fakes["load_pkg"] = load_pkg
    d = btn.orchestrate(run, solver, task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    assert d["chains"] == 1
    assert d["coverage_verdict"] == "OK"
    assert d["schedule"]["solver_run_id"] == "r-каталог"
    assert solver.started is None      # Timefold-пейринг исключён
    assert solver.catalog_call["sha"] == d["catalog_sha256"]
    for name in ("delivery.json", "log.json", "builder-input.json"):
        assert (run / "button-pairing" / name).is_file(), name
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert binp["provenance"]["--sit-min"]["from"] \
        == "min_sit_home_min"
    assert not (run / "button").exists()


def test_button_pairing_delivery_shows_three_uncovered_outcomes(tmp_path):
    """Аудит A4 (04.09.2026): выдача кнопки несёт три исхода по
    непокрытым — доказано невозможно (excluded_proven, с записями
    доказательств) и поиск не завершён / не найдено (search_incomplete с
    причиной словами); частичное покрытие ≠ выполненный план:
    coverage.complete=false виден получателю прямо в выдаче."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    proof = {"leg": "G-9", "kind": "graph_unreachable",
             "detail": "станция вылета недостижима из баз",
             "horizon": {"pairing_span_min": 10080, "bases": ["SVG"]},
             "data_fingerprint": "f" * 64}
    catalog = {
        "selected": [{"fns": ["G-1"], "duty_fns": [["G-1"]]}],
        "coverage": {"verdict": "INCOMPLETE_SEARCH", "complete": False,
                     "uncovered": 2, "excluded_proven": ["G-9"],
                     "search_incomplete": True,
                     "not_built_reasons": {"G-7": {
                         "search_incomplete": True,
                         "reason": "предел времени"}}},
        "proven_impossible_legs": ["G-9"],
        "proven_impossible_proofs": [proof]}
    fakes = _builder_fakes(_fakes(True), catalog=catalog)

    def load_pkg(path):
        return {"manifest": {
            "allowed_modes": {"pairing": ["feasibility-hard"],
                              "rostering": ["optimization"]},
            "objectives": {"rostering": ["G-1"]},
            "spec_version": "1.6"}}

    fakes["load_pkg"] = load_pkg
    d = btn.orchestrate(run, solver, task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    assert d["coverage_verdict"] == "INCOMPLETE_SEARCH"
    assert d["coverage"] == {
        "complete": False,
        "excluded_proven": ["G-9"],
        "search_incomplete": {"G-7": {"search_incomplete": True,
                                      "reason": "предел времени"}}}
    assert d["proven_impossible_legs"] == ["G-9"]
    assert d["proven_impossible_proofs"] == [proof]
    delivery = json.loads((run / "button-pairing" / "delivery.json")
                          .read_text(encoding="utf-8"))
    assert delivery["coverage"]["complete"] is False


def test_button_pairing_passes_dh_options_depth(tmp_path):
    """Приёмка №27: кнопка углубляет варианты перегонов собственного
    перечислителя (--dh-options 4) с происхождением в следе."""
    run = _run_dir(tmp_path, _doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    s = " ".join(seen["args"])
    assert "--dh-options 4" in s
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert binp["provenance"]["--dh-options"]["value"] == 4
    # Пакет улучшений №30 (а): обходы патологий CP-SAT 9.15 в профиле
    # кнопки — предобработка выключена, бригада поиска ≤ 8; оба флага
    # с происхождением в следе.
    assert "--no-presolve" in s
    assert "--workers 8" in s
    assert binp["provenance"]["--no-presolve"]["value"] is True
    assert binp["provenance"]["--workers"]["value"] == 8


def _pairing_fakes_min(tmp_path):
    """Общая заготовка пейринг-нажатия для тестов витка «путь к
    нулю» (ёмкость баз + тёплый старт)."""
    run = _run_dir(tmp_path, _doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    return run, fakes


def test_button_pairing_base_capacity_needs_warm_start(tmp_path):
    """Урок v5 (2026-08-26): жёсткая ёмкость без тёплого старта —
    отказ поиска; кнопка останавливается словами ДО запуска."""
    run, fakes = _pairing_fakes_min(tmp_path)
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        base_capacity=True,
                        sleep=lambda *_: None, **fakes)
    assert "тёплым стартом" in d["verdict"], d


def test_кнопка_выводит_реестр_под_модель_прогона_сама(tmp_path,
                                                       monkeypatch):
    """Поручение владельца 2026-09-03: реестр исполнителей — снимок кода
    на момент нажатия, выведенный под модель ЭТОГО пакета; статический
    файл площадки (CREW_VMP_REGISTRY) не читается — заведомо кривой
    путь в переменной кнопке не мешает, файл реестра появляется в
    каталоге прогона, все правила расчётной зоны в нём закрыты."""
    from vmp import registry_check as _regc
    monkeypatch.setattr(btn, "press_registry", REAL_PRESS_REGISTRY)
    run = _run_dir(tmp_path, _h0_doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    monkeypatch.setenv("CREW_VMP_REGISTRY",
                       str(tmp_path / "нет-реестра.json"))
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] != "остановка: ВМП не закрыт шаблонами COSMOS", d
    reg_path = run / "registry" / "vmp-executors-registry.json"
    assert reg_path.is_file()
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    pair_ids = {str(r["id"]) for r in _h0_doc()["rules"]
                if r.get("zone") in ("PAIRING", "BOTH")
                and r.get("kind") in ("HARD", "SOFT")}
    got = {r["rule_id"]: r["verdict"] for r in reg["rules"]}
    assert pair_ids <= set(got)
    assert all(got[i] in _regc.FINAL_VERDICTS for i in pair_ids), got
    assert reg["scope"].startswith("кнопка: ")


def test_кнопка_останавливается_на_правиле_без_исполнителя(tmp_path,
                                                            monkeypatch):
    """Ш-1п2 при выводе реестра на нажатии: правило расчётной зоны,
    посаженное на семью, которой нет в базе шаблонов, — пробел вывода
    (family_unknown) и поимённая остановка до начала расчёта. Класс
    «реестр не знает правила» сменился классом «база шаблонов не
    закрывает правило» — единственный настоящий пробел, который остался
    после того, как реестр перестал быть чужим файлом."""
    monkeypatch.setattr(btn, "press_registry", REAL_PRESS_REGISTRY)
    doc = _h0_doc()
    doc["rules"].append({
        "id": "R-чужая-семья", "kind": "HARD", "zone": "PAIRING",
        "title": "правило на несуществующей семье", "statement": "s",
        "sources": [{"sid": "S1", "anchor": "x"}],
        "template_fit": {"category": "A", "family": "семья_которой_нет",
                         "registry_rev": "x" * 12, "quote": "s"}})
    run = _run_dir(tmp_path, doc)
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    # Первым срабатывает гейт пакета (семья вне базы шаблонов — контракт
    # посадки); рубеж реестра — запасной для пробелов, которых гейт
    # пакета не видит. Важно одно: расчёт не начинается, правило названо.
    assert str(d["verdict"]).startswith("остановка"), d
    named = ("R-чужая-семья" in str(d.get("detail") or "")
             or "R-чужая-семья" in {g.get("rule_id")
                                    for g in d.get("conformance_gaps") or []})
    assert named, d
    assert not (run / "button-pairing" / "builder-catalog.json").exists()


def test_реестр_нажатия_свежий_и_совпадает_с_кодом(tmp_path, monkeypatch):
    """Сторож дрейфа при выводе на нажатии: отпечатки файлов-исполнителей
    в реестре равны живым отпечаткам кода — устаревший реестр
    невозможен по построению (прежний класс «реестр устарел» снят)."""
    from pathlib import Path as _P
    from vmp import registry_check as _regc
    monkeypatch.setattr(btn, "press_registry", REAL_PRESS_REGISTRY)
    run = _run_dir(tmp_path, _h0_doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] != "остановка: ВМП не закрыт шаблонами COSMOS", d
    reg = json.loads((run / "registry" / "vmp-executors-registry.json")
                     .read_text(encoding="utf-8"))
    root = _P(btn.__file__).resolve().parent.parent.parent
    assert reg["code_fingerprints"] == _regc.code_fingerprints(root)


def test_кнопка_останавливается_без_каталога_шаблонов(tmp_path, monkeypatch):
    """Шаг 2 каталога шаблонов (часть 13.6): каталог недоступен — не пробел
    ВМП, а среда. Без каталога уже при нажатии первым отвечает гейт пакета
    производителя (Ш-1, «остановка производителя: красный гейт пакета:
    база шаблонов недоступна»); рубеж Ш-1п2 ловит каталог, пропавший
    ПОСЛЕ гейта пакета и вывода реестра (раскат между шагами): реестр
    нажатия несёт блок catalog (как выводит registry_derive), гейт
    соответствия переводит отказ каталога в пробел вида
    catalog_unavailable, кнопка останавливается словами «остановка:
    каталог шаблонов недоступен» и называет путь; расчёт не начинается.
    Подмена — каталогом (пустая папка в CREW_TEMPLATE_CATALOG), не setattr."""
    from pathlib import Path as _P
    import catalog_copies as cc
    from vmp import catalog_access as ca
    from vmp import registry_check as _regc
    root = _P(btn.__file__).resolve().parent.parent.parent
    empty = tmp_path / "empty-catalog"
    empty.mkdir()

    def _press_with_catalog_block(run_dir, model, task):
        # каталог пропадает после гейта пакета — на выводе реестра
        cc.use_catalog(monkeypatch, empty)
        reg = {
            "created": "тестовая фикстура", "scope": "кнопка: тест",
            "code_fingerprints": _regc.code_fingerprints(root),
            "catalog": {"version": "0.1.0-draft.3", "fingerprint": "0" * 64,
                        "path": str(empty)},
            "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                       "title": r.get("title") or ""}
                      for r in (model or {}).get("rules") or []],
        }
        path = _P(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path

    monkeypatch.setattr(btn, "press_registry", _press_with_catalog_block)
    run = _run_dir(tmp_path, _h0_doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    try:
        d = btn.orchestrate(run, FakeSolver(), task="pairing",
                            sleep=lambda *_: None, **fakes)
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: каталог шаблонов недоступен", d
    gaps = d.get("conformance_gaps") or []
    assert [g["kind"] for g in gaps] == ["catalog_unavailable"], gaps
    assert str(empty) in gaps[0]["note"] and ca.CATALOG_ENV in gaps[0]["note"]
    assert "каталог шаблонов недоступен" in str(d.get("detail") or "")
    log = json.loads((run / "button-pairing" / "log.json")
                     .read_text(encoding="utf-8"))
    row = next(e for e in log if e["step"] == "Ш-1п2")
    assert row["status"] == "остановка"
    assert row["detail"] == "каталог шаблонов недоступен"
    assert not (run / "button-pairing" / "builder-catalog.json").exists()


def test_button_pairing_warm_start_passes_catalog(tmp_path):
    """Тёплый старт: каталог прежнего нажатия уходит построителю
    флагом --warm-catalog, ёмкость баз при нём законна; след несёт
    происхождение обоих ключей."""
    run, fakes = _pairing_fakes_min(tmp_path)
    prev = run / "button-pairing-prev"
    prev.mkdir()
    (prev / "builder-catalog.json").write_text(
        json.dumps(_CATALOG_OK), encoding="utf-8")
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        base_capacity=True,
                        warm_from="button-pairing-prev",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    s = " ".join(seen["args"])
    assert "--warm-catalog" in s and "builder-catalog.json" in s
    assert "--base-day-cap" in s
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert binp["provenance"]["--warm-catalog"]["from"] \
        == "каталог прежнего нажатия button-pairing-prev"
    assert "--base-day-cap" in binp["provenance"]


def test_button_pairing_warm_start_missing_catalog_stops(tmp_path):
    """Тёплый старт от несуществующего каталога — честная остановка
    с именем файла, построитель не запускается."""
    run, fakes = _pairing_fakes_min(tmp_path)
    called = []
    fakes["pairing_builder"] = (
        lambda payload, args, out: called.append(1))
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        warm_from="нет-такого",
                        sleep=lambda *_: None, **fakes)
    assert "не найден" in d["verdict"], d
    assert not called


def test_button_pairing_writes_source_rules_contract(tmp_path):
    """Г-П5 + внешний аудит 04.09.2026 (A1, инверсия прежнего стенда):
    контракт построителя собирается из форм пакета и подаётся в запуск
    ВСЕГДА — путь и отпечаток атомарно, без флага-включателя. Прежний
    стенд закреплял дефект: контракт лежал в следе «в запуск не подан»,
    и построитель считал по настройке площадки."""
    import hashlib
    import tests.test_cdpm_threshold_forms as tff
    doc = _doc()
    doc["rules"] += tff._doc_with_matrix()["rules"]
    run = _run_dir(tmp_path, doc)
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    srp = run / "button-pairing" / "source-rules.json"
    assert srp.is_file()
    contract = json.loads(srp.read_text(encoding="utf-8"))
    assert len(contract["duty_span_matrix"]) == 5
    args = seen["args"]
    assert "--source-rules" in args
    assert args[args.index("--source-rules") + 1] == str(srp)
    assert args[args.index("--source-rules-fp") + 1] \
        == hashlib.sha256(srp.read_bytes()).hexdigest()
    # Профиль пакета у построителя: умолчания площадки запрещены.
    assert "--vmp-package" in args
    # Без строгого режима в кнопочном профиле (вердикт владельца
    # словаря 2026-08-27: источник один, след полный).
    assert "--source-rules-strict" not in args
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert binp["provenance"]["--source-rules"]["fp"] \
        == hashlib.sha256(srp.read_bytes()).hexdigest()
    assert "note" not in binp["provenance"]["--source-rules"]
    assert binp["provenance"]["--vmp-package"]["value"] is True
    # Стендовый переключатель source_rules=False — только для стендов;
    # выключение называется в следе вслух.
    d2 = btn.orchestrate(run, FakeSolver(), task="pairing",
                         source_rules=False,
                         sleep=lambda *_: None, **fakes)
    assert d2["verdict"] == "пейринг построен", d2
    assert "--source-rules" not in seen["args"]


def test_A1_кнопка_транслирует_посадки_и_сутки_из_applied(tmp_path):
    """Внешний аудит 04.09.2026, A1: предел посадок за смену и предел
    цепочки в сутках из applied ВМП едут построителю ключами
    --max-segs / --pairing-span (прежде — не транслировались, и
    построитель добирал 5 посадок и 4 суток из настройки площадки).
    ВМП «две посадки», площадка «пять» → построитель получает 2."""
    run, fakes = _pairing_fakes_min(tmp_path)
    applied = dict(_APPLIED, max_landings_per_duty=2, max_pairing_days=3)
    fakes["coverage_fn"] = lambda solver, zb, task: {
        "uncovered": [], "objectives": [], "applied": applied}
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    args = seen["args"]
    assert args[args.index("--max-segs") + 1] == "2"
    assert args[args.index("--pairing-span") + 1] == str(3 * 1440)
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert binp["provenance"]["--max-segs"]["from"] \
        == "max_landings_per_duty"
    assert binp["provenance"]["--pairing-span"]["from"] \
        == "max_pairing_days"
    # Ведомость исполнения правил (общий контракт, раздел 5 плана):
    # артефакт нажатия рядом с builder-input.json.
    man = json.loads((run / "button-pairing" / "execution-manifest.json")
                     .read_text(encoding="utf-8"))
    assert man["task"] == "pairing" and man["blocking"] == []
    # Свод стенда без мягких (_doc): в зоне пейринга одно жёсткое.
    assert {r["rule_id"] for r in man["rules"]} == {"R-DUTY"}
    row = man["rules"][0]
    assert row["status"] == "исполнитель по реестру"
    assert row["applicability"]["applies"] is True


def test_A1_без_нормы_посадок_граница_перебора_факт_данных(tmp_path):
    """A1: ВМП без предела посадок — не «5 с площадки» и не «3 класса
    CosRules», а явная граница перебора из факта данных (число рабочих
    участков набора) с происхождением в следе."""
    run, fakes = _pairing_fakes_min(tmp_path)
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    args = seen["args"]
    assert args[args.index("--max-segs") + 1] == "1"     # один рейс в мире
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert "ВМП не устанавливает" in binp["provenance"]["--max-segs"]["rule"]


def test_A1_удалённый_обязательный_параметр_называет_правило(
        tmp_path, monkeypatch):
    """A1, приёмка: из applied исчез порог жёсткого правила ВМП (предел
    смены VN-010 набора H0) — остановка называет правило и параметр,
    построитель не запускается."""
    monkeypatch.setattr(btn, "press_registry", REAL_PRESS_REGISTRY)
    run = _run_dir(tmp_path, _h0_doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["optimization"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    applied = {k: v for k, v in _APPLIED.items() if k != "max_duty_span_min"}
    fakes["coverage_fn"] = lambda solver, zb, task: {
        "uncovered": [], "objectives": [], "applied": applied}
    called = []
    fakes["pairing_builder"] = lambda payload, args, out: called.append(1)
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert str(d["verdict"]).startswith("остановка"), d
    text = json.dumps(d, ensure_ascii=False)
    assert "VN-010" in text and "max_duty_duration" in text, d
    assert not called


def test_button_capacity_params_only_for_pairing(tmp_path):
    run = _run_dir(tmp_path, _doc())
    import pytest
    with pytest.raises(ValueError):
        btn.orchestrate(run, FakeSolver(), task="rostering",
                        base_capacity=True, **_fakes(True))


def test_button_pairing_without_builder_stops(tmp_path):
    """Директива Г-П4: без сконфигурированного построителя пейринг
    честно останавливается — Timefold-путь исключён."""
    run = _run_dir(tmp_path, _doc())
    fakes = _fakes(True)
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert "только" in d["verdict"] and "ortools" in d["verdict"], d


def test_button_pairing_relaxed_cover_not_registered(tmp_path):
    """RELAXED_COVER (и любой вердикт вне белого списка) — каталог
    НЕ регистрируется, отказ словами."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    bad = {"selected": [{"fns": ["G-1"]}],
           "coverage": {"verdict": "RELAXED_COVER"}}
    fakes = _builder_fakes(_fakes(True), catalog=bad)
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    d = btn.orchestrate(run, solver, task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert "вне белого списка" in d["verdict"], d
    assert not hasattr(solver, "catalog_call")


def _pairing_fakes():
    fakes = _fakes(True)
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    return fakes


def test_button_rostering_on_approved_pairing(tmp_path):
    """Второе нажатие двухфазного потока: ростеринг НА утверждённом
    пейринге. pairingRunId уходит решателю параметром, подтверждение
    фиксации (pairingRunId + число цепочек) — из СТАТУСА прогона в
    выдачу, артефакты — в button-on-pairing/ (сквозной ростеринг из
    button/ не затёрт), а пакет — БАЙТЫ пакета пейринга (равенство
    отпечатков у решателя; живой отказ пятого нажатия 2026-08-24:
    свежий экспорт несёт другой created)."""
    run = _run_dir(tmp_path, _doc())
    p = btn.orchestrate(run, FakeSolver(),
                        task="pairing", sleep=lambda *_: None,
                        **_builder_fakes(_pairing_fakes()))
    assert p["verdict"] == "пейринг построен", p
    solver = FakeSolver(status_chains=12)
    d = btn.orchestrate(run, solver, task="rostering",
                        pairing_run_id="r-пейринг",
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "допустимо", d
    assert solver.started["pairingRunId"] == "r-пейринг"
    assert d["pairing_run_id"] == "r-пейринг"
    assert d["pairing_chains"] == 12
    assert d["artifacts"]["certificate"] \
        == "button-on-pairing/certificate.json"
    assert (run / "button-on-pairing" / "delivery.json").is_file()
    assert not (run / "button").exists()
    assert (run / "button-on-pairing" / "package.zip").read_bytes() \
        == (run / "button-pairing" / "package.zip").read_bytes()


def test_button_on_pairing_needs_pairing_first(tmp_path):
    """Без прогона пейринга второе нажатие не имеет смысла — отказ
    словами до экспорта в решатель."""
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(), task="rostering",
                        pairing_run_id="r-1",
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "остановка: прогона пейринга нет"
    assert "pairing" in d["detail"]


def test_button_on_pairing_refuses_changed_model(tmp_path):
    """Постановка изменилась после утверждённого пейринга — пейринг
    устарел, второе нажатие останавливается словами (переиспользовать
    старый пакет молча нельзя, свежий — разойдётся отпечатком)."""
    doc = _doc()
    run = _run_dir(tmp_path, doc)
    p = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None,
                        **_builder_fakes(_pairing_fakes()))
    assert p["verdict"] == "пейринг построен", p
    doc["rules"][0]["statement"] += " (ужесточено после пейринга)"
    m.dump_model(doc, run / "extract" / "model.json")
    d = btn.orchestrate(run, FakeSolver(), task="rostering",
                        pairing_run_id="r-1",
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == ("остановка: постановка изменилась после "
                           "утверждённого пейринга")
    assert "model.json" in d["detail"]


def test_button_pairing_run_id_only_for_rostering(tmp_path):
    """pairingRunId с задачей пейринга бессмыслен — отказ словами до
    любых шагов."""
    import pytest
    run = _run_dir(tmp_path, _doc())
    with pytest.raises(ValueError, match="rostering"):
        btn.orchestrate(run, FakeSolver(), task="pairing",
                        pairing_run_id="r-1",
                        sleep=lambda *_: None, **_fakes(True))


def _compare_fakes():
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"],
                          "rostering": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    return fakes


def test_compare_three_legs_one_package(tmp_path):
    """Г-П2, проводка: три ноги (пейринг → ростеринг на нём →
    сквозной) на ОДНОМ пакете; входы формулы — из машинных счетов
    ног; источник стоимости пейринга сквозного печатается в выдачу.
    Сама формула покрыта tests/test_way_compare.py решателя."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    d = btn.orchestrate_compare(run, solver, sleep=lambda *_: None,
                                **_compare_fakes())
    assert d["verdict"].startswith("сравнение: победитель — "), d
    # Проводка ног Г-П4: пейринг — построителем (каталог, не start);
    # классика фиксируется на runId каталога, сквозной — без фиксации.
    assert [s["task"] for s in solver.starts] \
        == ["rostering", "rostering"]
    assert solver.catalog_call["sha"]
    assert solver.starts[0]["pairingRunId"] == "r-каталог"
    assert solver.starts[1]["pairingRunId"] is None
    # Г-П4: судья — только счёт финального ростеринга; повкладовый
    # у прогона-КАТАЛОГА не спрашивается (у каталога нет решения),
    # стоимость пейринга — справкой из сертификата построителя.
    # Разбор счёта РОСТЕРИНГОВЫХ ног спрашивается: штрафная сверка
    # Д4 сверяет штраф по ВМП с разбором под пакетом каждого прогона
    # (первые сертификаты H0 03.09 шли без неё — penalty_reconciliation
    # оставался null).
    assert "r-каталог" not in solver.breakdown_calls
    assert solver.breakdown_calls == ["r-77", "r-77"]
    assert d["inputs"]["classical_rostering_score"] == "0hard/0soft"
    assert d["inputs"]["through_rostering_score"] == "0hard/0soft"
    assert d["pairing_judged_by"].startswith(
        "счёт финального ростеринга")
    assert d["margin_pct"] == 5.0
    # Один пакет у всех ног — байты пейринга.
    pz = (run / "button-pairing" / "package.zip").read_bytes()
    assert (run / "button-on-pairing" / "package.zip").read_bytes() \
        == pz
    assert (run / "button-through" / "package.zip").read_bytes() == pz
    for art in ("button-compare/delivery.json",
                "button-compare/compare.md"):
        assert (run / art).is_file(), art
    assert set(d["legs"]) == {"пейринг", "классический ростеринг",
                              "сквозной ростеринг"}


def test_coverage_stop_waits_for_human(tmp_path):
    """П-1, моя половина: предпрогонный отчёт назвал правила без
    исполнителя — кнопка останавливается «ожидает решения человека»
    с перечнем; неавторизованные = критичные, счёт не стартует."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    unc = [{"rule_id": "R-A", "title": "Правило А",
            "reason": "формы нет"},
           {"rule_id": "R-B", "title": "Правило Б",
            "reason": "формы нет"}]
    d = btn.orchestrate(run, solver,
                        coverage_fn=lambda s, z, t: {"uncovered": unc},
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "ожидает решения человека: покрытие"
    assert d["uncovered"] == unc
    assert d["unauthorized"] == ["R-A", "R-B"]
    assert "пробелы каталога шаблонов" in d["note"]
    assert solver.started is None          # счёт не запускался


def test_coverage_partial_authorization_still_stops(tmp_path):
    """Авторизация поимённая и полная: часть правил без авторизации —
    остановка с перечнем ИМЕННО неавторизованных (критичных)."""
    run = _run_dir(tmp_path, _doc())
    unc = [{"rule_id": "R-A"}, {"rule_id": "R-B"}]
    d = btn.orchestrate(run, FakeSolver(),
                        coverage_fn=lambda s, z, t: {"uncovered": unc},
                        accepted_uncovered=["R-A"],
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "ожидает решения человека: покрытие"
    assert d["unauthorized"] == ["R-B"]


def test_coverage_authorized_runs_and_traced(tmp_path):
    """Полная поимённая авторизация: счёт идёт, выбор человека —
    машинным полем трассировки (в сертификатный след)."""
    run = _run_dir(tmp_path, _doc())
    unc = [{"rule_id": "R-A"}, {"rule_id": "R-B"}]
    d = btn.orchestrate(run, FakeSolver(),
                        coverage_fn=lambda s, z, t: {"uncovered": unc},
                        accepted_uncovered=["R-B", "R-A"],
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "допустимо"
    assert d["trace"]["coverage_authorization"] \
        == {"accepted": ["R-A", "R-B"]}


def test_coverage_gaps_artifact_written(tmp_path):
    """Этап 2 переноса ВМП: непокрытые правила Ш-1п обогащаются
    машинной формой из модели пакета, артефакт пробелов шаблонов
    (извещение + СТРОКИ ЗАЯВОК каталога, шаг 6) пишется и в ожидании
    решения, и при полной авторизации; правило вне модели — громкая
    пометка брака согласованности.

    Прежний ключ артефакта `cr_candidates` (строка очереди изменений с
    машинной формой правила авиакомпании) снят: заявка каталога несёт
    раздел, запись и виды, текста правила в ней нет."""
    run = _run_dir(tmp_path, _doc())
    unc = [{"rule_id": "R-REST", "reason": "формы нет"},
           {"rule_id": "R-NET", "reason": "вне жёсткого исполнения"}]
    d = btn.orchestrate(run, FakeSolver(),
                        coverage_fn=lambda s, z, t: {"uncovered": unc},
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "ожидает решения человека: покрытие"
    art = json.loads((run / "button" / "vmp-rule-gaps.json")
                     .read_text(encoding="utf-8"))
    assert "R-REST" in art["notify_text"]
    assert d["vmp_gaps_notify"] == art["notify_text"]
    by_id = {g["rule_id"]: g for g in art["gaps"]}
    assert by_id["R-REST"]["rule"] is not None, \
        "машинная форма правила из модели пакета"
    assert by_id["R-NET"]["rule"] is None
    assert "брак согласованности" in by_id["R-NET"]["reason"]
    assert "cr_candidates" not in art
    rows = {r["rule_id"]: r for r in art["catalog"]}
    assert set(rows) == {"R-REST", "R-NET"}
    assert d["catalog_requests"] == art["catalog"], \
        "выдача остановки несёт тот же перечень, что артефакт"
    # R-REST в модели есть, записи каталога под него нет — новая запись:
    # номер по закону и ссылка ручной подачи с ключом.
    from template_catalog import requests as _law
    rule = by_id["R-REST"]["rule"]
    key = _law.request_key("families", rule["kind"], rule["zone"],
                           rule["statement"])
    assert rows["R-REST"]["form"] == "new"
    assert rows["R-REST"]["request_id"] == _law.request_id_of(key)
    assert "key=" + key in rows["R-REST"]["link"]
    assert rule["statement"] not in json.dumps(art["catalog"],
                                               ensure_ascii=False), \
        "в строке заявки нет текста правила авиакомпании"
    # Правила нет в модели — номер не выдумывается.
    assert rows["R-NET"]["form"] == "none" and rows["R-NET"]["request_id"] is None
    # Полная авторизация: счёт идёт, артефакт с перечнем accepted.
    d2 = btn.orchestrate(run, FakeSolver(),
                         coverage_fn=lambda s, z, t: {"uncovered": unc},
                         accepted_uncovered=["R-REST", "R-NET"],
                         sleep=lambda *_: None, **_fakes(True))
    assert d2["verdict"] == "допустимо"
    art2 = json.loads((run / "button" / "vmp-rule-gaps.json")
                      .read_text(encoding="utf-8"))
    assert art2["accepted"] == ["R-NET", "R-REST"]
    assert [r["rule_id"] for r in art2["catalog"]] == ["R-REST", "R-NET"]
    assert (run / "button" / "catalog-requests.json").is_file(), \
        "квитанция нажатия пишется и при авторизованном счёте"


def _catalog_without_executors(tmp_path, monkeypatch, family):
    """Копия каталога, где у семьи ни одного исполнителя. Возврат — виды,
    объявленные записью и не исполняемые никем: ровно их и просит заявка.

    Подготовку делает ОБЩИЙ стенд `catalog_copies.without_executors` —
    один на обе стороны шва Ш-8: по этому же каталогу производитель
    отказывает, а потребитель подаёт заявку. Здесь только удобная
    подпись на одну семью."""
    import catalog_copies as cc
    return cc.without_executors(tmp_path, monkeypatch, family)[family]


def _fit(doc, rule_id, family, param="max_duty_duration"):
    """Посадка категории A правила на семью каталога: параметр словаря
    семьи с дословной цитатой — как требует контракт посадки."""
    for r in doc["rules"]:
        if r["id"] == rule_id:
            r["template_fit"] = {
                "category": "A", "family": family,
                "registry_rev": "x" * 12, "quote": r["statement"],
                "params": {param: {"value": 12, "quote": r["statement"]}}}
    return doc


def test_пробел_относительно_записи_подаётся_заявкой_на_вид(tmp_path,
                                                             monkeypatch):
    """Шаг 6 + K06: правило посажено на семью каталога, исполнителей у
    неё нет — кнопка подаёт заявку на вид исполнителя сама (данных
    авиакомпании в ней нет) и называет номер в выдаче остановки.

    Отказ даёт НАСТОЯЩИЙ рубеж выпуска пакета: `export_zip` здесь не
    подменён, барьер поддержки (K06) видит копию каталога без
    исполнителей и отказывает кодом `support-missing` с адресами
    записей. Прежде тот же договор проверялся на шаге покрытия —
    рубеж отказывает раньше, и до шага покрытия в этом сценарии дело
    не доходит."""
    from vmp import catalog_access as ca
    from vmp import catalog_requests as vcr
    from template_catalog import requests as _law
    kinds = _catalog_without_executors(tmp_path, monkeypatch, "duty_limits")
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    row = d["catalog_requests"][0]
    assert row["form"] == "kind"
    assert (row["section"], row["record"]) == ("families", "duty_limits")
    assert row["kinds"] == kinds
    rid = _law.request_id_of(_law.kind_request_key("families", "duty_limits",
                                                   kinds))
    assert row["request_id"] == rid and row["state"] == "подана"
    assert "подана" in row["words"]
    assert rid in d["detail"], "остановка называет номер заявки словами"
    doc = json.loads((tmp_path / "catalog-queue" / "outbox" / (rid + ".json"))
                     .read_text(encoding="utf-8"))
    assert doc["submitted_by"] == "COSMOS" and doc["draft"] is None
    assert doc["executor_kinds_requested"] == kinds
    assert doc["source"]["ref"].startswith(run.name + "#")
    receipt = json.loads((run / "button" / vcr.RECEIPT_NAME)
                         .read_text(encoding="utf-8"))
    assert receipt["rows"]["R-DUTY"]["request_id"] == rid


def test_автоподача_выключена_очередь_пуста_и_сказано_словами(tmp_path,
                                                               monkeypatch):
    """`CREW_CATALOG_REQUESTS_AUTO=0` — владелец оставил подачу за
    собой: файлов очереди нет, квитанция называет кнопку. Настоящий
    рубеж выпуска ведёт себя тут ровно как шаг покрытия: сам заявку не
    подаёт и о выдуманной подаче не сообщает."""
    from vmp import catalog_access as ca
    from vmp import catalog_requests as vcr
    monkeypatch.setenv(vcr.ENV_AUTO, "0")
    _catalog_without_executors(tmp_path, monkeypatch, "duty_limits")
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    assert not (tmp_path / "catalog-queue").exists()
    assert "кнопкой «Заявить в каталог»" in d["catalog_requests"][0]["words"]
    assert "Заявить в каталог" in d["detail"]


def _refuse_export(mp, code="support-missing",
                   items=("families/duty_limits",), words=None):
    """Подмена рубежа выпуска ТОЛЬКО для развилок, которых настоящий
    барьер поддержки дать не может: чужой код отказа и пустой перечень
    записей. Три сценария заявки настоящий барьер закрывает сам и
    подмены не знают."""
    from cdpm import export_task_package as tp

    def _refuse(run_dir):
        err = tp.TaskPackageError(
            words or ("выпуск пакета остановлен: семья duty_limits "
                      "(families/duty_limits) без подтверждённой поддержки"),
            code, list(items) if items is not None else None)
        raise err

    mp.setattr(tp, "export_zip", _refuse)


def test_чужой_код_отказа_останавливает_производителем_без_заявки(
        tmp_path, monkeypatch):
    """Код не `support-missing` (неполнота семьи, противоречие свода,
    расхождение носителей параметра) лечат не решением владельца о
    записи каталога: остановка производителя его же словами, очередь не
    трогается.

    Отказ здесь НАСТОЯЩИЙ и подмены рубежа нет: семья поддержана, а
    посадка правила неполна — рубеж сам отказывает кодом
    `family-incomplete` с перечнем `duty_limits/max_duty_duration`.
    Прежде эта проба шла на подмене, и сдача называла случай
    недостижимым; неавторское ревью 13.09.2026 (находка С-2) показало,
    что `TaskPackageError` поднимается примерно из двадцати пяти мест,
    в том числе вовсе без кода, и все идут через `package_refusal_stop`.

    Перечень записей у отказа НЕПУСТОЙ: иначе остановку дала бы вторая
    развилка (перечня нет), и проба судила бы не тот закон — снятие
    проверки кода осталось бы незамеченным."""
    import catalog_copies as cc
    from vmp import catalog_access as ca
    folder = cc.copy_catalog(tmp_path)          # исполнители на месте
    cc.use_catalog(monkeypatch, folder)
    doc = _fit(_doc(), "R-DUTY", "duty_limits")
    for r in doc["rules"]:                      # посадка без параметров
        if r["id"] == "R-DUTY":
            r["template_fit"]["params"] = {}
    try:
        run = _run_dir(tmp_path, doc)
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка производителя", d
    assert "полнота семьи" in d["detail"], d["detail"]
    assert "max_duty_duration" in d["detail"], d["detail"]
    assert "catalog_requests" not in d
    assert not (tmp_path / "catalog-queue").exists()


def test_support_missing_без_перечня_семей_говорит_словами(tmp_path,
                                                            monkeypatch):
    """`items` пуст: заявку строить не из чего — fail-closed остановка
    со словами «семьи не назвал», а не молчаливый пропуск и не
    выдуманная запись."""
    from vmp import catalog_access as ca
    _catalog_without_executors(tmp_path, monkeypatch, "duty_limits")
    _refuse_export(monkeypatch, items=None)
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка производителя", d
    assert "семьи не назвал" in d["detail"]
    assert not (tmp_path / "catalog-queue").exists()


def test_адрес_не_по_образцу_не_становится_заявкой_а_назван_словами(
        tmp_path, monkeypatch):
    """Предмет отказа назван не адресом записи («<раздел>/<id>»):
    заявку строить не из чего, но и пропасть пробел не имеет права —
    строка «не каталог» со словами, очередь пуста. Второй половине шва
    (рубежу производителя) это цена ошибки в перечне, а не тихий
    зелёный."""
    from vmp import catalog_access as ca
    _catalog_without_executors(tmp_path, monkeypatch, "duty_limits")
    _refuse_export(monkeypatch, items=["duty_limits"])   # без раздела
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    row = d["catalog_requests"][0]
    assert row["form"] == "none" and row["request_id"] is None
    assert "не по образцу" in row["words"] and "duty_limits" in row["words"]
    assert "не по образцу" in d["detail"]
    assert not (tmp_path / "catalog-queue" / "outbox").exists()


def test_семья_без_правил_свода_названа_строкой_без_правила(tmp_path,
                                                             monkeypatch):
    """Запись названа отказом, а правил свода на ней нет: пробел не
    пропадает молча — строка заявки считается ПО САМОЙ ЗАПИСИ (ключ
    строки — её адрес), и остановка называет запись словами.

    Развилка охраняет ШОВ ДВУХ ПОЛОВИН: рубеж производителя называет
    записи своим счётом семей (`model.readiness_barriers`), а COSMOS
    ищет правила на них своим (`catalog_requests.family_of`). Пока
    половины согласны, перечень пуст; разойдутся — запись обязана
    поехать заявкой, а не исчезнуть. Настоящий барьер такого
    расхождения по построению не даёт, поэтому здесь подмена."""
    from vmp import catalog_access as ca
    from vmp import catalog_requests as vcr
    from template_catalog import requests as _law
    kinds = _catalog_without_executors(tmp_path, monkeypatch, "night_series")
    _refuse_export(monkeypatch, items=["families/night_series"])
    try:
        run = _run_dir(tmp_path, _doc())          # ни одного правила семьи
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    assert d["conformance_gaps"] == [], "правил свода на записи нет"
    row = d["catalog_requests"][0]
    assert row["rule_id"] == "families/night_series"
    assert (row["form"], row["section"], row["record"]) == \
        ("kind", "families", "night_series")
    rid = _law.request_id_of(_law.kind_request_key("families", "night_series",
                                                   kinds))
    assert row["request_id"] == rid and row["state"] == "подана"
    assert "правил свода на записях нет: families/night_series" in d["detail"]
    assert (tmp_path / "catalog-queue" / "outbox" / (rid + ".json")).is_file()
    receipt = json.loads((run / "button" / vcr.RECEIPT_NAME)
                         .read_text(encoding="utf-8"))
    assert receipt["rows"]["families/night_series"]["request_id"] == rid


def test_пробел_без_записи_уже_заявлен_из_прогона_cdpm(tmp_path, monkeypatch):
    """Заявка CDPM (посадка категории C) уже в каталоге: кнопка называет
    её номер и состояние, дубля не заводит и в очередь ничего не
    пишет."""
    import catalog_copies as cc
    from vmp import catalog_access as ca
    from template_catalog import requests as _law
    from template_catalog.export import dump_json
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-REST")
    key = _law.request_key("families", rule["kind"], rule["zone"],
                           rule["statement"])
    rid = _law.request_id_of(key)
    folder = cc.copy_catalog(tmp_path)
    draft = {k: v for k, v in json.loads(
        (folder / "families.json").read_text(encoding="utf-8")
    )["families"][0].items() if k not in ("stand_tasks", "status", "history")}
    draft["id"] = "family_" + key[:12]
    draft["params"], draft["complements"] = [], []
    draft["check_templates"], draft["param_sets"] = [], []
    draft["conditions"], draft["subject_words"] = [], None
    draft["rule_form"], draft["violation_unit"] = None, None
    draft["kind"], draft["executor_kinds"] = "HARD", ["kotlin_knob"]
    # Каталог 0.5.0: размер нарушения задан ⇔ SOFT (X16) — у жёсткого
    # черновика по образцу мягкой семьи его быть не должно.
    draft["violation_size"] = None
    (folder / "requests.json").write_bytes(dump_json({
        "form_version": 1,
        "requests": [{
            "request_id": rid, "state": "принята", "section": "families",
            "record_id": draft["id"],
            "submitted_at": "2026-09-12T10:15:00+03:00",
            "submitted_by": "CDPM",
            "source": {"kind": "посадка категории C", "document_class": None,
                       "run_class": "прогон CDPM", "ref": "run-0088#" + key[:12]},
            "draft": draft, "executor_kinds_requested": None,
            "decision": {"by": "владелец", "at": "2026-09-12T10:00:00+03:00",
                         "words": "механика общая, исполнителя написать"},
            "cr_key": None}]}).encode("utf-8"))
    cc.use_catalog(monkeypatch, folder)
    try:
        run = _run_dir(tmp_path, doc)
        d = btn.orchestrate(
            run, FakeSolver(),
            coverage_fn=lambda s, z, t: {"uncovered": [{"rule_id": "R-REST"}]},
            sleep=lambda *_: None, **_fakes(True))
    finally:
        ca.forget()
    row = next(r for r in d["catalog_requests"] if r["rule_id"] == "R-REST")
    assert row["form"] == "new" and row["request_id"] == rid
    assert row["state"] == "принята"
    assert "пробел уже заявлен из прогона CDPM" in row["words"]
    assert not (tmp_path / "catalog-queue").exists()


def _catalog_without_executors_2(tmp_path, monkeypatch, *families):
    """То же на НЕСКОЛЬКО семей разом (вторая копия затёрла бы первую).
    Подпись оставлена ради читаемости вызовов; закон — общий стенд."""
    import catalog_copies as cc
    return cc.without_executors(tmp_path, monkeypatch, *families)


def test_слова_пробела_называют_свою_запись_а_не_соседнюю(tmp_path,
                                                           monkeypatch):
    """Находка С-3 неавторского ревью 13.09.2026: слова отказа рубежа
    склеивают до ЧЕТЫРЁХ семей в одну строку, и прежде эта строка,
    обрезанная на 300 знаках, ехала машинным полем `note` КАЖДОМУ
    пробелу — у второй и следующих записей в поле стоял обрывок про
    ПЕРВУЮ, да ещё оборванный посреди перечня.

    Теперь у каждого пробела свои короткие слова со своим адресом, а
    полные слова отказа целиком лежат в словах остановки.

    Рубеж здесь подменён, и вот почему: перечень `items` о НЕСКОЛЬКИХ
    семьях рубеж строит сам (`[pr["item"] for pr in sup]`), но чтобы до
    этого дошло на живом пути, обе семьи должны быть посажены полными
    посадками — иначе раньше отказывает старший барьер полноты семьи
    (проверено: посадка второй семьи без всех параметров даёт
    `family-incomplete`). Проба судит ПРОВОДКУ слов пробела, а не
    рубеж; заявки при этом настоящие — каталог правда без исполнителей
    у обеих семей."""
    from vmp import catalog_access as ca
    виды = _catalog_without_executors_2(tmp_path, monkeypatch,
                                        "duty_limits", "night_series")
    doc = _fit(_doc(), "R-DUTY", "duty_limits")
    doc = _fit(doc, "R-REST", "night_series", param="max_night_series")
    _refuse_export(monkeypatch, items=["families/duty_limits",
                                       "families/night_series"],
                   words=("выпуск пакета остановлен: семья duty_limits "
                          "(families/duty_limits) без подтверждённой "
                          "поддержки; семья night_series "
                          "(families/night_series) без подтверждённой "
                          "поддержки. Исполнимость даёт только "
                          "подтверждённая поддержка записи каталога по "
                          "привязкам обоих продуктов (клетка «есть» хотя "
                          "бы у одного при каталоге того же отпечатка): "
                          "дождитесь выпуска исполнителя (заявка в "
                          "каталог) либо обновите привязки, и повторите "
                          "выпуск"))
    try:
        run = _run_dir(tmp_path, doc)
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                            **_fakes(True))
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    пробелы = {g["rule_id"]: g for g in d["conformance_gaps"]}
    assert set(пробелы) >= {"R-DUTY", "R-REST"}, пробелы
    свои = {"R-DUTY": "duty_limits", "R-REST": "night_series"}
    for rid, семья in свои.items():
        note = str(пробелы[rid].get("note") or "")
        assert "families/%s" % семья in note, (rid, note)
        for чужая in set(свои.values()) - {семья}:
            assert чужая not in note, (
                "пробел %s называет ЧУЖУЮ запись %s: %s" % (rid, чужая, note))
        assert not note.endswith("…") and len(note) < 300, (rid, note)
    # Полные слова отказа не потеряны: они в словах остановки целиком.
    for семья in свои.values():
        assert семья in d["detail"], (семья, d["detail"][:400])
    assert len(d["detail"]) > 300, "полные слова отказа обрезаны"
    print("С-3: %s" % {r: пробелы[r]["note"][:60] for r in свои})
    assert виды["duty_limits"] and виды["night_series"]


def test_поле_заявки_у_пробела_считает_одна_функция_обе_ветви():
    """Закон «у пробела остановки есть поле `request`» живёт в ОДНОЙ
    функции (`btn.gaps_with_requests`), и проба судит ОБЕ её ветви:
    номер со состоянием там, где заявка есть, и честный `null` там, где
    её быть не может.

    Заведена по находке С-1 неавторского ревью 13.09.2026: закон был
    написан дословно дважды — на раннем отказе и на глубокой сверке, —
    и мутация поздней копии не красила НИ ОДНОЙ пробы, потому что проба
    того пути судила только ветку `null`. Теперь копия одна, а обе её
    ветви судятся здесь напрямую; оба места вызова охраняются своими
    сквозными пробами."""
    rows = [{"rule_id": "R-DUTY", "request_id": "req-" + "1" * 20,
             "state": "подана"},
            {"rule_id": "R-REST", "request_id": None, "state": None},
            {"rule_id": "families/night_series",
             "request_id": "req-" + "2" * 20, "state": "принята"}]
    gaps = [{"kind": "no_mechanic", "rule_id": "R-DUTY"},
            {"kind": "no_mechanic", "rule_id": "R-REST"},
            {"kind": "no_mechanic", "rule_id": "families/night_series"},
            {"kind": "no_mechanic", "rule_id": "R-NET"}]
    out = btn.gaps_with_requests(gaps, rows)
    по_правилу = {g["rule_id"]: g["request"] for g in out}
    assert по_правилу["R-DUTY"] == {"request_id": "req-" + "1" * 20,
                                    "state": "подана"}
    assert по_правилу["families/night_series"] == {
        "request_id": "req-" + "2" * 20, "state": "принята"}
    assert по_правилу["R-REST"] is None, "заявки нет — поле честно пустое"
    assert по_правилу["R-NET"] is None, "строки нет вовсе — поле пустое"
    assert [g["kind"] for g in out] == ["no_mechanic"] * 4, \
        "обогащается КОПИЯ пробела: прочие поля не теряются"
    assert all("request" not in g for g in gaps), \
        "входные пробелы не правятся на месте"


def _registry_press_fixture(monkeypatch, gap_rule="R-DUTY"):
    """Подмена снятия реестра: ОДНО правило незакрыто — пробел глубокой
    сверки Ш-1п2. Общая для двух тестов: у поддержанной записи и у
    записи без поддержки договор выдачи один и тот же."""
    from pathlib import Path as _P
    from vmp import registry_check as _regc
    root = _P(btn.__file__).resolve().parent.parent.parent

    def _press(run_dir, model, task, **kw):
        reg = {"created": "тестовая фикстура", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]),
                          "title": r.get("title") or "",
                          "verdict": ("no_mechanic" if r["id"] == gap_rule
                                      else "covered_full"),
                          "gap_note": "исполнителя нет"}
                         for r in (model or {}).get("rules") or []]}
        path = _P(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path

    monkeypatch.setattr(btn, "press_registry", _press)
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    return fakes


def test_пробел_глубокой_сверки_несёт_поле_заявки_при_поддержанной_записи(
        tmp_path, monkeypatch):
    """Ш-1п2 сам по себе: запись каталога ПОДДЕРЖАНА (рубеж выпуска
    молчит, до глубокой сверки дело доходит), а реестр держит правило
    незакрытым — у пробела остановки всё равно есть поле `request`.

    Заявки тут нет и быть не может (все объявленные виды на месте —
    это дефект кода, а не пробел каталога), и поле честно несёт null.
    Тест заведён 13.09.2026 вместе с правкой Т-04: прежде договор
    обогащения пробелов Ш-1п2 проверялся на записи БЕЗ исполнителей, а
    там теперь раньше отказывает рубеж K06 — без этого теста путь
    глубокой сверки остался бы без пробы."""
    import catalog_copies as cc
    from vmp import catalog_access as ca
    folder = cc.copy_catalog(tmp_path)
    cc.use_catalog(monkeypatch, folder)
    fakes = _registry_press_fixture(monkeypatch)
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), task="pairing",
                            sleep=lambda *_: None, **fakes)
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    gap = next(g for g in d["conformance_gaps"] if g["rule_id"] == "R-DUTY")
    assert "request" in gap and gap["request"] is None, gap
    assert not (tmp_path / "catalog-queue" / "outbox").exists(), \
        "поддержанная запись заявки на вид не рождает"


def test_остановка_соответствия_называет_заявку_каждого_пробела(tmp_path,
                                                                 monkeypatch):
    """У каждого пробела остановки — поле `request` с номером и
    состоянием заявки каталога (либо null, если заявка невозможна);
    строки самой сверки не трогаются, обогащается копия остановки.

    Договор проверяется на записи БЕЗ поддержки. Прежде до него
    доходила глубокая сверка Ш-1п2 (фикстура реестра ниже сохранена
    дословно); с барьером K06 на голове рубеж выпуска отказывает
    РАНЬШЕ, и тот же договор обязан соблюдаться на ранней остановке —
    тело теста не ослаблено ни на одну проверку. Путь Ш-1п2 отдельно
    охраняет тест выше."""
    from pathlib import Path as _P
    from vmp import catalog_access as ca
    from vmp import registry_check as _regc
    from template_catalog import requests as _law
    kinds = _catalog_without_executors(tmp_path, monkeypatch, "duty_limits")
    root = _P(btn.__file__).resolve().parent.parent.parent

    def _press(run_dir, model, task, **kw):
        """Реестр с ОДНИМ незакрытым правилом: пробел глубокой сверки."""
        reg = {"created": "тестовая фикстура", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]),
                          "title": r.get("title") or "",
                          "verdict": ("no_mechanic" if r["id"] == "R-DUTY"
                                      else "covered_full"),
                          "gap_note": "исполнителя нет"}
                         for r in (model or {}).get("rules") or []]}
        path = _P(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path

    monkeypatch.setattr(btn, "press_registry", _press)
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"pairing": ["feasibility-hard"]},
        "spec_version": "1.6"}}
    try:
        run = _run_dir(tmp_path, _fit(_doc(), "R-DUTY", "duty_limits"))
        d = btn.orchestrate(run, FakeSolver(), task="pairing",
                            sleep=lambda *_: None, **fakes)
    finally:
        ca.forget()
    assert d["verdict"] == "остановка: ВМП не закрыт шаблонами COSMOS", d
    gap = next(g for g in d["conformance_gaps"] if g["rule_id"] == "R-DUTY")
    rid = _law.request_id_of(_law.kind_request_key("families", "duty_limits",
                                                   kinds))
    assert gap["request"] == {"request_id": rid, "state": "подана"}
    assert (tmp_path / "catalog-queue" / "outbox" / (rid + ".json")).is_file()


def test_coverage_seam_dormant_without_endpoint(tmp_path):
    """Ручки службы ещё нет (отчёт None) — журнальная пометка и
    прежний путь; трассировка честно несёт null."""
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(),
                        coverage_fn=lambda s, z, t: None,
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "допустимо"
    assert d["trace"]["coverage_authorization"] is None
    steps = {e["step"]: e["status"] for e in d["journal"]}
    assert steps.get("Ш-1п") == "пропущен"


def test_coverage_adapter_contract_shape(tmp_path):
    """Адаптер контракта ручки (@ 2694c4f): критичные для авторизации
    = refused + notHard корзин logic + unmappedSoftRules СВОЕЙ задачи,
    с дедупликацией; вычислимость целевых — отдельным разделом."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    solver.coverage = lambda zip_b64: {
        "packageFingerprint": "fp-1",
        "tasks": {"rostering": {
            "logic": {"refused": [{"rule": "R-REF"}],
                      "notHard": ["R-NH", "R-REF"]},
            "unmappedSoftRules": ["R-SOFT"],
            "objectives": [{"id": "G-1", "computable": False,
                            "reason": "раздельным прогоном"}]}},
        "note": "клетки предметов в сверку не входят"}
    d = btn.orchestrate(run, solver, sleep=lambda *_: None,
                        **_fakes(True))
    assert d["verdict"] == "ожидает решения человека: покрытие"
    assert [u["rule_id"] for u in d["uncovered"]] \
        == ["R-REF", "R-NH", "R-SOFT"]


def test_coverage_not_hard_basket_is_gap_only_for_hard_rules():
    """Канон logic_gate: notHard — законное место всякого не-жёсткого
    правила; пробелом корзина считается только для HARD, правило вне
    модели — fail-closed. Живой ложный стоп: SOFT продления смены с
    посадкой A браковался «вне жёсткого исполнения» (run-0005 Теста,
    2026-09-02)."""
    solver = FakeSolver()
    solver.coverage = lambda zip_b64: {"tasks": {"pairing": {
        "logic": {"notHard": ["R-SOFT-EXT", "R-HARD-X", "R-UNKNOWN"]},
        "unmappedSoftRules": []}}}
    kinds = {"R-SOFT-EXT": "SOFT", "R-HARD-X": "HARD", "R-DEF": "DEF"}
    rep = btn._coverage_report(solver, b"", "pairing", kinds=kinds)
    assert [u["rule_id"] for u in rep["uncovered"]] \
        == ["R-HARD-X", "R-UNKNOWN"]
    # без модели — прежнее fail-closed поведение
    rep0 = btn._coverage_report(solver, b"", "pairing")
    assert [u["rule_id"] for u in rep0["uncovered"]] \
        == ["R-SOFT-EXT", "R-HARD-X", "R-UNKNOWN"]


def test_coverage_incomputable_objective_demotes_mode(tmp_path):
    """Невычислимая целевая честно снимает optimization с умолчания —
    живая находка дня (пейринг-optimization) закрыта машинно."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    solver.coverage = lambda zip_b64: {
        "tasks": {"rostering": {
            "logic": {}, "unmappedSoftRules": [],
            "objectives": [{"id": "G-1", "computable": False}]}}}
    fakes = _fakes(True)
    fakes["load_pkg"] = lambda path: {"manifest": {
        "allowed_modes": {"rostering": ["optimization",
                                        "feasibility-hard"]},
        "objectives": {"rostering": ["G-1"]},
        "spec_version": "1.6"}}
    d = btn.orchestrate(run, solver, sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "допустимо", d
    assert solver.started["mode"] == "feasibility-hard"
    assert solver.started["objective"] is None


def test_вычислимость_целевой_пейринга_судит_построитель_а_не_решатель():
    """Отчёт покрытия решателя честно говорит о СЕБЕ: «стоимость
    пейрингов на объединённой модели невычислима». Целевую пейринга
    исполняет построитель, и его ответ о ростеринге режим пейринга
    понижать не должен: первый сертификат H0 (03.09) — кнопка
    отступила на выполнимость, критерий в построитель не ушёл,
    каталог стоил ноль и CP-SAT доказал оптимум нулевой целевой."""
    manifest = {"allowed_modes": {"pairing": ["optimization",
                                              "feasibility-soft"],
                                  "rostering": ["optimization",
                                                "feasibility-soft"]},
                "objectives": {"pairing": ["O-PAIR"],
                               "rostering": ["O-ROST"]}}
    coverage = {"objectives": [
        {"id": "O-PAIR", "computable": False,
         "reason": "на объединённой модели стоимости пейрингов нет"},
        {"id": "O-ROST", "computable": False}]}
    # Пейринг: ответ решателя не про его исполнителя — optimization.
    assert btn._default_mode(manifest, "pairing", coverage) \
        == ("optimization", "O-PAIR")
    # Ростеринг: решатель судит о себе — режим честно понижается.
    assert btn._default_mode(manifest, "rostering", coverage) \
        == ("feasibility-soft", None)


def test_zone_cost_formula():
    """Г-П3, формула зонной метрики (словарь согласован обеими
    сторонами): вклад / литерал × вес компоненты; нецелые единицы —
    расхождение литерала словами; компонента без исполнителя —
    честная строка «нет», не ноль молча."""
    from webapp import way_compare as wc
    by_name = {"Layover nights away from home": {"soft": -80}}
    execs = {"R-T9-табл-1-строка-1-кол-1": {
        "constraint": "Layover nights away from home", "literal": 40}}
    comps = [
        {"rule_id": "R-T9-табл-1-строка-1-кол-1",
         "title": "Штраф за ночи ожидания", "weight": 10},
        {"rule_id": "R-T9-табл-1-строка-2-кол-1",
         "title": "Штраф за длинные паузы", "weight": 20},
    ]
    z = wc.zone_cost(by_name, comps, executors=execs)
    assert z["cost"] == 20.0            # 80/40 = 2 единицы × вес 10
    assert z["counted"][0]["units"] == 2
    assert [u["rule_id"] for u in z["uncounted"]] \
        == ["R-T9-табл-1-строка-2-кол-1"]
    assert "П-2" in z["uncounted"][0]["reason"]
    import pytest
    with pytest.raises(wc.WayCompareError, match="литерал"):
        wc.zone_cost({"Layover nights away from home": {"soft": -85}},
                     comps[:1], executors=execs)


def test_compare_carries_builder_certificate(tmp_path):
    """Г-П4: стоимость пейринга в сравнении — СПРАВКА из сертификата
    каталога построителя (решатель каталог не судит — происхождение
    числа честное); судья — только счёт финального ростеринга."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    fakes = _compare_fakes()

    def builder(payload, args, out):
        cat = dict(_CATALOG_OK, ip_objective=57600,
                   ip_status="FEASIBLE",
                   config_sig={"method": "columns"})
        raw = json.dumps(cat).encode("utf-8")
        (out / "builder-catalog.json").write_bytes(raw)
        return raw

    fakes["pairing_builder"] = builder
    d = btn.orchestrate_compare(run, solver, sleep=lambda *_: None,
                                **fakes)
    cert = d["pairing_builder_certificate"]
    assert cert["verdict"] == "OK"
    assert cert["chains"] == 1
    assert cert["ip_objective"] == 57600
    assert d["pairing_judged_by"].startswith(
        "счёт финального ростеринга")


def test_compare_forwards_coverage_authorization(tmp_path):
    """П-1 внутри сравнения: непокрытое правило без авторизации
    останавливает ногу (и сравнение — её словами), авторизация
    пробрасывается во все ноги через seams."""
    run = _run_dir(tmp_path, _doc())
    unc = [{"rule_id": "R-A"}]
    fakes = _compare_fakes()
    fakes["coverage_fn"] = lambda s, z, t: {
        "uncovered": unc, "applied": dict(_APPLIED)}
    d = btn.orchestrate_compare(run, FakeSolver(),
                                sleep=lambda *_: None, **fakes)
    assert d["verdict"] == ("сравнение остановлено: пейринг — "
                            "ожидает решения человека: покрытие")
    d2 = btn.orchestrate_compare(run, FakeSolver(),
                                 accepted_uncovered=["R-A"],
                                 sleep=lambda *_: None, **fakes)
    assert d2["verdict"].startswith("сравнение: победитель")
    assert d2["legs"]["пейринг"]["trace"]["coverage_authorization"] \
        == {"accepted": ["R-A"]}


def _restamping_clock(monkeypatch):
    """Часы записи о прогоне, идущие на секунду при каждом сборе.

    Объявление прогона берёт `created_at` из `status.json`, а у папки
    без него (стенд, синтетический прогон, папка до объявления) — из
    ТЕКУЩЕЙ СЕКУНДЫ. Отпечаток объявления едет в манифест пакета
    (`declaration_sha256`), поэтому два экспорта ОДНОЙ постановки по
    разные стороны границы секунды дают разные манифесты. Эти часы
    переводят «иногда» в «всегда»: каждый экспорт получает свою метку.

    С 10.09.2026 (f39a6be8) панель берёт `created_at` устойчивой отметкой,
    а не часами — часы пересборку больше не вызывают. Пересборка записи
    всё равно случается (новая ревизия головы, другой хранитель), и
    потребитель обязан её переживать: поэтому подделывается сам
    отпечаток объявления в манифесте — каждый экспорт получает новый."""
    from cdpm import run_record as rr
    tick = [0]

    def _fresh_declaration_sha(run_dir):
        tick[0] += 1
        return ("%064x" % tick[0])

    monkeypatch.setattr(rr, "declaration_sha256_of", _fresh_declaration_sha)


def test_compare_survives_run_record_restamp(tmp_path, monkeypatch):
    """Пересборка ЗАПИСИ О ПРОГОНЕ — не «постановка изменилась».

    Плавающая краснота полного прогона 10.09.2026
    (test_compare_forwards_coverage_authorization: зелёный поодиночке,
    красный раз в наборе): ноги сравнения едут на БАЙТАХ пакета
    пейринга, и перед этим свежий экспорт сверяется с ним по
    содержимому. Сверка исключала одну метку времени (`created`, снята
    Ф-1.7), а манифест несёт ещё и `declaration_sha256` — отпечаток
    записи о прогоне с `created_at` в теле; на границе секунды нога
    останавливалась «постановка изменилась после утверждённого
    пейринга». Здесь часы переведены на каждый экспорт: прежний код
    краснел ВСЕГДА, нынешний обязан дойти до победителя и назвать
    пересборку словами в журнале — молча такое проходить не должно."""
    _restamping_clock(monkeypatch)
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate_compare(run, FakeSolver(), sleep=lambda *_: None,
                                **_compare_fakes())
    assert d["verdict"].startswith("сравнение: победитель"), d
    for leg in ("button-on-pairing", "button-through"):
        log = json.loads((run / leg / "log.json")
                         .read_text(encoding="utf-8"))
        said = [e for e in log if e["step"] == "Ш-1з"]
        assert said, (leg, log)
        assert "declaration_sha256" in said[0]["detail"], said


def test_compare_twice_on_one_run_dir_is_deterministic(tmp_path, monkeypatch):
    """Тот же ход, что у `test_compare_forwards_coverage_authorization`
    (сравнение зовётся ДВАЖДЫ на одной папке прогона), но с ДВИЖУЩИМСЯ
    отпечатком записи о прогоне: каждый экспорт получает свой
    `declaration_sha256`.

    Так закрывается корень плавающей красноты 10.09.2026: манифест
    пакета несёт ключи, которые двигает сам повторный экспорт, и
    сравнение ног обязано считать их пересборкой, а не «постановка
    изменилась». Прежде такой ход краснел через раз — по границе
    секунды; здесь он краснел бы ВСЕГДА, и потому сторож надёжен.

    Вторая половина корня — общие носители (настройки продукта в
    каталоге данных машины): их уводит autouse-изоляция conftest, и её
    судит tests/test_store_isolation.py."""
    _restamping_clock(monkeypatch)
    run = _run_dir(tmp_path, _doc())
    fakes = _compare_fakes()
    fakes["coverage_fn"] = lambda s, z, t: {
        "uncovered": [{"rule_id": "R-A"}], "applied": dict(_APPLIED)}
    first = btn.orchestrate_compare(run, FakeSolver(),
                                    sleep=lambda *_: None, **fakes)
    assert first["verdict"] == ("сравнение остановлено: пейринг — "
                                "ожидает решения человека: покрытие")
    second = btn.orchestrate_compare(run, FakeSolver(),
                                     accepted_uncovered=["R-A"],
                                     sleep=lambda *_: None, **fakes)
    assert second["verdict"].startswith("сравнение: победитель"), second
    assert second["legs"]["пейринг"]["trace"]["coverage_authorization"] \
        == {"accepted": ["R-A"]}
    # Пересборка записи названа словами в журнале КАЖДОЙ ноги, которая
    # едет на байтах пакета пейринга: молча такое проходить не должно.
    for leg in ("button-on-pairing", "button-through"):
        log = json.loads((run / leg / "log.json").read_text(encoding="utf-8"))
        said = [e for e in log if e["step"] == "Ш-1з"]
        assert said and "declaration_sha256" in said[0]["detail"], (leg, log)


def test_second_press_still_refuses_changed_statement_under_restamp(
        tmp_path, monkeypatch):
    """Пересборка записи не глушит сверку: правка свода между нажатиями
    останавливает второе теми же словами и при бегущих часах — иначе
    исключение ключа было бы не уточнением, а дырой."""
    _restamping_clock(monkeypatch)
    doc = _doc()
    run = _run_dir(tmp_path, doc)
    p = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None,
                        **_builder_fakes(_pairing_fakes()))
    assert p["verdict"] == "пейринг построен", p
    doc["rules"][0]["statement"] += " (ужесточено после пейринга)"
    m.dump_model(doc, run / "extract" / "model.json")
    d = btn.orchestrate(run, FakeSolver(), task="rostering",
                        pairing_run_id="r-1",
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == ("остановка: постановка изменилась после "
                            "утверждённого пейринга")
    assert "model.json" in d["detail"]


def test_package_diff_names_restamp_apart_from_statement():
    """Договор сверки пакетов: пара слов — расхождение ПОСТАНОВКИ и
    пометка о ключах повторного экспорта. Разошёлся только ключ
    пересборки — постановка совпала (первое пусто, второе называет
    ключ); разошлось содержимое — первое называет файл и ключи
    манифеста, и пометка его не отменяет."""
    import io
    import zipfile

    def _zip(manifest, model=b"{}"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json",
                       json.dumps(manifest, ensure_ascii=False))
            z.writestr("model.json", model)
        return buf.getvalue()

    base = {"spec_version": "1.6", "record_id": "run-1",
            "declaration_sha256": "a" * 64}
    other = dict(base, declaration_sha256="b" * 64)
    diff, note = btn._package_diff(_zip(base), _zip(other))
    assert diff == ""
    assert "declaration_sha256" in note
    # содержимое разошлось — остановка, пометка её не отменяет
    diff, note = btn._package_diff(_zip(base), _zip(other, model=b"{ }"))
    assert diff == "model.json"
    assert "declaration_sha256" in note
    # разошёлся ключ манифеста вне перечня — он назван поимённо
    diff, note = btn._package_diff(
        _zip(base), _zip(dict(other, record_id="run-2")))
    assert "record_id" in diff
    # одна и та же пара байтов — ни расхождения, ни пометки
    assert btn._package_diff(_zip(base), _zip(base)) == ("", "")


def test_compare_stops_when_leg_stops(tmp_path):
    """Остановка ноги — остановка сравнения её словами (недопустимый
    счёт — законный вход, остановка шва — нет)."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver(states=("считает", "упал"),
                        failure="OutOfMemoryError")
    d = btn.orchestrate_compare(run, solver, sleep=lambda *_: None,
                                **_compare_fakes())
    # Г-П4: пейринг строит построитель (без прогона решателя), поэтому
    # «упал» решателя бьёт по первой Timefold-ноге — классике.
    assert d["verdict"] == ("сравнение остановлено: классический "
                            "ростеринг — прогон упал")
    assert "OutOfMemoryError" in d["detail"]
    assert len(solver.starts) == 1


def test_compare_margin_from_settings(tmp_path, monkeypatch):
    """Порог приемлемости — настройка производителя (решение
    владельца): cdpm-settings.json, умолчание 5%."""
    cfg = tmp_path / "cdpm-settings.json"
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(cfg))
    assert btn.margin_pct_setting() == 5.0
    cfg.write_text('{"way_margin_pct": 12.5}', encoding="utf-8")
    assert btn.margin_pct_setting() == 12.5
    run = _run_dir(tmp_path / "r", _doc())
    d = btn.orchestrate_compare(run, FakeSolver(),
                                sleep=lambda *_: None,
                                **_compare_fakes())
    assert d["margin_pct"] == 12.5


def test_button_task_absent_from_package_stops(tmp_path):
    """Пакет без семейства режимов задачи — остановка производителя
    СВОИМИ словами (постановка не разрешает такую задачу)."""
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **_fakes(True))
    assert d["verdict"] == "остановка производителя"
    assert "pairing" in d["detail"]


def test_button_crash_is_reported_not_certified(tmp_path):
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver(states=("считает", "упал"),
                        failure="OutOfMemoryError")
    d = btn.orchestrate(run, solver, sleep=lambda *_: None,
                        **_fakes())
    assert d["verdict"] == "прогон упал"
    assert "OutOfMemoryError" in d["detail"]
    assert d["solver_run_id"] == "r-77"
    assert not (run / "button" / "certificate.json").exists()


def test_button_foreign_readings_refused(tmp_path):
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver(applied_run_id="r-ЧУЖОЙ")
    d = btn.orchestrate(run, solver, sleep=lambda *_: None,
                        **_fakes())
    assert d["verdict"] == "остановка: расхождение прогона и показаний"
    assert "r-ЧУЖОЙ" in d["detail"]


def test_button_unconfirmed_readings_refused(tmp_path):
    from webapp.certify_input import CertifyInputError
    run = _run_dir(tmp_path, _doc())
    fakes = _fakes()

    def bad_input(pkg, payload, solved, applied, name):
        raise CertifyInputError("корзины разошлись с пакетом")

    fakes["build_input"] = bad_input
    d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None,
                        **fakes)
    assert d["verdict"] == "остановка: показания не подтверждены гейтом"
    assert "корзины" in d["detail"]


def test_button_pairing_conditions_gaps_write_and_stop(tmp_path):
    """Этап 5б + внешний аудит 04.09.2026 (A1): файл условий на бедной
    фикстуре не собирается — прогон идёт (пробелы артефактом
    conditions-gaps.json, след с пометкой). Флаг conditions больше не
    включатель: нужен ли файл условий какому-то правилу, судит ведомость
    исполнения (правило без исполнителя — остановка с его именем); на
    фикстуре без таких правил остановки нет ни с флагом, ни без."""
    run, fakes = _pairing_fakes_min(tmp_path)
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    gaps = run / "button-pairing" / "conditions-gaps.json"
    assert gaps.is_file(), "пробелы условий обязаны быть артефактом"
    assert "--conditions" not in " ".join(seen["args"])
    binp = json.loads((run / "button-pairing" / "builder-input.json")
                      .read_text(encoding="utf-8"))
    assert "не собрался" in binp["provenance"]["--conditions"]["note"]
    assert "ведомость" in binp["provenance"]["--conditions"]["note"]
    d2 = btn.orchestrate(run, FakeSolver(), task="pairing",
                         conditions=True,
                         sleep=lambda *_: None, **fakes)
    assert d2["verdict"] == "пейринг построен", d2


def test_button_pairing_inject_catalogs(tmp_path):
    """Догон PCDPM+ (пакет решателя 2a8e508): каталоги конструкций
    добора уходят построителю флагами --inject-catalog КАЖДЫЙ;
    несуществующий файл — честная остановка до запуска."""
    run, fakes = _pairing_fakes_min(tmp_path)
    cat1 = tmp_path / "dobor.json"
    cat1.write_text("{}", encoding="utf-8")
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")

    fakes["pairing_builder"] = builder
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        inject_catalogs=[str(cat1)],
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    s = " ".join(seen["args"])
    assert f"--inject-catalog {cat1}" in s
    d2 = btn.orchestrate(run, FakeSolver(), task="pairing",
                         inject_catalogs=[str(tmp_path / "нет.json")],
                         sleep=lambda *_: None, **fakes)
    assert "каталог добора не найден" in d2["verdict"], d2


def test_кнопка_ш2м_матрица_останавливает(tmp_path):
    """Ш-2м (директива владельца 2026-08-29): клетка манифеста,
    не совпадающая с независимым пересчётом COSMOS, останавливает
    кнопку ДО расчёта — состояние готовности не машинная правда."""
    run, fakes = _pairing_fakes_min(tmp_path)
    poisoned = {
        "form_version": 1, "model_revision": 1,
        "columns": {
            "pairing": {"state": "optimization", "grounds": {},
                        "notes": []},
            "rostering": {"state": "stop", "grounds": {},
                          "notes": []}}}
    fakes["load_pkg"] = lambda path: {
        "manifest": {"allowed_modes":
                     {"pairing": ["feasibility-hard"]},
                     "objectives": {}, "spec_version": "1.12",
                     "readiness_matrix": poisoned},
        "model": {}, "clean_data": None}
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == ("остановка: матрица готовности ВМП "
                           "запрещает запуск задачи"), d


def test_предел_без_улучшения_передаётся_только_заданным(tmp_path):
    """Слово владельца 30.08 («для демо 5 секунд»): необязательный
    unimprovedSpentLimitSeconds уходит решателю ТОЛЬКО заданным —
    клиенты без параметра (двойники, прежние реализации) валидны по
    построению; умолчание боевых прогонов не меняется."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    d = btn.orchestrate(run, solver, sleep=lambda *_: None,
                        **_fakes(True))
    assert d["verdict"] == "допустимо"
    assert "unimprovedSpentLimitSeconds" not in solver.started

    class DemoSolver(FakeSolver):
        def start(self, payload, zip_b64, mode, objective_id,
                  limit_s, task="rostering", pairing_run_id=None,
                  unimproved_limit_s=None):
            out = super().start(payload, zip_b64, mode, objective_id,
                                limit_s, task, pairing_run_id)
            self.started["unimproved"] = unimproved_limit_s
            return out

    run2 = _run_dir(tmp_path / "b", _doc())
    solver2 = DemoSolver()
    d2 = btn.orchestrate(run2, solver2, sleep=lambda *_: None,
                         unimproved_spent_limit_s=5, **_fakes(True))
    assert d2["verdict"] == "допустимо"
    assert solver2.started["unimproved"] == 5


def test_deterministic_profile_rostering_params():
    """Д1 (спецификация COSMOS 03.09): в детерминированном режиме
    решатель ростеринга получает зерно и счётные пределы; без единого
    счётного предела — остановка словами, а не молчаливые стенные часы."""
    seen = {}

    class _C:
        def post(self, url, params=None, json=None):
            seen.update(params or {})

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"runId": "r1"}
            return R()

    s = btn.SolverHttp.__new__(btn.SolverHttp)
    s.base = "http://x"
    s._c = _C()
    s.start({}, "", None, None, 60, "rostering",
            deterministic={"seed": 0, "step_limit": 20000,
                           "unimproved_step_limit": 2000})
    assert seen["deterministic"] == "true" and seen["seed"] == 0
    assert seen["stepLimit"] == 20000 and seen["unimprovedStepLimit"] == 2000
    with pytest.raises(btn.ButtonStop):
        s.start({}, "", None, None, 60, "rostering",
                deterministic={"seed": 0})


def test_deterministic_profile_builder_env_and_workers(tmp_path, monkeypatch):
    """Пейринг в профиле Д1: среда подпроцесса несёт PYTHONHASHSEED=0;
    второй ключ бригады поиска не добавляется (последний победил бы)."""
    calls = {}

    class _Proc:
        stdout = ""
        stderr = ""

    def _run(cmd, **kw):
        calls["cmd"] = list(cmd)
        calls["env"] = kw.get("env")
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text("{}", encoding="utf-8")
        return _Proc()

    monkeypatch.setattr("subprocess.run", _run)
    build = btn.subprocess_pairing_builder("метка", deterministic=True)
    build({"employees": []}, ["--deterministic-time", "60", "--workers", "1"],
          tmp_path)
    assert calls["env"] is not None
    assert calls["env"]["PYTHONHASHSEED"] == "0"
    assert calls["cmd"].count("--workers") == 1


def test_button_pairing_passes_history_pins_to_builder(tmp_path):
    """Закрепления из истории для пейринга (слово владельца 05.09.2026):
    утверждённые назначения раздела history (1.17.1) едут построителю
    каталогом закреплений (--pin-catalog, файл history-pins.json в
    button-pairing/), след — в provenance аргументов и в заявлении;
    рейс вне набора — остановка словами, а не пейринг без закреплений."""
    run = _run_dir(tmp_path, _doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["to_payload"] = lambda pkg: {
        "employees": [{"id": "H01", "base": "SVG", "skills": ["Captain"]}],
        "flights": [{"flightNumber": "PL101|2026-09-01T08:00:00",
                     "departureUTCDateTime": "2026-09-01T08:00:00Z",
                     "arrivalUTCDateTime": "2026-09-01T10:00:00Z"}]}
    seen = {}

    def builder(payload, args, out):
        seen["args"] = list(args)
        return json.dumps(_CATALOG_OK).encode("utf-8")
    fakes["pairing_builder"] = builder

    def load_pkg(path, rows=({"Рейс": "PL101", "Дата": "01.09.2026"},)):
        return {"manifest": {
            "allowed_modes": {"pairing": ["feasibility-hard"],
                              "rostering": ["optimization"]},
            "objectives": {"rostering": ["G-1"]},
            "spec_version": "1.17",
            "history": {"as_of": "2026-09-01T00:00", "coverage": "full",
                        "employees": [{"employee_id": "H01",
                                       "approved_assignments": list(rows)}]}}}
    fakes["load_pkg"] = load_pkg
    d = btn.orchestrate(run, FakeSolver(), task="pairing",
                        sleep=lambda *_: None, **fakes)
    assert d["verdict"] == "пейринг построен", d
    args = seen["args"]
    assert "--pin-catalog" in args
    pin_path = Path(args[args.index("--pin-catalog") + 1])
    assert pin_path.name == "history-pins.json" and pin_path.is_file()
    cat = json.loads(pin_path.read_text(encoding="utf-8"))
    assert cat["selected"] == [{
        "fns": ["PL101|2026-09-01T08:00:00"],
        "active_fns": ["PL101|2026-09-01T08:00:00"],
        "duty_fns": [["PL101|2026-09-01T08:00:00"]],
        "sourceUnit": "history:H01", "employees": ["H01"],
        "days": ["2026-09-01"]}]
    bi = json.loads((run / "button-pairing" / "builder-input.json")
                    .read_text(encoding="utf-8"))
    assert bi["provenance"]["--pin-catalog"]["value"] == str(pin_path)
    assert "цепочек 1" in bi["provenance"]["--pin-catalog"]["from"]
    assert d["assumptions"]["history"]["approved_pinned_chains"] == [
        {"sourceUnit": "history:H01", "legs": ["PL101|2026-09-01T08:00:00"],
         "employees": ["H01"]}]
    log = json.loads((run / "button-pairing" / "log.json").read_text(encoding="utf-8"))
    assert any(r.get("step") == "Ш-2с" and r.get("status") == "закрепления истории"
               for r in log), log
    # рейс вне набора — остановка словами
    run2 = _run_dir(tmp_path / "b", _doc())
    fakes["load_pkg"] = lambda path: load_pkg(
        path, rows=({"Рейс": "PL777", "Дата": "01.09.2026"},))
    d2 = btn.orchestrate(run2, FakeSolver(), task="pairing",
                         sleep=lambda *_: None, **fakes)
    assert d2["verdict"].startswith("остановка: утверждённые назначения истории"), d2
    assert "PL777" in json.dumps(d2, ensure_ascii=False)


def test_button_names_hard_violations_of_the_solver_by_constraint(tmp_path):
    """Живой проход H0-КИ-окна (05.09.2026): решатель сообщил «−12hard» без
    имён. Теперь выдача несёт solver_hard_breakdown — ограничения с
    отрицательным hard (имя, hard, совпадений) из /score-breakdown, и
    журнал шага Ш-4р называет их."""

    class HardSolver(FakeSolver):
        def score_breakdown(self, run_id, problem):
            out = super().score_breakdown(run_id, problem)
            out["constraints"] = {
                "Rest before horizon (history)": {"hard": -12, "soft": 0, "matches": 3,
                                                  "weight": "-1hard"},
                "Crew size": {"hard": 0, "soft": 0, "matches": 24, "weight": "-1hard"},
            }
            out["hard"] = -12
            return out
    run = _run_dir(tmp_path, _doc())
    d = btn.orchestrate(run, HardSolver(), sleep=lambda *_: None, **_fakes(True))
    assert d["solver_hard_breakdown"] == {
        "Rest before horizon (history)": {"hard": -12, "matches": 3}}
    log = json.loads((run / "button" / "log.json").read_text(encoding="utf-8"))
    rec = next(r for r in log if r.get("step") == "Ш-4р")
    assert "Rest before horizon (history): -12hard, совпадений 3" in rec["detail"]
    # без жёстких нарушений поле пусто (null), не пропадает
    d2 = btn.orchestrate(_run_dir(tmp_path / "b", _doc()), FakeSolver(),
                         sleep=lambda *_: None, **_fakes(True))
    assert d2["solver_hard_breakdown"] is None


def test_button_moves_parameter_reaches_the_solver_only_when_named(tmp_path):
    """Шаг 4 плана 05.09.2026: набор ходов решателя (moves=chain) едет
    в прогон только явным параметром нажатия; без него параметр решателю
    не передаётся — умолчание решателя не задето."""
    run = _run_dir(tmp_path, _doc())
    solver = FakeSolver()
    btn.orchestrate(run, solver, task="rostering", moves="chain",
                    sleep=lambda *_: None, **_fakes(True))
    assert solver.started["moves"] == "chain"
    solver2 = FakeSolver()
    btn.orchestrate(_run_dir(tmp_path / "b", _doc()), solver2, task="rostering",
                    sleep=lambda *_: None, **_fakes(True))
    assert solver2.started["moves"] is None
