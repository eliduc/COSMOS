# -*- coding: utf-8 -*-
"""A07-раннер: ЗАКОН ЖИВЫХ НАХОДОК у потребителя.

Первоисточник требования — контракт П0 §1.5 «панель, одиночный цикл,
сквозной цикл и раннер H0 читают одну функцию»; вердикты аудитора по
нему — VERDICT-A07-ef0ac6f9 (обещание не зачтено) и действующий
VERDICT-A07-d9343ead.

Раннер наборов H0 (`open-bench/polyaris-h0/run_local_pipeline.py`) до
этой правки держал СОБСТВЕННЫЙ перечень живых состояний реестра
(«открыто», «патч отбракован», «тупик»): мягче закона (не знал
«отклонено кодом» и «не разрешено арбитром») и одновременно строже
панели (краснел на любой живой находке, включая редакционную). Вердикт
A07 обещание единого закона за выполненное не зачёл.

Охраняемое:

- `cycle_status_of(exchange_dir, state)` даёт живые находки и
  блокирующие РОВНО те же, что `cdpm.exchange.live_findings` и
  `cdpm.exchange.registry_blockers` над реестром ОДНОГО загрузчика
  `cdpm.exchange.load_registry` (A07-F4) — потребитель согласован с
  законом и с панелью (`cdpm.webui._live_findings`,
  `cdpm.webui._state_by_registry`), не строже и не мягче;
- `clean` ложно при ЛЮБОМ блокирующем, даже когда панель назвала
  прогон «сквозной цикл: чисто по Б/К», и истинно при пустых
  блокирующих и чистом состоянии; живые НЕблокирующие (серьёзные,
  редакционные) чистоту не отменяют — панель кладёт их в бэклог тем же
  кругом, которым объявляет «чисто по Б/К»;
- унаследованная техническая отбраковка замком кода (A07-F3:
  «отклонено кодом» с проставленным closed_round) — ЖИВАЯ: при Б/К она
  блокирует, старый номер раунда сохранён в legacy_code_closure_round;
  настоящие закрытия патчем и решения арбитра не переоткрываются;
- блок `cycle_status` несёт `live_findings`, `blockers` и
  `registry_schema` — версию так, КАК ЕЁ ОТДАЛ ЗАГРУЗЧИК;
- отказ загрузчика (чужая версия, неполная форма `registry/2`, негодное
  поле `findings`, нечитаемый JSON) раннер называет ЕГО СЛОВАМИ и цикл
  чистым не считает (fail-closed); ветка отказа ОДНА — ошибка
  ввода-вывода и негодный аргумент папки обмена закрывают раннера теми
  же словами, а не роняют стадию;
- ОБЯЗАН ЛИ ФАЙЛ РЕЕСТРА БЫТЬ, решает закон, а не раннер (A07-R5,
  ведомость CDPM-122): `load_registry(path, expect_file=
  registry_expected(path.parent, state))` — одна строка, обе половины
  закона. Исполненный цикл без файла (слово состояния ЛИБО вердикт
  цикла в папке обмена) — исчезнувший носитель и отказ словами, а не
  «новый прогон» и не доказательство чистоты; ни слова, ни вердикта —
  реестра законно нет, и читается пустой;
- в исходнике раннера нет ни своего перечня состояний находки (вне
  комментариев и строк документации), ни своего чтения реестра.

ВСЕ ожидания выводятся из ФАКТИЧЕСКОГО поведения `cdpm.exchange` на
голове закона: тест зовёт сами функции закона и сверяет с ними раннера,
а не сличает его со своими константами. Сменится закон — красным станет
пара «закон ↔ потребитель», и её надлежит читать как смену закона.
"""
import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cdpm import exchange as ex                                   # noqa: E402

RUNNER_PATH = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
#: Состояние прогона, которым панель объявляет чистоту (webui, сквозной
#: цикл): «чисто по Б/К» — суждение по ТЕМ ЖЕ registry_blockers.
CLEAN_STATE = "сквозной цикл: чисто по Б/К (кругов: 2)"
DIRTY_STATE = "аудит остановлен: лимит"
#: Состояния ПРОГОНА ДО ЦИКЛА — слова панели (`cdpm/webui.py`), а не
#: перечень стенда: «новый» (прогон только заведён), «извлечено» (свод
#: снят, обменов ещё не было), «сквозной цикл идёт» (цикл пошёл и
#: реестра пока не написал). Классифицирует их ЗАКОН
#: (`ex.outcome_claimed` — ложь на всех трёх), стенд лишь называет
#: носители слов; принадлежность каждой строки панели проверяется
#: тестом положительного контроля ниже.
PRE_CYCLE_STATES = ("новый", "извлечено", "сквозной цикл идёт")


