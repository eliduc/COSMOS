# -*- coding: utf-8 -*-
"""Раннер наборов H0 и гейт НЕПРОТИВОРЕЧИВОСТИ выпуска пакета (половина
CDPM, слово владельца 11.09.2026).

Что проверяется:
  * ОТКАЗ выпуска пакета с добавочным ключом `code` тела ответа
    называется словами в журнале прогона, в состоянии стадии, в исходе
    этапа записи и в ИТОГЕ; отказ без кода идёт прежними словами;
  * КАЖДЫЙ код отказа выпуска из раздела 5 контрактов П0 (волна 17)
    назван словами и идёт ТЕМ ЖЕ путём, без особых веток;
  * ожидание набора `expect.consistency` судится по
    `manifest.consistency.verdict` пакета и считается судимым ключом;
  * строка отчёта о пакете несёт вердикт, правила и переводчика;
  * вопрос с полем `contradiction` показывается нормой и значениями
    (только показ: вопросы раннер не судит).

Стенды без сети и без панели: ответ службы подменяется, журнал ловится
захватом вывода."""
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _runner():
    p = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_local_pipeline_h0"] = mod
    spec.loader.exec_module(mod)
    return mod


#: Блок `consistency` манифеста пакета — то, что кладёт гейт выпуска.
CONSISTENCY_BLOCK = {
    "verdict": "sat",
    "model_sha256": "cc" * 32,
    "n_rules": 16,
    "n_translated": 14,
    "translator": {"role": "smt_translator", "provider": "cloud",
                   "model": "claude-sonnet-5"},
    "report": "extract/smt-report.json",
}

#: Вопрос части 1 «противоречие»: одна норма, разные значения.
CONTRADICTION_QUESTION = {
    "id": "Q-1-противоречие-rest_floors",
    "part": "1",
    "title": "Разные значения одной нормы",
    "contradiction": {
        "norm": {"family": "rest_floors", "param": "min_rest_home"},
        "values": [
            {"rule_id": "R-S1-абз-7", "value": 720, "unit": "мин",
             "source": "S1 абз. 7"},
            {"rule_id": "R-S2-строка-5", "value": 600, "unit": "мин",
             "source": "S2 строка 5"},
        ],
    },
}


