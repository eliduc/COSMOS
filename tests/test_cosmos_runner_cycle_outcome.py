# -*- coding: utf-8 -*-
"""A07-раннер: КРАСНОТА НЕ ЧИСТОГО ЦИКЛА ДОХОДИТ ДО КОНЦА.

Замечание С1 неавторского ревью `REVIEW-A07RUN-856f588c-COSMOS`: отказ
чтения реестра находок и живая блокирующая находка красили ТОЛЬКО
вердикт гейта стадии (пометка `NOT_CLEAN_CYCLE:` в состоянии раннера,
в `gates/gate-cycle.json` и в строке ИТОГ журнала). Дальше по конвейеру
краснота не шла:

  * `stage_outcome` судила стадию `cycle` кодом ответа службы, которого
    в блоке `cycle_status` нет вовсе, и ЗАПИСЬ О ПРОГОНЕ получала исход
    «успех» и при отказе реестра, и при живом блокере;
  * `stage_counters` берёт из блока только ЧИСЛА, а чистота была
    `bool`, отказ — строкой, живые и блокирующие — перечнями: запись
    несла от стадии лишь `rounds` и `sec`, и ни одного числа о
    находках;
  * ожидания набора на стадии `package` о цикле не спрашивали вовсе,
    вердикт пакета оставался прежним, и прогон снаружи выглядел
    зелёным.

Охраняемое этим стендом:

  * блок `cycle_status` несёт три ЦЕЛЫХ числа — `live_n`,
    `blockers_n`, `registry_refused` (0/1) — и они доходят до
    счётчиков этапа записи (`stage_counters`, `rr.normalize_entry`);
    числа именно целые, а не `bool`: булево счётчик намеренно
    пропускает;
  * исход этапа `cycle` при отказе реестра — «отказ по правилу» с
    кодом `registry-refusal`, при живом блокере — с кодом
    `not-clean-cycle`, при неподтверждённой чистоте без блокеров — с
    кодом `cycle-incomplete`; живые НЕблокирующие при чистоте по Б/К —
    «предупреждение» с кодом `live-non-blocking`; слова исходов — из
    словаря исходов записи (`vmp/run_record.py`), нового класса исхода
    не заводится;
  * ОСТАНОВКА ЧЕЛОВЕКОМ НАЗЫВАЕТСЯ СВОИМ ИСХОДОМ: состояние семейства
    «остановлено: <стадия>» (кнопка «Остановить» панели) даёт записи
    «остановлен человеком» с кодом `stopped-in-panel` — не «отказ по
    правилу», как было прежде. Признак берётся ПРЕДИКАТОМ ЗАКОНА
    (`cdpm.exchange.stopped_by_human`), а не литералом состояния, и
    судится раньше смыслового суда реестра, но позже настоящего сбоя
    службы;
  * ФАЙЛ РЕЕСТРА ПОСЛЕ ИСПОЛНЕННОГО ЦИКЛА ОБЯЗАН БЫТЬ (A07-R5): раннер
    зовёт загрузчик парой с предикатом закона
    (`registry_expected(exdir, state)`), и исчезнувший реестр даёт
    отказ словами закона, а не «чисто»;
  * НАСТОЯЩИЙ СБОЙ СЛУЖБЫ СТАРШЕ СМЫСЛА: целочисленный ответ 5xx в
    блоке стадии — «сбой», а не «отказ по правилу»;
  * ПЕРЕСУД ПОСЛЕ ОТВЕТОВ: стадия `answers` кладёт свой блок
    `cycle_status` (свежий реестр, а ЗАЯВКА ИСХОДА — у самого цикла) и
    судится тем же `cycle_outcome`; запись стадии `cycle` в журнале при
    этом НЕ переписывается;
  * ОДИН ЗАКОН ИТОГА: состояние прогона в итогах считает СЛОВАРЬ
    записи (`rr.state_from_journal`, по исходам всего журнала) — тот
    же, которым судят хранитель записи (панель) и карточка прогона
    COSMOS; своего отбора записей раннер не держит, и «текущий итог
    может стать чистым» исполняют ожидания набора по ПОСЛЕДНЕМУ блоку
    цикла, а не подмена итога записи;
  * чистый цикл без живых находок — «успех» без кода и слов; стадия,
    которой этим прогоном не было, судится общим правилом (отказ не
    выдумывается);
  * ожидания набора краснеют на не чистом цикле У ЛЮБОГО набора —
    `cycle_clean_problems` не спрашивает манифест: чистота цикла есть
    закон реестра, а не свойство набора;
  * набор, который ВСЁ ЖЕ объявил ожидаемый вердикт стадии `cycle`
    ключом `stage_verdicts`, получает расхождение и общим порядком:
    пометка `NOT_CLEAN_CYCLE:` перед словом вердикта — уже не то
    слово, которого ждали.

Ожидания парные с законом: реестр носителя пишет продуктовый писатель
`cdpm.exchange`, блок `cycle_status` строит сам раннер своей чистой
функцией, слова исходов берутся из `vmp.run_record`. Ни портов, ни
сети, ни служб, ни времени: пути только `tmp_path`.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdpm import exchange as ex                                   # noqa: E402
from vmp import run_record as rr                                  # noqa: E402

RUNNER_PATH = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
#: Состояние прогона, которым панель объявляет чистоту (то же, что в
#: tests/test_cosmos_runner_registry_law.py).
CLEAN_STATE = "сквозной цикл: чисто по Б/К (кругов: 2)"


def _runner():
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0",
                                                  RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_local_pipeline_h0"] = mod
    spec.loader.exec_module(mod)
    return mod


def _finding(fid, state, severity="КРИТИЧЕСКОЕ"):
    return {"id": fid, "key": f"k-{fid}", "severity": severity,
            "state": state, "opened_round": 1, "closed_round": None}


def _cycle_block(mod, exd: Path, findings, state=CLEAN_STATE,
                 broken: bool = False) -> dict:
    """Блок `cycle_status` состояния раннера — ровно так, как его кладёт
    стадия: выдача чистой функции плюс прежние ключи стадии
    (`exchange`, `locks`, `sec`). `broken` — носитель негодной формы,
    на котором закон отказывает."""
    exd.mkdir(parents=True, exist_ok=True)
    if broken:
        (exd / "registry.json").write_text("{не json", encoding="utf-8")
    else:
        ex._dump_registry(exd / "registry.json", {"findings": list(findings)})
    cyc = mod.cycle_status_of(exd, state)
    return dict(cyc, exchange={"rounds": 2, "verdict": "чисто"}, locks=[],
                sec=7)


def _state(block: dict, verdict: str = "EQUIVALENT") -> dict:
    """Состояние раннера с пройденной стадией цикла: сводка гейта под
    именем стадии и блок `cycle_status` рядом."""
    return {"stages": {"cycle": {"tag": "cycle", "verdict": verdict},
                       "cycle_status": block}}


# ── счётчики блока цикла в записи о прогоне ───────────────────────────

def test_счётчики_цикла_доходят_до_записи_о_прогоне(tmp_path):
    """Три числа блока — живые, блокирующие и признак отказа — уезжают
    в счётчики этапа записи. Раньше запись несла от стадии только
    `rounds` и `sec`: о находках в ней не было ни числа."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("B1", "открыто"),
                          _finding("E1", "тупик", severity="РЕДАКЦИОННОЕ")])
    # закон над тем же носителем — ожидание берётся у него, не у теста
    reg = ex.load_registry(tmp_path / "exchange" / "registry.json")
    assert block["live_n"] == len(ex.live_findings(reg))
    assert block["blockers_n"] == len(ex.registry_blockers(reg))
    assert block["registry_refused"] == 0

    counters = mod.stage_counters(_state(block), "cycle")
    assert counters["live_n"] == block["live_n"]
    assert counters["blockers_n"] == block["blockers_n"]
    assert counters["registry_refused"] == 0
    # прежние счётчики стадии на месте
    assert counters["rounds"] == 2 and counters["sec"] == 7


def test_счётчики_цикла_целые_числа_а_не_булево(tmp_path):
    """Счётчик — `int`: `stage_counters` намеренно пропускает `bool`
    (`isinstance(value, bool)`), и признак отказа булевым в запись бы
    не попал. Проверяется типом, а не равенством: `True == 1`."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [], broken=True)
    for key in ("live_n", "blockers_n", "registry_refused"):
        assert isinstance(block[key], int), key
        assert not isinstance(block[key], bool), key
    assert block["registry_refused"] == 1
    counters = mod.stage_counters(_state(block), "cycle")
    assert counters["registry_refused"] == 1
    # чистота осталась булевой — она слово вердикта, а не счётчик
    assert isinstance(block["clean"], bool)
    assert "clean" not in counters


def test_счётчики_цикла_запись_принимает(tmp_path):
    """Запись о прогоне принимает эти счётчики как есть: свободный
    словарь чисел схемы (`counters`), нормализация их не теряет."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("B1", "открыто")])
    outcome, code, words = mod.stage_outcome(_state(block), "cycle")
    entry = rr.normalize_entry({
        "stage": "cycle", "started_at": None, "finished_at": None,
        "outcome": outcome, "reason_code": code, "words": words,
        "actor": "раннер", "counters": mod.stage_counters(_state(block),
                                                          "cycle")})
    assert entry["counters"]["blockers_n"] == block["blockers_n"]
    assert entry["counters"]["registry_refused"] == 0
    assert entry["outcome"] in rr.OUTCOMES
    assert entry["reason_code"] == "not-clean-cycle"


