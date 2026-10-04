# -*- coding: utf-8 -*-
"""Прогон стендовых задач каталога шаблонов.

Запись каталога (семья либо критерий) несёт стендовые задачи: мир из
синтетических кодов и ожидания по видам исполнителей. Этот модуль берёт
задачи из живого каталога, переводит мир во вход исполнителя
(`vmp.stand_adapters`), зовёт исполнителя и сравнивает полученное с
ожиданием. Ни одного своего числа у прогона нет: пороги приходят миром,
ожидания — записью.

Три исхода и только три:

  * `passed` — исполнитель дал ровно ожидаемое;
  * `skipped` — вид стендом не судится либо судится только живым
    решателем, причина названа словами;
  * `failed` — расхождение. Это КРАСНЫЙ гейт: единственный законный путь
    для `failed` — явная запись в `KNOWN_FAILED` с причиной и словом
    владельца. Молчаливого пропуска нет, ожидание под расчёт не
    подгоняется: расхождение либо дефект входа стенда (правится адаптер),
    либо дефект исполнителя (доклад владельцу).

Отпечатки кода в файле результатов считаются ПОСЛЕ нормализации перевода
строки: на машине владельца `core.autocrlf=true`, CI идёт и на Windows, и
на Linux — отпечаток сырых байтов объявил бы результаты устаревшими
везде, кроме машины записи.

ЖИВОЙ ПРОГОН ВИДОВ РЕШАТЕЛЯ ПОДКЛЮЧЁН (§5.6 проекта, путь 1): виды
`kotlin_constraint`, `kotlin_logic_form`, `kotlin_metric` судятся
настоящим решателем — пакет постановки собирается библиотекой CDPM по
своду задачи, уходит прогоном по идентификатору, и повкладовый счёт
назначений стенда ПОД ПАКЕТОМ ЭТОГО прогона даёт числа
(`vmp.stand_solver`). Прогон идёт ТОЛЬКО ключом `--with-solver <адрес>`:
умолчания у адреса нет и не будет (решение владельца), без ключа виды
решателя остаются `skipped` со словами. Отказ живого звена — `failed`
словами отказа, а не `skipped`: неподтверждённое ожидание молчаливым
пропуском не прикрывается.

Отдельный явный API `qualification_evidence` импортирует Local-only
квалификационный actual-run для `kotlin_constraint` / `kotlin_metric`. Он не вызывает
решатель, не открывает admission и не выдаёт этот источник за HTTP:
независимый request, точные входы, bounded solve и оба объекта счёта
проверяет `vmp.qualification_run`; CLI не включает этот путь неявно.

ПРОГОН И ОТПЕЧАТОК ПАКЕТА ЖИВУТ ПОЛЯМИ СТРОКИ (`run_id`,
`package_fingerprint`), а не словами внутри `note`: `note` уезжает в
`stand_verified` файла привязок, и идентификатор прогона делал бы
привязки каноном ПРОГОНА, а не каталога — перепрогон менял бы два
десятка строк при побайтово тех же числах. Прослеживаемость от этого не
теряется: поля строки несут прогон и пакет, адрес службы назван шапкой
`solver` файла результатов и строкой над печатной таблицей.

СТЕНД УКЛАДЫВАЕТСЯ В ПРЕДЕЛ КОМАНДЫ САМ, а серию обрывает рано (просьба
Ж-3 проекта пакета «Kotlin-исполнители агентом»): три отказа ПОДРЯД БЕЗ
ЖИВОГО ОТВЕТА службы (соединение не встало, состояние не отдано, потолок
ожидания истёк) значат «считать некому» — остальные живые строки
`failed` словами `READY_MISS_WORDS` и БЕЗ новых прогонов (очередь чужой
службы стенд не набивает); ответ службы — отвергнутый гейтом пакет,
УПАВШИЙ на своём пакете прогон — счётчик сбрасывает. Ключ `--budget-s`
даёт команде предел времени: по исчерпании живые строки `failed` словами
`BUDGET_WORDS`, файл результатов пишется с ЧАСТИЧНЫМИ строками, а не
обрывается потолком команды конвейера кодом 124 без диагноза; ОСТАТОК
предела уезжает в живую строку и ограничивает её звенья, поэтому начатая
у границы строка предел не переезжает. Ключ `--solver-identity <sha256>`
(просьба Ж-4) кладёт отпечаток артефакта решателя в шапку
`solver.artifact_sha256`; без ключа поле `null`, и причина названа
словами. Ключ `--deterministic` включает профиль Д1 — умолчание ВЫКЛ,
включают на решателе С ПРАВКОЙ остановки Д1: он называет признак
остановки полем `termination`, и стенд кладёт его ПОЛЕМ СТРОКИ (вид,
воспроизводимость, слова), а не словами `note` — вид остановки зависит
от секундомера прогона, а `note` уезжает каноном в привязки
(`stand_solver.D1_FINDING`, `D1_STOP_SIGN`, `termination_row`).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from vmp import stand_adapters as sa

REPO = Path(__file__).resolve().parents[1]

#: Файлы, чей отпечаток делает результаты стенда свежими или устаревшими:
#: исполнители, которых стенд судит, и сам стенд. `vmp/stand_solver.py`
#: входит сюда с подключением живого прогона: исходы Kotlin-видов зависят
#: от него так же, как исходы чистых видов — от адаптеров, и файл
#: результатов не может молчать о коде, которым получен.
CODE_FILES = (
    "_pairing_builder.py",
    "_duty_periods.py",
    "_conditions.py",
    "vmp/conditions_export.py",
    "template_catalog/stand_contracts.py",
    "vmp/chain_price.py",
    "vmp/criteria.py",
    "vmp/source_check.py",
    "vmp/run/roster_certify.py",
    "vmp/run/fit_checks.py",
    "vmp/run/logic_checks.py",
    "vmp/stand_adapters.py",
    "vmp/stand_solver.py",
    "vmp/stand_tasks.py",
    "vmp/stand_components.py",
    "vmp/qualification_run.py",
)

#: Куда команда кладёт папку прогона, когда ключа --tmp-dir нет: В САМОМ
#: ДЕРЕВЕ, а не во временный каталог системы. Место названо явно и снято
#: с учёта git (.gitignore); папка прогона удаляется за собой.
TMP_ROOT = REPO / "_repro" / "stand"

#: Явный перечень известных красных задач: {(запись, задача, вид): причина}.
#: Пуст по замыслу — запись сюда делается ТОЛЬКО по слову владельца после
#: разбора расхождения, и каждая строка называет причину словами.
KNOWN_FAILED: dict = {}

#: Формы мира, из которых стенд COSMOS строит вход исполнителя
#: (`vmp.stand_adapters`): расписание и контрольная пара цены. Мир иной
#: формы — не дефект задачи: её судит стенд линии данных CDPM, и здесь
#: он даёт законный `skipped`, как и чужой шаблон проверки ниже.
BUILDABLE_WORLD_FORMS = ("schedule", "cost_pair")

#: Виды, которые стенд судит без решателя.
PURE_KINDS = ("builder_method", "check_template", "check_code", "export_form")
#: Виды живого решателя. kotlin_constraint/kotlin_metric additionally permit an explicitly
#: imported Local-only qualification run, with a distinct proof_source.
SOLVER_KINDS = ("kotlin_constraint", "kotlin_logic_form", "kotlin_metric")

#: Почему `solver.package_fingerprint` шапки результатов пуст: путь 1
#: §5.6 — свой пакет постановки на КАЖДУЮ задачу (у задачи свой мир и
#: свой состав семей), и одного отпечатка на файл не существует.
PACKAGE_PER_TASK = ("пакет постановки — свой на каждую задачу (путь 1 "
                    "§5.6): одного отпечатка на весь файл нет, отпечаток "
                    "задачи назван в её строке")

SOLVER_NOTE = ("решатель не зван: живой прогон Kotlin-видов идёт только "
               "командой --with-solver <адрес> по слову владельца (правило "
               "прогонов); умолчания у адреса нет, и сам стенд к экземпляру "
               "решателя не обращается")

#: Сколько отказов ПОДРЯД БЕЗ ЖИВОГО ОТВЕТА службы стенд терпит, прежде
#: чем прекратить серию (просьба Ж-3). Считаются ровно три случая:
#: соединение не встало на постановке прогона, состояние прогона не
#: отдано, потолок ожидания истёк (`StandSolverError.unanswered`). Три, а
#: не один: молчание бывает и у живой службы (чужой прогон занял поток),
#: и обрывать серию по первому значило бы звать мёртвой службу, которая
#: отвечает. Три подряд — уже не случайность.
#:
#: Ответ службы счётчик СБРАСЫВАЕТ, каким бы он ни был: пакет, отвергнутый
#: её гейтом, и прогон, УПАВШИЙ на своём пакете (`run_failed`), — это
#: живая служба. Правка по ревью 08.09.2026: падение приходит первым же
#: опросом и времени не стоит, поэтому обрыв по нему ничего не экономит,
#: зато три коррелированных падения одной записи (общий шаблон мира)
#: закрывали бы словами «решатель не отвечает» два десятка ЧУЖИХ строк —
#: ложь о живой службе и потеря свидетельства.
READY_MISS_LIMIT = 3

#: Слова строки, оставшейся без прогона из-за раннего обрыва серии.
READY_MISS_WORDS = ("решатель не отвечает: прогоны прекращены после "
                    "%d отказов подряд" % READY_MISS_LIMIT)

#: Слова строки, закрытой пределом времени команды (`--budget-s`,
#: просьба Ж-3). Частичные строки пишутся СЛОВАМИ и попадают в файл
#: результатов: молчаливое «строк меньше» читалось бы как «задач
#: меньше», а упор команды в её потолок — как «зависла».
BUDGET_WORDS = "бюджет стенда исчерпан"

#: Запас, который бюджет обязан оставлять потолку команды конвейера,
#: секунды. До правки по ревью 08.09.2026 запас считали НА СТРОКУ (до
#: ~360 с: клиент 120 с на каждое из трёх обращений плюс ожидание
#: «готов»), потому что начатая у границы строка шла своими прежними
#: потолками. Теперь остаток бюджета уезжает В СТРОКУ и её звенья, и
#: запас нужен только на то, что вне живых строк: чистых судей, чтение
#: каталога и запись файла результатов. Число названо словами в справке
#: ключа и в руководстве 13.4/13.8.
BUDGET_MARGIN_S = 60

#: Почему `solver.artifact_sha256` шапки результатов пуст (просьба Ж-4).
IDENTITY_NOTE = ("артефакт решателя не назван (ключ --solver-identity не "
                 "задан): привязка «адрес → артефакт» этим файлом не "
                 "доказана — по адресу мог отвечать другой решатель")

#: Чем судится число у ожидания `kotlin_constraint`: размер нарушения
#: записи (`violation_size`, словарь каталога `violation_sizes`) → поле
#: полученного. `count` — штраф растёт с ЧИСЛОМ единиц (сколько смен,
#: ночей, событий), мера — `matches`; `magnitude` — с ВЕЛИЧИНОЙ
#: превышения (сколько часов сверх), мера — `amount`. Выбор объявляет
#: ЗАПИСЬ, а не вид исполнителя: до выпуска 0.5.0 стенд молча сравнивал
#: оба числа, и совпадение «число нарушений = величина превышения» у
#: семьи счёта выглядело подтверждением, ничего не подтверждая.
VIOLATION_MEASURE = {"count": "matches", "magnitude": "amount"}


def measure_units(cat=None) -> dict:
    """Единица меры мягкого правила: РУССКОЕ слово исполнителя цены
    (`vmp.chain_price.UNITS`) → код единицы ожидания записи.

    Карта ОБРАТНАЯ словарю каталога `violation_units` и считается из
    него: рукописной карты у стенда с выпуска 0.5.0 нет — слово единицы
    живёт рядом с кодом в каталоге. Единица подтверждается СЛОВОМ, а не
    подгоняется под число; каталог недоступен либо словарь не карта —
    отказ словами (`vmp.chain_price.ChainPriceError`).
    """
    from vmp import chain_price as cp
    return {word: code for code, word in cp.unit_words(cat).items()}


def violation_measure(size) -> str | None:
    """Поле полученного, которым судится штраф семьи: `matches` при
    `count`, `amount` при `magnitude`. Размера нет (жёсткая семья) —
    None: штрафа у неё нет, и меры она не объявляет."""
    if size is None:
        return None
    return VIOLATION_MEASURE.get(str(size))


def violation_size_refusal(size, expected: dict, got: dict) -> str | None:
    """Расхождение «размер нарушения записи ↔ числа исполнителя» —
    словами; сошлось либо семья жёсткая — None.

    Четыре случая отказа, и каждый назван. Слово размера вне словаря
    каталога — стенду нечем выбрать меру. Ожидание не называет числа,
    которым запись велела судить, — судить нечем, ожидание подобрано не
    под запись. Семья ВЕЛИЧИНЫ, у которой исполнитель величины не дал, —
    мера не измерена. Семья СЧЁТА, у которой величина превышения не
    равна числу совпадений, — запись объявила «штраф растёт с числом
    единиц», а решатель считает иначе: это расхождение, а не тихое
    совпадение.
    """
    if size is None:
        return None
    field = violation_measure(size)
    if field is None:
        return ("размер нарушения записи %r словарю каталога "
                "(violation_sizes) не принадлежит: чем судить штраф — "
                "числом единиц или величиной превышения — записью не "
                "сказано" % (size,))
    if _dct(expected).get(field) is None:
        return ("запись объявила размер нарушения %r — судится число %r, "
                "а ожидание задачи его не называет (%s): ожидание "
                "подобрано не под запись"
                % (size, field, _render(dict(expected))))
    matches, amount = _dct(got).get("matches"), _dct(got).get("amount")
    if field == "amount" and amount is None:
        return ("запись объявила размер нарушения «magnitude» — мерой "
                "служит величина превышения (amount), а исполнитель её не "
                "дал: мера не измерена")
    if field == "matches" and isinstance(matches, int) \
            and isinstance(amount, int) and not isinstance(amount, bool) \
            and amount != matches:
        return ("запись объявила размер нарушения «count» — штраф растёт "
                "с числом единиц, и величина превышения обязана равняться "
                "числу совпадений; исполнитель дал matches=%d, amount=%d"
                % (matches, amount))
    return None


#: Образец коммита прогона — тот же, что у `$defs/commit` схемы
#: читателя: файл результатов не должен рождаться таким, каким его
#: отвергнет дверь привязок.
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: Образец отпечатка артефакта решателя (`--solver-identity`, Ж-4).
#: Регистр НЕ различается (правка по ревью 08.09.2026): `Get-FileHash`
#: PowerShell — обычный способ снять отпечаток на площадке — печатает
#: заглавные, а `hashlib.hexdigest()` конвейера строчные; один и тот же
#: отпечаток отвергать по регистру нечестно. В шапку результатов
#: значение уезжает приведённым к строчным (форма `hexdigest`).
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def canon_sha256(value) -> str | None:
    """Отпечаток артефакта решателя канонической формы либо None.

    Канон один — строчные знаки (`hexdigest`): в файле результатов один
    и тот же артефакт обязан выглядеть одинаково, кем бы отпечаток ни
    был снят.
    """
    text = str(value or "").strip()
    return text.lower() if text else None


Outcome = namedtuple("Outcome", "result got note")


class StandRunError(RuntimeError):
    """Прогон стенда не состоялся — причина словами."""


def _dct(value) -> dict:
    return value if isinstance(value, dict) else {}


def _prefix_template(name: str, known: dict) -> bool:
    """Шаблоны словаря группы `builtin_prefix` («история-*»,
    «горизонт-*») именуются образцом: имя годно, если совпадает начало."""
    for key, spec in known.items():
        if str(key).endswith("*") and _dct(spec).get("group") \
                == "builtin_prefix" and name.startswith(str(key)[:-1]):
            return True
    return False


def _no_judge(kind: str, symbol: str) -> Outcome:
    """Судимый вид с НЕИЗВЕСТНЫМ символом ожидания — это `failed`.

    `skipped` по замыслу §5.4 положен только видам БЕЗ судьи
    (`kotlin_knob`, `kotlin_form`, `condition_fn`) и
    Kotlin-видам без живого решателя. Символы ожиданий читатель не
    проверяет (X05 судит лишь виды), поэтому опечатка в символе посева у
    судимого вида раньше давала зелёный `skipped` и уходила в
    `stand_verified`: ожидание записи оказывалось неисполнимым МОЛЧА.
    Единственный законный путь к `failed` — разбор словами и, по слову
    владельца, строка `KNOWN_FAILED`."""
    return Outcome("failed", None,
                   f"судьи символа {symbol!r} у стенда нет: вид {kind} "
                   f"судим, значит ожидание записи неисполнимо — опечатка "
                   f"в символе посева либо исполнитель переименован")


# ------------------------------------------------------------- отпечатки

def code_fingerprint(path) -> str:
    """sha256 файла после нормализации CRLF -> LF."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def code_fingerprints(repo=None) -> dict:
    root = Path(repo or REPO)
    return {name: code_fingerprint(root / name) for name in CODE_FILES}