def _runner():
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0",
                                                  RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_local_pipeline_h0"] = mod
    spec.loader.exec_module(mod)
    return mod


def _finding(fid, state, severity="КРИТИЧЕСКОЕ", key=None,
             closed_round=None, **extra):
    f = {"id": fid, "key": key or f"k-{fid}", "severity": severity,
         "state": state, "opened_round": 1, "closed_round": closed_round}
    f.update(extra)
    return f


def _registry_all_states() -> dict:
    """Синтетический реестр: по одной БЛОКИРУЮЩЕЙ находке на КАЖДОЕ
    состояние из LIVE_STATES, живые неблокирующие (серьёзная и
    редакционная), настоящее закрытие патчем, унаследованная отбраковка
    замком кода (A07-F3 — по закону этой головы ЖИВАЯ) и пара находок
    ключа, решённого арбитром (решение гасит эхо спора)."""
    findings = [_finding(f"L{i}", state)
                for i, state in enumerate(ex.LIVE_STATES, start=1)]
    findings.append(_finding("S1", "открыто", severity="СЕРЬЁЗНОЕ"))
    findings.append(_finding("E1", "тупик", severity="РЕДАКЦИОННОЕ"))
    findings.append(_finding("C1", "закрыто", closed_round=3))
    findings.append(_finding("C2", "отклонено кодом", closed_round=4,
                             closed_by=ex.LEGACY_CODE_CLOSURE))
    findings.append(_finding("A1", "открыто", key="k-arb"))
    findings.append(_finding("A2", "арбитр: обработчик прав", key="k-arb"))
    return {"findings": findings}


def _rows(findings) -> list:
    """Строка находки так, как её называет раннер: (id, серьёзность,
    состояние)."""
    return [(f.get("id"), f.get("severity"), f.get("state"))
            for f in findings]


def _write(exd: Path, registry: dict, schema=ex.REGISTRY_SCHEMA) -> Path:
    """Реестр на диск; возвращает путь. `registry/2` пишет ПРОДУКТОВЫЙ
    писатель (он же судит форму состояний); прочие носители — старую
    форму без поля версии, чужую объявленную версию, неполную новую
    форму — продуктовый писатель не пишет вовсе (A07-F4), их оставляют
    прежние ревизии и чужие руки, поэтому они кладутся как есть."""
    exd.mkdir(parents=True, exist_ok=True)
    path = exd / "registry.json"
    if schema == ex.REGISTRY_SCHEMA:
        ex._dump_registry(path, registry)
    else:
        registry.pop("schema_version", None)
        if schema is not None:
            registry["schema_version"] = schema
        path.write_text(json.dumps(registry, ensure_ascii=False),
                        encoding="utf-8")
    return path


def _law(path: Path):
    """Закон над тем же носителем и ТЕМ ЖЕ загрузчиком: (живые,
    блокирующие) строками. Ожидания теста берутся отсюда, а не из
    собственных перечней."""
    reg = ex.load_registry(path)
    return _rows(ex.live_findings(reg)), _rows(ex.registry_blockers(reg))