# ── исход стадии цикла ────────────────────────────────────────────────

def test_отказ_реестра_исход_стадии_не_успех(tmp_path):
    """Реестр не читаем — стадия НЕ «успех»: «отказ по правилу» с
    кодом `registry-refusal` и словами самого отказа. Неизвестность не
    заменяется успехом."""
    mod = _runner()
    exd = tmp_path / "exchange"
    block = _cycle_block(mod, exd, [], broken=True)
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json")

    outcome, code, words = mod.stage_outcome(_state(block), "cycle")
    assert outcome != rr.OUTCOME_OK
    assert outcome == rr.OUTCOME_RULE and outcome in rr.OUTCOMES
    assert code == "registry-refusal"
    assert mod.CYCLE_REFUSAL_CODES[code] in words
    assert str(err.value) in words


def test_живой_блокер_исход_стадии_не_успех(tmp_path):
    """Реестр прочтён, но живая блокирующая находка есть — стадия НЕ
    «успех»: код `not-clean-cycle`, слова называют состояние прогона и
    ЧИСЛО блокирующих. Панель объявила «чисто по Б/К» — это её не
    спасает."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("B1", "открыто")])
    assert block["clean"] is False and "registry_refusal" not in block

    outcome, code, words = mod.stage_outcome(_state(block), "cycle")
    assert outcome != rr.OUTCOME_OK
    assert outcome == rr.OUTCOME_RULE and outcome in rr.OUTCOMES
    assert code == "not-clean-cycle"
    assert mod.CYCLE_REFUSAL_CODES[code] in words
    assert CLEAN_STATE in words and str(block["blockers_n"]) in words


def test_грязное_состояние_прогона_тоже_не_успех(tmp_path):
    """Блокирующих нет, но конвейер исхода не заявил (лимит кругов) —
    чистота НЕ подтверждена, и исход стадии тоже не «успех». Код
    отдельный — `cycle-incomplete`: «предъявить нечего» и «предъявлен
    живой блокер» — разные вещи, и звать первое «не чистым циклом»
    было бы неправдой."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [],
                         state="сквозной цикл: лимит кругов (3)")
    assert block["clean"] is False and block["blockers_n"] == 0
    outcome, code, words = mod.stage_outcome(_state(block), "cycle")
    assert outcome == rr.OUTCOME_RULE and code == "cycle-incomplete"
    assert mod.CYCLE_REFUSAL_CODES[code] in words
    assert "сквозной цикл: лимит кругов (3)" in words


def test_чистый_цикл_исход_успех(tmp_path):
    """Чистый цикл без живых находок — «успех» без кода и слов:
    правило красноты не красит там, где красить нечего."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("C1", "закрыто")])
    assert block["clean"] is True and block["live_n"] == 0
    assert mod.stage_outcome(_state(block), "cycle") == (rr.OUTCOME_OK,
                                                         None, None)
    assert mod.cycle_outcome(block) is None


def test_живые_неблокирующие_предупреждение_а_не_краснота(tmp_path):
    """Чисто по Б/К, но живая РЕДАКЦИОННАЯ находка есть —
    «предупреждение» с кодом `live-non-blocking`, не «отказ по
    правилу» и не молчаливый «успех»: их дорога — бэклог панели, но
    запись о прогоне о них не молчит."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("E1", "тупик", severity="РЕДАКЦИОННОЕ")])
    assert block["clean"] is True and block["live_n"] == 1
    outcome, code, words = mod.stage_outcome(_state(block), "cycle")
    assert outcome == rr.OUTCOME_WARN and outcome in rr.OUTCOMES
    assert code == "live-non-blocking"
    assert mod.CYCLE_WARNING_CODES[code] in words and "1" in words
    # предупреждение ожиданий набора не красит
    assert mod.cycle_clean_problems(_state(block)["stages"]) == []


def test_стадия_цикла_не_шла_отказ_не_выдумывается():
    """Стадии этим прогоном не было — исход судит общее правило, а не
    выдуманный отказ: ни блока, ни признака чистоты нет."""
    mod = _runner()
    assert mod.stage_outcome({"stages": {}}, "cycle") == (rr.OUTCOME_OK,
                                                          None, None)
    assert mod.cycle_outcome(None) is None
    assert mod.cycle_outcome({}) is None
    assert mod.cycle_outcome("не словарь") is None


def test_правило_цикла_чужие_стадии_не_трогает():
    """Суд цикла — только у стадии `cycle`: у стадии выпуска пакета
    прежний разбор ответа службы не изменился."""
    mod = _runner()
    state = {"stages": {"package_status": {"status": 409},
                        "cycle_status": {"clean": False, "blockers_n": 3,
                                         "state": "лимит"}}}
    assert mod.stage_outcome(state, "package")[1] == "coverage-gap"
    assert mod.stage_outcome(state, "cycle")[1] == "not-clean-cycle"


# ── ожидания набора ───────────────────────────────────────────────────

def test_ожидания_набора_краснеют_на_отказе_и_на_блокере(tmp_path):
    """Не чистый цикл — расхождение ожиданий У ЛЮБОГО набора: ни ключа
    в манифесте, ни объявленного вердикта для этого не нужно. Иначе
    набор, забывший объявить стадию, оставался бы зелёным — это и был
    «зелёный снаружи» замечания С1."""
    mod = _runner()
    блокер = _cycle_block(mod, tmp_path / "с-блокером",
                          [_finding("B1", "открыто")])
    отказ = _cycle_block(mod, tmp_path / "негодный", [], broken=True)
    чисто = _cycle_block(mod, tmp_path / "чистый", [])

    beda = mod.cycle_clean_problems(_state(блокер)["stages"])
    assert len(beda) == 1 and "not-clean-cycle" in beda[0]
    assert "cycle" in beda[0]

    beda2 = mod.cycle_clean_problems(_state(отказ)["stages"])
    assert len(beda2) == 1 and "registry-refusal" in beda2[0]

    assert mod.cycle_clean_problems(_state(чисто)["stages"]) == []
    # стадии не было — расхождения нет: судить нечего
    assert mod.cycle_clean_problems({}) == []
    assert mod.cycle_clean_problems(None) == []


def test_объявленный_вердикт_цикла_замечает_пометку(tmp_path):
    """Набор ВПРАВЕ объявить ожидаемый вердикт стадии `cycle` ключом
    `stage_verdicts`; тогда пометка `NOT_CLEAN_CYCLE:`, которой стадия
    перекрашивает вердикт гейта, даёт расхождение и общим порядком.

    Слово вердикта у наборов разное (у набора с открытыми вопросами о
    целевой цикл эталону не эквивалентен по замыслу), поэтому
    ожидание тут берётся как ЛЮБОЕ слово, а не зашивается."""
    mod = _runner()
    for ждали in ("EQUIVALENT", "NOT_EQUIVALENT"):
        exp = {"stage_verdicts": {"cycle": ждали}}
        stages = {"cycle": {"verdict": ждали}}
        assert mod.stage_verdicts_problems(exp, stages) == ([], [])
        # стадия перекрасила вердикт пометкой — ожидание разошлось
        крашено = {"cycle": {"verdict": "NOT_CLEAN_CYCLE:" + ждали}}
        probs, warns = mod.stage_verdicts_problems(exp, крашено)
        assert warns == []
        assert len(probs) == 1 and "cycle" in probs[0]
        assert "NOT_CLEAN_CYCLE:" in probs[0] and ждали in probs[0]


def test_коды_отказа_цикла_названы_словами():
    """У каждого кода стадии есть слова человеку, и других кодов стадия
    не выдаёт: код без слов был бы машинным именем без смысла. Отказы и
    предупреждения живут ВРОЗЬ — это разные исходы словаря записи, и
    смешивать их в одном перечне значило бы стирать разницу."""
    mod = _runner()
    assert set(mod.CYCLE_REFUSAL_CODES) == {"registry-refusal",
                                            "not-clean-cycle",
                                            "cycle-incomplete"}
    assert set(mod.CYCLE_WARNING_CODES) == {"live-non-blocking"}
    assert set(mod.CYCLE_HUMAN_CODES) == {"stopped-in-panel"}
    перечни = (mod.CYCLE_REFUSAL_CODES, mod.CYCLE_WARNING_CODES,
               mod.CYCLE_HUMAN_CODES)
    for перечень in перечни:
        for code, words in перечень.items():
            assert code == code.lower() and " " not in code
            assert isinstance(words, str) and len(words) > 10
    # три перечня — три РАЗНЫХ исхода словаря записи, и пересекаться им
    # нельзя: общий код стирал бы разницу классов
    все = [c for перечень in перечни for c in перечень]
    assert len(все) == len(set(все))