def _head_commit(repo=None) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"],
                             cwd=str(repo or REPO), capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return value if out.returncode == 0 and len(value) == 40 else None


# --------------------------------------------------------------- каталог

def _catalog(path=None):
    from vmp import catalog_access as _ca
    if path:
        import template_catalog as tc
        return tc.load(path)
    return _ca.current()


def _records(cat) -> list:
    """[(секция, запись)] в порядке файла привязок: семьи, затем критерии."""
    out = [("families", r) for r in cat.families_full()]
    out += [("criteria", r) for r in
            (cat.criteria().get(cid) for cid in sorted(cat.criteria()))]
    return [(s, r) for s, r in out if isinstance(r, dict)]


def _family_statement(record: dict) -> str | None:
    """Carry declared semantics into the synthetic SMT subject without inference.

    A family's text can describe a topic while measure states its actual norm.
    Neutral labels preserve counts/formulas/scopes as written, not new demands.
    Only exact repeated values are omitted; task worlds/oracles are not inputs.
    """
    sem = record.get("semantics")
    sem = sem if isinstance(sem, dict) else {}
    parts, seen = [], set()
    for field, label in (("text", "Описание"), ("measure", "Измерение"),
                         ("scope", "Область"), ("unit", "Единица")):
        value = sem.get(field)
        if isinstance(value, str) and value.strip() and value not in seen:
            parts.append(f"{label}: {value}")
            seen.add(value)
    return "\n".join(parts) if parts else record.get("title")


def _family_meta(cat) -> dict:
    """{семья: {params, kind, zone, title, statement, registry_rev}} —
    из живого каталога; своей карты семей у стенда нет."""
    out = {}
    for r in cat.families_full():
        if not isinstance(r, dict):
            continue
        out[str(r.get("id"))] = {
            "params": sorted(str(p.get("name"))
                             for p in (r.get("params") or ())
                             if isinstance(p, dict)),
            "kind": r.get("kind"), "zone": r.get("zone"),
            "title": r.get("title"),
            "statement": _family_statement(r),
            "registry_rev": cat.fingerprint()}
    return out


def _param_family(meta: dict) -> dict:
    out = {}
    for family, info in meta.items():
        for name in info.get("params") or ():
            out[name] = family
    return out


def families_for(world: dict, family: str | None, meta: dict) -> list:
    """Семьи свода задачи: семья записи плюс все семьи, чьи параметры
    названы миром (то же правило, что у состава пакета живого прогона —
    без посадки семьи её пороги до исполнителя не доедут)."""
    by_param = _param_family(meta)
    names = {family} if family in meta else set()
    for name in sa.params_of(world):
        fam = by_param.get(name)
        if fam:
            names.add(fam)
    return sorted(names)


# ------------------------------------------------------------- построитель


def _builder(world, *, rules=False):
    import _pairing_builder as _pb
    payload, args = sa.world_to_builder(world, rules=rules)
    return _pb.Builder(payload, args)


def _apply_cli(args, argv: list) -> None:
    """Аргументы исполнителя критерия -> поля `Namespace` построителя.

    Исполнитель отдаёт КЛЮЧИ ЗАПУСКА (это его контракт с main), а стенд
    зовёт построитель внутрипроцессно — перевод ключей в поля живёт в
    одном месте и отказывает словами на незнакомом ключе."""
    i = 0
    while i < len(argv):
        key = str(argv[i])
        if not key.startswith("--"):
            raise StandRunError(
                f"исполнитель критерия отдал не ключ: {key!r}")
        name = key[2:].replace("-", "_")
        if not hasattr(args, name):
            raise StandRunError(
                f"исполнитель критерия отдал ключ {key!r}, которого нет "
                f"у набора флагов стенда")
        if i + 1 < len(argv) and not str(argv[i + 1]).startswith("--"):
            raw = argv[i + 1]
            cur = getattr(args, name)
            setattr(args, name, int(raw) if isinstance(cur, int)
                    and not isinstance(cur, bool) else raw)
            i += 2
        else:
            setattr(args, name, True)
            i += 1


def _stand_knobs(args, world) -> None:
    for key, value in sorted((world.get("stand") or {}).items()):
        if key in sa.STAND_KNOBS:
            setattr(args, key, value)


def _price(builder, chain, dh_legs):
    crew = builder.crew_size(chain)
    return builder.price(chain, dh_legs, crew)


def _soft_row(price: dict, family: str):
    for row in price.get("soft") or ():
        if row.get("family") == family:
            return row
    return None


