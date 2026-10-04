"""Мост CDPM -> COSMOS, этап А: приём пакета постановки и конвертер.

Каждая отказная ветка условия МУ-02 проверена нарушающим случаем И
контрольным: гейт, который отвергает всё, так же бесполезен, как
пропускающий всё.

Эталонный пакет — набор «Полярис-нано» из комплекта передачи. Данных
заказчика в тестах нет и быть не может (условие МУ-07).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from webapp import task_package as tp

REPO = Path(__file__).resolve().parents[1]
ETALON_DIR = REPO.parent / "handoff" / "2026-08-22-MOST-A" / "etalon-nano"
ETALON = ETALON_DIR / "run-0027-task-package.zip"
ETALON_LIMITED = ETALON_DIR / "run-0027-limited-modes.zip"

etalon_needed = pytest.mark.skipif(
    not ETALON.exists(),
    reason="эталонный пакет вехи А не найден: %s" % ETALON)


@pytest.fixture()
def unpacked(tmp_path: Path) -> Path:
    """Эталон, распакованный в каталог: так его можно портить."""
    if not ETALON.exists():
        pytest.skip("эталонный пакет вехи А не найден: %s" % ETALON)
    d = tmp_path / "pkg"
    d.mkdir()
    with zipfile.ZipFile(ETALON) as z:
        z.extractall(d)
    return d


def _repack(src: Path, dst: Path) -> Path:
    with zipfile.ZipFile(dst, "w") as z:
        for p in sorted(src.iterdir()):
            if p.is_file():
                z.write(p, p.name)
    return dst


# ------------------------------------------------------------ приём

@etalon_needed
def test_эталонный_пакет_принимается_целиком():
    pkg = tp.load(ETALON)
    assert pkg["files_checked"] == 5, "сверены не все файлы манифеста"
    assert pkg["manifest"]["spec_version"].startswith("1.")
    assert pkg["fingerprint"] and len(pkg["fingerprint"]) == 64
    # Гейт производителя реально запускался и был зелёным.
    assert "ВЕРДИКТ" in (pkg["gate_output"] or "")


@etalon_needed
def test_разрешённые_режимы_читаются_из_манифеста():
    pkg = tp.load(ETALON)
    modes = tp.allowed_modes(pkg, "rostering")
    assert "feasibility-hard" in modes and "feasibility-soft" in modes
    # Контроль: у негодной задачи перечень пуст, а не «любой режим».
    assert tp.allowed_modes(pkg, "нет-такой-задачи") == []


@etalon_needed
def test_негативный_эталон_запрещает_мягкий_режим_ростеринга():
    if not ETALON_LIMITED.exists():
        pytest.skip("негативный эталон не найден")
    pkg = tp.load(ETALON_LIMITED)
    # Контроль: жёсткий режим тем же пакетом разрешён.
    tp.check_mode(pkg, "rostering", "feasibility-hard")
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.check_mode(pkg, "rostering", "feasibility-soft")
    assert "не разрешён пакетом" in str(e.value)


def test_подмена_байта_в_файле_ловится_суммой(unpacked: Path, tmp_path: Path):
    # Контроль: до подмены пакет принимается (гейт не гоняем — он про
    # то же самое, но дороже; здесь проверяется НАША сверка).
    tp.load(unpacked, run_gate=False)
    obj = unpacked / "objective.json"
    # Порча ПОЗИЦИОННАЯ, а не по содержимому: прежняя версия искала в
    # файле литерал '"weight": 10' и при перевыпуске эталона тихо
    # перестала что-либо портить — отказ не наступал, а тест зеленел.
    # Приписанный пробел вдобавок сильнее: файл остаётся ВАЛИДНЫМ JSON
    # с прежним смыслом, поэтому разбор содержимого подмены не заметил
    # бы. Ловит её только сверка сумм, что и есть предмет проверки.
    obj.write_bytes(obj.read_bytes() + b" ")
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.load(unpacked, run_gate=False)
    assert "контрольная сумма" in str(e.value)
    assert "objective.json" in str(e.value)


def test_подмена_манифеста_меняет_отпечаток_постановки(unpacked: Path):
    before = tp.load(unpacked, run_gate=False)["fingerprint"]
    mf = unpacked / "manifest.json"
    body = json.loads(mf.read_text(encoding="utf-8"))
    body["revision"] = int(body.get("revision") or 0) + 1
    mf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    after = tp.load(unpacked, run_gate=False)["fingerprint"]
    assert before != after, (
        "разные постановки обязаны иметь разные подписи (МУ-04)")


def test_неизвестная_старшая_версия_спецификации_отвергается(unpacked: Path):
    mf = unpacked / "manifest.json"
    body = json.loads(mf.read_text(encoding="utf-8"))
    body["spec_version"] = "2.0"
    mf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.load(unpacked, run_gate=False)
    assert "не поддержана" in str(e.value)


def test_младшая_версия_выше_известной_принимается(unpacked: Path):
    # Спецификация §3: известный MAJOR + больший MINOR — читать известное.
    mf = unpacked / "manifest.json"
    body = json.loads(mf.read_text(encoding="utf-8"))
    body["spec_version"] = "1.9"
    mf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    pkg = tp.load(unpacked, run_gate=False)
    assert pkg["manifest"]["spec_version"] == "1.9"


def test_объявленный_файл_отсутствует_отказ(unpacked: Path):
    (unpacked / "model.json").unlink()
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.load(unpacked, run_gate=False)
    assert "отсутствует в пакете" in str(e.value)


@etalon_needed
def test_красный_гейт_производителя_останавливает_приём(unpacked: Path,
                                                        tmp_path: Path):
    """Гейт CDPM проверяет СОГЛАСОВАННОСТЬ пакета, а не только суммы.

    Взят случай, который сумма поймать не может в принципе: вес мягкого
    компонента снят (null), а манифест честно пересчитан. Пакет
    внутренне непротиворечив по контрольным суммам и наш приём его
    пропускает — отвергает именно гейт производителя, и потому он
    обязателен (постановка §3 п.2).
    """
    obj = unpacked / "objective.json"
    body = json.loads(obj.read_text(encoding="utf-8"))
    body["rostering"]["components"][0]["weight"] = None
    raw = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
    obj.write_bytes(raw)
    mf = unpacked / "manifest.json"
    man = json.loads(mf.read_text(encoding="utf-8"))
    for f in man["files"]:
        if f["name"] == "objective.json":
            f["sha256"] = hashlib.sha256(raw).hexdigest()
            f["bytes"] = len(raw)
    mf.write_text(json.dumps(man, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    # Контроль: наша сверка сумм такую подмену пропускает — суммы сходятся.
    tp.load(unpacked, run_gate=False)
    # ...и ровно поэтому гейт производителя обязателен на приёме.
    zip_path = _repack(unpacked, tmp_path / "подменённый.zip")
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.load(zip_path, run_gate=True)
    assert "гейт пакета отверг постановку" in str(e.value)
    assert "без числового веса" in str(e.value)


# --------------------------------------------------------- конвертер

@etalon_needed
def test_конвертер_строит_набор_решателя():
    pkg = tp.load(ETALON)
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)
    # В книге нано 7 строк рейсов, из них одна — ТОЧНЫЙ повтор PL101
    # (оставленная находка пакета F-0001). Повтор свёрнут со счётом:
    # решатель отвергает повтор личности рейса намеренно.
    assert rep["flights"] == 6, "рейсы книги данных нано без повтора"
    assert rep["duplicate_rows_collapsed"] == 1
    # Численность экипажа книги — НЕ константа: набор перевыпускается
    # (2026-08-23 экипаж расширен под норму резерва), и пришпиленное
    # число ловит перевыпуск, а не дефект. Проверяется инвариант
    # решаемости: людей хватает на все места.
    assert rep["employees"] >= rep["seats"], (
        "людей меньше, чем мест: набор заведомо неукомплектуем")
    # Численность пришла МАШИННЫМ полем манифеста (спецификация 1.2),
    # а не разбором формулировки: распознавание — у производителя.
    assert rep["crew_size_source"].startswith("машинное поле")
    # Книга v3 несёт тип воздушного судна — правил без данных нет.
    assert rep["rules_without_data"] == []
    # Состав места ВЫВЕДЕН ИЗ ПРАВИЛА пакета, а не придуман конвертером.
    assert rep["seat_roles"] == ["Captain", "FirstOfficer"]
    assert rep["crew_size_rule"] == "R-S1-табл-2-строка-3-кол-3"
    assert rep["seats"] == rep["flights"] * 2
    # Лист, которого конвертер не переносит, НАЗВАН, а не пропущен молча.
    assert "Смены" in rep["sheets_not_carried"]
    # Набор пригоден решателю: у каждого места есть свой рейс.
    legs = {f["flightLegId"] for f in payload["flights"]}
    assert all(a["flight"] in legs for a in payload["flightAssignments"])


@etalon_needed
def test_пояс_аэропорта_доезжает_до_набора():
    """Ревизия 1.10 (шов пояса, живой случай одной из линий):
    канонический лист
    «Аэропорты» (Код, timeZone: IANA) пробрасывается в аэропорты
    набора — построитель судит окна матрицы в поясе базы ИЗ ДАННЫХ.
    Листа нет — поля нет (честное отсутствие, построитель скажет
    вслух); выдумывания пояса не бывает."""
    import copy
    pkg = tp.load(ETALON)
    payload0 = tp.to_payload(pkg)
    # Эталон листа поясов не несёт — поля нет ни у одного аэропорта.
    assert all("timeZone" not in a for a in payload0["airports"])
    code0 = payload0["airports"][0]["code"]
    pkg2 = copy.deepcopy(pkg)
    holder = next(iter(pkg2["clean_data"]["tables"].values()))
    holder["Аэропорты"] = {
        "columns": ["Код", "timeZone"],
        "rows": [{"Код": code0, "timeZone": "Europe/Berlin"}]}
    payload2 = tp.to_payload(pkg2)
    by = {a["code"]: a for a in payload2["airports"]}
    assert by[code0]["timeZone"] == "Europe/Berlin"
    assert all("timeZone" not in a for c, a in by.items()
               if c != code0)


@etalon_needed
def test_сроки_допусков_доезжают_до_решателя():
    """Колонка «Действует до» — исполняемое окно, а не украшение книги.

    До 2026-08-23 конвертер брал с листа «Допуски» только тип судна, и
    срок терялся на входе: правило свода о действующем допуске было
    неисполнимо не по бедности решателя, а потому что данные до него не
    доезжали. Проверяется прочтение границ, а не конкретная дата
    эталона: конец суток включительно, нижняя граница не ограничена.
    """
    from datetime import datetime, timedelta
    pkg = tp.load(ETALON)
    payload = tp.to_payload(pkg)
    rows = [r for r in (pkg["clean_data"]["tables"]["D1"][tp.SHEET_QUALS]["rows"])
            if str(r.get("Действует до") or "").strip()]
    assert rows, "в эталоне нет допусков со сроком — проверять нечего"
    row = rows[0]
    emp = next(e for e in payload["employees"] if e["id"] == row["Табельный номер"])
    window = emp["qualificationWindows"][row["Тип ВС"]][0]
    until = datetime.strptime(str(row["Действует до"])[:10], "%Y-%m-%d")
    expected_hi = int((until + timedelta(days=1) - datetime(1970, 1, 1)).total_seconds() // 60) - 1
    assert window == [0, expected_hi], (
        "окно допуска обязано кончаться последней минутой указанных суток")
    rep = tp.conversion_report(pkg, payload)
    assert rep["qualification_windows"]["employees_with_windows"] > 0


def test_срок_допуска_не_указан_окно_открытое():
    """«Срок не указан» — не «просрочен»: подставлять сюда дату нельзя.

    Пакет строится в самом тесте: правило обязано проверяться и в
    свежем клоне (CI), где эталонов рядом с репозиторием нет — красный
    прогон 2026-08-24 на eliduc/COSMOS случился ровно из-за чтения
    эталона по машинному пути вне репозитория.
    """
    pkg = {
        "manifest": {"crew_size": {"value": 1, "rule": "R-TEST"},
                     "has_data": True},
        "model": {"rules": []},
        "clean_data": {"tables": {"D1": {
            tp.SHEET_FLIGHTS: {
                "columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                            "Прилёт", "Время прилёта"],
                "rows": [{"Номер рейса": "PL1", "Дата": "2026-03-02",
                          "Вылет": "PLY", "Время вылета": "10:00",
                          "Прилёт": "SVG", "Время прилёта": "12:00"}]},
            tp.SHEET_CREW: {
                "columns": ["Табельный номер", "База", "Должность"],
                "rows": [{"Табельный номер": "E1", "База": "PLY",
                          "Должность": "командир"}]},
            tp.SHEET_QUALS: {
                "columns": ["Табельный номер", "Тип ВС", "Действует до"],
                "rows": [{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                          "Действует до": ""}]},
        }}},
    }
    payload = tp.to_payload(pkg)
    emp = next(e for e in payload["employees"] if e.get("qualificationWindows"))
    window = emp["qualificationWindows"]["Полярис-Р200"][0]
    assert window[1] == tp.QUAL_OPEN_END_MIN, "окно обязано быть открытым"
    # Контроль: указанный срок даёт ЗАКРЫТОЕ окно последней минутой суток.
    from datetime import datetime, timedelta
    pkg["clean_data"]["tables"]["D1"][tp.SHEET_QUALS]["rows"][0]["Действует до"] = "2026-03-31"
    payload2 = tp.to_payload(pkg)
    emp2 = next(e for e in payload2["employees"] if e.get("qualificationWindows"))
    until = datetime(2026, 3, 31)
    expected_hi = int((until + timedelta(days=1) - datetime(1970, 1, 1)).total_seconds() // 60) - 1
    assert emp2["qualificationWindows"]["Полярис-Р200"][0] == [0, expected_hi]


@etalon_needed
def test_роли_экипажа_из_должностей_книги():
    payload = tp.to_payload(tp.load(ETALON))
    roles = {e["id"]: e["skills"] for e in payload["employees"]}
    assert roles["PLR-1009"] == ["Captain"], "командир"
    assert roles["PLR-1008"] == ["FirstOfficer"], "второй пилот"


@etalon_needed
def test_отпуска_становятся_недоступными_днями():
    payload = tp.to_payload(tp.load(ETALON))
    e = next(x for x in payload["employees"] if x["id"] == "PLR-1008")
    assert "2026-09-02" in e["unavailableDays"]
    assert "2026-09-12" in e["unavailableDays"], "правая граница включительно"
    assert "2026-09-13" not in e["unavailableDays"]


def test_нет_очищенных_данных_отказ_с_причиной():
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload({"manifest": {}, "model": {}, "clean_data": None})
    assert "нет очищенных данных" in str(e.value)


def test_неполнота_колонок_названа_поимённо(unpacked: Path):
    data = json.loads((unpacked / "clean-data.json").read_text("utf-8"))
    sheet = data["tables"]["D1"]["Рейсы"]
    sheet["columns"] = [c for c in sheet["columns"] if c != "Время вылета"]
    pkg = {"clean_data": data, "model": {}}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    text = str(e.value)
    assert "Время вылета" in text and "Рейсы" in text


def test_пустая_база_приписки_отказ_с_именем_сотрудника(unpacked: Path):
    """Пустая «База» — не «без базы», а ссылка на аэропорт с кодом «»:
    решатель её не разрешит (500 без причины), а без базы стыковку и
    отдых дома не отличить от внебазовых. Отказ до решателя, с именем."""
    data = json.loads((unpacked / "clean-data.json").read_text("utf-8"))
    rows = data["tables"]["D1"][tp.SHEET_CREW]["rows"]
    rows[0]["База"] = ""
    pkg = tp.load(ETALON, run_gate=False)
    pkg["clean_data"] = data
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert rows[0]["Табельный номер"] in str(e.value) and "База" in str(e.value)
    # Контроль: с базой тот же пакет конвертируется.
    rows[0]["База"] = "PLY"
    assert tp.to_payload(pkg)["employees"]


def test_численность_экипажа_ни_поля_ни_правила_отказ(unpacked: Path):
    """Ни машинного поля (пакет до 1.2), ни распознаваемого правила —
    отказ: состав, выведенный из ничего, сделал бы задачу другой."""
    pkg = tp.load(unpacked, run_gate=False)
    pkg["manifest"].pop("crew_size", None)
    pkg["model"] = {"rules": []}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "численность экипажа не задана" in str(e.value)


@etalon_needed
def test_машинное_поле_численности_сильнее_разбора_образцом():
    """Спецификация 1.2: распознавание — у ПРОИЗВОДИТЕЛЯ. Формулировку
    правила пишет его формализатор, и наши образцы ломались о неё
    дважды; поле манифеста обязано побеждать разбор текста."""
    pkg = tp.load(ETALON, run_gate=False)
    machine = pkg["manifest"]["crew_size"]
    assert machine["value"] == 2 and machine["rule"]
    n, rule = tp.crew_size_from_model(pkg)
    assert (n, rule) == (machine["value"], machine["rule"])

    # СТАРШИНСТВО проверяется прямо, а не через формулировку эталона.
    # Прежняя версия требовала, чтобы текст правила НЕ распознавался
    # образцами, — и держалась на том, как формализатор написал фразу в
    # конкретном прогоне. Формулировка законно сменилась, и проверка
    # покраснела, не найдя дефекта. Здесь текст правила намеренно
    # РАСХОДИТСЯ с машинным полем: если бы читался он, ответ стал бы 9.
    rules = {r["id"]: r for r in pkg["model"]["rules"]}
    rules[machine["rule"]]["statement"] = "crew_size = 9 (пилоты)"
    n2, rule2 = tp.crew_size_from_model(pkg)
    assert (n2, rule2) == (machine["value"], machine["rule"]), \
        "прочитан текст правила вместо машинного поля манифеста"


@pytest.mark.parametrize("value,rule,fragment", [
    (0, "R-1", "негодной численностью"),
    (-2, "R-1", "негодной численностью"),
    ("два", "R-1", "негодной численностью"),
    (True, "R-1", "негодной численностью"),
    (2, "", "без ссылки на правило"),
    (2, None, "без ссылки на правило"),
])
def test_негодное_машинное_поле_численности_отвергается(value, rule, fragment):
    """Машинное поле не освобождает от проверки: численность без
    трассировки или с бессмысленным значением — отказ, а не доверие."""
    pkg = {"manifest": {"crew_size": {"value": value, "rule": rule}},
           "model": {"rules": []}}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.crew_size_from_model(pkg)
    assert fragment in str(e.value)


def test_запасной_разбор_образцом_живёт_для_пакетов_до_1_2(unpacked: Path):
    """Пакеты версии до 1.2 машинного поля не несут — запасной путь
    обязан продолжать работать, иначе прежние пакеты станут нечитаемы."""
    pkg = tp.load(unpacked, run_gate=False)
    pkg["manifest"].pop("crew_size", None)
    pkg["model"] = {"rules": [
        {"id": "R-S3-стр-1-абз-7", "statement": "crew_size = 2 (пилоты)"}]}
    n, rule = tp.crew_size_from_model(pkg)
    assert (n, rule) == (2, "R-S3-стр-1-абз-7")


# ------------------- карта пакета (секция rule_maps, ревизия 1.14)

def test_запасной_разбор_идёт_правилами_карты_пакета():
    """Ревизия 1.14: подсекция rule_maps.crew_size_rules манифеста
    называет правила-носители численности — запасной путь сканирует
    ЕЁ, а не зашитый кортеж. Идентификатор синтетический: зашитой
    координаты с таким именем в коде нет и не будет."""
    pkg = {"manifest": {"rule_maps": {"crew_size_rules": ["R-СИНТ-1"]}},
           "model": {"rules": [
               {"id": "R-СИНТ-1", "statement": "crew_size = 3 (пилоты)"}]}}
    assert tp.crew_size_from_model(pkg) == (3, "R-СИНТ-1")


def test_карта_пакета_сильнее_легаси_кортежа():
    """Пакет с картой судится ТОЛЬКО ею: правило легаси-кортежа в
    модели есть и читается образцом, но карта пакета его не называет
    — отказ, а не тихое чтение зашитой координаты."""
    model = {"rules": [
        {"id": "R-S3-стр-1-абз-7", "statement": "crew_size = 2 (пилоты)"}]}
    pkg = {"manifest": {"rule_maps": {"crew_size_rules": ["R-СИНТ-1"]}},
           "model": model}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.crew_size_from_model(pkg)
    assert "R-СИНТ-1" in str(e.value)
    assert "R-S3-стр-1-абз-7" not in str(e.value)
    # Контроль: тот же пакет БЕЗ секции читается легаси-кортежем.
    pkg2 = {"manifest": {}, "model": model}
    assert tp.crew_size_from_model(pkg2) == (2, "R-S3-стр-1-абз-7")


def test_машинное_поле_численности_сильнее_карты_пакета():
    """Приоритет не меняется: машинное поле crew_size манифеста
    сильнее ЛЮБОГО разбора — по карте пакета тоже."""
    pkg = {"manifest": {
               "crew_size": {"value": 2, "rule": "R-МАШ"},
               "rule_maps": {"crew_size_rules": ["R-СИНТ-1"]}},
           "model": {"rules": [
               {"id": "R-СИНТ-1", "statement": "crew_size = 9 (пилоты)"}]}}
    assert tp.crew_size_from_model(pkg) == (2, "R-МАШ")


@pytest.mark.parametrize("bad", [
    "не список", [], [""], [7], {"R-СИНТ-1": 1},
])
def test_кривая_подсекция_crew_size_rules_отказ_словами(bad):
    pkg = {"manifest": {"rule_maps": {"crew_size_rules": bad}},
           "model": {"rules": [
               {"id": "R-СИНТ-1", "statement": "crew_size = 2 (пилоты)"}]}}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.crew_size_from_model(pkg)
    assert "crew_size_rules" in str(e.value)


def test_источник_численности_называет_карту_пакета():
    """Отчёт конвертации обязан назвать происхождение состава: разбор
    по карте пакета — не то же, что разбор по зашитому кортежу."""
    pkg = {
        "manifest": {"rule_maps": {"crew_size_rules": ["R-СИНТ-1"]}},
        "model": {"rules": [
            {"id": "R-СИНТ-1", "statement": "crew_size = 1 (пилот)"}]},
        "clean_data": {"tables": {"D1": {
            tp.SHEET_FLIGHTS: {
                "columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                            "Прилёт", "Время прилёта"],
                "rows": [{"Номер рейса": "PL1", "Дата": "2026-03-02",
                          "Вылет": "PLY", "Время вылета": "10:00",
                          "Прилёт": "SVG", "Время прилёта": "12:00"}]},
            tp.SHEET_CREW: {
                "columns": ["Табельный номер", "База", "Должность"],
                "rows": [{"Табельный номер": "E1", "База": "PLY",
                          "Должность": "командир"}]},
        }}},
    }
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)
    assert rep["crew_size_rule"] == "R-СИНТ-1"
    assert "карте пакета" in rep["crew_size_source"]


def test_состав_книги_расходится_с_правилом_отказ(unpacked: Path):
    """Правило требует двоих, а в книге одна роль — это вопрос к
    постановке, и конвертер обязан его задать, а не досчитать."""
    pkg = tp.load(unpacked, run_gate=False)
    rows = pkg["clean_data"]["tables"]["D1"]["Экипажи"]["rows"]
    for r in rows:
        r["Должность"] = "командир"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "однозначно не выводится" in str(e.value)


def test_неизвестная_должность_названа(unpacked: Path):
    pkg = tp.load(unpacked, run_gate=False)
    rows = pkg["clean_data"]["tables"]["D1"]["Экипажи"]["rows"]
    rows[0]["Должность"] = "штурман-испытатель"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "неизвестные должности" in str(e.value)
    assert "штурман-испытатель" in str(e.value)


# ------------------------------------------- подпись прогона (МУ-04)

@etalon_needed
def test_отпечаток_пакета_входит_в_подпись_прогона(monkeypatch):
    """Разные постановки — разные подписи. Без этого два прогона по
    РАЗНЫМ пакетам CDPM неотличимы в истории."""
    from webapp import problem_manifest as pm

    monkeypatch.setenv("CREW_TASK_PACKAGE", str(ETALON))
    with_pkg = pm._task_package_fingerprint()
    assert with_pkg is not None
    assert len(with_pkg["fingerprint"]) == 64
    # Номер прогона НЕ пришпиливается: эталоны перевыпускаются, и
    # равенство конкретному «run-0033» ломалось при каждом перевыпуске,
    # не находя при этом ни одного дефекта. Проверяются форма номера и
    # то, что подпись взяла его ИЗ ПАКЕТА, а не придумала.
    import json as _json
    import re as _re
    import zipfile as _zip
    with _zip.ZipFile(ETALON) as z:
        manifest = _json.loads(z.read("manifest.json").decode("utf-8"))
    assert _re.fullmatch(r"run-\d{4}", with_pkg["source_run"]), \
        "номер прогона не в форме run-NNNN: %r" % (with_pkg["source_run"],)
    assert with_pkg["source_run"] == manifest["source_run"]

    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    assert pm._task_package_fingerprint() is None, (
        "прогон без пакета обязан давать честный None, а не пустой хэш")


def test_негодный_пакет_валит_постановку_а_не_проходит_молча(monkeypatch,
                                                             tmp_path):
    from webapp import problem_manifest as pm

    monkeypatch.setenv("CREW_TASK_PACKAGE", str(tmp_path / "нет-такого.zip"))
    with pytest.raises(pm.ManifestError) as e:
        pm._task_package_fingerprint()
    assert "не принят" in str(e.value)


def test_противоречивый_дубль_ключа_отвергается(unpacked: Path):
    """Точный повтор строки сведений не несёт — сворачивается. Повтор
    ключа с ДРУГИМИ данными — вопрос к авиакомпании, и досчитывать его
    конвертер не имеет права."""
    pkg = tp.load(unpacked, run_gate=False)
    rows = pkg["clean_data"]["tables"]["D1"]["Рейсы"]["rows"]
    # Контроль: как есть (точный повтор) — набор строится.
    tp.to_payload(pkg)
    # Тот же ключ, другое время прилёта — расхождение.
    rows[2]["Время прилёта"] = "16:30"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "с РАЗНЫМ" in str(e.value)
    assert "PL101" in str(e.value)


# ------------------- устойчивость к переформулировкам свода

@pytest.mark.parametrize("statement,expected", [
    ("crew_size = 2 (пилоты)", 2),
    ("pilot_count(flight_duty) = 2 (в состав входят два пилота).", 2),
    ("Для каждой полётной смены: |пилоты в экипаже| = 2.", 2),
    ("Экипаж состоит из 2 пилотов", 2),
    ("В экипаж входят 3 пилота", 3),
    ("Численность: 4 пилота на смену", 4),
])
def test_численность_экипажа_читается_из_разных_формулировок(statement,
                                                             expected):
    """Формулировки свода порождает языковая модель, и между прогонами
    они меняются законно: одно правило приходило четырьмя разными
    записями. Идентификатор правила при этом тот же, поэтому образцы
    обязаны держать РАЗНЫЕ записи одного смысла."""
    pkg = {"model": {"rules": [
        {"id": "R-S1-табл-2-строка-3-кол-3", "statement": statement}]}}
    n, rule = tp.crew_size_from_model(pkg)
    assert n == expected
    assert rule == "R-S1-табл-2-строка-3-кол-3"


def test_численность_без_числа_в_тексте_отказ():
    """Контроль широкому образцу: правило без числа не должно давать
    случайного совпадения — это отказ, а не догадка."""
    pkg = {"model": {"rules": [
        {"id": "R-S3-стр-1-абз-7",
         "statement": "Состав экипажа определяется отдельным приказом"}]}}
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.crew_size_from_model(pkg)
    assert "численность экипажа" in str(e.value)


@etalon_needed
def test_тип_судна_берётся_из_книги_когда_она_его_несёт():
    """Книга ревизии v3 несёт колонку «Тип ВС» — правило допуска по типу
    получает данные и перестаёт быть «формализовано, не исполняется»."""
    pkg = tp.load(ETALON, run_gate=False)
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)
    assert rep["aircraft_type_from_book"] is True
    assert rep["rules_without_data"] == []
    assert all(f["aircraftType"] for f in payload["flights"])
    # Тип рейса совпадает с допуском экипажа — иначе правило допуска
    # ложно заблокировало бы каждое назначение.
    types = {f["aircraftType"] for f in payload["flights"]}
    rated = {t for e in payload["employees"] for t in e["typeRatings"]}
    assert types & rated, "типы рейсов и допуски экипажа не пересекаются"


def test_книга_без_колонки_типа_судна_даёт_пустой_тип_и_оговорку(
        unpacked: Path):
    """Старые книги колонки не несут: подставлять умолчание домена
    нельзя — правило допуска стало бы ложно жёстким."""
    pkg = tp.load(unpacked, run_gate=False)
    sheet = pkg["clean_data"]["tables"]["D1"]["Рейсы"]
    sheet["columns"] = [c for c in sheet["columns"] if c != "Тип ВС"]
    for row in sheet["rows"]:
        row.pop("Тип ВС", None)
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)
    assert rep["aircraft_type_from_book"] is False
    assert any("Тип ВС" in s for s in rep["rules_without_data"])
    assert all(f["aircraftType"] == "" for f in payload["flights"])


# --------------------------------------------- набор «мини» (крупнее нано)

ETALON_MINI = (REPO.parent / "handoff" / "2026-08-22-MOST-A" / "etalon-mini"
               / "mini-task-package.zip")

mini_needed = pytest.mark.skipif(
    not ETALON_MINI.exists(),
    reason="эталон «мини» не найден: %s" % ETALON_MINI)


@mini_needed
def test_набор_мини_конвертируется_целиком():
    """Второй эталон крупнее нано и ловит то, чего нано показать не
    может: несколько аэропортов, повтор строки внутри большего
    расписания, состав на два десятка человек.

    Заодно это охрана от возврата закрытого класса: в прежней выгрузке
    очистка вычитала 12 строк из 13 по находке о минимальном разрыве —
    свойстве ПАРЫ рейсов, а не строки, — и «мини» оказывался беднее
    нано. Ноль вычтенных здесь проверяется намеренно.
    """
    pkg = tp.load(ETALON_MINI, run_gate=False)
    assert pkg["manifest"]["spec_version"].startswith("1.")
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)

    # Вычет строки очисткой сам по себе законен (адресная находка
    # блокирующей серьёзности), и запрещать его числом — значит ловить
    # перевыпуск набора вместо дефекта. Значимо другое: вычет не должен
    # трогать листы, из которых строится набор решателя.
    removed = ((pkg.get("clean_data") or {}).get("cleaning") or {}).get("removed") or []
    carried = {"Рейсы", "Экипажи", "Допуски", "Отпуска"}
    hit = [r for r in removed if str(r.get("sheet")) in carried]
    assert not hit, ("очистка вычла строки из листов набора решателя: %s" % hit)
    assert rep["flights"] >= 10, "мини обязан быть крупнее нано"
    assert rep["employees"] >= 20
    assert rep["seats"] == rep["flights"] * 2
    assert rep["seat_roles"] == ["Captain", "FirstOfficer"]
    assert rep["crew_size_source"].startswith("машинное поле")
    assert rep["rules_without_data"] == []
    assert rep["airports"] >= 5, "несколько станций, а не одна пара"

    # Набор пригоден решателю: у каждого места есть свой рейс, личности
    # рейсов уникальны (повтор личности решатель отвергает намеренно).
    legs = [f["flightNumber"] for f in payload["flights"]]
    assert len(legs) == len(set(legs)), "личности рейсов обязаны быть уникальны"
    assert all(a["flight"] in set(legs) for a in payload["flightAssignments"])


# ------------------------------------------- целевые функции (этап Б)


@etalon_needed
def test_целевые_эталона_читаются_машинными_полями():
    """Направление и метрика берутся ПОЛЯМИ, а не разбором прозы."""
    pkg = tp.load(ETALON, run_gate=False)
    ros = tp.objectives(pkg, "rostering")
    assert len(ros) == 1
    o = ros[0]
    assert o["direction"] == "minimize"
    assert o["metric"] == "credit_hours_spread"
    # Проза доезжает целой — она человеку для сверки, не машине.
    assert o["statement"]
    par = tp.objectives(pkg, "pairing")
    assert par and par[0]["metric"] == "total_pairing_cost"


@etalon_needed
def test_целевая_ростеринга_эталона_проходит_гейт():
    pkg = tp.load(ETALON, run_gate=False)
    oid = tp.objectives(pkg, "rostering")[0]["id"]
    got = tp.check_objective(pkg, "rostering", oid)
    assert got["metric"] == "credit_hours_spread"


@etalon_needed
def test_стоимость_пейрингов_не_исполнима_ростерингом():
    """Метрика зоны пейринга не становится целевой ростеринга.

    Похожая величина на объединённой модели была бы правдоподобной и
    неверной — отказ обязан быть с названием зоны.
    """
    pkg = tp.load(ETALON, run_gate=False)
    oid = tp.objectives(pkg, "pairing")[0]["id"]
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.check_objective(pkg, "rostering", oid)
    # Целевая пейринга в зоне ростеринга даже не объявлена.
    assert "не объявлена пакетом" in str(e.value)
    # А сама метрика — не применима задаче, если целевую подсунуть в зону.
    assert "total_pairing_cost" in tp.METRICS
    assert tp.METRICS["total_pairing_cost"] == ("pairing",)


@etalon_needed
def test_режим_оптимизации_без_целевой_отвергается():
    pkg = tp.load(ETALON, run_gate=False)
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.check_objective(pkg, "rostering", "")
    assert "objective_id" in str(e.value)


@etalon_needed
def test_метрика_вне_словаря_не_заменяется_похожей(tmp_path: Path):
    """Словарь потребителя может быть старее пакета — тогда отказ."""
    pkg = tp.load(ETALON, run_gate=False)
    zone = pkg["objective"]["rostering"]
    zone["objectives"][0]["metric"] = "crew_happiness_index"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.check_objective(pkg, "rostering",
                           zone["objectives"][0]["id"])
    assert "вне известного словаря" in str(e.value)


@mini_needed
def test_целевые_мини_совпадают_по_метрикам_с_нано():
    """Наборы разные, метрики те же — словарь работает, а не совпадение."""
    pkg = tp.load(ETALON_MINI, run_gate=False)
    assert tp.objectives(pkg, "rostering")[0]["metric"] == "credit_hours_spread"
    assert tp.objectives(pkg, "pairing")[0]["metric"] == "total_pairing_cost"
    oid = tp.objectives(pkg, "rostering")[0]["id"]
    assert tp.check_objective(pkg, "rostering", oid)["direction"] == "minimize"


# ---------------- обязательства покрытия против транспорта (2026-08-27)

def _pkg_с_транспортом() -> dict:
    """Книга с плановым интервалом и колонками «Перевозчик»/«Перегон»:
    один рейс-обязательство, один чужой перегон, один свой вне окна."""
    cols = ["Номер рейса", "Дата", "Вылет", "Время вылета",
            "Прилёт", "Время прилёта", "Перевозчик", "Перегон"]
    return {
        "manifest": {"crew_size": {"value": 1, "rule": "R-TEST"},
                     "has_data": True},
        "model": {"rules": []},
        "clean_data": {"tables": {"D1": {
            "Свойства": {
                "columns": ["ключ", "значение"],
                "rows": [
                    {"ключ": "planSince",
                     "значение": "2030-01-01T00:00:00"},
                    {"ключ": "planUntil",
                     "значение": "2030-01-31T23:59:00"},
                ]},
            tp.SHEET_FLIGHTS: {
                "columns": cols,
                "rows": [
                    {"Номер рейса": "ZZ100", "Дата": "2030-01-05",
                     "Вылет": "HUB", "Время вылета": "10:00",
                     "Прилёт": "DXB", "Время прилёта": "15:00",
                     "Перевозчик": "ZZ", "Перегон": "false"},
                    {"Номер рейса": "YY900", "Дата": "2030-01-06",
                     "Вылет": "DXB", "Время вылета": "08:00",
                     "Прилёт": "HUB", "Время прилёта": "12:00",
                     "Перевозчик": "YY", "Перегон": "true"},
                    {"Номер рейса": "ZZ200", "Дата": "2030-02-02",
                     "Вылет": "HUB", "Время вылета": "09:00",
                     "Прилёт": "DXB", "Время прилёта": "14:00",
                     "Перевозчик": "ZZ", "Перегон": "false"},
                ]},
            tp.SHEET_CREW: {
                "columns": ["Табельный номер", "База", "Должность"],
                "rows": [{"Табельный номер": "E1", "База": "HUB",
                          "Должность": "командир"}]},
        }}},
    }


def test_обязательство_только_свой_рейс_в_плановом_интервале():
    pkg = _pkg_с_транспортом()
    payload = tp.to_payload(pkg)
    by = {f["flightNumber"]: f for f in payload["flights"]}
    # Транспорт ОСТАЁТСЯ в наборе — доступен пассажиром.
    assert len(payload["flights"]) == 3
    ob = by["ZZ100|2030-01-05T10:00:00"]
    assert "transportOnly" not in ob, (
        "обязательство обязано остаться чистой записью прежней формы")
    assert by["YY900|2030-01-06T08:00:00"]["transportOnly"] is True
    assert by["ZZ200|2030-02-02T09:00:00"]["transportOnly"] is True, (
        "свой рейс вне планового интервала — транспорт")
    # Рабочие места — только у обязательств (словарь судьи).
    seats = {a["flight"] for a in payload["flightAssignments"]}
    assert seats == {"ZZ100|2030-01-05T10:00:00"}
    cov = tp.conversion_report(pkg, payload)["coverage_obligations"]
    assert cov["obligations"] == 1 and cov["transport"] == 2
    assert cov["transport_by_reason"]["колонка «Перегон»"] == 1
    assert cov["transport_by_reason"]["вылет вне планового интервала"] == 1
    assert cov["transport_carriers"] == {"YY": 1, "ZZ": 1}


def test_смешение_перевозчиков_в_обязательствах_отказ():
    pkg = _pkg_с_транспортом()
    rows = pkg["clean_data"]["tables"]["D1"][tp.SHEET_FLIGHTS]["rows"]
    rows.append({"Номер рейса": "XX7", "Дата": "2030-01-07",
                 "Вылет": "HUB", "Время вылета": "11:00",
                 "Прилёт": "DXB", "Время прилёта": "16:00",
                 "Перевозчик": "XX", "Перегон": "false"})
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    text = str(e.value)
    assert "смешаны разные перевозчики" in text
    assert "ZZ (1)" in text and "XX (1)" in text


def test_все_рейсы_транспорт_отказ():
    pkg = _pkg_с_транспортом()
    for r in pkg["clean_data"]["tables"]["D1"][tp.SHEET_FLIGHTS]["rows"]:
        r["Перегон"] = "true"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "обязательств покрытия нет" in str(e.value)


def test_нечитаемый_плановый_интервал_отказ():
    pkg = _pkg_с_транспортом()
    rows = pkg["clean_data"]["tables"]["D1"]["Свойства"]["rows"]
    rows[0]["значение"] = "октябрь"
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "нечитаем" in str(e.value)


def test_книга_без_перегона_и_свойств_прежнее_поведение():
    pkg = _pkg_с_транспортом()
    d1 = pkg["clean_data"]["tables"]["D1"]
    del d1["Свойства"]
    sheet = d1[tp.SHEET_FLIGHTS]
    sheet["columns"] = [c for c in sheet["columns"]
                        if c not in ("Перевозчик", "Перегон")]
    for r in sheet["rows"]:
        r.pop("Перевозчик", None)
        r.pop("Перегон", None)
    payload = tp.to_payload(pkg)
    assert all("transportOnly" not in f for f in payload["flights"])
    assert len(payload["flightAssignments"]) == len(payload["flights"])


# ------------- H14-WIRE-2: объявленный горизонт учёта в наборе решателя
#
# Стык плана единой версии 06.09.2026: решатель (K-4/K-32) читает факт
# `window_horizon` {start_inclusive, end_exclusive, source}; без него
# область выводится из программы рейсов (source=inferred), отчёт
# ночёвок несёт declared_accounting_horizon_unknown, а вход
# сертификатора при этом даёт ОБЪЯВЛЕННОЕ окно из planSince/planUntil —
# сверка цены расходилась на каждом прогоне (INCOMPARABLE).

KOTLIN_SCHEDULE = (REPO / "kotlin-solver" / "src" / "main" / "kotlin" / "org"
                   / "acme" / "flighcrewscheduling" / "domain"
                   / "FlightCrewSchedule.kt")

#: Ключи поля — ровно те, что у класса AccountingWindow решателя.
WINDOW_KEYS = {"start_inclusive", "end_exclusive", "source"}


def _manifest_horizon(start="2030-01-01T00:00", end_incl="2030-01-31T23:59",
                      end_excl="2030-02-01T00:00", tz=None) -> dict:
    """Раздел manifest.horizon в форме производителя (спецификация
    §1.18.1, cdpm/history_section.declared_horizon)."""
    return {"start": start, "end_inclusive": end_incl,
            "end_exclusive": end_excl, "scale": "minute", "tz": tz,
            "source": {"sheet": "Свойства",
                       "keys": ["planSince", "planUntil"], "sid": "D1"}}


def test_H14_WIRE_2_форма_поля_совпадает_с_решателем():
    """Контракт провода судится по исходнику решателя, а не по памяти:
    ключ набора `window_horizon` и три поля класса AccountingWindow."""
    if not KOTLIN_SCHEDULE.exists():
        pytest.skip("исходник решателя не рядом: %s" % KOTLIN_SCHEDULE)
    src = KOTLIN_SCHEDULE.read_text(encoding="utf-8")
    assert 'JsonProperty("window_horizon")' in src
    assert "class AccountingWindow" in src
    for key in WINDOW_KEYS:
        assert "var %s" % key in src, "у решателя нет поля %s" % key


def test_H14_WIRE_2_горизонт_из_manifest_horizon():
    """Пакет 1.18 с разделом manifest.horizon: start и end_exclusive
    едут в набор КАК ЕСТЬ (минуту прибавил производитель, читатель не
    прибавляет повторно), tz=null не мешает; источник — manifest.horizon.
    Вход сертификатора берёт то же поле копией — обе стороны сверки
    цены видят одно окно."""
    from webapp import solution_export as se
    pkg = _pkg_с_транспортом()
    pkg["manifest"]["spec_version"] = "1.18"
    pkg["manifest"]["horizon"] = _manifest_horizon()
    payload = tp.to_payload(pkg)
    assert payload["window_horizon"] == {
        "start_inclusive": "2030-01-01T00:00",
        "end_exclusive": "2030-02-01T00:00",
        "source": "manifest.horizon"}
    assert set(payload["window_horizon"]) == WINDOW_KEYS
    assert se.declared_window(payload) == payload["window_horizon"]
    assert tp.conversion_report(pkg, payload)["window_horizon"] == \
        payload["window_horizon"]
    # Раздел манифеста — объявленный факт: границы не на полных сутках
    # — показание производителя, не замок читателя (§1.18.1).
    pkg["manifest"]["horizon"] = _manifest_horizon(
        start="2030-01-01T06:00", end_incl="2030-01-31T17:59",
        end_excl="2030-01-31T18:00", tz="Europe/Berlin")
    assert tp.to_payload(pkg)["window_horizon"] == {
        "start_inclusive": "2030-01-01T06:00",
        "end_exclusive": "2030-01-31T18:00",
        "source": "manifest.horizon"}


def test_H14_WIRE_2_горизонт_из_planSince_planUntil_без_раздела():
    """Пакет без раздела horizon, плановое окно книги из целых суток:
    поле строится из planSince/planUntil тем же правилом, что вход
    сертификатора (planUntil включительный до минуты → +1 минута)."""
    from webapp import solution_export as se
    pkg = _pkg_с_транспортом()
    assert "horizon" not in pkg["manifest"]
    payload = tp.to_payload(pkg)
    assert payload["window_horizon"] == {
        "start_inclusive": "2030-01-01T00:00:00",
        "end_exclusive": "2030-02-01T00:00:00",
        "source": tp.WINDOW_SOURCE_PLAN_PROPS}
    assert set(payload["window_horizon"]) == WINDOW_KEYS
    # Правило одно на обе стороны: вход сертификатора, выводя окно сам
    # из _coverage набора, получает ровно то же поле.
    without = dict(payload)
    del without["window_horizon"]
    assert se.declared_window(without) == payload["window_horizon"]
    assert se.declared_window(payload) == payload["window_horizon"]


def test_H14_WIRE_2_окно_не_из_целых_суток_или_без_свойств_поля_нет():
    """Плановое окно не из целых суток, перевёрнутое либо отсутствующее
    — поля нет: честно «не объявлен», подстановки суток не бывает; вход
    сертификатора при этом тоже даёт null."""
    from webapp import solution_export as se
    pkg = _pkg_с_транспортом()
    rows = pkg["clean_data"]["tables"]["D1"]["Свойства"]["rows"]
    rows[0]["значение"] = "2030-01-01T06:00:00"
    payload = tp.to_payload(pkg)
    assert "window_horizon" not in payload
    assert se.declared_window(payload) is None
    assert tp.conversion_report(pkg, payload)["window_horizon"] is None
    # Книга без листа «Свойства» — поля нет.
    pkg2 = _pkg_с_транспортом()
    del pkg2["clean_data"]["tables"]["D1"]["Свойства"]
    for r in pkg2["clean_data"]["tables"]["D1"][tp.SHEET_FLIGHTS]["rows"]:
        r["Перегон"] = "false"
        r["Перевозчик"] = "ZZ"
    assert "window_horizon" not in tp.to_payload(pkg2)
    # Перевёрнутое окно — тоже нет (в обязательствах при этом ни одного
    # рейса — отказ по покрытию раньше поля; судится сам помощник).
    assert tp.declared_window_horizon(
        {}, "2030-01-31T00:00:00", "2030-01-01T23:59:00") is None
    assert tp.declared_window_horizon({}, None, None) is None


@pytest.mark.parametrize("horizon,fragment", [
    (_manifest_horizon(start="2030-02-01T00:00"), "перевёрнут"),
    (_manifest_horizon(end_excl="февраль"), "нечитаем"),
    ({"start": "2030-01-01T00:00", "end_inclusive": "2030-01-31T23:59"},
     "end_exclusive"),
    ("2030-01", "не словарь"),
])
def test_H14_WIRE_2_негодный_manifest_horizon_отказ_словами(horizon,
                                                             fragment):
    """Объявленный, но негодный горизонт — дефект пакета, а не повод
    молча считать окно «не объявленным» (неизвестное ≠ отсутствие)."""
    pkg = _pkg_с_транспортом()
    pkg["manifest"]["horizon"] = horizon
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "manifest.horizon" in str(e.value) and fragment in str(e.value)


# ------------- даты и допуски: неизвестное ≠ отсутствие ограничения (A7D)
#
# Внешний аудит 04.09.2026, находка A7 (дешёвая часть, A7D). Класс
# ошибки: соседние дато-колонки одного пакета судились разными
# правилами — «Дата» рейса при нечитаемости отказывала, нечитаемая
# «Действует до» молча давала ОТКРЫТОЕ окно, нечитаемый или
# перевёрнутый отпуск молча пропадал. Стенды ниже воспроизводили
# дефект на коде fde9c5f (падали) и зелены после лечения.

def _pkg_A7D(*, flights=None, quals=None, vacations=None,
             qual_columns=("Табельный номер", "Тип ВС", "Действует до"),
             crew=None) -> dict:
    """Синтетический пакет: один рейс, один командир, допуск, отпуск.
    Строится в тесте — эталонов в свежем клоне нет (условие МУ-07)."""
    tables = {
        tp.SHEET_FLIGHTS: {
            "columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                        "Прилёт", "Время прилёта", "Тип ВС"],
            "rows": flights if flights is not None else [
                {"Номер рейса": "PL1", "Дата": "2026-03-02",
                 "Вылет": "PLY", "Время вылета": "10:00",
                 "Прилёт": "SVG", "Время прилёта": "12:00",
                 "Тип ВС": "Полярис-Р200"}]},
        tp.SHEET_CREW: {
            "columns": ["Табельный номер", "База", "Должность"],
            "rows": crew if crew is not None else [
                {"Табельный номер": "E1", "База": "PLY",
                 "Должность": "командир"}]},
        tp.SHEET_QUALS: {
            "columns": list(qual_columns),
            "rows": quals if quals is not None else [
                {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                 "Действует до": "2027-03-31"}]},
        tp.SHEET_VACATIONS: {
            "columns": ["Табельный номер", "С", "По"],
            "rows": vacations if vacations is not None else [
                {"Табельный номер": "E1", "С": "2026-03-10",
                 "По": "2026-03-12"}]},
    }
    return {
        "manifest": {"crew_size": {"value": 1, "rule": "R-TEST"},
                     "has_data": True},
        "model": {"rules": []},
        "clean_data": {"tables": {"D1": tables}},
    }


def _min(iso: str) -> int:
    from datetime import datetime
    return int((datetime.fromisoformat(iso)
                - datetime(1970, 1, 1)).total_seconds() // 60)


def _holds(windows: list[list[int]], dep_iso: str, arr_iso: str) -> bool:
    """Правило решателя дословно (Employee.holdsQualification): весь
    рейс лежит в ОДНОМ окне. Зеркало здесь — чтобы стенд говорил о
    том же, о чём судит решатель, а не о форме списка."""
    dep, arr = _min(dep_iso), _min(arr_iso)
    return any(w[0] <= dep and arr <= w[1] for w in windows)


def test_A7D_нечитаемая_дата_допуска_отказ_с_адресом():
    """Нечитаемое непустое «Действует до» — отказ с листом, строкой и
    колонкой, как у даты рейса. На коде до лечения — ОТКРЫТОЕ окно:
    просроченный допуск с опечаткой в дате становился бессрочным."""
    pkg = _pkg_A7D(quals=[
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует до": "2027-03-31"},
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует до": "31/03/2027"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    text = str(e.value)
    assert "«Допуски»" in text and "строка данных 2" in text
    assert "«Действует до»" in text and "31/03/2027" in text


def test_A7D_пустой_срок_допуска_по_прежнему_открытое_окно():
    """Документированное поведение сохраняется: ПУСТОЕ «Действует до» —
    окно открытое («не указан» ≠ «просрочен»). Отказ — только на
    НЕПУСТОМ нечитаемом значении."""
    pkg = _pkg_A7D(quals=[{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                          "Действует до": ""}])
    payload = tp.to_payload(pkg)
    emp = payload["employees"][0]
    assert emp["qualificationWindows"]["Полярис-Р200"] == [[0, tp.QUAL_OPEN_END_MIN]]
    rep = tp.conversion_report(pkg, payload)
    assert rep["qualification_windows"]["open_until"] == 1
    assert rep["qualification_windows"]["dated_until"] == 0


def test_A7D_перевёрнутый_отпуск_отказ_с_адресом():
    """«По» раньше «С» — отказ с адресом строки. На коде до лечения
    цикл дней не выполнялся ни разу: отпуск исчезал молча, сотрудник
    становился «доступным»."""
    pkg = _pkg_A7D(vacations=[
        {"Табельный номер": "E1", "С": "2026-03-10", "По": "2026-03-12"},
        {"Табельный номер": "E1", "С": "2026-03-20", "По": "2026-03-15"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    text = str(e.value)
    assert "«Отпуска»" in text and "строка данных 2" in text
    assert "перевёрнут" in text and "E1" in text


def test_A7D_нечитаемая_дата_отпуска_отказ_с_адресом():
    """Нечитаемая граница отпуска — отказ с адресом и колонкой. На коде
    до лечения — `continue` без счёта: отпуск пропадал."""
    pkg = _pkg_A7D(vacations=[
        {"Табельный номер": "E1", "С": "10 марта", "По": "2026-03-12"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    text = str(e.value)
    assert "«Отпуска»" in text and "строка данных 1" in text
    assert "колонка «С»" in text and "10 марта" in text


def test_A7D_пустая_граница_отпуска_отказ():
    """Отпуск без конца (или без начала) постановкой не описан:
    подставлять границу нельзя, отказ словами."""
    pkg = _pkg_A7D(vacations=[
        {"Табельный номер": "E1", "С": "2026-03-10", "По": ""}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "пуста граница «По»" in str(e.value)


def test_A7D_отпуск_без_владельца_отказ():
    pkg = _pkg_A7D(vacations=[
        {"Табельный номер": "", "С": "2026-03-10", "По": "2026-03-12"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "без табельного номера" in str(e.value)
    assert "«Отпуска»" in str(e.value)


def test_A7D_дата_в_форме_CDPM_дд_мм_гггг_читается_везде():
    """CDPM замораживает ячейки-даты Excel как «дд.мм.гггг»
    (cdpm.data_freeze._cell_str), а его гейт данных принимает обе
    формы (cdpm.data_checks.parse_date). На коде до лечения конвертер
    читал только ISO: дата рейса отказывала, «Действует до» становилось
    открытым окном, отпуск пропадал — при одном и том же входе."""
    pkg = _pkg_A7D(
        flights=[{"Номер рейса": "PL1", "Дата": "02.03.2026",
                  "Вылет": "PLY", "Время вылета": "10:00",
                  "Прилёт": "SVG", "Время прилёта": "12:00",
                  "Тип ВС": "Полярис-Р200"}],
        quals=[{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                "Действует до": "31.03.2027"}],
        vacations=[{"Табельный номер": "E1", "С": "10.03.2026",
                    "По": "12.03.2026"}])
    payload = tp.to_payload(pkg)
    assert payload["flights"][0]["departureUTCDateTime"] == "2026-03-02T10:00:00"
    emp = payload["employees"][0]
    assert emp["unavailableDays"] == ["2026-03-10", "2026-03-11", "2026-03-12"]
    window = emp["qualificationWindows"]["Полярис-Р200"][0]
    assert window == [0, _min("2027-04-01T00:00:00") - 1]
    # Обе формы — один результат.
    assert tp.to_payload(_pkg_A7D())["employees"][0]["qualificationWindows"] \
        == emp["qualificationWindows"]


def test_A7D_момент_вместо_даты_в_дато_колонке_отказ():
    """Хвост времени в дато-колонке допустим только нулевой: «31.03.2027
    12:00» в «Действует до» — не сутки, а момент, и читать его как конец
    суток значило бы продлить допуск на полдня молча."""
    pkg = _pkg_A7D(quals=[{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                          "Действует до": "31.03.2027 12:00"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "момент времени, а не дата" in str(e.value)
    # Контроль: нулевое время (так пишет JSON-выгрузка) — это сутки.
    pkg = _pkg_A7D(quals=[{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                          "Действует до": "2027-03-31T00:00:00"}])
    window = tp.to_payload(pkg)["employees"][0]["qualificationWindows"]["Полярис-Р200"][0]
    assert window[1] == _min("2027-04-01T00:00:00") - 1


def test_A7D_допуск_с_завтрашней_даты_не_разрешает_сегодняшний_рейс():
    """Приёмка плана A7: «Действует с» — нижняя граница окна. На коде
    до лечения начало окна всегда было 0, и допуск, выданный завтра,
    разрешал сегодняшний рейс. Окна судятся по сотруднику, типу и
    интервалу; несколько строк одного допуска — интервал с разрывами."""
    cols = ("Табельный номер", "Тип ВС", "Действует с", "Действует до")
    pkg = _pkg_A7D(qual_columns=cols, quals=[
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует с": "2026-03-03", "Действует до": "2027-03-31"}])
    windows = tp.to_payload(pkg)["employees"][0]["qualificationWindows"]["Полярис-Р200"]
    assert windows == [[_min("2026-03-03T00:00:00"), _min("2027-04-01T00:00:00") - 1]]
    assert not _holds(windows, "2026-03-02T10:00:00", "2026-03-02T12:00:00"), \
        "допуск с завтрашней даты разрешил сегодняшний рейс"
    assert _holds(windows, "2026-03-03T10:00:00", "2026-03-03T12:00:00")

    # Разрыв: допуск до 01.03 и снова с 03.03 — рейс 02.03 не покрыт
    # ни одним окном, рейсы по краям покрыты; рейс через границу окна
    # (вылет в окне, прилёт вне) не покрыт.
    pkg = _pkg_A7D(qual_columns=cols, quals=[
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует с": "", "Действует до": "2026-03-01"},
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует с": "2026-03-03", "Действует до": ""}])
    payload = tp.to_payload(pkg)
    emp = payload["employees"][0]
    windows = emp["qualificationWindows"]["Полярис-Р200"]
    assert len(windows) == 2 and emp["typeRatings"] == ["Полярис-Р200"]
    assert _holds(windows, "2026-03-01T10:00:00", "2026-03-01T12:00:00")
    assert not _holds(windows, "2026-03-02T10:00:00", "2026-03-02T12:00:00")
    assert _holds(windows, "2026-03-03T10:00:00", "2026-03-03T12:00:00")
    assert not _holds(windows, "2026-03-01T22:00:00", "2026-03-02T01:00:00")
    qw = tp.conversion_report(pkg, payload)["qualification_windows"]
    assert (qw["dated_until"], qw["open_until"], qw["dated_from"]) == (1, 1, 1)


def test_A7D_перевёрнутое_окно_допуска_отказ():
    cols = ("Табельный номер", "Тип ВС", "Действует с", "Действует до")
    pkg = _pkg_A7D(qual_columns=cols, quals=[
        {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
         "Действует с": "2027-04-01", "Действует до": "2027-03-31"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "перевёрнуто" in str(e.value) and "строка данных 1" in str(e.value)


def test_A7D_допуск_без_владельца_или_типа_отказ():
    """Прежде строка допуска без табельного номера или типа молча
    пропускалась (continue)."""
    pkg = _pkg_A7D(quals=[{"Табельный номер": "E1", "Тип ВС": "",
                          "Действует до": "2027-03-31"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "«Допуски»" in str(e.value) and "без владельца или без предмета" in str(e.value)


def test_A7D_ограничение_чужого_сотрудника_отклонено_со_счётом():
    """Ограничение, чей владелец не на листе «Экипажи», перенести
    некуда — оно ЯВНО отклонено с адресом и причиной, счёт в выдаче
    конвертера. Прежде такие строки исчезали без следа."""
    pkg = _pkg_A7D(
        vacations=[
            {"Табельный номер": "E1", "С": "2026-03-10", "По": "2026-03-12"},
            {"Табельный номер": "E9", "С": "2026-03-10", "По": "2026-03-12"}],
        quals=[
            {"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
             "Действует до": "2027-03-31"},
            {"Табельный номер": "E9", "Тип ВС": "Полярис-Р200",
             "Действует до": "2027-03-31"}])
    payload = tp.to_payload(pkg)
    rep = tp.conversion_report(pkg, payload)
    assert rep["constraints_declined"] == 2
    rows = rep["constraints_declined_rows"]
    assert {r["constraint"] for r in rows} == {"отпуск", "допуск"}
    assert all(r["owner"] == "E9" and "«Экипажи»" in r["why"] for r in rows)
    assert any("«Отпуска», строка данных 2" in r["where"] for r in rows)
    assert rep["constraints_carried"]["vacations"] == {"rows": 2, "carried": 1, "days": 6}
    assert rep["constraints_carried"]["qualifications"]["carried"] == 1
    # Перенесённое — на месте.
    assert payload["employees"][0]["unavailableDays"] == [
        "2026-03-10", "2026-03-11", "2026-03-12"]


def test_A7D_нечитаемая_дата_рейса_отказ_с_адресом():
    """Даты рейсов отказывали и прежде; теперь — тем же разборщиком и
    с тем же адресом, что у допусков и отпусков."""
    pkg = _pkg_A7D(flights=[{"Номер рейса": "PL1", "Дата": "2 марта",
                             "Вылет": "PLY", "Время вылета": "10:00",
                             "Прилёт": "SVG", "Время прилёта": "12:00"}])
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.to_payload(pkg)
    assert "«Рейсы», строка данных 1, колонка «Дата»" in str(e.value)


@pytest.mark.parametrize("value,expect", [
    ("", None), (None, None), ("  ", None),
    ("02.03.2026", "2026-03-02"), ("2026-03-02", "2026-03-02"),
    ("2026-03-02T00:00:00", "2026-03-02"), ("02.03.2026 00:00", "2026-03-02"),
])
def test_A7D_один_разборщик_дат_обе_канонические_формы(value, expect):
    got = tp.parse_package_date(value, sheet="Отпуска", row=1, column="С")
    assert (got.date().isoformat() if got else None) == expect


@pytest.mark.parametrize("value", [
    "31/03/2027", "2026-3-2", "31.02.2026", "2026-02-30", "март",
    "02.03.2026 09:30", "2026-03-02T09:30",
])
def test_A7D_один_разборщик_дат_нечитаемое_отказ(value):
    with pytest.raises(tp.TaskPackageRefused) as e:
        tp.parse_package_date(value, sheet="Отпуска", row=3, column="По")
    assert "лист «Отпуска», строка данных 3, колонка «По»" in str(e.value)