# ── восемь регрессий квитанции аудитора ───────────────────────────────
#
# Квитанция `A07RUN-REVIEW-RECEIPT-20260912-GPT`, раздел «Конкретная
# помощь автору»: «Проверять и функцию, и конечный payload
# `record_stage`/агрегацию в изолированной синтетике, с БУКВАЛЬНЫМИ
# ожидаемыми исходами, а не только равенством двух потребителей одной
# функции». Ниже — восемь классов оттуда, и каждый судит СТРОКИ записи
# о прогоне: `outcome` и `reason_code` этапа журнала, `results.state`
# итогов. Слова берутся у словаря записи (`vmp.run_record`), перечни
# состояний и серьёзностей — у закона находок (`cdpm.exchange`); своих
# слов стенд не пишет.

#: Набор «ИД H0-К» — для регрессии 5 (манифест читается, не правится).
KIT_H0K = ROOT / "open-bench" / "polyaris-h0k"
#: НЕЗАЯВЛЯЮЩИЕ ФИНАЛЫ НИТИ СКВОЗНОГО ЦИКЛА — слова панели, а не
#: выдумка стенда (замечание Н3 второго неавторского ревью: прежде тут
#: стояло «аудит остановлен: лимит кругов» — состояние СТАРОГО
#: аудит-цикла, которое раннер не видит вовсе). Ровно эти пять строк
#: ставит нить `start_full_cycle` панели, когда исход не заявлен;
#: сверка с исходником панели — отдельным тестом ниже.
PANEL_FINALS = ("сквозной цикл: лимит кругов (3)",
                "красный гейт аудит-цикла",
                "красный гейт арбитража",
                "остановлено: сквозной цикл",
                "отказ сквозного цикла")
#: РАЗДЕЛЯЕТ ИХ ЗАКОН, А НЕ СТЕНД (`cdpm.exchange.stopped_by_human`):
#: остановка ЧЕЛОВЕКОМ — свой класс исхода записи («остановлен
#: человеком», код `stopped-in-panel`), прочие незаявляющие финалы —
#: «отказ по правилу» с кодом `cycle-incomplete`. Своего перечня
#: состояний стенд не заводит: перечислены СЛОВА ПАНЕЛИ, а классы им
#: раздаёт предикат закона (полнота разбиения судится тестом ниже).
HUMAN_STOP_STATES = tuple(s for s in PANEL_FINALS if ex.stopped_by_human(s))
INCOMPLETE_STATES = tuple(s for s in PANEL_FINALS
                          if not ex.stopped_by_human(s))
#: По одному из каждого класса — для тестов, где важен не перебор, а
#: сам класс.
INCOMPLETE_STATE = INCOMPLETE_STATES[0]
HUMAN_STOP_STATE = HUMAN_STOP_STATES[0]
#: Состояние после кругов ответов (`Runner.stage_answers` судит им
#: круг). Заявкой исхода ЦИКЛА оно не является — см. С2.
ANSWERS_STATE = "ответы применены"


def _declaration(plan) -> dict:
    """Объявление прогона — наименьшее, которое принимает словарь
    записи (`rr.validate`: каталог, привязки и документы правил
    названы). Настоящее объявление собирает паспорт набора; здесь
    судятся ИСХОДЫ этапов, и голова взята минимальной, чтобы стенд не
    зависел ни от набора, ни от каталога, ни от служб."""
    return {"run_id": "проба-исхода-цикла", "purpose": "регресс",
            "created_by": "стенд", "contour": {"name": "local"},
            "catalog": {"version": "проба", "fingerprint":
                        rr.sha256_text("каталог пробы")},
            "bindings": {"catalog_version": "проба"},
            "rules_documents": [{"name": "правила пробы",
                                 "file": "правила.docx", "kind": "docx",
                                 "sha256": rr.sha256_text("правила пробы")}],
            "planned_stages": [str(s) for s in plan]}


class _Стенд:
    """Носитель записи БЕЗ службы: ровно те поля и методы, которые
    читают `Runner.record_stage` и `Runner.finish_record`. Сами методы
    вызываются НАСТОЯЩИЕ (`mod.Runner.record_stage(стенд, …)`) — стенд
    судит продуктовый путь записи, а не свою копию его."""

    def __init__(self, mod, record: dict, stages: dict):
        self.mod = mod
        self.record = record
        self.state = {"run": "run-0001", "stages": stages}
        self.отправлено = []

    def save_record(self) -> None:
        """Запись на диск стенду не нужна: судится тело, а не файл."""

    def send_record_append(self, entries=None, results=None) -> None:
        """Хозяин записи — панель; службы тут нет, отправленное только
        копится, чтобы было видно, что этап уезжал."""
        self.отправлено.append((entries, results))

    def fetch_tree(self):
        """Ручки дерева прогонов нет — сравнения в итогах пусты."""
        return None

    def record_stage(self, *args, **kw):
        return self.mod.Runner.record_stage(self, *args, **kw)


def _прогон(mod, stages: dict, пройдено, план=None) -> dict:
    """Прогон по ЗАПИСИ: состояние раннера — синтетика, отметки этапов
    и итоги ставят настоящие `Runner.record_stage` и
    `Runner.finish_record` теми же аргументами, что даёт `main`
    (`*stage_outcome(rn.state, stage)`)."""
    план = list(план if план is not None else пройдено)
    record = rr.new_record(_declaration(план))
    стенд = _Стенд(mod, record, stages)
    for stage in пройдено:
        mod.Runner.record_stage(стенд, stage, rr.now_iso(), rr.now_iso(),
                                *mod.stage_outcome(стенд.state, stage))
    mod.Runner.finish_record(стенд)
    return record


def _этап(record: dict, stage: str) -> dict:
    """ПОСЛЕДНЯЯ запись стадии в журнале."""
    rows = [e for e in record.get("journal") or [] if e.get("stage") == stage]
    assert rows, f"стадии {stage} в журнале нет"
    return rows[-1]