def _judge_builder(ctx) -> Outcome:
    expected = ctx["expected"]
    world, family = ctx["world"], ctx["family"]
    symbol = str(expected.get("symbol") or "")
    if symbol == "resolve_time_scale":
        import _pairing_builder as _pb
        declared = sa.params_of(world).get("base_tz_offset_min")
        value, _note, refusal = _pb.resolve_time_scale(declared, None)
        if refusal:
            return Outcome("failed", None, refusal)
        return _compare(expected, value, symbol)
    if symbol == "_exec_min_person_days":
        return _judge_min_person_days(ctx)
    if symbol == "_exec_rank_normalized_cost":
        return _judge_rank_normalized_cost(ctx)
    rules = symbol in ("Builder.pairing_days_ok", "Builder.pairing_span_ok")
    builder = _builder(world, rules=rules)
    chain, dh_legs = sa.chain_of(builder, world)
    if not chain:
        return Outcome("failed", None,
                       "цепочка мира пуста: рабочих рейсов нет")
    if symbol == "measure":
        row = _soft_row(_price(builder, chain, dh_legs), family)
        if row is None:
            return Outcome("failed", None,
                           f"в расшифровке цены нет строки семьи {family!r}: "
                           f"мягкое правило до цены не доехало")
        return _compare(expected, row["measure"], symbol)
    if symbol == "Builder.duty_limit_ok":
        n = sum(1 for d in chain if not builder.duty_limit_ok(d["legs"]))
        return _compare(expected, n, symbol)
    if symbol == "Builder.periods_tiers":
        n = 0 if builder.periods_tiers(chain, dh_legs) is not None else 1
        return _compare(expected, n, symbol)
    if symbol == "Builder.adjacent_dh_ok":
        n = 0 if builder.adjacent_dh_ok(chain, dh_legs) else 1
        return _compare(expected, n, symbol)
    if symbol == "Builder.pairing_days_ok":
        n = 0 if builder.pairing_days_ok(chain, dh_legs) else 1
        return _compare(expected, n, symbol)
    if symbol == "Builder.pairing_span_ok":
        n = 0 if builder.pairing_span_ok(chain) else 1
        return _compare(expected, n, symbol)
    if symbol == "Builder.chain_ok":
        n = 0 if builder.chain_ok(chain) else 1
        return _compare(expected, n, symbol)
    if symbol == "Builder.rest_floor_after":
        periods = builder.periods_of(chain, dh_legs)
        if not periods:
            return Outcome("failed", None,
                           "периоды цепочки не построены: отдых после "
                           "периода не считается")
        return _compare(expected, int(builder.rest_floor_after(periods[0])),
                        symbol)
    if symbol == "Builder.crew_size":
        return _compare(expected, builder.crew_size(chain), symbol)
    if symbol == "Builder.price":
        _apply_criterion(builder, world)
        return _compare(expected, _price(builder, chain, dh_legs)["cost"],
                        symbol)
    return _no_judge("builder_method", symbol)


def _criterion_of(world: dict) -> dict:
    crit = world.get("criterion")
    if not isinstance(crit, dict):
        raise StandRunError("мир задачи критерия не несёт блока criterion")
    return dict(crit)


def _apply_criterion(builder, world) -> None:
    """Критерий мира — исполнителем словаря, а не своими ключами.

    Ключи запуска строит исполнитель вида (`vmp.criteria`), стенд их
    только переносит в поля построителя; ручки стенда кладутся ПОСЛЕ,
    поэтому вес мягкого правила мира виден в цене (исполнитель критерия
    обнуляет собственные коэффициенты способа, а не веса правил ВМП)."""
    from vmp import criteria as crit
    criterion = _criterion_of(world)
    kind = str(criterion.get("kind"))
    fn = crit.EXECUTORS.get(kind)
    if fn is None:
        raise StandRunError(
            f"исполнителя вида критерия {kind!r} у построителя нет")
    argv, prov = [], {}
    fn(criterion, argv, prov)
    _apply_cli(builder.args, argv)
    _stand_knobs(builder.args, world)
    builder._soft_rules_cache = None


def _judge_min_person_days(ctx) -> Outcome:
    world, expected = ctx["world"], ctx["expected"]
    builder = _builder(world)
    chain, dh_legs = sa.chain_of(builder, world)
    _apply_criterion(builder, world)
    price = _price(builder, chain, dh_legs)
    return _compare(expected, price["criterion"], "_exec_min_person_days")


@contextlib.contextmanager
def _stand_contour(path):
    """Контур данных стенда на время вызова исполнителя критерия.

    Исполнитель зовёт собственную самопроверку по файлу пар КОНТУРА
    (`check_control_pair` без пути), и на машине с чужим носителем он
    отказал бы данными другого заказчика. Контур стенда — сама папка
    задачи: исполнитель сверяется с парой МИРА, а не с чужой."""
    import os
    old = os.environ.get("CREW_DATA_DIR")
    os.environ["CREW_DATA_DIR"] = str(Path(path).parent)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CREW_DATA_DIR", None)
        else:
            os.environ["CREW_DATA_DIR"] = old


def _judge_rank_normalized_cost(ctx) -> Outcome:
    from vmp import criteria as crit
    from vmp import template_registry as tr
    world, expected = ctx["world"], ctx["expected"]
    rates = world.get("rates_deci") or {}
    criterion = {"kind": "rank_normalized_cost", "label": "стенд",
                 "rates": {"flight": int(rates["flight"]) / 10,
                           "work": int(rates["work"]) / 10},
                 "rates_version": {"source": "стенд", "version": "1",
                                   "effective_from": "2026-10-01"},
                 "source_rule": "стенд"}
    path = sa.cost_pair_to_control_pair(
        world, ctx["tmp_dir"], expected_total_deci=int(expected["value"]))
    argv, prov = [], {}
    with _stand_contour(path):
        crit.EXECUTORS["rank_normalized_cost"](criterion, argv, prov)
    import _pairing_builder as _pb
    payload, args = sa.cost_pair_payload(world)
    _apply_cli(args, argv)
    builder = _pb.Builder(payload, args)
    builder.build_duties(None)
    work = [builder.legs["F1"]]
    dh_legs = [builder.legs["D1"]] if "D1" in builder.legs else []
    chain = [{"i": 0, "legs": work, "dep": work[0]["dep"], "arr": work[-1]["arr"],
              "t0": work[0]["t0"], "t1": work[-1]["t1"],
              "span": (work[-1]["t1"] - work[0]["t0"]).total_seconds() / 60}]
    builder.annotate_duty(chain[0])
    value = builder.cost_0096(chain, dh_legs, int(world["crew"]))
    try:
        state = tr.check_control_pair(
            "rank_normalized_cost",
            {"deci_work": int(args.cost_rank_deci_work),
             "deci_dh": int(args.cost_rank_deci_dh)}, path=path)
    except tr.ControlPairError as e:
        return Outcome("failed", value, f"контрольная пара мира: {e}")
    out = _compare(expected, value, "_exec_rank_normalized_cost")
    return Outcome(out.result, out.got,
                   f"{out.note}; контрольная пара мира: {state}")


# ------------------------------------------------------------- сертификат

def _rule_id(family: str) -> str:
    return f"R-STAND-{family}"


def _certificate(ctx):
    return sa.world_to_certificate(ctx["world"], ctx["families"],
                                   meta=ctx["meta"])


def _pick(checks: list, rid: str):
    for c in checks:
        if str((c.get("basis") or {}).get("rule")) == rid:
            return c
    return None


BAD_STATUS = ("не прогнана", "частично прогнана")


def _judge_check_template(ctx) -> Outcome:
    from vmp.run import roster_certify as rc
    expected, family = ctx["expected"], ctx["family"]
    template = str(expected.get("template") or "")
    svod, dataset = _certificate(ctx)
    rid = _rule_id(family)
    if template == rc.WORLD_PARAM_TEMPLATE:
        checks, _rest = rc.world_param_checks(svod)
        check = _pick(checks, rid)
        if check is None:
            return Outcome("failed", None,
                           f"записи параметра мира у правила {rid} нет")
        got = {"template": check.get("template"),
               "params": dict(check.get("params") or {}), "findings": 0}
        return _compare_map(expected, got, template)
    if template == rc.SOFT_MEASURE_TEMPLATE:
        checks, findings = rc.measure_soft_rules(svod, dataset)
        check = _pick(checks, rid)
        if check is None:
            return Outcome("failed", None,
                           f"меры мягкого правила {rid} нет: семья "
                           f"{family!r} исполнителю меры не досталась")
        if check.get("status") in BAD_STATUS:
            return Outcome("failed", {"status": check.get("status")},
                           f"проверка «{check.get('status')}»: "
                           f"{check.get('reason') or 'причина не названа'}")
        got = {"template": check.get("template"),
               "params": _rule_params(svod, rid),
               "findings": sum(1 for f in findings
                               if f.get("check") == check.get("id"))}
        measure = check.get("measure")
        if isinstance(measure, dict):
            got["measure"] = measure.get("value")
        return _compare_map(expected, got, template)
    # Шаблон СЛОВАРЯ, но не сертификатный — законный `skipped`: его
    # исполняет линия данных CDPM. Имя вне словаря — `failed`: это не
    # чужой исполнитель, а опечатка ожидания, и молчать о ней нельзя.
    known = _dct(ctx["catalog"].dictionaries().get("check_templates"))
    if template in known or _prefix_template(template, known):
        return Outcome("skipped", None,
                       f"шаблона {template!r} стенд не исполняет: "
                       f"исполнитель шаблона — линия данных CDPM")
    return Outcome("failed", None,
                   f"шаблона {template!r} нет в словаре check_templates "
                   f"каталога: ожидание записи неисполнимо — опечатка в "
                   f"имени шаблона либо шаблон снят")


def _rule_params(svod: dict, rid: str) -> dict:
    """Параметры правила свода: значения посадки плюс поля формы правила,
    которые исполнитель читает мимо посадки (окно дней недели у меры
    календарного окна живёт в `export_solver.days`)."""
    for r in svod.get("rules") or ():
        if str(r.get("id")) != rid:
            continue
        out = {k: (v.get("value") if isinstance(v, dict) else v)
               for k, v in ((r.get("template_fit") or {}).get("params")
                            or {}).items()}
        days = (r.get("export_solver") or {}).get("days")
        if days:
            out["days"] = list(days)
        return out
    return {}


# ------------------------------------------------------------ проверки кода

def _logic_symbols() -> frozenset:
    """Символы судей логических форм — ИМЕНА исполнителей планов
    `vmp.run.logic_checks._EXECUTORS`, а не перечень в стенде.

    Носитель истины один: форму читает `plan_from_fit`, исполняет
    `run_plan` — тот же модуль, что и здесь. Агент CR класса «рост
    таблиц решателя» дописывает новую форму в `vmp/run/logic_checks.py`
    (путь ему открыт), а стенд ему закрыт (FORBIDDEN_PATHS): с перечнем
    в стенде каждая новая форма красила бы задачи записи «судьи символа
    у стенда нет» по построению (живой случай 09.09.2026, CR
    duty_series_limit, символ `_duty_series`, три задачи), и починить это
    мог только человек — против принципа контура самоулучшения (судьи
    добавляются, агент не подменяется)."""
    from vmp.run import logic_checks as lc
    return frozenset(fn.__name__ for fn in lc._EXECUTORS.values())


