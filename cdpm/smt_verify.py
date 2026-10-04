# -*- coding: utf-8 -*-
"""СМТ-1: машинная проверка непротиворечивости свода через SMT (Z3).

Решение владельца 2026-08-21. Назначение — не решать ростеринг, а
ЛОВИТЬ КОНФЛИКТЫ ПРАВИЛ до аудитора и до прогона: два жёстких правила,
которые нельзя выполнить одновременно, дают unsat, и Z3 называет
минимальное конфликтующее ядро (unsat core) — пары/группы правил.

Дисциплина «модель предлагает — код проверяет»:

- языковая модель ПЕРЕВОДИТ жёсткие правила свода в утверждения
  SMT-LIB над единым словарём функций (перевод — предложение);
- КОД проверяет перевод парсером Z3 (кривой SMT — повторная попытка,
  затем честный отказ) и машинно решает: sat = противоречий не
  найдено, unsat = конфликт с именованным ядром;
- вердикт «непротиворечиво» означает ровно то, что ПЕРЕВЕДЁННЫЕ
  утверждения совместны; качество перевода — на модели, поэтому
  каждый перевод сохраняется в отчёте и проверяем глазами.

Мягкие правила (SOFT) в проверку не входят: штрафуемые предпочтения
не бывают противоречием. Строки OUT — вне контура.

Проверка — ОБЯЗАТЕЛЬНЫЙ рубеж выпуска пакета постановки (предложение
CDPM, слово владельца 11.09.2026 «давай реализуем оба предложения»):
кнопка панели «Проверить непротиворечивость» и гейт выпуска пакета
(`cdpm/export_task_package.py`) читают и пишут ОДИН отчёт кнопки
`<прогон>/extract/smt-report.json`. Отчёт привязан к своду отпечатком
канонической модели (`model_sha256`): вердикт о другом своде не
считается вердиктом, и гейт выпуска перепроверяет свод сам.

Предмет проверки (A01-F1, 12.09.2026): то, что переводчик читает из
жёстких правил, — идентификаторы, виды и формулировки (`subject_of`,
отпечаток `subject_fingerprint`). Сборка пакета может изменить предмет
(правило стало жёстким по каталогу, снято, переформулировано) — тогда
отчёт кнопки о ВЫПУСКАЕМОМ наборе правил не судит, и рубеж выпуска
проверяет выпускаемый свод отдельно, сохраняя второй, явно названный
отчёт пакета `<прогон>/extract/smt-report-package.json`; отчёт кнопки
при этом не переписывается. Каждый отчёт несёт отпечаток своего
предмета (`subject_sha256`).
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
from datetime import datetime
from pathlib import Path

from cdpm import llm as llm_mod

from cdpm import model as m

#: Отчёт проверки в каталоге прогона — один носитель у кнопки панели и
#: у гейта выпуска пакета (путь относительно каталога прогона).
REPORT_REL = "extract/smt-report.json"

#: Отчёт о ВЫПУСКАЕМОМ своде (A01-F1, 12.09.2026): второй, явно названный
#: отчёт рядом с отчётом кнопки — его пишет рубеж выпуска, когда сборка
#: пакета изменила предмет проверки (см. `subject_of`); отчёт кнопки
#: остаётся один и о каноническом своде.
PACKAGE_REPORT_REL = "extract/smt-report-package.json"

#: Какой отчёт: кнопки — о каноническом своде прогона; пакета — о
#: выпускаемом своде (после преобразований сборки).
CANONICAL = "canonical"
PACKAGE = "package"

#: Роль канала-переводчика: одна у кнопки панели и у гейта выпуска.
TRANSLATOR_ROLE = "smt_translator"

#: Версия схемы отчёта (контракт П0 §1.1, A01, 12.09.2026): отчёт несёт
#: статус КАЖДОГО жёсткого правила (translated / untranslatable /
#: missing), и вердикт «sat» возможен только при полном переводе.
#: Отчёт без этого поля — старая форма: о полноте перевода он не
#: говорит, свежим не считается и пересчитывается.
REPORT_SCHEMA = "smt-report/2"

#: Статусы правила в отчёте.
TRANSLATED = "translated"
UNTRANSLATABLE = "untranslatable"
MISSING = "missing"

_PROMPT_SMT = """Ты — переводчик формализованных правил планирования
экипажей в SMT-LIB 2 для проверки НЕПРОТИВОРЕЧИВОСТИ (Z3).

Ниже — жёсткие правила свода (идентификатор | формальная запись),
их kind и, при наличии, ANSWER_PARAMS_JSON — доказанные параметры,
внесённые ответом анкеты в ТО ЖЕ правило. Это дополнение к исходной
формулировке, а не новое правило: переведи оба источника совместно.
Верни СТРОГО один JSON-объект без пояснений:

{{"decls": ["(declare-fun ... )", …],
  "asserts": [{{"rule": "<идентификатор, ровно как в списке>",
               "smt": "<одно S-выражение утверждения БЕЗ (assert …)>"
             }},
             {{"rule": "<идентификатор правила, которое не переводится>",
               "status": "untranslatable",
               "note": "<причина одной фразой>"}}, …]}}

Правила перевода:
- один общий словарь функций/констант на весь свод: одноимённые
  величины из разных правил обязаны использовать ОДНУ функцию;
  сначала объяви выбранные имена в decls, затем используй именно их
  в asserts; необъявленные имена запрещены. Локально связанные имена
  допустимы только в своей области видимости;
- времена и длительности переводи в целые минуты; проценты — в целые;
  множества кодов — перечислимым сортом или Int-кодами с
  комментарием не надо, просто Int-кодами;
