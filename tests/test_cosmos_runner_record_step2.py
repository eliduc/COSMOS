# -*- coding: utf-8 -*-
"""Раннер наборов H0 и запись о прогоне, шаг 2 (проект
`final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`, разделы 10 и 11).

Судятся три вещи, которых раньше не было.

  * **Объявление прогона.** Панель не создаёт прогон без объявления
    (раздел 11.1): создание несёт номер записи раннера, кто объявил,
    цель, политику ответов, план этапов, точку остановки и ожидания
    (манифест набора). Номер чеканится ДО создания прогона — он у
    головы записи, собранной сразу за паспортом.
  * **Хранитель файла записи.** Панель ведёт запись раннера (200 и тот
    же номер) — раннер в папку прогона не пишет вовсе и шлёт этапы
    ручкой дописывания (раздел 11.2). Панель записи не ведёт (404 или
    чужой номер — старая панель) — переходное правило «два летописца»:
    файл ведёт раннер, ручка не зовётся. Отказ хранителя — слова в
    журнал, прогон продолжается: запись в каталоге `--out` полна
    всегда.
  * **Ожидание `record_stitched`.** После НАЖАТИЙ COSMOS у хозяина
    записи стоят этапы COSMOS по каждой ожидаемой задаче; без нажатий
    ключ честно не судится — как `cosmos_numbers`.

Шаг 3 (разделы 3.3 и 12.4) добавил четвёртое — **итоги записи**: при
закрытии раннер кладёт в `results.comparison` сравнение с ОЖИДАНИЯМИ
набора (ровно суд стадии `package`, второго суда нет), с ПРЕДЫДУЩИМ
прогоном того же корня и с ЗОЛОТЫМ (оба — из ручки дерева панели; ручки
ещё нет — пусто, без падения) и шлёт их хозяину тем же дописыванием.
Признак золотого раннер не ставит НИКОГДА.

Договор с CDPM 11.09.2026 (волна 15) добавил пятое — **модели COSMOS в
голове**: голову чеканит хозяин записи, и до 11.09 в ней стояли только
модели CDPM — сертификатор и читатель ИДАК не попадали в неё вовсе
(находка К5). Раннер спрашивает среду COSMOS ручкой
`GET /api/run/environment` ДО чеканки и кладёт его модели в `models[]`
словом `system: "cosmos"`; не дождался — слова в `models_note`, а не
выдуманная раскладка.

Подстава панели — подменой `client`, подстава COSMOS — подменой
открывателя ссылки (`_open`): ни сети, ни служб, ни моделей.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Вымышленная база панели: адреса среды в стендах запрещены
#: (tests/test_no_environment_literals.py).
PANEL_BASE = "http://панель.invalid"
RUN = "прогон-1"


def _runner():
    p = ROOT / "open-bench" / "polyaris-h0" / "run_local_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_local_pipeline_h0", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _passport(**over) -> dict:
    """Наименьший паспорт, из которого запись уже собирается (тот же, что
    в tests/test_cosmos_runner_expect.py)."""
    sha = "1" * 64
    doc = {"schema": "cosmos-run-passport/v1",
           "kit": {"customer": "Проба-раннер",
                   "manifest": "open-bench/проба/kit-manifest.json",
                   "references": {"answer_book": {"sha256": sha}}},
           "rules_documents": [{"name": "ПРАВИЛА.docx", "sha256": sha,
                                "bytes": 10}],
           "data_documents": [],
           "catalog": {"path": "каталоги/проба", "version": "0.4.0",
                       "fingerprint": sha},
           "bindings": {"file": "vmp/bindings.json", "catalog_version": "0.4.0",
                        "catalog_fingerprint": sha},
           "agents_layout": {"roles": {}},
           "solver": {"engine": "timefold", "url": None, "build": None},
           "python": {"version": "3.14.4", "packages": [],
                      "packages_sha256": sha},
           "code": {"commit": "коммит-пробы", "fingerprint": "отпечаток-пробы"},
           "panel": {"base_url": None, "in_process": True},
           "stages": {"until": "package"}}
    doc.update(over)
    return doc


class _Keys:
    """Ключи командной строки, которые читает сборка записи."""

    def __init__(self, purpose=None, contour=None, from_stage=None,
                 until="package", cosmos_url=None):
        self.purpose = purpose
        self.contour = contour
        self.from_stage = from_stage
        self.until = until
        self.cosmos_url = cosmos_url


def _record(r, planned=("create", "package")):
    return r.build_record(_passport(), _Keys(), list(planned))


class _Resp:
    """Ответ службы: код и тело словарём."""

    def __init__(self, status: int, body=None):
        self.status_code = status
        self._body = {} if body is None else body
        self.text = json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"служба ответила {self.status_code}")


class _Panel:
    """Подстава панели CDPM: помнит вызовы и отвечает по таблице.

    `record` — ответ ручки записи (`GET …/record`), `append` — ответ
    ручки дописывания; `record` не назван — «старая панель»: 404."""

    def __init__(self, record=None, append=None, run: str = RUN, tree=None):
        self.run = run
        self.record = record if record is not None else _Resp(404, {})
        self.append = append if append is not None else _Resp(
            200, {"ok": True, "accepted": 1, "replaced": 0})
        #: Ручка дерева прогонов (шаг 3, раздел 12.2) — её делает
        #: половина CDPM; не названа — «ручки ещё нет»: 404.
        self.tree = tree if tree is not None else _Resp(404, {})
        self.calls: list = []

    def post(self, path, files=None, data=None, params=None, json=None):
        self.calls.append(("POST", path, {"files": files, "data": data,
                                          "params": params, "json": json}))
        if path == "/api/runs":
            return _Resp(200, {"run": self.run, "state": "заморожено",
                               "freeze_counters": {"files": 1, "clauses": 2}})
        if path.endswith("/record/append"):
            return self.append
        return _Resp(200, {})

    def get(self, path):
        self.calls.append(("GET", path, {}))
        if path.endswith("/record"):
            return self.record
        if path.endswith("/tree"):
            return self.tree
        return _Resp(404, {})

    def put(self, path, json=None):
        self.calls.append(("PUT", path, {"json": json}))
        return _Resp(200, {})

    def body(self, method: str, tail: str):
        """Тело последнего вызова, чей путь кончается на `tail`."""
        for m, path, kw in reversed(self.calls):
            if m == method and path.endswith(tail):
                return kw
        return None

    def count(self, method: str, tail: str) -> int:
        return sum(1 for m, path, _kw in self.calls
                   if m == method and path.endswith(tail))


def _runner_instance(r, tmp_path, panel, record=None):
    """Раннер без __init__ (так же его строят соседние стенды): приёмы
    записи не имеют права требовать поднятой панели."""
    rn = object.__new__(r.Runner)
    rn.out = Path(tmp_path) / "out"
    rn.out.mkdir(parents=True, exist_ok=True)
    rn.state_path = rn.out / "run-state.json"
    rn.state = {"stages": {}}
    rn.passport = _passport()
    rn.record = record
    rn._custodian = None
    rn.client = panel
    rn.base_url = None
    return rn


def _prepare_create(rn, tmp_path, monkeypatch):
    """Обстановка стадии create без служб: файл набора, строки jpg,
    заморозка в каталоге прогонов; сверка заморозки — заглушкой (её
    судит стенд паспорта)."""
    kit = Path(tmp_path) / "kit"
    (kit / "etalon").mkdir(parents=True, exist_ok=True)
    (kit / "etalon" / "jpg-lines.json").write_text(
        json.dumps(["абзац"], ensure_ascii=False), encoding="utf-8")
    doc = kit / "ПРАВИЛА.docx"
    doc.write_bytes(b"docx")
    rn.kit = kit
    rn.paths = [doc]
    rn.manifest = {"data_class": "synthetic", "llm_scope": "any"}
    rn.customer = "Проба-раннер"
    runs = Path(tmp_path) / "runs"
    clauses = runs / RUN / "freeze" / "clauses"
    clauses.mkdir(parents=True, exist_ok=True)
    (clauses / "S3.json").write_text(
        json.dumps({"clauses": [{"text": "абзац"}]}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setenv("CDPM_RUNS_DIR", str(runs))
    rn.check_freeze = lambda: None


def _panel_record(record: dict, journal=()) -> dict:
    """Единая запись у хозяина глазами панели: голова раннера и журнал,
    где чужие записи помечены именем системы."""
    return {"schema": record["schema"],
            "declaration": record["declaration"],
            "declaration_sha256": record["declaration_sha256"],
            "journal": list(journal),
            "results": record["results"]}


def _cosmos_entry(stage: str, substage=None) -> dict:
    return {"system": "cosmos", "stage": stage, "substage": substage,
            "outcome": "успех"}


# ── Модели COSMOS в голове записи (договор с CDPM 11.09.2026) ──────────
#: Вымышленный адрес COSMOS: адресов среды в стендах нет.
COSMOS_BASE = "http://косомос.invalid"


class _Answer:
    """Ответ ручки среды COSMOS: `urlopen` отдаёт контекст с `read`."""

    def __init__(self, body):
        self._raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Cosmos:
    """Подстава COSMOS: помнит запрошенные адреса и заголовки."""

    def __init__(self, body=None, raises=None):
        self.body = body
        self.raises = raises
        self.calls: list = []

    def __call__(self, request, timeout=None):
        self.calls.append({"url": request.full_url, "timeout": timeout,
                           "headers": dict(request.headers)})
        if self.raises is not None:
            raise self.raises
        return _Answer(self.body)


def _passport_cdpm_models() -> dict:
    """Паспорт с раскладкой ролей CDPM: наименьший паспорт стенда идёт
    без ролей, а здесь судится соседство двух систем в одной голове."""
    return _passport(agents_layout={"roles": {"extractor": {"cloud": {
        "provider": "cloud", "model": "модель-извлечения",
        "effort": "high"}}}})


def _env_answer(**over) -> dict:
    """Ответ ручки среды COSMOS по договору 11.09.2026."""
    doc = {"schema": "cosmos-run-environment/v1",
           "contour": "local",
           "llm_scope": "any",
           "code": {"cosmos_commit": "коммит-косомоса"},
           "catalog": {"name": "проба", "version": "0.4.0",
                       "fingerprint": "2" * 64, "path": "каталоги/проба"},
           "models": [{"role": "сертификатор", "model": "модель-сертификатора",
                       "provider": "anthropic", "channel": "api",
                       "effort": "high", "system": "cosmos"},
                      {"role": "читатель ИДАК", "model": "модель-читателя",
                       "provider": "ollama", "channel": "local",
                       "effort": None, "system": "cosmos",
                       "words": "слова причины голове не нужны"}]}
    doc.update(over)
    return doc


def test_модели_косомоса_ложатся_в_голову_рядом_с_моделями_cdpm():
    """Раннер — хозяин записи, и до 11.09.2026 в голове стояли только
    модели CDPM (находка К5). Теперь модели COSMOS ложатся рядом словом
    `system: "cosmos"`, а модель CDPM поля не получает вовсе; слова
    причины строки (`words`) голова отбрасывает сама — их поля в словаре
    нет."""
    r = _runner()
    record = r.build_record(_passport_cdpm_models(), _Keys(),
                            ["create", "package"], cosmos_env=_env_answer())
    rows = record["declaration"]["models"]
    assert [m["role"] for m in rows] == ["extractor", "сертификатор",
                                         "читатель ИДАК"]
    assert "system" not in rows[0]
    assert all(m["system"] == "cosmos" for m in rows[1:])
    assert all("words" not in m for m in rows)
    assert rows[1]["model"] == "модель-сертификатора"
    assert rows[1]["provider"] == "anthropic" and rows[1]["effort"] == "high"
    # Раскладка полна — слов о неполноте в голове нет.
    assert "models_note" not in record["declaration"]
    assert r.rr.validate(record) == []
    # Голова целиком уезжает в форму создания прогона: её же отпечаток
    # считает хранитель.
    form = r.declaration_form(record)
    assert json.loads(form["declaration"]) == record["declaration"]
    assert form["declaration_sha256"] == record["declaration_sha256"]


def test_косомос_не_опрошен_слова_в_голове_а_не_выдуманные_модели():
    """Спросить среду не удалось — голова несёт модели CDPM, а неполноту
    называет полем `models_note`: молчать о пропавших ролях нельзя."""
    r = _runner()
    keys, planned = _Keys(), ["create", "package"]
    quiet = r.build_record(_passport_cdpm_models(), keys, planned,
                           cosmos_env=None)
    assert quiet["declaration"]["models_note"] == r.MODELS_NOTE_NO_COSMOS
    assert [m["role"] for m in quiet["declaration"]["models"]] == ["extractor"]
    assert r.rr.validate(quiet) == []
    # Ответил, но моделей не назвал — это другая причина, и слова другие.
    empty = r.build_record(_passport_cdpm_models(), keys, planned,
                           cosmos_env=_env_answer(models=[]))
    assert empty["declaration"]["models_note"] == r.MODELS_NOTE_COSMOS_SILENT


def test_адрес_косомоса_ключ_сильнее_переменной_контура(monkeypatch):
    """Адрес COSMOS — тот же, куда контур шлёт пакет постановки. Ключ
    запуска сильнее переменной (закон дверей паспорта); ни ключа, ни
    переменной — адрес не выдумывается."""
    r = _runner()
    monkeypatch.delenv(r.COSMOS_URL_ENV, raising=False)
    assert r.cosmos_url(_Keys()) is None
    monkeypatch.setenv(r.COSMOS_URL_ENV, COSMOS_BASE + "/")
    assert r.cosmos_url(_Keys()) == COSMOS_BASE
    assert r.cosmos_url(_Keys(cosmos_url=COSMOS_BASE + "/иной")) == \
        COSMOS_BASE + "/иной"


def test_ручка_среды_косомоса_спрашивается_допуском_набора(monkeypatch, capsys):
    """Раннер зовёт `GET /api/run/environment` с допуском моделей НАБОРА
    и токеном канала; ответ не по договору либо недоступный COSMOS —
    None со словами в журнал, а не падение: летопись прогон не роняет."""
    r = _runner()
    monkeypatch.setenv(r.COSMOS_URL_ENV, COSMOS_BASE)
    monkeypatch.setenv(r.COSMOS_TOKEN_ENV, "токен-стенда")
    cosmos = _Cosmos(_env_answer())
    got = r.ask_cosmos_environment(_Keys(), "any", _open=cosmos)
    assert got == _env_answer()
    call = cosmos.calls[0]
    assert call["url"] == COSMOS_BASE + r.COSMOS_ENV_ROUTE + "?scope=any"
    assert call["timeout"] == r.COSMOS_ENV_TIMEOUT_S
    assert call["headers"]["X-channel-token"] == "токен-стенда"
    # Допуск не назван — параметра нет: самый узкий выберет сам COSMOS.
    r.ask_cosmos_environment(_Keys(), None, _open=cosmos)
    assert cosmos.calls[1]["url"] == COSMOS_BASE + r.COSMOS_ENV_ROUTE
    # Ответ не по договору — как будто не спрошен.
    other = _Cosmos({"schema": "чужая-схема/v1", "models": []})
    assert r.ask_cosmos_environment(_Keys(), None, _open=other) is None
    assert "не по договору" in capsys.readouterr().out
    # COSMOS недоступен — слова в журнал, прогон продолжается.
    down = _Cosmos(raises=OSError("нет связи"))
    assert r.ask_cosmos_environment(_Keys(), None, _open=down) is None
    assert "среда COSMOS не прочитана" in capsys.readouterr().out
    # Адреса нет вовсе — ручка не зовётся.
    monkeypatch.delenv(r.COSMOS_URL_ENV)
    never = _Cosmos(_env_answer())
    assert r.ask_cosmos_environment(_Keys(), None, _open=never) is None
    assert never.calls == []
    assert "адрес не назван" in capsys.readouterr().out


def test_допуск_моделей_набора_читается_из_паспорта():
    """Допуск моделей знает ХОЗЯИН записи: он едет в COSMOS из паспорта
    набора, а не выдумывается площадкой."""
    r = _runner()
    assert r.kit_llm_scope(_passport()) is None
    p = _passport()
    p["kit"] = {**p["kit"], "llm_scope": "any"}
    assert r.kit_llm_scope(p) == "any"


# ── Объявление прогона при создании (раздел 11.1) ──────────────────────

def test_форма_создания_несёт_объявление_прогона():
    """Поля объявления — из ГОЛОВЫ записи, а не собраны заново: номер
    записи тот самый, что отчеканен до создания прогона; план этапов —
    через запятую; ожидания — манифест набора путём от корня."""
    r = _runner()
    record = _record(r, planned=("create", "extract", "package"))
    form = r.declaration_form(record)
    assert set(form) == {"declaration", "declaration_sha256",
                         "record_run_id", "created_by", "purpose",
                         "answer_policy", "planned_stages", "stop_after",
                         "expectations_kind", "expectations_ref"}
    # голова целиком и её отпечаток — хранитель кладёт её как есть
    assert json.loads(form["declaration"]) == record["declaration"]
    assert form["declaration_sha256"] == record["declaration_sha256"]
    assert form["declaration_sha256"] == r.rr.declaration_sha256(
        json.loads(form["declaration"]))
    assert form["record_run_id"] == record["declaration"]["run_id"]
    assert form["created_by"] == r.RECORD_ACTOR
    assert form["purpose"] == r.PURPOSE_DEFAULT
    assert form["answer_policy"] == "книга"
    assert form["planned_stages"] == "create,extract,package"
    assert form["stop_after"] == "package"
    assert form["expectations_kind"] == "манифест набора"
    assert form["expectations_ref"] == "open-bench/проба/kit-manifest.json"
    # Записи нет — объявлять нечего: выдумывать номер раннер не станет.
    assert r.declaration_form(None) == {}
    assert r.declaration_form({"declaration": {}}) == {}


def test_прогон_объявляется_панели_при_создании(tmp_path, monkeypatch):
    """Стадия create отдаёт объявление формой создания прогона; заказчик
    и признак комплекта едут как прежде — старая панель лишних полей не
    читает, и прогон идёт по-старому."""
    r = _runner()
    panel = _Panel()
    record = _record(r)
    rn = _runner_instance(r, tmp_path, panel, record)
    _prepare_create(rn, tmp_path, monkeypatch)

    rn.stage_create()

    kw = panel.body("POST", "/api/runs")
    assert kw["data"]["record_run_id"] == record["declaration"]["run_id"]
    assert kw["data"]["created_by"] == r.RECORD_ACTOR
    assert kw["data"]["purpose"] == r.PURPOSE_DEFAULT
    assert kw["data"]["answer_policy"] == "книга"
    assert kw["data"]["stop_after"] == "package"
    assert kw["data"]["expectations_kind"] == "манифест набора"
    assert kw["params"]["customer"] == rn.customer
    assert kw["params"]["data_class"] == "synthetic"
    assert rn.state["run"] == RUN


# ── Хранитель файла записи (раздел 11.1, переходное правило 11.3) ──────

def test_хранитель_панель_файл_в_папку_прогона_не_пишется(tmp_path,
                                                          monkeypatch):
    """Панель ведёт нашу запись: раннер пишет файл ТОЛЬКО в свой --out, а
    этап уезжает ей дописыванием — телом по разделу 11.2."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r)
    panel = _Panel(record=_Resp(200, _panel_record(record)))
    rn = _runner_instance(r, tmp_path, panel, record)
    _prepare_create(rn, tmp_path, monkeypatch)

    rn.stage_create()
    assert rn.record_custodian() == r.CUSTODIAN_PANEL
    rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                    "2026-09-10T09:00:03+03:00", rr.OUTCOME_OK)

    assert (rn.out / rr.RECORD_FILE).is_file()
    assert not (rn.run_dir() / rr.RECORD_FILE).exists()
    body = panel.body("POST", "/record/append")["json"]
    assert body["schema"] == r.RECORD_APPEND_SCHEMA == \
        "cosmos-run-record-append/v1"
    assert body["record_id"] == record["declaration"]["run_id"]
    assert body["declaration_sha256"] == record["declaration_sha256"]
    assert body["system"] == r.RECORD_SYSTEM == "runner"
    entry = body["entries"][0]
    assert entry["stage"] == "create" and entry["outcome"] == rr.OUTCOME_OK
    assert entry["actor"] == r.RECORD_ACTOR
    # Запись этапа — по словарю полей: хранитель судит её схемой.
    assert set(entry) == set(rr.JOURNAL_FIELDS)
    assert rr.normalize_entry(entry) == entry