def _judge_check_code(ctx) -> Outcome:
    expected, family = ctx["expected"], ctx["family"]
    symbol = str(expected.get("symbol") or "")
    if symbol == "_run_unavailability":
        # The binding names the primary-source checker, not the neighboring
        # certificate executor. Exercise that exact mechanism with the same
        # declared native input; no source compiler or model call is needed.
        from vmp import source_check as sc
        if (family != "crew_unavailability"
                or set(expected) != {"symbol", "findings"}
                or type(expected.get("findings")) is not int
                or expected["findings"] < 0):
            return Outcome("failed", None, "unavailability: неверная семья или точное ожидание findings")
        world = ctx["world"]
        days = sa.unavailable_days_of(world)
        if days is None:
            return Outcome("failed", None, "unavailability: нет явного crews[].unavailable_days")
        problem = sa.world_to_problem(world)
        if not isinstance(world.get("assignments"), list) or not world["assignments"]:
            return Outcome("failed", None, "unavailability: нет явных назначений для проверки")
        assignments = sa._assignments_of(world)
        flights = {f["flightNumber"] for f in problem["flights"]}
        seen = set()
        for row in assignments:
            seat = row.get("seat", 0)
            key = (row.get("flight"), seat)
            if (row.get("crew") not in days or row.get("flight") not in flights
                    or type(seat) is not int or seat < 0 or key in seen):
                return Outcome("failed", None, "unavailability: неоднозначное или неизвестное назначение")
            seen.add(key)
        native_rows = problem["flightAssignments"]
        if (not native_rows or len(native_rows) != len(assignments)
                or any(row.get("employee") not in days for row in native_rows)):
            return Outcome("failed", None, "unavailability: назначения мира перенесены не полностью")
        timeline = sc.build_timelines(problem)
        spec = {"id": _rule_id(family), "kind": "unavailability", "anchor": "стенд"}
        result = sc._run_unavailability(spec, timeline)
        if (not isinstance(result.violations, list)
                or result.verdict != ("violated" if result.violations else "ok")
                or type(result.objects_seen) is not int
                or result.objects_seen != len(native_rows)):
            return Outcome("failed", None, "unavailability: фактический исполнитель не проверил все назначения")
        return _compare(expected, len(result.violations), symbol, key="findings")
    from vmp.run import fit_checks as fc
    svod, dataset = _certificate(ctx)
    rid = _rule_id(family)
    rule = next((r for r in svod["rules"] if str(r.get("id")) == rid), None)
    if rule is None:
        return Outcome("failed", None,
                       f"в своде стенда нет правила семьи {family!r}")
    params = sa.params_of(ctx["world"])
    counter = fc._Counter()
    if symbol in _logic_symbols():
        from vmp.run import logic_checks as lc
        tz = int(params.get("base_tz_offset_min") or 0)
        plan = lc.plan_from_fit(family, rule, base_tz_offset_min=tz)
        if plan is None:
            return Outcome("failed", None,
                           f"план формы семьи {family!r} не собран: посадка "
                           f"правила не несёт нужных параметров")
        check, found = lc.run_plan(rid, rule, plan, dataset, counter)
        return _code_outcome(expected, check, found, symbol)
    if symbol == "_rest_floor":
        checks, found = [], []
        for kind, name in (("home", "min_rest_home"),
                           ("outstation", "min_rest_outstation")):
            value = params.get(name)
            if value is None:
                return Outcome("failed", None,
                               f"мир не называет {name}: пол отдыха "
                               f"{kind!r} судить нечем")
            c, f = fc._rest_floor(rid, rule, dataset, counter, kind, value)
            checks.append(c)
            found.extend(f)
        bad = [c for c in checks if c.get("status") in BAD_STATUS]
        if bad:
            return Outcome("failed", {"status": bad[0].get("status")},
                           f"проверка «{bad[0].get('status')}»: "
                           f"{bad[0].get('reason') or 'причина не названа'}")
        return _compare(expected, len(found), symbol, key="findings")
    simple = {"_monthly_block": ("max_monthly_block",),
              "_turnaround": ("min_turnaround",),
              "_landings_per_duty": ("max_landings_per_duty",),
              "_pairing_days": ("max_pairing_days",)}
    if symbol in simple:
        (name,) = simple[symbol]
        value = params.get(name)
        if value is None:
            return Outcome("failed", None,
                           f"мир не называет {name}: проверка {symbol} "
                           f"своего числа не берёт")
        check, found = getattr(fc, symbol)(rid, rule, dataset, counter, value)
        return _code_outcome(expected, check, found, symbol)
    if symbol == "_fleet_qualification":
        check, found = fc._fleet_qualification(rid, rule, dataset, counter)
        return _code_outcome(expected, check, found, symbol)
    if symbol == "apply_extension_caps":
        from vmp.run import roster_certify as rc
        specs, _checks, _found, _rest = fc.fit_code_checks(svod, dataset)
        _kept, _notes, checks, found = rc.apply_extension_caps(
            svod, specs, dataset)
        if not checks:
            return Outcome("failed", None,
                           "потолок продления не применялся: проверки кода "
                           "нет (свод без спецификации базового предела "
                           "дополняющей семьи либо потолок не строже)")
        return _code_outcome(expected, checks[0], found, symbol)
    return _no_judge("check_code", symbol)


def _code_outcome(expected, check, found, symbol) -> Outcome:
    if check.get("status") in BAD_STATUS:
        return Outcome("failed", {"status": check.get("status")},
                       f"проверка «{check.get('status')}»: "
                       f"{check.get('reason') or 'причина не названа'}")
    return _compare(expected, len(found), symbol, key="findings")


# ------------------------------------------------------------- сравнение

def _compare(expected: dict, got, symbol: str, key: str = "value") -> Outcome:
    want = expected.get(key)
    note = (f"получено {got}, ожидалось {want} ({symbol})")
    return Outcome("passed" if got == want else "failed", got, note)


def _render(values: dict) -> str:
    """Ожидание-объект словами и коротко: «findings=1, params.days=[…]».

    Эта строка живёт в `stand_verified` файла привязок и в сертификате —
    JSON целиком превратил бы её в шум."""
    parts = []
    for key in sorted(values):
        value = values[key]
        if isinstance(value, dict):
            parts += [f"{key}.{n}={value[n]!r}" for n in sorted(value)]
        else:
            parts.append(f"{key}={value!r}")
    return ", ".join(parts) or "—"


def _compare_map(expected: dict, got: dict, symbol: str) -> Outcome:
    """Сравнение ожидания-объекта: каждое НАЗВАННОЕ ожиданием поле должно
    сойтись. `params` — вложенная карта и судится по названным именам:
    ожидание называет параметры, которые решают исход, а правило свода
    законно несёт и другие (потолок продления, условия исключения)."""
    diff = []
    for key, want in sorted(expected.items()):
        if key not in got:
            # Ожидание `null` — «этим видом не судится»; ожидание с
            # числом, которого исполнитель не дал, — расхождение, а не
            # молчаливый пропуск.
            if want is not None:
                diff.append(f"{key}: исполнитель значения не дал, "
                            f"ожидалось {want!r}")
            continue
        if key == "params" and isinstance(want, dict) \
                and isinstance(got[key], dict):
            for name, value in sorted(want.items()):
                if got[key].get(name) != value:
                    diff.append(f"params.{name}: получено "
                                f"{got[key].get(name)!r}, ожидалось {value!r}")
            continue
        if got[key] != want:
            diff.append(f"{key}: получено {got[key]!r}, ожидалось {want!r}")
    shown = {k: (({n: got[k].get(n) for n in sorted(v)}
                  if k == "params" and isinstance(v, dict)
                  and isinstance(got.get(k), dict) else got.get(k)))
             for k, v in expected.items() if k in got}
    note = ("получено %s, ожидалось %s (%s)"
            % (_render(shown), _render(expected), symbol))
    return Outcome("passed" if not diff else "failed", got,
                   note if not diff else note + "; " + "; ".join(diff))


def _judge_export_form(ctx) -> Outcome:
    """Explicit component world -> real conditions exporter -> real reader.

    This is executor evidence, not package admission or support approval.
    Auxiliary conditions inputs must be declared by the world; no norms or
    payload are invented from the expected values. Export refusal is failed,
    never successful support evidence merely because refusal was anticipated.
    """
    expected = ctx.get("expected")
    from template_catalog.stand_contracts import export_form_expectation_problem
    problem = export_form_expectation_problem(expected)
    if problem:
        raise ValueError(problem)
    world = ctx["world"]
    inputs = world.get("export_form_input")
    if (not isinstance(inputs, dict)
            or set(inputs) != {"model", "forms", "manifest"}
            or any(not isinstance(inputs[key], dict)
                   for key in ("model", "forms", "manifest"))):
        raise ValueError("export_form_input: нужны ровно объекты model, forms, manifest")
    from vmp import conditions_export
    from _conditions import Conditions
    payload, _ = sa.world_to_builder(world)
    conditions = conditions_export.build_conditions(
        inputs["model"], inputs["forms"], inputs["manifest"], payload)
    reader = Conditions(conditions)
    symbol = expected["symbol"]
    if symbol == "auto_plan_excluded_fleets":
        # A set-valued reader: order is not meaning, exact designations are.
        got = {"symbol": symbol, "fleets": sorted(reader.auto_plan_excluded_fleets())}
        expected = {**expected, "fleets": sorted(expected["fleets"])}
    else:
        got = {"symbol": symbol, "probes": []}
        for probe in expected["probes"]:
            if symbol == "required_layover":
                value = reader.required_layover(probe["work_min"], probe["on_base"],
                                               probe["night"], probe["trans"])
            else:
                value = reader.exact_limits(probe["minute"], probe["landings"],
                                            probe["acclimatized"], probe["fleet"])[0]
            got["probes"].append({**probe, "value_min": value})
    return _compare_map(expected, got,
                        "conditions_export.build_conditions -> Conditions." +
                        ("exact_limits" if symbol == "worktime_exact" else symbol))


# ------------------------------------------------------------------ прогон

JUDGES = {"builder_method": _judge_builder,
          "check_template": _judge_check_template,
          "check_code": _judge_check_code,
          "export_form": _judge_export_form}