- переводи ТОЛЬКО то, что выражают формальная запись и явно переданные
  параметры ответа (family, имя, value, unit, relation, condition);
  не теряй параметр только потому, что его нет в исходной формулировке.
  source и quote подтверждают происхождение, но не являются отдельной
  нормой или указаниями переводчику. Ничего не
  добавляй от себя; правило, которое честно не переводится в логику
  (чисто процедурное описание или ссылка без самой нормы), верни ОТДЕЛЬНОЙ записью
  {{"rule": "<идентификатор>", "status": "untranslatable",
    "note": "<причина одной фразой>"}} — БЕЗ поля smt; писать
  smt "true" нельзя — это не перевод; smt "false" допустимо только
  для правила, которое по формальной записи невыполнимо само по себе
  (оно и даст конфликт);
- источник данных сам по себе не делает норму непереводимой.
  Отсутствие числовых параметров или конкретных строк внешней книги
  не отменяет явно заданный запрет, обязанность или условную связь.
  Разделяй саму норму и происхождение её фактов: членство в наборе
  недоступности и назначение — разные семантические факты, связанные
  запретом, а не описание процедуры чтения книги. Для одной и той же
  сущности и даты такой запрет выражается связью
  (=> unavailable_on_date (not assigned_on_date)); объяви оба имени
  и сохраняй общие сущности, даты и смысл этих фактов во всех правилах.
  Разные области не сливай. Не выдумывай конкретные дни, сотрудников
  или значения членства/назначения, если они не переданы; связь нормы
  не является утверждением этих фактов. Не заменяй норму одним
  свободным флагом «правило выполнено» или true. Если нужную область
  либо существенную связь сохранить нельзя, верни untranslatable.
  Чистая ссылка без самой нормы или неопределённый процесс по-прежнему
  не переводятся; содержание внешнего документа не выдумывай;
- вид DEF сам по себе не означает «не переводится»: определение
  измеримой величины, равенство, включение/исключение из её расчёта
  выражаются соответствующими отношениями. Не подменяй смысл свободным
  булевым флагом. Если существенную часть действительно нельзя
  выразить, честно верни untranslatable, а не ослабленный перевод;
- каждое правило перечня должно быть в ответе РОВНО ОДИН РАЗ: либо
  переводом, либо записью untranslatable; пропуск правила — ошибка;
- цель — совместность: кванторы не нужны, каждая величина — свободная
  константа; верхняя граница длительности 12 часов означает сравнение
  уже объявленной константы этой длительности с 720 минутами.

