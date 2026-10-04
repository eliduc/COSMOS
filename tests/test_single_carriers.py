# -*- coding: utf-8 -*-
"""Сторож «один носитель перечней семей и форм».

09.09.2026 первая семья, пришедшая конвейером пополнения
(duty_series_limit, каталог 0.4.0), сломала сразу четыре места: они
держали перечень семей своими руками, семьи в перечне не было — и
сертификат молча оставлял правило непроверенным. Носителем стал КОД
ПРОВЕРОК (`vmp/run/logic_checks.py`: FAMILY_FORMS, _EXECUTORS) и
ТАБЛИЦЫ РЕШАТЕЛЯ, прочитанные из исходников Kotlin
(`vmp/kotlin_tables.py`: HISTORY_FIT_FAMILIES, FAMILY_TO_CONSTRAINT).

Сторож ищет в боевом коде строковые литералы, РАВНЫЕ идентификатору
семьи каталога или имени формы решателя, и требует, чтобы каждое такое
место было названо: либо носитель, либо законное адресное употребление
(обоснование строкой), либо известный долг с датой. Новое место
литерала — красный сторож: перечень заводится не молча.

Разбор находок 10.09.2026 (таблица в отчёте; долги — ниже, словарём
DEBTS): законными признаны АДРЕСНЫЕ употребления — ветка исполнителя
одной семьи, таблица «семья → колонки листа», отказ словами про
конкретное правило, омонимы (ключ раздела времени пакета `time_scale`).
Долгами были названы восемь рукописных перечней в шести файлах: они и
есть класс ошибки 09.09.

Починка 10.09.2026 (пакет «один носитель перечней»): шесть перечней
закрыты — их множества отдаёт носитель. Мягкие семьи части «Пейринг» и
единицу их меры даёт запись каталога (`vmp.chain_price.soft_families`,
признак «kind == SOFT и зона PAIRING | BOTH»); ключи построителя у этих
семей — опись исполнителей `vmp.pairing_input.SOFT_EXECUTION`, и
ведомость исполнения читает её же вместо своего зеркала; семьи событий
ночи — запись каталога с единицей нарушения `night`
(`vmp.penalty_reconcile.night_event_families`); «строка измерена, а не
посчитана» — сам сертификат (`measured_rules`), а не перечень
семей-мер; семьи, читающие историю сотрудника, — поле записи каталога
`reads_history` (`vmp.history_assumption.history_families`, сторожа —
tests/test_audit_a7_history.py). Сторожа остальных носителей —
tests/vmp/test_family_carriers.py.

Два перечня сертификатора (PEOPLE_FAMILIES, WORLD_PARAM_FAMILIES) в
долгах НЕ ОСТАЛИСЬ и в носители не переведены: разбор 10.09.2026 показал,
что они пишутся рукой ПО ЗАМЫСЛУ — это свидетели закона паритета (ниже,
в LEGAL).

ДОЛГОВ БОЛЬШЕ НЕТ (11.09.2026, каталог 0.5.0). Последними оставались
ЧЕТЫРЕ СЕМЬИ ГРАНИЦЫ ГОРИЗОНТА в `vmp/history_assumption.py`: историю у
них читает не форма правила, а сама мера, и признака «читает историю» у
каталога 0.4.0 не было ни в записи, ни в таблицах решателя — носителя не
существовало. Выпуск 0.5.0 завёл поле записи семьи `reads_history
{need, kind}`, и перечень снят: его отдаёт запись. Словарь DEBTS
оставлен ПУСТЫМ намеренно — это место, куда долг заводится словами и
датой, если появится снова.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from template_catalog import reader                      # noqa: E402
from vmp.run import logic_checks as lgc                  # noqa: E402

#: Боевой код, который сторож читает. Тесты, стенды open-bench, cdpm и
#: руководства не входят: у них своя жизнь и свои свидетели.
AREAS = ("vmp", "webapp", "catalog_service", "template_catalog")

#: Файлы-НОСИТЕЛИ: перечень семей и форм здесь и обязан жить.
CARRIER_FILES = {
    "vmp/run/logic_checks.py":
        "носитель форм и исполнителей: FAMILY_FORMS, _EXECUTORS",
    "vmp/kotlin_tables.py":
        "носитель таблиц решателя (читает исходники Kotlin)",
}

#: Носитель — словами, для отказа сторожа.
CARRIER_WORDS = ("носитель — logic_checks.FAMILY_FORMS / _EXECUTORS / "
                 "таблицы решателя")

#: Законные адресные употребления: (файл, литерал) → обоснование.
#: Адресное — это ветка, таблица или отказ ПРО ЭТУ СЕМЬЮ, а не перечень
#: семей: новая семья каталога такое место не затрагивает.
LEGAL = {
    ("vmp/run/fit_checks.py", "crew_composition"):
        "ветка исполнителя посадки: счёт состава группы по листу",
    ("vmp/run/fit_checks.py", "duty_limits"):
        "ветка исполнителя посадки: пределы смены (размах, посадки, дни)",
    ("vmp/run/fit_checks.py", "rest_floors"):
        "ветка исполнителя посадки: полы отдыха дома и вне базы",
    ("vmp/run/fit_checks.py", "connection_windows"):
        "ветка исполнителя посадки: минимальная стыковка",
    ("vmp/run/fit_checks.py", "span_accumulators"):
        "ветка исполнителя посадки: месячный блок",
    ("vmp/run/fit_checks.py", "fleet_exclusions"):
        "ветка исполнителя посадки: допуск на тип судна",
    ("vmp/run/fit_checks.py", "time_scale"):
        "адресное чтение смещения пояса базы у правила шкалы времени",

    ("vmp/run/roster_certify.py", "layover_penalty"):
        "ветка исполнителя мягкой меры: ночёвка вне базы",
    ("vmp/run/roster_certify.py", "calendar_windows"):
        "ветка исполнителя мягкой меры: ночёвка в день окна",
    ("vmp/run/roster_certify.py", "duty_extension_penalty"):
        "ветка исполнителя мягкой меры: смена сверх порога",
    ("vmp/run/roster_certify.py", "balanced_workload"):
        "ветка исполнителя мягкой меры: разброс кредитных часов",

    ("vmp/stand_adapters.py", "calendar_windows"):
        "адресный переходник стенда: дни окна из мира в раздел пакета; "
        "семья без переходника — отказ словами, не молчание",
    ("vmp/stand_adapters.py", "duty_extension_penalty"):
        "адресный переходник стенда: условия продления в параметры правила",

    ("vmp/chain_price.py", "layover_penalty"):
        "адресная ветка исполнителя меры цены цепочки (ночёвка): семью "
        "различает ветвь, а не перечень — обход двери каталога в точке "
        "ценообразования стоил бы дороже самой меры; сам перечень "
        "мягких семей отдаёт носитель soft_families (10.09.2026)",
    ("vmp/chain_price.py", "calendar_windows"):
        "адресная ветка исполнителя меры цены цепочки (ночь дня окна); "
        "перечень мягких семей — у носителя soft_families",
    ("vmp/chain_price.py", "duty_extension_penalty"):
        "адресная ветка исполнителя меры цены цепочки (продление); "
        "перечень мягких семей — у носителя soft_families",

    ("vmp/pairing_input.py", "layover_penalty"):
        "НОСИТЕЛЬ ключей построителя (SOFT_EXECUTION) и адресная ветвь "
        "--w-night: опись исполнителей растёт вместе с ветвью, а "
        "ведомость исполнения читает её же вместо зеркала (10.09.2026)",
    ("vmp/pairing_input.py", "calendar_windows"):
        "носитель ключей построителя (SOFT_EXECUTION) и адресная ветвь "
        "--w-weekend-night",
    ("vmp/pairing_input.py", "duty_extension_penalty"):
        "носитель ключей построителя (SOFT_EXECUTION) и адресная ветвь "
        "--w-long-duty",

    ("vmp/run/roster_certify.py", "crew_composition"):
        "СВИДЕТЕЛЬ ЗАКОНА ПАРИТЕТА (PEOPLE_FAMILIES): правило L15 судит "
        "природу записи каталога ПО КОДУ, беря сторону кода отсюда, а "
        "конвейер заявок держит перечень неприкосновенным для агента "
        "(webapp/change_requests.PARITY_WITNESS_CONSTS, просьба В-2). "
        "Чтение каталога сделало бы L15 тождеством, а заявку на людскую "
        "семью — красной по построению (снимок ветки разошёлся бы со "
        "снимком main); разбор 10.09.2026",
    ("vmp/run/roster_certify.py", "reserve_floors"):
        "свидетель закона паритета (PEOPLE_FAMILIES), см. выше",
    ("vmp/run/roster_certify.py", "fleet_exclusions"):
        "свидетель закона паритета (PEOPLE_FAMILIES), см. выше",
    ("vmp/run/roster_certify.py", "time_scale"):
        "свидетель закона паритета (WORLD_PARAM_FAMILIES): у природ "
        "people и world_param второй стороны в коде нет — сторону "
        "решателя (HISTORY_FIT_FAMILIES) им заменяет этот перечень",

    ("vmp/export_solutions.py", "time_scale"):
        "омоним: ключ раздела времени пакета и поле выдачи, не семья",
    ("webapp/app.py", "time_scale"):
        "омоним: поле заголовка выгрузки X-COSMOS-Time-Scale, не семья",
}

#: ИЗВЕСТНЫЕ ДОЛГИ: рукописные перечни семей вне носителя — тот самый
#: класс ошибки 09.09.2026. Записи СНИМАЮТСЯ ПОСЛЕ ПОЧИНКИ вместе с
#: перечнями. ПУСТО с 11.09.2026: пять перечней закрыты 10.09, шестым —
#: HISTORY_FAMILIES, последними — четыре семьи границы горизонта (поле
#: записи `reads_history` выпуска 0.5.0). Пустой словарь — не забытый
#: механизм: новый долг заводится сюда строкой с датой и словами «снять
#: после починки», а сторож ниже считает их, чтобы они не множились молча.
DEBTS: dict = {}

#: Долги, у которых нет своего литерала в коде (перечень целиком, а не
#: отдельное имя): проверяются глазами, в сверке записей не участвуют.
_DEBTS_WITHOUT_LITERAL = {k for k in DEBTS if k[1].startswith("__перечень__")}


def family_ids(root: Path = ROOT) -> set[str]:
    """Идентификаторы семей каталога: все записи любого статуса плюс
    принятые кандидаты конвейера пополнения, если они лежат в дереве."""
    cat = reader.load(root / "catalog")
    out = set(cat.families(status=reader.STATUS_ALL))
    cand = root / "catalog" / "candidates" / "families"
    if cand.is_dir():
        for p in sorted(cand.glob("*.json")):
            doc = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("id"):
                out.add(str(doc["id"]))
    return out


def executor_symbols() -> set[str]:
    """Имена форм решателя, у которых есть исполнитель проверок."""
    return set(lgc.FORMS) | {str(k) for k in lgc._EXECUTORS}


def sources(root: Path = ROOT, areas=AREAS) -> list[Path]:
    out: list[Path] = []
    for area in areas:
        d = root / area
        if not d.is_dir():
            continue
        out += [p for p in sorted(d.rglob("*.py"))
                if "__pycache__" not in p.parts]
    return out


def scan(paths, needles: set[str], root: Path = ROOT) -> list[tuple]:
    """Находки: (файл от корня, строка, литерал). Ищутся строковые
    литералы, РАВНЫЕ имени из needles, — сокращения и куски слов не в
    счёт: перечень пишут именами."""
    found: list[tuple] = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(p))
        rel = p.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in needles):
                found.append((rel, node.lineno, node.value))
    return sorted(found)


def unnamed(hits) -> list[str]:
    """Находки, которые никто не назвал, — словами отказа."""
    out = []
    for rel, line, literal in hits:
        if rel in CARRIER_FILES or (rel, literal) in LEGAL \
                or (rel, literal) in DEBTS:
            continue
        out.append("перечень семей вне носителя: %s:%d — %s; %s"
                   % (rel, line, literal, CARRIER_WORDS))
    return out


def test_сканер_видит_литерал_семьи_в_подложенном_файле(tmp_path):
    """Сторож ловит то, что судит: подложенный файл с перечнем семей —
    находка и отказ теми словами, что названы в задании."""
    area = tmp_path / "vmp"
    area.mkdir()
    (area / "поддельный.py").write_text(
        "СЕМЬИ = (\"night_series\", \"нет-такой-семьи\")\n"
        "ФОРМЫ = (\"DUTY_SERIES_LIMIT\",)\n", encoding="utf-8")
    needles = family_ids() | executor_symbols()
    hits = scan(sources(tmp_path), needles, root=tmp_path)
    assert [(f, l) for f, _, l in hits] == [
        ("vmp/поддельный.py", "night_series"),
        ("vmp/поддельный.py", "DUTY_SERIES_LIMIT")], hits
    assert unnamed(hits) == [
        "перечень семей вне носителя: vmp/поддельный.py:1 — night_series; "
        + CARRIER_WORDS,
        "перечень семей вне носителя: vmp/поддельный.py:2 — "
        "DUTY_SERIES_LIMIT; " + CARRIER_WORDS]


def test_перечни_семей_и_форм_живут_только_у_носителя():
    """Каждый литерал семьи или формы вне носителя обязан быть назван:
    законное адресное употребление или известный долг."""
    needles = family_ids() | executor_symbols()
    assert len(needles) >= 20, "перечень имён для поиска пуст — сторож слеп"
    hits = scan(sources(), needles)
    seen = {(rel, literal) for rel, _, literal in hits
            if rel not in CARRIER_FILES}
    new = unnamed(hits)
    assert not new, (
        "новых мест: %d. Место либо адресное (запись в LEGAL с "
        "обоснованием), либо перечень (чинить: читать носитель).\n%s"
        % (len(new), "\n".join(new)))
    stale = (set(LEGAL) | set(DEBTS)) - seen - _DEBTS_WITHOUT_LITERAL
    assert not stale, (
        "записи сторожа ни на что не указывают — снять из LEGAL/DEBTS: %s"
        % ", ".join("%s — %s" % k for k in sorted(stale)))


def test_долги_посчитаны_и_названы_датой():
    """Долг — временная запись: у каждой есть дата и слова «снять после
    починки». Счёт долгов виден в отказе, чтобы он не рос молча."""
    for key, reason in DEBTS.items():
        assert "снять после починки" in reason, key
        assert re.search(r"\d{2}\.\d{2}\.\d{4}", reason), key
    files = sorted({f for f, _ in DEBTS})
    # 10.09.2026: было 19 записей в 6 файлах; пять перечней починены
    # (носители — см. заголовок файла), два перечня сертификатора
    # признаны свидетелями закона паритета и переехали в LEGAL. Шестым
    # починен HISTORY_FAMILIES (тем же днём): три семьи, дублировавшие
    # FAMILY_FORMS, сняты — их отдаёт носитель. 11.09.2026: сняты
    # последние четыре — семьи ГРАНИЦЫ ГОРИЗОНТА, их называет поле
    # записи `reads_history` каталога 0.5.0.
    assert not DEBTS and not files, (
        "счёт долгов изменился: %d записей в %d файлах (%s). Долг "
        "погашен — снять запись; долг заведён — так перечни и множатся"
        % (len(DEBTS), len(files), ", ".join(files)))


def test_носители_на_месте_и_несут_семьи():
    """Носитель без перечня — не носитель: проверяем, что формы и
    таблицы решателя действительно читаются."""
    for rel in CARRIER_FILES:
        assert (ROOT / rel).is_file(), rel
    assert set(lgc.FAMILY_FORMS) <= family_ids(), (
        "семья форм решателя не найдена в каталоге: %s"
        % sorted(set(lgc.FAMILY_FORMS) - family_ids()))
    assert executor_symbols() == set(lgc._EXECUTORS), (
        "форма без исполнителя или исполнитель без формы: %s"
        % sorted(set(lgc.FORMS) ^ set(lgc._EXECUTORS)))
    from vmp import kotlin_tables as kt
    assert set(kt.history()["families"]), "таблица семей истории пуста"
    assert set(kt.family_to_constraint()), "таблица мягких семей пуста"


@pytest.mark.parametrize("rel,reason", sorted(CARRIER_FILES.items()))
def test_носитель_назван_словами(rel, reason):
    assert reason and (ROOT / rel).is_file()