def _kind_outcome(kind: str, ctx, *, solver=None) -> Outcome:
    form = (ctx.get("world") or {}).get("form")
    if form not in BUILDABLE_WORLD_FORMS:
        return Outcome("skipped", None,
                       f"мир формы «{form}» стенд COSMOS не строит: "
                       f"задачу судит стенд линии данных CDPM")
    if kind in ("kotlin_knob", "kotlin_form"):
        evidence = ctx.get("component_evidence")
        if evidence is None:
            return Outcome("skipped", None,
                           "нет явного измерения Kotlin-компонента; символ и чужой вид не доказательство")
        try:
            got = evidence.judge(kind, ctx["expected"],
                                 ctx["section"] + "/" + ctx["record"]["id"],
                                 ctx["task_id"], ctx["world"])
            return Outcome("passed", got,
                           "точные входы и код; измерение Kotlin-компонента, не допуск/живой стенд")
        except Exception as error:
            return Outcome("failed", None, f"{type(error).__name__}: {error}")
    qualification = ctx.get("qualification_evidence")
    record_id = (ctx["section"] + "/" + ctx["record"]["id"]
                 if kind in ("kotlin_constraint", "kotlin_metric") and qualification is not None else None)
    if record_id is not None and qualification.contains(record_id, ctx["task_id"]):
        try:
            measured = qualification.judge(kind, ctx["expected"], record_id, ctx["task_id"], ctx["world"])
            from vmp.qualification_run import PROOF_SOURCE
            ctx["proof_source"] = PROOF_SOURCE
            ctx["qualification_run_id"] = measured["run_id"]
            ctx["qualification_input_fingerprint"] = measured["input_fingerprint"]
            ctx["termination"] = measured["termination"]
            return Outcome("passed", measured["got"],
                "Local-only qualification: actual bounded solve and declared-world breakdown; not HTTP/live admission")
        except Exception as error:
            return Outcome("failed", None, f"qualification run: {type(error).__name__}: {error}")
    if kind in SOLVER_KINDS:
        if solver is None:
            return Outcome("skipped", None, SOLVER_NOTE)
        return _judge_with_solver(kind, ctx, solver)
    judge = JUDGES.get(kind)
    if judge is None:
        return Outcome("skipped", None,
                       f"вид {kind!r} стендом не судится: судьи у него нет "
                       f"по замыслу (его свидетель — гейт символов)")
    try:
        return judge(ctx)
    except Exception as e:                                   # noqa: BLE001
        return Outcome("failed", None,
                       f"{type(e).__name__}: {e}")


class SolverGate:
    """Один клиент решателя на весь прогон стенда — и состояние службы.

    Два правила живут здесь, а не в судье строки.

    КЛИЕНТ ОДИН. `SolverHttp` держит свой пул соединений; клиент на
    каждую строку означал бы два десятка пулов к чужой службе за один
    прогон и ни одного закрытого. Замысел — «тот же клиент, которым
    ходит кнопка», а не «столько клиентов, сколько строк».

    ПУЛ ПРОВЕРЯЕТСЯ ДО СЕРИИ. Пул прогонов службы мал
    (`newFixedThreadPool(2)`), и занятый пул стенд обязан опознать
    прежде, чем поставит в очередь два десятка своих прогонов: живая
    находка 08.09.2026 — прогон на занятом экземпляре не начинается
    вовсе, каждая строка ждала бы полный потолок, а очередь чужой
    службы росла бы на каждую. Поэтому: сперва живость ручкой кнопки
    (`health`, прогонов не ставит), затем ПЕРВЫЙ прогон серии с
    коротким потолком (`stand_solver.PROBE_WAIT_S`) — не дошёл до
    «готов», значит считать некому, и остальные строки красные СЛОВАМИ
    без единого нового прогона.

    СЕРИЯ ОБРЫВАЕТСЯ РАНО (просьба Ж-3). Три отказа ПОДРЯД БЕЗ ЖИВОГО
    ОТВЕТА службы (`unanswered`: соединение не встало, состояние не
    отдано, потолок ожидания истёк) — это «считать некому», и остальные
    живые строки красные словами без новых прогонов. ЛЮБОЙ ответ службы
    счётчик сбрасывает: и пакет, отвергнутый её гейтом, и прогон,
    упавший на своём пакете (`run_failed`) — служба отвечает, серию
    вести можно, а свидетельство прочих строк терять не за что.

    БЮДЖЕТ КОМАНДЫ ЖИВЁТ ЗДЕСЬ ЖЕ (`budget_s`, просьба Ж-3). Отсчёт идёт
    ОТ НАЧАЛА СЕРИИ (заведения шлюза): предел этот — предел ВРЕМЕНИ
    КОМАНДЫ, ради потолка конвейера он и заведён, поэтому секунды чистых
    судей в него входят, хотя обрывать их бюджет не может (они идут
    секундами и сети не касаются). Исчерпав бюджет, стенд новых прогонов
    не ставит, а оставшиеся живые строки закрывает словами «бюджет стенда
    исчерпан» — файл результатов пишется с частичными строками, а не
    обрывается потолком команды кодом 124 без диагноза.

    ОСТАТОК БЮДЖЕТА УЕЗЖАЕТ В СТРОКУ (`left_s`, правка по ревью
    08.09.2026). Прежде бюджет судился только ПЕРЕД постановкой прогона,
    и строка, начатая у самой границы, шла своими прежними потолками
    (клиент 120 с × 3 обращения, ожидание «готов» 60 с) — до ~360 с
    сверх предела; обещание «стенд укладывается в предел команды сам»
    держалось на удаче. Теперь каждое звено строки получает потолок не
    больше остатка, а кончившийся посреди строки остаток — красная
    строка словами бюджета.
    """

    def __init__(self, url: str, *, budget_s=None, clock=None,
                 deterministic: bool = False):
        self.url = str(url or "").strip()
        self.state = "unknown"      # unknown | ok | dead | busy
        self.reason = None
        self._client = None
        self._ready_seen = False
        self._misses = 0
        # Часы берутся В МОМЕНТ ВЫЗОВА, а не умолчанием аргумента: иначе
        # это не ручка — подмена `time.monotonic` в тесте не действовала
        # бы, и проводка ключа `--budget-s` осталась бы без свидетеля
        # (тот же урок, что у `WAIT_S`/`POLL_S` стенда).
        clock = clock or time.monotonic
        self._clock = clock
        self.deterministic = bool(deterministic)
        self.budget_s = None if budget_s is None else float(budget_s)
        self._deadline = (None if self.budget_s is None
                          else clock() + self.budget_s)

    def profile(self):
        """Профиль прогона Д1 либо None (умолчание — стенные часы)."""
        if not self.deterministic:
            return None
        from vmp import stand_solver as ss
        return dict(ss.DETERMINISTIC)

    def left_s(self):
        """Остаток бюджета команды в секундах либо None (предела нет).

        Уезжает В СТРОКУ (`stand_solver.measure(left_s=…)`): звенья
        строки берут из него свои потолки, и начатая у границы строка
        предел команды не переезжает.
        """
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - self._clock())

    def over_budget(self):
        """Слова об исчерпанном бюджете команды либо None."""
        if self._deadline is None or self._clock() <= self._deadline:
            return None
        return (f"{BUDGET_WORDS}: живые строки после него прогонов не "
                f"ставили (предел --budget-s = {self.budget_s:g} с) — "
                f"увеличьте предел либо сузьте прогон ключом --records")

    def spent_mid_row(self, detail: str) -> str:
        """Слова строки, у которой бюджет кончился ПОСРЕДИ прогона.

        Начинаются теми же словами `BUDGET_WORDS`, что и слова строки
        без прогона: обе — частичные строки одного упора в предел, и
        считает их один `partial_rows`.
        """
        return (f"{BUDGET_WORDS} посреди строки (предел --budget-s = "
                f"{self.budget_s:g} с): {detail} — увеличьте предел либо "
                f"сузьте прогон ключом --records")

    def client(self):
        if self._client is None:
            from vmp import stand_solver as ss
            self._client = ss.solver_client(self.url)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            from vmp import stand_solver as ss
            ss.close_client(self._client)
            self._client = None

    def wait_s(self):
        """Потолок ожидания «готов»: короткий у ПЕРВОГО прогона серии
        (проба пула), полный — после первого дошедшего."""
        from vmp import stand_solver as ss
        return None if self._ready_seen else ss.PROBE_WAIT_S

    def blocked(self):
        """Слова, почему прогон ставить нельзя, либо None.

        Живость спрашивается один раз и ДО первого прогона: молчащая
        служба не должна порождать двадцать четыре одинаковых ожидания.
        Бюджет судится ПЕРЕД живостью: исчерпав его, стенд не ходит к
        службе вовсе — ни прогоном, ни вопросом о живости.
        """
        if self.state in ("dead", "busy"):
            return self.reason
        spent = self.over_budget()
        if spent:
            return spent
        if self.state == "unknown":
            try:
                alive = bool(self.client().health())
                words = None
            except Exception as e:                            # noqa: BLE001
                alive, words = False, f"{type(e).__name__}: {e}"
            if alive:
                self.state = "ok"
            else:
                self.state = "dead"
                self.reason = (
                    "решатель не отвечает (рабочая ручка службы молчит"
                    + (f": {words}" if words else "")
                    + "): прогоны не ставились — поднимите решатель по "
                    "адресу ключа --with-solver и повторите")
        return self.reason if self.state in ("dead", "busy") else None

    def seen_ready(self) -> None:
        self._ready_seen = True
        self._misses = 0

    def note_failure(self, exc) -> None:
        """Отказ строки — не всегда беда службы: пакет, отвергнутый её
        гейтом, законен, и упавший прогон — тоже её ответ. А вот прогон,
        оставшийся в ОЧЕРЕДИ, значит занятый пул, и серию дальше вести
        нельзя.

        Отказы БЕЗ ЖИВОГО ОТВЕТА (`unanswered`: соединение не встало,
        состояние не отдано, потолок ожидания истёк) считаются ПОДРЯД
        (Ж-3): три таких — «считать некому», серия прекращается словами.
        Любой отказ, в котором служба ОТВЕТИЛА (гейт отверг пакет,
        прогон упал на своём пакете), счётчик сбрасывает: обрывать серию
        по ним значило бы называть мёртвой службу, которая отвечает, и
        терять свидетельство прочих строк.
        """
        if getattr(exc, "unanswered", False):
            self._misses += 1
            if self._misses >= READY_MISS_LIMIT and self.state != "busy":
                self.state = "dead"
                self.reason = (
                    f"{READY_MISS_WORDS}; последний отказ — «{exc}». "
                    f"Остальные живые строки прогонов НЕ ставили: пул "
                    f"чужой службы стенд не набивает. Поднимите решатель "
                    f"по адресу ключа --with-solver и повторите")
        else:
            self._misses = 0
        if getattr(exc, "queued", False):
            self.state = "busy"
            self.reason = (
                "решатель занят: прогон стенда не дошёл до состояния "
                "«готов» и остался в очереди службы — её пул прогонов "
                "держит чужая работа; остальные строки видов решателя "
                "прогонов НЕ ставили, чтобы не набивать очередь. "
                "Освободите пул (перезапуск экземпляра) и повторите")