def test_регрессия_1_живая_бк_находка_при_ответе_200_не_успех(tmp_path):
    """(1) Живая Б/К-находка + `status: 200` (и без числового status) —
    запись стадии НЕ «успех»: «отказ по правилу» с кодом
    `not-clean-cycle`. Прежде ответ 200 (как и его отсутствие) означал
    «успех» — это и был зелёный снаружи."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("B1", "открыто", severity="БЛОКИРУЮЩЕЕ")])
    for статус in ({"status": 200}, {}):
        stages = {"cycle": {"tag": "cycle", "verdict": "EQUIVALENT"},
                  "cycle_status": dict(block, **статус)}
        record = _прогон(mod, stages, ["cycle"])
        запись = _этап(record, "cycle")
        assert запись["outcome"] == "отказ по правилу"
        assert запись["outcome"] == rr.OUTCOME_RULE
        assert запись["reason_code"] == "not-clean-cycle"
        assert запись["counters"]["blockers_n"] == 1
        assert record["results"]["state"] == rr.STATE_RULE_STOP


@pytest.mark.parametrize("порча", ["чужая версия", "битый JSON"])
def test_регрессия_2_отказ_закона_словами_закона(tmp_path, порча):
    """(2) Чужая версия и битый JSON — «отказ по правилу» с кодом
    `registry-refusal`, и слова в записи — СЛОВА ЗАКОНА: раннер их не
    сочиняет, а переносит (`ExchangeError` загрузчика)."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    if порча == "чужая версия":
        (exd / "registry.json").write_text(
            '{"schema_version": "registry/9", "findings": []}',
            encoding="utf-8")
    else:
        (exd / "registry.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json")

    block = mod.cycle_status_of(exd, CLEAN_STATE)
    record = _прогон(mod, {"cycle_status": block}, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись["reason_code"] == "registry-refusal"
    # СЛОВА ЗАКОНА ДОХОДЯТ ЦЕЛИКОМ — до блока стадии; в запись они
    # уезжают ровно настолько, насколько пускает предел слов этапа
    # (`cycle_outcome` режет по 300 знакам). На новой базе слова закона
    # о чужой версии длиннее предела — и это не потеря смысла, а
    # известная граница поля: сам блок несёт их полностью.
    assert str(err.value) in block["registry_refusal"]
    полные = (f"{mod.CYCLE_REFUSAL_CODES['registry-refusal']}: "
              f"{block['registry_refusal']}")
    assert полные.startswith(запись["words"])
    assert запись["words"] == полные or len(запись["words"]) == 300
    assert запись["counters"]["registry_refused"] == 1
    assert record["results"]["state"] == rr.STATE_RULE_STOP


def test_регрессия_2_ошибка_ввода_вывода_тоже_отказ(tmp_path, monkeypatch):
    """(2, ветка OSError) — детерминированная инъекция ошибки чтения с
    ограниченной областью подмены (просьба рецензента, Н2): носитель
    есть и годен, но `Path.read_text` бросает `OSError`. Исход тот же —
    `registry-refusal` со словами отказа; успехом неизвестность не
    заменяется.

    ОЖИДАНИЕ — У ЗАКОНА НА ТОМ ЖЕ НОСИТЕЛЕ. На новой базе загрузчик
    сам называет эту беду своими словами («реестр временно недоступен»)
    и намеренно НЕ выносит подробности операционной системы в слова,
    оставляя их причиной исключения. Поэтому стенд сверяет запись с
    тем, что бросил закон, а не с текстом подменённой ошибки: слова в
    записи обязаны быть словами закона, чьи бы они ни были."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    ex._dump_registry(exd / "registry.json", {"findings": []})
    беда = OSError("носитель занят другим процессом")

    def _падает(self, *a, **kw):
        raise беда

    monkeypatch.setattr(Path, "read_text", _падает)
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json")
    block = mod.cycle_status_of(exd, CLEAN_STATE)
    monkeypatch.undo()

    # сырые слова носителя закон оставил ПРИЧИНОЙ, а не своими словами
    assert err.value.__cause__ is беда
    record = _прогон(mod, {"cycle_status": block}, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись["reason_code"] == "registry-refusal"
    assert str(err.value) in block["registry_refusal"]
    assert str(err.value) in запись["words"]
    assert record["results"]["state"] == rr.STATE_RULE_STOP


def test_исчезнувший_реестр_доходит_до_записи_о_прогоне(tmp_path):
    """ЗАКОН A07-R5 НА УРОВНЕ ЗАПИСИ: цикл отработал (в папке обмена
    лежит его вердикт), а файла реестра нет — запись о прогоне обязана
    получить «отказ по правилу» с кодом `registry-refusal` и словами
    ЗАКОНА, а не зелёный «успех».

    Прежде исчезновение носителя читалось пустым реестром: живых
    находок нет — значит «чисто», и запись выходила зелёной. Ровно
    этот ложный зелёный и закрывает предикат `registry_expected`."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    # цикл отработал: вердикт на месте, а реестр исчез
    (exd / ex.CYCLE_VERDICT_FILE).write_text('{"verdict": "успех"}',
                                             encoding="utf-8")
    assert not (exd / "registry.json").exists()
    assert ex.registry_expected(exd, CLEAN_STATE) is True
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json", expect_file=True)

    block = dict(mod.cycle_status_of(exd, CLEAN_STATE),
                 exchange={"rounds": 2, "verdict": "чисто"}, locks=[], sec=7)
    assert block["clean"] is False and block["registry_refused"] == 1
    stages = {"cycle": {"tag": "cycle", "verdict": "EQUIVALENT"},
              "cycle_status": block}
    record = _прогон(mod, stages, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись["reason_code"] == "registry-refusal"
    assert str(err.value) in block["registry_refusal"]
    assert запись["counters"]["registry_refused"] == 1
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    беда = mod.cycle_clean_problems(stages)
    assert len(беда) == 1 and "registry-refusal" in беда[0]


def test_регрессия_3_чистый_цикл_успех(tmp_path):
    """(3) Чистый цикл — «успех» без кода и слов, состояние прогона
    «завершён»: правило красноты не красит там, где красить нечего."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("C1", "закрыто")])
    assert "закрыто" not in ex.LIVE_STATES        # закон, не слово теста
    assert block["clean"] is True and block["live_n"] == 0
    record = _прогон(mod, {"cycle_status": block}, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "успех" == rr.OUTCOME_OK
    assert запись["reason_code"] is None and запись["words"] is None
    assert record["results"]["state"] == "завершён" == rr.STATE_DONE


def test_регрессия_4_живая_редакционная_предупреждение(tmp_path):
    """(4) Живая РЕДАКЦИОННАЯ находка при чистоте по Б/К —
    «предупреждение» с кодом `live-non-blocking`, и состояние прогона
    всё равно «завершён»: предупреждение прогон не красит, но и
    молчанием не становится."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("E1", "открыто", severity="РЕДАКЦИОННОЕ"),
                          _finding("S1", "тупик", severity="СЕРЬЁЗНОЕ")])
    # серьёзность вне блокирующих — по закону находок, не по слову теста
    assert "РЕДАКЦИОННОЕ" not in ex.BLOCKING_SEVERITIES
    assert "СЕРЬЁЗНОЕ" not in ex.BLOCKING_SEVERITIES
    record = _прогон(mod, {"cycle_status": block}, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "предупреждение" == rr.OUTCOME_WARN
    assert запись["reason_code"] == "live-non-blocking"
    assert запись["counters"]["live_n"] == 2
    assert запись["counters"]["blockers_n"] == 0
    assert record["results"]["state"] == "завершён" == rr.STATE_DONE


@pytest.mark.parametrize("финал", INCOMPLETE_STATES)
def test_регрессия_5_незаявляющий_финал_цикла_правдивый_не_чистый(tmp_path,
                                                                  финал):
    """(5) НЕЗАЯВЛЯЮЩИЙ ФИНАЛ ЦИКЛА: блокирующих нет, но конвейер исхода
    не заявил — «отказ по правилу» с кодом `cycle-incomplete`, а не
    выдуманный «успех» и не «живой блокер». Судятся ВСЕ пять таких
    финалов нити сквозного цикла словами панели (замечание Н3 второго
    ревью: прежде тут стояло выдуманное состояние, а посылка «H0-К до
    ответов — незаявляющий финал» не измерена и по коду скорее ложна —
    вопросы анкеты не находки реестра, и H0-К с чистым реестром заявит
    «чисто по Б/К» ещё до ответов).

    И никакого ложного требования вердикта цикла: манифест H0-К в
    `expect.stage_verdicts` стадию `cycle` НЕ объявляет, а ожидания
    набора краснеют законом реестра — без объявления."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [], state=финал)
    assert block["blockers_n"] == 0 and block["clean"] is False

    import json
    манифест = json.loads((KIT_H0K / "kit-manifest.json")
                          .read_text(encoding="utf-8"))
    вердикты = (манифест.get("expect") or {}).get("stage_verdicts") or {}
    assert "cycle" not in вердикты, "манифест H0-К вердикта цикла не объявляет"

    stages = {"cycle": {"tag": "cycle", "verdict": "NOT_EQUIVALENT"},
              "cycle_status": block}
    record = _прогон(mod, stages, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись["reason_code"] == "cycle-incomplete"
    assert финал in запись["words"]
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    # ожидания набора краснеют тем же кодом и без объявления в манифесте
    беда = mod.cycle_clean_problems(stages)
    assert len(беда) == 1 and "cycle-incomplete" in беда[0]


def test_незаявляющие_финалы_взяты_у_панели():
    """Пять незаявляющих финалов и одно заявляющее состояние — СЛОВА
    ПАНЕЛИ, а не стенда: каждая строка обязана стоять в исходнике нити
    сквозного цикла `cdpm/webui.py`. Иначе стенд судил бы выдуманный
    класс состояний (замечание Н3), а на новой базе расхождение слов
    прошло бы незамеченным.

    Здесь же — СТОРОЖ ЛИТЕРАЛА ЗАЯВКИ ЧИСТОТЫ (`OUTCOME_CLAIMED_STATE`):
    предиката «заявил ЧИСТОТУ» закон пока не даёт, строка в раннере
    одна, и сверяется она с исходником панели. Общий
    `ex.outcome_claimed` сюда не годится и проверен на том же месте:
    он ШИРЕ заявки чистоты (истина и на «аудит остановлен: …»), и
    подменять им эту строку нельзя.

    И РАЗБИЕНИЕ ПАНЕЛЬНЫХ ФИНАЛОВ ПОЛНО: каждый финал попадает ровно в
    один класс закона — остановка человеком либо незаявляющий финал."""
    mod = _runner()
    панель = (ROOT / "cdpm" / "webui.py").read_text(encoding="utf-8")
    for состояние in PANEL_FINALS + (ANSWERS_STATE,
                                     mod.OUTCOME_CLAIMED_STATE):
        # число панель подставляет отдельным куском («лимит кругов (N)»,
        # «чисто по Б/К (кругов: N)») — сверяется часть ДО скобки
        кусок = состояние.split("(")[0].strip()
        assert кусок and кусок in панель, состояние
    # разбиение панельных финалов законом полно и без пересечений
    assert HUMAN_STOP_STATES and INCOMPLETE_STATES
    assert len(HUMAN_STOP_STATES) + len(INCOMPLETE_STATES) == len(PANEL_FINALS)
    assert not (set(HUMAN_STOP_STATES) & set(INCOMPLETE_STATES))
    # и ни один панельный финал заявкой чистоты не считается
    for финал in PANEL_FINALS:
        assert not финал.startswith(mod.OUTCOME_CLAIMED_STATE), финал
        assert mod.cycle_status_of(None, финал)["clean"] is False
    assert mod.ANSWERS_APPLIED_STATE == ANSWERS_STATE
    assert not ANSWERS_STATE.startswith(mod.OUTCOME_CLAIMED_STATE)
    # заявка ЧИСТОТЫ уже заявка исхода — но не наоборот: общий предикат
    # закона шире, и подменить им литерал нельзя (потому он и остался)
    assert ex.outcome_claimed(CLEAN_STATE) is True
    шире = [s for s in ex.OUTCOME_CLAIMED_STATES
            if not s.startswith(mod.OUTCOME_CLAIMED_STATE)]
    assert шире, "закон дал предикат чистоты — литерал пора заменять"
    for состояние in шире:
        assert ex.outcome_claimed(состояние) is True, состояние


def test_регрессия_6_ответы_законно_разрешают(tmp_path):
    """(6) Ответы законно разрешают прежние находки — и разрешают их НА
    ДЕЛЕ: между двумя чтениями меняется РЕЕСТР (блокер закрыт кругом
    ответов), а не слово состояния (замечание С2 второго ревью: прежде
    «разрешение» изображалось сменой состояния на «ответы применены»,
    и тем же приёмом «разрешался» красный гейт цикла).

    Заявка исхода у обоих чтений одна — состояние САМОГО цикла. Что
    проверяется:

      * запись стадии `cycle` в журнале ОСТАЁТСЯ прежней
        (`not-clean-cycle` — правда о своём моменте, прежний исход не
        переписывается);
      * запись стадии `answers` — «успех»: пересуд по свежему реестру
        чист;
      * состояние прогона — по словарю записи, то есть «остановлен по
        правилу»: красная запись цикла в журнале есть, и одного закона
        итога на всех читателей это не нарушает;
      * ОЖИДАНИЯ НАБОРА по последнему блоку ЧИСТЫ — ровно здесь и
        живёт «текущий итог может стать чистым» квитанции аудитора."""
    mod = _runner()
    exd = tmp_path / "exchange"
    # цикл кончился заявкой чистоты, а в реестре был живой блокер
    было = _cycle_block(mod, exd, [_finding("B1", "открыто",
                                            severity="БЛОКИРУЮЩЕЕ")],
                        state=CLEAN_STATE)
    assert было["clean"] is False and было["blockers_n"] == 1
    # круги ответов закрыли находку — реестр стал другим
    ex._dump_registry(exd / "registry.json",
                      {"findings": [dict(_finding("B1", "закрыто",
                                                  severity="БЛОКИРУЮЩЕЕ"),
                                         closed_round=2)]})
    # пересуд идёт ЗАЯВКОЙ САМОГО ЦИКЛА, как его делает стадия ответов
    стало = mod.cycle_status_of(exd, было["state"])
    assert стало["clean"] is True and стало["blockers_n"] == 0

    stages = {"cycle": {"tag": "cycle", "verdict": "EQUIVALENT"},
              "cycle_status": было,
              "answers_status": {"rounds": [{"round": 1}], "book": True,
                                 "open_after": 0, "sec": 11},
              "answers": {"tag": "answers", "verdict": "EQUIVALENT",
                          "cycle_status": стало}}
    record = _прогон(mod, stages, ["cycle", "answers"])

    цикл = _этап(record, "cycle")
    assert цикл["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert цикл["reason_code"] == "not-clean-cycle"
    ответы = _этап(record, "answers")
    assert ответы["outcome"] == "успех" == rr.OUTCOME_OK
    assert ответы["reason_code"] is None
    # блок цикла НЕ переписан: в состоянии раннера лежат оба
    assert stages["cycle_status"] is было
    assert stages["cycle_status"]["clean"] is False

    # итог записи — общий закон словаря: красная запись цикла в журнале
    assert record["results"]["state"] == rr.state_from_journal(record)
    assert record["results"]["state"] == "остановлен по правилу" \
        == rr.STATE_RULE_STOP
    # а «текущий итог стал чистым» видно ожиданиями набора
    assert mod.cycle_clean_problems(stages) == []


def test_регрессия_6_пересуд_берёт_заявку_у_цикла_а_не_у_ответов(tmp_path):
    """(6, суть С2) Состояние «ответы применены» о цикле не утверждает
    НИЧЕГО, и заявкой чистоты оно не считается. Незаявляющий финал
    цикла остаётся `cycle-incomplete` и после ответов — над тем же
    реестром без блокеров.

    Прежде такой прогон объявлялся «завершённым» с чистыми ожиданиями:
    красный гейт аудит-цикла, красный гейт арбитража, остановка,
    отказ стадии и лимит кругов уходили в пакет зелёными."""
    mod = _runner()
    exd = tmp_path / "exchange"
    for финал in INCOMPLETE_STATES:
        было = _cycle_block(mod, exd, [], state=финал)
        # заявка у САМОГО цикла — как её берёт стадия ответов
        стало = mod.cycle_status_of(exd, было["state"])
        assert стало["clean"] is False, финал
        stages = {"cycle_status": было,
                  "answers": {"tag": "answers", "verdict": "EQUIVALENT",
                              "cycle_status": стало}}
        record = _прогон(mod, stages, ["cycle", "answers"])
        assert _этап(record, "answers")["reason_code"] == "cycle-incomplete"
        assert record["results"]["state"] == rr.STATE_RULE_STOP, финал
        беда = mod.cycle_clean_problems(stages)
        assert len(беда) == 1 and "cycle-incomplete" in беда[0], финал
        # а состояние после ответов чистоты не заявляет и само по себе
        assert mod.cycle_status_of(exd, ANSWERS_STATE)["clean"] is False


def test_регрессия_6_краснота_цикла_без_ответов_остаётся(tmp_path):
    """(6, обратная сторона) Подменить исход нечем: стадии `answers` в
    журнале нет вовсе (прогон кончился раньше) либо она «не начат» —
    краснота стадии `cycle` в состояние прогона входит как есть.

    Иначе «итог судится по актуальным свидетельствам» превратилось бы
    в «красноту всегда можно потерять»."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [],
                         state=INCOMPLETE_STATE)
    stages = {"cycle_status": block}
    # прогон встал на цикле: план шире пройденного
    record = _прогон(mod, stages, ["cycle"], план=["cycle", "answers"])
    assert _этап(record, "answers")["outcome"] == "не начат" \
        == rr.OUTCOME_NOT_STARTED
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    # и стадии answers в плане не было вовсе — то же самое
    record2 = _прогон(mod, stages, ["cycle"])
    assert [e["stage"] for e in record2["journal"]] == ["cycle"]
    assert record2["results"]["state"] == rr.STATE_RULE_STOP