class _Resp:
    """Ответ службы панели: тело пакета либо отказ словами."""

    def __init__(self, status_code: int, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _Client:
    def __init__(self, resp: _Resp):
        self.resp = resp
        self.urls: list = []

    def get(self, url, **kw):
        self.urls.append(url)
        return self.resp


def _package_zip(manifest: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def _stand(tmp_path, rlp, resp: _Resp, *, questions=(), expect=None,
           strict=False):
    """Раннер со стадией package на подменённой службе: без сети, без
    гейта эквивалентности (он судится своими тестами)."""
    rn = object.__new__(rlp.Runner)
    rn.out = tmp_path / "out"
    rn.out.mkdir(parents=True, exist_ok=True)
    rn.state = {"run": "run-0001", "stages": {}}
    rn.state_path = rn.out / "run-state.json"
    rn.manifest = {"strict_questions": strict}
    if expect is not None:
        rn.manifest["expect"] = expect
    rn.cosmos_press = None
    rn.cosmos_press_path = None
    rn.etalon = None
    rn.signals = None
    rn.client = _Client(resp)
    run_dir = tmp_path / "runs" / "run-0001"
    (run_dir / "extract").mkdir(parents=True, exist_ok=True)
    (run_dir / "extract" / "model.json").write_text(
        json.dumps({"rules": [], "objectives": [],
                    "questions": list(questions)}, ensure_ascii=False),
        encoding="utf-8")
    rn.run_dir = lambda: run_dir
    rn.gate = lambda tag, model_path, pkg_path=None: {
        "tag": tag, "verdict": "EQUIVALENT"}
    return rn


# ── Отказ выпуска пакета: код тела ответа словами ─────────────────────

#: Перечень кодов отказа выпуска, который раннер обязан знать словами:
#: код → находка аудита, которая его ввела. Перечень взят из РАЗДЕЛА 5
#: «Коды отказа выпуска (сводно)» контрактов П0
#: (`P0-CONTRACTS-v1.2-20260911.md`; `landing-version`,
#: `landing-incomplete`, `manifest-mismatch` — оттуда же, 11.1 и 3.1 п. 8)
#: и из строки «Коды отказа выпуска» стороны COSMOS
#: (`P0-COSMOS-SIDE-v1-20260911.md`, раздел 0-бис) — то есть из
#: КОНТРАКТА, а не из самого словаря раннера: иначе тест повторял бы за
#: кодом, вместо того чтобы его судить.
REFUSAL_CODES_BY_FINDING = {
    "smt-contradiction": "гейт непротиворечивости, волна 14",
    "smt-unavailable": "гейт непротиворечивости, волна 14",
    "smt-incomplete": "A01",
    "answer-provenance": "A03",
    "param-carriers-diverge": "A05",
    "landing-version": "A05",
    "landing-incomplete": "A05",
    "family-incomplete": "K01",
    "criterion-direction": "A06",
    "objective-mismatch": "A08",
    "support-missing": "K06",
    "manifest-mismatch": "В-8",
}


@pytest.mark.parametrize("code", sorted(REFUSAL_CODES_BY_FINDING))
def test_каждый_код_отказа_выпуска_назван_словами(code):
    """Каждый код контрактов П0 у раннера со словами: A01
    `smt-incomplete`, A03 `answer-provenance`, A05
    `param-carriers-diverge` и `landing-version` / `landing-incomplete`,
    K01 `family-incomplete`, A06 `criterion-direction`, A08
    `objective-mismatch`, K06 `support-missing`, В-8
    `manifest-mismatch`; прежние `smt-contradiction` и `smt-unavailable`
    судятся тем же законом.

    Без слов новый отказ выглядит поломкой раннера: «ПАКЕТ НЕ СОБРАН:
    409» — красное без причины, а причина у отказа есть и названа
    контрактом."""
    r = _runner()
    why = r.PACKAGE_REFUSAL_CODES.get(code)
    assert isinstance(why, str) and why.strip(), code
    got = r.package_refusal(409, json.dumps(
        {"detail": "подробности службы", "code": code}, ensure_ascii=False))
    assert got == {"status": 409, "code": code,
                   "detail": "подробности службы",
                   "words": f"выпуск пакета отказан (409): {code} — {why}: "
                            f"подробности службы"}
    assert "неизвестен" not in got["words"], code


def test_словарь_отказов_выпуска_равен_перечню_контрактов_П0():
    """Ни больше, ни меньше: код вне перечня контрактов — самодеятельность
    раннера, код перечня без строки — молчание о чужом отказе (раздел 5
    контрактов П0, находки A01, A03, A05, A06, A08, K01, K06, В-8)."""
    r = _runner()
    assert set(r.PACKAGE_REFUSAL_CODES) == set(REFUSAL_CODES_BY_FINDING)
    пустые = [k for k, v in r.PACKAGE_REFUSAL_CODES.items()
              if not (isinstance(v, str) and v.strip())]
    assert пустые == []


def test_поведение_вне_словаря_волной_17_не_изменилось():
    """Новые коды прежнего поведения не трогают: тело отказа БЕЗ кода —
    `code` None и `words` None (раннер причину не выдумывает), а код, не
    названный словами, по-прежнему объявлен неизвестным, а не проглочен
    (так волна 18 увидит свой новый код, а не тишину)."""
    r = _runner()
    assert r.package_refusal(409, json.dumps({"detail": "готовность не та"},
                                             ensure_ascii=False)) == {
        "status": 409, "code": None, "detail": "готовность не та",
        "words": None}
    got = r.package_refusal(409, json.dumps({"detail": "нечто",
                                             "code": "код-из-будущего"},
                                            ensure_ascii=False))
    assert got["code"] == "код-из-будущего" and "неизвестен" in got["words"]


@pytest.mark.parametrize("code", ["smt-incomplete", "family-incomplete",
                                  "landing-incomplete", "manifest-mismatch"])
def test_новый_код_идёт_тем_же_путём_что_и_прежние(code, tmp_path, capsys):
    """Особых веток у новых кодов нет: журнал стадии, состояние стадии и
    исход этапа записи о прогоне берут код и слова одной дорогой —
    `package_refusal` (A01, K01, A05, В-8)."""
    from vmp import run_record as rr
    r = _runner()
    why = r.PACKAGE_REFUSAL_CODES[code]
    body = json.dumps({"detail": "перечень: X, Y", "code": code},
                      ensure_ascii=False)
    rn = _stand(tmp_path, r, _Resp(409, text=body))
    rn.stage_package()
    out = capsys.readouterr().out
    assert f"выпуск пакета отказан (409): {code} — {why}" in out
    assert "перечень: X, Y" in out
    status = rn.state["stages"]["package_status"]
    assert status["status"] == 409 and status["code"] == code
    outcome, reason, words = r.stage_outcome(rn.state, "package")
    assert (outcome, reason) == (rr.OUTCOME_RULE, code)
    assert why in words


def test_коды_отказа_выпуска_разбираются_и_называются_словами():
    """Оба кода гейта названы словами: противоречие несёт ядро из
    `detail`, невозможность проверки — «fail-closed»."""
    r = _runner()
    got = r.package_refusal(409, json.dumps(
        {"detail": "противоречие: ядро R-12, R-15", "code": "smt-contradiction"},
        ensure_ascii=False))
    assert got["code"] == "smt-contradiction"
    assert got["detail"] == "противоречие: ядро R-12, R-15"
    assert "выпуск пакета отказан" in got["words"]
    assert "smt-contradiction" in got["words"]
    assert "ядро R-12, R-15" in got["words"]
    got = r.package_refusal(409, json.dumps(
        {"detail": "решателя z3 нет", "code": "smt-unavailable"},
        ensure_ascii=False))
    assert got["code"] == "smt-unavailable"
    assert "проверка непротиворечивости невозможна (fail-closed)" in got["words"]
    assert "решателя z3 нет" in got["words"]


def test_отказ_без_кода_и_чужое_тело_слов_раннера_не_получают():
    """Прежние отказы выпуска — без кода: раннер им кода не выдумывает и
    слов своих не приписывает (прежнее поведение не меняется)."""
    r = _runner()
    got = r.package_refusal(409, json.dumps({"detail": "готовность не та"},
                                            ensure_ascii=False))
    assert got == {"status": 409, "code": None, "detail": "готовность не та",
                   "words": None}
    assert r.package_refusal(500, "служба сломалась")["words"] is None
    assert r.package_refusal(422, "[1, 2]")["code"] is None
    # Код, которого раннер не знает, назван неизвестным, а не проглочен.
    got = r.package_refusal(409, json.dumps({"detail": "нечто",
                                             "code": "новый-код"},
                                            ensure_ascii=False))
    assert got["code"] == "новый-код" and "неизвестен" in got["words"]


def test_стадия_пакета_называет_отказ_словами_и_кладёт_код_в_состояние(
        tmp_path, capsys):
    r = _runner()
    body = json.dumps({"detail": "противоречие: ядро R-12, R-15",
                       "code": "smt-contradiction"}, ensure_ascii=False)
    rn = _stand(tmp_path, r, _Resp(409, text=body))
    rn.stage_package()
    out = capsys.readouterr().out
    assert "ПАКЕТ НЕ СОБРАН: 409" in out
    assert "выпуск пакета отказан (409): smt-contradiction" in out
    assert "ядро R-12, R-15" in out
    status = rn.state["stages"]["package_status"]
    assert status["status"] == 409 and status["code"] == "smt-contradiction"
    assert "smt-contradiction" in status["words"]


def test_исход_этапа_берёт_код_отказа_а_не_пробел_покрытия():
    """409 перестал быть только пробелом покрытия: код отказа
    становится кодом причины этапа, и запись о прогоне не лжёт."""
    from vmp import run_record as rr
    r = _runner()
    state = {"stages": {"package_status": {
        "status": 409, "error": "{...}", "code": "smt-contradiction",
        "words": "выпуск пакета отказан (409): smt-contradiction — …"}}}
    outcome, reason, words = r.stage_outcome(state, "package")
    assert outcome == rr.OUTCOME_RULE
    assert reason == "smt-contradiction"
    assert "smt-contradiction" in words
    # Отказ без кода судится как прежде — пробелом покрытия.
    state = {"stages": {"package_status": {"status": 409,
                                           "error": "пробел покрытия"}}}
    _o, reason, words = r.stage_outcome(state, "package")
    assert reason == "coverage-gap" and "пробел покрытия" in words


def test_итог_прогона_называет_отказ_выпуска_словами(tmp_path, monkeypatch,
                                                     capsys):
    """Вердикты стадий отказа не показывают: «пакета нет» без причины —
    половина правды, и ИТОГ называет причину словами."""
    r = _runner()
    words = ("выпуск пакета отказан (409): smt-unavailable — проверка "
             "непротиворечивости невозможна (fail-closed)")

    class _Fake:
        def __init__(self, *a, **kw):
            self.state = {"stages": {}}

        def make_passport(self, **kw):
            return {}

        def make_record(self, a, planned):
            return None

        def guard_code(self, stage):
            pass

        def stage_create(self):
            self.state["stages"]["package_status"] = {
                "status": 409, "code": "smt-unavailable", "words": words}

        def record_stage(self, *a, **kw):
            pass

        def finish_record(self, state=None):
            pass

    monkeypatch.setattr(r, "Runner", _Fake)
    assert r.main(["--out", str(tmp_path / "o"), "--until", "create"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "ИТОГ" in ln]
    assert len(lines) == 2, lines
    assert words in lines[1]


# ── Ожидание набора `consistency` ─────────────────────────────────────

def test_ожидание_непротиворечивости_судится_по_манифесту_пакета():
    r = _runner()
    man = {"consistency": dict(CONSISTENCY_BLOCK)}
    assert r.consistency_problems({"consistency": "sat"}, man) == []
    # Набор без жёстких правил ждёт «none».
    none_man = {"consistency": dict(CONSISTENCY_BLOCK, verdict="none")}
    assert r.consistency_problems({"consistency": "none"}, none_man) == []
    probs = r.consistency_problems({"consistency": "sat"}, none_man)
    assert len(probs) == 1
    assert "вердикт непротиворечивости" in probs[0]
    assert "'none'" in probs[0] and "'sat'" in probs[0]


def test_ожидания_нет_не_судится_а_ключа_в_пакете_нет_красная_строка():
    """Совместимость и fail-closed рядом: комплект без ожидания не
    краснеет, а объявленное ожидание при молчащем манифесте — краснеет
    (вердикт обязан класть гейт выпуска)."""
    r = _runner()
    assert r.consistency_problems({}, {"consistency": dict(CONSISTENCY_BLOCK)}) == []
    assert r.consistency_problems({"objectives": 2}, {}) == []
    probs = r.consistency_problems({"consistency": "sat"}, {"coverage": 1})
    assert len(probs) == 1 and "не несёт вердикта" in probs[0]
    # Пакета нет вовсе (выпуск отказан либо прогон встал раньше).
    probs = r.consistency_problems({"consistency": "sat"}, None)
    assert len(probs) == 1 and "не собран" in probs[0]


def test_слово_вердикта_непротиворечивости_проверяется():
    """Ожидание — слово вердикта, а не что угодно: «unsat» ожиданием не
    бывает (с ним пакет не выпускается), опечатка не молчит."""
    r = _runner()
    for bad in ("unsat", "да", "", 1, True):
        probs = r.consistency_problems({"consistency": bad},
                                       {"consistency": dict(CONSISTENCY_BLOCK)})
        assert len(probs) == 1, (bad, probs)
        assert "не слово вердикта" in probs[0], bad
        assert "sat, none" in probs[0], bad


def test_ключ_непротиворечивости_считается_судимым():
    r = _runner()
    judged, skipped = r.expect_key_verdicts({"consistency": "sat",
                                             "objectives": 2})
    assert "consistency" in judged and "consistency" not in skipped


def test_расхождение_ожидания_перекрашивает_вердикт_гейта_пакета(tmp_path,
                                                                 capsys):
    """Сквозь стадию: пакет пришёл с «none», набор ждал «sat» — красная
    строка словами и перекрашенный вердикт."""
    r = _runner()
    man = {"coverage": 1, "readiness_matrix": {"columns": {}},
           "consistency": dict(CONSISTENCY_BLOCK, verdict="none")}
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip(man)),
                expect={"consistency": "sat"})
    rn.stage_package()
    out = capsys.readouterr().out
    assert "ОЖИДАНИЯ НАБОРА НЕ ВЫПОЛНЕНЫ" in out
    assert "вердикт непротиворечивости" in out
    summary = rn.state["stages"]["package"]
    assert summary["verdict"].startswith("EXPECT_MISMATCH:")
    assert summary["expect"]["problems"] and "consistency" in summary["expect"]["checked"]


def test_совпавшее_ожидание_идёт_в_проверенные(tmp_path, capsys):
    r = _runner()
    man = {"coverage": 1, "readiness_matrix": {"columns": {}},
           "consistency": dict(CONSISTENCY_BLOCK)}
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip(man)),
                expect={"consistency": "sat"})
    rn.stage_package()
    out = capsys.readouterr().out
    assert "ОЖИДАНИЯ НАБОРА выполнены" in out
    summary = rn.state["stages"]["package"]
    assert summary["expect"]["problems"] == []
    assert summary["expect"]["checked"] == ["consistency"]