def test_потребитель_читает_ровно_закон_exchange(tmp_path):
    """Согласованность потребителя: живые находки и блокирующие раннера
    РОВНО равны выводу `live_findings` / `registry_blockers` над
    реестром `load_registry` — одного загрузчика продукта."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, _registry_all_states())
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    live, blockers = _law(path)
    assert cyc["live_findings"] == live
    assert cyc["blockers"] == blockers
    # Поимённо: живы все шесть состояний LIVE_STATES, неблокирующие и
    # унаследованная отбраковка замком кода (A07-F3); настоящее закрытие
    # патчем и эхо решённого арбитром ключа — нет.
    assert [r[0] for r in cyc["live_findings"]] == [
        f"L{i}" for i in range(1, len(ex.LIVE_STATES) + 1)] + ["S1", "E1",
                                                               "C2"]
    assert [r[0] for r in cyc["blockers"]] == [
        f"L{i}" for i in range(1, len(ex.LIVE_STATES) + 1)] + ["C2"]
    assert "registry_refusal" not in cyc


def test_потребитель_согласован_с_панелью(tmp_path):
    """Тот же предикат, что у панели (`webui._live_findings`): раннер не
    строже и не мягче экрана прогона."""
    from cdpm import webui
    mod = _runner()
    run_dir = tmp_path / "run-0001"
    _write(run_dir / "exchange", _registry_all_states())
    cyc = mod.cycle_status_of(run_dir / "exchange", CLEAN_STATE)
    assert cyc["live_findings"] == _rows(webui._live_findings(run_dir))


def test_cycle_status_несёт_блокирующих_и_версию_схемы(tmp_path):
    """Блок записи: живые, блокирующие и версия схемы реестра."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, _registry_all_states())
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert set(cyc) >= {"state", "clean", "live_findings", "blockers",
                        "registry_schema"}
    # Версия — из ОДНОГО места: то, что отдал загрузчик.
    assert cyc["registry_schema"] == ex.load_registry(path).get(
        "schema_version") == ex.REGISTRY_SCHEMA
    assert cyc["state"] == CLEAN_STATE


@pytest.mark.parametrize("state", ex.LIVE_STATES)
@pytest.mark.parametrize("severity", ex.BLOCKING_SEVERITIES)
def test_чисто_по_БК_панели_не_спасает_при_блокере(tmp_path, state, severity):
    """`clean` ложно при ЛЮБОМ блокирующем — даже когда панель назвала
    прогон чистым по Б/К (контрпример CONS-02: «успех» на молчании
    аудитора при отбракованном патче)."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, {"findings": [_finding("B1", state,
                                              severity=severity)]})
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    # Композиция A07 × A09 на общей голове: единственное из живых
    # состояний, которое ЕДИНЫЙ ЗАГРУЗЧИК доводит при чтении, — это
    # унаследованная техническая отбраковка: она дозревает до тупика с
    # кодовой причиной (§12.8) и получает путь к арбитру. Остальные
    # живые состояния читаются тем же словом, каким записаны. Предмет
    # пробы — «clean ложно при ЛЮБОМ блокирующем» — не изменился: ни
    # одно состояние из перечня не перестало блокировать.
    прочтётся = "тупик" if state == ex.CODE_DEADLOCK_KIND else state
    assert _law(path)[1] == [("B1", severity, прочтётся)]
    assert cyc["blockers"] == [("B1", severity, прочтётся)]
    assert cyc["clean"] is False


def test_чисто_при_пустых_блокирующих(tmp_path):
    """Истинно при пустых блокирующих И чистом состоянии прогона;
    грязное состояние при пустом реестре чистым не становится."""
    mod = _runner()
    exd = tmp_path / "exchange"
    _write(exd, {"findings": [_finding("C1", "закрыто", closed_round=2)]})
    assert mod.cycle_status_of(exd, CLEAN_STATE)["clean"] is True
    assert mod.cycle_status_of(exd, DIRTY_STATE)["clean"] is False


def test_живые_неблокирующие_чистоты_не_отменяют(tmp_path):
    """Серьёзная и редакционная живые находки — предупреждение, не
    краснота: панель кладёт их в бэклог тем же кругом, которым
    объявляет «чисто по Б/К»."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, {"findings": [
        _finding("S1", "открыто", severity="СЕРЬЁЗНОЕ"),
        _finding("E1", "патч отбракован", severity="РЕДАКЦИОННОЕ")]})
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert _law(path)[1] == []
    assert cyc["blockers"] == []
    assert [r[0] for r in cyc["live_findings"]] == ["S1", "E1"]
    assert cyc["clean"] is True