def test_регрессия_6_ответы_с_живым_блокером_итог_не_чистят(tmp_path):
    """(6, третья сторона) Пересуд после ответов ПОКРАСНЕЛ (в реестре
    живой блокер) — стадия `answers` отказывает тем же кодом, и
    состояние прогона остаётся «остановлен по правилу». Ответы гасят
    краснотý цикла только тогда, когда пересуд чист."""
    mod = _runner()
    было = _cycle_block(mod, tmp_path / "чисто", [], state=CLEAN_STATE)
    # ответы завели НОВЫЙ блокер — заявка цикла прежняя, реестр свежий
    стало = _cycle_block(mod, tmp_path / "с-блокером",
                         [_finding("B1", "открыто", severity="БЛОКИРУЮЩЕЕ")],
                         state=было["state"])
    stages = {"cycle_status": было,
              "answers": {"tag": "answers", "verdict": "EQUIVALENT",
                          "cycle_status": стало}}
    record = _прогон(mod, stages, ["cycle", "answers"])
    ответы = _этап(record, "answers")
    assert ответы["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert ответы["reason_code"] == "not-clean-cycle"
    assert ответы["counters"]["blockers_n"] == 1
    assert record["results"]["state"] == rr.STATE_RULE_STOP
    беда = mod.cycle_clean_problems(stages)
    assert len(беда) == 1 and "not-clean-cycle" in беда[0]
    assert "answers" in беда[0]


def test_регрессия_7_подменённый_итог_при_текущем_блокере(tmp_path):
    """(7) Состояние прогона ЗАЯВЛЯЕТ «сквозной цикл: чисто по Б/К», а
    в реестре текущий блокер — «отказ по правилу» с кодом
    `not-clean-cycle`: слово конвейера реестра не отменяет, и панель
    пересуживает такой прогон так же."""
    from cdpm import webui
    mod = _runner()
    run_dir = tmp_path / "run-0001"
    block = _cycle_block(mod, run_dir / "exchange",
                         [_finding("B1", "открыто", severity="БЛОКИРУЮЩЕЕ")],
                         state=CLEAN_STATE)
    assert block["state"] == CLEAN_STATE and block["clean"] is False
    # панель того же дерева отнимает у этого прогона зелёное
    показано = webui._state_by_registry(run_dir, CLEAN_STATE)
    assert показано != CLEAN_STATE and ex.VERDICT_NOT_CLOSED in показано

    record = _прогон(mod, {"cycle_status": block}, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись["reason_code"] == "not-clean-cycle"
    assert CLEAN_STATE in запись["words"]
    assert record["results"]["state"] == rr.STATE_RULE_STOP


def test_регрессия_8_настоящий_5xx_остаётся_сбоем(tmp_path):
    """(8) Настоящий ответ службы 503 — «сбой», а НЕ «отказ по
    правилу»: поломку службы нельзя выдавать за штатную остановку по
    правилу, и смысловой суд до неё не доходит (блок при этом не
    чист — судить его было бы чем).

    4xx остаётся отказом по правилу с кодом ответа — общим правилом."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange",
                         [_finding("B1", "открыто", severity="БЛОКИРУЮЩЕЕ")])
    assert block["clean"] is False
    record = _прогон(mod, {"cycle_status": dict(block, status=503,
                                                error="служба недоступна")},
                     ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "сбой" == rr.OUTCOME_FAILURE
    assert запись["reason_code"] == "http-503"
    assert record["results"]["state"] == "сбой" == rr.STATE_FAILURE

    # 4xx — отказ по правилу кодом ответа, тоже не смысловой суд
    record4 = _прогон(mod, {"cycle_status": dict(block, status=404,
                                                 error="прогона нет")},
                      ["cycle"])
    запись4 = _этап(record4, "cycle")
    assert запись4["outcome"] == "отказ по правилу" == rr.OUTCOME_RULE
    assert запись4["reason_code"] == "http-404"
    assert record4["results"]["state"] == rr.STATE_RULE_STOP


def test_итог_записи_считает_словарь_а_не_раннер(tmp_path):
    """ОДИН ЗАКОН ИТОГА НА ВСЕХ ЧИТАТЕЛЕЙ ЗАПИСИ (замечание С1 второго
    неавторского ревью): раннер своего отбора записей не держит и имени
    состояния словарю не называет — `finish_record` без явного
    состояния закрывает запись общим `rr.state_from_journal`.

    Прежде раннер считал итог по ПОСЛЕДНЕЙ записи каждой стадии
    («прошедшие ответы перекрывают цикл»), а хранитель записи (панель,
    `cdpm/run_record.py::_record_state`) и карточка прогона COSMOS
    (`webapp/run_journal.py::_keeper_state`) этого отбора не знают: одна
    запись одного прогона давала «завершён» в `--out` и «остановлен по
    правилу» у панели.

    Сторож судит и ИСХОДНИКОМ (`state_by_latest` в раннере больше нет,
    и своего имени состояния он не передаёт), и ПОВЕДЕНИЕМ: итог записи
    равен `rr.state_from_journal` той же записи."""
    import ast
    mod = _runner()
    assert not hasattr(mod, "state_by_latest"), \
        "второй закон итога в раннере снова заведён"
    исходник = RUNNER_PATH.read_text(encoding="utf-8")
    дерево = ast.parse(исходник)
    закрытие = [n for n in ast.walk(дерево)
                if isinstance(n, ast.FunctionDef) and n.name == "finish_record"]
    assert len(закрытие) == 1
    вызовы = [n for n in ast.walk(закрытие[0]) if isinstance(n, ast.Call)
              and ast.unparse(n.func).endswith("rr.finish")]
    assert len(вызовы) == 1, "запись закрывается ровно одним вызовом словаря"
    # третий аргумент — ровно имя, названное звавшим, без пересчёта
    assert [ast.unparse(a) for a in вызовы[0].args][-1] == "state"

    # поведение: итог записи = закон словаря, на всех исходах сразу
    block = _cycle_block(mod, tmp_path / "exchange", [])
    for outcome, ждём in ((rr.OUTCOME_FAILURE, rr.STATE_FAILURE),
                          (rr.OUTCOME_HUMAN, rr.STATE_HUMAN_STOP),
                          (rr.OUTCOME_RULE, rr.STATE_RULE_STOP),
                          (rr.OUTCOME_WARN, rr.STATE_DONE),
                          (rr.OUTCOME_OK, rr.STATE_DONE)):
        record = rr.new_record(_declaration(["cycle", "answers"]))
        стенд = _Стенд(mod, record, {"cycle_status": block})
        mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                                outcome, "проба", "исход стадии пробой")
        mod.Runner.record_stage(стенд, "answers", rr.now_iso(), rr.now_iso(),
                                rr.OUTCOME_OK, None, None)
        mod.Runner.finish_record(стенд)
        assert record["results"]["state"] == ждём, outcome
        assert record["results"]["state"] == rr.state_from_journal(record)


def test_успех_ответов_не_гасит_сбой_и_остановку_цикла(tmp_path):
    """Замечание С3 второго ревью: прежний отбор выбрасывал запись
    стадии `cycle` при ЛЮБОМ прошедшем исходе ответов — в том числе
    гасил «сбой» и «остановлен человеком», которые ответы разрешить не
    могут (достижимо в ПРОДОЛЖЕННОМ прогоне `--run`: стадия цикла
    упала, автопродолжение пошло с ответов).

    Общий закон словаря такого не допускает: журнал
    `[cycle сбой, …, answers успех]` даёт «сбой», а
    `[cycle остановлен человеком, …]` — «остановлен человеком»."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [])
    for исход_цикла, ждём in ((rr.OUTCOME_FAILURE, rr.STATE_FAILURE),
                              (rr.OUTCOME_HUMAN, rr.STATE_HUMAN_STOP)):
        record = rr.new_record(_declaration(["cycle", "answers", "package"]))
        стенд = _Стенд(mod, record, {"cycle_status": block})
        # прежний, оборванный проход
        mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                                исход_цикла, "stage-exception",
                                "стадия цикла оборвалась")
        mod.Runner.record_stage(стенд, "answers", None, None,
                                rr.OUTCOME_NOT_STARTED, "stage-not-reached",
                                "этап плана не начат")
        # продолжение прогона: ответы и пакет прошли
        mod.Runner.record_stage(стенд, "answers", rr.now_iso(), rr.now_iso(),
                                rr.OUTCOME_OK, None, None)
        mod.Runner.record_stage(стенд, "package", rr.now_iso(), rr.now_iso(),
                                rr.OUTCOME_OK, None, None)
        mod.Runner.finish_record(стенд)
        assert record["results"]["state"] == ждём, исход_цикла
        # и запись стадии цикла в журнале осталась как была
        assert _этап(record, "cycle")["outcome"] == исход_цикла


def test_итог_раннера_сходится_с_хранителем_и_карточкой(tmp_path):
    """Тот же журнал — тот же итог у ВСЕХ читателей записи: раннера
    (запись в `--out`), словаря (`vmp/run_record.py`), хранителя записи
    панели (`cdpm/run_record.py::_record_state`, режим «хранитель
    панель»: этапы приезжают ему дописыванием) и карточки прогона
    COSMOS (`webapp/run_journal.py::_keeper_state`).

    Именно это расхождение и было замечанием С1 второго ревью: раннер
    говорил «завершён», панель и карточка — «остановлен по правилу»."""
    from cdpm import run_record as хранитель
    from webapp import run_journal as карточка
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [],
                         state=INCOMPLETE_STATE)
    record = rr.new_record(_declaration(["cycle", "answers", "package"]))
    стенд = _Стенд(mod, record, {"cycle_status": block})
    mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                            *mod.stage_outcome(стенд.state, "cycle"))
    for stage in ("answers", "package"):
        mod.Runner.record_stage(стенд, stage, rr.now_iso(), rr.now_iso(),
                                rr.OUTCOME_OK, None, None)
    mod.Runner.finish_record(стенд)

    журнал = record["journal"]
    итог = record["results"]["state"]
    assert итог == rr.STATE_RULE_STOP
    assert итог == rr.state_from_journal(record)
    assert итог == хранитель._record_state({"state": ANSWERS_STATE}, [],
                                           True, журнал)
    assert итог == хранитель._record_state({"state": ANSWERS_STATE}, [],
                                           False, журнал)
    assert итог == карточка._keeper_state({"results": {"state": итог},
                                           "journal": журнал})