def test_хранитель_раннер_при_старой_панели(tmp_path, monkeypatch):
    """Старая панель ручки записи не знает (404) — переходное правило:
    файл в папку прогона пишет раннер, как сегодня, и дописывание не
    шлётся."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r)
    rn = _runner_instance(r, tmp_path, _Panel(), record)
    _prepare_create(rn, tmp_path, monkeypatch)

    rn.stage_create()
    assert rn.record_custodian() == r.CUSTODIAN_RUNNER
    rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                    "2026-09-10T09:00:03+03:00", rr.OUTCOME_OK)

    assert (rn.out / rr.RECORD_FILE).is_file()
    assert (rn.run_dir() / rr.RECORD_FILE).is_file()
    assert rn.client.count("POST", "/record/append") == 0


def test_чужая_запись_у_панели_оставляет_хранителем_раннера(tmp_path,
                                                            monkeypatch):
    """Панель ответила записью с ЧУЖИМ номером — она ведёт свою половину,
    не нашу: хранитель остаётся раннер, дописывание не шлётся (иначе
    записи двух прогонов слились бы в одну)."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r)
    theirs = copy.deepcopy(_panel_record(record))
    theirs["declaration"] = dict(theirs["declaration"], run_id="чужой-номер")
    rn = _runner_instance(r, tmp_path, _Panel(record=_Resp(200, theirs)),
                          record)
    _prepare_create(rn, tmp_path, monkeypatch)

    rn.stage_create()
    assert rn.record_custodian() == r.CUSTODIAN_RUNNER
    rn.record_stage("create", None, None, rr.OUTCOME_OK)
    assert rn.client.count("POST", "/record/append") == 0
    assert (rn.run_dir() / rr.RECORD_FILE).is_file()


