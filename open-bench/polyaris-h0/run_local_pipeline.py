# -*- coding: utf-8 -*-
"""Прогон ступени ИДАК-0 ШТАТНЫМ конвейером ЛОКАЛЬНО: панель CDPM в
процессе (тот же код и те же стадии, что на площадке), без раската.

Стадии (стадия = точка выхода с гейтом):
  create  — новый прогон панели: загрузка трёх файлов набора, заморозка
            (jpg — зрением облака), политика файлов «cloud»;
  extract — извлечение моделью (роль extractor);
  cycle   — сквозной цикл (аудит-цикл → арбитраж тупиков → …);
  package — пакет постановки (анкета пуста по замыслу: вопросов быть не
            должно — вопрос = дефект конвейера).
После каждого выхода: гейт эквивалентности (vmp.equivalence, строгий
режим) против эталона комплекта И поклаузная сверка с эталонной моделью
читателя канонической формы (cdpm.form_text) — где именно отклонилась
половина LLM. Отчёты — в каталог прогона; копии — в каталог сигналов
COSMOS (--signals-dir), по одному файлу на тег.

Ожидания набора (ключ `expect` манифеста) судятся ЧЕСТНО (поручение
владельца 10.09.2026): итог различает «проверено» (ключи, которые
раннер судил сам, поимённо), «не судится раннером» (ключи с причиной —
например числа сверки с ИДАК: они получаются НАЖАТИЯМИ в COSMOS, а не
прогоном CDPM) и «не выполнено». Строка «ожидания выполнены» ставится
только при пустом перечне расхождений, и несудимые ключи при ней
названы явно. Ключ --cosmos-press <json> даёт раннеру машинный отчёт
нажатий COSMOS — тогда `cosmos_numbers` судится: вердикты, сертификаты
и НОЛЬ нарушений (критерий «без ошибок» по слову владельца
09.09.2026), а число строк сверки — справочно: оно гуляет по чтению
модели, и расхождение ровно в строках при совпавших нарушениях —
предупреждение, а не провал.

ВЕРДИКТЫ СТАДИЙ объявляет НАБОР, а не раннер (ключ `stage_verdicts`
манифеста комплекта): вердикт стадии — свойство набора, и у Н0-B
извлечение «не эквивалентно до ответов» ожидаемо, а не ошибочно.
Раннер сверяет вердикты стадий CDPM со своим состоянием, а вердикты
стадий COSMOS (пейринг, ростер, сверка с ИДАК) — с машинным отчётом
нажатий; без отчёта это предупреждение, а не краснота. Расхождение
называет стадию, что вышло и что ожидалось. Ключа в манифесте нет —
прежнее поведение: вердикты стадий не судятся.

НЕПРОТИВОРЕЧИВОСТЬ ПОСТАНОВКИ (гейт выпуска пакета половины CDPM, слово
владельца 11.09.2026): выпуск пакета требует свежего вердикта SMT, и
раннер видит три его следа. ОТКАЗ выпуска несёт добавочный ключ `code`
тела ответа — раннер называет его словами в журнале и в ИТОГЕ
(smt-contradiction — правила противоречат друг другу, ядро R-…, R-… в
`detail`; smt-unavailable — проверка непротиворечивости невозможна,
гейт закрыт наглухо); отказ без кода идёт прежними словами. МАНИФЕСТ
выпущенного пакета несёт блок `consistency` (вердикт, отпечаток модели,
правил и переведённых, переводчик), и раннер печатает его строкой
отчёта о пакете; ожидание набора `consistency` («sat» либо «none»)
сверяется с ним и СУДИТСЯ — ожидание объявлено, а блока в манифесте
пакета нет, значит красная строка (fail-closed). ВОПРОС части 1
«противоречие» несёт поле `contradiction` — раннер показывает норму и
разошедшиеся значения строкой рядом с вопросом (только показ: вопросы
раннер не судит).

ПАСПОРТ ПРОГОНА (слово владельца 10.09.2026): все параметры прогона
называются и сверяются ДО стадии create — набор правил и набор данных
авиакомпании по отпечаткам, каталог правил, привязки исполнителей
COSMOS, раскладка моделей по ролям, решатель, все библиотеки, код и
панель CDPM. Расхождение — отказ словами «объявлено … / на деле …» до
первого вызова модели; после заморозки идёт вторая сверка «конвейер
заморозил ровно объявленное». Паспорт печатается в журнал целиком и
ложится файлом run-passport.json в --out и в папку прогона. Сборка и
сверка — продуктовый модуль vmp/run_passport.py.

ЗАПИСЬ О ПРОГОНЕ (проект «Запись о прогоне», шаг 1): прогон — отдельная
сущность с номером, началом и концом. До стадии create рядом с паспортом
ложится run-record.json: голова — объявление из паспорта (цель ключом
--purpose, площадка ключом --contour), тело — журнал этапов с исходом,
хронометражем, счётчиками и артефактами, хвост — вердикты, состояние и
общий хронометраж. Словарь полей — продуктовый модуль vmp/run_record.py
(схема vmp/run_record.schema.json), его же читают CDPM и COSMOS.

МОДЕЛИ COSMOS В ГОЛОВЕ (договор с CDPM 11.09.2026): голову чеканит
ХОЗЯИН записи — здесь раннер, — и до 11.09 в ней стояли только модели
CDPM: сертификатор и читатель ИДАК в голову не попадали вовсе. Перед
чеканкой раннер спрашивает среду COSMOS ручкой GET /api/run/environment
(адрес — ключ --cosmos-url либо переменная контура CREW_CHANNEL_URL, та
же, куда уходит пакет постановки) и кладёт его модели в models[] словом
system: "cosmos". Спросить не удалось — голова несёт модели CDPM, а её
поле models_note называет неполноту словами: роль не теряется молча.

ОБЪЯВЛЕНИЕ ПРОГОНА И ХРАНИТЕЛЬ ЗАПИСИ (тот же проект, шаг 2, разделы 10
и 11): номер записи чеканится ДО создания прогона на панели, и создание
несёт объявление — номер записи, кто объявил, цель, политика ответов,
план этапов, точка остановки и ожидания (манифест набора). Кто ведёт
ФАЙЛ записи в папке прогона, раннер спрашивает у панели ручкой
GET /api/runs/<прогон>/record: панель ведёт запись раннера (200 и тот же
номер) — раннер в папку прогона не пишет вовсе, только в свой --out, а
этапы и итоги шлёт ручкой POST /api/runs/<прогон>/record/append; панель
записи не ведёт (404 или чужой номер — старая панель) — переходное
правило «два летописца»: файл в папку прогона пишет раннер, как сегодня,
и ручка не зовётся. Отказ хранителя (404/409/422) — предупреждение
словами: запись в --out полна всегда, летопись прогон не роняет.
Ожидание набора record_stitched судит сшивку: после НАЖАТИЙ COSMOS у
хозяина записи обязаны стоять этапы COSMOS (без нажатий не судится).

ИТОГИ ЗАПИСИ (тот же проект, шаг 3, разделы 3.3 и 12.4): при закрытии
записи раннер кладёт в results.comparison ТРИ сравнения — с ОЖИДАНИЯМИ
набора (ровно тот суд, что прошёл на стадии package: судимые ключи,
несудимые с причиной, расхождения, предупреждения и вердикт), с
ПРЕДЫДУЩИМ прогоном того же корня и с ЗОЛОТЫМ прогоном (оба — из ручки
дерева панели GET /api/runs/<прогон>/tree; ручки ещё нет либо
сравнивать не с чем — сравнения пусты, и раннер их не выдумывает).
Признак золотого раннер НЕ ставит НИКОГДА (решение владельца 6.3 от
10.09.2026): прогон-образец называет только владелец словом после
приёмки на Prod, и ожидание набора golden_by_owner_only краснеет, если
признак у хозяина записи стоит без подписи (судится по нажатиям).

ЧИСТЫЙ ЦИКЛ — ПО ЗАКОНУ РЕЕСТРА (A07; первоисточник требования —
контракт П0 §1.5 «панель, одиночный цикл, сквозной цикл и раннер H0
читают одну функцию»; вердикты аудитора VERDICT-A07-ef0ac6f9 и
действующий VERDICT-A07-d9343ead): собственного перечня живых
состояний находок раннер НЕ держит. Живые находки даёт
`cdpm.exchange.live_findings`, блокирующие (БЛОКИРУЮЩЕЕ/КРИТИЧЕСКОЕ без closed_round; ключ, решённый
арбитром, гасит эхо спора) — `cdpm.exchange.registry_blockers`; те же
функции читают панель, одиночный и сквозной цикл и гейт, и раннер
обязан быть с ними СОГЛАСЕН — не строже и не мягче. «Чисто» =
состояние прогона «сквозной цикл: чисто по Б/К» И пустой перечень
блокирующих; живые НЕблокирующие (серьёзные, редакционные) идут в
журнал предупреждением, а не краснотой. Сам реестр берёт ОДИН
загрузчик продукта `cdpm.exchange.load_registry` (A07-F4): чужая версия
и негодная форма — отказ словами, унаследованная отбраковка замком кода
живая (A07-F3). ОБЯЗАН ЛИ ФАЙЛ РЕЕСТРА БЫТЬ, решает тот же закон одним
предикатом `cdpm.exchange.registry_expected(exdir, state)` (A07-R5,
ведомость CDPM-122; «или» из слова состояния и вердикта цикла в папке
обмена): исполненный цикл без файла — исчезнувший носитель и отказ
словами, а не «новый прогон» и не доказательство чистоты; ни слова, ни
вердикта — реестра законно нет, и читается пустой. Блок
`cycle_status` записи несёт живые находки, блокирующие и версию схемы
реестра, как её отдал загрузчик; версию раннер не «исправляет» и не
гадает, а отказ закона называет его словами и считает цикл не чистым.

КРАСНОТА ЦИКЛА ДОХОДИТ ДО КОНЦА (замечание С1 неавторского ревью
REVIEW-A07RUN-856f588c-COSMOS и квитанция аудитора
A07RUN-REVIEW-RECEIPT-20260912-GPT): не чистый цикл красит не только
вердикт гейта `cycle` (пометка NOT_CLEAN_CYCLE:), но и ЗАПИСЬ О
ПРОГОНЕ, и ОЖИДАНИЯ НАБОРА. Блок `cycle_status` несёт три ЦЕЛЫХ числа
— `live_n`, `blockers_n`, `registry_refused` (0/1), — и они уезжают в
счётчики этапа записи; ожидания набора на стадии package красит
`cycle_clean_problems` — у ЛЮБОГО набора, без объявления в манифесте:
чистота цикла — закон реестра, а не свойство набора.

СМЫСЛОВОЙ ИСХОД ЦИКЛА — ТРИ КОДА ОТКАЗА, ОДИН КОД ОСТАНОВКИ ЧЕЛОВЕКОМ
И ОДИН КОД ПРЕДУПРЕЖДЕНИЯ
(`cycle_outcome`, слова исходов — из словаря записи `vmp/run_record.py`,
своего перечня исходов раннер не заводит):

  * `stopped-in-panel` — ОСТАНОВЛЕН ЧЕЛОВЕКОМ (кнопка «Остановить»
    панели). Признак берётся предикатом закона
    `cdpm.exchange.stopped_by_human`, а не литералом состояния, и
    судится раньше смыслового суда: «человек нажал кнопку» — не
    «система остановилась сама и назвала причину»;
  * `registry-refusal` — закон отказался читать реестр: чистота НЕ
    подтверждена, и неизвестность не заменяется успехом;
  * `not-clean-cycle` — реестр прочтён, живые БЛОКИРУЮЩИЕ есть;
  * `cycle-incomplete` — блокирующих нет, но исход конвейер не
    заявил (лимит кругов, ожидание ответов, остановка): чистота не
    подтверждена — это не то же самое, что живой блокер, и звать её
    «не чистым циклом» было бы неправдой;
  * `live-non-blocking` — ПРЕДУПРЕЖДЕНИЕ: чисто по Б/К, но живые
    неблокирующие есть; их дорога — бэклог панели, а не краснота.

НАСТОЯЩИЙ СБОЙ СЛУЖБЫ СТАРШЕ СМЫСЛА: целочисленный ответ 5xx в блоке
стадии — «сбой», и смысловой суд до него не доходит; поломку службы
нельзя выдавать за штатный отказ по правилу.

ПЕРЕСУД ПОСЛЕ ОТВЕТОВ, А ИСТОРИЯ НЕ ПЕРЕПИСЫВАЕТСЯ: стадия `answers`
кладёт СВОЙ блок `cycle_status` в `stages.answers.cycle_status`, и её
исход судится тем же `cycle_outcome`. Пересуд берёт ЗАЯВКУ ИСХОДА У
САМОГО ЦИКЛА (состояние, записанное стадией цикла в
`stages.cycle_status.state`) и читает реестр ЗАНОВО тем же загрузчиком:
о цикле после ответов не меняется ничего, кроме реестра, а состояние
«ответы применены» о цикле не утверждает ничего вовсе (замечание С2
второго неавторского ревью REVIEW2-A07RUN-e67bef1c-COSMOS). Заявивший
чистоту цикл + свежий реестр судится честно — закрытые ответами
блокеры дают зелень, новые дают красноту; не заявивший исхода цикл
остаётся `cycle-incomplete`. Запись стадии `cycle` в журнале остаётся
как была — её исход в момент стадии правда.

ОДИН ЗАКОН ИТОГА НА ВСЕХ ЧИТАТЕЛЕЙ ЗАПИСИ: состояние прогона при
закрытии записи считает СЛОВАРЬ записи (`vmp/run_record.py`,
`state_from_journal` — по исходам ВСЕХ записей журнала), и своего
отбора записей раннер не держит (замечание С1 второго неавторского
ревью: отбор «последняя запись стадии, ответы перекрывают цикл» знал
только раннер, а хранитель записи `cdpm/run_record.py::_record_state` и
карточка COSMOS `webapp/run_journal.py::_keeper_state` — нет, и одна
запись одного прогона давала два разных итога). Условие аудитора
«текущий итог может стать чистым» исполняется НЕ итогом записи, а
судом о ТЕКУЩЕМ состоянии дел: ожиданиями набора и вердиктами по
ПОСЛЕДНЕМУ блоку цикла (`cycle_clean_problems` судит пересуженный блок
стадии ответов, а нет его — блок стадии цикла). Красный цикл,
разрешённый ответами, даёт запись «остановлен по правилу» с чистыми
ожиданиями набора: история красна, текущая чистота видна — и второго
закона состояний для этого заводить не пришлось.

Запуск — ТОЛЬКО по слову владельца («Правило прогонов»):
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> --preflight
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> --passport-only
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> --until package
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> --run run-0001 --until cycle
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> \
        --kit-dir open-bench/polyaris-h0c --cosmos-press <отчёт нажатий>.json
    python open-bench/polyaris-h0/run_local_pipeline.py --out <каталог> \
        --kit-dir open-bench/polyaris-h0c --catalog <снимок каталога> \
        --bindings <привязки под него> --passport-only
Окружение ставит сам скрипт: CDPM_RUNS_DIR=<out>/runs (прогоны вне
общего каталога данных), CDPM_AGENTS_DEFAULT — раскладка набора
(манифест называет её путём ОТ КОРНЯ репозитория); каталог, привязки и
решатель — ключи --catalog / --bindings / --solver-url, они сильнее
переменных контура; ключи провайдеров — из настроек CDPM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
# Корень репозитория в путях импорта — ДО первой стадии: паспорт прогона
# (vmp.run_passport) собирается раньше, чем строится Runner, и путь ему
# нужен уже тогда.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from vmp import run_passport as rp                                # noqa: E402
from vmp import run_record as rr                                  # noqa: E402

STAGES = ("create", "data", "extract", "data_run", "cycle", "answers", "package")
CUSTOMER = "Полярис-H0 (ВМП H0 комплекта)"   # умолчание; заказчик — из манифеста набора
# Исполняемый код серии: отпечаток исходников этих корней штампуется в
# состояние прогона на первой стадии и сверяется перед каждой следующей
# (С-4 проверки плана: коммит сам по себе серию к коду не привязывает —
# раннер исполняет живое дерево, модули гейта импортируются позже старта).
CODE_ROOTS = ("cdpm", "vmp", "open-bench/polyaris-h0", "open-bench/polyaris-h0k")


def _log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def _git(repo: Path, *args) -> str:
    try:
        # rstrip, не strip: у первой строки porcelain ведущий пробел —
        # часть двухбуквенного статуса (" M path").
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                              text=True, timeout=30).stdout.rstrip()
    except Exception:                                        # noqa: BLE001
        return ""


def code_identity(repo: Path = REPO) -> dict:
    """Кортеж исполняемого кода: хэш каждого исходника (py/json корней
    CODE_ROOTS), сводный отпечаток, коммит HEAD и грязь дерева в этих
    корнях (рабочее дерево общее с сессией COSMOS — судим только свои
    корни)."""
    files: dict = {}
    for root in CODE_ROOTS:
        for p in sorted((repo / root).rglob("*")):
            if p.suffix not in (".py", ".json") or not p.is_file():
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            rel = str(p.relative_to(repo)).replace("\\", "/")
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    digest = hashlib.sha256("\n".join(f"{k} {v}" for k, v in files.items())
                            .encode("utf-8")).hexdigest()[:16]
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *CODE_ROOTS)
    return {"fingerprint": digest, "files": files,
            "commit": _git(repo, "rev-parse", "HEAD")[:12],
            "tree_dirty": bool(dirty),
            "dirty_paths": [ln[3:].strip().replace("\\", "/") for ln in dirty.splitlines()]}


def _loaded_sources(repo: Path = REPO) -> set:
    """Исходники репозитория, уже загруженные в этот процесс."""
    out: set = set()
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            out.add(str(Path(f).resolve().relative_to(repo)).replace("\\", "/"))
        except (ValueError, OSError):
            pass
    return out


def blocking_paths(paths, repo: Path = REPO) -> list:
    """Из изменённых/грязных путей — те, что исполняет конвейер: корни
    CDPM и раннера целиком, из vmp — только загруженные модули (сессия
    COSMOS правит vmp в том же дереве; её незагруженные файлы — лишь
    предупреждение)."""
    loaded = _loaded_sources(repo)
    return [p for p in paths
            if p in loaded or p.startswith("cdpm/") or p.startswith("open-bench/polyaris-h0/")]


def _primary(item: dict):
    for s in item.get("sources") or []:
        if isinstance(s, dict):
            return (str(s.get("sid")), str(s.get("anchor")))
    return None


def clause_level_diff(cand: dict, ref: dict) -> dict:
    """Поклаузная сверка кандидата с эталонной моделью читателя: строки
    и целевые сопоставляются по первичной клаузе (sid, якорь) — id у
    извлечения свои. Возвращает {missing, extra, mismatches}."""
    from cdpm import model as m
    fams = m.template_family_traits()  # noqa: F841 — прогрев среза базы
    out = {"missing": [], "extra": [], "mismatches": [], "ok": []}
    def _fields(r):
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        params = {k: str((v or {}).get("value") if isinstance(v, dict) else v)
                  for k, v in (fit.get("params") or {}).items()}
        return {"kind": r.get("kind"), "zone": r.get("zone"),
                "weight": r.get("weight"), "family": fit.get("family"),
                "category": fit.get("category"), "params": params}
    def _ofields(o):
        c = o.get("criterion") if isinstance(o.get("criterion"), dict) else {}
        return {"zone": o.get("zone"), "direction": o.get("direction"),
                "metric": o.get("metric"), "kind": c.get("kind"),
                "rate": c.get("rate_per_duty") if c else (o.get("rate") or {}).get("value")}
    ref_rules = {_primary(r): r for r in ref.get("rules", []) if _primary(r)}
    cand_by_anchor: dict = {}
    for r in cand.get("rules", []):
        if r.get("kind") == "OUT":
            continue
        cand_by_anchor.setdefault(_primary(r), []).append(r)
    for key, rr in ref_rules.items():
        got = cand_by_anchor.get(key) or []
        if not got:
            out["missing"].append({"anchor": key, "id": rr["id"], "kind": rr.get("kind")})
            continue
        want = _fields(rr)
        best = None
        for c in got:
            have = _fields(c)
            diff = {k: (want[k], have[k]) for k in want if want[k] != have[k]}
            if best is None or len(diff) < len(best[1]):
                best = (c, diff)
        c, diff = best
        if diff:
            out["mismatches"].append({"anchor": key, "ref": rr["id"],
                                      "cand": c.get("id"), "diff": diff})
        else:
            out["ok"].append(rr["id"])
        if len(got) > 1:
            out["extra"].append({"anchor": key, "ids": [x.get("id") for x in got[1:]],
                                 "note": "несколько строк на одной клаузе"})
    for key, rs in cand_by_anchor.items():
        if key not in ref_rules:
            out["extra"].append({"anchor": key, "ids": [x.get("id") for x in rs]})
    ref_obj = {_primary(o): o for o in ref.get("objectives", []) if _primary(o)}
    cand_obj: dict = {}
    for o in cand.get("objectives", []):
        cand_obj.setdefault(_primary(o), []).append(o)
    for key, ro in ref_obj.items():
        got = cand_obj.get(key) or []
        if not got:
            out["missing"].append({"anchor": key, "id": ro["id"], "kind": "objective"})
            continue
        want, have = _ofields(ro), _ofields(got[0])
        diff = {k: (want[k], have[k]) for k in want if str(want[k]) != str(have[k])}
        if diff:
            out["mismatches"].append({"anchor": key, "ref": ro["id"],
                                      "cand": got[0].get("id"), "diff": diff})
        else:
            out["ok"].append(ro["id"])
    for key, os_ in cand_obj.items():
        if key not in ref_obj:
            out["extra"].append({"anchor": key, "ids": [x.get("id") for x in os_],
                                 "note": "целевая сверх эталона"})
    return out


def _anchor_key(src) -> str | None:
    """Ключ якоря источника правила: «sid:якорь» без лишних пробелов."""
    if not isinstance(src, dict):
        return None
    sid = str(src.get("sid") or "").strip()
    anchor = " ".join(str(src.get("anchor") or "").split())
    return f"{sid}:{anchor}" if anchor else None


def rule_ids_by_etalon(rules: list, etalon: dict | None) -> dict:
    """{id правила модели: id эталона (VN-…)} — сопоставление по якорям
    источников, как у гейта эквивалентности. Модель CDPM называет
    правила стабильными id по якорю (R-S2-лист-«VMP-H0-2»-строка-5), а
    ожидания манифеста — VN-номерами эталона; без карты раннер краснел
    ложно (Н0-C фаза 1, run-0102, 09.09.2026). Правило без пары в
    эталоне остаётся под своим id."""
    by_anchor: dict = {}
    for e in (etalon or {}).get("rules") or []:
        if isinstance(e, dict):
            for src in e.get("sources") or []:
                k = _anchor_key(src)
                if k and k not in by_anchor:
                    by_anchor[k] = str(e.get("id"))
    out: dict = {}
    for r in rules:
        rid = str(r.get("id"))
        hit = None
        for src in r.get("sources") or []:
            k = _anchor_key(src)
            if k and k in by_anchor:
                hit = by_anchor[k]
                break
        out[rid] = hit or rid
    return out


# ── Честность итога ожиданий (поручение владельца 10.09.2026) ──
# Прежде строка «ОЖИДАНИЯ НАБОРА выполнены» перечисляла ВСЕ ключи
# `expect` манифеста, включая те, которых раннер не касается: числа
# сверки с ИДАК получаются НАЖАТИЯМИ в COSMOS, а не прогоном CDPM, и
# «выполнено» по ним было словом без основания. Теперь итог различает
# три вещи: проверено (судимые ключи поимённо), не судится раннером
# (ключи с причиной) и не выполнено (расхождения).

#: Ключи `expect`, которые раннер СУДИТ сам: имя → чем судится.
JUDGED_EXPECT_KEYS = {
    "questions": "анкета модели",
    "allowed_modes": "режимы задач в манифесте пакета",
    "readiness_ladder": "лестница готовности в манифесте пакета",
    "optimization_state": "ячейка optimization матрицы готовности",
    "note_contains": "заметка ячейки и notes лестницы",
    "objectives": "число целевых модели",
    "history": "раздел истории сотрудника в манифесте пакета",
    "family_complete": "полнота параметров семьи по носителям ВМП",
    "coverage_gap_rules": "правила посадки C (пробел покрытия)",
    "rule_landing": "категория посадки правила",
    "package_ready": "собран ли пакет постановки",
    "catalog_requests": "кандидаты заявки по ручке панели",
    "stage_verdicts": "вердикты стадий по манифесту комплекта",
    "consistency": "вердикт непротиворечивости в манифесте пакета",
}

#: Ключи `expect`, которых раннер НЕ касается, и ПОЧЕМУ. `cosmos_numbers`
#: судится только с машинным отчётом нажатий (`--cosmos-press`).
UNJUDGED_EXPECT_KEYS = {
    "cosmos_numbers": ("числа сверки с ИДАК в COSMOS: получаются нажатиями "
                       "в COSMOS, прогоном CDPM не проверяются — нужен "
                       "машинный отчёт нажатий (ключ --cosmos-press)"),
    "cosmos_default_mode": ("режим кнопки COSMOS: выбирается при нажатии в "
                            "COSMOS, прогоном CDPM не проверяется"),
    "record_stitched": ("сшивка записи о прогоне: этапы COSMOS попадают к "
                        "хозяину записи от НАЖАТИЙ в COSMOS, прогоном CDPM "
                        "не проверяются — нужен машинный отчёт нажатий "
                        "(ключ --cosmos-press)"),
    "golden_by_owner_only": ("признак золотого прогона: его ставит СЛОВОМ "
                             "владелец у хозяина записи после приёмки на "
                             "Prod, прогоном CDPM без нажатий он не "
                             "проверяется — нужен машинный отчёт нажатий "
                             "(ключ --cosmos-press)"),
    "family": ("описание семьи каталога справочно: проверяются её "
               "следствия — family_complete и rule_landing"),
}

#: Вердикты задач COSMOS, при которых счёт признаётся состоявшимся.
#: Это УМОЛЧАНИЕ раннера: набор, у которого вердикт задачи другой,
#: называет его сам ключом `stage_verdicts` манифеста (ниже).
COSMOS_VERDICTS = {"pairing": "пейринг построен", "rostering": "допустимо"}


# ── Ожидаемые вердикты стадий (ключ `stage_verdicts` манифеста) ──
# Вердикт стадии — свойство НАБОРА, а не раннера: у Н0-B извлечение «не
# эквивалентно до ответов» по замыслу (второй параметр семьи приходит
# ответом анкеты), и красная строка тут была бы неправдой. Поэтому
# ожидаемые вердикты объявляет комплект, а раннер их только сверяет.
# Ключа нет — прежнее поведение: вердикты стадий не судятся.

#: Стадии COSMOS в ожидании вердиктов: их вердикт приходит машинным
#: отчётом нажатий (--cosmos-press), а не прогоном CDPM.
COSMOS_TASK_STAGES = ("pairing", "rostering")
#: Стадия сверки решений с исходными документами.
SOURCE_CHECK_STAGE = "source-check"
#: Вердикт стадии сверки с ИДАК, когда нарушений нет.
SOURCE_CHECK_CLEAN = "без нарушений"


def source_check_verdict(press: dict | None) -> str | None:
    """Вердикт стадии «сверка с ИДАК» по машинному отчёту нажатий:
    «без нарушений», когда у КАЖДОЙ сверенной задачи нарушений ноль;
    иначе «нарушений N» общим числом. Блоков сверки в отчёте нет —
    None: судить нечем, и выдумывать вердикт нельзя."""
    if not isinstance(press, dict):
        return None
    total = None
    for task in COSMOS_TASK_STAGES:
        block = press.get(task)
        if not isinstance(block, dict):
            continue
        _rows, problems = _cosmos_rows_and_problems(block)
        if problems is None:
            continue
        total = (total or 0) + int(problems)
    if total is None:
        return None
    return SOURCE_CHECK_CLEAN if total == 0 else "нарушений %d" % total


def expected_task_verdict(exp: dict, task: str) -> str:
    """Ожидаемый вердикт задачи COSMOS: из `stage_verdicts` манифеста
    комплекта, а нет его — прежнее умолчание раннера (совместимость с
    комплектами, выпущенными до ключа)."""
    want = (exp or {}).get("stage_verdicts")
    if isinstance(want, dict):
        got = want.get(task)
        if isinstance(got, str) and got.strip():
            return got
    return COSMOS_VERDICTS[task]


def stage_verdicts_problems(exp: dict, stages: dict,
                            press: dict | None = None
                            ) -> tuple[list[str], list[str]]:
    """Ожидание `stage_verdicts` — вердикты стадий по манифесту набора.

    Ожидание — `{имя стадии: ожидаемый вердикт}`; ключи, начинающиеся с
    подчёркивания, — пояснения набора, они не судятся. Вердикт стадии
    CDPM берётся из состояния раннера (гейт эквивалентности выхода),
    вердикт задачи COSMOS и сверки с ИДАК — из машинного отчёта
    нажатий.

    Возврат — (расхождения, предупреждения). Расхождение называет
    СТАДИЮ, что вышло и что ожидалось. Предупреждением, а не
    расхождением, отвечают две честные пустоты: стадия не пройдена
    (прогон остановлен раньше) и отчёта нажатий нет — судить нечем, и
    красная строка была бы неправдой. Имя стадии, неизвестное раннеру, —
    расхождение: молча пропускать ожидание нельзя."""
    want = (exp or {}).get("stage_verdicts")
    if not isinstance(want, dict):
        return [], []
    stages = stages if isinstance(stages, dict) else {}
    problems: list[str] = []
    warnings: list[str] = []
    for name in sorted(k for k in want if not str(k).startswith("_")):
        cell = want.get(name)
        # Ожидание стадии — слово вердикта либо ПЕРЕЧЕНЬ законных слов:
        # у стадии, где модель вправе задать необязательный вопрос
        # (Н0-С, 17.09.2026), extract законно и EQUIVALENT, и
        # NOT_EQUIVALENT — оба пути набор объявляет явно.
        cells = cell if isinstance(cell, list) else [cell]
        if not cells or not all(isinstance(c, str) and c.strip() for c in cells):
            problems.append(f"вердикт стадии {name}: ожидание {cell!r} не "
                            f"слово вердикта")
            continue
        if name in STAGES:
            got = (stages.get(name) or {}).get("verdict") \
                if isinstance(stages.get(name), dict) else None
            if got is None:
                warnings.append(f"вердикт стадии {name} не сверен: стадия не "
                                f"пройдена этим прогоном")
                continue
        elif name in COSMOS_TASK_STAGES or name == SOURCE_CHECK_STAGE:
            if not isinstance(press, dict):
                warnings.append(f"вердикт стадии {name} не сверен: он "
                                f"получается НАЖАТИЯМИ в COSMOS — нужен "
                                f"машинный отчёт нажатий (ключ "
                                f"--cosmos-press)")
                continue
            if name == SOURCE_CHECK_STAGE:
                got = source_check_verdict(press)
                if got is None:
                    problems.append(f"вердикт стадии {name}: в отчёте нажатий "
                                    f"нет блоков сверки — сверить нечем")
                    continue
            else:
                block = press.get(name)
                got = block.get("verdict") if isinstance(block, dict) else None
        else:
            problems.append(f"вердикт стадии {name}: такой стадии раннер не "
                            f"знает — судить нечем (стадии прогона: "
                            f"{', '.join(STAGES)}; стадии COSMOS: "
                            f"{', '.join(COSMOS_TASK_STAGES)}, "
                            f"{SOURCE_CHECK_STAGE})")
            continue
        if str(got) not in cells:
            problems.append(f"вердикт стадии {name}: {got!r}, ожидался "
                            f"{cell!r}")
    return problems, warnings


# ── Непротиворечивость постановки (гейт выпуска пакета, 11.09.2026) ───
# Половина CDPM сделала SMT-проверку непротиворечивости ОБЯЗАТЕЛЬНЫМ
# гейтом выпуска пакета постановки (слово владельца 11.09.2026). Что
# видит раннер:
#   * ОТКАЗ выпуска несёт добавочный ключ `code` тела ответа:
#     smt-contradiction — правила противоречат друг другу (в `detail`
#     названо ядро: R-…, R-…); smt-unavailable — проверить не удалось
#     (нет z3, перевод дважды кривой, unknown, канал отказал), и гейт
#     закрыт наглухо (fail-closed). Отказ без кода — прежние отказы
#     выпуска, слова раннера у них не меняются;
#   * МАНИФЕСТ выпущенного пакета несёт необязательный блок
#     `consistency` с вердиктом (`sat` — противоречий нет, `none` —
#     жёстких правил нет), отпечатком модели, числом правил и
#     переведённых правил и переводчиком (роль, канал, модель);
#   * ВОПРОС части 1 «противоречие» несёт необязательное поле
#     `contradiction`: какая норма и какие значения разошлись.
#
# Волна 17 добавляет к тем же двум кодам отказы контрактов П0 (раздел 5
# «Коды отказа выпуска (сводно)» документа P0-CONTRACTS-v1.2-20260911 и
# строка «Коды отказа выпуска» стороны COSMOS, раздел 0-бис документа
# P0-COSMOS-SIDE-v1-20260911): неполный перевод свода, ячейка посадки без
# подтверждённого происхождения, разошедшиеся носители параметра,
# недостача обязательного параметра семьи, направление критерия не по
# каталогу, целевая не равна проекции модели, семья без свидетельства
# поддержки, посадка неизвестной версии или неполная, расхождение
# пересчёта с манифестом. САМИ отказы рождает код CDPM (рубеж выпуска) и
# приёмный путь COSMOS; раннеру они приходят тем же телом 409 с ключом
# `code` — он только НАЗЫВАЕТ их словами, особых веток у них нет.

#: Отказы выпуска пакета, у которых есть слова раннера: код тела ответа
#: → чем он является. Кода нет — прежнее поведение (тело как есть).
PACKAGE_REFUSAL_CODES = {
    # Гейт непротиворечивости постановки (11.09.2026; полнота — A01).
    "smt-contradiction": "правила постановки противоречат друг другу",
    "smt-unavailable": "проверка непротиворечивости невозможна (fail-closed)",
    "smt-incomplete": "переведены не все жёсткие правила свода",
    # Происхождение значения в ячейке посадки (A03).
    "answer-provenance": ("ячейка посадки не подтверждена точной версией "
                          "ответа и его издания"),
    # Исполняемая модель: посадка, семья, критерий, целевая, поддержка
    # (A05, K01, A06, A08, K06).
    "param-carriers-diverge": ("носители параметра разошлись по значению, "
                               "единице или отношению"),
    "family-incomplete": ("обязательный параметр использованной семьи не "
                          "задан и не отклонён (перепосадка требуется)"),
    "criterion-direction": ("направление целевой или критерия не по "
                            "таблице каталога"),
    "objective-mismatch": "objective.json не равен проекции модели",
    "support-missing": ("у семьи нет подтверждённой поддержки для нужного "
                        "продукта, режима и версии"),
    # Читатель и приёмник пакета на стороне COSMOS (A05 п. 8, В-8).
    "landing-version": ("версия схемы посадки неизвестна либо старая форма "
                        "без режима закреплённой версии"),
    "landing-incomplete": ("ячейка посадки landing/2 без обязательных полей "
                           "(значение, единица, отношение, источник)"),
    "manifest-mismatch": ("пересчёт families или support по пакету разошёлся "
                          "с манифестом"),
}


def package_refusal(status: int, text: str) -> dict:
    """Отказ выпуска пакета словами: `{status, code, detail, words}`.

    Тело отказа — `{"detail": "<слова>", "code": "<код>"}` (ключ `code`
    добавочный: прежние отказы выпуска идут без него). Тело не JSON,
    не словарь, без кода — `code` None и `words` None: молчать о чужом
    отказе нельзя, но и выдумывать ему код тоже."""
    detail = None
    code = None
    try:
        body = json.loads(text or "")
    except ValueError:
        body = None
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, list):
            d = "; ".join(str(x) for x in d)
        detail = str(d) if d is not None else None
        c = body.get("code")
        code = str(c).strip() if isinstance(c, str) and c.strip() else None
    out = {"status": int(status), "code": code, "detail": detail,
           "words": None}
    if code:
        why = PACKAGE_REFUSAL_CODES.get(code) or ("код раннеру неизвестен: "
                                                 "назовите его словами в "
                                                 "раннере")
        out["words"] = (f"выпуск пакета отказан ({status}): {code} — {why}"
                        + (f": {detail}" if detail else ""))
    return out


#: Слова вердикта непротиворечивости, которые набор вправе ожидать:
#: `sat` — противоречий нет; `none` — жёстких правил нет, проверять
#: было нечего. `unsat` ожиданием не бывает: с ним пакет не выпускается
#: вовсе (гейт отвечает отказом smt-contradiction).
CONSISTENCY_VERDICTS = ("sat", "none")


def consistency_words(man) -> str | None:
    """Строка отчёта о непротиворечивости из манифеста ПАКЕТА: вердикт,
    правил всего и переведено, переводчик (роль, канал, модель) и путь
    отчёта. Блока в манифесте нет — None (печатать нечего)."""
    block = (man or {}).get("consistency") if isinstance(man, dict) else None
    if not isinstance(block, dict):
        return None
    tr = block.get("translator") if isinstance(block.get("translator"), dict) else {}
    who = ", ".join(f"{name} {tr[key]}" for key, name
                    in (("role", "роль"), ("provider", "канал"),
                        ("model", "модель")) if tr.get(key))
    out = (f"непротиворечивость: вердикт {block.get('verdict')!r}, правил "
           f"{block.get('n_rules')}, переведено {block.get('n_translated')}")
    if who:
        out += f"; переводчик: {who}"
    if block.get("report"):
        out += f"; отчёт {block.get('report')}"
    return out


def consistency_problems(exp: dict, man) -> list[str]:
    """Ожидание `consistency` — слово вердикта непротиворечивости против
    блока `consistency` манифеста ПАКЕТА.

    Ожидания у набора нет — не судится (комплекты, выпущенные до ключа,
    краснеть не обязаны). Ожидание есть, а блока в манифесте пакета нет
    — красная строка: гейт выпуска обязан класть вердикт в манифест, и
    молчание манифеста доверием не покрывается (fail-closed). Пакета
    нет вовсе (`man` None — выпуск отказан либо прогон встал раньше) —
    тоже красная строка: сверять нечем, а ожидание объявлено."""
    want = (exp or {}).get("consistency")
    if want is None:
        return []
    if not isinstance(want, str) or want.strip() not in CONSISTENCY_VERDICTS:
        return [f"вердикт непротиворечивости: ожидание {want!r} не слово "
                f"вердикта ({', '.join(CONSISTENCY_VERDICTS)})"]
    want = want.strip()
    if not isinstance(man, dict):
        return ["вердикт непротиворечивости: пакет постановки не собран — "
                "сверять нечем, а ожидание объявлено"]
    block = man.get("consistency")
    if not isinstance(block, dict):
        return ["вердикт непротиворечивости: пакет не несёт вердикта "
                "непротиворечивости (ключа consistency в манифесте пакета "
                "нет) — гейт выпуска обязан его положить"]
    got = block.get("verdict")
    if str(got) != want:
        return [f"вердикт непротиворечивости: {got!r}, ожидался {want!r}"]
    return []


def contradiction_words(q) -> str | None:
    """Поле `contradiction` вопроса части 1 словами: какая норма (семья
    посадки либо предмет) с каким параметром и какие значения разошлись
    — правило, значение, единица, источник. Поля нет — None."""
    block = (q or {}).get("contradiction") if isinstance(q, dict) else None
    if not isinstance(block, dict):
        return None
    norm = block.get("norm") if isinstance(block.get("norm"), dict) else {}
    who = norm.get("family") or norm.get("subject") or "не названа"
    values = []
    for v in block.get("values") or []:
        if not isinstance(v, dict):
            continue
        unit = f" {v['unit']}" if v.get("unit") else ""
        # Место источника — объект {sid, anchor} ($defs/source_ref замка
        # части 1, CDPM 11.09); строка — как есть (совместимость).
        s = v.get("source")
        if isinstance(s, dict):
            s = "/".join(str(s[k]) for k in ("sid", "anchor") if s.get(k))
        src = f" (источник {s})" if s else ""
        values.append(f"{v.get('rule_id')} = {v.get('value')}{unit}{src}")
    return (f"противоречие: норма {who}"
            + (f", параметр {norm['param']}" if norm.get("param") else "")
            + (f", уточнитель {norm['qualifier']}" if norm.get("qualifier") else "")
            + "; значения: " + ("; ".join(values) or "не названы"))


def question_lines(questions, limit: int = 10) -> list[str]:
    """Вопросы анкеты словами для журнала: строка вопроса, а у вопроса с
    полем `contradiction` — ещё одна строка о норме и её значениях.
    Только ПОКАЗ: вопросы части 1 раннер не судит."""
    out: list[str] = []
    for q in list(questions or [])[:limit]:
        if not isinstance(q, dict):
            continue
        out.append(f"   ? {q.get('id')} [{q.get('part')}]: {q.get('title')}")
        words = contradiction_words(q)
        if words:
            out.append("     " + words)
    return out


def _cosmos_rows_and_problems(block: dict) -> tuple[int | None, int | None]:
    """Строк сверки и нарушений из блока задачи отчёта нажатий: строки —
    сумма счётчиков `check.counts` (ok + soft_info + not_checkable),
    нарушения — `check.problems`; блока сверки нет — (None, None)."""
    check = block.get("check") if isinstance(block.get("check"), dict) else None
    if not check:
        return None, None
    counts = check.get("counts") if isinstance(check.get("counts"), dict) else {}
    rows = sum(int(v) for v in counts.values() if isinstance(v, (int, float)))
    problems = check.get("problems")
    return rows, (int(problems) if isinstance(problems, (int, float)) else None)


def cosmos_numbers_problems(exp: dict, press: dict | None,
                            package_sha: str | None = None
                            ) -> tuple[list[str], list[str]]:
    """Ожидание `cosmos_numbers` против машинного отчёта нажатий COSMOS.

    Отчёт — словарь вида `{package_sha, package_run, pairing, rostering}`,
    у задачи `{verdict, score, certified, check: {counts: {ok, soft_info,
    not_checkable}, problems}}` (его пишет прогон нажатий COSMOS).
    Ожидание манифеста — строка «строк/нарушений», например «11/0».

    Критерий «без ошибок» — слово владельца 09.09.2026: ВЕРДИКТЫ
    («пейринг построен», «допустимо»), СЕРТИФИКАТЫ и НОЛЬ нарушений.
    Число СТРОК сверки — справочное: оно гуляет по чтению модели
    (`not_checkable` ±1 у одного и того же ИДАК), поэтому расхождение
    РОВНО в строках при совпавших нарушениях — предупреждение, а не
    провал; расхождение в нарушениях, вердикте или сертификате —
    провал. Возвращает (провалы, предупреждения)."""
    want = exp.get("cosmos_numbers")
    if not isinstance(want, dict):
        return [], []
    if not isinstance(press, dict):
        return ["числа COSMOS: машинного отчёта нажатий нет — сверить нечем "
                "(ключ --cosmos-press)"], []
    problems: list[str] = []
    warnings: list[str] = []
    if package_sha and press.get("package_sha") \
            and str(press["package_sha"]) != str(package_sha):
        problems.append("числа COSMOS: отчёт нажатий о другом пакете "
                        f"({str(press['package_sha'])[:12]}…, а прогон собрал "
                        f"{str(package_sha)[:12]}…)")
    for task in ("pairing", "rostering"):
        cell = want.get(task)
        if cell is None:
            continue
        block = press.get(task)
        if not isinstance(block, dict):
            problems.append(f"числа COSMOS[{task}]: в отчёте нажатий нет "
                            f"этой задачи")
            continue
        verdict = block.get("verdict")
        want_verdict = expected_task_verdict(exp, task)
        if verdict != want_verdict:
            problems.append(f"числа COSMOS[{task}]: вердикт {verdict!r}, "
                            f"ожидался {want_verdict!r}")
        if block.get("certified") is not True:
            problems.append(f"числа COSMOS[{task}]: сертификат не выдан "
                            f"(certified={block.get('certified')!r})")
        rows, found = _cosmos_rows_and_problems(block)
        if rows is None:
            problems.append(f"числа COSMOS[{task}]: в отчёте нажатий нет "
                            f"блока сверки check — строки и нарушения "
                            f"сверить нечем")
            continue
        want_rows, _sep, want_problems = str(cell).partition("/")
        try:
            want_rows_n = int(want_rows)
            want_problems_n = int(want_problems)
        except ValueError:
            problems.append(f"числа COSMOS[{task}]: ожидание {cell!r} не "
                            f"вида «строк/нарушений»")
            continue
        if found != want_problems_n:
            problems.append(f"числа COSMOS[{task}]: нарушений {found}, "
                            f"ожидалось {want_problems_n}")
        elif rows != want_rows_n:
            warnings.append(f"числа COSMOS[{task}]: строк сверки {rows}, в "
                            f"манифесте {want_rows_n} при {found} нарушениях "
                            f"— число строк справочное (гуляет по чтению "
                            f"модели), критерий «без ошибок» выполнен")
    return problems, warnings


def load_cosmos_press(path) -> dict | None:
    """Машинный отчёт нажатий COSMOS по пути ключа --cosmos-press: путь
    не назван — None (числа COSMOS тогда не судятся); файла нет, не
    JSON, не словарь — отказ словами ДО прогона, а не молчаливое
    «нечего судить»."""
    if not path:
        return None
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"--cosmos-press: файла нет: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit("--cosmos-press: отчёт нажатий не разобран как "
                         f"JSON: {e}") from None
    if not isinstance(doc, dict):
        raise SystemExit("--cosmos-press: отчёт нажатий — не словарь")
    return doc


def expect_key_verdicts(exp: dict, judged_extra: tuple = ()) -> tuple[list, dict]:
    """Разбор ключей `expect` на судимые и несудимые: (перечень судимых,
    {несудимый ключ: причина}). `judged_extra` — ключи, ставшие судимыми
    у ЭТОГО запуска (например `cosmos_numbers` при --cosmos-press).
    Ключ, неизвестный обеим таблицам, честно назван неизвестным — новое
    ожидание не выдаёт себя за проверенное."""
    judged: list[str] = []
    skipped: dict = {}
    for key in sorted(k for k in exp if not str(k).startswith("_")):
        if key in JUDGED_EXPECT_KEYS or key in judged_extra:
            judged.append(key)
        elif key in UNJUDGED_EXPECT_KEYS:
            skipped[key] = UNJUDGED_EXPECT_KEYS[key]
        else:
            skipped[key] = ("ключ раннеру неизвестен: судить его нечем — "
                            "опишите проверку в раннере либо уберите ключ")
    return judged, skipped


def extra_expect_problems(exp: dict, model: dict, man, requests_status,
                          etalon: dict | None = None) -> list[str]:
    """Ожидания наборов-вариаций Н0 (поручение владельца 09.09.2026) сверх
    штатных ключей `expect`:
      family_complete {семья: [параметры]} — все параметры семьи заданы хотя
        бы одним носителем ВМП (Н0-B: второй — по ответу анкеты);
      coverage_gap_rules [id] — ровно эти правила в посадке C (Н0-C, фаза 1);
      rule_landing {id: категория} — категория посадки правила (Н0-C, фаза 2);
      package_ready false|true — пакет не собран (man None) / собран;
      catalog_requests {count, kind, zone, statement_contains} — кандидаты
        заявки по ручке панели /api/runs/<run>/catalog-requests (Н0-C, фаза 1).
    Правила модели называются по эталону (`etalon` — ВМП набора): id по
    якорям источников, как у гейта эквивалентности (rule_ids_by_etalon).
    Чистая функция: судится без сервера, тесты — tests/test_cosmos_runner_expect.py."""
    problems: list[str] = []
    rules = [r for r in (model.get("rules") or []) if isinstance(r, dict)]
    names = rule_ids_by_etalon(rules, etalon)
    fam_params: dict = {}
    for r in rules:
        fit = r.get("template_fit") or {}
        fam = fit.get("family")
        if fam:
            fam_params.setdefault(str(fam), set()).update(
                str(k) for k in (fit.get("params") or {}))
    for fam, want in (exp.get("family_complete") or {}).items():
        have = fam_params.get(str(fam), set())
        missing = [p for p in (want or []) if str(p) not in have]
        if missing:
            problems.append(f"семья {fam}: не заданы параметры {missing} "
                            f"(есть {sorted(have)})")
    if "coverage_gap_rules" in exp:
        got = sorted(names[str(r.get("id"))] for r in rules
                     if (r.get("template_fit") or {}).get("category") == "C")
        want = sorted(str(x) for x in (exp.get("coverage_gap_rules") or []))
        if got != want:
            problems.append(f"посадка C у правил {got}, ожидалось {want}")
    for rid, cat in (exp.get("rule_landing") or {}).items():
        r = next((x for x in rules if names[str(x.get("id"))] == str(rid)
                  or str(x.get("id")) == str(rid)), None)
        got = (r.get("template_fit") or {}).get("category") if r else None
        if got != cat:
            problems.append(f"посадка {rid}: категория {got!r}, ожидалось {cat!r}")
    if "package_ready" in exp:
        want = bool(exp.get("package_ready"))
        got = man is not None
        if got != want:
            problems.append("пакет " + ("собран, а ожидался стоп (пробел покрытия)"
                                        if got else "не собран, а ожидалась готовность"))
    cr = exp.get("catalog_requests")
    if isinstance(cr, dict):
        if not isinstance(requests_status, dict):
            problems.append("заявки в каталог: ручка панели catalog-requests не "
                            "ответила — сверить нечем")
        else:
            cands = [c for c in (requests_status.get("candidates") or [])
                     if isinstance(c, dict) and c.get("candidate")]
            if "count" in cr and len(cands) != int(cr["count"]):
                problems.append(f"заявок-кандидатов {len(cands)}, ожидалось "
                                f"{cr['count']}: {[c.get('rule_id') for c in cands]}")
            for c in cands:
                for key in ("kind", "zone"):
                    if cr.get(key) and str(c.get(key)) != str(cr[key]):
                        problems.append(f"заявка {c.get('rule_id')}: {key} "
                                        f"{c.get(key)!r}, ожидалось {cr[key]!r}")
                sub = str(cr.get("statement_contains") or "").lower()
                if sub and sub not in str(c.get("statement") or "").lower():
                    problems.append(f"заявка {c.get('rule_id')}: в формулировке "
                                    f"нет «{sub}»")
    return problems


# ─────────────────────── Паспорт прогона (слово владельца 10.09.2026) ──
# «Перед каждым прогоном нужно чётко установить все его параметры… Не
# может быть, что остаётся старый каталог предыдущего прогона. Каждый
# прогон имеет свои параметры». Сборка и сверка — в продуктовом модуле
# vmp/run_passport.py (его же читают панели); здесь — двери контура,
# печать и ключ --passport-only.

def apply_doors(catalog=None, bindings=None, solver_url=None) -> None:
    """Двери контура ДО первого чтения: каталог правил
    (CREW_TEMPLATE_CATALOG), привязки исполнителей COSMOS
    (CREW_COSMOS_BINDINGS) и адрес решателя (CREW_SOLVER_URL).

    Ключ командной строки СИЛЬНЕЕ переменной: прогон называет свои
    параметры сам, а не наследует их от предыдущего. Ключ не назван —
    переменная контура остаётся как есть, и паспорт называет её
    источником словами: молчаливого умолчания у прогона нет."""
    if catalog:
        os.environ[rp.CATALOG_ENV] = str(Path(catalog).resolve())
    if bindings:
        os.environ[rp.BINDINGS_ENV] = str(Path(bindings).resolve())
    if solver_url:
        os.environ[rp.SOLVER_ENV] = str(solver_url)


def passport_code(code: dict) -> dict:
    """Блок кода для паспорта: коммит, отпечаток исходников, число
    файлов и грязь дерева. Поимённый перечень 146 файлов в паспорт не
    едет — его заменяет отпечаток, а перечень остаётся в состоянии
    прогона."""
    return {"commit": code.get("commit"), "fingerprint": code.get("fingerprint"),
            "tree_dirty": code.get("tree_dirty"),
            "files": len(code.get("files") or {}),
            "roots": list(CODE_ROOTS)}


def log_passport(p: dict, out: Path) -> Path:
    """Паспорт — в журнал ЦЕЛИКОМ (выжимка строками плюс сам документ) и
    файлом в каталог прогона раннера. Ключей и токенов в паспорте нет по
    построению: читаются только двери и файлы набора."""
    for line in rp.render(p):
        _log(line)
    _log("паспорт прогона целиком:\n"
         + json.dumps(p, ensure_ascii=False, indent=1))
    path = rp.write(p, Path(out) / rp.PASSPORT_FILE)
    _log(f"паспорт прогона записан: {path}")
    return path


def passport_only(out: Path, a) -> int:
    """Ключ --passport-only: собрать и сверить паспорт, НЕ запуская
    прогон. Панель в процессе не поднимается, ключи провайдеров не
    нужны — судится ровно то, чем прогон пойдёт. Код возврата: 0 —
    паспорт сошёлся, 2 — расхождения.

    Паспорт сошёлся — рядом ложится ЗАПИСЬ О ПРОГОНЕ с одним лишь
    объявлением (журнал пуст, состояние «объявлен»): голова записи
    готова, а этапов ещё не было. Паспорт не сошёлся — записи нет:
    запись с объявлением, которое не сошлось с действительностью, была
    бы неправдой."""
    p = rp.build(kit_dir=Path(a.kit_dir).resolve(), repo=REPO,
                 code=passport_code(code_identity()), base_url=a.base_url,
                 until=a.until, catalog_key=a.catalog,
                 bindings_key=a.bindings, solver_key=a.solver_url)
    log_passport(p, out)
    words = rp.refusal(p)
    if words:
        _log(words)
        return 2
    _log("паспорт прогона сошёлся: объявленные параметры равны "
         "действительным")
    record = record_or_warn(
        p, a, planned_stages(a),
        cosmos_env=ask_cosmos_environment(a, kit_llm_scope(p)))
    if record is not None:
        _log(rr.summary(record))
        write_record(record, out)
    return 0


# ────────────────────── Запись о прогоне (проект 10.09.2026, шаг 1) ────
# Прогон — отдельная сущность с номером, началом и концом: объявление до
# старта (голова), журнал этапов с исходами и хронометражем (тело), итоги
# (хвост). Словарь полей — продуктовый модуль vmp/run_record.py; здесь
# только сборка головы из паспорта, отметки этапов и запись файлом.

#: Цель прогона по умолчанию: раннер наборов гоняют регрессом.
PURPOSE_DEFAULT = "регресс"

#: Площадка по умолчанию: панель в процессе — локальная машина.
CONTOUR_DEFAULT = "local"

#: Кто объявил прогон (актор записи): не человек, а раннер.
RECORD_ACTOR = "раннер наборов H0"

#: Имя системы у записей, которые раннер шлёт хранителю (раздел 11.2
#: проекта). Ключ идемпотентности хранителя — (система, этап, подэтап):
#: раннер зовётся «runner», чтобы его этапы не мешались с этапами
#: COSMOS у того же прогона. Окажется, что хранитель принимает только
#: слово «cosmos», — меняется ровно эта строка.
RECORD_SYSTEM = "runner"

#: Имя системы COSMOS в журнале хозяина записи (её этапы судит ожидание
#: record_stitched).
RECORD_SYSTEM_COSMOS = "cosmos"

#: Схема тела дописывания записи (раздел 11.2 проекта).
RECORD_APPEND_SCHEMA = "cosmos-run-record-append/v1"

#: Кто ведёт ФАЙЛ записи в папке прогона.
CUSTODIAN_PANEL, CUSTODIAN_RUNNER = "панель", "раннер"

#: Ручка записи у панели: путь без базы (базу раннер знает свою).
RECORD_ROUTE = "/api/runs/{run}/record"

#: Ручка дерева прогонов у панели (шаг 3 проекта, раздел 12.2): ответ —
#: `{node, path, siblings, golden, compare_golden, compare_previous,
#: expectations}`. Ручку делает половина CDPM; пока её нет, раннер
#: честно оставляет сравнения ПУСТЫМИ, а не выдумывает их сам.
RUN_TREE_ROUTE = "/api/runs/{run}/tree"

#: Вид сравнения ожиданий в итогах записи (раздел 3.3 проекта): ожидания
#: у раннера всегда из манифеста набора.
EXPECTATIONS_KIND = "манифест набора"

#: Вердикт ожиданий в итогах записи. «Не судились» — не то же, что
#: «выполнены»: прогон мог остановиться раньше суда либо у набора нет
#: ключа `expect`.
EXPECT_VERDICT_OK = "выполнены"
EXPECT_VERDICT_BAD = "не выполнены"
EXPECT_VERDICT_NONE = "не судились"

# ───────────────────── Модели COSMOS в голове записи (11.09.2026) ─────
# Голову чеканит ХОЗЯИН записи — здесь раннер, — и до 11.09.2026 в ней
# стояли только модели CDPM: сертификатор и читатель ИДАК в голову не
# попадали вовсе (находка К5). По договору с CDPM 11.09.2026 раннер
# спрашивает среду COSMOS ДО чеканки и кладёт его модели в `models[]`
# словом `system: "cosmos"`; не дождался — слова в `models_note`, а не
# выдуманная раскладка.

#: Ручка среды COSMOS.
COSMOS_ENV_ROUTE = "/api/run/environment"
#: Схема её ответа.
COSMOS_ENV_SCHEMA = "cosmos-run-environment/v1"
#: Переменная контура с адресом COSMOS — ТА ЖЕ, куда контур шлёт пакет
#: постановки и нажатия (`cdpm/channel.py`): второго адреса COSMOS у
#: контура нет, и заводить его ради записи незачем.
COSMOS_URL_ENV = "CREW_CHANNEL_URL"
#: Токен канала COSMOS: машинные ручки записи открыты по нему.
COSMOS_TOKEN_ENV = "CREW_CHANNEL_TOKEN"
#: Срок ответа ручки среды: объявление не имеет права висеть на сети.
COSMOS_ENV_TIMEOUT_S = 10

#: Слова головы, когда среду COSMOS при объявлении спросить не удалось.
MODELS_NOTE_NO_COSMOS = "COSMOS не опрошен при объявлении"
#: Слова головы, когда COSMOS ответил, но моделей не назвал.
MODELS_NOTE_COSMOS_SILENT = "COSMOS не назвал моделей при объявлении"


def planned_stages(a) -> list:
    """План этапов прогона по ключам --from-stage и --until: непройденные
    этапы плана помечаются в журнале исходом «не начат», а не
    отсутствуют."""
    start = a.from_stage or STAGES[0]
    return list(STAGES[STAGES.index(start):STAGES.index(a.until) + 1])


def kit_llm_scope(passport: dict) -> str | None:
    """Допуск моделей НАБОРА по паспорту (`kit.llm_scope`): его знает
    хозяин записи, а не COSMOS, и по нему COSMOS выбирает сертификатора
    и читателя ИДАК. Паспорт допуска не назвал — None: COSMOS возьмёт
    самый узкий (`local-only`), а не самый широкий."""
    kit = (passport or {}).get("kit")
    got = kit.get("llm_scope") if isinstance(kit, dict) else None
    return str(got) if got else None


def cosmos_url(a=None) -> str | None:
    """Адрес COSMOS: ключ `--cosmos-url` СИЛЬНЕЕ переменной контура
    (тот же закон, что у дверей паспорта). Ни ключа, ни переменной —
    None: адрес не выдумывается."""
    got = getattr(a, "cosmos_url", None) or os.environ.get(COSMOS_URL_ENV)
    return (str(got or "").strip().rstrip("/") or None)


def ask_cosmos_environment(a=None, scope: str | None = None,
                           _open=urllib.request.urlopen):
    """Среда COSMOS ручкой `GET /api/run/environment` — ДО чеканки
    головы. Ответ словарём либо None со словами причины в журнале.

    Сеть и отказ COSMOS — НЕ исключение: летопись не имеет права
    отменять прогон. Не ответил — раннер объявляет прогон своими
    моделями и честно пишет в голову `models_note`.

    `scope` — допуск моделей НАБОРА (`manifest.llm_scope`): им COSMOS
    выбирает сертификатора и читателя ИДАК, как при нажатии."""
    base = cosmos_url(a)
    if not base:
        _log(f"среда COSMOS не спрошена: адрес не назван (ключ --cosmos-url "
             f"или переменная {COSMOS_URL_ENV}) — модели COSMOS в голову "
             "записи не попадут")
        return None
    url = base + COSMOS_ENV_ROUTE
    if scope:
        url += "?" + urllib.parse.urlencode({"scope": str(scope)})
    headers = {"Accept": "application/json"}
    token = str(os.environ.get(COSMOS_TOKEN_ENV) or "").strip()
    if token:
        headers["X-Channel-Token"] = token
    try:
        with _open(urllib.request.Request(url, headers=headers, method="GET"),
                   timeout=COSMOS_ENV_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:                                # noqa: BLE001
        _log(f"среда COSMOS не прочитана ({base}{COSMOS_ENV_ROUTE}): {e} — "
             "модели COSMOS в голову записи не попадут")
        return None
    if not isinstance(body, dict) or body.get("schema") != COSMOS_ENV_SCHEMA:
        _log(f"среда COSMOS ответила не по договору (ждали схему "
             f"{COSMOS_ENV_SCHEMA}) — модели COSMOS в голову записи не "
             "попадут")
        return None
    return body


def cosmos_head_models(answer) -> tuple:
    """Модели COSMOS строками ГОЛОВЫ записи и слова о неполноте.

    Поля строки — поля `declaration.models[]` словаря записи, слово
    системы — `cosmos`; всё лишнее (слова причины у неназванной роли)
    отбрасывает сам словарь. Ни одной строки — пустой перечень и слова
    причины: роль не теряется молча."""
    if not isinstance(answer, dict):
        return [], MODELS_NOTE_NO_COSMOS
    rows = []
    for row in (answer.get("models") or []):
        if not isinstance(row, dict) or not str(row.get("role") or "").strip():
            continue
        rows.append({"role": row.get("role"), "model": row.get("model"),
                     "provider": row.get("provider"),
                     "channel": row.get("channel"),
                     "effort": row.get("effort"),
                     "system": rr.SYSTEM_COSMOS})
    return (rows, None) if rows else ([], MODELS_NOTE_COSMOS_SILENT)


def build_record(passport: dict, a, planned: list,
                 panel_code: dict | None = None,
                 cosmos_env: dict | None = None) -> dict:
    """Запись о прогоне из паспорта: голова — те же параметры, названные
    именами записи. Цель и площадку называет человек ключами --purpose и
    --contour; без ключей — «регресс» на площадке «local» (панель в
    процессе), а адреса служб едут в запись из паспорта.

    `panel_code` — блок `code` из предполётной сверки ПАНЕЛИ
    (`GET /api/declaration-preview`): коммит CDPM в голове — это код той
    панели, что ведёт прогон, а не голова дерева раннера. Дерево может
    уйти вперёд записью в ведомость, панель — нет, и её гейт «объявлено /
    на деле» честно отказывает по коммиту (живая находка круга s5,
    10.09.2026 23:56: «объявлен a6bf50de, на деле a78a924d»). Панель
    молчит или стара — коммит из паспорта, как прежде.

    `cosmos_env` — ответ ручки среды COSMOS (`ask_cosmos_environment`):
    его модели ложатся в голову РЯДОМ с моделями CDPM словом
    `system: "cosmos"`. Ответа нет — модели CDPM как прежде, а неполнота
    названа в голове полем `models_note`: молчать о пропавших ролях
    нельзя (договор с CDPM 11.09.2026)."""
    declaration = rr.passport_to_declaration(
        passport, purpose=(a.purpose or PURPOSE_DEFAULT),
        contour=(a.contour or CONTOUR_DEFAULT), created_by=RECORD_ACTOR,
        planned_stages=planned)
    panel_commit = str((panel_code or {}).get("cdpm_commit") or "")
    if panel_commit:
        declaration.setdefault("code", {})["cdpm_commit"] = panel_commit
    models, note = cosmos_head_models(cosmos_env)
    if models:
        declaration["models"] = list(declaration.get("models") or []) + models
    if note:
        declaration["models_note"] = note
    return rr.new_record(declaration)


def record_or_warn(passport: dict, a, planned: list,
                   panel_code: dict | None = None,
                   cosmos_env: dict | None = None):
    """Запись из паспорта; не собралась — предупреждение словами и None.

    Гейт параметров прогона — ПАСПОРТ, и он уже сошёлся; запись ведёт
    летопись. Отказ летописи не имеет права отменять прогон, но и
    молчать о нём нельзя: слова идут в журнал и в отчёт исполнителя."""
    try:
        return build_record(passport, a, planned, panel_code=panel_code,
                            cosmos_env=cosmos_env)
    except rr.RunRecordError as e:                        # noqa: BLE001
        _log("ПРЕДУПРЕЖДЕНИЕ: запись о прогоне не создана — " + str(e))
        return None


def write_record(record: dict, *places) -> list:
    """Запись файлом run-record.json во все названные места (каталог
    --out и папка прогона). Место недоступно — предупреждение словами:
    журнал прогона важнее, чем вторая копия записи."""
    written = []
    for place in places:
        if place is None:
            continue
        try:
            written.append(rr.save(place, record))
        except OSError as e:                              # noqa: BLE001
            _log(f"предупреждение: запись о прогоне не легла в {place}: {e}")
    return written


def declaration_form(record: dict | None) -> dict:
    """Объявление прогона полями формы создания прогона панели
    (раздел 11.1 проекта): панель родит прогон ТОЛЬКО объявленным, а
    голова записи у раннера уже собрана — номер записи чеканится до
    создания прогона, и в форму уезжает именно он.

    Все поля берутся из ГОЛОВЫ записи, а не собираются заново: второй
    сбор параметров разошёлся бы с записью на первом же ключе. Записи
    нет — полей нет: объявлять нечего, и выдумывать номер ради формы
    раннер не станет. Старая панель лишних полей формы не читает —
    прогон идёт как прежде."""
    d = (record or {}).get("declaration") or {}
    if not d.get("run_id"):
        return {}
    exp = d.get("expectations") or {}
    return {
        # Голова записи ЦЕЛИКОМ и её отпечаток: хозяин записи — кто объявил
        # (раннер), хранитель (панель) кладёт присланную голову как есть и
        # считает отпечаток той же функцией словаря — иначе у одного номера
        # две головы, и дописывание раннера получает «объявление разошлось»
        # (живая находка круга 10.09, run-0135). Восемь полей ниже — те же
        # значения короткой строкой для старой панели и формы человека.
        "declaration": json.dumps(d, ensure_ascii=False, sort_keys=True),
        "declaration_sha256": str((record or {}).get("declaration_sha256")
                                  or rr.declaration_sha256(d)),
        "record_run_id": str(d["run_id"]),
        "created_by": str(d.get("created_by") or RECORD_ACTOR),
        "purpose": str(d.get("purpose") or PURPOSE_DEFAULT),
        # Раннер отвечает на анкеты КНИГОЙ ОТВЕТОВ набора, а не рукой
        # человека: политика едет из головы, где она уже названа.
        "answer_policy": str(d.get("answer_policy") or rr.ANSWER_POLICIES[1]),
        "planned_stages": ",".join(str(s) for s in d.get("planned_stages") or []),
        "stop_after": str(d.get("stop_after") or STAGES[-1]),
        "expectations_kind": str(exp.get("kind") or "манифест набора"),
        "expectations_ref": str(exp.get("ref") or ""),
    }


def tree_path(run: str | None = None) -> str | None:
    """Путь ручки дерева прогонов у панели (раздел 12.2 проекта). Имени
    прогона нет — пути нет: спрашивать дерево не о чем."""
    return RUN_TREE_ROUTE.format(run=run) if run else None


def record_path(record_api=None, run: str | None = None) -> str | None:
    """Путь ручки записи о прогоне у панели.

    Манифест пакета несёт `record_api` полным адресом
    («<база панели>/api/runs/<прогон>/record»), но у раннера СВОЯ дверь к
    той же панели — панель в процессе либо ключ --base-url, — и базу он
    берёт свою: из адреса манифеста читается только ПУТЬ. Поля нет —
    путь строится по имени прогона (раздел 10 проекта)."""
    if record_api:
        path = urllib.parse.urlsplit(str(record_api)).path
        if path:
            return path
    return RECORD_ROUTE.format(run=run) if run else None


def keeper_head(body: dict | None, record: dict | None) -> tuple:
    """Голова хранителя из ответа рождения прогона (поле `record`
    {run_id, declaration_sha256}, панель с 10.09.2026) против головы
    раннера: (отпечаток хранителя | None, слова расхождения | None).

    Хранитель, принявший присланную голову как есть (поле declaration
    формы), отчеканит ТОТ ЖЕ отпечаток; хранитель без этого поля
    чеканит свою голову из восьми полей — тогда голов две, и дописывание
    обязано нести отпечаток ХРАНИТЕЛЯ (иначе 409 «объявление
    разошлось»), а расхождение называется словами, не молчит."""
    keeper = ((body or {}).get("record") or {}) if isinstance(body, dict) else {}
    sha = str(keeper.get("declaration_sha256") or "") or None
    mine = str(((record or {}).get("declaration_sha256")) or "")
    if not sha or sha == mine:
        return sha, None
    return sha, (f"хранитель отчеканил СВОЮ голову записи ({sha[:12]}…), не "
                 f"голову раннера ({mine[:12]}…): панель без поля declaration — "
                 "дописывание идёт с отпечатком хранителя, в записи раннера "
                 "(--out) голова своя; две головы на один номер — до правки панели")


def append_body(record: dict, entries=None, results=None,
                declaration_sha256: str | None = None) -> dict:
    """Тело дописывания записи хранителю (раздел 11.2 проекта): схема,
    номер записи, отпечаток ГОЛОВЫ (хранитель сверит его со своей —
    разошлись, значит объявление подменили), система-отправитель и
    записи журнала; итоги — по желанию.

    Ключ идемпотентности у хранителя — (система, этап, подэтап), и
    повтор ключа ЗАМЕНЯЕТ прежнюю запись: поэтому этап шлётся при его
    завершении, в том числе при сбое, а не копится до конца прогона."""
    d = (record or {}).get("declaration") or {}
    body = {"schema": RECORD_APPEND_SCHEMA,
            "record_id": d.get("run_id"),
            "declaration_sha256": (declaration_sha256
                                   or (record or {}).get("declaration_sha256")),
            "system": RECORD_SYSTEM,
            "entries": [e for e in (entries or []) if isinstance(e, dict)]}
    if results:
        body["results"] = results
    return body


#: Этап COSMOS в записи: задача кнопки (так его называет манифест
#: набора) → прочие имена, под которыми хозяин мог его записать.
#: Основные имена берутся у словаря (`rr.COSMOS_STAGES`), здесь —
#: ростер, посчитанный на выбранном пейринге.
#: Имена этапов COSMOS в записи хозяина сверх слова словаря
#: (`rr.COSMOS_STAGES`): ростер, построенный на утверждённом пейринге, и
#: сверка с исходными документами — у неё в словаре записи нет задачи
#: кнопки, этап называется словами (живая находка круга 10.09, run-0141).
RECORD_STAGE_ALIASES = {"rostering": ("ростер на пейринге",),
                        "source-check": ("сверка с ИДАК",)}


def record_stage_names(key: str) -> set:
    """Имена, под которыми этап COSMOS мог лечь в журнал хозяина записи:
    сама задача кнопки (`pairing`, `rostering`, `source-check`) и её
    слово из словаря записи. Судится сшивка, а не то, каким из имён
    хозяин назвал этап."""
    names = {str(key)}
    word = rr.COSMOS_STAGES.get(str(key))
    if word:
        names.add(word)
    names.update(RECORD_STAGE_ALIASES.get(str(key), ()))
    return names


def record_journal_rows(record) -> list:
    """Строки журнала записи парами (система, запись). Чужие записи
    хозяин метит именем системы — ключом `system` у самой записи либо
    обёрткой `{system, entry}` (раздел 11.2 проекта: принятое ложится
    строками `{received_at, system, key, entry}`). Читаются оба вида:
    раннер судит сшивку, а не раскладку хозяина."""
    rows = []
    for item in (record or {}).get("journal") or []:
        if not isinstance(item, dict):
            continue
        inner = item.get("entry")
        rows.append((str(item.get("system") or ""),
                     inner if isinstance(inner, dict) else item))
    return rows


def record_stitched_problems(exp: dict, record) -> list:
    """Ожидание `record_stitched` — «запись сшита»: у ХОЗЯИНА записи
    стоят этапы COSMOS по каждой ожидаемой задаче (раздел 10 проекта,
    пункт 5). Ожидание — `{"cosmos_stages": [задача, …]}`.

    Судится ТОЛЬКО когда нажатия COSMOS делались (машинный отчёт
    нажатий, ключ --cosmos-press): без нажатий этапов COSMOS в записи и
    быть не должно, красная строка была бы неправдой — ключ тогда честно
    уезжает в «не судится раннером», как `cosmos_numbers`."""
    want = exp.get("record_stitched")
    if not isinstance(want, dict):
        return []
    stages = [str(s) for s in (want.get("cosmos_stages") or [])]
    if not stages:
        return []
    named = ", ".join(stages)
    if not isinstance(record, dict):
        return ["сшивка записи: единой записи у хозяина нет — ручка записи "
                f"не ответила либо записи по этому прогону не заведено "
                f"(ожидались этапы COSMOS: {named})"]
    rows = [e for system, e in record_journal_rows(record)
            if system == RECORD_SYSTEM_COSMOS]
    if not rows:
        return ["сшивка записи: в журнале записи нет ни одной строки системы "
                f"«{RECORD_SYSTEM_COSMOS}» — этапы нажатий хозяину не пришли "
                f"(ожидались: {named})"]
    missing = [s for s in stages
               if not any(str(e.get("stage")) in record_stage_names(s)
                          for e in rows)]
    if missing:
        got = ", ".join(sorted({str(e.get("stage")) for e in rows}))
        return [f"сшивка записи: у хозяина нет этапов COSMOS: "
                f"{', '.join(missing)} (в журнале системы "
                f"«{RECORD_SYSTEM_COSMOS}» стоят: {got})"]
    return []


def golden_signature_missing(golden: dict) -> list:
    """Чего не хватает подписи золотого прогона: кто подтвердил, когда и
    какими словами. Тот же состав, что судит словарь записи
    (`rr.validate`), — раннер не заводит своей мерки."""
    names = {"by": "кто подтвердил (by)", "at": "когда (at)",
             "words": "слова владельца (words)"}
    return [names[key] for key in ("by", "at", "words")
            if not str((golden or {}).get(key) or "").strip()]


def golden_by_owner_only_problems(exp: dict, record, mine=None) -> list:
    """Ожидание `golden_by_owner_only` — «золотым прогон называет ТОЛЬКО
    владелец» (решение владельца 6.3 от 10.09.2026: после приёмки на
    Prod система спрашивает, и признак ставится по его подтверждению).

    Судятся две вещи: раннер признак не ставит НИКОГДА (своя запись,
    `mine`) и у ХОЗЯИНА записи признак не стоит без подписи (`record`) —
    «золотой» без слов владельца был бы признаком, поставленным машиной.

    Как и `record_stitched`, судится ТОЛЬКО когда нажатия COSMOS
    делались (машинный отчёт нажатий, ключ --cosmos-press): без нажатий
    прогон приёмки не проходил, спрашивать владельца было не о чем, и
    красная строка была бы неправдой."""
    want = exp.get("golden_by_owner_only")
    if not isinstance(want, dict):
        return []
    problems: list = []
    my_golden = ((mine or {}).get("results") or {}).get("golden") or {} \
        if isinstance(mine, dict) else {}
    if my_golden.get("confirmed") and not want.get("runner_sets_golden"):
        problems.append("признак золотого: его поставил РАННЕР — прогон-"
                        "образец называет только владелец словом после "
                        "приёмки на Prod, машина этого не решает")
    if not isinstance(record, dict):
        return problems + ["признак золотого: единой записи у хозяина нет — "
                           "ручка записи не ответила либо записи по этому "
                           "прогону не заведено (проверить признак нечем)"]
    golden = ((record.get("results") or {}).get("golden")) or {}
    if not golden.get("confirmed"):
        return problems
    missing = golden_signature_missing(golden)
    if missing:
        problems.append("признак золотого у хозяина записи стоит БЕЗ подписи: "
                        f"не сказано {', '.join(missing)} — признак ставится "
                        "только словом владельца после приёмки на Prod")
    return problems


def comparison_expectations(declaration, expect) -> dict:
    """Сравнение с ожиданиями набора для ИТОГОВ записи (раздел 3.3
    проекта): ровно тот суд, что прошёл на стадии package
    (`summary["expect"]`), названный полями записи. Второго суда нет —
    источник правды один, иначе итоги и журнал разошлись бы словами.

    Вердикт различает три вещи, как и строка журнала: расхождения есть —
    «не выполнены»; расхождений нет и судимые ключи названы —
    «выполнены»; судить было нечего (ключа `expect` у набора нет либо
    прогон встал раньше суда) — «не судились», а не «выполнены»:
    молчание ожиданием не бывает."""
    d = (declaration or {}).get("expectations") or {} \
        if isinstance(declaration, dict) else {}
    out = {"kind": str(d.get("kind") or EXPECTATIONS_KIND),
           "ref": str(d.get("ref") or ""),
           "checked": [], "not_judged": {}, "problems": [], "warnings": [],
           "verdict": EXPECT_VERDICT_NONE}
    if not isinstance(expect, dict):
        return out
    out["checked"] = [str(k) for k in (expect.get("checked") or [])]
    out["not_judged"] = {str(k): str(v) for k, v
                         in (expect.get("not_judged") or {}).items()}
    out["problems"] = [str(p) for p in (expect.get("problems") or [])]
    out["warnings"] = [str(w) for w in (expect.get("warnings") or [])]
    if out["problems"]:
        out["verdict"] = EXPECT_VERDICT_BAD
    elif out["checked"]:
        out["verdict"] = EXPECT_VERDICT_OK
    return out


def comparison_row(cmp) -> dict:
    """Строка сравнения в итогах записи: С КЕМ сравнивали и слова
    сравнения (раздел 12.4 проекта — `{run_id, words[]}`; сами сравнения
    считает `vmp/run_tree.py`, карточка показывает их без сборки
    дерева). Сравнения нет — пусто."""
    if not isinstance(cmp, dict):
        return {}
    run_id = cmp.get("a")
    return {"run_id": str(run_id) if run_id else None,
            "words": [str(w) for w in (cmp.get("words") or [])]}


def comparison_from_tree(tree) -> dict:
    """Сравнения с предыдущим прогоном того же корня и с золотым — из
    ответа ручки дерева панели (`compare_previous` и `compare_golden`,
    раздел 12.2). Ручки ещё нет, она промолчала или сравнивать не с чем
    — ПУСТО: сравнение, которого не было, раннер не выдумывает."""
    if not isinstance(tree, dict):
        return {"previous_run": {}, "golden": {}}
    return {"previous_run": comparison_row(tree.get("compare_previous")),
            "golden": comparison_row(tree.get("compare_golden"))}


def stage_counters(state: dict, stage: str) -> dict:
    """Счётчики стадии из состояния раннера: все числа блока стадии (и
    блока `<стадия>_status`) под их собственными именами — так, как их
    называют носители систем (раздел 3.2 проекта)."""
    out: dict = {}

    def walk(block):
        for key, value in (block or {}).items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                out.setdefault(str(key), value)
            elif isinstance(value, dict):
                walk(value)

    for name in (stage, f"{stage}_status"):
        block = (state.get("stages") or {}).get(name)
        if isinstance(block, dict):
            walk(block)
    return out


def stage_artifacts(state: dict, stage: str) -> list:
    """Артефакты стадии ссылками (не копиями): папка прогона CDPM после
    заморозки и пакет постановки после выпуска."""
    out: list = []
    if stage == STAGES[0] and state.get("run"):
        out.append({"kind": "прогон CDPM", "path": str(state["run"]),
                    "sha256": None})
    pkg = ((state.get("stages") or {}).get("package_status") or {}).get("path")
    if stage == "package" and pkg:
        out.append({"kind": "пакет постановки", "path": str(pkg),
                    "sha256": None})
    return out


def stage_outcome(state: dict, stage: str) -> tuple:
    """Исход стадии по состоянию раннера: (исход, код причины, слова).

    Отказ службы РАЗДЕЛЯЕТСЯ по существу, а не по факту неудачи: 409 на
    выпуске пакета — «пробел покрытия», то есть штатный отказ ПО
    ПРАВИЛУ (система остановилась сама и назвала причину), любой другой
    ответ 4xx — тоже отказ по правилу с кодом ответа, а 5xx —
    поломка службы, то есть сбой. Прогон, остановленный правилом, —
    пройденный прогон с исходом, а не провал.

    Отказ с КОДОМ в теле (гейт непротиворечивости CDPM, 11.09.2026)
    называется своим кодом и своими словами: 409 стал не единственной
    причиной — «пробел покрытия» у противоречия правил был бы
    неправдой, и запись о прогоне лгала бы вслед за ним.

    СТАДИИ `cycle` И `answers` судятся НЕ ответом службы, а законом
    реестра: ответа с кодом состояния у цикла в блоке нет вовсе, и
    прежний суд «кода нет — значит успех» давал записи о прогоне
    «успех» и при отказе чтения реестра, и при живой блокирующей
    находке (замечание С1 первого неавторского ревью). Смысловой исход
    обеих называет `cycle_outcome` по блоку `cycle_status` — у цикла
    своему (`stages.cycle_status`), у ответов ПЕРЕСУЖЕННОМУ
    (`stages.answers.cycle_status`: заявка исхода — у самого цикла,
    реестр — свежий, после кругов ответов).

    ИСХОД СТАДИИ — ПРАВДА О ЕЁ МОМЕНТЕ, И ОН НЕ ПЕРЕПИСЫВАЕТСЯ: исход
    стадии `cycle` остаётся в журнале как записан, что бы ни показал
    пересуд. Итог ПРОГОНА считает словарь записи по всем записям
    журнала (`rr.state_from_journal`, `Runner.finish_record`), а суд о
    ТЕКУЩЕЙ чистоте несут ожидания набора (`cycle_clean_problems`, по
    последнему блоку) — своего отбора записей у раннера нет.

    НАСТОЯЩИЙ СБОЙ СЛУЖБЫ СТАРШЕ СМЫСЛА (квитанция аудитора
    A07RUN-REVIEW-RECEIPT-20260912-GPT, регрессия 8): целочисленный
    код ответа вне 2xx/3xx разбирается ОБЩИМ правилом ниже — 5xx
    остаётся «сбоем», 4xx отказом по правилу с кодом ответа, — и
    смысловой суд до него не доходит. Поломку службы нельзя выдавать
    за штатный отказ по правилу: «служба сломалась» и «система
    остановилась сама и назвала причину» — разные исходы.

    ПОРЯДОК СУДА У СТАДИИ `cycle` (и у пересуда `answers`) — от самого
    старшего исхода к самому младшему, и каждый ярус называет СВОЙ
    класс словаря записи:

    1. настоящий транспортный ответ службы вне 2xx/3xx — общее правило
       ниже: 5xx «сбой», 4xx «отказ по правилу» кодом ответа;
    2. ОСТАНОВЛЕН ЧЕЛОВЕКОМ — предикат закона
       `cdpm.exchange.stopped_by_human` внутри `cycle_outcome`
       (`stopped-in-panel`, `rr.OUTCOME_HUMAN`);
    3. смысловой суд закона реестра: `registry-refusal`,
       `not-clean-cycle`, `cycle-incomplete`, предупреждение
       `live-non-blocking`;
    4. ничего из этого — «успех» общим правилом.

    Порядок 2 перед 3 зелёного не прибавляет: «остановлен человеком»
    старше «остановлен по правилу» и в словаре итога
    (`rr.state_from_journal`), и в ожиданиях набора
    (`cycle_clean_problems` красит оба)."""
    status = ((state.get("stages") or {}).get(f"{stage}_status") or {})
    code = status.get("status")
    broken = (isinstance(code, int) and not isinstance(code, bool)
              and not 200 <= code < 400)
    if not broken:
        cyc = cycle_outcome(cycle_block_of(state, stage))
        if cyc is not None:
            return cyc
    if not isinstance(code, int) or 200 <= code < 400:
        return rr.OUTCOME_OK, None, None
    words = str(status.get("error") or "")[:300]
    refusal_code = status.get("code")
    if isinstance(refusal_code, str) and refusal_code.strip():
        return (rr.OUTCOME_RULE, refusal_code.strip(),
                str(status.get("words") or words)[:300])
    if code == 409:
        return (rr.OUTCOME_RULE, "coverage-gap",
                "пакет не выдан: пробел покрытия — правило не ложится ни на "
                "один шаблон каталога" + (f"; {words}" if words else ""))
    if code < 500:
        return rr.OUTCOME_RULE, f"http-{code}", words or f"служба отказала ({code})"
    return rr.OUTCOME_FAILURE, f"http-{code}", words or f"служба сломалась ({code})"


def _finding_row(finding: dict) -> tuple:
    """Находка реестра строкой журнала и состояния: (id, серьёзность,
    состояние) — ровно так, как раннер называл её до A07."""
    return (finding.get("id"), finding.get("severity"), finding.get("state"))


#: Состояние прогона, которым СКВОЗНОЙ ЦИКЛ заявляет законченный исход,
#: судимый затем реестром находок («сквозной цикл: чисто по Б/К
#: (кругов: N)» с необязательным хвостом «; серьёзных в бэклоге: N» —
#: `cdpm/webui.py`, нить сквозного цикла; сверка по началу строки).
#: Строка одна: она и до правок A07 стояла в коде раннера, правка её
#: лишь НАЗЫВАЕТ.
#:
#: Это НЕ второй перечень закона находок (`cdpm.exchange.LIVE_STATES`,
#: `SEVERITIES`) и не свой суд о чистоте: чистоту по Б/К судит реестр
#: одним загрузчиком продукта, а здесь названо лишь то, ЗАЯВЛЕН ли
#: исход вообще. Состояние, исхода не заявляющее («сквозной цикл: лимит
#: кругов (N)», «красный гейт аудит-цикла», «красный гейт арбитража»,
#: «остановлено: сквозной цикл», «отказ сквозного цикла»), чистоты не
#: подтверждает — и это отдельный код `cycle-incomplete`, а не «живой
#: блокер».
#:
#: Состояния стадии ОТВЕТОВ здесь нет намеренно (замечание С2 второго
#: неавторского ревью): «ответы применены» о цикле не утверждает
#: ничего, и пересуд после ответов берёт заявку у состояния САМОГО
#: цикла (`stages.cycle_status.state`), а не у состояния после ответов.
#:
#: ЗАМЕНИТЬ ПРЕДИКАТОМ ЗАКОНА `cycle_clean_claimed(state)`, КОГДА CDPM
#: ЕГО ДАСТ. Предиката «заявил ЧИСТОТУ» закон пока не даёт, а
#: появившийся `cdpm.exchange.outcome_claimed(state)` (A07-R5) сюда не
#: годится: он ШИРЕ — истина и для «остановлен по Б/К», и для
#: «аудит-цикл: успех», и для «арбитраж готов», а ими чистота по Б/К не
#: заявляется. Взять его здесь значило бы объявить чистым остановленный
#: по блокирующим прогон. Поэтому литерал ОДИН и остаётся до предиката;
#: просьба к CDPM записана в отчёте единицы, а сторож ниже сверяет
#: строку с исходником панели (`tests/test_cosmos_runner_cycle_outcome.py`,
#: `test_незаявляющие_финалы_взяты_у_панели`). Требование ФАЙЛА реестра
#: этой строкой НЕ судится — его судит закон (`registry_expected`).
OUTCOME_CLAIMED_STATE = "сквозной цикл: чисто по Б/К"

#: Состояние прогона, которым панель заканчивает УСПЕШНЫЙ круг ответов
#: (`cdpm/webui.py`, стадия применения ответов). Заявкой исхода ЦИКЛА
#: оно не является (см. выше) — по нему `Runner.stage_answers` судит
#: только свой круг: иное состояние круга («красный гейт ответов»,
#: «остановлено: применение ответов», «отказ применения ответов») —
#: стоп прохода. Одна константа на всё место употребления: придёт
#: предикат закона — заменять будет нечего, кроме этой строки.
#:
#: ЗАМЕНИТЬ ПРЕДИКАТОМ ЗАКОНА, КОГДА CDPM ЕГО ДАСТ — тем же порядком,
#: что и `OUTCOME_CLAIMED_STATE` выше: здесь нужен предикат «круг
#: ответов закончился успешно», а его у закона пока нет (`outcome_claimed`
#: об этом состоянии говорит лишь то, что исхода ЦИКЛА оно не заявляет,
#: и на вопрос об успехе круга ответов не отвечает вовсе).
ANSWERS_APPLIED_STATE = "ответы применены"


def cycle_status_of(exchange_dir, state) -> dict:
    """Исход сквозного цикла по ПАПКЕ ОБМЕНА прогона и состоянию,
    которым его назвал конвейер. Чистая функция (выделена из
    `stage_cycle` без смены поведения стадии) — она же под тестом.

    Зовётся ДВАЖДЫ за прогон, и ОБА раза состоянием САМОГО ЦИКЛА:
    стадией `cycle` — тем, которым цикл только что кончился; стадией
    `answers` ПОСЛЕ кругов ответов — тем же самым, записанным в
    `stages.cycle_status.state`, но по СВЕЖЕМУ реестру. Меняется между
    двумя вызовами только реестр: ответы законно разрешают прежние
    находки (и могут завести новые), а о самом цикле после ответов не
    становится известно ничего нового — состояние «ответы применены»
    говорит об анкете, а не о цикле (замечание С2 второго неавторского
    ревью REVIEW2-A07RUN-e67bef1c-COSMOS: заявка чистоты, взятая у
    состояния после ответов, делала «завершённым» прогон с красным
    гейтом цикла, остановкой и отказом стадии). Историю это не
    переписывает: блоки лежат врозь (`stages.cycle_status` и
    `stages.answers.cycle_status`).

    A07, §1.5 «потребитель» контракта П0 (первоисточник требования
    «панель, одиночный цикл, сквозной цикл и раннер H0 читают одну
    функцию»; вердикты аудитора VERDICT-A07-ef0ac6f9 и действующий
    VERDICT-A07-d9343ead): раннер — ПОТРЕБИТЕЛЬ закона живых находок,
    а не второй его автор.
    Собственного перечня состояний он не держит: живые находки даёт
    `cdpm.exchange.live_findings(reg)` (состояние из
    `exchange.LIVE_STATES`, без closed_round, ключ не решён арбитром),
    блокирующие — `cdpm.exchange.registry_blockers(reg)` (из живых те,
    чья серьёзность БЛОКИРУЮЩЕЕ или КРИТИЧЕСКОЕ). Тот же предикат
    читают панель (`webui._live_findings`), цикл и гейт: раннер обязан
    быть с ними СОГЛАСЕН — не строже и не мягче. Одно расхождение
    названо честно: на ошибке ВВОДА-ВЫВОДА (носитель не читается)
    раннер закрывается словами отказа, а панель
    (`webui._state_by_registry`) до исправления R3 на стороне CDPM —
    нет, она показывает записанное состояние; расхождение в безопасную
    сторону, и с исправлением R3 стороны сойдутся. Прежний перечень
    раннера («открыто», «патч отбракован», «тупик») не знал «отклонено
    кодом» и «не разрешено арбитром» (мягче закона) и при этом красил
    прогон любой живой находкой, включая редакционную (строже панели,
    которая считает «чисто по Б/К» через registry_blockers).

    ЧИТАЕТ ОДИН ЗАГРУЗЧИК (A07-F4): реестр берётся
    `cdpm.exchange.load_registry(путь, expect_file=…)` — тем же,
    которым читают цикл, панель, арбитр и гейты. Своего `json.loads` у
    раннера больше нет, и версию он не толкует: нет поля версии —
    старая форма, как её читает загрузчик; чужая версия, неполный
    `registry/2` и негодная форма поля `findings` — ОТКАЗ словами.
    Миграцию чтения (A07-F3: унаследованная отбраковка замком кода —
    живая) загрузчик и `live_findings` применяют сами; файл на диске
    чтением не переписывается.

    ОБЯЗАН ЛИ ФАЙЛ РЕЕСТРА БЫТЬ — РЕШАЕТ ЗАКОН, А НЕ РАННЕР (A07-R5,
    `cdpm.exchange.registry_expected(exdir, state)`; ведомость
    CDPM-122). Реестр пишет КАЖДЫЙ круг цикла и переписывает арбитраж,
    поэтому «цикл отработал, а файла нет» — не новый прогон, а
    ИСЧЕЗНУВШИЙ носитель, и прежнее чтение такого прогона пустым
    реестром работало доказательством чистоты. Закон из двух частей,
    «или»: СЛОВО состояния (`outcome_claimed`) либо КАТАЛОГ обмена
    (`cycle_ran` — в папке лежит вердикт цикла `verdict.json`). Раннеру
    нужны обе: пересуд после ответов идёт ЗАЯВКОЙ САМОГО ЦИКЛА, но
    состояние «сквозной цикл: …» при этом уже сменилось, а каталог
    вердикт хранит. Вызов — ровно одной строкой, парой с загрузчиком:

        reg = ex_mod.load_registry(
            reg_path,
            expect_file=ex_mod.registry_expected(reg_path.parent, state))

    Требование не выполнено — загрузчик отказывает СВОИМИ СЛОВАМИ
    («реестр отсутствует после исполненного цикла: …»), и отказ идёт
    общей веткой ниже: цикл НЕ чист, `registry_refused` = 1. Ни слова,
    ни каталога — требования нет, и действительно новый прогон (до
    цикла, без вердикта в папке) читает пустой реестр, как и прежде:
    живых нет, чистоту судит состояние.

    Выдача — она же блок `cycle_status` состояния раннера:

    - `state` — состояние прогона, как его назвал конвейер;
    - `clean` — состояние ЗАЯВЛЯЕТ исход (`OUTCOME_CLAIMED_STATE`) И
      перечень блокирующих пуст;
    - `live_findings` — живые находки (id, серьёзность, состояние);
    - `blockers` — блокирующие из них, тем же кортежем;
    - `registry_schema` — `schema_version` из реестра, КАК ЕГО ОТДАЛ
      загрузчик (старая форма без поля и отсутствие файла — None);
    - `registry_refusal` — слова отказа; ключ появляется ТОЛЬКО когда
      реестр раннеру не читаем;
    - `live_n`, `blockers_n`, `registry_refused` — те же три величины
      ЦЕЛЫМИ ЧИСЛАМИ (последняя 0/1). Перечни и строки в счётчики
      этапа записи о прогоне не попадают (`stage_counters` берёт
      только числа и намеренно пропускает `bool`), и до этой правки
      запись о прогоне несла от стадии лишь `rounds` и `sec` —
      блокирующие и отказ в ней не были видны вовсе (замечание С1).

    Версию реестра судит не раннер: он не «исправляет» старую форму и
    не гадает. Откажет закон (`ExchangeError` загрузчика — версия,
    форма, нечитаемый JSON — либо ошибка ввода-вывода) — раннер
    называет отказ ЕГО СЛОВАМИ и считает цикл НЕ чистым (fail-closed),
    а не молча зелёным. Ветка отказа ОДНА (`except Exception`) и
    закрывает всё, что бросает закон, а вместе с ним и негодный
    аргумент: путь реестра и импорт закона строятся ВНУТРИ `try`,
    поэтому `exchange_dir` не путём (None, число) — тоже отказ
    словами, а не падение стадии (замечание Н1 ревью)."""
    clean_state = str(state or "").startswith(OUTCOME_CLAIMED_STATE)
    out: dict = {"state": state, "clean": clean_state, "live_findings": [],
                 "blockers": [], "registry_schema": None,
                 "live_n": 0, "blockers_n": 0, "registry_refused": 0}
    try:
        from cdpm import exchange as ex_mod
        reg_path = Path(exchange_dir) / "registry.json"
        reg = ex_mod.load_registry(
            reg_path,
            expect_file=ex_mod.registry_expected(reg_path.parent, state))
        live = ex_mod.live_findings(reg)
        blockers = ex_mod.registry_blockers(reg)
    except Exception as e:                                   # noqa: BLE001
        # Отказал ЗАКОН, а не раннер: своей трактовки у раннера нет —
        # его слова и переносятся в запись (fail-closed). Числа при
        # отказе остаются нулями, а `registry_refused` = 1: пустой
        # перечень живых при нечитаемом реестре — не «чисто», и
        # счётчик отказа не даёт прочесть нули как чистоту.
        out["registry_refusal"] = f"реестр раннеру не читаем: {e}"
        out["registry_refused"] = 1
        out["clean"] = False
        return out
    out["registry_schema"] = reg.get("schema_version")
    out["live_findings"] = [_finding_row(f) for f in live]
    out["blockers"] = [_finding_row(f) for f in blockers]
    out["live_n"] = len(out["live_findings"])
    out["blockers_n"] = len(out["blockers"])
    out["clean"] = clean_state and not out["blockers"]
    return out


#: Отказы стадии сквозного цикла, у которых есть КОД причины записи о
#: прогоне: код → чем он является. Устроены как коды отказа выпуска
#: пакета (`PACKAGE_REFUSAL_CODES`): машинное имя рядом со словами
#: человеку. Разница в источнике: там код называет служба ответом, тут
#: — сам закон реестра, и выдумывать ему код не приходится.
CYCLE_REFUSAL_CODES = {
    "registry-refusal": ("реестр находок раннеру не читаем — чистота "
                         "цикла НЕ подтверждена"),
    "not-clean-cycle": ("сквозной цикл не чист: живые блокирующие "
                        "находки реестра"),
    "cycle-incomplete": ("чистота цикла не подтверждена: конвейер "
                         "исхода не заявил"),
}

#: Предупреждения стадии сквозного цикла: код → чем он является.
#: Отдельно от отказов, потому что это ДРУГОЙ исход словаря записи
#: («предупреждение», не «отказ по правилу») — прогон им не краснеет.
CYCLE_WARNING_CODES = {
    "live-non-blocking": ("цикл чист по Б/К, но живые неблокирующие "
                          "находки реестра есть"),
}

#: Остановка ЧЕЛОВЕКОМ на стадии сквозного цикла: код → чем он
#: является. Третий перечень и третий исход словаря записи
#: («остановлен человеком», `rr.OUTCOME_HUMAN`) — не отказ по правилу
#: («система остановилась сама и назвала причину») и не предупреждение.
#: Остаток, названный в отчёте прошлой ревизии, закрыт: признак даёт
#: ПРЕДИКАТ ЗАКОНА (`cdpm.exchange.stopped_by_human`), а не литерал
#: состояния — своего перечня состояний раннер по-прежнему не держит.
CYCLE_HUMAN_CODES = {
    "stopped-in-panel": ("прогон остановлен человеком в панели: кнопка "
                         "«Остановить» посреди стадии"),
}


def _stopped_by_human(state) -> bool:
    """ОСТАНОВЛЕН ЛИ ПРОГОН ЧЕЛОВЕКОМ — по предикату закона
    (`cdpm.exchange.stopped_by_human`, семейство «остановлено:
    <стадия>»), а не по литералу панели: перечня состояний прогона
    раннер не держит и заводить не будет.

    Закон недостижим (импорт не удался) — отвечаем «нет»: тогда
    состояние судит смысловой суд ниже и даёт «отказ по правилу»
    (`cycle-incomplete`), то есть краснее, а не зеленее. Зелёного эта
    ветка не даёт ни при каком исходе импорта."""
    try:
        from cdpm import exchange as ex_mod
        return bool(ex_mod.stopped_by_human(state))
    except Exception:                                        # noqa: BLE001
        return False


def _cycle_n(block, count_key: str, rows_key: str) -> int:
    """Число блока `cycle_status` по двум свидетельствам сразу: целому
    счётчику и длине перечня. Красит ЛЮБОЕ из них: счётчик потерялся
    (блок собран не раннером, старая запись) — судит перечень, перечень
    не приехал — судит счётчик. `bool` за число не принимается —
    `True == 1` не находка."""
    n = (block or {}).get(count_key)
    n = n if isinstance(n, int) and not isinstance(n, bool) else 0
    rows = (block or {}).get(rows_key)
    if isinstance(rows, (list, tuple)):
        n = max(n, len(rows))
    return n


def cycle_block_of(state: dict, stage: str):
    """Блок `cycle_status`, которым судится СМЫСЛ стадии: у `cycle` —
    свой (`stages.cycle_status`), у `answers` — ПЕРЕСУЖЕННЫЙ после
    кругов ответов (`stages.answers.cycle_status`). У прочих стадий
    смыслового блока нет — None, и общее правило судит их как прежде.

    Пересуд лежит ВНУТРИ сводки стадии ответов, а не поверх блока
    цикла: прежний исход не переписывается, и в журнале остаются оба —
    что было на цикле и что стало после ответов."""
    stages = (state.get("stages") or {}) if isinstance(state, dict) else {}
    if stage == "cycle":
        return stages.get("cycle_status")
    if stage == "answers":
        block = stages.get("answers")
        return block.get("cycle_status") if isinstance(block, dict) else None
    return None


def cycle_outcome(block) -> tuple | None:
    """Смысловой исход стадии по блоку `cycle_status`: (исход, код
    причины, слова) — либо None, когда судить нечего.

    Стадия `cycle` ответа службы с кодом состояния в блоке не держит, и
    общий суд `stage_outcome` («кода нет — успех») давал ей «успех» и
    при отказе чтения реестра, и при живой блокирующей находке: запись
    о прогоне выходила зелёной там, где вердикт гейта уже нёс пометку
    NOT_CLEAN_CYCLE (замечание С1 первого неавторского ревью
    REVIEW-A07RUN-856f588c-COSMOS). Тем же судом судится ПЕРЕСУД после
    ответов (`stages.answers.cycle_status`) — один закон на оба места;
    разница только в реестре, заявка исхода у обоих одна и та же —
    состояние самого цикла (замечание С2 второго ревью).

    Пять случаев, и три из них — «отказ по правилу» (слово из словаря
    исходов записи, `vmp/run_record.py`; своих исходов раннер не
    заводит): система остановилась сама и назвала причину, а не
    сломалась.

    * `stopped-in-panel` — прогон ОСТАНОВЛЕН ЧЕЛОВЕКОМ (`rr.OUTCOME_HUMAN`).
      Судится ПЕРВЫМ после настоящего сбоя службы и по ПРЕДИКАТУ ЗАКОНА
      (`cdpm.exchange.stopped_by_human`: семейство «остановлено:
      <стадия>» — кнопка «Остановить» панели), а не по литералу
      состояния. Прежде такой прогон звался «отказ по правилу»
      (`cycle-incomplete`) — классом ниже настоящего: «система
      остановилась сама и назвала причину» и «человек нажал кнопку» —
      разные вещи, и словарь записи различает их сам (итог «остановлен
      человеком» старше «остановлен по правилу», `rr.state_from_journal`).
      Отказ чтения реестра он не прячет: чистоты остановленный прогон не
      заявляет НИКОГДА (`outcome_claimed` на этом семействе ложь), и
      ожидания набора краснеют на нём так же, как на отказе;
    * `registry-refusal` — закон отказался читать реестр (чужая версия,
      негодная форма, нечитаемый JSON, ошибка ввода-вывода). Чистота
      НЕ подтверждена, и неизвестность не заменяется успехом. Судится
      ДВУМЯ свидетельствами, как и находки: строкой `registry_refusal`
      и целым счётчиком `registry_refused` — потерялась строка (блок
      собран не раннером, старая запись), судит счётчик, и отказ
      называется «отказ без слов» (замечание Н2 второго неавторского
      ревью: одно свидетельство на отказ — меньше, чем на находки);
    * `not-clean-cycle` — реестр прочтён, живые БЛОКИРУЮЩИЕ есть
      (счётчик `blockers_n` либо непустой перечень `blockers`);
    * `cycle-incomplete` — блокирующих нет, но `clean` не истинно:
      состояние цикла исхода НЕ ЗАЯВИЛО (лимит кругов, красный гейт
      аудит-цикла или арбитража, остановка человеком, отказ стадии
      панели). Отдельный код, а не «не чистый цикл»: «нечего
      предъявить» и «предъявлен живой блокер» — разные вещи
      (квитанция аудитора: правдивый не чистый исход БЕЗ ложного
      требования вердикта цикла);
    * `live-non-blocking` — ПРЕДУПРЕЖДЕНИЕ (`rr.OUTCOME_WARN`), а не
      краснота: чисто по Б/К, но живые неблокирующие (серьёзные,
      редакционные) есть. Их дорога — бэклог панели; прогон ими не
      краснеет, но и молчать о них запись не должна.

    Блока нет, он не словарь, в нём нет ни признака чистоты, ни отказа,
    ни чисел (стадия этим прогоном не шла) — None: исход судит общее
    правило, и выдумывать отказ не начатой стадии нельзя. Чистый цикл
    без живых находок — тоже None: исход «успех» ставит
    `stage_outcome`."""
    if not isinstance(block, dict):
        return None
    # ОСТАНОВКА ЧЕЛОВЕКОМ — РАНЬШЕ СМЫСЛОВОГО СУДА, но позже настоящего
    # сбоя службы (его отбирает `stage_outcome` выше): прогон, который
    # человек остановил кнопкой, не «остановился сам и назвал причину».
    # Класс исхода при этом СТАРШЕ отказа по правилу в словаре записи,
    # так что зелёного эта ветка не добавляет ни в одном случае.
    if _stopped_by_human(block.get("state")):
        return (rr.OUTCOME_HUMAN, "stopped-in-panel",
                f"{CYCLE_HUMAN_CODES['stopped-in-panel']}: состояние прогона "
                f"{block.get('state')!r}"[:300])
    refusal = block.get("registry_refusal")
    if isinstance(refusal, str) and refusal.strip():
        return (rr.OUTCOME_RULE, "registry-refusal",
                f"{CYCLE_REFUSAL_CODES['registry-refusal']}: "
                f"{refusal.strip()}"[:300])
    # Отказ судится и СЧЁТЧИКОМ (замечание Н2): строки нет, а
    # `registry_refused` = 1 — отказ всё равно отказ, и молчаливым
    # успехом он не становится.
    refused = block.get("registry_refused")
    if isinstance(refused, int) and not isinstance(refused, bool) and refused:
        return (rr.OUTCOME_RULE, "registry-refusal",
                f"{CYCLE_REFUSAL_CODES['registry-refusal']}: отказ без слов "
                f"(счётчик registry_refused = {refused})"[:300])
    blockers_n = _cycle_n(block, "blockers_n", "blockers")
    if blockers_n:
        return (rr.OUTCOME_RULE, "not-clean-cycle",
                f"{CYCLE_REFUSAL_CODES['not-clean-cycle']}: состояние прогона "
                f"{block.get('state')!r}, блокирующих "
                f"{blockers_n}"[:300])
    if "clean" not in block:
        return None
    if not block.get("clean"):
        return (rr.OUTCOME_RULE, "cycle-incomplete",
                f"{CYCLE_REFUSAL_CODES['cycle-incomplete']}: состояние прогона "
                f"{block.get('state')!r}"[:300])
    live_n = _cycle_n(block, "live_n", "live_findings")
    if live_n:
        return (rr.OUTCOME_WARN, "live-non-blocking",
                f"{CYCLE_WARNING_CODES['live-non-blocking']}: живых "
                f"неблокирующих находок {live_n} — дорога бэклога панели, "
                f"не краснота"[:300])
    return None


def cycle_clean_problems(stages) -> list[str]:
    """Не чистый сквозной цикл — расхождение ожиданий ЛЮБОГО набора.

    Чистота цикла — ЗАКОН реестра, а не свойство набора: объявлять её
    в манифесте нечего, и набор, забывший объявить, не становится
    зелёным (замечание С1, пункт «в»: краснота цикла до ожиданий
    пакета не доходила, и прогон снаружи выглядел зелёным везде, кроме
    одного поля вердикта гейта `cycle`).

    Ожидаемый ВЕРДИКТ стадии `cycle` набор вправе объявить ключом
    `stage_verdicts` — он тогда сверяется общим порядком, и пометка
    NOT_CLEAN_CYCLE: перед словом вердикта даст расхождение сама. Но
    ЭТА проверка от объявления не зависит: слова вердикта цикла у
    разных наборов разные (у набора с открытыми вопросами о целевой
    цикл не эквивалентен эталону по замыслу), а чистота — одна на
    всех.

    Судится ПОСЛЕДНИЙ блок: пересуженный после ответов
    (`stages.answers.cycle_status`), а нет его — блок самой стадии
    цикла. ИМЕННО ЗДЕСЬ исполняется условие аудитора «текущий итог
    может стать чистым» (квитанция A07RUN-REVIEW-RECEIPT-20260912-GPT,
    регрессия 6: «финальный результат судится по актуальным
    свидетельствам, а не только исторической красноте»): ожидания
    набора — суд о ТЕКУЩЕМ состоянии дел, и законно разрешённая
    ответами находка их не красит. Историю это не трогает, и второго
    закона состояний прогона для этого не заводится: запись стадии
    `cycle` в журнале остаётся как была, а итог записи считает словарь
    (`rr.state_from_journal`) — один на всех читателей (замечание С1
    второго неавторского ревью).

    Блок пересуда считается НАЛИЧНЫМ только когда он и вправду пересуд:
    в нём есть признак чистоты (`clean`) либо слова отказа
    (`registry_refusal`). Пустой словарь и сводка стадии ответов без
    пересуда красный блок цикла не перекрывают (замечание Н1 второго
    ревью: «блок есть» не то же самое, что «пересуд был»).

    НЕ КРАСИТ ровно одно — ПРЕДУПРЕЖДЕНИЕ (`live-non-blocking`: живые
    неблокирующие при чистоте по Б/К): иначе бэклог панели, которым
    цикл и заканчивается штатно, объявлялся бы расхождением набора.
    Всё остальное, что назвал `cycle_outcome`, краснит — и отказ по
    правилу, и ОСТАНОВКА ЧЕЛОВЕКОМ (`stopped-in-panel`): остановленный
    кнопкой цикл чистым не бывает, и правило «краснит только отказ по
    правилу» роняло бы такой прогон в зелёное ровно тогда, когда его
    исход стал честнее. Успех `cycle_outcome` не называет вовсе
    (None)."""
    stages = (stages or {}) if isinstance(stages, dict) else {}
    answers = stages.get("answers")
    after = answers.get("cycle_status") if isinstance(answers, dict) else None
    judged = isinstance(after, dict) and ("clean" in after
                                          or "registry_refusal" in after)
    stage, block = (("answers", after) if judged
                    else ("cycle", stages.get("cycle_status")))
    row = cycle_outcome(block)
    if row is None or row[0] == rr.OUTCOME_WARN:
        return []
    where = "стадия cycle" if stage == "cycle" \
        else "стадия answers (пересуд цикла после ответов)"
    return [f"{where}: {row[1]} — {row[2]}"]


class Runner:
    def __init__(self, out: Path, signals_dir: Path | None, max_rounds: int,
                 max_megacycles: int, allow_dirty: bool = False,
                 base_url: str | None = None, runs_dir: Path | None = None,
                 kit_dir: Path = HERE, cosmos_press: Path | None = None,
                 after_catalog_release: bool = False):
        self.out = out
        # Продолжение ТОГО ЖЕ прогона после выпуска шаблона (слово владельца
        # 16.09.2026: Н0-С одним прогоном): между стадиями меняются ДАННЫЕ
        # каталога — привязки, результаты стендов, эталоны и манифесты
        # наборов (json), а исполняемый код (py) остаётся тем же.
        self.after_catalog_release = after_catalog_release
        self.signals = signals_dir
        self.max_rounds = max_rounds
        self.max_megacycles = max_megacycles
        self.allow_dirty = allow_dirty
        self.code = code_identity()
        # Машинный отчёт нажатий COSMOS: без него ключ ожиданий
        # `cosmos_numbers` не судится (нажатия идут в COSMOS, а не в
        # этом прогоне) и честно называется несудимым.
        self.cosmos_press_path = Path(cosmos_press) if cosmos_press else None
        self.cosmos_press = load_cosmos_press(self.cosmos_press_path)
        # Набор — данные раннера (принцип «принципы, а не заплатки»):
        # каталог комплекта несёт манифест с файлами, эталоном, книгой
        # ответов, раскладкой ролей и ожиданиями прогона (ключ expect —
        # ИДАК H0-К, 2026-09-04); раннер один на все комплекты формы H0.
        self.kit = Path(kit_dir).resolve()
        self.manifest = json.loads((self.kit / "kit-manifest.json").read_text(encoding="utf-8"))
        self.customer = str(self.manifest.get("customer") or CUSTOMER)
        # Раскладку моделей ищет ОДНО место продукта (vmp.run_passport):
        # сперва путь от КОРНЯ репозитория, затем прежний путь от папки
        # набора. Копия набора вне open-bench соседа «../polyaris-h0»
        # не имеет — прежний порядок ронял прогон на первой стадии
        # (показ фазы 1 Н0-C, 09.09.2026).
        found = rp.resolve_layout(self.kit, self.manifest, REPO)
        self.layout_path = (found["file"] or (self.kit / "agents-layout-h0.json")
                            ).resolve()
        self.layout_tried = found["tried"]
        # Паспорт прогона: собирается и сверяется ДО стадии create
        # (метод passport()); здесь только место под него.
        self.passport = None
        # Запись о прогоне: голова из паспорта до стадии create, журнал
        # этапов по ходу, итоги в конце (метод make_record()).
        self.record = None
        # Хранитель ФАЙЛА записи в папке прогона: панель (она ведёт
        # запись, объявленную раннером) либо сам раннер (переходное
        # правило). Спрашивается у панели один раз, когда прогон уже
        # рождён — метод record_custodian().
        self._custodian = None
        # Режим площадки (H0П, решение владельца 03.09 «условия на Проде
        # абсолютно те же, что на локальном успешном прогоне»): панель —
        # ШТАТНАЯ СЛУЖБА по адресу base_url, а не в процессе; её среду
        # (раскладка, носители контура, реестр) раннер задать не может —
        # он ДОКАЗЫВАЕТ те же условия: раскладка H0 ставится прогону через
        # API, носители контура обязаны не применяться к заказчику набора
        # (предполётная проверка), артефакты прогона читаются из каталога
        # прогонов службы (runs_dir).
        self.base_url = base_url
        if base_url:
            if runs_dir is None:
                raise SystemExit("--base-url требует --runs-dir (каталог прогонов службы)")
            os.environ["CDPM_RUNS_DIR"] = str(runs_dir)
        else:
            os.environ["CDPM_RUNS_DIR"] = str(out / "runs")
            os.environ["CDPM_AGENTS_DEFAULT"] = str(self.layout_path)
            os.environ.pop("CDPM_SCOPE_FORCE", None)
            # Носители контура (гарантированные вопросы и темы условий)
            # принадлежат КОНКРЕТНОМУ заказчику и лежат в каталоге данных
            # машины. Эталонный прогон обязан быть от них независим: иначе
            # результат зависит от того, чьи файлы лежат на этой машине, и
            # воспроизвести его у другого исполнителя нельзя. Живой случай
            # 2026-09-03: тема «шкала времени» второго заказчика (её поле
            # applies_to пусто, значит носитель считается общим) поставила
            # вопрос в анкету набора H0, у которого шкала объявлена
            # собственным правилом. Пустое значение = механизм спит.
            os.environ["CDPM_CONDITIONS_TOPICS"] = ""
            os.environ["CDPM_ENSURE_QUESTIONS"] = ""
        sys.path.insert(0, str(REPO))
        from cdpm import providers as prov
        # Ключ «cloud» из настроек CDPM — каналу выполнения (МП-2): в
        # штатном запуске это делает webui.main() перед uvicorn; панель в
        # процессе стартует мимо main(), и без прокидки канал облака
        # молча уезжает в агентный Claude CLI (живой отказ прогона №4,
        # run-0001: CLI ответил прозой «I found the context here…»).
        prov.sync_cloud_key_to_env()
        cloud = next((p for p in prov.list_providers() if p["name"] == "cloud"), {})
        if cloud.get("channel") != "API":
            raise RuntimeError(
                f"канал облака Claude — {cloud.get('channel')!r}, а не API: "
                f"ключ «cloud» в настройках CDPM отсутствует или не прокинут; "
                f"агентный CLI для конвейера запрещён (fail-closed)")
        if base_url:
            import httpx
            # Долгие ручки (заморозка со зрением, выгрузка пакета) —
            # таймаут ответа минуты, не секунды по умолчанию httpx.
            self.client = httpx.Client(base_url=base_url,
                                       timeout=httpx.Timeout(900.0, connect=30.0))
            self.webui = None
        else:
            from fastapi.testclient import TestClient
            from cdpm import webui
            self.webui = webui
            self.client = TestClient(webui.app)
        self.paths = [self.kit / p for p in self.manifest["rules_files"]]
        self.data_paths = [self.kit / p for p in self.manifest.get("data_files") or []]
        self.etalon = json.loads((REPO / self.manifest["etalon_vmp"]).read_text(encoding="utf-8"))
        self.etalon_form = json.loads((REPO / self.manifest["etalon_form"]).read_text(encoding="utf-8"))
        self.state_path = out / "run-state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) \
            if self.state_path.is_file() else {"stages": {}}

    # ---------------------------------------------------------- служебное
    def _save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1),
                                   encoding="utf-8")

    def run_dir(self) -> Path:
        return Path(os.environ["CDPM_RUNS_DIR"]) / self.state["run"]

    def guard_code(self, stage: str) -> None:
        """Перед стадией: код тот же, что на первой стадии прогона; иначе
        стадия не запускается (выход кодом 2). Грязное дерево на старте
        — отказ без --allow-dirty."""
        now = code_identity()
        want = self.state.get("code")
        if want is None:
            block = blocking_paths(now["dirty_paths"])
            other = [p for p in now["dirty_paths"] if p not in block]
            if other:
                _log("предупреждение: грязные файлы вне исполняемого кода: " + ", ".join(other))
            if block and not self.allow_dirty:
                _log("ИСПОЛНЯЕМЫЙ КОД НЕ ЗАКОММИЧЕН: " + ", ".join(block) +
                     " — прогон серии не стартует (коммит либо --allow-dirty для дымового)")
                raise SystemExit(2)
            self.state["code"] = dict(now, smoke=bool(block))
            self._save()
            _log(f"код прогона: коммит {now['commit']}, отпечаток {now['fingerprint']} "
                 f"({len(now['files'])} файлов)"
                 + (" — ДЫМОВОЙ (код не закоммичен: " + ", ".join(block) + ")" if block else ""))
        else:
            changed = sorted(k for k in set(want["files"]) | set(now["files"])
                             if want["files"].get(k) != now["files"].get(k))
            block = blocking_paths(changed)
            if block and self.after_catalog_release:
                # Ключ --after-catalog-release: смена json-данных каталога и
                # эталонов законна и ЗАПИСЫВАЕТСЯ в состояние прогона (не
                # глотается); любой изменённый .py — по-прежнему стоп.
                code_changed = [p for p in block if not p.endswith(".json")]
                if code_changed:
                    _log(f"КОД ИЗМЕНИЛСЯ ВНУТРИ ПРОГОНА (не данные каталога): "
                         f"{', '.join(code_changed)} (коммит {want['commit']} → "
                         f"{now['commit']}); стадия {stage} не запускается")
                    raise SystemExit(2)
                _log(f"ПРОДОЛЖЕНИЕ ПОСЛЕ ВЫПУСКА КАТАЛОГА: изменились данные "
                     f"каталога и эталоны (json): {', '.join(block)} (коммит "
                     f"{want['commit']} → {now['commit']}); исполняемый код тот же")
                self.state.setdefault("catalog_release_continuation", []).append(
                    {"stage": stage, "commit_from": want["commit"],
                     "commit_to": now["commit"], "changed": block})
                block = []
            if block:
                _log(f"КОД ИЗМЕНИЛСЯ ВНУТРИ ПРОГОНА: {', '.join(block)} (коммит "
                     f"{want['commit']} → {now['commit']}); стадия {stage} не запускается")
                raise SystemExit(2)
            if changed:
                _log("предупреждение: изменились незагруженные файлы: " + ", ".join(changed))
        self.state.setdefault("code_by_stage", {})[stage] = {
            "commit": now["commit"], "fingerprint": now["fingerprint"]}
        self._save()

    # ------------------------------------------------- паспорт прогона
    def make_passport(self, *, until: str | None = None,
                      catalog_key: str | None = None,
                      bindings_key: str | None = None,
                      solver_key: str | None = None) -> dict:
        """Паспорт прогона ДО первой стадии: собрать один объявленный
        набор параметров (набор правил и данных авиакомпании, каталог,
        привязки, раскладка моделей, решатель, библиотеки, код, панель),
        сверить его с действительностью, напечатать в журнал ЦЕЛИКОМ и
        положить файлом в --out.

        Расхождение — отказ словами «объявлено … / на деле …» ДО первого
        вызова модели: слово владельца 10.09.2026 «каждый прогон имеет
        свои параметры», и узнавать о чужом каталоге на середине прогона
        поздно (Н0-C, 09.09.2026)."""
        p = rp.build(kit_dir=self.kit, manifest=self.manifest, repo=REPO,
                     code=passport_code(self.code), base_url=self.base_url,
                     until=until, catalog_key=catalog_key,
                     bindings_key=bindings_key, solver_key=solver_key)
        self.passport = p
        log_passport(p, self.out)
        words = rp.refusal(p)
        if words:
            _log(words)
            raise SystemExit(2)
        _log("паспорт прогона сошёлся: объявленные параметры равны "
             "действительным")
        # Прогон продолжается (--run / --from-stage): заморозка уже
        # лежит на диске — сверяем её сразу, а не со следующей стадией.
        if self.state.get("run"):
            self.check_freeze()
        return p

    def check_freeze(self) -> None:
        """Вторая сверка паспорта — ПОСЛЕ заморозки: конвейер обязан был
        заморозить ровно объявленные документы и книгу данных. Манифеста
        заморозки ещё нет — сверять нечего, молчим."""
        # getattr, а не self.passport: раннер бывает собран мимо
        # __init__ (стенды CDPM строят его object.__new__), и сверка
        # заморозки не имеет права ронять такой разбор.
        passport = getattr(self, "passport", None)
        if not passport or not self.state.get("run"):
            return
        run_dir = self.run_dir()
        problems = rp.freeze_problems(passport, run_dir)
        if problems:
            for w in problems:
                _log("   ! " + w)
            raise SystemExit("СТОП: прогон заморозил не то, что объявлено "
                             "паспортом (расхождений %d)" % len(problems))
        _log("заморозка сверена с паспортом: расхождений нет")
        # Паспорт — рядом с итогами прогона: панель читает его там же,
        # где отчёты, а не в каталоге запуска раннера.
        try:
            rp.write(passport, run_dir / rp.PASSPORT_FILE)
        except OSError as e:                              # noqa: BLE001
            _log(f"предупреждение: паспорт не лёг в папку прогона: {e}")

    # ------------------------------------------------ запись о прогоне
    def make_record(self, a, planned: list):
        """Запись о прогоне ДО стадии create: голова — объявление из
        паспорта, журнал пуст, состояние «объявлен».

        Отказ сборки записи прогон НЕ останавливает: гейт параметров —
        паспорт (он уже сошёлся), а запись ведёт летопись. Расхождение
        печатается словами и попадает в отчёт исполнителя, но полсотни
        минут работы моделей из-за летописи не пропадают."""
        kept = self.continued_record(a)
        if kept is not None:
            self.record = kept
            _log("запись о прогоне продолжена: "
                 f"{(kept.get('declaration') or {}).get('run_id')} (из {self.out}); "
                 "новая голова не чеканится")
            return kept
        self.record = record_or_warn(
            self.passport, a, planned, panel_code=self.panel_code(),
            cosmos_env=ask_cosmos_environment(a, kit_llm_scope(self.passport)))
        if self.record is None:
            return None
        _log(rr.summary(self.record))
        self.save_record()
        return self.record

    def panel_code(self) -> dict | None:
        """Блок `code` предполётной сверки панели (`GET
        /api/declaration-preview`): коммит панели, ведущей прогон. Панели
        нет, ручка стара (404) или молчит — None, коммит идёт из паспорта;
        причина — словами в журнал, не молча."""
        client = getattr(self, "client", None)
        if client is None:
            return None
        try:
            r = client.get("/api/declaration-preview")
            body = r.json() if r.status_code == 200 else None
        except Exception as e:                            # noqa: BLE001
            _log(f"код панели не прочитан ({e}) — коммит CDPM в голове из "
                 "паспорта (дерево раннера)")
            return None
        code = ((body or {}).get("declaration") or {}).get("code") \
            if isinstance(body, dict) else None
        if not isinstance(code, dict) or not code.get("cdpm_commit"):
            _log("панель не назвала свой коммит (declaration-preview) — коммит "
                 "CDPM в голове из паспорта (дерево раннера)")
            return None
        _log(f"код панели: {str(code['cdpm_commit'])[:12]} — коммит CDPM в голове "
             "по панели, не по дереву раннера")
        return code

    def continued_record(self, a):
        """Продолжение прогона (`--run`) ведёт ТУ ЖЕ запись из `--out`, а
        не чеканит новую: номер записи — личность прогона у хранителя
        (панели) и в манифесте пакета; вторая голова сделала бы запись
        раннера «чужой» для хранителя и порвала бы сшивку (открытый
        вопрос пакета D, закрыт 10.09.2026). Условия: назван `--run`, в
        `--out` лежит `run-record.json`, и состояние раннера в том же
        `--out` помнит именно этот прогон. Иначе — запись новая."""
        run = getattr(a, "run", None)
        if not run:
            return None
        path = self.out / rr.RECORD_FILE
        if not path.is_file():
            return None
        remembered = str((getattr(self, "state", None) or {}).get("run") or "")
        if remembered != str(run):
            _log(f"предупреждение: в {self.out} запись другого прогона "
                 f"({remembered or 'без номера'}), а продолжается {run} — "
                 "чеканится новая запись")
            return None
        try:
            return rr.load(path)
        except rr.RunRecordError as e:                    # noqa: BLE001
            _log(f"предупреждение: запись {path} не прочитана ({e}) — "
                 "чеканится новая")
            return None

    def record_custodian(self) -> str:
        """Кто ведёт ФАЙЛ записи в папке прогона — панель или сам раннер
        (раздел 11.1 проекта: «хранитель»).

        Спрашивается у панели ОДИН раз, когда прогон уже рождён:
        `GET /api/runs/<прогон>/record`. Ответ 200 и номер записи наш —
        хозяин записи раннер, а хранитель файла ПАНЕЛЬ: раннер в папку
        прогона не пишет вовсе (два летописца переписывали бы друг
        друга — живая находка run-0129..0131) и шлёт свои этапы ручкой
        дописывания. Ответ 404, чужой номер или молчание службы —
        переходное правило: файл ведёт раннер, как сегодня, и ручка не
        зовётся.

        Прогон ещё не рождён (или записи нет) — ответ «раннер» БЕЗ
        запоминания: спросим, когда будет о чём спрашивать."""
        kept = getattr(self, "_custodian", None)
        if kept:
            return kept
        record = getattr(self, "record", None)
        client = getattr(self, "client", None)
        run = (getattr(self, "state", None) or {}).get("run")
        if record is None or client is None or not run:
            return CUSTODIAN_RUNNER
        mine = str(((record.get("declaration") or {}).get("run_id")) or "")
        path = record_path(run=run)
        try:
            r = client.get(path)
            status = r.status_code
            theirs = ((r.json() or {}).get("declaration") or {}).get("run_id") \
                if status == 200 else None
        except Exception as e:                            # noqa: BLE001
            _log(f"предупреждение: ручка записи панели не ответила либо ответ "
                 f"не разобран ({path}): {e} — хранитель файла записи РАННЕР "
                 f"(переходное правило)")
            self._custodian = CUSTODIAN_RUNNER
            return self._custodian
        if status == 200 and str(theirs or "") == mine:
            self._custodian = CUSTODIAN_PANEL
            _log(f"хранитель файла записи — ПАНЕЛЬ: запись {mine} ведёт она, "
                 f"раннер шлёт ей этапы и итоги (файл в папку прогона не "
                 f"пишет; своя копия остаётся в {self.out})")
        elif status == 200:
            self._custodian = CUSTODIAN_RUNNER
            _log(f"хранитель файла записи — РАННЕР: у панели своя запись "
                 f"{theirs!r}, а не наша {mine!r} — переходное правило «два "
                 f"летописца», дописывание не шлётся")
        elif status == 404:
            self._custodian = CUSTODIAN_RUNNER
            _log("хранитель файла записи — РАННЕР: панель записи о прогоне "
                 "не ведёт (404) — переходное правило «два летописца»")
        else:
            self._custodian = CUSTODIAN_RUNNER
            _log(f"предупреждение: ручка записи панели ответила {status} — "
                 f"хранитель файла записи РАННЕР (переходное правило)")
        return self._custodian

    def record_places(self) -> list:
        """Места записи: каталог --out ВСЕГДА; папка прогона — только
        пока файл там ведёт сам раннер. Хранитель панель — вторая копия
        переписала бы сшитую запись хозяина половиной раннера."""
        places = [self.out]
        if self.record_custodian() == CUSTODIAN_PANEL:
            return places
        if (getattr(self, "state", None) or {}).get("run"):
            try:
                places.append(self.run_dir())
            except KeyError:
                pass
        return places

    def send_record_append(self, entries=None, results=None) -> None:
        """Этапы и итоги — ХОЗЯИНУ записи через хранителя
        (`POST /api/runs/<прогон>/record/append`, раздел 11.2 проекта).

        Зовётся, только когда хранитель — панель. Отказ хранителя
        (404 — прогона или записи нет, 409 — голова разошлась, 422 —
        запись не по словарю) и молчание службы — ПРЕДУПРЕЖДЕНИЕ
        словами и продолжение прогона: файл записи в каталоге --out
        полон всегда, и летопись не имеет права ронять прогон."""
        if self.record_custodian() != CUSTODIAN_PANEL:
            return
        record = getattr(self, "record", None)
        run = (getattr(self, "state", None) or {}).get("run")
        if record is None or not run:
            return
        path = record_path(run=run) + "/append"
        body = append_body(record, entries=entries, results=results,
                           declaration_sha256=(self.state or {}).get("record_keeper_sha"))
        try:
            r = self.client.post(path, json=body)
        except Exception as e:                            # noqa: BLE001
            _log(f"ПРЕДУПРЕЖДЕНИЕ: записи не ушли хранителю ({path}): {e}")
            return
        status = r.status_code
        try:
            answer = r.json()
        except ValueError:                                # ответ без тела
            answer = {}
        answer = answer if isinstance(answer, dict) else {}
        if status == 200:
            _log(f"хранитель записи принял: записей {answer.get('accepted')}, "
                 f"заменено {answer.get('replaced')}")
            return
        words = answer.get("words") or answer.get("detail") or ""
        if isinstance(words, list):
            words = "; ".join(str(w) for w in words)
        _log(f"ПРЕДУПРЕЖДЕНИЕ: хранитель записи не принял дописывание "
             f"({status}, код {answer.get('code')!r}): {str(words)[:300]} — "
             f"прогон продолжается, запись в {self.out} полна")

    def save_record(self) -> None:
        record = getattr(self, "record", None)
        if record is not None:
            write_record(record, *self.record_places())

    def record_stage(self, stage: str, started_at: str, finished_at: str,
                     outcome: str, reason: str | None = None,
                     words: str | None = None) -> None:
        """Отметка этапа в журнале записи: время, исход, счётчики и
        артефакты. Журнал только дописывается, и после каждой отметки
        запись ложится файлом — оборванный прогон оставляет пройденное,
        а не пустоту.

        Хранитель записи — панель: тот же этап тут же уезжает ей
        дописыванием. Шлётся ПРИ ЗАВЕРШЕНИИ этапа (и при сбое): у
        хранителя ключ (система, этап, подэтап), повтор заменяет
        прежнюю запись, и ждать конца прогона незачем."""
        record = getattr(self, "record", None)
        if record is None:
            return
        try:
            rr.append_stage(record, {
                "stage": stage, "started_at": started_at,
                "finished_at": finished_at, "outcome": outcome,
                "reason_code": reason, "words": words, "actor": RECORD_ACTOR,
                "counters": stage_counters(self.state, stage),
                "artifacts": stage_artifacts(self.state, stage)})
        except rr.RunRecordError as e:                    # noqa: BLE001
            _log(f"ПРЕДУПРЕЖДЕНИЕ: этап {stage} в запись не принят — {e}")
            return
        self.save_record()
        self.send_record_append(entries=[(record.get("journal") or [])[-1]])

    def finish_record(self, state: str | None = None) -> None:
        """Итоги записи: вердикты стадий, сравнения, состояние прогона и
        общий хронометраж. Этапы плана, до которых прогон не дошёл,
        дописываются исходом «не начат» — план объявлен, и молчание о
        непройденном было бы потерей.

        Сравнения (раздел 3.3 проекта): с ОЖИДАНИЯМИ набора — ровно тот
        суд, что прошёл на стадии package; с ПРЕДЫДУЩИМ прогоном того же
        корня и с ЗОЛОТЫМ — из ручки дерева панели, а нет её — пусто.

        Признак золотого раннер НЕ ставит НИКОГДА (решение владельца 6.3
        от 10.09.2026): `results.golden` остаётся неподтверждённым, и
        назвать прогон образцом может только владелец словом после
        приёмки на Prod.

        СОСТОЯНИЕ ПРОГОНА СЧИТАЕТ СЛОВАРЬ ЗАПИСИ, А НЕ РАННЕР: имени
        состояния `rr.finish` не передаётся, и запись закрывается общим
        законом `rr.state_from_journal` — по исходам ВСЕХ записей
        журнала («сбой» > «остановлен человеком» > «отказ по правилу» >
        «завершён»). Свой отбор записей раннер держать не вправе:
        хранитель записи (панель, `cdpm/run_record.py::_record_state`) и
        карточка COSMOS (`webapp/run_journal.py::_keeper_state`) читают
        те же записи своим — общим — законом, и раннерский отбор
        «последняя запись стадии, ответы перекрывают цикл» давал одной
        записи одного прогона ДВА разных итога (замечание С1 второго
        неавторского ревью REVIEW2-A07RUN-e67bef1c-COSMOS). Условие
        аудитора «текущий итог может стать чистым» исполняют не итоги
        записи, а ожидания набора и вердикты по ПОСЛЕДНЕМУ блоку цикла
        (`cycle_clean_problems`): красный цикл, разрешённый ответами,
        даёт «остановлен по правилу» с чистыми ожиданиями — история
        красна, текущая чистота видна.

        Состояние, названное звавшим (`STATE_FAILURE` и
        `STATE_HUMAN_STOP` из `main`), старше и не пересчитывается."""
        record = getattr(self, "record", None)
        if record is None:
            return
        passed = {e.get("stage") for e in record.get("journal") or []}
        for stage in (record.get("declaration") or {}).get("planned_stages") or []:
            if stage not in passed:
                self.record_stage(stage, None, None, rr.OUTCOME_NOT_STARTED,
                                  "stage-not-reached",
                                  "этап плана не начат: прогон закончился раньше")
        verdicts = {k: (v.get("verdict") if isinstance(v, dict) else v)
                    for k, v in (self.state.get("stages") or {}).items()
                    if k in STAGES}
        package = (self.state.get("stages") or {}).get("package")
        comparison = {"expectations": comparison_expectations(
            record.get("declaration"),
            (package or {}).get("expect") if isinstance(package, dict) else None)}
        comparison.update(comparison_from_tree(self.fetch_tree()))
        try:
            rr.finish(record, {"verdicts": verdicts,
                               "comparison": comparison}, state)
        except rr.RunRecordError as e:                    # noqa: BLE001
            _log(f"ПРЕДУПРЕЖДЕНИЕ: итоги в запись не приняты — {e}")
            return
        _log(rr.summary(record))
        self.save_record()
        # Сравнения — словами в журнал прогона: их считает общий модуль
        # дерева (`vmp/run_tree.py`), раннер только показывает. Суд
        # ожиданий печатать здесь незачем — он уже прозвучал на стадии
        # package, и второй его печатью раннер не занимается.
        res = record.get("results") or {}
        for key, name in (("previous_run", "СРАВНЕНИЕ С ПРЕДЫДУЩИМ ПРОГОНОМ"),
                          ("golden", "СРАВНЕНИЕ С ЗОЛОТЫМ ПРОГОНОМ")):
            row = (res.get("comparison") or {}).get(key) or {}
            words = row.get("words") or []
            if not words:
                continue
            _log(f"{name} ({row.get('run_id')}):")
            for line in words:
                _log("   " + str(line))
        # Итоги — хозяину записи: вердикты, выдачи и сравнения (разделы
        # 11.2 и 12.4). Этапы плана, дописанные здесь исходом «не начат»,
        # уже ушли ему отметками выше — второй раз их слать незачем, и
        # тело дописывания идёт с пустыми entries.
        self.send_record_append(results={"verdicts": res.get("verdicts") or {},
                                         "deliveries": res.get("deliveries") or [],
                                         "comparison": res.get("comparison") or {}})

    def status(self) -> dict:
        r = self.client.get(f"/api/runs/{self.state['run']}")
        r.raise_for_status()
        return r.json()

    def wait(self, busy_states: tuple, timeout_s: int, label: str) -> dict:
        t0 = time.time()
        last = ""
        while True:
            st = self.status()
            state = str(st.get("state") or "")
            prog = st.get("progress") or {}
            line = state + (f" [{prog.get('stage')}: {prog.get('done')}/{prog.get('total')}]"
                            if prog else "")
            if line != last:
                _log(f"{label}: {line}")
                last = line
            if state not in busy_states:
                return st
            if time.time() - t0 > timeout_s:
                raise TimeoutError(f"{label}: превышено {timeout_s} с (состояние {state!r})")
            time.sleep(5)

    def reference_model(self) -> dict:
        """Эталонная модель читателя канонической формы ИЗ ЗАМОРОЗКИ
        ЭТОГО ПРОГОНА (те же якоря, что у кандидата)."""
        from cdpm import extract as extract_mod
        from cdpm import form_text as ft
        entries = extract_mod._load_kit(self.run_dir() / "freeze")
        form, rep = ft.read_form_text(entries)
        model = ft.model_from_form_text(form)
        return model, rep

    def gate(self, tag: str, model_path: Path, pkg_path: Path | None = None) -> dict:
        from cdpm import model as m
        from vmp import equivalence as eq
        cand = json.loads(model_path.read_text(encoding="utf-8"))
        etalon_zip = None
        if pkg_path is not None:
            cand_kit_zip = self.signals.parent / "kit-run" / "task-package.zip" \
                if self.signals else None
            if cand_kit_zip and cand_kit_zip.is_file():
                etalon_zip = cand_kit_zip
        res = eq.equivalence(cand, self.etalon,
                             cand_zip=pkg_path if (pkg_path and etalon_zip) else None,
                             etalon_zip=etalon_zip,
                             strict_questions=bool(self.manifest.get("strict_questions", True)))
        ref, rep = self.reference_model()
        cl = clause_level_diff(cand, ref)
        c = res["counts"]
        summary = {
            "tag": tag, "run": self.state["run"], "verdict": res["verdict"],
            "rules": f"{c['covered']}/{c['etalon_rules']}", "extra": c["extra"],
            "objectives": f"{c['objectives_ok']}/{c['objectives_etalon']}",
            "objectives_extra": c["objectives_extra"],
            "questions_total": c["questions_total"], "open_questions": c["open_questions"],
            "form": f"{c['form_rows_ok']}/{c['form_rows']}", "problems": res["problems"],
            "clause_level": {"ok": len(cl["ok"]), "missing": cl["missing"],
                             "extra": cl["extra"], "mismatches": cl["mismatches"]},
            "reader_unrecognized": rep["unrecognized"],
            "contract": (m.validate_schema(cand) + m.validate_model(cand)
                         + m.validate_template_fit(cand)),
            "model_path": str(model_path),
            "package_path": str(pkg_path) if pkg_path else None,
            "code": {k: v for k, v in (self.state.get("code") or {}).items()
                     if k in ("commit", "fingerprint", "smoke", "tree_dirty")},
        }
        # Акт приёмки (П4, Д5): хэш и сводка входят в отчёт гейта, если
        # акт изготовлен; полнота — по проверке кода.
        from cdpm import acceptance_act as act_mod
        act = act_mod.load(self.run_dir())
        if act is not None:
            summary["acceptance_act"] = dict(act_mod.summary(act),
                                             problems=len(act_mod.validate(act, cand)))
        rdir = self.out / "gates"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / f"gate-{tag}.md").write_text(
            eq.render_report(res, header=f"Прогон {self.state['run']}, выход {tag}"),
            encoding="utf-8")
        (rdir / f"gate-{tag}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.signals:
            # Имя прохода — в имени каталога сигналов: у каждого прохода
            # свой каталог --out, а прогон в нём всегда run-0001, и
            # копии проходов перетирали друг друга (COSMOS видел только
            # последний).
            sdir = self.signals / f"gate-{self.out.name}-{self.state['run']}"
            sdir.mkdir(parents=True, exist_ok=True)
            shutil.copy(model_path, sdir / f"model-{tag}.json")
            if pkg_path:
                shutil.copy(pkg_path, sdir / f"task-package-{tag}.zip")
            shutil.copy(rdir / f"gate-{tag}.md", sdir / f"gate-{tag}.md")
            shutil.copy(rdir / f"gate-{tag}.json", sdir / f"gate-{tag}.json")
        _log(f"ГЕЙТ {tag}: {summary['verdict']} — правил {summary['rules']}, "
             f"сверх {summary['extra']}, целевых {summary['objectives']}"
             f" (+{summary['objectives_extra']}), вопросов "
             f"{summary['questions_total']}, форма {summary['form']}, "
             f"нарушений {len(res['problems'])}; поклаузно: ок "
             f"{len(cl['ok'])}, нет {len(cl['missing'])}, лишних "
             f"{len(cl['extra'])}, расхождений {len(cl['mismatches'])}")
        for p in res["problems"][:12]:
            _log("   - " + p)
        for mm in cl["mismatches"][:12]:
            _log(f"   ≠ {mm['ref']} ({mm['anchor'][1]}): {mm['diff']}")
        self.state["stages"][tag] = summary
        self._save()
        return summary

    # -------------------------------------------------------------- стадии
    def check_catalog(self) -> bool:
        """Закрепление базы шаблонов у комплекта: манифест несёт блок
        `catalog {version, fingerprint}`, и он обязан совпасть с
        каталогом продукта ДО старта (перевыпуск эталонов, 08.09; §3
        варианта А проекта 7-Б `KATALOG-SHAG-7-CDPM-NOSITELI-PROEKT-CLD.md`).

        Без блока проверки нет — комплекты, выпущенные до перевыпуска,
        идут как прежде; сказать об этом словами обязательно, иначе
        «проверки не было» и «проверка прошла» выглядят одинаково."""
        block = self.manifest.get("catalog")
        if not isinstance(block, dict):
            _log("каталог шаблонов: комплект базу не закрепляет (в манифесте "
                 "нет блока catalog) — сверка не делается")
            return True
        try:
            from vmp import catalog_access as ca
            stamp = ca.stamp()
        except Exception as e:                                # noqa: BLE001
            _log(f"КАТАЛОГ ШАБЛОНОВ НЕ ЧИТАЕТСЯ: {e} — комплект закреплён за "
                 f"{block.get('version')}, а сверить не по чему")
            return False
        same = (str(block.get("version")) == str(stamp["version"])
                and str(block.get("fingerprint")) == str(stamp["fingerprint"]))
        words = (f"комплект закреплён за {block.get('version')} · "
                 f"{str(block.get('fingerprint'))[:12]}…, у продукта "
                 f"{stamp['version']} · {stamp['fingerprint'][:12]}…")
        if same:
            _log("каталог шаблонов: " + words + " — сошлось")
            return True
        _log("КАТАЛОГ ШАБЛОНОВ НЕ ТОТ: " + words + " — перевыпустите комплект "
             "под текущую версию либо закрепите продукт за версией комплекта "
             "(CREW_TEMPLATE_CATALOG)")
        return False

    def preflight(self) -> bool:
        from cdpm import providers as prov
        ok = True
        for p in self.paths:
            if not p.is_file():
                _log(f"НЕТ ФАЙЛА: {p}"); ok = False
        layout = json.loads(self.layout_path.read_text(encoding="utf-8"))
        for aux in ("form_version", "customer", "_comment"):
            layout.pop(aux, None)
        tmp = self.out / "_preflight"; tmp.mkdir(parents=True, exist_ok=True)
        probs = prov.save_agents(tmp, layout)
        if probs:
            _log("РАСКЛАДКА НЕ ПРИНЯТА: " + "; ".join(probs)); ok = False
        for provider in ("cloud", "openai"):
            if not prov.provider_key(provider):
                _log(f"НЕТ КЛЮЧА провайдера {provider!r} в настройках CDPM"); ok = False
        eff = {role: prov.effective_role(prov.load_agents(tmp), role)
               for role in ("extractor", "replier", "auditor", "arbiter", "applier",
                            "vision", "answer_applier", "data_compiler", "logic_verifier")}
        _log("роли → конфигурация: " + ", ".join(f"{k}={v}" for k, v in eff.items()))
        agents_now = prov.load_agents(tmp)
        indep = prov.independence_problems(agents_now)
        _log(f"слой 2: извлечение {prov.role_family(agents_now, 'extractor')}, второй "
             f"читатель {prov.role_family(agents_now, 'second_reader')}"
             + (" — " + "; ".join(indep) if indep else " — независимы"))
        if indep:
            ok = False
        if self.base_url:
            # Режим площадки: носители контура каталога данных площадки
            # не должны применяться к заказчику набора — иначе условия
            # отличаются от локального прогона (там механизм спит).
            from cdpm import model as m
            carriers = m.contour_carriers_report(self.customer)
            applied = [c for c in carriers if c.get("applied")]
            _log(f"носители контура площадки: {len(carriers)}, применяются к набору: "
                 f"{len(applied)}" + ("".join(f"\n   ! {c.get('file')}: заказчик "
                                              f"{c.get('customer')!r}, applies_to "
                                              f"{c.get('applies_to')!r}" for c in applied)))
            if applied:
                _log("УСЛОВИЯ ОТЛИЧАЮТСЯ ОТ ЛОКАЛЬНЫХ: носитель контура применяется к "
                     "набору — ограничьте его поле applies_to его же заказчиком")
                ok = False
            try:
                r = self.client.get("/api/runs")
                _log(f"панель {self.base_url}: HTTP {r.status_code}, прогонов "
                     f"{len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else '?'}")
                if r.status_code != 200:
                    ok = False
            except Exception as e:                           # noqa: BLE001
                _log(f"ПАНЕЛЬ НЕДОСТУПНА {self.base_url}: {e}"); ok = False
        for p in self.data_paths:
            if not p.is_file():
                _log(f"НЕТ ФАЙЛА ДАННЫХ: {p}"); ok = False
        _log(f"книга данных: {[p.name for p in self.data_paths] or "нет — пакет пойдёт без clean-data"}")
        _log(f"мишень: набор {[p.name for p in self.paths]}, эталон "
             f"{self.manifest['etalon_vmp']}, форма {self.manifest['etalon_form']}")
        if not self.check_catalog():
            ok = False
        etalon_zip = (self.signals.parent / "kit-run" / "task-package.zip") if self.signals else None
        _log(f"эталонный пакет для слоя пакетов: "
             f"{'есть' if etalon_zip and etalon_zip.is_file() else 'нет (слой пакетов будет пропущен)'}")
        _log(f"каталог прогонов: {os.environ['CDPM_RUNS_DIR']}; сигналы: {self.signals}")
        c = self.code
        block = blocking_paths(c["dirty_paths"])
        _log(f"код: коммит {c['commit']}, отпечаток {c['fingerprint']} ({len(c['files'])} файлов); "
             f"исполняемый код {'НЕ ЗАКОММИЧЕН: ' + ', '.join(block) if block else 'закоммичен'}"
             + ("; прочие грязные: " + ", ".join(p for p in c['dirty_paths'] if p not in block)
                if len(block) < len(c["dirty_paths"]) else ""))
        if block and not self.allow_dirty:
            ok = False
        _log("предполётная проверка: " + ("ЧИСТО" if ok else "КРАСНАЯ"))
        return ok

    def stage_create(self) -> None:
        files = [("files", (p.name, p.read_bytes(),
                            "application/octet-stream")) for p in self.paths]
        t0 = time.time()
        # Признак допуска моделей — у комплекта (манифест набора):
        # класс данных, явный допуск и основание едут с прогоном от
        # создания (решение владельца 03.09); площадка не прижимает.
        kit_params = {k: self.manifest[k] for k in ("data_class", "llm_scope",
                                                    "llm_scope_basis")
                      if self.manifest.get(k)}
        # ОБЪЯВЛЕНИЕ ПРОГОНА (раздел 11.1 проекта): панель не создаёт
        # прогон без объявления, а голова записи у раннера уже собрана —
        # номер записи чеканится ДО создания прогона (make_record идёт
        # сразу за паспортом). Старая панель лишних полей формы не
        # читает: прогон идёт как прежде.
        form = declaration_form(getattr(self, "record", None))
        if form:
            _log(f"объявление прогона панели: запись {form['record_run_id']}, "
                 f"объявил {form['created_by']}, цель {form['purpose']}, "
                 f"ответы «{form['answer_policy']}», план "
                 f"{form['planned_stages']}, остановка после "
                 f"{form['stop_after']}, ожидания — {form['expectations_kind']} "
                 f"{form['expectations_ref']}")
        r = self.client.post("/api/runs", files=files, data=form,
                             params={"cloud_images": "true", "customer": self.customer,
                                     **kit_params})
        body = r.json()
        if r.status_code != 200 or body.get("state") != "заморожено":
            raise RuntimeError(f"заморозка: {r.status_code} {body}")
        self.state["run"] = body["run"]
        keeper_sha, words = keeper_head(body, getattr(self, "record", None))
        if keeper_sha:
            self.state["record_keeper_sha"] = keeper_sha
        if words:
            _log("ПРЕДУПРЕЖДЕНИЕ: " + words)
        self._save()
        fc = body.get("freeze_counters") or {}
        _log(f"ЗАМОРОЖЕНО {body['run']} за {time.time() - t0:.0f} с: файлов "
             f"{fc.get('files')}, клауз {fc.get('clauses')}")
        # Прогон рождён — сразу спрашиваем, кто теперь ведёт файл записи:
        # ответ нужен раньше первой отметки этапа (она уже пишет файл и
        # шлёт дописывание).
        self.record_custodian()
        # Сверка jpg-заморозки (зрение) со строками генератора набора.
        s3 = json.loads((self.run_dir() / "freeze" / "clauses" / "S3.json")
                        .read_text(encoding="utf-8"))
        # Строки генератора jpg — ключ манифеста jpg_lines (умолчание —
        # etalon/jpg-lines.json рядом с манифестом; варианты комплекта в
        # подкаталогах указывают ../etalon/jpg-lines.json).
        want = json.loads((self.kit / (self.manifest.get("jpg_lines")
                                       or "etalon/jpg-lines.json")).read_text(encoding="utf-8"))
        got = [c["text"] for c in s3["clauses"]]
        same = got == want
        _log(f"зрение jpg: абзацев {len(got)} (ожидалось {len(want)}) — "
             f"{'БУКВА В БУКВУ' if same else 'РАСХОЖДЕНИЯ'}")
        if not same:
            import difflib
            for d in difflib.ndiff(want, got):
                if d[:1] in "+-":
                    _log("   " + d[:200])
        self.state["stages"]["create"] = {"run": body["run"], "freeze": fc,
                                          "jpg_exact": same, "sec": round(time.time() - t0)}
        r2 = self.client.put(f"/api/runs/{self.state['run']}/files-config",
                             json={"files": {p.name: {"processing": "cloud"}
                                             for p in self.paths}})
        r2.raise_for_status()
        _log("политика файлов: cloud ×" + str(len(self.paths)))
        if self.base_url:
            # Раскладка ролей набора — прогону явно (в процессе её даёт
            # площадочный дефолт через CDPM_AGENTS_DEFAULT; у службы свой
            # дефолт, и прогон обязан нести раскладку H0 сам).
            layout = json.loads(self.layout_path.read_text(encoding="utf-8"))
            for aux in ("form_version", "customer", "_comment"):
                layout.pop(aux, None)
            r3 = self.client.put(f"/api/runs/{self.state['run']}/agents", json=layout)
            if r3.status_code != 200:
                raise RuntimeError(f"раскладка ролей не принята: {r3.status_code} {r3.text[:300]}")
            eff = self.client.get(f"/api/runs/{self.state['run']}/agents/effective").json()
            _log("раскладка H0 поставлена прогону: " + ", ".join(
                f"{e.get('role')}={(((e.get('channels') or {}).get('cloud') or {}).get('model') or '?')}"
                for e in (eff.get("roles") or []) if isinstance(e, dict)
                and e.get("role") in ("extractor", "auditor", "arbiter", "applier",
                                      "vision", "data_compiler")))
        self._save()
        # Вторая сверка паспорта: конвейер обязан был заморозить ровно
        # объявленные документы, а не то, что оказалось под рукой.
        self.check_freeze()

    def stage_data(self) -> None:
        """Загрузка книги данных прогона: заморозка данных и её гейт —
        кодом, без моделей (контракт данных пакета — COSMOS,
        final-report/KONTRAKT-DANNYH-PAKETA-CLD.md)."""
        if not self.data_paths:
            _log("книги данных в наборе нет — стадия пропущена")
            self.state["stages"]["data"] = {"files": 0, "skipped": True}
            self._save()
            return
        t0 = time.time()
        files = [("files", (p.name, p.read_bytes(),
                            "application/octet-stream")) for p in self.data_paths]
        r = self.client.post(f"/api/runs/{self.state['run']}/data", files=files)
        if r.status_code != 200:
            raise RuntimeError(f"загрузка данных: {r.status_code} {r.text[:300]}")
        body = r.json()
        _log(f"ДАННЫЕ {body.get('state')} за {time.time() - t0:.0f} с: "
             f"{body.get('counters')}; нарушений гейта {len(body.get('problems') or [])}")
        for pr in (body.get("problems") or [])[:10]:
            _log("   ! " + str(pr))
        self.state["stages"]["data"] = {"state": body.get("state"),
                                        "counters": body.get("counters"),
                                        "problems": body.get("problems") or [],
                                        "sec": round(time.time() - t0)}
        # Политика КАЖДОГО файла прогона, включая книгу данных: набор
        # обезличен по построению (вымышленная авиакомпания, генератор
        # в репозитории), и это его объявленное свойство. Незаписанный
        # файл получает строжайшее умолчание «только локально», и тогда
        # строжайший класс прогона становится локальным: перекрёстное
        # чтение уходит на локальную модель, а второе чтение
        # выключается. Живой отказ 03.09: книга данных появилась в
        # наборе, политику ей никто не поставил, и извлечение упало на
        # недоступной Ollama после трёх стадий работы.
        r2 = self.client.put(f"/api/runs/{self.state['run']}/files-config",
                             json={"files": {p.name: {"processing": "cloud"}
                                             for p in self.paths + self.data_paths}})
        r2.raise_for_status()
        _log("политика файлов прогона: cloud ×%d (правила %d + данные %d)"
             % (len(self.paths) + len(self.data_paths), len(self.paths),
                len(self.data_paths)))
        self._save()
        # Заморозка книги данных — тот же закон, что у документов правил.
        self.check_freeze()

    def stage_data_run(self) -> None:
        """Линия данных: проверки, скомпилированные из строк свода
        (модель-компилятор), отчёт 3.3 и его гейт. Без неё пакет идёт
        без clean-data.json, и матрица готовности стоит в stop."""
        if not self.data_paths:
            _log("книги данных нет — линия данных пропущена")
            self.state["stages"]["data_run"] = {"skipped": True}
            self._save()
            return
        t0 = time.time()
        r = self.client.post(f"/api/runs/{self.state['run']}/data-run",
                             params={"scope": "any"})
        if r.status_code != 200:
            raise RuntimeError(f"линия данных: {r.status_code} {r.text[:300]}")
        st = self.wait_data(("проверки идут",), 3600, "проверки данных")
        rep_path = self.run_dir() / "data-report" / "data-report.json"
        summary = {}
        if rep_path.is_file():
            doc = json.loads(rep_path.read_text(encoding="utf-8"))
            findings = doc.get("findings") or []
            summary = {"checks": len(doc.get("checks") or []),
                       "findings": len(findings),
                       "by_severity": {}}
            for f in findings:
                sev = str(f.get("severity") or "?")
                summary["by_severity"][sev] = summary["by_severity"].get(sev, 0) + 1
        _log(f"ЛИНИЯ ДАННЫХ {st.get('data_state')} за {time.time() - t0:.0f} с: "
             f"{json.dumps(summary, ensure_ascii=False)}; ошибка: {st.get('error')}")
        for pr in (st.get("data_report_problems") or [])[:10]:
            _log("   ! " + str(pr))
        # Отказ линии данных тоже судится СОСТОЯНИЕМ: пакет без отчёта
        # 3.3 идёт без clean-data, и матрица готовности встанет — но уже
        # на шаге, где причина не видна.
        if str(st.get("data_state")) != "отчёт готов":
            raise SystemExit(
                "СТОП: линия данных не завершилась (состояние %r): %s"
                % (st.get("data_state"), st.get("error")))
        self.state["stages"]["data_run"] = {"state": st.get("data_state"),
                                            "summary": summary,
                                            "sec": round(time.time() - t0)}
        self._save()

    def wait_data(self, busy_states: tuple, timeout_s: int, label: str) -> dict:
        """Ожидание стадии ЛИНИИ ДАННЫХ (свой ключ состояния)."""
        t0 = time.time()
        last = ""
        while True:
            st = self.status()
            state = str(st.get("data_state") or "")
            if state != last:
                _log(f"{label}: {state}")
                last = state
            if state not in busy_states:
                return st
            if time.time() - t0 > timeout_s:
                raise TimeoutError(f"{label}: превышено {timeout_s} с "
                                   f"(состояние {state!r})")
            time.sleep(5)

    def stage_extract(self) -> None:
        t0 = time.time()
        # Класс данных решает, КАКИМИ каналами пойдут перекрёстное и
        # второе чтение. Незаписанный файл строжайшим умолчанием
        # утягивает весь прогон в локальный класс, и стадия падает на
        # недоступной локальной модели через минуты работы. Проверяем
        # до старта и называем виновный файл.
        cfg = self.client.get(
            f"/api/runs/{self.state['run']}/files-config").json()
        loose = sorted(name for name, c in (cfg.get("files") or {}).items()
                       if c.get("processing") != "cloud")
        if loose:
            raise SystemExit(
                "СТОП: файлы прогона без облачной политики: %s — "
                "строжайший класс прогона стал бы локальным, и "
                "перекрёстное чтение ушло бы на локальную модель"
                % ", ".join(loose))
        r = self.client.post(f"/api/runs/{self.state['run']}/extract",
                             params={"scope": "any", "customer": self.customer})
        if r.status_code != 200:
            raise RuntimeError(f"извлечение: {r.status_code} {r.text[:300]}")
        st = self.wait(("извлечение идёт",), 3600, "извлечение")
        es = st.get("extract_summary") or {}
        _log(f"ИЗВЛЕЧЕНИЕ {st.get('state')} за {time.time() - t0:.0f} с: клауз "
             f"{es.get('clauses_fed')}, кандидатов {es.get('candidates')}, правил "
             f"{es.get('rules')}, вопросов {es.get('questions')}, целевых "
             f"{es.get('objectives')}, отбраковано {es.get('rejected')}; ошибка: "
             f"{st.get('error')}")
        if st.get("extract_problems"):
            for p in st["extract_problems"][:10]:
                _log("   ! " + p)
        self.state["stages"]["extract_status"] = {"state": st.get("state"),
                                                  "summary": es,
                                                  "sec": round(time.time() - t0)}
        self._save()
        model_path = self.run_dir() / "extract" / "model.json"
        # Судит СОСТОЯНИЕ стадии, а не наличие файла: отказ извлечения
        # оставляет на диске частичный свод, и проход шёл дальше как ни
        # в чём не бывало — цикл его выправлял, а серия засчитывала
        # проход, в котором стадия отказала (проход 5 серии 03.09:
        # отказ извлечения за 145 с, две мягкие посадки без цитаты, и
        # только гейт извлечения об этом сказал). Проход с отказавшей
        # стадией непригоден: причина отказа не устранена, а повторить
        # его нельзя.
        if str(st.get("state")) != "извлечено":
            raise SystemExit(
                "СТОП: извлечение не завершилось (состояние %r): %s"
                % (st.get("state"), st.get("error")))
        if not model_path.is_file():
            raise SystemExit("СТОП: извлечение не дало свода (%s): %s"
                             % (st.get("state"), st.get("error")))
        self.gate("extract", model_path)

    def stage_cycle(self) -> None:
        t0 = time.time()
        r = self.client.post(f"/api/runs/{self.state['run']}/full-cycle",
                             params={"auditor": "cloud", "worker": "cloud",
                                     "scope": "any", "max_rounds": self.max_rounds,
                                     "max_megacycles": self.max_megacycles})
        if r.status_code != 200:
            raise RuntimeError(f"цикл: {r.status_code} {r.text[:300]}")
        st = self.wait(("сквозной цикл идёт", "аудит-цикл идёт", "арбитраж идёт"),
                       7200, "цикл")
        ex = st.get("exchange_summary") or {}
        _log(f"ЦИКЛ {st.get('state')} за {time.time() - t0:.0f} с: вердикт "
             f"{ex.get('verdict')}, обменов {ex.get('rounds')}, история "
             f"{[h.get('weighty') for h in ex.get('history') or []]}, покрытие "
             f"{ex.get('coverage')}; ошибка: {st.get('error')}")
        # СЛУЖБА СЛОМАЛАСЬ — ЭТО СБОЙ, А НЕ ОТКАЗ ПО ПРАВИЛУ (замечание
        # С4 второго неавторского ревью REVIEW2-A07RUN-e67bef1c-COSMOS).
        # Нить сквозного цикла при исключении кладёт слова в общее поле
        # `error` (отказ стадии, недоступный каталог шаблонов); на
        # штатных финалах — и на остановке человеком — она ставит туда
        # None. Значит признак раннеру нужен ОДИН и общий, слов панели
        # он знать не обязан: есть `error` — стадия бросает, `main`
        # пишет исход «сбой» (`stage-exception`) и закрывает запись
        # состоянием «сбой». Бросок стоит ДО сохранения `cycle_status`
        # намеренно: сохранённый блок заставил бы продолжение прогона
        # (`--run`) считать стадию цикла пройденной и пропустить её.
        # Так же судит извлечение (`stage_extract` выше).
        if str(st.get("error") or "").strip():
            raise RuntimeError(
                "цикл: стадия панели отказала (состояние %r): %s"
                % (st.get("state"), str(st.get("error"))[:300]))
        # Отбраковки замков по раундам.
        exd = self.run_dir() / "exchange"
        locks = []
        for rdir in sorted(exd.glob("rounds/round-*")):
            pr = rdir / "patch-rejections.json"
            if pr.is_file():
                rows = json.loads(pr.read_text(encoding="utf-8"))
                for x in rows:
                    locks.append({"round": rdir.name, "answer": x.get("answer"),
                                  "reasons": x.get("reasons")})
        if locks:
            _log(f"отбраковок заплат кодом: {len(locks)}")
            for x in locks[:10]:
                _log(f"   × {x['round']} {x['answer']}: {'; '.join(x['reasons'] or [])[:160]}")
        # Исход цикла — по состоянию прогона и ЗАКОНУ РЕЕСТРА
        # (cdpm.exchange), не по модели: «лимит кругов» с живой
        # блокирующей находкой — не «чисто» (проверка плана №18/№21:
        # проход №20 выдан за чистый при открытом критическом). Судит
        # чистая функция cycle_status_of — своего перечня состояний
        # раннер не держит (A07, VERDICT-A07-ef0ac6f9 §1.5).
        cyc = cycle_status_of(exd, st.get("state"))
        live, blockers, clean = cyc["live_findings"], cyc["blockers"], cyc["clean"]
        if cyc.get("registry_refusal"):
            _log("ОТКАЗ ЧТЕНИЯ РЕЕСТРА: " + cyc["registry_refusal"])
        if not clean:
            _log(f"ЦИКЛ НЕ ЧИСТ: состояние {st.get('state')!r}, блокирующих "
                 f"находок реестра {len(blockers)}: {blockers[:5]}")
        # Живые НЕблокирующие (серьёзные, редакционные) вердикт прогона
        # не красят — их дорога бэклог панели, а не краснота стадии.
        warn = [f for f in live if f not in blockers]
        if warn:
            _log(f"предупреждение: живых неблокирующих находок реестра "
                 f"{len(warn)}: {warn[:5]}")
        self.state["stages"]["cycle_status"] = dict(
            cyc, exchange=ex, locks=locks, sec=round(time.time() - t0))
        self._save()
        summary = self.gate("cycle", self.run_dir() / "extract" / "model.json")
        if not clean:
            summary["verdict"] = "NOT_CLEAN_CYCLE:" + str(summary.get("verdict"))
            self.state["stages"]["cycle"] = summary
            self._save()
            _log("ГЕЙТ cycle перекрашен: цикл не чист — " + summary["verdict"])

    def stage_answers(self) -> None:
        """Книга ответов набора закрывает анкету (кругами, как у лестницы
        «Полярис»): анкета рендерится из канонической модели прогона тем
        же кодом, что у панели, ответы — только из машинной книги
        (несопоставленный вопрос — стоп, раннер ответов не выдумывает).
        Пустая книга при открытых вопросах — дефект набора со строгими
        вопросами (H0), судит гейт strict_questions."""
        t0 = time.time()
        book_rel = self.manifest.get("answer_book")
        book = json.loads((self.kit / book_rel).read_text(encoding="utf-8")) if book_rel else {}
        has_book = bool(book.get("exact") or book.get("prefix") or book.get("regex"))
        rounds = []
        for rnd in range(1, self.max_rounds + 1):
            st = self.status()
            open_q = (st.get("readiness") or {}).get("vmp_open_questions")
            if not open_q:
                break
            if not has_book:
                _log(f"АНКЕТА НЕ ПУСТА: открытых {open_q}, книга ответов набора пуста — "
                     f"ответы не применяются; на строгом наборе это дефект конвейера, "
                     f"судит гейт")
                model = json.loads((self.run_dir() / "extract" / "model.json")
                                   .read_text(encoding="utf-8"))
                for line in question_lines(model.get("questions")):
                    _log(line)
                break
            sys.path.insert(0, str(REPO / "tools"))
            import nano_regression as nr
            from cdpm import anketa_xlsx
            model = json.loads((self.run_dir() / "extract" / "model.json")
                               .read_text(encoding="utf-8"))
            anketa = self.out / f"anketa-round-{rnd}.xlsx"
            anketa_xlsx.render_anketa_xlsx(model, anketa)
            filled_path = self.out / f"book-round-{rnd}.xlsx"
            filled, unmatched = nr.fill_book(anketa, book, filled_path)
            if unmatched:
                (self.out / f"unmatched-round-{rnd}.txt").write_text(
                    "\n".join(unmatched) + "\n", encoding="utf-8")
                raise SystemExit("СТОП: вопросы без ответа в машинной книге (%d): %s"
                                 % (len(unmatched), "; ".join(u[:120] for u in unmatched[:6])))
            if not filled:
                raise SystemExit(f"СТОП: открытых {open_q}, а заполнять нечего")
            r = self.client.post(f"/api/runs/{self.state['run']}/answers",
                                 params={"scope": "any"},
                                 files=[("file", (f"book-{rnd}.xlsx", filled_path.read_bytes(),
                                                  "application/octet-stream"))])
            if r.status_code != 200:
                raise RuntimeError(f"подача книги: {r.status_code} {r.text[:300]}")
            st = self.wait(("применение ответов идёт",), 3600, f"ответы, круг {rnd}")
            rounds.append({"round": rnd, "filled": filled, "open_before": open_q,
                           "state": st.get("state")})
            _log(f"ОТВЕТЫ круг {rnd}: подано {filled} (открытых было {open_q}) → "
                 f"{st.get('state')}; ошибка: {st.get('error')}")
            # Исход круга судится СОСТОЯНИЕМ, как у извлечения: отказ
            # применения, красный гейт ответов панели или остановка —
            # стоп прохода (иначе та же книга подавалась бы повторно
            # платными вызовами моделей, а красный гейт при закрытой
            # анкете уходил бы в пакет незамеченным — ревью H0-К 04.09).
            # Строка состояния — ОДНА именованная константа модуля
            # (замечание Н5 второго ревью: копий литерала было две);
            # придёт предикат закона — менять будет одно место.
            if str(st.get("state")) != ANSWERS_APPLIED_STATE:
                self.state["stages"]["answers_status"] = {
                    "rounds": rounds, "book": True, "stop": st.get("state"),
                    "error": st.get("error"), "sec": round(time.time() - t0)}
                self._save()
                raise SystemExit("СТОП: применение ответов (круг %d) завершилось "
                                 "состоянием %r: %s"
                                 % (rnd, st.get("state"), st.get("error")))
        st = self.status()
        open_after = (st.get("readiness") or {}).get("vmp_open_questions")
        # Закрытие анкеты судится фактом, а не числом кругов: круг,
        # закрывший последний вопрос, — успех, даже если он был последним
        # разрешённым (for/else давал здесь ложный стоп — ревью 04.09).
        if open_after and has_book:
            raise SystemExit(f"СТОП: анкета не закрылась за {self.max_rounds} кругов "
                             f"(открытых {open_after})")
        self.state["stages"]["answers_status"] = {
            "rounds": rounds, "book": bool(has_book),
            "open_after": open_after,
            "sec": round(time.time() - t0)}
        self._save()
        summary = self.gate("answers", self.run_dir() / "extract" / "model.json")
        # ПЕРЕСУД ЦИКЛА ПОСЛЕ ОТВЕТОВ (квитанция аудитора
        # A07RUN-REVIEW-RECEIPT-20260912-GPT, регрессии 5 и 6): ответы
        # законно разрешают прежние находки, и ожидания набора судятся
        # по актуальным свидетельствам. Реестр читается ЗАНОВО тем же
        # загрузчиком продукта.
        # ЗАЯВКА ИСХОДА БЕРЁТСЯ У САМОГО ЦИКЛА, а не у состояния после
        # ответов (замечание С2 второго неавторского ревью
        # REVIEW2-A07RUN-e67bef1c-COSMOS): «ответы применены» говорит о
        # закрытой анкете и о цикле не утверждает ничего, а прежний
        # выбор делал «чистым» цикл, кончившийся красным гейтом,
        # остановкой или лимитом кругов. Меняется между двумя чтениями
        # только реестр — значит честно судится ровно то, что ответы и
        # могли изменить: закрытые блокеры дают зелень, новые красноту,
        # незаявивший исхода цикл остаётся `cycle-incomplete`.
        # Блок цикла НЕ переписывается — пересуд ложится своим ключом
        # внутрь сводки стадии ответов: прежний исход остаётся правдой
        # о своём моменте, а в журнале видно оба.
        claim = ((self.state.get("stages") or {}).get("cycle_status")
                 or {}).get("state")
        after = cycle_status_of(self.run_dir() / "exchange", claim)
        summary["cycle_status"] = after
        self.state["stages"]["answers"] = summary
        self._save()
        row = cycle_outcome(after)
        if row is not None:
            _log(f"ПЕРЕСУД ЦИКЛА ПОСЛЕ ОТВЕТОВ: {row[0]} ({row[1]}) — {row[2]}")
        else:
            _log("ПЕРЕСУД ЦИКЛА ПОСЛЕ ОТВЕТОВ: чисто по Б/К "
                 f"(заявка цикла {claim!r}, живых находок "
                 f"{after.get('live_n')})")

    def _catalog_requests_status(self):
        """Раздел «Пробелы каталога шаблонов» прогона по ручке панели —
        для ожидания catalog_requests (Н0-C, фаза 1); нет ответа — None."""
        try:
            r = self.client.get(f"/api/runs/{self.state['run']}/catalog-requests")
        except Exception:                                    # noqa: BLE001
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def fetch_record(self, man=None):
        """Единая запись о прогоне у ХОЗЯИНА — ручкой записи панели.
        Адрес берётся из манифеста пакета (`record_api`), база — своя
        (`record_path`); поля в манифесте нет — путь по имени прогона.

        Ручка не ответила, ответила не 200 или ответ не запись — None:
        ожидание `record_stitched` тогда краснеет СЛОВАМИ, а не молчит."""
        path = record_path((man or {}).get("record_api"),
                           (getattr(self, "state", None) or {}).get("run"))
        if not path:
            return None
        try:
            r = self.client.get(path)
        except Exception as e:                            # noqa: BLE001
            _log(f"предупреждение: ручка записи не ответила ({path}): {e}")
            return None
        if r.status_code != 200:
            _log(f"предупреждение: ручка записи ответила {r.status_code} "
                 f"({path})")
            return None
        try:
            doc = r.json()
        except ValueError:
            _log(f"предупреждение: ручка записи ответила не JSON ({path})")
            return None
        return doc if isinstance(doc, dict) else None

    def fetch_tree(self):
        """Дерево прогонов по ЭТОМУ прогону — ручкой панели
        `GET /api/runs/<прогон>/tree` (шаг 3, раздел 12.2 проекта):
        из ответа берутся `compare_previous` и `compare_golden`.

        Ручку делает половина CDPM; пока её нет (404), пока служба
        молчит или отвечает не записью — None, и сравнения в итогах
        остаются ПУСТЫМИ. Летопись прогон не роняет, и сравнения,
        которого не было, раннер не выдумывает."""
        path = tree_path((getattr(self, "state", None) or {}).get("run"))
        client = getattr(self, "client", None)
        if not path or client is None:
            return None
        empty = ("— сравнения с предыдущим и золотым в итогах записи "
                 "остаются пустыми")
        try:
            r = client.get(path)
        except Exception as e:                            # noqa: BLE001
            _log(f"предупреждение: ручка дерева прогонов не ответила "
                 f"({path}): {e} {empty}")
            return None
        if r.status_code != 200:
            _log(f"предупреждение: ручка дерева прогонов ответила "
                 f"{r.status_code} ({path}) {empty}")
            return None
        try:
            doc = r.json()
        except ValueError:
            _log(f"предупреждение: ручка дерева прогонов ответила не JSON "
                 f"({path}) {empty}")
            return None
        return doc if isinstance(doc, dict) else None

    def _package_sha(self) -> str | None:
        """sha256 собранного пакета постановки — для сверки с машинным
        отчётом нажатий COSMOS (тот же пакет или другой)."""
        path = (self.state.get("stages", {}).get("package_status")
                or {}).get("path")
        if not path or not Path(path).is_file():
            return None
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def check_expect(self, man, model: dict, summary: dict) -> None:
        """Ожидания прогона из манифеста набора (ключ expect) против
        пакета и модели: вопросы анкеты, режимы, лестница, состояние
        ячейки optimization, сообщение о режиме, число целевых, вердикт
        непротиворечивости пакета; для наборов-вариаций Н0 — ключи
        extra_expect_problems. `man` None — пакет не собран (стоп
        прогона): судятся только модель и ручки.
        Расхождение перекрашивает вердикт гейта пакета.

        Итог различает ТРИ вещи (поручение владельца 10.09.2026):
        `checked` — ключи, которые раннер судил САМ; `not_judged` —
        ключи с причиной, почему раннер их не судит (например числа
        сверки с ИДАК: они получаются нажатиями в COSMOS, а не прогоном
        CDPM, — их судит только машинный отчёт нажатий --cosmos-press);
        `problems` — расхождения. «Ожидания выполнены» ставится ТОЛЬКО
        при пустых problems, и несудимые ключи при этом названы явно."""
        exp = self.manifest.get("expect")
        if not isinstance(exp, dict):
            return
        from cdpm import model as m
        man_raw = man
        man = man or {}
        problems: list[str] = []
        qs = [q for q in (model.get("questions") or []) if isinstance(q, dict)]
        ids = {str(q.get("id")) for q in qs}
        want_q = set(exp.get("questions") or [])
        # `questions_optional` — вопросы, которые модель ВПРАВЕ задать, но не
        # обязана (зависят от пересказа модели; Н0-С 17.09.2026): сверх
        # ожидаемых они не считаются, а их отсутствие — не расхождение.
        optional_q = set(exp.get("questions_optional") or [])
        if "questions" in exp:
            if ids - want_q - optional_q:
                problems.append(f"вопросы сверх ожидаемых: {sorted(ids - want_q - optional_q)}")
            if want_q - ids:
                problems.append(f"ожидаемые вопросы не заданы: {sorted(want_q - ids)}")
            open_ids = [str(q.get("id")) for q in qs if m.is_open_question(q)]
            if open_ids:
                problems.append(f"открытые вопросы: {open_ids}")
        matrix = m.readiness_matrix(model)
        columns = (man.get("readiness_matrix") or {}).get("columns") or {}
        for task, modes in (exp.get("allowed_modes") or {}).items():
            got = (man.get("allowed_modes") or {}).get(task) or []
            if set(got) != set(modes):
                problems.append(f"allowed_modes[{task}] = {got}, ожидалось {modes}")
        for task, state in (exp.get("readiness_ladder") or {}).items():
            got = (columns.get(task) or {}).get("state")
            if got != state:
                problems.append(f"лестница[{task}] = {got!r}, ожидалось {state!r}")
        if exp.get("optimization_state"):
            for task, cell in matrix.items():
                got = (cell.get("optimization") or {}).get("state")
                if got != exp["optimization_state"]:
                    problems.append(f"матрица[{task}].optimization = {got!r}, "
                                    f"ожидалось {exp['optimization_state']!r}")
        if exp.get("note_contains"):
            sub = str(exp["note_contains"]).lower()
            for task, cell in matrix.items():
                note = str((cell.get("optimization") or {}).get("note") or "")
                if sub not in note.lower():
                    problems.append(f"матрица[{task}]: в заметке нет «{sub}»: {note[:120]!r}")
            for task, col in columns.items():
                notes = " ".join(str(x) for x in (col.get("notes") or []))
                if sub not in notes.lower():
                    problems.append(f"лестница[{task}]: в notes пакета нет «{sub}»: "
                                    f"{notes[:120]!r}")
        if "objectives" in exp:
            n = len(model.get("objectives") or [])
            if n != int(exp["objectives"]):
                problems.append(f"целевых {n}, ожидалось {exp['objectives']}")
        # Спецификация 1.17: раздел истории сотрудника в манифесте —
        # наличие и полнота по сотрудникам (набор H0-КИ, ступень L).
        if exp.get("history"):
            h = exp["history"]
            got = man.get("history")
            if bool(got) != bool(h.get("present", True)):
                problems.append(f"manifest.history: {'есть' if got else 'нет'}, "
                                f"ожидалось {'есть' if h.get('present', True) else 'нет'}")
            elif got:
                cov = got.get("coverage") or {}
                for key in ("employees_total", "with_history"):
                    if key in h and cov.get(key) != h[key]:
                        problems.append(f"history.coverage.{key} = {cov.get(key)}, "
                                        f"ожидалось {h[key]}")
        # Непротиворечивость постановки (гейт выпуска CDPM, 11.09.2026):
        # вердикт кладёт в манифест пакета сам гейт, раннер его только
        # сверяет со словом набора. Ожидания у набора нет — не судится.
        problems += consistency_problems(exp, man_raw)
        needs_requests = isinstance(exp.get("catalog_requests"), dict)
        problems += extra_expect_problems(
            exp, model, man_raw,
            self._catalog_requests_status() if needs_requests else None,
            etalon=getattr(self, "etalon", None))
        # Числа COSMOS, сшивка записи и признак золотого судятся ТОЛЬКО с
        # машинным отчётом нажатий: и то, и другое, и третье получается
        # НАЖАТИЯМИ в COSMOS (золотой — ещё и словом владельца после
        # приёмки). Без отчёта все три ключа честно уезжают в «не судится
        # раннером». Запись у хозяина читается ОДИН раз на оба ключа,
        # которым она нужна.
        warnings: list[str] = []
        judged_extra: tuple = ()
        if self.cosmos_press is not None:
            judged_extra = ("cosmos_numbers", "record_stitched",
                            "golden_by_owner_only")
            got, warnings = cosmos_numbers_problems(
                exp, self.cosmos_press, self._package_sha())
            problems += got
            keeper = self.fetch_record(man_raw) \
                if (isinstance(exp.get("record_stitched"), dict)
                    or isinstance(exp.get("golden_by_owner_only"), dict)) \
                else None
            problems += record_stitched_problems(exp, keeper)
            problems += golden_by_owner_only_problems(
                exp, keeper, getattr(self, "record", None))
        # Вердикты стадий — по манифесту комплекта, а не по зашитым
        # умолчаниям: у Н0-B извлечение «не эквивалентно до ответов» по
        # замыслу. Стадии CDPM судятся всегда, стадии COSMOS — только с
        # отчётом нажатий (без него предупреждение, а не краснота).
        got_stages, warn_stages = stage_verdicts_problems(
            exp, self.state.get("stages") or {}, self.cosmos_press)
        problems += got_stages
        warnings += warn_stages
        # Чистота сквозного цикла — ЗАКОН реестра, а не свойство
        # набора: судится у всех и без объявления в манифесте. Иначе
        # отказ чтения реестра и живая блокирующая находка красили бы
        # ТОЛЬКО вердикт гейта cycle, а ожидания набора оставались бы
        # «выполнены» (замечание С1 неавторского ревью).
        problems += cycle_clean_problems(self.state.get("stages") or {})
        judged, not_judged = expect_key_verdicts(exp, judged_extra)
        summary["expect"] = {"problems": problems, "checked": judged,
                             "not_judged": not_judged, "warnings": warnings,
                             "cosmos_press": str(self.cosmos_press_path)
                             if self.cosmos_press_path else None}
        if problems:
            summary["verdict"] = "EXPECT_MISMATCH:" + str(summary.get("verdict"))
            _log(f"ОЖИДАНИЯ НАБОРА НЕ ВЫПОЛНЕНЫ ({len(problems)}):")
            for p in problems:
                _log("   ! " + p)
        else:
            _log("ОЖИДАНИЯ НАБОРА выполнены; ПРОВЕРЕНО раннером: "
                 + (", ".join(judged) or "ничего"))
        for key, why in sorted(not_judged.items()):
            _log(f"   ~ НЕ СУДИТСЯ РАННЕРОМ: {key} — {why}")
        for w in warnings:
            _log("   ? ПРЕДУПРЕЖДЕНИЕ: " + w)
        self.state["stages"]["package"] = summary
        self._save()

    def stage_package(self) -> None:
        model = json.loads((self.run_dir() / "extract" / "model.json").read_text(encoding="utf-8"))
        qs = model.get("questions") or []
        strict = bool(self.manifest.get("strict_questions", True))
        if qs and strict:
            _log(f"АНКЕТА НЕ ПУСТА: вопросов {len(qs)} — на строгом наборе (ИД H0-ВМП) "
                 f"это дефект конвейера")
            for line in question_lines(qs):
                _log(line)
        else:
            # Вопрос части 1 «противоречие» показывается и на нестрогом
            # наборе: он блокирует строки постановки, и молчать о нём
            # нельзя. Только показ — судить его раннеру нечем.
            contra = [q for q in qs if isinstance(q, dict)
                      and isinstance(q.get("contradiction"), dict)]
            if contra:
                _log(f"ВОПРОСЫ О ПРОТИВОРЕЧИИ: {len(contra)} — часть 1 "
                     f"блокирует строки постановки")
                for line in question_lines(contra):
                    _log(line)
        r = self.client.get(f"/api/runs/{self.state['run']}/export/task-package")
        if r.status_code != 200:
            _log(f"ПАКЕТ НЕ СОБРАН: {r.status_code} {r.text[:400]}")
            # Отказ выпуска с добавочным ключом `code` (гейт
            # непротиворечивости CDPM, 11.09.2026) называется СЛОВАМИ;
            # отказ без кода — прежнее поведение, слова те же.
            refusal = package_refusal(r.status_code, r.text)
            if refusal["words"]:
                _log("   ! " + refusal["words"])
            self.state["stages"]["package_status"] = {"status": r.status_code,
                                                      "error": r.text[:400],
                                                      "code": refusal["code"],
                                                      "words": refusal["words"]}
            self._save()
            summary = self.gate("package", self.run_dir() / "extract" / "model.json")
            # Стоп прогона — тоже предмет ожиданий набора (Н0-C, фаза 1:
            # пробел покрытия, заявка в каталог, пакет не собран).
            self.check_expect(None, model, summary)
            return
        pkg = self.out / f"{self.state['run']}-task-package.zip"
        pkg.write_bytes(r.content)
        import zipfile
        with zipfile.ZipFile(pkg) as z:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
        _log(f"ПАКЕТ собран: {pkg.name}, покрытие {man.get('coverage')}, форма "
             f"{man.get('form', {}).get('problems')}, исключённых "
             f"{man.get('excluded_until_data')}")
        _log(f"   данные в пакете: {man.get('has_data')}; готовность "
             f"{json.dumps(man.get('readiness_matrix'), ensure_ascii=False)[:300]}; "
             f"измерение {json.dumps(man.get('measure', {}).get('counts'), ensure_ascii=False)[:200]}")
        # Вердикт гейта непротиворечивости — из манифеста пакета
        # (11.09.2026); блока нет — строки нет (старый пакет).
        words = consistency_words(man)
        if words:
            _log("   " + words)
        self.state["stages"]["package_status"] = {"path": str(pkg),
                                                  "coverage": man.get("coverage"),
                                                  "form_problems": man.get("form", {}).get("problems")}
        self._save()
        summary = self.gate("package", self.run_dir() / "extract" / "model.json", pkg)
        self.check_expect(man, model, summary)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--signals-dir", default=None,
                    help="scratchpad COSMOS: копии моделей/отчётов по тегам")
    ap.add_argument("--run", default=None, help="продолжить существующий прогон")
    ap.add_argument("--until", choices=STAGES, default="package")
    ap.add_argument("--from-stage", choices=STAGES, default=None)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--max-megacycles", type=int, default=3)
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="дымовой прогон на незакоммиченном коде (не серия)")
    ap.add_argument("--base-url", default=None,
                    help="режим площадки: адрес штатной службы панели CDPM "
                         "(например http://127.0.0.1:8097); без ключа — панель в процессе")
    ap.add_argument("--runs-dir", default=None,
                    help="каталог прогонов службы (с --base-url): артефакты прогона "
                         "читаются оттуда, например <данные>\\cdpm-runs")
    ap.add_argument("--kit-dir", default=str(HERE),
                    help="каталог комплекта с kit-manifest.json (умолчание — H0; "
                         "open-bench/polyaris-h0k — ИДАК H0-К без критерия)")
    ap.add_argument("--after-catalog-release", action="store_true",
                    help="продолжение ТОГО ЖЕ прогона после выпуска шаблона (Н0-С "
                         "одним прогоном): смена json-данных каталога, привязок и "
                         "эталонов между стадиями законна и пишется в состояние; "
                         "любой изменённый .py — по-прежнему стоп")
    ap.add_argument("--cosmos-press", default=None,
                    help="машинный отчёт нажатий COSMOS (json): с ним раннер "
                         "судит ожидание cosmos_numbers — вердикты, "
                         "сертификаты и нарушения сверки; без него ключ "
                         "честно называется несудимым")
    ap.add_argument("--catalog", default=None,
                    help="папка каталога правил ЭТОГО прогона (ставит "
                         "CREW_TEMPLATE_CATALOG); без ключа — каталог "
                         "контура либо каталог дерева, и паспорт называет "
                         "это словами")
    ap.add_argument("--bindings", default=None,
                    help="файл привязок исполнителей COSMOS под этот каталог "
                         "(ставит CREW_COSMOS_BINDINGS); без ключа привязки "
                         "дерева годятся ТОЛЬКО для каталога дерева, иначе "
                         "отказ до старта")
    ap.add_argument("--solver-url", default=None,
                    help="адрес решателя ЭТОГО прогона (ставит "
                         "CREW_SOLVER_URL); стадиям CDPM решатель не нужен — "
                         "паспорт помечает это честно")
    ap.add_argument("--passport-only", action="store_true",
                    help="собрать и сверить паспорт прогона, ничего не "
                         "запуская: код 0 — сошёлся, 2 — расхождения")
    ap.add_argument("--purpose", choices=rr.PURPOSES, default=PURPOSE_DEFAULT,
                    help="цель прогона в записи о прогоне (умолчание — "
                         "регресс)")
    ap.add_argument("--contour", choices=rr.CONTOURS, default=None,
                    help="площадка прогона в записи о прогоне (умолчание — "
                         "local: панель в процессе); адреса служб едут в "
                         "запись из паспорта")
    ap.add_argument("--cosmos-url", default=None,
                    help="адрес COSMOS для ручки среды " + COSMOS_ENV_ROUTE
                         + ": его модели по ролям (сертификатор, читатель "
                         "ИДАК) ложатся в ГОЛОВУ записи словом system: "
                         "cosmos. Без ключа — переменная контура "
                         + COSMOS_URL_ENV + " (та же, куда уходит пакет "
                         "постановки); ни того ни другого — голова несёт "
                         "только модели CDPM и слова models_note")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # Двери контура — ДО построения раннера: панель в процессе и гейт
    # эквивалентности обязаны читать каталог и привязки ЭТОГО прогона.
    apply_doors(a.catalog, a.bindings, a.solver_url)
    if a.passport_only:
        return passport_only(out, a)
    rn = Runner(out, Path(a.signals_dir) if a.signals_dir else None,
                a.max_rounds, a.max_megacycles, allow_dirty=a.allow_dirty,
                base_url=a.base_url, runs_dir=Path(a.runs_dir) if a.runs_dir else None,
                kit_dir=Path(a.kit_dir),
                cosmos_press=Path(a.cosmos_press) if a.cosmos_press else None,
                after_catalog_release=a.after_catalog_release)
    if a.preflight:
        return 0 if rn.preflight() else 1
    if a.run:
        rn.state["run"] = a.run
    # Паспорт прогона — ДО стадии create: параметры называются и
    # сверяются раньше, чем потрачен первый вызов модели.
    rn.make_passport(until=a.until, catalog_key=a.catalog,
                     bindings_key=a.bindings, solver_key=a.solver_url)
    start = a.from_stage or ("create" if not rn.state.get("run") else
                             next((s for s in STAGES[1:]
                                   if s not in rn.state["stages"]
                                   and f"{s}_status" not in rn.state["stages"]), "package"))
    plan = list(STAGES[STAGES.index(start):STAGES.index(a.until) + 1])
    # Запись о прогоне — ДО стадии create, следом за паспортом: голова
    # записи и есть объявленные им параметры.
    rn.make_record(a, plan)
    for stage in plan:
        _log(f"=== СТАДИЯ {stage} ===")
        rn.guard_code(stage)
        started_at = rr.now_iso()
        try:
            getattr(rn, f"stage_{stage}")()
        except KeyboardInterrupt:
            rn.record_stage(stage, started_at, rr.now_iso(), rr.OUTCOME_HUMAN,
                            "interrupted", "прогон прерван человеком")
            rn.finish_record(rr.STATE_HUMAN_STOP)
            raise
        except BaseException as e:                        # noqa: BLE001
            rn.record_stage(stage, started_at, rr.now_iso(),
                            rr.OUTCOME_FAILURE, "stage-exception",
                            f"{type(e).__name__}: {e}"[:300])
            rn.finish_record(rr.STATE_FAILURE)
            raise
        rn.record_stage(stage, started_at, rr.now_iso(),
                        *stage_outcome(rn.state, stage))
    rn.finish_record()
    _log("ИТОГ: " + json.dumps({k: (v.get("verdict") if isinstance(v, dict) else v)
                               for k, v in rn.state["stages"].items()
                               if k in STAGES}, ensure_ascii=False))
    # Отказ выпуска пакета с кодом (гейт непротиворечивости CDPM) — в
    # ИТОГЕ словами: вердикты стадий его не показывают, а «пакета нет»
    # без причины — половина правды.
    refusal = (rn.state.get("stages") or {}).get("package_status") or {}
    if isinstance(refusal, dict) and refusal.get("words"):
        _log("ИТОГ: " + str(refusal["words"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