# ── Строка отчёта о пакете ────────────────────────────────────────────

def test_строка_отчёта_о_пакете_несёт_вердикт_правила_и_переводчика(tmp_path,
                                                                    capsys):
    r = _runner()
    man = {"coverage": 1, "readiness_matrix": {"columns": {}},
           "consistency": dict(CONSISTENCY_BLOCK)}
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip(man)))
    rn.stage_package()
    out = capsys.readouterr().out
    assert "непротиворечивость: вердикт 'sat'" in out
    assert "правил 16, переведено 14" in out
    assert "роль smt_translator" in out and "канал cloud" in out
    assert "модель claude-sonnet-5" in out
    assert "extract/smt-report.json" in out


def test_пакет_без_блока_непротиворечивости_строки_не_получает(tmp_path,
                                                               capsys):
    """Старый пакет блока не несёт — раннер о нём молчит, а не выдумывает
    вердикт."""
    r = _runner()
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip({"coverage": 1})))
    rn.stage_package()
    assert "непротиворечивость" not in capsys.readouterr().out
    assert r.consistency_words({"coverage": 1}) is None
    assert r.consistency_words(None) is None


# ── Показ вопроса о противоречии ──────────────────────────────────────

def test_вопрос_о_противоречии_показывается_нормой_и_значениями():
    r = _runner()
    lines = r.question_lines([CONTRADICTION_QUESTION])
    assert len(lines) == 2, lines
    assert "Q-1-противоречие-rest_floors" in lines[0]
    assert "противоречие: норма rest_floors" in lines[1]
    assert "параметр min_rest_home" in lines[1]
    assert "R-S1-абз-7 = 720 мин (источник S1 абз. 7)" in lines[1]
    assert "R-S2-строка-5 = 600 мин (источник S2 строка 5)" in lines[1]
    # Норма может называться предметом, а не семьёй посадки.
    q = {"id": "Q-2", "part": "1", "title": "т",
         "contradiction": {"norm": {"subject": "минимальный отдых",
                                    "param": "часы"},
                           "values": [{"rule_id": "R-1", "value": 10}]}}
    assert "норма минимальный отдых" in r.question_lines([q])[1]
    assert "R-1 = 10" in r.question_lines([q])[1]