def test_отказ_хранителя_не_роняет_прогон(tmp_path, monkeypatch, capsys):
    """Отказ дописывания (409 — голова разошлась, 422 — запись не по
    словарю) — предупреждение СЛОВАМИ и продолжение: запись в каталоге
    --out полна всегда."""
    from vmp import run_record as rr
    r = _runner()
    for status, code in ((409, "declaration-mismatch"), (422, "entry-invalid")):
        place = Path(tmp_path) / f"отказ-{status}"
        record = _record(r)
        panel = _Panel(record=_Resp(200, _panel_record(record)),
                       append=_Resp(status, {"ok": False, "code": code,
                                             "words": ["слова отказа"]}))
        rn = _runner_instance(r, place, panel, record)
        _prepare_create(rn, place, monkeypatch)
        rn.stage_create()
        rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                        "2026-09-10T09:00:03+03:00", rr.OUTCOME_OK)
        printed = capsys.readouterr().out
        assert "ПРЕДУПРЕЖДЕНИЕ" in printed and str(status) in printed
        assert code in printed and "слова отказа" in printed
        # Прогон идёт дальше, и своя запись полна.
        mine = rr.load(rn.out)
        assert [e["stage"] for e in mine["journal"]] == ["create"]


def test_итоги_уходят_хранителю_вердиктами_и_выдачами(tmp_path, monkeypatch):
    """Закрытие записи шлёт хозяину ИТОГИ — вердикты, выдачи (раздел
    11.2) и сравнения (шаг 3, раздел 12.4); этапы плана, помеченные «не
    начат», уходят отметками."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r, planned=("create", "package"))
    panel = _Panel(record=_Resp(200, _panel_record(record)))
    rn = _runner_instance(r, tmp_path, panel, record)
    _prepare_create(rn, tmp_path, monkeypatch)

    rn.stage_create()
    rn.state["stages"]["create"] = {"sec": 1, "verdict": "EQUIVALENT"}
    rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                    "2026-09-10T09:00:01+03:00", rr.OUTCOME_OK)
    rn.finish_record()

    body = panel.body("POST", "/record/append")["json"]
    assert body["system"] == r.RECORD_SYSTEM
    assert body["results"]["verdicts"] == {"create": "EQUIVALENT"}
    assert body["results"]["deliveries"] == []
    # Суда ожиданий не было (стадия package не пройдена) — сравнение
    # честно пусто и названо «не судились».
    assert body["results"]["comparison"]["expectations"]["verdict"] == \
        "не судились"
    assert body["results"]["comparison"]["previous_run"] == {}
    stages = [e["stage"] for _m, path, kw in panel.calls
              if path.endswith("/record/append")
              for e in (kw["json"] or {}).get("entries") or []]
    assert stages == ["create", "package"]


# ── Путь ручки записи (раздел 10: record_api манифеста пакета) ─────────

def test_путь_ручки_записи_из_манифеста_пакета():
    """Манифест пакета несёт `record_api` полным адресом, но база у
    раннера СВОЯ (панель в процессе либо --base-url): читается только
    путь. Поля нет — путь строится по имени прогона."""
    r = _runner()
    assert r.record_path(f"{PANEL_BASE}/api/runs/{RUN}/record", "другой") == \
        f"/api/runs/{RUN}/record"
    assert r.record_path(None, RUN) == f"/api/runs/{RUN}/record"
    assert r.record_path("", RUN) == f"/api/runs/{RUN}/record"
    assert r.record_path(None, None) is None


# ── Ожидание «запись сшита» (раздел 10, пункт 5) ───────────────────────

WANT_STITCH = {"record_stitched": {"cosmos_stages": ["pairing", "rostering",
                                                     "source-check"]}}


def test_сшивка_записи_судится_по_журналу_хозяина():
    """Этапы COSMOS у хозяина записи: имя этапа — задача кнопки либо её
    слово из словаря записи; нет ожидаемой стадии — красная строка
    словами, какой именно нет."""
    r = _runner()
    full = {"journal": [_cosmos_entry("pairing", "1"),
                        _cosmos_entry("rostering", "2"),
                        _cosmos_entry("source-check", "3")]}
    assert r.record_stitched_problems(WANT_STITCH, full) == []
    # Тот же журнал словами словаря записи (attempt_to_journal зовёт
    # этапы «пейринг» и «ростер») и обёрткой {system, entry}.
    words = {"journal": [{"system": "cosmos", "entry": {"stage": "пейринг"}},
                         {"system": "cosmos",
                          "entry": {"stage": "ростер на пейринге"}},
                         {"system": "cosmos",
                          "entry": {"stage": "source-check"}}]}
    assert r.record_stitched_problems(WANT_STITCH, words) == []

    half = {"journal": [_cosmos_entry("pairing"), _cosmos_entry("rostering")]}
    probs = r.record_stitched_problems(WANT_STITCH, half)
    assert len(probs) == 1 and "source-check" in probs[0]

    # Записи раннера этапами COSMOS не притворяются.
    mine = {"journal": [{"system": "runner", "stage": "pairing"}]}
    probs = r.record_stitched_problems(WANT_STITCH, mine)
    assert len(probs) == 1 and "cosmos" in probs[0]

    probs = r.record_stitched_problems(WANT_STITCH, None)
    assert len(probs) == 1 and "ручка записи" in probs[0]
    # Ожидания в наборе нет — судить нечего.
    assert r.record_stitched_problems({}, None) == []


def test_сшивка_зелена_при_ждёт_с_полной_записью_у_хранителя(tmp_path,
                                                             monkeypatch):
    """Очередь COSMOS ещё «ждёт» повтора, а запись у ХРАНИТЕЛЯ уже
    полна — ожидание `record_stitched` зелено (разбор 11.09.2026).

    Так бывает штатно: отправка оборвалась по сроку занятой панели уже
    ПОСЛЕ того, как хозяин положил этапы; повтор по ключу
    идемпотентности заменит строку, а не удвоит. Судить сшивку по
    очереди отправителя значило бы краснеть там, где летопись цела —
    раннер судит ЗАПИСЬ ХОЗЯИНА."""
    from webapp import run_journal

    r = _runner()
    wd = tmp_path / "package-runs" / ("7" * 64)
    head = {"record_id": RUN, "owner": "панель CDPM",
            "record_api": f"{PANEL_BASE}/api/runs/{RUN}/record",
            "declaration_sha256": "f" * 64}

    def _timeout(req, timeout=None):
        raise TimeoutError("панель занята")

    monkeypatch.setattr(run_journal._urlrequest, "urlopen", _timeout)
    run_journal._append_row(run_journal.outbox_path(wd), {
        "key": {"system": "cosmos", "stage": "пейринг", "substage": "A1"},
        "entry": {"stage": "пейринг", "substage": "A1", "outcome": "успех"},
        "at": "2026-09-11T02:00:00+03:00", "results": {}})
    got = run_journal.flush_outbox(wd, head)
    assert got["outcome"] == run_journal.STITCH_WAITING, got
    state = run_journal.stitch_state(wd, head)
    assert state["waiting"] == 1 and state["refused"] == 0, state

    keeper = {"journal": [_cosmos_entry("pairing", "1"),
                          _cosmos_entry("rostering", "2"),
                          _cosmos_entry("source-check", "3")]}
    assert r.record_stitched_problems(WANT_STITCH, keeper) == [], \
        "«ждёт» у отправителя не красит сшивку: у хранителя запись полна"


def test_сшивка_судится_только_при_нажатиях(tmp_path, monkeypatch):
    """Без машинного отчёта нажатий ключ честно уезжает в «не судится
    раннером» (этапы COSMOS берутся от НАЖАТИЙ, а не от прогона CDPM); с
    отчётом — судится и попадает в «ПРОВЕРЕНО раннером»."""
    r = _runner()
    record = _record(r)
    stitched = _panel_record(record, journal=[
        _cosmos_entry("pairing", "1"), _cosmos_entry("rostering", "2"),
        _cosmos_entry("source-check", "3")])
    exp = copy.deepcopy(WANT_STITCH)

    rn = _runner_instance(r, tmp_path, _Panel(record=_Resp(200, stitched)),
                          record)
    rn.state["run"] = RUN
    rn.manifest = {"expect": exp}
    rn.etalon = None
    rn.cosmos_press = None
    rn.cosmos_press_path = None

    summary: dict = {}
    rn.check_expect({}, {}, summary)
    assert summary["expect"]["problems"] == []
    assert summary["expect"]["checked"] == []
    assert "record_stitched" in summary["expect"]["not_judged"]
    assert "--cosmos-press" in summary["expect"]["not_judged"]["record_stitched"]

    rn.cosmos_press = {"package_sha": None}
    summary = {}
    rn.check_expect({}, {}, summary)
    assert summary["expect"]["problems"] == []
    assert "record_stitched" in summary["expect"]["checked"]
    assert "record_stitched" not in summary["expect"]["not_judged"]

    # Хозяин этапов COSMOS не получил — красная строка со словами.
    rn2 = _runner_instance(r, Path(tmp_path) / "красный",
                           _Panel(record=_Resp(200, _panel_record(record))),
                           record)
    rn2.state["run"] = RUN
    rn2.manifest = {"expect": exp}
    rn2.etalon = None
    rn2.cosmos_press = {"package_sha": None}
    rn2.cosmos_press_path = None
    summary = {}
    rn2.check_expect({}, {}, summary)
    assert len(summary["expect"]["problems"]) == 1
    assert "сшивка записи" in summary["expect"]["problems"][0]
    assert str(summary["verdict"]).startswith("EXPECT_MISMATCH")


def test_манифесты_трёх_наборов_несут_ожидание_сшивки():
    """Ожидание «запись сшита» — у всех трёх наборов приёмки (Н0, Н0-B,
    Н0-C) и одним перечнем стадий: приёмка одинакова на каждой ступени."""
    r = _runner()
    for name in ("polyaris-h0", "polyaris-h0b", "polyaris-h0c"):
        man = json.loads((ROOT / "open-bench" / name / "kit-manifest.json")
                         .read_text(encoding="utf-8"))
        want = man["expect"]["record_stitched"]
        assert want["cosmos_stages"] == ["pairing", "rostering",
                                         "source-check"], name
        # Ключ известен раннеру: без нажатий — с причиной, с нажатиями —
        # судимый (иначе новое ожидание выдало бы себя за проверенное).
        judged, skipped = r.expect_key_verdicts(man["expect"])
        assert "record_stitched" in skipped, name
        judged, skipped = r.expect_key_verdicts(
            man["expect"], ("cosmos_numbers", "record_stitched"))
        assert "record_stitched" in judged, name
    # Фаза 2 Н0-C описана тем же ожиданием, что и живой ключ expect.
    h0c = json.loads((ROOT / "open-bench" / "polyaris-h0c" /
                      "kit-manifest.json").read_text(encoding="utf-8"))
    assert h0c["expect_phases"]["2"]["record_stitched"] == \
        h0c["expect"]["record_stitched"]


def test_продолжение_прогона_ведёт_ту_же_запись(tmp_path, capsys):
    """`--run <прогон>` при записи в --out и том же прогоне в состоянии —
    запись продолжается (номер тот же, новая голова не чеканится); другой
    прогон в состоянии или пустой --out — запись новая."""
    r = _runner()
    rn = _runner_instance(r, tmp_path, _Panel())
    first = _record(r)
    rr = r.rr
    rr.save(rn.out / rr.RECORD_FILE, first)
    rn.state["run"] = RUN

    class _Args(_Keys):
        def __init__(self, run):
            super().__init__()
            self.run = run

    kept = rn.make_record(_Args(RUN), ["create", "package"])
    assert kept["declaration"]["run_id"] == first["declaration"]["run_id"]
    assert "продолжена" in capsys.readouterr().out
    # состояние помнит другой прогон — новая запись
    rn.state["run"] = "run-9999"
    fresh = rn.make_record(_Args(RUN), ["create", "package"])
    words = capsys.readouterr().out
    assert fresh is not kept and "чеканится новая запись" in words
    # без --run — всегда новая (номер в ту же секунду может совпасть:
    # чеканка — момент плюс корень; сравнивается сама запись, не номер)
    rn.state["run"] = RUN
    again = rn.make_record(_Args(None), ["create", "package"])
    assert again is not kept and "продолжена" not in capsys.readouterr().out


def test_голова_хранителя_из_ответа_рождения_запасной_путь():
    """Панель, принявшая голову раннера как есть, отвечает тем же
    отпечатком — слов нет; панель без поля declaration чеканит свою —
    дописывание несёт отпечаток ХРАНИТЕЛЯ и расхождение названо словами."""
    r = _runner()
    record = _record(r)
    mine = record["declaration_sha256"]
    sha, words = r.keeper_head({"run": RUN, "record": {"run_id": record["declaration"]["run_id"],
                                                        "declaration_sha256": mine}}, record)
    assert sha == mine and words is None
    sha2, words2 = r.keeper_head({"run": RUN, "record": {"declaration_sha256": "f" * 64}}, record)
    assert sha2 == "f" * 64 and "две головы" in words2
    assert r.keeper_head({"run": RUN}, record) == (None, None)
    body = r.append_body(record, entries=[], declaration_sha256=sha2)
    assert body["declaration_sha256"] == "f" * 64
    assert r.append_body(record, entries=[])["declaration_sha256"] == mine


# ── Итоги записи: сравнения и золотой (шаг 3, разделы 3.3 и 12.4) ──────

#: Суд ожиданий стадии package глазами раннера (то, что кладёт
#: `check_expect`): расхождений нет, судимые ключи названы, несудимые —
#: с причиной, число строк сверки — предупреждением.
PACKAGE_EXPECT = {
    "problems": [], "checked": ["objectives", "questions"],
    "not_judged": {"cosmos_numbers": "нужен машинный отчёт нажатий"},
    "warnings": ["числа COSMOS[pairing]: строк сверки 12, в манифесте 11"],
    "cosmos_press": None,
}

#: Ответ ручки дерева прогонов панели (раздел 12.2): сравнения считает
#: `vmp/run_tree.py`, раннер переносит их в итоги как есть.
TREE_ANSWER = {
    "node": {"run_id": "запись-2"},
    "path": [], "siblings": [],
    "golden": {"run_id": "запись-0"},
    "compare_previous": {"kind": "против предыдущего прогона того же корня",
                         "a": "запись-1", "b": "запись-2",
                         "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-1 → запись-2",
                                   "   корень один: входы те же"]},
    "compare_golden": {"kind": "против золотого прогона",
                       "a": "запись-0", "b": "запись-2",
                       "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-0 → запись-2"]},
}


def _run_to_finish(r, tmp_path, monkeypatch, panel, record, expect=None):
    """Прогон до закрытия записи: create с отметкой, суд ожиданий стадии
    package в состоянии, итоги. Служб нет — панель подставная."""
    rn = _runner_instance(r, tmp_path, panel, record)
    _prepare_create(rn, tmp_path, monkeypatch)
    rn.stage_create()
    rn.state["stages"]["create"] = {"sec": 1, "verdict": "EQUIVALENT"}
    rn.record_stage("create", "2026-09-10T09:00:00+03:00",
                    "2026-09-10T09:00:01+03:00", r.rr.OUTCOME_OK)
    if expect is not None:
        rn.state["stages"]["package"] = {"verdict": "EQUIVALENT",
                                         "expect": copy.deepcopy(expect)}
        rn.record_stage("package", "2026-09-10T09:00:01+03:00",
                        "2026-09-10T09:00:04+03:00", r.rr.OUTCOME_OK)
    rn.finish_record()
    return rn


def test_итоги_несут_сравнение_с_ожиданиями_набора(tmp_path, monkeypatch):
    """Сравнение с ожиданиями в итогах — ровно суд стадии package (один
    источник правды, второго суда нет), а вид и путь ожиданий — из
    объявления. Запись со сравнениями сходится со словарём полей."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r, planned=("create", "package"))
    panel = _Panel(record=_Resp(200, _panel_record(record)))
    rn = _run_to_finish(r, tmp_path, monkeypatch, panel, record,
                        expect=PACKAGE_EXPECT)

    got = rn.record["results"]["comparison"]["expectations"]
    assert got == {"kind": "манифест набора",
                   "ref": "open-bench/проба/kit-manifest.json",
                   "checked": ["objectives", "questions"],
                   "not_judged": {"cosmos_numbers":
                                  "нужен машинный отчёт нажатий"},
                   "problems": [],
                   "warnings": ["числа COSMOS[pairing]: строк сверки 12, в "
                                "манифесте 11"],
                   "verdict": "выполнены"}
    assert rr.validate(rn.record) == []
    # То же лежит файлом: карточка читает итоги, а не пересчитывает их.
    assert rr.load(rn.out)["results"]["comparison"]["expectations"] == got
    # Расхождения ожиданий доезжают до итогов вердиктом «не выполнены».
    bad = _run_to_finish(
        r, Path(tmp_path) / "расхождение", monkeypatch,
        _Panel(record=_Resp(200, _panel_record(_record(r)))), _record(r),
        expect={**PACKAGE_EXPECT, "problems": ["целевых 1, ожидалось 2"]})
    assert bad.record["results"]["comparison"]["expectations"]["verdict"] == \
        "не выполнены"
    # Прогон встал раньше суда — «не судились», а не «выполнены».
    none = _run_to_finish(
        r, Path(tmp_path) / "без-суда", monkeypatch,
        _Panel(record=_Resp(200, _panel_record(_record(r)))), _record(r))
    assert none.record["results"]["comparison"]["expectations"]["verdict"] == \
        "не судились"