def test_ключ_решённый_арбитром_не_блокирует(tmp_path):
    """Решение арбитра гасит ВСЕ находки ключа — раннер судит тем же
    законом, своего толкования не имеет."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, {"findings": [
        _finding("A1", "тупик", key="k-arb"),
        _finding("A2", "арбитр: аудитор прав", key="k-arb")]})
    assert _law(path) == ([], [])
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["live_findings"] == [] and cyc["clean"] is True


@pytest.mark.parametrize("closed_by", [ex.LEGACY_CODE_CLOSURE, None])
@pytest.mark.parametrize("severity,blocks", [("КРИТИЧЕСКОЕ", True),
                                             ("БЛОКИРУЮЩЕЕ", True),
                                             ("РЕДАКЦИОННОЕ", False)])
def test_унаследованная_отбраковка_замком_кода_живая(
        tmp_path, closed_by, severity, blocks):
    """A07-F3: запись «отклонено кодом» с проставленным closed_round —
    техническая отбраковка прежнего писателя, а не исполненный патч и
    не решение арбитра. Закон возвращает её в ЖИВЫЕ, старый номер
    раунда остаётся в legacy_code_closure_round — раннер согласен, и
    при Б/К такой прогон НЕ чист, сколько бы панель ни объявляла
    «чисто по Б/К»."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, {"findings": [
        _finding("G1", "отклонено кодом", severity=severity,
                 closed_round=4, closed_by=closed_by)]})
    live, blockers = _law(path)
    # КОМПОЗИЦИЯ A07 × A09 на общей голове (решение аудитора
    # DECISION-A07-A09-INTEGRATION-LAW от 12.09.2026). Прежнее ожидание
    # «состояние остаётся „отклонено кодом“» описывало голову ДО
    # подключения дозревания §12.8: единый загрузчик теперь доводит
    # унаследованную техническую отбраковку до ТУПИКА с кодовой
    # причиной — так у вчерашней записи появляется путь к арбитру,
    # которого прежде не было. Предмет пробы (запись ЖИВАЯ, при Б/К
    # прогон НЕ чист, история и байты на диске целы) не изменился ни в
    # одном утверждении; изменилось одно слово состояния, а ВИД назван
    # отдельным полем deadlock_kind — сведений не теряется.
    # Правка внесена стороной CDPM в файл стороны COSMOS и сообщена ей
    # прямо: закон один на обе стороны, расходиться ему нельзя.
    row = ("G1", severity, "тупик")
    assert live == [row]                      # закон: живая
    assert blockers == ([row] if blocks else [])
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["live_findings"] == live
    assert cyc["blockers"] == blockers
    assert cyc["clean"] is (not blocks)
    # История не потеряна: номер прежнего «закрытия» сохранён законом.
    kept = ex.load_registry(path)["findings"][0]
    assert kept["legacy_code_closure_round"] == 4
    assert kept["closed_round"] is None
    # Приобретение композиции, ради которого слово состояния и менялось:
    # вид тупика назван, и по нему запись попадает в перечень арбитража.
    assert kept["deadlock_kind"] == ex.CODE_DEADLOCK_KIND
    # Носитель на диске чтением НЕ переписан.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["findings"][0]["closed_round"] == 4


def test_закрытия_патчем_и_решения_арбитра_не_переоткрываются(tmp_path):
    """Миграция чтения возвращает в живые ТОЛЬКО техническую
    отбраковку: настоящее закрытие по исполненному патчу («закрыто» с
    closed_round) и решение арбитра остаются закрытыми — переоткрывать
    их нечем и незачем."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, {"findings": [
        _finding("P1", "закрыто", closed_round=2),
        _finding("P2", "исправлено", closed_round=3),
        _finding("R1", "арбитр: в анкету", key="k-r", closed_round=5)]})
    assert _law(path) == ([], [])
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["live_findings"] == [] and cyc["blockers"] == []
    assert cyc["clean"] is True
    assert "legacy_code_closure_round" not in json.dumps(
        ex.load_registry(path), ensure_ascii=False)


def test_старая_форма_без_версии_читается_загрузчиком(tmp_path):
    """Реестр БЕЗ поля версии (старая форма registry/1): раннер её не
    «исправляет» и не гадает — как её читает загрузчик, так и он.
    Ожидание выведено из факта: отказа нет, находки те же,
    `registry_schema` — то, что отдал загрузчик (поля нет, значит
    None)."""
    mod = _runner()
    exd = tmp_path / "exchange"
    path = _write(exd, _registry_all_states(), schema=None)
    live, blockers = _law(path)
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["registry_schema"] == ex.load_registry(path).get(
        "schema_version") is None
    assert cyc["live_findings"] == live
    assert cyc["blockers"] == blockers
    assert "registry_refusal" not in cyc
    assert cyc["clean"] is False        # блокирующие живы


@pytest.mark.parametrize("raw,schema", [
    (None, "registry/1"),               # объявленная старая версия
    (None, "registry/9"),               # чужая версия
    ({}, ex.REGISTRY_SCHEMA),           # неполная новая форма: нет findings
    ({"findings": "abc"}, None),        # поле findings не список
    ({"findings": ["a", 1]}, None),     # записи находок не объекты
])
def test_отказ_загрузчика_раннер_называет_его_словами(tmp_path, raw, schema):
    """Откажет ЗАКОН (`load_registry` — версия, неполная форма, негодное
    поле `findings`) — раннер переносит ЕГО СЛОВА в запись и считает
    цикл не чистым (fail-closed), а не молча зелёным и не «починенным»
    по-своему. Слова сверяются с тем, что сказал сам загрузчик."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True, exist_ok=True)
    body = dict(raw if raw is not None else _registry_all_states())
    if schema is not None:
        body["schema_version"] = schema
    path = exd / "registry.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(path)
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["clean"] is False
    assert "раннеру не читаем" in cyc["registry_refusal"]
    assert str(err.value) in cyc["registry_refusal"]
    assert cyc["live_findings"] == [] and cyc["blockers"] == []
    assert cyc["registry_schema"] is None