def test_вопрос_без_поля_противоречия_идёт_одной_строкой():
    r = _runner()
    q = {"id": "Q-3b-история-boundary", "part": "3b", "title": "Остатки"}
    assert r.question_lines([q]) == [
        "   ? Q-3b-история-boundary [3b]: Остатки"]
    assert r.contradiction_words(q) is None
    assert r.contradiction_words({"contradiction": "строка"}) is None
    assert r.question_lines([]) == [] and r.question_lines(None) == []


def test_вопрос_о_противоречии_показывается_и_на_нестрогом_наборе(tmp_path,
                                                                  capsys):
    """Часть 1 блокирует строки постановки — молчать о ней нельзя даже
    там, где вопросы анкеты набору разрешены."""
    r = _runner()
    plain = {"id": "Q-3b-история-boundary", "part": "3b", "title": "Остатки"}
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip({"coverage": 1})),
                questions=[plain, CONTRADICTION_QUESTION], strict=False)
    rn.stage_package()
    out = capsys.readouterr().out
    assert "ВОПРОСЫ О ПРОТИВОРЕЧИИ: 1" in out
    assert "противоречие: норма rest_floors" in out
    # Вопрос без противоречия сюда не попадает: показ — о части 1.
    assert "Q-3b-история-boundary" not in out


def test_на_строгом_наборе_противоречие_идёт_строкой_под_вопросом(tmp_path,
                                                                  capsys):
    r = _runner()
    rn = _stand(tmp_path, r, _Resp(200, content=_package_zip({"coverage": 1})),
                questions=[CONTRADICTION_QUESTION], strict=True)
    rn.stage_package()
    out = capsys.readouterr().out
    assert "АНКЕТА НЕ ПУСТА: вопросов 1" in out
    assert "Q-1-противоречие-rest_floors" in out
    assert "противоречие: норма rest_floors" in out