def test_стадия_ответов_пересуживает_цикл_и_историю_не_трогает():
    """Проводка пересуда — в самой стадии `answers` (живой прогон тут
    не делается: он требует службы, набора и слова владельца, поэтому
    судится ИСХОДНИК стадии).

    Стадия обязана: позвать `cycle_status_of` (один загрузчик, свой
    `json.loads` тут запрещён тем же законом) и положить выдачу ВНУТРЬ
    сводки стадии ответов. Присваивать `stages["cycle_status"]` ей
    нельзя — это история стадии цикла, и переписывать её значило бы
    терять правду о моменте."""
    import ast
    дерево = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    стадия = [n for n in ast.walk(дерево)
              if isinstance(n, ast.FunctionDef) and n.name == "stage_answers"]
    assert len(стадия) == 1
    вызовы = [ast.unparse(n.func) for n in ast.walk(стадия[0])
              if isinstance(n, ast.Call)]
    assert any(c.endswith("cycle_status_of") for c in вызовы), вызовы
    цели = [ast.unparse(t) for n in ast.walk(стадия[0])
            if isinstance(n, ast.Assign) for t in n.targets]
    поле = [t for t in цели if "cycle_status" in t]
    assert поле, цели
    for t in поле:
        assert "'cycle_status'" in t or '"cycle_status"' in t
        assert "stages" not in t or "answers" in t, \
            "пересуд ложится в сводку стадии ответов, а не поверх блока цикла"