def test_битый_реестр_не_роняет_стадию(tmp_path):
    """Нечитаемый файл реестра — тоже отказ словами загрузчика и не
    чистый цикл."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    (exd / "registry.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json")
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["clean"] is False
    assert str(err.value) in cyc["registry_refusal"]


def test_ошибка_ввода_вывода_тоже_отказ_словами(tmp_path, monkeypatch):
    """Ветка отказа ОДНА: не только отказ формы, но и любая ошибка
    чтения носителя (`OSError` — файл занят, права, сбой диска)
    закрывает раннера СЛОВАМИ, а не роняет стадию и не даёт зелёного
    (замечание Н2 неавторского ревью: прежде эта ветка держалась
    только чтением кода).

    ЗАКОН НА ЭТОЙ ГОЛОВЕ СМЕНИЛСЯ (A07-R3, `cdpm/exchange.py`): сырая
    `OSError` до потребителя больше не долетает — загрузчик называет
    свою беду СВОИМИ словами (`RegistryUnavailable`: «реестр временно
    недоступен»), а подробности операционной системы намеренно
    оставляет ПРИЧИНОЙ исключения. Поэтому ожидание берётся у ЗАКОНА
    на том же носителе, а не у слов подменённой ошибки: раннер обязан
    перенести ровно то, что сказал закон, и ни слова своего не
    прибавить.

    Подмена — кроссплатформенная (`pathlib.Path.read_text` изнутри
    загрузчика); настоящий дескриптор Windows с запретом совместного
    доступа даёт то же самое, но зависит от машины и тут не нужен."""
    mod = _runner()
    exd = tmp_path / "exchange"
    _write(exd, _registry_all_states())
    real_read_text = Path.read_text
    beda = OSError(5, "носитель не читается")

    def _bad_read_text(self, *a, **kw):
        if self.name == "registry.json":
            raise beda
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _bad_read_text)
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json")
    # сырая ошибка носителя осталась ПРИЧИНОЙ, а не словами закона
    assert err.value.__cause__ is beda
    assert str(beda) not in str(err.value)
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["clean"] is False
    assert "реестр раннеру не читаем" in cyc["registry_refusal"]
    assert str(err.value) in cyc["registry_refusal"]
    assert cyc["live_findings"] == [] and cyc["blockers"] == []
    assert cyc["registry_schema"] is None


@pytest.mark.parametrize("negodnyi", [None, 42, object()])
def test_негодный_аргумент_отказ_словами_а_не_падение(negodnyi):
    """Папка обмена не путём (None, число, чужой объект) — отказ
    СЛОВАМИ и не чистый цикл, а не падение стадии `TypeError`
    (замечание Н1 неавторского ревью: путь реестра и импорт закона
    строятся внутри `try`). Из стадии такой аргумент недостижим
    (`stage_cycle` даёт всегда `Path`), но функция чистая и зовётся
    не только оттуда."""
    mod = _runner()
    cyc = mod.cycle_status_of(negodnyi, CLEAN_STATE)
    assert cyc["clean"] is False
    assert "реестр раннеру не читаем" in cyc["registry_refusal"]
    assert cyc["registry_refused"] == 1
    assert cyc["live_findings"] == [] and cyc["blockers"] == []


def test_реестра_нет_и_следа_цикла_нет_читается_пустой(tmp_path):
    """ЗАКОН A07-R5, ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: ни слово состояния, ни
    каталог обмена исполненного цикла файла не требуют — реестра
    законно нет (цикл до обменов не дошёл), загрузчик читает пустой,
    живых находок нет, отказа нет. Ровно так было и до правки, и
    действительно новый прогон этим держится.

    Класс состояния называет ЗАКОН (`outcome_claimed` — ложь), а не
    стенд; сами строки — панели, и её исходником они проверены."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    панель = (ROOT / "cdpm" / "webui.py").read_text(encoding="utf-8")
    for состояние in PRE_CYCLE_STATES:
        assert состояние in панель, состояние
        assert ex.outcome_claimed(состояние) is False, состояние
        assert ex.cycle_ran(exd) is False
        assert ex.registry_expected(exd, состояние) is False, состояние
        assert ex.load_registry(exd / "registry.json") == {}
        cyc = mod.cycle_status_of(exd, состояние)
        assert "registry_refusal" not in cyc, состояние
        assert cyc["registry_refused"] == 0
        assert cyc["registry_schema"] is None
        assert cyc["live_findings"] == [] and cyc["blockers"] == []
        # чистоты такое состояние не заявляет — и цикл не чист
        assert cyc["clean"] is False, состояние


