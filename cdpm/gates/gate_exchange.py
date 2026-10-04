# -*- coding: utf-8 -*-
"""Гейт аудит-цикла CDPM (пакет работ АЦ-2).

Проверяет каталог завершённого цикла:

0. ФОРМА АРТЕФАКТОВ (A07-X02): файлы каталога недоверенные, и ни один
   не вправе уронить гейт трассировкой — артефакт негодной формы
   получает ОДИН названный отказ («артефакт <имя>: не объект / поле
   «…» не список / записи поля «…» не объекты»), круг с ним не
   судится, проверка идёт дальше либо останавливается СЛОВАМИ;
1. самотест: валидаторы контрактов ловят контрольные дефекты;
2. по каждому обмену: пакет замечаний валиден против ВХОДНОЙ версии
   свода обмена; ответ полон (ни одного замечания без ответа); метрика
   пересчитана и совпала с записанной;
3. реестр: повторная проводка всех обменов даёт ровно записанный
   реестр — жизненный цикл находок не подделан;
4. diff-only между соседними версиями: изменены только строки принятых
   патчей (за вычетом отбракованных);
5. регресс-токены живы в финальной версии;
6. вердикт цикла соответствует правилу останова по истории метрик И
   терминальному реестру (A07, контракт П0 §1.2): «успех» невозможен
   при живой блокирующей/критической находке реестра; сводка
   `registry_verdict` в verdict.json сходится с пересчётом по тому же
   реестру; версия схемы реестра известна (registry/2 либо старая
   форма без поля); реестр ОБЯЗАН существовать (A07-R5) — гейт зовут
   на ЗАВЕРШЁННЫЙ цикл, и исчезнувший registry.json красит гейт
   словами, а не читается пустым реестром.

Печатает число рассмотренных объектов (У-05) и ВЕРДИКТ; красный гейт —
код возврата 1.

Запуск (полными путями, из любого каталога):
    python -m cdpm.gates.gate_exchange КАТАЛОГ_ЦИКЛА
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cdpm import exchange as ex, model as m


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


#: Поля-перечни версии свода, по которым гейт ходит напрямую
#: (`{r["id"] for r in doc.get("rules", [])}` и т. п.).
_MODEL_LISTS = ("rules", "questions", "sources")


def _form_problems(name: str, value, lists: tuple[str, ...] = ()) -> list[str]:
    """ЗАКОН ФОРМЫ АРТЕФАКТА (A07-X02, остаток шестого неавтор-ревью:
    «след вместо слов»). Гейт читает НЕДОВЕРЕННЫЕ файлы каталога, и ни
    один из них не вправе уронить проверку трассировкой: артефакт
    негодной формы получает ОДИН НАЗВАННЫЙ отказ, а проверка идёт
    дальше либо останавливается СЛОВАМИ.

    Прежде отказ формы пролезал внутрь контрактов и падал там:
    `remarks.json` со строкой вместо перечня разбирался по буквам и
    ронял `validate_remarks` («'str' object has no attribute 'get'»),
    версия свода со строкой вместо объекта — `doc.get("rules")`, а
    `"rules": "abc"` — `check_diff_only` («string indices must be
    integers»). Аналитик получал стек вместо причины, а гейт —
    неопределённый исход вместо красного.

    Судится РОВНО то, по чему гейт ходит: корень — объект; названные
    поля, если они есть и не пусты (`null` = «поля нет»), — перечни
    объектов. Содержание артефакта здесь не судится: это работа
    контрактов `ex.validate_*` и повторной проводки."""
    if not isinstance(value, dict):
        return [f"артефакт {name}: не объект: {type(value).__name__}"]
    out: list[str] = []
    for field in lists:
        got = value.get(field)
        if got is None:
            continue
        if not isinstance(got, list):
            out.append(f"артефакт {name}: поле «{field}» не список: "
                       f"{type(got).__name__}")
            continue
        bad = [repr(x)[:40] for x in got if not isinstance(x, dict)]
        if bad:
            out.append(f"артефакт {name}: записи поля «{field}» не "
                       f"объекты: " + ", ".join(bad[:3]))
    return out


def _canon(value):
    """Каноническое сравнение версий свода: обе стороны — через JSON
    (кортежи и множества кода становятся тем же, чем они стали бы на
    диске). Сравниваются ЗНАЧЕНИЯ, а не байты файла: порядок ключей и
    отступы записи к закону не относятся."""
    return json.loads(json.dumps(value, ensure_ascii=False,
                                 sort_keys=True, default=str))


def _frozen_context(out_dir: Path) -> tuple[dict | None, str]:
    """ЗАМОРОЖЕННЫЙ ВХОД цикла для повторной проводки (A07-R1): клаузы,
    якоря и записи комплекта — те же, по которым цикл судил заплаты.

    Комплект лежит ВНЕ каталога обмена: у прогона это `<прогон>/freeze`
    (сосед каталога `exchange`, он же сосед архивной ревизии
    `exchange-rev-NNN`), а цикл, кроме того, оставляет квитанцию
    `replay-context.json` с ОТНОСИТЕЛЬНЫМ адресом комплекта и
    отпечатком клауз. Ищем по квитанции и рядом с прогоном; найденный
    комплект сверяется с отпечатком квитанции — подменённый комплект
    назван причиной, а не молча даёт другой результат проводки.

    Возвращает (контекст | None, слова причины). Нет комплекта —
    СУДИТЬ НЕЧЕМ: круг с заплатами такой гейт красит словами
    (fail-closed), а не пропускает зелёным."""
    receipt, recorded_digest = None, None
    rpath = out_dir / ex.REPLAY_CONTEXT_FILE
    if rpath.is_file():
        try:
            receipt = _load(rpath)
        except (OSError, ValueError):
            receipt = None
    base = out_dir.resolve().parent
    candidates = []
    if isinstance(receipt, dict):
        recorded_digest = receipt.get("digest")
        if receipt.get("kit"):
            candidates.append(Path(base, str(receipt["kit"])))
    candidates.append(out_dir.parent / "freeze")
    tried = []
    for cand in candidates:
        tried.append(str(cand))
        if not (cand / "manifest.json").is_file():
            continue
        try:
            from cdpm import extract as extract_mod
            context = ex.frozen_context(extract_mod._load_kit(cand))
        except Exception as e:                            # noqa: BLE001
            return None, (f"комплект заморозки {cand} не читается: "
                          f"{str(e)[:120]}")
        if recorded_digest and ex.context_digest(context) != recorded_digest:
            return None, (f"комплект заморозки {cand} не тот, по которому "
                          f"шёл цикл: отпечаток клауз не совпадает с "
                          f"квитанцией {ex.REPLAY_CONTEXT_FILE}")
        return context, ""
    return None, ("комплект заморозки не найден (искали: "
                  + "; ".join(tried[:3]) + ")")


def _selftest() -> None:
    from cdpm.sample import sample_model
    doc = sample_model()
    bad = {"round": 1, "remarks": [{
        "id": "X", "severity": "КРИТИЧЕСКОЕ", "defect_class": "искажение",
        "target": {"rule": doc["rules"][0]["id"]},
        "source_refs": [{"sid": "S1", "anchor": "П.1.1"}],
        "text": "т", "evidence": ""}]}
    assert any("без доказательства" in p
               for p in ex.validate_remarks(bad, doc)), \
        "самотест: замечание без цитаты не поймано"
    assert any("без ответа" in p for p in ex.validate_response(
        {"answers": []}, bad)), "самотест: дыра в ответах не поймана"
    assert ex.decide([{"weighty": (0, 0, 0)},
                      {"weighty": (0, 0, 0)}], 9, {}) == "успех"
    # A07: та же чистая история при живой критической находке реестра
    # — «не закрыто», не «успех».
    live = {"findings": [{"id": "F1", "key": "k", "severity": "КРИТИЧЕСКОЕ",
                          "state": "патч отбракован", "opened_round": 1,
                          "closed_round": None}]}
    assert ex.decide([{"weighty": (0, 0, 0)}, {"weighty": (0, 0, 0)}],
                     9, registry=live) == ex.VERDICT_NOT_CLOSED, \
        "самотест: живая критическая находка не блокирует успех"


def check(out_dir: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    c = {"rounds": 0, "remarks": 0, "answers": 0, "versions": 0,
         "findings": 0}
    verdict_path = out_dir / "verdict.json"
    if not verdict_path.is_file():
        return [f"вердикт {verdict_path} не существует"], c
    # A07-X02: форма сводки судится ДО всякого её использования.
    # Сводка — корень проверки (по ней считается число обменов), и
    # негодная форма здесь останавливает гейт СЛОВАМИ, а не роняет его.
    try:
        summary = _load(verdict_path)
    except ValueError as e:
        return [f"артефакт verdict.json: не читается как JSON: "
                f"{str(e)[:120]}"], c
    #: Артефакты, чья форма уже названа, второй раз не называются:
    #: закон «один артефакт — один отказ» (финальная версия свода
    #: судится и обменом, и хвостом проверки).
    bad_form: set[str] = set()

    def _form(name: str, value, lists: tuple[str, ...] = ()) -> list[str]:
        if name in bad_form:
            return []
        out = _form_problems(name, value, lists)
        if out:
            bad_form.add(name)
        return out

    form = _form("verdict.json", summary, ("history", "deadlocks"))
    if form:
        return form, c
    n_rounds = summary.get("rounds", 0)
    if not isinstance(n_rounds, int) or isinstance(n_rounds, bool) \
            or n_rounds < 0:
        return [f"артефакт verdict.json: поле «rounds» не целое "
                f"неотрицательное: {n_rounds!r}"], c

    # A07-F4: форма снимка старта судится ТЕМ ЖЕ загрузчиком, что у
    # цикла (`ex.load_registry`): неизвестная версия схемы и неполная
    # новая форма красят гейт словами, а не проезжают в повторную
    # проводку. A07-F3: загрузчик же делает миграцию чтения — старая
    # техническая отбраковка замком кода в реплее живая, как и в
    # записи (обе стороны сравнения судятся одним законом).
    try:
        registry_replay = ex.load_registry(out_dir / "registry-start.json")
    except ValueError as e:
        problems.append(str(e))
        registry_replay = {}
    # A07-R1: замороженный вход повторной проводки — один на цикл.
    context, context_reason = _frozen_context(out_dir)
    # Круг, который проводкой не судился (нет комплекта, отказ формы),
    # делает бессмысленной и сверку итогового реестра: её ждёт не
    # «сходится», а уже названная причина.
    replay_ok = True
    history = []
    for rnd in range(1, n_rounds + 1):
        rdir = out_dir / "rounds" / f"round-{rnd:02d}"
        c["rounds"] += 1
        try:
            pack = _load(rdir / "remarks.json")
            resp = _load(rdir / "response.json")
            metrics = _load(rdir / "metrics.json")
            old_doc = _load(out_dir / "versions" /
                            f"model-v{rnd - 1:02d}.json")
            new_doc = _load(out_dir / "versions" /
                            f"model-v{rnd:02d}.json")
        except FileNotFoundError as e:
            problems.append(f"обмен {rnd}: нет файла {e.filename}")
            continue
        except ValueError as e:
            # A07-X02: рваный JSON артефакта — названный отказ, а не
            # трассировка из середины гейта.
            problems.append(f"обмен {rnd}: артефакт каталога обмена не "
                            f"читается как JSON: {str(e)[:120]}")
            replay_ok = False
            continue
        # A07-X02 (ЗАКОН ФОРМЫ): по всем пяти артефактам круга гейт
        # ходит напрямую (`pack.get("remarks")`, `doc.get("rules")`,
        # `r["id"]`). Негодная форма называется здесь ОДНИМ отказом на
        # артефакт, и круг дальше не судится: контракты и повторная
        # проводка получили бы не данные, а падение.
        names = (f"rounds/round-{rnd:02d}/remarks.json",
                 f"rounds/round-{rnd:02d}/response.json",
                 f"rounds/round-{rnd:02d}/metrics.json",
                 f"versions/model-v{rnd - 1:02d}.json",
                 f"versions/model-v{rnd:02d}.json")
        problems += [f"обмен {rnd}: {p}" for p in
                     _form(names[0], pack, ("remarks",))
                     + _form(names[1], resp, ("answers",))
                     + _form(names[2], metrics)
                     + _form(names[3], old_doc, _MODEL_LISTS)
                     + _form(names[4], new_doc, _MODEL_LISTS)]
        # Слова о форме звучат один раз на артефакт, но КРУГ с негодным
        # артефактом не судится НИКОГДА: версия свода — вход двух
        # соседних кругов, и «уже назвали» не делает её годной (иначе
        # второй круг получал бы её как прежнюю версию и падал).
        if any(n in bad_form for n in names):
            replay_ok = False
            continue
        c["remarks"] += len(pack.get("remarks", []))
        c["answers"] += len(resp.get("answers", []))
        c["versions"] += 1
        problems += [f"обмен {rnd}: {p}"
                     for p in ex.validate_remarks(pack, old_doc)]
        problems += [f"обмен {rnd}: {p}"
                     for p in ex.validate_response(resp, pack)]
        recomputed = ex.metrics_of(pack)
        if list(recomputed["weighty"]) != list(metrics.get("weighty")) \
                or recomputed["E"] != metrics.get("E"):
            problems.append(f"обмен {rnd}: метрика не сходится с "
                            f"пересчётом из пакета замечаний")
        history.append(recomputed)

        try:
            patch_rej = _load(rdir / "patch-rejections.json") \
                if (rdir / "patch-rejections.json").is_file() else []
        except ValueError as e:                          # A07-X02
            problems.append(f"обмен {rnd}: артефакт patch-rejections.json "
                            f"не читается как JSON: {str(e)[:120]}")
            patch_rej = []
        # A07-R1: перечень отбраковок — предъявленная сторона сверки, и
        # подделать его формой нельзя: негодная форма называется
        # словами, а не роняет гейт трассировкой.
        if not isinstance(patch_rej, list) \
                or any(not isinstance(r, dict) for r in patch_rej):
            problems.append(
                f"обмен {rnd}: patch-rejections.json негоден формой — "
                f"перечень записей отбраковки не читается")
            patch_rej = [r for r in (patch_rej
                                     if isinstance(patch_rej, list) else [])
                         if isinstance(r, dict)]
        rejected = {r.get("answer") for r in patch_rej}
        # A07-F1 (шестое неавтор-ревью, остаток (а)): дешёвый замок
        # ПОДЧИЩЕННОГО следа. Принятый и НЕ отбракованный ответ, чья
        # заявленная заплата слово в слово равна строке ПРЕЖНЕЙ версии,
        # в честном цикле невозможен: такую заплату цикл всегда бракует
        # («заплата без следа» первой на строке, откат — после чужого
        # исправления). Стёртая отбраковка при такой заплате — подделка
        # кэша, красный словами; ложных красных на честных каталогах
        # нет по построению. Проверка осталась ДЕШЁВЫМ первым судьёй:
        # общую проводку ниже она не заменяет и работает даже там, где
        # замороженного входа нет.
        old_rows = {r.get("id"): r for r in old_doc.get("rules", [])
                    if isinstance(r, dict)}
        for a in resp.get("answers", []):
            if not isinstance(a, dict) \
                    or a.get("verdict") not in ("ПРИНЯТО", "ЧАСТИЧНО") \
                    or a.get("remark") in rejected:
                continue
            p = a.get("patch")
            if isinstance(p, dict) and isinstance(p.get("rule"), dict) \
                    and p.get("rule_id") in old_rows \
                    and p["rule"] == old_rows[p["rule_id"]]:
                problems.append(
                    f"обмен {rnd}: ответ {a.get('remark')}: заявленная "
                    f"заплата слово в слово равна строке прежней версии "
                    f"({p['rule_id']}), а отбраковки нет — подчищенный "
                    f"след (честный цикл бракует такую заплату как "
                    f"«заплата без следа» либо откат)")
        # A07-R1 (находка аудитора 12.09.2026): ИСХОД ПРИНАДЛЕЖИТ
        # ОТВЕТУ. Прежде исполнение выводилось разницей версий
        # (`executed_by_trace`): два замечания на одну строку — и
        # стёртая отбраковка плохого ответа давала ему исполнение за
        # счёт чужой правки той же строки. Теперь круг ПРОВОДИТСЯ
        # ЗАНОВО тем же кодом, что и в цикле (`ex.apply_round_pure` на
        # закреплённом входе: свод и реестр начала круга, пакет
        # замечаний, ответ, замороженный комплект). Сверяются три
        # предъявленные вещи: версия свода, исходы ответов и реестр.
        if ex.round_needs_context(resp) and context is None:
            # Судить нечем — fail-closed словами: у круга есть заплаты,
            # а закреплённого входа, по которому они судились, нет.
            problems.append(
                f"обмен {rnd}: судить нечем — {context_reason}; "
                f"детерминированная проводка круга с заплатами "
                f"невозможна, зелёным он не считается")
            replay_ok = False
            continue
        try:
            res = ex.apply_round_pure(
                old_doc, registry_replay, pack, resp, rnd,
                context=context,
                cycle=registry_replay.get("cycle", 1))
        except ValueError as e:
            # A07: состояние вне перечня STATES и прочие отказы формы —
            # красный гейт словами, а не падение.
            problems.append(f"обмен {rnd}: {e}")
            replay_ok = False
            continue
        except Exception as e:                            # noqa: BLE001
            # Гейт читает НЕДОВЕРЕННЫЕ файлы каталога: подделанный
            # ответ или версия не вправе уронить проверку трассировкой
            # — отказ называется и красит гейт (fail-closed).
            problems.append(
                f"обмен {rnd}: детерминированная проводка обмена "
                f"отказала ({type(e).__name__}): {str(e)[:160]}")
            replay_ok = False
            continue
        registry_replay = res["registry"]
        accepted = res["targets"]
        if _canon(res["model"]) != _canon(new_doc):
            problems.append(
                f"обмен {rnd}: версия свода model-v{rnd:02d}.json не "
                f"совпадает с детерминированной проводкой обмена по "
                f"замечаниям и ответам — предъявленная правка не "
                f"выводится из принятых заплат")
        # Исходы: чей ответ встал, а чей отбракован, — считает проводка,
        # а не файл. Стёртая отбраковка (подделанный кэш) и лишняя
        # отбраковка одинаково красят гейт С ИМЕНЕМ ОТВЕТА.
        replay_rej = {rj.get("answer") for rj in res["rejections"]
                      if isinstance(rj, dict)}
        for aid in sorted(str(x) for x in (replay_rej - rejected)):
            problems.append(
                f"обмен {rnd}: ответ {aid} отбракован проводкой, а в "
                f"patch-rejections.json записи нет — отбраковка стёрта "
                f"или ответ подменён")
        for aid in sorted(str(x) for x in (rejected - replay_rej)):
            problems.append(
                f"обмен {rnd}: ответ {aid} записан отбракованным, а "
                f"проводка его отбраковки не даёт — запись отбраковки "
                f"лишняя или ответ подменён")
        # Новые строки получают идентификатор сервером — diff-only с
        # «+» это учитывает; цели — ТЕ ЖЕ, что у проводки.
        problems += [f"обмен {rnd}: {p}"
                     for p in ex.check_diff_only(old_doc, new_doc,
                                                 accepted)]

    # П0 §0.1 / A07: версия схемы реестра — известная либо старая форма
    # без поля (registry/1); неизвестная — отказ, не догадка. A07-F4:
    # судит тот же загрузчик, что и у цикла, — он же ловит неполную
    # новую форму (registry/2 без «findings»), которая прежде
    # читалась как пустой реестр. A07-F3: он же возвращает старую
    # техническую отбраковку замком кода в живые — обе стороны
    # сравнения проекций судятся одним законом.
    # A07-R5: гейт зовут только на ЗАВЕРШЁННЫЙ цикл — вердикт этого
    # каталога уже прочитан выше (без verdict.json проверка вышла
    # словами). Исход заявлен, значит реестр обязан быть: исчезнувший
    # registry.json прежде читался пустым, и цикл с исчезнувшим
    # реестром получал зелёный гейт (счётчик находок 0, блокеров нет,
    # «успех» сходился сам с собой).
    schema = None
    try:
        stored = ex.load_registry(out_dir / "registry.json",
                                  expect_file=True)
        schema = stored.get("schema_version")
    except ValueError as e:
        problems.append(str(e))
        stored = {}
    c["findings"] = len(stored.get("findings", []))
    # В2-4: сравнение — по УСТОЙЧИВОЙ проекции жизненного цикла.
    # Поля контента (text/basis/partial/remapped) добавлялись
    # эволюцией кода: побайтовое сравнение краснило все каталоги,
    # завершённые прежними версиями (состязательная проверка
    # 2026-08-21). Ключи обеих сторон — в текущей канонизации.
    def _projection(findings: list[dict]) -> list[tuple]:
        return [(f.get("id"), f.get("state"),
                 ex._normalize_key(f.get("key", "")),
                 f.get("opened_round"), f.get("closed_round"),
                 f.get("severity"), f.get("deadlock_kind"))
                for f in findings]

    if replay_ok and _projection(registry_replay.get("findings", [])) != \
            _projection(stored.get("findings", [])):
        problems.append("реестр не совпадает с повторной проводкой "
                        "обменов — жизненный цикл находок не сходится")

    # A07-X02: перечень регресс-токенов — недоверенный файл каталога;
    # негодная форма называется отказом, а `check_regress_tokens` его
    # не получает (он ходит по записям как по объектам).
    try:
        tokens = _load(out_dir / "tokens.json") \
            if (out_dir / "tokens.json").is_file() else []
    except ValueError as e:
        problems.append(f"артефакт tokens.json: не читается как JSON: "
                        f"{str(e)[:120]}")
        tokens = []
    if not isinstance(tokens, list) \
            or any(not isinstance(t, dict) for t in tokens):
        problems.append("артефакт tokens.json: не перечень записей "
                        "регресс-токенов — откаты исправлений судить "
                        "нечем")
        tokens = []
    if n_rounds:
        final_name = f"versions/model-v{n_rounds:02d}.json"
        try:
            final = _load(out_dir / "versions" /
                          f"model-v{n_rounds:02d}.json")
        except (FileNotFoundError, ValueError) as e:
            problems.append(f"артефакт {final_name}: финальная версия "
                            f"свода не читается: {str(e)[:120]}")
            final = None
        # A07-X02: форма финальной версии — ОДИН отказ на артефакт (её
        # же судил последний обмен), и контракты П-1 её не получают:
        # `validate_schema` и регресс-токены ходят по строкам свода.
        if final is not None:
            problems += _form(final_name, final, _MODEL_LISTS)
        if final is not None and final_name not in bad_form:
            problems += ex.check_regress_tokens(final, tokens)
            problems += m.validate_schema(final) + m.validate_model(final)

    # Настоящий лимит обменов записан в вердикт (без него ранняя
    # стагнация ложно требовала «лимит»: реконструкция подставляла
    # число сыгранных обменов вместо лимита — поймано сквозным циклом
    # 2026-08-21).
    # A07: правило останова судится с ТЕРМИНАЛЬНЫМ реестром — тем же
    # registry.json, что и у цикла (выше он сверен с повторной
    # проводкой), той же функцией registry_blockers.
    blockers = ex.registry_blockers(stored)
    # A07-X02: лимит обменов — из недоверенной сводки; нечисло роняло
    # бы правило останова сравнением int со строкой. Негодное значение
    # называется, а судит гейт по числу сыгранных обменов.
    recorded_max = summary.get("max_rounds") or max(n_rounds, 1)
    if not isinstance(recorded_max, int) or isinstance(recorded_max, bool) \
            or recorded_max < 1:
        problems.append(f"артефакт verdict.json: поле «max_rounds» не "
                        f"целое положительное: "
                        f"{summary.get('max_rounds')!r}")
        recorded_max = max(n_rounds, 1)
    expected = ex.decide(history, recorded_max, registry=stored)
    got_verdict = summary.get("verdict")
    if got_verdict not in ex.CYCLE_VERDICTS:
        problems.append(f"вердикт {got_verdict!r} вне перечня")
    # A07-F2 (находка аудитора 12.09.2026): «успех» при непустом
    # registry_blockers — красный ВСЕГДА, без оглядки на длину
    # истории и промежуточное решение decide. Прежде сверка шла
    # только при expected != «продолжать»: при нуле или одном
    # чистом раунде decide говорит «продолжать», и подменённый
    # «успех» проходил гейт. Незавершённая история сама по себе
    # терминального успеха не доказывает.
    forged_success = got_verdict == "успех" and bool(blockers)
    if forged_success:
        problems.append(
            f"вердикт {got_verdict!r} при живых блокирующих/"
            f"критических находках реестра (ожидалось "
            f"{ex.VERDICT_NOT_CLOSED!r}): "
            + ", ".join(f"{b.get('id')} ({b.get('severity')}, "
                        f"{b.get('state')})" for b in blockers))
    # Остаток F2 (третье неавтор-ревью, проба P7): «успех» при ПУСТОМ
    # реестре и незавершённой истории (ноль или один чистый раунд —
    # правило останова говорит «продолжать») прежде тоже проезжал:
    # сверка с decide шла только при expected != «продолжать».
    # Терминальный успех доказывают CLEAN_NEEDED чистых аудитов
    # подряд; их нет — слова отказа. Честные «не закрыто», «лимит» и
    # «стагнация» на той же короткой истории остаются зелёными: они
    # не объявляют цикл закрытым успехом.
    if got_verdict == "успех" and expected == "продолжать":
        problems.append(
            f"вердикт {got_verdict!r} при незавершённой истории "
            f"(правило останова: {expected}): обменов {len(history)}, "
            f"а терминальный успех доказывают {ex.CLEAN_NEEDED} "
            f"чистых полных аудита подряд")
    if got_verdict == "остановлен по Б/К":
        # ОСТ-1: остановка по решению человека законна только на
        # обмене с Б=0, К=0 и С>0 — decide здесь не судья.
        last_w = tuple((history[-1] if history else {})
                       .get("weighty") or (1, 1, 0))
        if not (last_w[0] == 0 and last_w[1] == 0 and last_w[2] > 0):
            problems.append(
                f"вердикт «остановлен по Б/К» при метрике последнего "
                f"обмена {list(last_w)} — критерий Б=0, К=0, С>0 не "
                f"выполнен")
        # A07 (ревью, находка 1): «чисто по Б/К» судится и реестром —
        # живая блокирующая/критическая находка делает остановку
        # подменой вердикта.
        if blockers:
            problems.append(
                f"вердикт «остановлен по Б/К» при живых блокирующих/"
                f"критических находках реестра: "
                + ", ".join(str(f.get("id")) for f in blockers[:6]))
    elif not forged_success and expected != "продолжать" \
            and got_verdict != expected:
        if expected == ex.VERDICT_NOT_CLOSED:
            # Подмена вердикта при живой Б/К-находке — нарушение с
            # именами находок (контрпример аудитора CONS-02).
            problems.append(
                f"вердикт {got_verdict!r} при живых блокирующих/"
                f"критических находках реестра (ожидалось "
                f"{expected!r}): "
                + ", ".join(f"{b.get('id')} ({b.get('severity')}, "
                            f"{b.get('state')})" for b in blockers))
        else:
            problems.append(f"вердикт {got_verdict!r} не следует из "
                            f"истории метрик (ожидалось {expected!r})")
    # A07: сводка блокирующих в verdict.json сходится с пересчётом по
    # реестру (перечень id и счётчик).
    rv = summary.get("registry_verdict")
    if rv is None:
        # Сводка цикла до registry/2 допустима лишь при старой форме
        # реестра и прежнем слове вердикта.
        if got_verdict == ex.VERDICT_NOT_CLOSED or schema is not None:
            problems.append("сводка verdict.json без registry_verdict — "
                            "перечень блокирующих находок реестра не "
                            "записан")
    elif not isinstance(rv, dict):
        problems.append("registry_verdict в verdict.json не объект")
    else:
        recorded = sorted(str(b.get("id")) for b in
                          (rv.get("blockers") or [])
                          if isinstance(b, dict))
        actual = sorted(str(b.get("id")) for b in blockers)
        if recorded != actual or rv.get("count") != len(actual):
            problems.append(f"registry_verdict не сходится с пересчётом "
                            f"по реестру: записано {recorded} "
                            f"(count={rv.get('count')!r}), по реестру "
                            f"{actual}")
    return problems, c


def run(out_dir: str) -> int:
    _selftest()
    problems, c = check(Path(out_dir))
    print(f"рассмотрено: обменов {c['rounds']}, замечаний "
          f"{c['remarks']}, ответов {c['answers']}, версий "
          f"{c['versions']}, находок реестра {c['findings']} "
          f"(самотест пройден)")
    if problems:
        for p in problems:
            print(f"НАРУШЕНИЕ: {p}")
        print(f"ВЕРДИКТ: КРАСНЫЙ — нарушений {len(problems)}")
        return 1
    print("ВЕРДИКТ: аудит-цикл соответствует контракту")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(description="Гейт аудит-цикла CDPM")
    ap.add_argument("out", help="каталог цикла (exchange)")
    args = ap.parse_args(argv)
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