def test_состояние_названное_звавшим_не_пересчитывается(tmp_path):
    """Состояние, названное `main` (прерывание человеком, сбой стадии),
    старше пересчёта: `finish_record` его не трогает — иначе прерванный
    прогон с чистыми стадиями объявлялся бы «завершённым»."""
    mod = _runner()
    block = _cycle_block(mod, tmp_path / "exchange", [])
    record = rr.new_record(_declaration(["cycle"]))
    стенд = _Стенд(mod, record, {"cycle_status": block})
    mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                            *mod.stage_outcome(стенд.state, "cycle"))
    mod.Runner.finish_record(стенд, rr.STATE_HUMAN_STOP)
    assert _этап(record, "cycle")["outcome"] == rr.OUTCOME_OK
    assert record["results"]["state"] == rr.STATE_HUMAN_STOP


# ── сбой службы внутри цикла судится сбоем (С4) ───────────────────────

class _СтендЦикла:
    """Носитель для НАСТОЯЩЕЙ `Runner.stage_cycle` без службы: ручка
    сквозного цикла отвечает 200, а `wait` отдаёт ровно то состояние
    линии, которым кончила нить панели. Дальше броска стадия идти не
    должна — и папка прогона, и сохранение состояния, и гейт нарочно
    отказывают, чтобы «дошло» было видно отказом, а не молчанием."""

    class _Ответ:
        status_code = 200
        text = ""

    def __init__(self, mod, статус: dict):
        self.mod = mod
        self.статус = статус
        self.state = {"run": "run-0001", "stages": {}}
        self.max_rounds = 5
        self.max_megacycles = 3
        self.client = self
        self.позвано = []

    def post(self, path, params=None, files=None):
        self.позвано.append(path)
        return self._Ответ()

    def wait(self, busy_states, timeout_s, label):
        return self.статус

    def run_dir(self):
        raise AssertionError("стадия пошла дальше броска: папка прогона")

    def _save(self):
        raise AssertionError("стадия пошла дальше броска: сохранение")

    def gate(self, *a, **kw):
        raise AssertionError("стадия пошла дальше броска: гейт")


def _отказ_нити(refusal_state: str, e) -> dict:
    """Состояние линии и слова — СЛОВАМИ ПАНЕЛИ: их собирает её же
    `webui._stage_failure`, стенд ничего не сочиняет."""
    from cdpm import webui
    return webui._stage_failure(refusal_state, e)


def test_сбой_стадии_панели_внутри_цикла_судится_сбоем():
    """Замечание С4 второго неавторского ревью: нить сквозного цикла
    при исключении кладёт слова в общее поле `error` («отказ сквозного
    цикла», «остановка: каталог шаблонов недоступен: …»), а раннер это
    писал в журнал и шёл дальше — исход выходил «отказ по правилу»
    `cycle-incomplete`, то есть «система остановилась сама и назвала
    причину», хотя служба СЛОМАЛАСЬ.

    Теперь стадия бросает, как бросает извлечение при не-«извлечено»:
    признак общий (поле `error`), слов панели раннеру знать не нужно."""
    from cdpm import catalog_bridge
    mod = _runner()
    случаи = [_отказ_нити("отказ сквозного цикла",
                          RuntimeError("реестр находок не открылся")),
              _отказ_нити("отказ сквозного цикла",
                          catalog_bridge.CatalogRequired("папки каталога нет"))]
    for отказ in случаи:
        assert отказ.get("error"), отказ
        стенд = _СтендЦикла(mod, dict(отказ, exchange_summary={}))
        with pytest.raises(RuntimeError) as беда:
            mod.Runner.stage_cycle(стенд)
        # слова броска называют и состояние линии, и причину панели
        assert str(отказ["state"]) in str(беда.value)
        assert str(отказ["error"])[:60] in str(беда.value)
        # и до сохранения блока цикла стадия не дошла: продолжение
        # прогона (`--run`) стадию цикла пропускать не должно
        assert "cycle_status" not in стенд.state["stages"]


def test_бросок_цикла_даёт_записи_исход_сбой():
    """Тот же случай на уровне ЗАПИСИ: `main` ловит исключение стадии и
    пишет исход «сбой» с кодом `stage-exception`, а запись закрывается
    состоянием «сбой» — не «остановлен по правилу»."""
    mod = _runner()
    отказ = _отказ_нити("отказ сквозного цикла", RuntimeError("нить упала"))
    record = rr.new_record(_declaration(["cycle"]))
    стенд = _Стенд(mod, record, {})
    # ровно те аргументы, которыми зовёт `main` в ветке исключения
    mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                            rr.OUTCOME_FAILURE, "stage-exception",
                            f"RuntimeError: {отказ['error']}"[:300])
    mod.Runner.finish_record(стенд, rr.STATE_FAILURE)
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "сбой" == rr.OUTCOME_FAILURE
    assert запись["reason_code"] == "stage-exception"
    assert record["results"]["state"] == "сбой" == rr.STATE_FAILURE


@pytest.mark.parametrize("стоп", HUMAN_STOP_STATES)
def test_остановка_человеком_называется_своим_исходом(tmp_path, стоп):
    """ОСТАТОК ПРОШЛОЙ РЕВИЗИИ ЗАКРЫТ: «остановлено: <стадия>» — стоп
    человеком в панели — нить ставит БЕЗ `error`, и прежде раннер судил
    его `cycle-incomplete`, то есть «отказ по правилу»: класс ниже
    настоящего («остановлен человеком»), хоть и выше прежнего «успеха».

    Признак раннер берёт ПРЕДИКАТОМ ЗАКОНА
    (`cdpm.exchange.stopped_by_human`), а не литералом состояния:
    своего перечня состояний прогона у него по-прежнему нет. Стенд
    состояния тоже не классифицирует — берёт слова панели и спрашивает
    закон.

    Проверяется весь путь: сама стадия (броска нет — служба не
    ломалась), суд стадии (`stage_outcome`) и ЗАПИСЬ О ПРОГОНЕ — исход
    «остановлен человеком» с кодом `stopped-in-panel`, итог записи
    «остановлен человеком» (словарём, а не раннером), и ожидания
    набора при этом краснеют: остановленный кнопкой цикл чистым не
    бывает."""
    mod = _runner()
    assert ex.stopped_by_human(стоп) is True
    assert ex.outcome_claimed(стоп) is False       # чистоты не заявляет
    стенд = _СтендЦикла(mod, {"state": стоп, "error": None,
                              "exchange_summary": {}})
    # броска нет: поле `error` пусто — значит служба не ломалась
    with pytest.raises(AssertionError) as дошло:
        mod.Runner.stage_cycle(стенд)
    assert "папка прогона" in str(дошло.value)

    block = _cycle_block(mod, tmp_path / "exchange", [], state=стоп)
    assert block["clean"] is False and block["blockers_n"] == 0
    исход, код, слова = mod.stage_outcome(_state(block), "cycle")
    assert исход == "остановлен человеком" == rr.OUTCOME_HUMAN
    assert код == "stopped-in-panel"
    assert стоп in слова

    stages = {"cycle": {"tag": "cycle", "verdict": "NOT_EQUIVALENT"},
              "cycle_status": block}
    record = _прогон(mod, stages, ["cycle"])
    запись = _этап(record, "cycle")
    assert запись["outcome"] == "остановлен человеком" == rr.OUTCOME_HUMAN
    assert запись["reason_code"] == "stopped-in-panel"
    assert record["results"]["state"] == "остановлен человеком" \
        == rr.STATE_HUMAN_STOP == rr.state_from_journal(record)
    # ожидания набора краснеют и на остановке человеком
    беда = mod.cycle_clean_problems(stages)
    assert len(беда) == 1 and "stopped-in-panel" in беда[0]