def _judge_with_solver(kind, ctx, solver: SolverGate):
    """Живой прогон Kotlin-вида (§5.6, путь 1) — через `vmp.stand_solver`.

    Пакет постановки собирается библиотекой CDPM по своду ТОЙ ЖЕ задачи,
    уходит прогоном по идентификатору, и повкладовый счёт назначений
    стенда под пакетом прогона даёт числа. Несостоявшееся звено (решатель
    молчит, пакет отвергнут его гейтом, ограничения нет в разборе) — это
    `failed` СЛОВАМИ отказа, а не `skipped`: ожидание записи остаётся
    неподтверждённым, и молчаливого пропуска у этого не бывает.
    """
    from vmp import stand_solver as ss
    blocked = solver.blocked()
    if blocked:
        return Outcome("failed", None, blocked)
    cat = ctx.get("catalog")
    try:
        out = ss.measure(
            kind, world=ctx.get("world") or {}, expected=ctx["expected"],
            families=ctx.get("families") or [], meta=ctx.get("meta") or {},
            stamp=ss.stamp_of(cat), client=solver.client(),
            base=ss.run_base(ctx.get("tmp_dir"), ctx.get("task_id")),
            record=ctx.get("record"), family=ctx.get("family"),
            wait_s=solver.wait_s(), deterministic=solver.profile(),
            left_s=solver.left_s(), catalog=cat,
            **({"spec_version": ctx["spec_version"]}
               if ctx.get("spec_version") is not None else {}))
    except ss.StandSolverError as e:
        # Прогон, если он успел начаться, — В СТРОКЕ: «ограничения нет в
        # разборе», «форма не порождена», «счёт не отдан» случаются, когда
        # прогон в реестре службы уже есть, и красная строка обязана
        # прослеживаться до него так же, как зелёная.
        ctx["run_id"] = getattr(e, "run_id", None)
        solver.note_failure(e)
        if getattr(e, "out_of_time", False):
            # Остаток бюджета кончился посреди строки — слова у неё
            # БЮДЖЕТНЫЕ, теми же словами, что у строк без прогона:
            # частичные строки считает один `partial_rows`.
            return Outcome("failed", None, solver.spent_mid_row(str(e)))
        return Outcome("failed", None, str(e))
    solver.seen_ready()
    ctx["run_id"] = out.get("run_id")
    ctx["package_fingerprint"] = out.get("package_fingerprint")
    ctx["termination"] = out.get("termination")
    # Чем меряется штраф, объявляет ЗАПИСЬ (`violation_size`, выпуск
    # 0.5.0), а не вид исполнителя: расхождение поля и чисел решателя —
    # `failed` СЛОВАМИ, а не тихое совпадение двух чисел.
    size = _dct(ctx.get("record")).get("violation_size")
    if kind == "kotlin_constraint":
        why = violation_size_refusal(size, ctx["expected"], out["got"])
        if why:
            return Outcome("failed", out["got"], why)
    outcome = _compare_map(ctx["expected"], out["got"],
                           SOLVER_SYMBOL.get(kind, kind))
    # Прогон и отпечаток пакета — ПОЛЯМИ строки, а не словами внутри
    # `note` (правка канона): `note` уезжает в `stand_verified` привязок,
    # и идентификатор прогона делал бы файл привязок каноном ПРОГОНА, а
    # не каталога — двадцать четыре изменённые строки при побайтово тех
    # же числах. Прослеживаемость от этого не теряется: поля строки несут
    # прогон и отпечаток, адрес службы назван шапкой файла результатов, а
    # печатная таблица дописывает их к строке словами.
    note = outcome.note
    if out.get("note"):
        note = f"{note}; {out['note']}"
    field = violation_measure(size) if kind == "kotlin_constraint" else None
    if field:
        # Мера штрафа названа В СТРОКЕ: читающий строку видит, какое
        # число судила запись, а не догадывается по виду исполнителя.
        # Только у ограничения: `kotlin_logic_form` меры не считает
        # (`amount` у него null по замыслу), и слова о мере там лгали бы.
        note = f"{note}; мера штрафа — {field} (размер нарушения «{size}»)"
    return Outcome(outcome.result, outcome.got, note)


#: Чем судим Kotlin-вид — словами в строке результата (место `symbol`
#: чистых видов): ручкой решателя, а не именем функции стенда.
SOLVER_SYMBOL = {
    "kotlin_constraint": "score-breakdown прогона",
    "kotlin_logic_form": "score-breakdown прогона (порождённое ограничение)",
    "kotlin_metric": "score-breakdown прогона (целевая пакета)",
}


def run_tasks(*, catalog=None, kinds=None, solver_url=None,
              tmp_dir=None, records=None, budget_s=None,
              deterministic: bool = False,
              spec_version: str | None = None, component_evidence=None,
              qualification_evidence=None) -> list:
    """Прогон задач каталога: список строк результата (форма §5.5).

    Клиент решателя здесь ОДИН на всю серию (`SolverGate`) и закрывается
    по её окончании: пул соединений на каждую строку — не замысел «один
    клиент кнопки», и открытые соединения к чужой службе после прогона
    не остаются.

    `budget_s` — предел ВРЕМЕНИ КОМАНДЫ (Ж-3), и отсчёт идёт от начала
    серии: ради потолка команды конвейера предел и заведён, поэтому
    секунды чистых судей в него входят. Обрывать чистые виды бюджет не
    может: они идут секундами, сети не касаются, и терять их
    свидетельство не за что. Исчерпав предел, стенд новых прогонов не
    ставит, а оставшиеся живые строки закрывает словами; остаток предела
    уезжает в живую строку и ограничивает её звенья (`SolverGate.left_s`).
    `deterministic` — профиль Д1 (умолчание ВЫКЛ).
    `spec_version` — явный контракт пакетов только для прогона с решателем;
    None сохраняет умолчание производителя и прежнюю форму вызовов.
    """
    if spec_version is not None and not solver_url:
        raise ValueError("spec_version имеет смысл только с solver_url: "
                         "без решателя пакеты стенда не собираются")
    if qualification_evidence is not None and solver_url:
        raise ValueError("qualification evidence and live HTTP solver are separate proof sources")
    if solver_url and spec_version is None:
        # РВ-138, исправление 3: живой прогон — единственный путь стенда к
        # сборке пакета постановки (`stand_solver.run_world` →
        # `package_bytes` → `export_zip`). Контракт пакета без явного
        # `spec_version` берётся из окружения производителя, поэтому
        # окружение обязано быть пусковиковым — иначе пакет уйдёт
        # умолчанием 1.18 и решатель прочтёт его «архивом». Отказ — до
        # первой сборки, а не красными строками после.
        from vmp import launcher_env
        words = launcher_env.refusal(
            purpose="живой прогон стенда (сборка пакетов постановки)")
        if words:
            raise StandRunError(words)
    cat = catalog if hasattr(catalog, "families_full") else _catalog(catalog)
    meta = _family_meta(cat)
    want = tuple(kinds) if kinds else None
    solver = (SolverGate(solver_url, budget_s=budget_s,
                         deterministic=deterministic)
              if solver_url else None)
    rows = []
    try:
        for section, record in _records(cat):
            rid = str(record.get("id"))
            if records and rid not in records:
                continue
            family = rid if section == "families" else None
            for task in record.get("stand_tasks") or ():
                if not isinstance(task, dict):
                    continue
                world = task.get("world") or {}
                ctx = {"world": world, "family": family, "record": record,
                       "section": section, "meta": meta, "catalog": cat,
                       "tmp_dir": tmp_dir, "task_id": str(task.get("id")),
                       "families": families_for(world, family, meta)}
                if component_evidence is not None:
                    ctx["component_evidence"] = component_evidence
                if qualification_evidence is not None:
                    ctx["qualification_evidence"] = qualification_evidence
                if spec_version is not None:
                    ctx["spec_version"] = spec_version
                for kind in sorted(task.get("expected") or {}):
                    if want and kind not in want:
                        continue
                    ctx["expected"] = dict((task["expected"] or {})[kind])
                    ctx["run_id"] = None
                    ctx["package_fingerprint"] = None
                    ctx["termination"] = None
                    for field in ("proof_source", "qualification_run_id", "qualification_input_fingerprint"):
                        ctx.pop(field, None)
                    out = _kind_outcome(kind, ctx, solver=solver)
                    key = (f"{section}/{rid}", str(task.get("id")), kind)
                    result = out.result
                    note = out.note
                    if result == "failed" and key in KNOWN_FAILED:
                        note = f"{note}; KNOWN_FAILED: {KNOWN_FAILED[key]}"
                    row = {
                        "record": f"{section}/{rid}",
                        "task": str(task.get("id")),
                        "executor_kind": kind, "result": result,
                        "got": out.got, "expected": ctx["expected"],
                        "note": note, "run_id": ctx.get("run_id"),
                        "package_fingerprint": ctx.get("package_fingerprint")}
                    if ctx.get("termination"):
                        # Признак остановки — поле ТОЛЬКО когда служба
                        # его дала (профиль Д1 и решатель с правкой):
                        # пустое поле в каждой строке меняло бы файл
                        # результатов всем, кто ключом не пользуется.
                        row["termination"] = ctx["termination"]
                    for field in ("proof_source", "qualification_run_id", "qualification_input_fingerprint"):
                        if field in ctx:
                            row[field] = ctx[field]
                    rows.append(row)
    finally:
        if solver is not None:
            solver.close()
    rows.sort(key=lambda r: (r["record"], r["task"], r["executor_kind"]))
    return rows


def results_document(rows: list, cat, *, at=None, commit=None,
                     solver=None, repo=None, component_reference=None,
                     qualification_reference=None, environment=None) -> dict:
    """Файл результатов формы §5.5.

    Коммит прогона ОБЯЗАТЕЛЕН и судится здесь, на записи: без git (не
    рабочее дерево, git не в пути) прежний код молча ставил `null`,
    строка уезжала в `stand_verified` привязок, и файл отвергала уже
    дверь — образцом схемы `$defs/commit`. Отказ на месте записи
    называет, что делать."""
    head = commit or _head_commit(repo)
    if not _COMMIT_RE.match(str(head or "")):
        raise StandRunError(
            f"коммит прогона не определён ({head!r}): git недоступен либо "
            f"это не рабочее дерево — задайте --commit <7–40 знаков "
            f"0-9a-f>; файл результатов без коммита отвергнет дверь "
            f"привязок (образец commit схемы читателя)")
    result = {"form_version": 1,
            "catalog_version": cat.version(),
            "catalog_fingerprint": cat.fingerprint(),
            "code": code_fingerprints(repo),
            "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": head,
            "solver": solver,
            "results": rows}
    if component_reference is not None:
        result["component_evidence"] = component_reference
    if qualification_reference is not None:
        result["qualification_evidence"] = qualification_reference
    if environment is not None:
        result["environment"] = environment
    problems = _component_stale_reasons(result, cat, repo) + _qualification_stale_reasons(result, cat, repo)
    if problems:
        raise StandRunError("; ".join(problems))
    return result


def _component_stale_reasons(doc, cat, repo=None):
    selected = [row for row in doc.get("results", [])
                if row.get("executor_kind") in ("kotlin_knob", "kotlin_form")
                and row.get("result") == "passed"]
    if not selected and "component_evidence" not in doc:
        return []
    try:
        from vmp.stand_components import load_reference
        evidence = load_reference(doc.get("component_evidence"), catalog=cat, repo=repo or REPO)
        if evidence.document["producer"]["commit"] != doc.get("commit"):
            raise ValueError("component producer and stand commit differ")
        seen = set()
        for row in selected:
            key = (row["record"], row["task"], row["executor_kind"])
            if key in seen:
                raise ValueError("duplicate component stand row")
            seen.add(key)
            task = evidence.declared.get(key[:2])
            if task is None:
                raise ValueError("component stand row has unknown task")
            got = evidence.judge(key[2], row["expected"], key[0], key[1], task["world"])
            if got != row.get("got"):
                raise ValueError("component stand row differs from primary measurements")
        return []
    except Exception as error:
        return [f"Kotlin component evidence invalid: {type(error).__name__}: {error}"]