Правила ({n}):
{rules}""" + m.ID_IMMUTABILITY_RULE


class SmtVerifyError(RuntimeError):
    """Проверка не состоялась (перевод/парсер/Z3); причина названа."""


def z3_problem() -> str | None:
    """Почему SMT-решатель недоступен на площадке (None — доступен):
    пакета нет либо он есть, но не загружается (например, заблокирована
    библиотека libz3). Проверяется настоящим импортом, а не только
    поиском пакета (ревью 06.09).

    Один помощник у кнопки панели (отказ ДО старта фоновой нити) и у
    гейта выпуска пакета (отказ ДО сборки): «проверить нечем» обе
    стороны называют одними словами."""
    import importlib
    import importlib.util as _ilu
    if _ilu.find_spec("z3") is None:
        return ("SMT-решатель (пакет z3-solver) не установлен на площадке — "
                "установите его в окружение панели: python -m pip install "
                "\"z3-solver>=4.13\" и повторите")
    try:
        importlib.import_module("z3")
    except Exception as e:                                  # noqa: BLE001
        return ("SMT-решатель (пакет z3-solver) установлен, но не "
                f"загружается: {str(e)[:200]} — переустановите пакет "
                "(python -m pip install --force-reinstall "
                "\"z3-solver>=4.13\") и повторите")
    return None


def model_fingerprint(model_bytes: bytes) -> str:
    """Отпечаток свода, о котором вынесен вердикт, — sha256 БАЙТОВ
    канонической модели прогона (`extract/model.json`). Тот же байт в
    байт файл читает кнопка панели и гейт выпуска, поэтому отпечаток
    у обеих сторон один."""
    return hashlib.sha256(model_bytes).hexdigest()


def _answer_params(rule: dict) -> dict | None:
    """Only answer-carried norms supplement the original statement.

    Keep their entire cells, including provenance and scope, in the subject:
    changing the answer must invalidate a report even if statement is fixed.
    This is an identity projection; verify() judges provenance before use.
    """
    from cdpm import provenance
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or not isinstance(fit.get("params"), dict):
        return None
    params = {str(name): copy.deepcopy(cell) for name, cell in fit["params"].items()
              if provenance.is_answer_cell(cell)}
    return {"family": fit.get("family"), "params": params} if params else None


def subject_of(doc: dict) -> list[dict]:
    """ПРЕДМЕТ SMT-проверки свода (A01-F1, 12.09.2026): ровно то, что
    переводчик читает из жёстких правил, — идентификатор, вид (HARD /
    DATA / DEF — членство в наборе жёстких) и формальная запись
    `statement` в том виде, в каком она идёт в задание (`verify`:
    строка «id | statement»). Канонический порядок — по идентификатору.
    Доказанный параметр мира (nature=world_param закреплённого каталога)
    остаётся в своде, но не является ограничением решения. Его посадка
    поэтому влияет на членство в предмете; сам носитель не переписывается.
    Параметры из анкеты дополняют исходную формулировку и входят в
    предмет вместе с точным происхождением. Остальные поля не идут."""
    rows = []
    for rule in _hard_rules(doc):
        row = {"id": str(rule.get("id")), "kind": str(rule.get("kind")),
               "statement": str(rule.get("statement", "")).strip()}
        answer = _answer_params(rule)
        if answer is not None:
            row["answer_params"] = answer
        rows.append(row)
    return sorted(rows, key=lambda x: x["id"])


def subject_fingerprint(doc: dict) -> str:
    """Отпечаток предмета проверки — sha256 канонического JSON
    `subject_of(doc)`. Равные отпечатки — один и тот же набор жёстких
    правил с теми же формулировками: отчёт об одном годится для другого."""
    body = json.dumps(subject_of(doc), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def subject_changes(before: dict, after: dict) -> list[str]:
    """Чем предмет проверки выпускаемого свода `after` отличается от
    канонического `before` — словами по правилам (для слов отказов и
    отчёта пакета): стало жёстким при сборке, перестало быть жёстким,
    вид изменился, формулировка изменилась, появилось, исчезло."""
    def kinds(doc: dict) -> dict[str, str]:
        return {str(r.get("id")): str(r.get("kind"))
                for r in doc.get("rules", []) if isinstance(r, dict)}
    kb, ka = kinds(before), kinds(after)
    sb = {s["id"]: s for s in subject_of(before)}
    sa = {s["id"]: s for s in subject_of(after)}
    out: list[str] = []
    for rid in sorted(set(sb) | set(sa)):
        b, a = sb.get(rid), sa.get(rid)
        if b is None:
            out.append(f"{rid} — стало жёстким при сборке ({kb[rid]}→"
                       f"{a['kind']})" if rid in kb
                       else f"{rid} — появилось при сборке ({a['kind']})")
        elif a is None:
            out.append(f"{rid} — перестало быть жёстким при сборке "
                       f"({b['kind']}→{ka[rid]})" if rid in ka
                       else f"{rid} — исчезло при сборке")
        else:
            if a["kind"] != b["kind"]:
                out.append(f"{rid} — вид изменился при сборке "
                           f"({b['kind']}→{a['kind']})")
            if a["statement"] != b["statement"]:
                out.append(f"{rid} — формулировка изменилась при сборке")
            if a.get("answer_params") != b.get("answer_params"):
                out.append(f"{rid} — параметры анкеты изменились при сборке")
    return out


#: Слова о жёстком правиле без пригодного идентификатора — один
#: носитель у проверки (`verify`) и у выпуска пакета: переводить и
#: называть такое правило нечем, а выдумывать ему имя запрещено
#: (A01-F2); за словами идут ПОЗИЦИИ среди жёстких правил.
NAMELESS_WORDS = ("жёсткое правило свода без идентификатора — переводить "
                  "и называть нечего; позиции среди жёстких правил: ")


def nameless_hard_positions(doc: dict) -> list[int]:
    """Позиции жёстких правил БЕЗ пригодного идентификатора (не строка
    либо пусто). Пусто — все жёсткие правила названы.

    Свод с таким правилом незаконен (схема краснит «правило без id»), и
    читатели — проверка непротиворечивости и рубеж выпуска — отвечают
    СЛОВАМИ с позицией, а не разбором по ключу: `KeyError: 'id'` из
    середины сборки — не отказ (находка неавторского ревью 12.09.2026:
    правило без ключа `id` роняло сборку пакета в `vmp_form`)."""
    return [i for i, r in enumerate(_hard_rules(doc))
            if not isinstance(r.get("id"), str) or not r["id"].strip()]


def report_path(run_dir, which: str = CANONICAL) -> Path:
    """Путь отчёта: кнопки (`REPORT_REL`, умолчание) либо пакета
    (`PACKAGE_REPORT_REL`, `which == PACKAGE`)."""
    return Path(run_dir) / (PACKAGE_REPORT_REL if which == PACKAGE
                            else REPORT_REL)


def _read_json_report(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        # A cached verdict cannot choose the last of contradictory fields.
        # Unusable evidence follows the existing recheck path; keep its bytes.
        got = m.loads_strict(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def read_report(run_dir, which: str | None = None) -> dict | None:
    """Отчёт прогона (None — отчёта нет либо он нечитаем: вердикта
    нет, а не «вердикт пуст»).

    `which` — `CANONICAL` (отчёт кнопки) или `PACKAGE` (отчёт пакета).
    Без `which` — отчёт О ВЫПУСКЕ прогона: отчёт пакета, если он есть и
    о текущем каноническом своде (`model_sha256` равен отпечатку
    `extract/model.json`) — он о фактически выпускаемом наборе жёстких
    правил; иначе отчёт кнопки. Рубеж выпуска и повтор читают отчёты
    явно, по `which`."""
    if which is None:
        pkg = _read_json_report(report_path(run_dir, PACKAGE))
        if pkg is not None:
            model_path = Path(run_dir) / "extract" / "model.json"
            try:
                sha = model_fingerprint(model_path.read_bytes()) \
                    if model_path.is_file() else None
            except OSError:
                sha = None
            if sha and is_fresh(pkg, sha):
                return pkg
        return _read_json_report(report_path(run_dir, CANONICAL))
    return _read_json_report(report_path(run_dir, which))


def write_report(run_dir, report: dict, which: str = CANONICAL) -> None:
    """Отчёт на диск в том же виде, в каком его пишет кнопка панели;
    `which == PACKAGE` — отчёт пакета отдельным файлом (отчёт кнопки не
    трогается)."""
    path = report_path(run_dir, which)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def is_fresh(report: dict | None, model_sha256: str,
             subject_sha256: str | None = None) -> bool:
    """Отчёт СВЕЖ, когда он о том же своде И в действующей форме:
    отпечаток модели в отчёте равен отпечатку канонической модели
    прогона, а версия схемы — `REPORT_SCHEMA`. Отчёт без отпечатка
    (сделан до 11.09.2026) свежим не считается — неизвестно, о каком
    своде его вердикт; отчёт без версии схемы (до 12.09.2026) — тоже:
    о полноте перевода он не говорит (A01).

    `subject_sha256` (A01-F1) — отпечаток предмета проверки, о котором
    отчёт обязан быть. Рубеж выпуска задаёт его ВСЕГДА, обоим отчётам:
    отчёт о другом наборе жёстких правил (или о тех же именах, но иных
    формулировках) вердиктом о выпуске не является, и отчёт, не
    назвавший своего предмета вовсе (форма до 12.09.2026), — тоже: он
    пересчитывается. Без `subject_sha256` (кнопка панели: её дело —
    канонический свод, доказанный отпечатком модели) предмет не
    судится."""
    if not isinstance(report, dict):
        return False
    if not (bool(report.get("model_sha256"))
            and report["model_sha256"] == model_sha256
            and report.get("schema_version") == REPORT_SCHEMA):
        return False
    if subject_sha256 is not None \
            and report.get("subject_sha256") != subject_sha256:
        return False
    return True


def _proven_world_parameter(rule: dict) -> bool:
    """Exclude only a complete, pinned world carrier, never a family-name hint.

    Descriptive HARD is required by the catalogue for world parameters; it
    does not turn a data coordinate system into a constraint on a solution.
    Unavailable catalogues and damaged/legacy evidence retain the HARD
    obligation. All readers use this same selector and subject fingerprint.
    """
    fit = rule.get("template_fit")
    if (rule.get("kind") != "HARD"
            or not isinstance(rule.get("id"), str) or not rule["id"].strip()
            or not isinstance(fit, dict)
            or fit.get("schema_version") != "landing/2"
            or fit.get("category") not in ("A", "B")
            or not isinstance(fit.get("family"), str) or not fit["family"]
            or not isinstance(fit.get("registry_rev"), str) or not fit["registry_rev"]):
        return False
    cells = fit.get("params")
    if (not isinstance(cells, dict) or not cells
            or any(not isinstance(cell, dict) or "pending_revision" in cell
                   for cell in cells.values())):
        return False
    from cdpm import catalog_bridge as cb
    from template_catalog import family_contract, landing, semantics
    try:
        catalog = cb.catalog()
        pin = fit.get("family_contract")
        reference = family_contract.read(pin, catalog, family=fit["family"],
                                         registry_rev=fit["registry_rev"])
        if (reference["issues"] or reference["verdict"] != semantics.COMPATIBLE
                or not reference["old_members"]
                or not set(reference["old_members"]).issubset(cells)):
            return False
        origin = semantics.resolve_release(catalog,
            fingerprint=pin["catalog_fingerprint"], version=pin["catalog_version"])
        record = origin.family(pin["family_id"])
        return (record.get("nature") == "world_param"
                and record.get("kind") == rule["kind"]
                and not landing.problems(rule, catalog))
    except cb.CATALOG_ERRORS:
        return False


def _hard_rules(doc: dict) -> list[dict]:
    """Жёсткие правила свода: `kind` из (HARD, DATA, DEF).

    Записи-не-объекты (и `rules` не списком) пропускаются: такой свод
    незаконен и краснится схемой (`model.validate_schema`), а предмет
    проверки и гейт производителя обязаны и на чужом пакете отвечать
    словами, а не падать разбором."""
    rows = doc.get("rules")
    return [r for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict)
            and r.get("kind") in ("HARD", "DATA", "DEF")
            and not _proven_world_parameter(r)]


def _translator_of(llm) -> dict:
    """Кто переводил: роль и ДЕЙСТВУЮЩИЕ провайдер с моделью по
    отпечатку канала. У канала без отпечатка (подменные функции
    тестов) — роль и два пустых поля: выдумывать имя модели нельзя."""
    ident: dict = {}
    raw = llm_mod.channel_identity(llm)
    if raw:
        try:
            got = json.loads(raw)
            ident = got if isinstance(got, dict) else {}
        except ValueError:
            ident = {}
    eff = ident.get("effective")
    eff = eff if isinstance(eff, dict) else {}
    return {"role": TRANSLATOR_ROLE,
            "provider": eff.get("provider") or ident.get("provider"),
            "model": eff.get("model") or ident.get("model")}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_smt_script(decls: list[str],
                     asserts: list[dict]) -> str:
    """SMT-LIB скрипт с именованными утверждениями — имена дают
    unsat core в терминах идентификаторов правил свода.

    Имя утверждения — идентификатор правила КАК ЕСТЬ в закавыченном
    символе SMT-LIB (`|R-14|`): дефисы, точки и косые черты решатель
    принимает, и ядро конфликта называет правила теми же именами, что
    свод и анкета (11.09.2026: прежняя замена всех не-буквенных знаков
    подчёркиванием давала ядро «R_14», которого в своде нет). Внутрь
    закавыченного символа нельзя только вертикальную черту, обратную
    косую и перевод строки — они заменяются."""
    out = ["(set-option :produce-unsat-cores true)"]
    out += list(decls)
    for i, a in enumerate(asserts):
        name = re.sub(r"[|\\\s]", "_", str(a.get("rule") or f"r{i}"))
        smt = str(a.get("smt") or "").strip()
        if not smt or smt.lower() == "true":
            # A01: утверждение без S-выражения — не перевод; прежняя
            # подстановка «true» молча делала правило «переведённым».
            # «false» — законный перевод невыполнимого правила: даёт
            # unsat с ядром из этого правила (REPLY-AUDITOR-01/02).
            raise SmtVerifyError(
                f"утверждение {name} без S-выражения перевода")
        out.append(f"(assert (! {smt} :named |{name}|))")
    out.append("(check-sat)")
    return "\n".join(out)


def _run_z3(script: str) -> tuple[str, list[str]]:
    """(вердикт sat|unsat|unknown, ядро конфликта). Кривой скрипт —
    исключение с причиной парсера (вызывающий даёт модели повтор)."""
    import z3
    solver = z3.Solver()
    # Парсеру скрипт подаётся БЕЗ (set-option …): с опцией
    # именованные утверждения приходят обёрнутыми в Implies(имя, …)
    # и «выключаются» решателем (ложный sat на заведомом конфликте —
    # поймано смоуком). Без опции приходят голые выражения — трекинг
    # свой, имена — из :named в порядке утверждений.
    text = script.replace("(set-option :produce-unsat-cores true)\n",
                          "")
    try:
        constraints = z3.parse_smt2_string(text)
    except z3.Z3Exception as e:
        detail = str(e)
        # Translate the real parser diagnostic, not the SMT grammar. A
        # hand-written identifier scan would misjudge let/define-fun scopes
        # and quoted symbols. Calls with a wrong signature retain the raw
        # diagnostic: their function may already have been declared.
        unknown = list(dict.fromkeys(re.findall(
            r'unknown constant ([^"\\\r\n()]+)"', detail)))
        if unknown:
            names = ", ".join(unknown)
            raise SmtVerifyError(
                f"SMT не разобрался: необъявленная константа {names[:200]}. "
                "Используй точные имена, объявленные в decls, либо локально "
                "связанные в своей области видимости; проверь перевод. "
                f"Диагностика Z3: {detail[:300]}") from None
        raise SmtVerifyError(f"SMT не разобрался: {detail[:300]}") from None
    names = re.findall(r":named \|([^|]+)\|", script)
    prefix = "__cdpm_track_"
    for i, c in enumerate(constraints):
        label = names[i] if i < len(names) else f"a{i}"
        solver.assert_and_track(c, z3.Bool(prefix + label))
    got = solver.check()
    if got == z3.unsat:
        core = [str(b)[len(prefix):] for b in solver.unsat_core()]
        return "unsat", core
    return ("sat" if got == z3.sat else "unknown"), []


def verify(doc: dict, llm, model_sha256: str | None = None) -> dict:
    """Полная проверка свода: перевод моделью (с одним повтором на
    кривом SMT) → Z3 → отчёт. Отчёт несёт каждый перевод — проверяем
    глазами.

    `model_sha256` — отпечаток канонической модели, о которой вынесен
    вердикт (`model_fingerprint` по байтам `extract/model.json`): по
    нему гейт выпуска пакета решает, свеж ли отчёт. Без отпечатка
    отчёт остаётся диагностикой и свежим не считается."""
    hard = _hard_rules(doc)
    # Жёсткое правило без пригодного идентификатора переводить нельзя:
    # ни назвать его в задании, ни спросить о нём ответ, ни назвать в
    # отчёте — а выдумывать ему имя запрещено (A01-F2). Такой свод
    # незаконен (схема краснит «правило без id»), и проверка отвечает
    # СЛОВАМИ с позицией, а не разбором по ключу: читатели этой ошибки
    # (рубеж выпуска, кнопка панели) превращают её в честный отказ.
    nameless = nameless_hard_positions(doc)
    if nameless:
        raise SmtVerifyError(NAMELESS_WORDS
                             + ", ".join(str(i) for i in nameless[:12]))
    from cdpm import provenance
    answer_problems = [problem for rule in hard
                       for problem in provenance.rule_problems(doc, rule)]
    if answer_problems:
        raise SmtVerifyError("параметры анкеты не обоснованы: "
                             + "; ".join(answer_problems))
    ids = [r["id"] for r in hard]
    stamp = {"schema_version": REPORT_SCHEMA, "model_sha256": model_sha256,
             # A01-F1: предмет проверки — отпечаток жёстких правил ТОГО
             # свода, что переводился (идентификаторы, виды,
             # формулировки); по нему рубеж выпуска узнаёт, о выпускаемом
             # ли наборе этот отчёт.
             "subject_sha256": subject_fingerprint(doc),
             "checked_at": _now(), "translator": _translator_of(llm)}
    if not hard:
        # Проверять нечего: жёстких правил нет. Вердикт решателя тут не
        # «совместно», а «вопроса не было» — пакет такой свод называет
        # непротиворечивостью вида «none» (счётчик правил нулевой).
        return {"status": "нет жёстких правил — проверять нечего",
                "verdict": "sat", "core": [], "n_rules": 0,
                "n_translated": 0, "n_untranslatable": 0,
                "untranslated": [], "dropped": [], "malformed": [],
                "translations": [], **stamp}
    listing_rows = []
    for row in subject_of(doc):
        listing_rows.append(f"{row['id']} | {row['statement']}\nkind={row['kind']}")
        if "answer_params" in row:
            listing_rows.append("ANSWER_PARAMS_JSON: " + json.dumps(
                row["answer_params"], ensure_ascii=False, sort_keys=True))
    listing = "\n".join(listing_rows)
    prompt = _PROMPT_SMT.format(n=len(hard), rules=listing)
    # Бюджет повторов — ОДИН на свод (как у кнопки до 12.09.2026): и на
    # нарушение формы, и на кривой SMT; парсер упал на первой попытке,
    # форма нарушена на второй — повтора за форму уже нет, отчёт честно
    # неполон (fail-closed).
    retry_note = ""
    rows: dict[str, dict] = {}
    missing: dict[str, str] = {}
    dropped: list[str] = []
    decls: list[str] = []
    malformed: list[dict] = []
    parsed_subset = False
    for attempt in (1, 2):
        ask = prompt if attempt == 1 else prompt + "\n\n" + retry_note
        raw = llm(ask)  # Transport failures do not receive a parsing retry.
        try:
            got = llm_mod.parse_json_reply(raw)
        except llm_mod.LLMReplyError as e:
            if attempt == 2:
                raise SmtVerifyError(
                    "JSON переводчика SMT не разобрался: " + str(e)) from None
            retry_note = ("Предыдущий ответ не разобрался как JSON: " + str(e)
                          + "\nВерни СТРОГО один JSON-объект по исходной схеме.")
            continue
        # A01-F2 (12.09.2026): форма исходных массивов судится ДО
        # фильтрации — элемент вне формы (не объект в `asserts`, не
        # строка в `decls`, не список вместо массива) есть нарушение
        # формы, а не то, что можно молча отбросить: он называется
        # позицией и видом («asserts[3]: не объект (null)»), тратит тот
        # же единственный повтор и, сохранившись, остаётся в отчёте
        # причиной отсутствия вердикта. Идентификатор ему не
        # выдумывается.
        decls, asserts, malformed, positions = split_reply_form(got)
        rows, problems, missing, dropped = classify_translation(
            asserts, ids, positions)
        problems = [malformed_words(e) for e in malformed] + problems
        if missing or dropped or problems:
            # A01: ответ обязан назвать каждое жёсткое правило — либо
            # переводом, либо явным «не переводится» с причиной — и не
            # называть чужих. Один повтор с перечнем; сохранившаяся после
            # повтора ошибка (недостающие, повторные или лишние
            # идентификаторы, записи вне формы) — отчёт без вердикта, не
            # отбрасывание (REPLY-AUDITOR-01/A01: точный набор, без
            # молчаливого отбрасывания).
            if attempt == 2:
                break
            named = {p.split(":", 1)[0] for p in problems}
            retry_note = (
                "Предыдущий ответ неполон или нарушает форму: "
                + "; ".join(problems + [
                    f"нет записи для {r}" for r in missing
                    if r not in named and "повторная" not in missing[r]])
                + ". Верни СТРОГО один JSON-объект по схеме с записью "
                  "для КАЖДОГО правила перечня и только для них: перевод "
                  "(smt) либо {\"status\": \"untranslatable\", "
                  "\"note\": причина}"
                + ("; каждая запись asserts — объект, каждая запись "
                   "decls — строка, ничего иного в массивах"
                   if malformed else "")
                + ".")
            continue
        incomplete = any(rows[r]["status"] == UNTRANSLATABLE for r in ids)
        translated = [{"rule": r, "smt": rows[r]["smt"]}
                      for r in ids if rows[r]["status"] == TRANSLATED]
        if not translated:
            break                 # нечего проверять; честный полный отказ
        script = build_smt_script(decls, translated)
        try:
            verdict, core = _run_z3(script)
        except SmtVerifyError as e:
            if attempt == 2:
                raise
            retry_note = ("Предыдущий перевод не прошёл парсер Z3: "
                          + str(e)[:300]
                          + "\nВерни СТРОГО один JSON-объект по схеме, "
                            "исправив SMT.")
            continue
        if incomplete:
            # A truthful untranslatable neighbour must not hide invalid SMT
            # in the translated subset or bypass the existing repair budget.
            # Even SAT/UNSAT of this subset is NOT a whole-model verdict.
            parsed_subset = True
            break
        status = ("непротиворечиво (все жёсткие правила переведены, "
                  "утверждения совместны)" if verdict == "sat"
                  else "КОНФЛИКТ ПРАВИЛ" if verdict == "unsat"
                  else "решатель не пришёл к вердикту")
        return {"status": status, "verdict": verdict, "core": core,
                "n_rules": len(ids), "n_translated": len(ids),
                "n_untranslatable": 0, "untranslated": [],
                "dropped": dropped, "malformed": malformed,
                "translations": [{"rule": r, **rows[r]} for r in ids],
                "script": script, **stamp}
    # Перевод неполон (после повтора) либо часть правил честно не
    # переводится: вердикта нет, отчёт называет каждое правило с
    # причиной. Для корректной смешанной формы переведённая часть уже
    # проверена парсером, но это не вердикт обо всём своде.
    translations = []
    for r in ids:
        row = dict(rows.get(r) or {"status": MISSING,
                                   "note": missing.get(r, "нет записи")})
        if row["status"] == TRANSLATED:
            row["note"] = (
                "S-выражение проверено парсером в переведённой части; "
                "перевод свода неполон — общего вердикта нет"
                if parsed_subset else
                "S-выражение дано; парсером не проверено — перевод свода неполон")
        translations.append({"rule": r, **row})
    untranslated = [r for r in ids
                    if rows.get(r, {}).get("status") != TRANSLATED]
    n_un = sum(1 for r in ids
               if rows.get(r, {}).get("status") == UNTRANSLATABLE)
    # Инвариант формы: отчёт без вердикта обязан называть причину —
    # непереведённое правило, запись ответа не о правиле свода (чужой
    # идентификатор либо запись без идентификатора, названная позицией)
    # либо запись вне формы (n_rules = translated + untranslatable +
    # missing).
    # Явный `raise`, а не `assert`: под `python -O` утверждения не
    # исполняются, а замок формы обязан держать и там (12.09.2026).
    if not (untranslated or dropped or malformed):
        raise SmtVerifyError(
            "проверка непротиворечивости не состоялась: отчёт без вердикта "
            "не назвал причины — ни непереведённых правил, ни записей не о "
            "правилах свода, ни записей вне формы")
    words = "перевод неполон" if untranslated else "перевод неточен"
    if untranslated:
        words += ": не переведены " + ", ".join(untranslated) + (
            f" (в том числе честно не переводятся: {n_un})" if n_un else "")
    if dropped:
        words += ("; записи ответа переводчика не о правилах свода "
                  "(чужой идентификатор либо запись без идентификатора, "
                  "названная позицией): " + ", ".join(dropped))
    if malformed:
        words += ("; записи вне формы в ответе переводчика: "
                  + "; ".join(malformed_words(e) for e in malformed))
    return {"status": words + " — вердикта о непротиворечивости нет",
            "verdict": "none", "core": [], "n_rules": len(ids),
            "n_translated": len(ids) - len(untranslated),
            "n_untranslatable": n_un, "untranslated": untranslated,
            "dropped": dropped, "malformed": malformed,
            "translations": translations, "decls": decls, **stamp}


def report_shape_problems(report: dict) -> list[dict]:
    """Name malformed report containers before iterating or filtering them.

    Missing/null optional containers retain legacy behavior. A scalar, or a
    wrong element inside a present list, must not disappear as empty evidence.
    This is the stored report's form, not a new translator response schema.
    """
    problems = []
    for field, item_type in (("core", str), ("dropped", str),
                             ("translations", dict), ("malformed", dict)):
        raw = report.get(field)
        if raw is None:
            continue
        if not isinstance(raw, list):
            problems.append({"path": field,
                             "words": f"{field}: не список ({_kind_word(raw)})"})
            continue
        expected = "не строка" if item_type is str else "не объект"
        for index, item in enumerate(raw):
            if not isinstance(item, item_type):
                path = f"{field}[{index}]"
                problems.append({"path": path,
                                 "words": f"{path}: {expected} ({_kind_word(item)})"})
    return problems


def report_completeness(report: dict, doc: dict) -> dict:
    """Сверка отчёта с ВЫПУСКАЕМЫМ сводом (A01, REPLY-AUDITOR-01: гейт
    не доверяет счётчикам `n_rules`/`n_translated`, а сверяет точный
    набор жёстких правил свода со строками `translations[]`).
    Возвращает `{"n_rules", "n_translated", "missing", "foreign",
    "duplicate", "bad", "malformed", "malformed_words"}`: `missing` —
    правила свода без строки `translated` (нет строки, `untranslatable`,
    `missing`), `foreign` — записи, которым правило свода не
    соответствует (строка отчёта о чужом правиле; чужой идентификатор
    или запись без идентификатора — позицией — из `dropped`),
    `duplicate` — правила с двумя строками, `bad` — строки `translated`
    без непустого S-выражения либо с `true`, `malformed` — позиции
    записей ответа переводчика вне формы (A01-F2: «asserts[3]»,
    `malformed_words` — те же со словами «не объект (null)»); такой
    ответ не считается переводом, даже если все правила названы. Отчёт
    годится вердиктом только при пустых пяти перечнях."""
    ids = [str(r.get("id")) for r in _hard_rules(doc)]
    shape = report_shape_problems(report)
    known = set(ids)
    rows = report.get("translations")
    rows = rows if isinstance(rows, list) else []
    seen: dict[str, int] = {}
    # Записи ответа не о правилах свода, сохранившиеся после повтора,
    # `verify()` кладёт в `dropped` (в строки отчёта они не входят):
    # чужой идентификатор — своим именем, запись без идентификатора —
    # позицией «asserts[2]». Набор неточен, и рубеж обязан отказать с
    # их именами (контракт §12.1 п. 2).
    dropped = report.get("dropped")
    dropped = dropped if isinstance(dropped, list) else []
    foreign: list[str] = [x for x in dropped
                          if isinstance(x, str) and x not in known]
    bad: list[str] = []
    translated: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("rule"))
        if rid not in known:
            if rid not in foreign:
                foreign.append(rid)
            continue
        seen[rid] = seen.get(rid, 0) + 1
        if row.get("status") == TRANSLATED:
            smt = row.get("smt")
            smt = smt.strip() if isinstance(smt, str) else ""
            if smt and smt.lower() != "true":
                translated.add(rid)
            else:
                bad.append(rid)
    duplicate = [r for r in ids if seen.get(r, 0) > 1]
    missing = [r for r in ids
               if r not in translated or r in duplicate or r in bad]
    raw_mal = report.get("malformed")
    raw_mal = [e for e in raw_mal if isinstance(e, dict)] \
        if isinstance(raw_mal, list) else []
    return {"n_rules": len(ids), "n_translated": len(ids) - len(missing),
            "missing": missing, "foreign": foreign, "duplicate": duplicate,
            "bad": bad, "malformed": ([malformed_item(e) for e in raw_mal]
                                       + [p["path"] for p in shape]),
            "malformed_words": ([malformed_words(e) for e in raw_mal]
                                + [p["words"] for p in shape])}


def _kind_word(x) -> str:
    """Вид значения JSON словом для слов отказа: null, bool, number,
    string, list, object (bool — раньше number: в Python bool есть int)."""
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, (int, float)):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "list"
    if isinstance(x, dict):
        return "object"
    return type(x).__name__


def _brief(x) -> str:
    try:
        text = json.dumps(x, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(x)
    return text[:80]


def split_reply_form(got: dict) -> tuple[list[str], list[dict],
                                         list[dict], list[int]]:
    """Форма массивов ответа переводчика ДО фильтрации (A01-F2):
    `(decls, asserts, malformed, positions)` — пригодные объявления
    (строки), пригодные записи (объекты), перечень записей ВНЕ ФОРМЫ и
    ИСХОДНЫЕ позиции пригодных записей в массиве `asserts` ответа.
    Каждая запись вне формы названа позицией и видом: `{"field":
    "asserts" | "decls", "index": i | None, "kind": "null" | "number" |
    "string" | "list" | "bool" | "object", "text": <начало значения>}`;
    `index None` — сам массив не список. Записи вне формы не
    отбрасываются молча: они тратят единственный повтор и,
    сохранившись, оставляют отчёт без вердикта. Идентификатор правила
    такой записи не приписывается — его нет; `positions` даёт ту же
    честность записи-объекту, которая правила не назвала: разбор зовёт
    её исходной позицией, а не выдуманным именем."""
    malformed: list[dict] = []
    positions: list[int] = []

    def take(name: str, raw, ok) -> list:
        good: list = []
        if not isinstance(raw, list):
            malformed.append({"field": name, "index": None,
                              "kind": _kind_word(raw), "text": _brief(raw)})
            return good
        for i, x in enumerate(raw):
            if ok(x):
                good.append(x)
                if name == "asserts":
                    positions.append(i)
            else:
                malformed.append({"field": name, "index": i,
                                  "kind": _kind_word(x), "text": _brief(x)})
        return good
    decls = take("decls", got.get("decls", []), lambda d: isinstance(d, str))
    asserts = take("asserts", got.get("asserts", []),
                   lambda a: isinstance(a, dict))
    return decls, asserts, malformed, positions


def malformed_item(entry: dict) -> str:
    """Позиция записи вне формы как идентификатор перечня `items`
    отказа: «asserts[3]», «decls[1]», «asserts» (массив не список)."""
    field = str(entry.get("field") or "asserts")
    index = entry.get("index")
    return field if index is None else f"{field}[{index}]"


def malformed_words(entry: dict) -> str:
    """Слова о записи вне формы: «asserts[3]: не объект (null)»,
    «decls[1]: не строка (number)», «asserts: не список (object)»."""
    field = str(entry.get("field") or "asserts")
    kind = str(entry.get("kind") or "?")
    if entry.get("index") is None:
        what = "не список"
    else:
        what = "не строка" if field == "decls" else "не объект"
    return f"{malformed_item(entry)}: {what} ({kind})"


def classify_translation(asserts: list[dict], ids: list[str],
                         positions: list[int] | None = None) -> tuple:
    """Разбор ответа переводчика по контракту `smt-report/2` (A01,
    П0 §12.1): на каждое жёсткое правило РОВНО ОДНА запись — перевод с
    непустым S-выражением (не `true`; `false` — законный перевод
    невыполнимого правила) либо явный статус `untranslatable` с
    причиной; лишних записей быть не должно.
    Возвращает `(rows, problems, missing, dropped)`:
    `rows` — по правилу `{status, smt | note}`; `problems` — слова о
    нарушениях формы для записки повтора; `missing` — правила перечня
    без пригодной записи, каждое с причиной (`{rule: note}`): нет
    записи, нет S-выражения, повторная запись (повтор снимает ОБЕ
    записи — какая из них верна, неизвестно); `dropped` — записи
    ответа, не относящиеся ни к одному правилу перечня: чужой
    идентификатор (называется как есть) либо запись БЕЗ пригодного
    идентификатора (`rule` отсутствует, пуст или не строка) — такая
    зовётся ИСХОДНОЙ ПОЗИЦИЕЙ («asserts[2]»), потому что имени у неё
    нет и выдумывать его нельзя (A01-F2 вердикта аудитора). Записи
    `dropped` называются в записке повтора, а если сохранились после
    него — отчёт остаётся без вердикта и рубеж выпуска отказывает с их
    именами (без молчаливого отбрасывания).

    `positions` — исходные позиции переданных записей в массиве
    `asserts` ответа (из `split_reply_form`, до отсева записей вне
    формы): по ним и зовутся записи без идентификатора. Без
    `positions` позицией считается номер записи в переданном списке."""
    known = set(ids)
    rows: dict[str, dict] = {}
    problems: list[str] = []
    missing: dict[str, str] = {}
    dropped: list[str] = []
    seen: set[str] = set()
    for k, a in enumerate(asserts):
        rid = a.get("rule")
        if not isinstance(rid, str) or not rid.strip():
            # Запись не назвала правила: идентификатора у неё НЕТ. Ни
            # «None», ни имя соседнего правила ей не приписывается —
            # она зовётся своей позицией в ответе и видом значения. Как
            # и чужой идентификатор, остаётся в `dropped`: набор
            # неточен, вердикта нет, рубеж выпуска отказывает.
            pos = positions[k] if positions and k < len(positions) else k
            name = f"asserts[{pos}]"
            if name not in dropped:
                dropped.append(name)
                problems.append(f"{name}: запись без идентификатора "
                                f"правила (rule = {_brief(rid)})")
            continue
        if rid not in known:
            name = str(rid)
            if name not in dropped:
                dropped.append(name)
                problems.append(f"лишний идентификатор {name!r} — такого "
                                f"правила в перечне нет")
            continue
        if rid in seen:
            problems.append(f"повторная запись для {rid}")
            rows.pop(rid, None)
            missing[rid] = "повторная запись: какая из двух верна — неизвестно"
            continue
        seen.add(rid)
        status = str(a.get("status") or "").strip().lower()
        if status == UNTRANSLATABLE:
            note = str(a.get("note") or "").strip()
            rows[rid] = {"status": UNTRANSLATABLE,
                         "note": note or "причина не названа"}
            continue
        smt = a.get("smt")
        smt = smt.strip() if isinstance(smt, str) else ""
        if smt and smt.lower() != "true":
            # «false» — законный перевод невыполнимого правила (даст
            # unsat с ядром из него), «true» — не перевод.
            rows[rid] = {"status": TRANSLATED, "smt": smt}
        else:
            problems.append(f"{rid}: нет S-выражения перевода (smt пуст, "
                            f"отсутствует или равен true)")
            missing[rid] = "нет S-выражения перевода (smt пуст, " \
                           "отсутствует или равен true)"
    for r in ids:
        if r not in rows and r not in missing:
            missing[r] = "нет записи в ответе переводчика"
    return rows, problems, missing, dropped