def test_остановка_человеком_старше_отказа_реестра_но_не_сбоя(tmp_path):
    """ПОРЯДОК ЯРУСОВ, названный в `stage_outcome`: настоящий сбой
    службы (5xx) старше всего; остановка человеком — старше смыслового
    суда закона реестра; смысловой суд — старше «успеха».

    Зелёного перестановка не прибавляет ни в одном случае: «остановлен
    человеком» старше «остановлен по правилу» и в словаре итога
    (`rr.state_from_journal`), и в ожиданиях набора (краснеют оба)."""
    mod = _runner()
    # остановка человеком ПРИ нечитаемом реестре — всё равно остановка
    block = _cycle_block(mod, tmp_path / "exchange", [],
                         state=HUMAN_STOP_STATE, broken=True)
    assert block["registry_refused"] == 1 and block["registry_refusal"]
    исход, код, _ = mod.stage_outcome(_state(block), "cycle")
    assert (исход, код) == (rr.OUTCOME_HUMAN, "stopped-in-panel")
    # ...и при живом блокирующем — тоже
    грязный = _cycle_block(mod, tmp_path / "блокер",
                           [_finding("B1", "открыто", severity="БЛОКИРУЮЩЕЕ")],
                           state=HUMAN_STOP_STATE)
    assert грязный["blockers_n"] == 1
    assert mod.stage_outcome(_state(грязный), "cycle")[:2] == (
        rr.OUTCOME_HUMAN, "stopped-in-panel")
    # а настоящий сбой службы остановку перекрывает: 5xx старше
    сбой = mod.stage_outcome(
        {"stages": {"cycle_status": dict(block, status=503,
                                         error="служба недоступна")}},
        "cycle")
    assert сбой[:2] == (rr.OUTCOME_FAILURE, "http-503")
    # словарь записи: остановка человеком старше остановки по правилу
    record = rr.new_record(_declaration(["cycle", "answers"]))
    стенд = _Стенд(mod, record, {})
    mod.Runner.record_stage(стенд, "cycle", rr.now_iso(), rr.now_iso(),
                            rr.OUTCOME_HUMAN, "stopped-in-panel", "стоп")
    mod.Runner.record_stage(стенд, "answers", rr.now_iso(), rr.now_iso(),
                            rr.OUTCOME_RULE, "cycle-incomplete", "не заявлен")
    mod.Runner.finish_record(стенд)
    assert record["results"]["state"] == rr.STATE_HUMAN_STOP


def test_остановка_человеком_доходит_и_до_пересуда_после_ответов(tmp_path):
    """Один закон на оба места: пересуд цикла после ответов берёт
    ЗАЯВКУ У САМОГО ЦИКЛА, и остановленный человеком цикл остаётся
    остановленным, сколько бы кругов ответов за ним ни прошло — даже
    когда реестр к тому времени чист.

    Иначе прогон, остановленный кнопкой, объявлялся бы «завершённым» с
    чистыми ожиданиями набора: ровно та подмена, которую запретило
    замечание С2 второго ревью, только другим состоянием."""
    mod = _runner()
    exd = tmp_path / "exchange"
    было = _cycle_block(mod, exd, [], state=HUMAN_STOP_STATE)
    стало = mod.cycle_status_of(exd, было["state"])   # заявка у цикла
    assert стало["blockers_n"] == 0 and стало["clean"] is False
    stages = {"cycle": {"tag": "cycle", "verdict": "NOT_EQUIVALENT"},
              "cycle_status": было,
              "answers": {"tag": "answers", "verdict": "EQUIVALENT",
                          "cycle_status": стало}}
    record = _прогон(mod, stages, ["cycle", "answers"])
    for stage in ("cycle", "answers"):
        запись = _этап(record, stage)
        assert запись["outcome"] == rr.OUTCOME_HUMAN, stage
        assert запись["reason_code"] == "stopped-in-panel", stage
    assert record["results"]["state"] == rr.STATE_HUMAN_STOP
    беда = mod.cycle_clean_problems(stages)
    assert len(беда) == 1 and "stopped-in-panel" in беда[0]
    assert "пересуд цикла после ответов" in беда[0]


def test_признак_остановки_берётся_у_закона_а_не_литералом():
    """Своего перечня состояний ПРОГОНА раннер не держит и здесь:
    «остановлен человеком» он узнаёт вызовом предиката закона
    (`cdpm.exchange.stopped_by_human`), а семейства «остановлено:
    <стадия>» литералом в его коде нет вовсе — только в документации.

    Сторож судит исходником: сменится семейство у панели — раннер не
    заметит перемены, потому что судит не словом, а законом."""
    import ast
    дерево = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    вызовы = [ast.unparse(n.func) for n in ast.walk(дерево)
              if isinstance(n, ast.Call)]
    assert any(c.endswith(".stopped_by_human") for c in вызовы), \
        "признак остановки человеком берётся не у закона: " + repr(вызовы[:20])
    документация = {id(n.value) for n in ast.walk(дерево)
                    if isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)}
    строки = [n.value for n in ast.walk(дерево)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)
              and id(n) not in документация]
    for литерал in строки:
        assert not литерал.startswith(ex.STOPPED_BY_HUMAN_PREFIX), литерал


# ── устойчивость форм блока (Н1, Н2) ──────────────────────────────────

def test_пустой_блок_пересуда_красный_цикл_не_перекрывает(tmp_path):
    """Замечание Н1: блок пересуда считается НАЛИЧНЫМ только когда он и
    вправду пересуд — в нём есть признак чистоты (`clean`) либо слова
    отказа (`registry_refusal`). Пустой словарь и сводка стадии ответов
    без пересуда красный блок цикла не перекрывают."""
    mod = _runner()
    красный = _cycle_block(mod, tmp_path / "exchange", [],
                           state=INCOMPLETE_STATE)
    for сводка in ({"cycle_status": {}},
                   {"verdict": "EQUIVALENT"},
                   {"cycle_status": {"live_n": 0, "blockers_n": 0}},
                   {"cycle_status": "не словарь"}):
        беда = mod.cycle_clean_problems({"cycle_status": красный,
                                         "answers": сводка})
        assert len(беда) == 1 and "cycle-incomplete" in беда[0], сводка
        assert "стадия cycle" in беда[0], сводка
    # а настоящий пересуд — перекрывает
    чистый = _cycle_block(mod, tmp_path / "чисто", [], state=CLEAN_STATE)
    assert mod.cycle_clean_problems(
        {"cycle_status": красный,
         "answers": {"cycle_status": чистый}}) == []


def test_отказ_реестра_судится_и_счётчиком(tmp_path):
    """Замечание Н2: отказ судится ДВУМЯ свидетельствами, как и находки.
    Строка `registry_refusal` потерялась (блок собран не раннером,
    старая запись), а счётчик `registry_refused` = 1 — это всё равно
    отказ, и молчаливым «успехом» он не становится."""
    mod = _runner()
    исход, код, слова = mod.cycle_outcome({"registry_refused": 1})
    assert исход == "отказ по правилу" == rr.OUTCOME_RULE
    assert код == "registry-refusal"
    assert "отказ без слов" in слова
    # даже при заявленной чистоте: отказ старше заявки
    assert mod.cycle_outcome({"registry_refused": 1,
                              "clean": True})[1] == "registry-refusal"
    # булево за счётчик не принимается — `True == 1` не отказ
    assert mod.cycle_outcome({"registry_refused": True, "clean": True}) is None
    assert mod.cycle_outcome({"registry_refused": 0, "clean": True}) is None
    # а со словами отказ называется словами закона, не «без слов»
    block = _cycle_block(mod, tmp_path / "exchange", [], broken=True)
    assert "отказ без слов" not in mod.cycle_outcome(block)[2]


def test_литерал_состояния_ответов_один_на_весь_раннер():
    """Замечание Н5: строка «ответы применены» была в раннере в двух
    местах (кортеж заявок и сравнение круга ответов). Кортеж снят (С2),
    осталось одно употребление — именованная константа модуля; сам
    литерал в коде встречается ровно один раз, в её объявлении."""
    mod = _runner()
    исходник = RUNNER_PATH.read_text(encoding="utf-8")
    assert исходник.count('"ответы применены"') == 1
    assert mod.ANSWERS_APPLIED_STATE == "ответы применены"
    assert isinstance(mod.OUTCOME_CLAIMED_STATE, str)