def _qualification_stale_reasons(doc, cat, repo=None):
    """A result file cannot relabel Local qualification as an HTTP run."""
    from vmp.qualification_run import KINDS, PROOF_SOURCE, load_reference
    selected = [row for row in doc.get("results", []) if row.get("proof_source") == PROOF_SOURCE
                or "qualification_run_id" in row or "qualification_input_fingerprint" in row
                or (row.get("executor_kind") in KINDS and row.get("result") == "passed"
                    and doc.get("solver") is None)]
    if not selected and "qualification_evidence" not in doc:
        return []
    try:
        if doc.get("solver") is not None:
            raise ValueError("qualification results cannot claim a live HTTP solver")
        evidence = load_reference(doc.get("qualification_evidence"), expected_commit=doc.get("commit"), catalog=cat, repo=repo or REPO)
        if evidence.document["producer"]["commit"] != doc.get("commit"):
            raise ValueError("qualification producer and stand commit differ")
        seen = set()
        for row in doc.get("results", []):
            key = (row.get("record"), row.get("task"))
            if row.get("executor_kind") in KINDS and evidence.contains(*key) and row.get("result") == "passed" and row not in selected:
                raise ValueError("qualification passed row lost its proof_source")
        for row in selected:
            key = (row["record"], row["task"])
            if (key in seen or row.get("proof_source") != PROOF_SOURCE or row.get("executor_kind") not in KINDS
                    or row.get("result") != "passed" or row.get("run_id") is not None or row.get("package_fingerprint") is not None):
                raise ValueError("invalid/duplicate/relabelled qualification result row")
            seen.add(key)
            task = evidence.declared.get(key)
            if task is None:
                raise ValueError("unknown qualification task")
            measured = evidence.judge(row["executor_kind"], row["expected"], *key, task["world"])
            if (measured["got"] != row.get("got") or measured["run_id"] != row.get("qualification_run_id")
                    or measured["input_fingerprint"] != row.get("qualification_input_fingerprint")
                    or measured["termination"] != row.get("termination")):
                raise ValueError("qualification row differs from primary actual run")
        return []
    except Exception as error:
        return [f"Kotlin qualification evidence invalid: {type(error).__name__}: {error}"]


def stale_reasons(doc: dict, cat, repo=None) -> list:
    """Почему файл результатов устарел: слова, не булево.

    Карта `code` судится В ОБЕ СТОРОНЫ: не только «названные отпечатки
    сошлись с диском», но и «названы РОВНО те файлы, которыми стенд
    свеж» (`CODE_FILES`). Односторонний суд принимал бы укороченную
    карту (в пределе пустую) за свежесть, и чужие исходы попадали бы в
    привязки без единого прогона исполнителя — файл результатов не может
    лгать ни тем, что сказал, ни тем, о чём умолчал."""
    out = _component_stale_reasons(doc, cat, repo) + _qualification_stale_reasons(doc, cat, repo)
    from vmp import launcher_env
    outside = launcher_env.stale_reason(doc)
    if outside:
        out.append(outside)
    if doc.get("catalog_fingerprint") != cat.fingerprint():
        out.append(
            f"отпечаток каталога в результатах "
            f"{str(doc.get('catalog_fingerprint'))[:12]}… не совпадает с "
            f"текущим {cat.fingerprint()[:12]}…")
    if doc.get("catalog_version") != cat.version():
        out.append(f"версия каталога в результатах "
                   f"{doc.get('catalog_version')!r} не совпадает с текущей "
                   f"{cat.version()!r}")
    named = {str(k) for k in (doc.get("code") or {})}
    current = code_fingerprints(repo)
    for name in sorted(set(current) - named):
        out.append(f"карта code результатов не называет {name}: свежесть "
                   f"этого файла не подтверждена")
    for name in sorted(named - set(current)):
        out.append(f"карта code результатов называет {name} — его нет среди "
                   f"файлов, которыми стенд свеж")
    for name, value in sorted(current.items()):
        if name in named and (doc.get("code") or {}).get(name) != value:
            out.append(f"отпечаток {name} изменился с записи результатов")
    return out


def unexpected_failures(rows: list) -> list:
    return [r for r in rows if r["result"] == "failed"
            and (r["record"], r["task"], r["executor_kind"]) not in KNOWN_FAILED]


def partial_rows(rows: list) -> list:
    """Строки, оставшиеся БЕЗ прогона по пределу времени команды.

    Отдельный счёт нужен, чтобы упор в бюджет было видно словами и в
    итоге команды: «строк меньше» без объяснения читается как «задач
    меньше», а это неправда — прогоны не ставились.
    """
    return [r for r in rows if str(r.get("note") or "").startswith(
        BUDGET_WORDS)]


def solver_header(url, *, artifact_sha256=None) -> dict | None:
    """Шапка `solver` файла результатов (§5.5 + просьба Ж-4).

    `artifact_sha256` — отпечаток артефакта решателя, отвечающего по
    адресу: с ним привязка «адрес → артефакт» живёт В ФАЙЛЕ РЕЗУЛЬТАТОВ,
    а не только в свидетельстве кандидата. Значение приводится к
    строчным знакам (канон `hexdigest`): `Get-FileHash` на площадке даёт
    заглавные, и один артефакт обязан выглядеть в файле одинаково.
    Ключа нет — поле `null`, и молчаливым это не остаётся: причина
    сказана словами в `note`, иначе `null` читался бы как «артефакт тот
    же».
    """
    if not url:
        return None
    sha = canon_sha256(artifact_sha256)
    note = PACKAGE_PER_TASK if sha else f"{PACKAGE_PER_TASK}; {IDENTITY_NOTE}"
    return {"url": str(url), "package_fingerprint": None,
            "artifact_sha256": sha, "note": note}


# ------------------------------------------------------------------ команда

def _trace(row: dict) -> str:
    """Хвост ПЕЧАТНОЙ строки: прогон службы, отпечаток его пакета и
    признак остановки прогона.

    В `note` их нет по замыслу (`note` уезжает в `stand_verified`
    привязок и обязан быть каноном КАТАЛОГА, а не прогона), но у экрана
    прослеживаемость остаётся: печатная таблица — свидетельство этого
    прогона, а не файл каталога. Адрес службы называет строка «решатель»
    над таблицей и шапка `solver` файла результатов."""
    parts = []
    if row.get("run_id"):
        parts.append("прогон %s" % row["run_id"])
    fp = str(row.get("package_fingerprint") or "")
    if fp:
        parts.append("пакет %s…" % fp[:12])
    stop = row.get("termination") if isinstance(row.get("termination"),
                                                dict) else None
    if stop and stop.get("words"):
        parts.append(str(stop["words"]))
    return ("  [" + ", ".join(parts) + "]") if parts else ""


def termination_counts(rows: list) -> dict:
    """Сколько живых строк каким видом остановки кончилось.

    Итог команды под ключом `--deterministic` говорит о сборке решателя
    ПО СТРОКАМ, а не догадкой: строки с полем `termination` пришли от
    решателя С правкой Д1, строки без него — от службы, поля не давшей.
    Прежде итог печатал `d1_words()` без ответа службы и утверждал
    «сборка решателя без правки Д1» даже тогда, когда каждая строка
    прогона несла признак остановки (правка по ревью 08.09.2026).
    """
    out = {}
    for row in rows:
        if not row.get("run_id"):
            continue
        stop = row.get("termination")
        kind = str(stop.get("kind") or "?") if isinstance(stop, dict) \
            else None
        out[kind] = out.get(kind, 0) + 1
    return out


def _table(rows: list) -> str:
    width = [max(len(str(r[k])) for r in rows) if rows else 0
             for k in ("record", "task", "executor_kind", "result")]
    head = ("запись", "задача", "вид", "исход")
    width = [max(w, len(h)) for w, h in zip(width, head)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(head, width))
             + "  получено/ожидалось"]
    for r in rows:
        cells = [str(r[k]).ljust(w) for k, w in
                 zip(("record", "task", "executor_kind", "result"), width)]
        lines.append("  ".join(cells) + "  " + str(r["note"]) + _trace(r))
    return "\n".join(lines)