def test_итоги_со_сравнениями_уезжают_хозяину(tmp_path, monkeypatch):
    """Итоги едут хозяину тем же дописыванием: вердикты, выдачи и
    сравнения. Записей журнала в этом теле нет — этапы ушли отметками
    раньше, и хранитель принимает тело с пустыми entries."""
    r = _runner()
    record = _record(r, planned=("create", "package"))
    panel = _Panel(record=_Resp(200, _panel_record(record)),
                   tree=_Resp(200, TREE_ANSWER))
    rn = _run_to_finish(r, tmp_path, monkeypatch, panel, record,
                        expect=PACKAGE_EXPECT)

    body = panel.body("POST", "/record/append")["json"]
    assert body["entries"] == []
    assert set(body["results"]) == {"verdicts", "deliveries", "comparison"}
    assert body["results"]["comparison"] == rn.record["results"]["comparison"]
    assert body["results"]["verdicts"] == {"create": "EQUIVALENT",
                                           "package": "EQUIVALENT"}


def test_сравнение_с_предыдущим_и_золотым_из_ручки_дерева(tmp_path,
                                                          monkeypatch,
                                                          capsys):
    """Ручка дерева ответила — в итогах стоят `{run_id, words}` по
    предыдущему прогону того же корня и по золотому, и слова сравнения
    звучат в журнале прогона; ручки ещё нет (404) — сравнения ПУСТЫ,
    прогон закрывается, запись сходится."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r, planned=("create", "package"))
    panel = _Panel(record=_Resp(200, _panel_record(record)),
                   tree=_Resp(200, TREE_ANSWER))
    rn = _run_to_finish(r, tmp_path, monkeypatch, panel, record,
                        expect=PACKAGE_EXPECT)

    cmp = rn.record["results"]["comparison"]
    assert cmp["previous_run"] == {
        "run_id": "запись-1",
        "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-1 → запись-2",
                  "   корень один: входы те же"]}
    assert cmp["golden"] == {
        "run_id": "запись-0",
        "words": ["СРАВНЕНИЕ ПРОГОНОВ запись-0 → запись-2"]}
    assert rr.validate(rn.record) == []
    assert panel.count("GET", f"/api/runs/{RUN}/tree") == 1
    # Слова сравнения — человеку в журнал прогона, с кем сравнивали.
    printed = capsys.readouterr().out
    assert "СРАВНЕНИЕ С ПРЕДЫДУЩИМ ПРОГОНОМ (запись-1)" in printed
    assert "СРАВНЕНИЕ С ЗОЛОТЫМ ПРОГОНОМ (запись-0)" in printed
    assert "корень один: входы те же" in printed

    # Ручки шага 3 у панели ещё нет: честно пусто, без падения.
    quiet = _run_to_finish(
        r, Path(tmp_path) / "без-дерева", monkeypatch,
        _Panel(record=_Resp(200, _panel_record(_record(r)))), _record(r),
        expect=PACKAGE_EXPECT)
    cmp2 = quiet.record["results"]["comparison"]
    assert cmp2["previous_run"] == {} and cmp2["golden"] == {}
    assert cmp2["expectations"]["verdict"] == "выполнены"
    assert rr.validate(quiet.record) == []
    # Молчание ручки названо словами, а прогон закрыт.
    words = capsys.readouterr().out
    assert "ручка дерева прогонов ответила 404" in words
    assert "СРАВНЕНИЕ С ПРЕДЫДУЩИМ" not in words


def test_раннер_не_ставит_признак_золотого(tmp_path, monkeypatch):
    """Признак прогона-образца ставит ТОЛЬКО владелец словом после
    приёмки на Prod (решение владельца 6.3): раннер не ставит его
    никогда — даже когда дерево показало золотого соседа, — и в итоги,
    уезжающие хозяину, признак не попадает вовсе."""
    from vmp import run_record as rr
    r = _runner()
    record = _record(r, planned=("create", "package"))
    panel = _Panel(record=_Resp(200, _panel_record(record)),
                   tree=_Resp(200, TREE_ANSWER))
    rn = _run_to_finish(r, tmp_path, monkeypatch, panel, record,
                        expect=PACKAGE_EXPECT)

    assert rn.record["results"]["golden"] == {"confirmed": False, "by": None,
                                              "at": None, "words": None}
    assert rr.load(rn.out)["results"]["golden"]["confirmed"] is False
    assert "golden" not in panel.body("POST", "/record/append")["json"]["results"]
    # Сравнение с золотым — это ПОКАЗ, а не признак: слова в итогах
    # стоят, а прогон золотым не назван.
    assert rn.record["results"]["comparison"]["golden"]["run_id"] == "запись-0"


def test_коммит_cdpm_в_голове_записи_по_панели_а_не_по_дереву(tmp_path, capsys):
    """Панель, ведущая прогон, называет свой коммит предполётной сверкой
    (declaration-preview) — он и идёт в голову записи; дерево раннера может
    уйти вперёд записью в ведомость (живая находка круга s5 10.09.2026:
    гейт панели «объявлен a6bf50de, на деле a78a924d»). Панель молчит —
    коммит из паспорта."""
    r = _runner()

    class _PanelWithPreview(_Panel):
        def get(self, path):
            if path.endswith("/api/declaration-preview"):
                return _Resp(200, {"ok": True, "declaration": {
                    "code": {"cdpm_commit": "панель-коммит-1234",
                             "cosmos_commit": "панель-коммит-1234",
                             "runner_fingerprint": None}}})
            return super().get(path)

    rn = _runner_instance(r, tmp_path, _PanelWithPreview())
    rec = rn.make_record(_Keys(), ["create", "package"])
    assert rec["declaration"]["code"]["cdpm_commit"] == "панель-коммит-1234"
    assert rec["declaration"]["code"]["cosmos_commit"] == "коммит-пробы"
    assert "по панели" in capsys.readouterr().out
    # старая панель (404) — коммит из паспорта, словами
    rn2 = _runner_instance(r, tmp_path / "два", _Panel())
    rec2 = rn2.make_record(_Keys(), ["create", "package"])
    assert rec2["declaration"]["code"]["cdpm_commit"] == "коммит-пробы"
    assert "из паспорта" in capsys.readouterr().out