@pytest.mark.parametrize("состояние", [CLEAN_STATE, DIRTY_STATE])
def test_заявленный_исход_без_файла_реестра_отказ(tmp_path, состояние):
    """ЗАКОН A07-R5, ПОЛОВИНА «СЛОВО» (`outcome_claimed`): состояние
    ЗАЯВИЛО исход цикла, а файла реестра нет — это не новый прогон, а
    ИСЧЕЗНУВШИЙ носитель: реестр пишет каждый круг цикла и
    переписывает арбитраж.

    Прежде такой прогон читался пустым реестром, и исчезновение файла
    работало доказательством чистоты: живых нет → «сквозной цикл:
    чисто по Б/К» → зелёная запись. Теперь загрузчик отказывает
    своими словами (`registry_expected` → `expect_file=True`), раннер
    их переносит, и цикл чистым не считается."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    assert not (exd / "registry.json").exists()
    assert ex.outcome_claimed(состояние) is True, состояние
    assert ex.registry_expected(exd, состояние) is True
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json",
                         expect_file=ex.registry_expected(exd, состояние))
    cyc = mod.cycle_status_of(exd, состояние)
    assert cyc["clean"] is False
    assert cyc["registry_refused"] == 1
    assert str(err.value) in cyc["registry_refusal"]
    assert cyc["live_findings"] == [] and cyc["blockers"] == []
    assert cyc["registry_schema"] is None


def test_след_цикла_без_файла_реестра_отказ(tmp_path):
    """ЗАКОН A07-R5, ПОЛОВИНА «КАТАЛОГ» (`cycle_ran`): слово состояния
    об исходе цикла молчит, но в папке обмена лежит ВЕРДИКТ ЦИКЛА —
    значит цикл отработал, и реестр писался каждый его круг. Файла
    нет — носитель исчез, и читать «пусто» здесь нельзя.

    Одной первой половины закона было бы мало: прогон живёт дальше
    цикла (стадия ответов ставит своё состояние), а каталог помнит
    исполненный цикл, сколько бы стадий за ним ни прошло. Обратное
    тоже проверено: убрали вердикт — требования нет, и тот же прогон
    снова читается пустым реестром."""
    mod = _runner()
    exd = tmp_path / "exchange"
    exd.mkdir(parents=True)
    состояние = mod.ANSWERS_APPLIED_STATE
    assert ex.outcome_claimed(состояние) is False       # слово молчит
    assert ex.registry_expected(exd, состояние) is False
    вердикт = exd / ex.CYCLE_VERDICT_FILE
    вердикт.write_text(json.dumps({"verdict": "успех"}, ensure_ascii=False),
                       encoding="utf-8")
    assert ex.cycle_ran(exd) is True                    # каталог говорит
    assert ex.registry_expected(exd, состояние) is True
    with pytest.raises(ex.ExchangeError) as err:
        ex.load_registry(exd / "registry.json",
                         expect_file=ex.registry_expected(exd, состояние))
    cyc = mod.cycle_status_of(exd, состояние)
    assert cyc["clean"] is False
    assert cyc["registry_refused"] == 1
    assert str(err.value) in cyc["registry_refusal"]
    # и без вердикта в папке требования нет — тот же прогон читается
    вердикт.unlink()
    снова = mod.cycle_status_of(exd, состояние)
    assert "registry_refusal" not in снова and снова["registry_refused"] == 0


@pytest.mark.parametrize("findings,rejudged,clean", [
    ([], False, True),
    ([_finding("B1", "открыто")], True, False),
    ([_finding("G1", "отклонено кодом", closed_round=4,
               closed_by=ex.LEGACY_CODE_CLOSURE)], True, False),
])
def test_раннер_согласен_с_пересудом_панели(tmp_path, findings, rejudged,
                                            clean):
    """Панель этой головы ПЕРЕСУЖИВАЕТ записанное «сквозной цикл: чисто
    по Б/К» реестром (`webui._state_by_registry`): старый прогон с
    живым блокером — в том числе с унаследованной отбраковкой замком
    кода — показывается как «не закрыто (по реестру)». Раннер считает
    `clean` = состояние И нет блокирующих, и обязан быть с этим
    СОГЛАСЕН: где панель отняла зелёное, там и цикл не чист — и на
    записанном состоянии, и на пересуженном."""
    from cdpm import webui
    mod = _runner()
    run_dir = tmp_path / "run-0001"
    exd = run_dir / "exchange"
    _write(exd, {"findings": findings})
    shown = webui._state_by_registry(run_dir, CLEAN_STATE)
    assert (shown != CLEAN_STATE) is rejudged
    if rejudged:
        assert ex.VERDICT_NOT_CLOSED in shown
    # Записанное состояние: раннер судит реестром, а не словом.
    assert mod.cycle_status_of(exd, CLEAN_STATE)["clean"] is clean
    # Пересуженное состояние панели раннер тоже чистым не считает.
    assert mod.cycle_status_of(exd, shown)["clean"] is clean


def test_панель_и_раннер_одинаково_запираются_на_негодной_форме(tmp_path):
    """Форма реестра негодна — обе стороны fail-closed: панель снимает
    записанную чистоту («не закрыто (по реестру)»), раннер называет
    отказ и цикл чистым не считает. Зелёного не остаётся ни у кого."""
    from cdpm import webui
    mod = _runner()
    run_dir = tmp_path / "run-0001"
    exd = run_dir / "exchange"
    exd.mkdir(parents=True)
    (exd / "registry.json").write_text(
        json.dumps({"schema_version": "registry/9", "findings": []}),
        encoding="utf-8")
    shown = webui._state_by_registry(run_dir, CLEAN_STATE)
    assert shown != CLEAN_STATE and ex.VERDICT_NOT_CLOSED in shown
    cyc = mod.cycle_status_of(exd, CLEAN_STATE)
    assert cyc["clean"] is False and cyc["registry_refusal"]


def _code_strings(path: Path) -> list:
    """Строковые литералы исходника ВНЕ документации: docstring и
    голая строка-выражение отбрасываются (комментарии в дерево не
    попадают вовсе)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_в_раннере_нет_собственного_перечня_состояний():
    """Своего перечня состояний у раннера нет: слова состояний живут
    только в комментариях и документации, а не в условиях кода.

    Сторож судит РАВЕНСТВОМ литерала слову состояния, а не вхождением
    подстроки (замечание Н5 неавторского ревью): законная строка
    журнала, внутри которой случайно стоит слово состояния, красила бы
    сторож зря, а копию перечня вида `ex.LIVE_STATES[:3]` он всё равно
    не заметит. Это сторож, а не доказательство: доказательство —
    парные тесты «закон ↔ потребитель» выше."""
    strings = _code_strings(RUNNER_PATH)
    for state in ex.STATES:
        assert state not in strings, \
            f"состояние {state!r} литералом в коде раннера"
    assert "cycle_status_of" in RUNNER_PATH.read_text(encoding="utf-8")


def test_в_раннере_нет_своего_чтения_реестра():
    """Один загрузчик (A07-F4): реестр читает `exchange.load_registry`,
    а не собственный `json.loads` раннера — иначе версия и форма
    судились бы вторым, своим судом."""
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    fn = [n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "cycle_status_of"]
    assert len(fn) == 1, "cycle_status_of в раннере ровно одна"
    calls = [ast.unparse(n.func) for n in ast.walk(fn[0])
             if isinstance(n, ast.Call)]
    assert any(c.endswith("load_registry") for c in calls), calls
    assert not [c for c in calls if c.endswith("json.loads")], calls