def _stop_summary(rows: list) -> str:
    """Сводка признаков остановки живых строк словами.

    Судит СТРОКИ прогонов: сколько их пришло с полем `termination` и
    каких видов, сколько — без поля (служба без правки Д1). Утверждать о
    сборке решателя что-либо иное итог команды не вправе: ключ
    `--deterministic` говорит только о том, что профиль запрошен.
    """
    counts = termination_counts(rows)
    if not counts:
        return ("живых прогонов в этом прогоне не было — судить о "
                "признаке остановки не по чему")
    from vmp import stand_solver as ss
    named = {k: n for k, n in counts.items() if k is not None}
    silent = counts.get(None, 0)
    parts = []
    for kind, n in sorted(named.items()):
        known = ss.TERMINATION_KINDS.get(kind) or \
            "вид вне закрытого перечня стенда"
        parts.append("%s — строк %d (%s)" % (kind, n, known))
    if silent:
        parts.append("поля termination нет — строк %d: служба этих "
                     "прогонов старой сборки, без правки Д1" % silent)
    return "; ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default=None,
                    help="папка каталога (умолчание — каталог двери)")
    ap.add_argument("--kinds", default=None,
                    help="виды через запятую (умолчание — все объявленные)")
    ap.add_argument("--component-evidence", type=Path, default=None,
                    help="явный файл измерений Kotlin-компонентов; не запуск службы и не допуск")
    ap.add_argument("--component-inputs", type=Path, default=None,
                    help="неизменяемые исходные ZIP/миры/задачи измерения компонентов")
    ap.add_argument("--spec-version", default=None,
                    help="контракт пакетов разрешённого тестового контура "
                         "(умолчание — версия производителя); только с "
                         "--with-solver. Не меняет глобальное умолчание, "
                         "свидетельство поддержки или акт выпуска")
    ap.add_argument("--records", default=None,
                    help="имена записей через запятую (умолчание — все "
                         "записи каталога). Ключ конвейера пополнения "
                         "(шаг 6): агент, дописавший исполнителя одной "
                         "записи, судит стендом ЕЁ, а не весь каталог — "
                         "прогон записи виден в свидетельстве строкой, а "
                         "не тонет в общем итоге")
    ap.add_argument("--with-solver", dest="solver", default=None,
                    metavar="<адрес>",
                    help="адрес живого решателя для Kotlin-видов — того "
                         "контура, на котором проверяете (решатель Test "
                         "на legion: http://127.0.0.1:8098). Экземпляр "
                         "владельца — только по его слову: его пул "
                         "прогонов занят рабочими задачами. Без ключа "
                         "виды kotlin_* остаются skipped: умолчания у "
                         "адреса нет и не бывает. Отдельные измерения "
                         "kotlin_knob/kotlin_form читаются явным "
                         "--component-evidence; прогон живого "
                         "решателя идёт только по слову владельца")
    ap.add_argument("--solver-identity", dest="solver_identity", default=None,
                    metavar="<sha256>",
                    help="отпечаток артефакта решателя (64 знака 0-9a-f, "
                         "регистр любой — Get-FileHash даёт заглавные), "
                         "отвечающего по адресу --with-solver: уезжает в "
                         "шапку solver.artifact_sha256 файла результатов "
                         "строчными (канон hexdigest), и привязка «адрес → "
                         "артефакт» живёт в самом файле. Без ключа поле "
                         "null, а причина названа словами в note шапки: "
                         "null иначе читался бы как «артефакт тот же»")
    ap.add_argument("--budget-s", dest="budget_s", type=float, default=None,
                    metavar="<секунды>",
                    help="предел времени КОМАНДЫ, отсчёт от начала серии: "
                         "исчерпав его, стенд новых прогонов не ставит, а "
                         "оставшиеся строки видов решателя закрывает словами "
                         "«бюджет стенда исчерпан» — файл результатов "
                         "пишется с частичными строками. Остаток предела "
                         "уезжает В СТРОКУ и ограничивает её звенья, поэтому "
                         "начатая у границы строка предел не переезжает. "
                         "Чистые виды бюджет не обрывает (они идут секундами "
                         "и сети не касаются), но их время в предел входит: "
                         "берите на %d с меньше потолка команды конвейера. "
                         "Без ключа предела нет, и худший случай (решатель "
                         "молчит) упирается в потолок команды кодом 124 без "
                         "диагноза" % BUDGET_MARGIN_S)
    ap.add_argument("--deterministic", action="store_true",
                    help="прогонять живые виды профилем Д1 (зерно 0, "
                         "счётный предел 1). УМОЛЧАНИЕ — ВЫКЛ, и это не "
                         "лень: без правки остановки счётный предел "
                         "судится только между завершёнными шагами поиска "
                         "и на мире без исполнимого хода не наступает "
                         "вовсе (stand_solver.D1_FINDING). Ключ включают "
                         "на решателе С правкой остановки Д1: он называет "
                         "признак остановки полем termination, и стенд "
                         "кладёт его ПОЛЕМ строки (вид, воспроизводимость, "
                         "слова) — в note его нет, note уезжает каноном в "
                         "привязки. Итог команды сводит признаки по "
                         "строкам; служба без правки поля не даёт, и это "
                         "сказано числом строк")
    ap.add_argument("--write-results", type=Path, default=None,
                    help="записать файл результатов (форма §5.5)")
    ap.add_argument("--tmp-dir", type=Path, default=None,
                    help="папка временных файлов судей (контрольная пара); "
                         "без ключа — папка под _repro/stand рядом с "
                         "деревом, которую команда создаёт и УДАЛЯЕТ за "
                         "собой при штатном выходе; прогон, снятый на "
                         "полпути, оставляет _repro/stand/stand-* — её "
                         "убирают рукой")
    ap.add_argument("--at", default=None, help="момент прогона (ISO)")
    ap.add_argument("--commit", default=None, help="коммит прогона (40 hex)")
    ap.add_argument("--outside-launcher", dest="outside_launcher",
                    action="store_true",
                    help="разрешить команду (--with-solver или запись "
                         "результатов) без окружения пусковика площадки — "
                         "лишь для разработки и проб: шапка результатов "
                         "назовёт недостающие переменные, и дверь привязок "
                         "такой файл не примет. Живой прогон с решателем "
                         "вне пусковика этим ключом не открывается: сборка "
                         "пакетов требует ещё явного --spec-version (РВ-138)")
    args = ap.parse_args(argv)
    if (args.solver or args.write_results) and not args.outside_launcher:
        # Класс «вне окружения пусковика» (РВ-138, исправление 3):
        # живой прогон и свидетельство судят то, что исполняет панель, —
        # значит, и окружение у них то же. Неполное — отказ до старта.
        from vmp import launcher_env
        words = launcher_env.refusal(
            purpose="стенд с решателем или записью результатов")
        if words:
            print(words)
            return 2
    if bool(args.component_evidence) != bool(args.component_inputs):
        print("отказ: --component-evidence и --component-inputs задаются вместе")
        return 2
    if args.records and args.write_results:
        # Файл результатов — свидетельство ВСЕГО каталога: строки
        # `stand_verified` привязок собираются из него. Сузив прогон и
        # переписав файл, мы молча стёрли бы исходы прочих записей —
        # отказ на месте, а не потеря в свидетельстве.
        print("отказ: --records сужает прогон до записей, а --write-results "
              "пишет свидетельство всего каталога — вместе они стёрли бы "
              "исходы прочих записей; прогоните запись без "
              "--write-results либо весь каталог без --records")
        return 2
    # Ключи живого прогона без адреса решателя — отказ на месте: ключ,
    # который ничего не делает, читается как сделанная работа.
    for key, value in (("--solver-identity", args.solver_identity),
                       ("--budget-s", args.budget_s),
                       ("--deterministic", args.deterministic),
                       ("--spec-version", args.spec_version is not None)):
        if value and not args.solver:
            print("отказ: ключ %s имеет смысл только с --with-solver "
                  "<адрес> — без адреса виды kotlin_* остаются skipped и "
                  "прогонов нет вовсе" % key)
            return 2
    if args.solver_identity and not _SHA256_RE.match(
            str(args.solver_identity).strip()):
        print("отказ: --solver-identity %r не отпечаток артефакта: нужны "
              "64 знака 0-9a-f (sha256 файла решателя; регистр любой — "
              "Get-FileHash даёт заглавные, в файл уедут строчные) — шапка "
              "результатов лгать о личности артефакта не должна"
              % str(args.solver_identity))
        return 2
    if args.budget_s is not None and args.budget_s <= 0:
        print("отказ: --budget-s %g — бюджет должен быть больше нуля; "
              "нулевой предел закрыл бы словами ВСЕ живые строки, не "
              "поставив ни одного прогона" % args.budget_s)
        return 2
    kinds = [k.strip() for k in (args.kinds or "").split(",") if k.strip()]
    if args.tmp_dir is None:
        # Без ключа команда сама заводит папку прогона и убирает за
        # собой ПРИ ШТАТНОМ ВЫХОДЕ (прогон, снятый на полпути, оставляет
        # _repro/stand/stand-* — так и сказано в справке ключа и в
        # руководстве: её убирают рукой). Место — В САМОМ ДЕРЕВЕ
        # (_repro/stand/<момент>), а не во временном каталоге системы:
        # временные файлы живут либо в tmp_path тестов, либо в папке
        # разбора рядом с кодом, а системный временный каталог Windows
        # чистит при нехватке места — папка, исчезающая посреди прогона,
        # не место для файлов судей.
        import tempfile
        root = TMP_ROOT
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="stand-", dir=str(root)) \
                as tmp:
            return _run(args, kinds, Path(tmp))
    return _run(args, kinds, Path(args.tmp_dir))


def _run(args, kinds: list, tmp: Path) -> int:
    tmp.mkdir(parents=True, exist_ok=True)
    cat = _catalog(args.catalog)
    component = component_ref = None
    if getattr(args, "component_evidence", None) is not None:
        from vmp.stand_components import load_evidence, reference
        try:
            component = load_evidence(args.component_evidence, catalog=cat,
                                      input_root=args.component_inputs, repo=REPO)
            component_ref = reference(args.component_evidence, args.component_inputs)
        except Exception as error:
            print(f"отказ: Kotlin component evidence: {type(error).__name__}: {error}")
            return 2
    records = [r.strip() for r in (getattr(args, "records", None) or "")
               .split(",") if r.strip()]
    rows = run_tasks(catalog=cat, kinds=kinds or None, solver_url=args.solver,
                     tmp_dir=tmp, records=records or None,
                     budget_s=getattr(args, "budget_s", None),
                     deterministic=bool(getattr(args, "deterministic",
                                                False)),
                     **({"component_evidence": component} if component is not None else {}),
                     **({"spec_version": args.spec_version}
                        if getattr(args, "spec_version", None) is not None
                        else {}))
    if records and not rows:
        # Сужение до записи, которой в каталоге нет (либо у неё нет
        # стендовых задач), — не «зелёный ноль»: молчаливого «прогнали
        # ничего» у гейта конвейера быть не может.
        print("отказ: записи %s не дали ни одной стендовой задачи — "
              "проверьте имена (--records)" % ", ".join(records))
        return 2
    print(_table(rows))
    if args.solver:
        # Адрес — один раз и над итогом: в строках он не повторяется
        # (`note` строки — канон каталога, не паспорт прогона).
        print("решатель живого прогона: %s" % args.solver)
        if args.solver_identity:
            print("артефакт решателя: %s" % canon_sha256(args.solver_identity))
        if args.deterministic:
            from vmp import stand_solver as ss
            # Профиль — голова, а признак остановки — СВОДКОЙ ПО СТРОКАМ:
            # об остановке знают строки прогонов, а не ключ команды.
            print("профиль прогона: %s" % ss.d1_words())
            print("признак остановки прогонов: %s" % _stop_summary(rows))
    counts = {k: sum(1 for r in rows if r["result"] == k)
              for k in ("passed", "failed", "skipped")}
    print("итог стенда: задач %d, строк %d — passed %d, failed %d, skipped %d"
          % (len({(r["record"], r["task"]) for r in rows}), len(rows),
             counts["passed"], counts["failed"], counts["skipped"]))
    spent = partial_rows(rows)
    if spent:
        # Упор в бюджет называется СЛОВАМИ и числом строк: файл
        # результатов уходит частичным, и молчать об этом нельзя.
        print("%s (%g с): живых строк без прогона %d — файл результатов "
              "частичный" % (BUDGET_WORDS, args.budget_s, len(spent)))
    if args.write_results:
        # Шапка `solver` файла результатов — форма §5.5 проекта: адрес,
        # отпечаток пакета и (просьба Ж-4) отпечаток артефакта решателя.
        # Отпечаток пакета здесь `null` ОСОЗНАННО и назван словами полем
        # `note` шапки (отступление от §5.5, оно же в приложении схемы и
        # в руководстве 13.8): путь 1 §5.6 собирает СВОЙ пакет на каждую
        # задачу, и одного отпечатка на весь файл не существует — он
        # живёт полем `package_fingerprint` своей строки, а поле шапки
        # лгать не должно.
        # Паспорт окружения (РВ-138, исправление 3): файл сам говорит,
        # снят ли он в окружении пусковика; снятый вне его дверь
        # привязок не принимает (`launcher_env.stale_reason`).
        from vmp import launcher_env
        doc = results_document(
            rows, cat, at=args.at, commit=args.commit,
            **({"component_reference": component_ref} if component_ref is not None else {}),
            solver=solver_header(args.solver,
                                 artifact_sha256=args.solver_identity),
            environment=launcher_env.passport())
        Path(args.write_results).write_bytes(
            (json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True)
             + "\n").encode("utf-8"))
        print("записаны результаты стенда %s" % args.write_results)
    bad = unexpected_failures(rows)
    if bad:
        print("красные строки вне KNOWN_FAILED: %d" % len(bad))
        return 1
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    sys.exit(main())
