# -*- coding: utf-8 -*-
"""Сборщик набора «ИД H0-К» — ИДАК H0 БЕЗ критерия оптимизации
(поручение владельца 2026-09-04).

Комплект выводится из формы H0 (open-bench/polyaris-h0/etalon/
VMP-H0-form.json) тем же генератором build_kit_h0: единственное
отличие — разделы «в) критерии оптимизации» обеих частей пусты
(в jpg — заголовок раздела и запись «(нет)», как в каноническом
markdown формы), у эталона нет целевых (objectives = []).

Ожидаемое поведение конвейера на этом наборе — в EXPECTED.md и в
ключе «expect» манифеста (раннер run_local_pipeline судит по нему):
  - вопросы анкеты — ровно Q-3a-GOAL и Q-3b-GOAL (о целевой), книга
    ответов закрывает их ответом «критерия оптимизации нет»;
  - матрица корректности: optimization = «отказ подтверждён» с
    сообщением о режиме взвешенных штрафов;
  - allowed_modes обеих задач: feasibility-soft, feasibility-hard;
    лестница готовности — feasible_soft, в notes — сообщение;
  - COSMOS без mode берёт feasibility-soft (шаг Ш-1к кнопки).

Этот же сборщик переносит свежую сборку в набор «ИД H0-КИ»
(open-bench/polyaris-h0kh): своего сборщика комплекта у Н0-КИ нет —
документы правил и обе проекции эталона у него КОПИИ Н0-К, а своё у
набора только книга данных с листами истории (build_data_h0kh.py) и
ожидания вариантов stop/full. До 11.09.2026 копии переносились рукой, и
это стоило живого дефекта: перевыпуск 11.09 (f4372275) перенёс документы
и форму, а ВМП эталона Н0-КИ остался с отпечатками ПРЕЖНЕЙ сборки —
эталон называл sha, которого в комплекте уже нет. Теперь один запуск
сборщика Н0-К делает перенос сам (sync_h0kh).

Запуск: python open-bench/polyaris-h0k/build_kit_h0k.py
"""
from __future__ import annotations

import copy
import io
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
H0 = REPO / "open-bench" / "polyaris-h0"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(H0))
import build_kit_h0 as bk  # noqa: E402
from cdpm import vmp_form as vf  # noqa: E402

#: Имя папки набора Н0-КИ рядом с Н0-К (H0-К + листы истории): свежую
#: сборку Н0-К туда переносит sync_h0kh — сборщика комплекта у набора нет.
H0KH_NAME = "polyaris-h0kh"
#: Файлы эталона, которые Н0-КИ берёт у Н0-К без изменений (карта якорей
#: и строки jpg — о тех же документах; обе проекции эталона переносятся
#: отдельно, с отпечатками по факту файлов Н0-КИ).
H0KH_ETALON_COPIES = ("anchors-map.json", "jpg-lines.json")

CUSTOMER = "Полярис-H0-К (ВМП H0 без критерия оптимизации)"
DOCX_NAME = "ID-H0K-VMP-1.docx"
XLSX_NAME = "ID-H0K-VMP-2.xlsx"
JPG_NAME = "ID-H0K-VMP-3.jpg"
DATA_NAME = bk.DATA_NAME            # книга данных — та же, что у H0
RENAMES = {bk.DOCX_NAME: DOCX_NAME, bk.XLSX_NAME: XLSX_NAME,
           bk.JPG_NAME: JPG_NAME}
EMPTY_MARK = "(нет)"                # канонический рендер пустого раздела формы

# Ответ авиакомпании на вопрос о целевой: критерия нет. Фраза
# «критерия оптимизации нет» распознаётся матрицей как отказ от
# оптимизации (cdpm.model._GOAL_DECLINE_PHRASES), обработчик ответов
# обязан выставить decline=true и пустые objectives.
GOAL_ANSWER = {
    "3a": ("Критерия оптимизации нет. Оптимизация пейринга не требуется: "
           "решение ищите в режиме взвешенных штрафов (feasibility-soft) — "
           "все жёсткие правила соблюдены, минимизируется взвешенная сумма "
           "штрафов мягких правил; коэффициенты мягких правил указаны в их "
           "строках, новых целевых не вносить."),
    "3b": ("Критерия оптимизации нет. Оптимизация ростеринга не требуется: "
           "решение ищите в режиме взвешенных штрафов (feasibility-soft) — "
           "все жёсткие правила соблюдены, минимизируется взвешенная сумма "
           "штрафов мягких правил; коэффициенты мягких правил указаны в их "
           "строках, новых целевых не вносить."),
}

EXPECT = {
    "_comment": ("Ожидание прогона H0-К (владелец 04.09.2026): сообщение, "
                 "что критерия оптимизации нет и возможен только поиск "
                 "feasibility-soft — оптимальное решение, удовлетворяющее "
                 "всем жёстким ограничениям, с критерием, равным взвешенной "
                 "сумме штрафов. Раннер сверяет ключи ниже с пакетом."),
    # 1.17.2 (решение владельца 05.09.2026): истории в книге нет, а правила
    # ростеринга (пол отдыха) судятся с остатками — вопрос линии данных
    # законен и ожидаем; ответ «б» — продолжить с нулевыми остатками.
    "questions": ["Q-3a-GOAL", "Q-3b-GOAL"],   # + вопросы истории по виду — при сборке
    "_history_question": ("вопрос линии данных 1.17.2 (решение владельца "
                          "05.09.2026): у правил ростеринга есть остатки "
                          "истории, а истории в книге нет — авиакомпания "
                          "отвечает «б» (продолжить с нулевыми остатками)"),
    "allowed_modes": {"pairing": ["feasibility-soft", "feasibility-hard"],
                      "rostering": ["feasibility-soft", "feasibility-hard"]},
    "readiness_ladder": {"pairing": "feasible_soft",
                         "rostering": "feasible_soft"},
    "optimization_state": "отказ подтверждён",
    "note_contains": "критерия оптимизации нет",
    "objectives": 0,
    "cosmos_default_mode": "feasibility-soft",
    # Ожидаемые вердикты стадий (11.09.2026): только стадии CDPM —
    # EXPECTED.md набора говорит, что извлечение и сквозной цикл
    # показывают открытые вопросы о целевой, а ответы и пакет обязаны
    # быть эквивалентны.
    "stage_verdicts": bk.stage_verdicts(
        "ожидаемые вердикты стадий H0-К (EXPECTED.md): извлечение и "
        "сквозной цикл показывают открытые вопросы о целевой — не "
        "эквивалентно ДО ОТВЕТОВ по замыслу набора; ответы и пакет "
        "обязаны быть эквивалентны (правил 16/16, целевых 0/0, форма "
        "полная). Вердикт цикла не объявлен: он перекрашивается "
        "пометкой чистоты цикла и словом вердикта не исчерпывается.",
        with_cosmos=False, extract="NOT_EQUIVALENT"),
    # Вердикт гейта непротиворечивости в манифесте пакета (11.09.2026):
    # слово одно на все наборы формы H0 — берётся у базового набора.
    # Критерия оптимизации у набора нет, а ЖЁСТКИЕ правила есть, и
    # противоречий в них нет — «sat», а не «none».
    "consistency": bk.CONSISTENCY,
}


#: Пояснение к ожидаемым вердиктам стадий Н0-КИ, стоп-вариант (книга
#: истории без строки H14): слово в слово то, что стояло в манифесте
#: комплекта до 11.09.2026, — перенос его не переписывает.
H0KH_STAGE_NOTE_STOP = (
    "ожидаемые вердикты стадий H0-КИ — те же, что у H0-К: извлечение не "
    "эквивалентно до ответов (открытые вопросы о целевой и об истории), "
    "ответы и пакет эквивалентны. Стадии COSMOS не объявлены: по замыслу "
    "набора кнопка ростеринга ОСТАНАВЛИВАЕТСЯ словами (у H14 нет "
    "истории), и вердикта задачи здесь не ждут.")
#: Пояснение к ожидаемым вердиктам стадий Н0-КИ, полный вариант (история
#: у всех 24 сотрудников). Стадии CDPM — те же: документы правил и эталон
#: у вариантов ОБЩИЕ, и вопросы о целевой открыты при извлечении так же.
#: Из стадий COSMOS объявлен только ростеринг: его ждёт сам замысел
#: набора (ключ expect.history полного варианта, согласован с COSMOS
#: 05.09.2026) — остановки Ш-2с нет, а пол отдыха дома у решателя
#: жёсткий, и он его не нарушает. Пейринг и сверку с ИДАК набор не
#: объявляет: живого прогона с машинным отчётом нажатий у него не было.
H0KH_STAGE_NOTE_FULL = (
    "ожидаемые вердикты стадий H0-КИ-полного. Стадии CDPM — те же, что у "
    "стоп-варианта (документы правил и эталон у вариантов общие): "
    "извлечение не эквивалентно до ответов из-за открытых вопросов о "
    "целевой, ответы и пакет эквивалентны. Из стадий COSMOS объявлен "
    "только ростеринг «допустимо» — этого ждёт замысел полного варианта "
    "(ключ expect.history): история есть у всех 24 сотрудников, "
    "остановки Ш-2с нет, а пол отдыха дома жёсткий у решателя, и он его "
    "не нарушает. Пейринг и сверка с ИДАК не объявлены: живого прогона с "
    "машинным отчётом нажатий у набора не было, и выдумывать их вердикты "
    "нечем. Стадии COSMOS судятся только с машинным отчётом нажатий "
    "(--cosmos-press).")


def h0kh_stage_verdicts(variant: str) -> dict:
    """Ожидание `stage_verdicts` набора Н0-КИ по варианту книги истории
    («stop» — H14 без строки, «full» — история у всех 24). Слова вердиктов
    — у базового сборщика (bk.stage_verdicts), своё у варианта только
    пояснение и то, какие стадии он объявляет."""
    if variant == "full":
        return bk.stage_verdicts(
            H0KH_STAGE_NOTE_FULL, with_cosmos=False,
            extract="NOT_EQUIVALENT",
            rostering=bk.STAGE_VERDICTS_COSMOS["rostering"])
    return bk.stage_verdicts(H0KH_STAGE_NOTE_STOP, with_cosmos=False,
                             extract="NOT_EQUIVALENT")


def check_sources(sources: list, et: dict) -> None:
    """Обе проекции эталона — об одних и тех же файлах (закон базового
    сборщика, 11.09.2026): состав источников ВМП обязан совпасть с
    формой по (sid, файл). Расхождение — отказ словами: эталон, который
    называет файлы, которых в комплекте нет, врёт паспорту прогона."""
    mine = [(str(s.get("sid")), str(s.get("file"))) for s in sources]
    theirs = [(str(s.get("sid")), str(s.get("file")))
              for s in (et.get("sources") or [])]
    if theirs != mine:
        raise SystemExit(f"ЭТАЛОН И ФОРМА О РАЗНЫХ ИСТОЧНИКАХ: ВМП {theirs}, "
                         f"форма {mine}")


def derive_form(h0_form: dict) -> dict:
    """Форма H0-К: та же форма без критериев обеих частей."""
    form = copy.deepcopy(h0_form)
    form["customer"] = CUSTOMER
    for part in ("pairing", "rostering"):
        form.setdefault(part, {})["criteria"] = []
    meta = form.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = ("ИДАК H0-К (2026-09-04): форма H0 без разделов «в) "
                    "критерии оптимизации» — целевых нет по замыслу; "
                    "ожидается отказ от оптимизации по ответу авиакомпании "
                    "и режим взвешенных штрафов. " + str(meta.get("note") or ""))
    for s in form.get("sources") or []:
        s["file"] = RENAMES.get(s.get("file"), s.get("file"))
    return form


def derive_etalon(h0_etalon: dict, form: dict) -> dict:
    """Эталон ВМП H0-К: правила H0 буква в букву, целевых нет."""
    et = copy.deepcopy(h0_etalon)
    et["objectives"] = []
    et["questions"] = []
    meta = et.setdefault("meta", {})
    meta["customer"] = CUSTOMER
    meta["note"] = (form.get("meta") or {}).get("note")
    et["sources"] = copy.deepcopy(form.get("sources") or [])
    return et


def jpg_items(by_file: dict) -> list:
    """Разделы «в)» пусты: под каждым заголовком — «(нет)» (как в
    каноническом markdown формы, vmp_form.form_markdown)."""
    out = []
    for kind, rid, text in by_file["jpg"]:
        out.append((kind, rid, text))
        if kind == "heading":
            out.append(("note", None, EMPTY_MARK))
    return out


def dump_json(path: Path, obj, indent: int = 1) -> None:
    """Запись json комплекта: отступ по умолчанию — сборщицкий (1)."""
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=indent)
                          + "\n", encoding="utf-8")


def json_indent(text: str) -> int:
    """Отступ, которым файл записан сейчас: перезапись чужого манифеста
    не переформатирует его целиком (у стоп-варианта Н0-КИ отступ 1, у
    полного — 2). Файла в одну строку не бывает — тогда отступ 1."""
    for line in text.splitlines()[1:]:
        body = line.lstrip(" ")
        if body and body[0] not in "}]":
            return len(line) - len(body)
    return 1


def sync_h0kh(form: dict, et: dict, docs: Path, etalon: Path,
              kh_dir: Path, catalog: dict | None = None) -> dict | None:
    """Перенос свежей сборки Н0-К в набор Н0-КИ (H0-К + листы истории).

    Своего сборщика комплекта у Н0-КИ нет: документы правил и обе
    проекции эталона у него — копии Н0-К, и до 11.09.2026 их переносили
    рукой (живой след: ВМП эталона Н0-КИ с отпечатками прежней сборки).
    Переносится всё, что у набора не своё:
      * три файла правил — байт в байт;
      * обе проекции эталона (форма и ВМП) с отпечатками ПО ФАКТУ
        перенесённых файлов (bk.stamp_sources по папке Н0-КИ, а не
        переписанное число Н0-К) и сверкой состава источников;
      * карта якорей и строки jpg — они о тех же документах;
      * книга ответов — ответы Н0-К; СВОИ СЛОВА набора (_comment,
        customer) сохраняются. Если у Н0-КИ когда-нибудь появятся свои
        ОТВЕТЫ, ему нужен свой сборщик, и этот перенос надо пересмотреть;
      * ожидания вариантов stop и full, у которых одно место правды, —
        вердикты стадий (h0kh_stage_verdicts) и слово непротиворечивости
        (bk.CONSISTENCY); блок catalog — если вариант его несёт.
    Остальное в манифестах вариантов — своё (ожидания истории, вопросы
    анкеты, пояснения) и не трогается.

    Набора рядом нет — перенос не делается и об этом говорится словами
    (сборка во временную папку, стенд); набор есть, но неполон или о
    других файлах правил — отказ словами."""
    kh = Path(kh_dir)
    if not kh.is_dir():
        print(f"набора Н0-КИ рядом нет ({kh}) — перенос не делался")
        return None
    kh_docs, kh_etalon = kh / "docs", kh / "etalon"
    for d in (kh_docs, kh_etalon):
        if not d.is_dir():
            raise SystemExit(f"набор Н0-КИ неполон: нет папки {d}")
    names = [str(s.get("file") or "") for s in (form.get("sources") or [])]
    if not names:
        raise SystemExit("в форме Н0-К нет источников: переносить нечего")
    for name in names:
        if not (Path(docs) / name).is_file():
            raise SystemExit(f"нет файла правил Н0-К: {Path(docs) / name}")
    # Манифесты вариантов читаются и судятся ДО записи: отказ не имеет
    # права оставить набор перевязанным наполовину (урок сборщика Н0-C
    # 09.09.2026 — неудачный запуск переписывал документы комплекта).
    found = []
    for rel, variant in (("kit-manifest.json", "stop"),
                         ("full/kit-manifest.json", "full")):
        path = kh / rel
        if not path.is_file():
            if variant == "stop":
                raise SystemExit(f"набор Н0-КИ неполон: нет {path}")
            print(f"вариант «{variant}» набора Н0-КИ не выложен ({path}) — "
                  f"ожидания не переносились")
            continue
        text = path.read_text(encoding="utf-8")
        man = json.loads(text)
        mine = [Path(str(r)).name for r in (man.get("rules_files") or [])]
        if mine != names:
            raise SystemExit(
                f"{path}: файлы правил {mine} ≠ файлам Н0-К {names} — перенос "
                f"не о тех документах, комплект правьте руками")
        found.append((path, variant, man, json_indent(text)))
    for name in names:
        shutil.copyfile(docs / name, kh_docs / name)
    for name in H0KH_ETALON_COPIES:
        shutil.copyfile(etalon / name, kh_etalon / name)
    # Отпечатки — по факту файлов, лежащих в Н0-КИ (они только что
    # скопированы, но правда об отпечатке берётся с диска набора).
    kh_form = copy.deepcopy(form)
    sources = bk.stamp_sources(kh_form, kh_docs)
    kh_et = copy.deepcopy(et)
    check_sources(sources, kh_et)
    kh_et["sources"] = copy.deepcopy(sources)
    dump_json(kh_etalon / "VMP-H0K-form.json", kh_form)
    dump_json(kh_etalon / "VMP-H0K.json", kh_et)
    book = json.loads((etalon / "ANSWER-BOOK-MACHINE.json")
                      .read_text(encoding="utf-8"))
    kh_book = kh_etalon / "ANSWER-BOOK-MACHINE.json"
    if kh_book.is_file():
        own = json.loads(kh_book.read_text(encoding="utf-8"))
        for key in ("customer", "_comment"):
            if key in own:
                book[key] = own[key]
    dump_json(kh_book, book)
    variants = []
    for path, variant, man, indent in found:
        exp = man.setdefault("expect", {})
        exp["stage_verdicts"] = h0kh_stage_verdicts(variant)
        exp["consistency"] = bk.CONSISTENCY
        if catalog is not None and man.get("catalog") is not None:
            man["catalog"] = copy.deepcopy(catalog)
        dump_json(path, man, indent)
        variants.append(variant)
    res = {"dir": str(kh), "docs": names, "variants": variants,
           "sources": sources}
    print(f"набор Н0-КИ перевязан за Н0-К ({kh.name}): документы "
          + ", ".join(names) + f"; варианты ожиданий {', '.join(variants)}; "
          "отпечатки по факту файлов набора — "
          + ", ".join(f"{s['sid']} {s['sha256'][:12]}…" for s in sources))
    return res


def build(kit_dir: Path = HERE, kh_dir: Path | None = None) -> dict:
    form = derive_form(bk.load_form())
    bad = vf.validate_form(form, strict=False)
    if bad:
        raise SystemExit("ФОРМА H0-К НЕ ЧИСТА:\n  " + "\n  ".join(bad))
    if any(form[p].get("criteria") for p in ("pairing", "rostering")):
        raise SystemExit("в форме H0-К остались критерии")
    docs = kit_dir / "docs"
    etalon = kit_dir / "etalon"
    data = kit_dir / "data"
    for d in (docs, etalon, data):
        d.mkdir(parents=True, exist_ok=True)
    by_file = bk.lines_by_file(form)
    by_file["jpg"] = jpg_items(by_file)
    bk.build_docx(docs / DOCX_NAME, by_file["docx"])
    bk.build_xlsx(docs / XLSX_NAME, by_file["xlsx"])
    jpg_lines = bk.build_jpg(docs / JPG_NAME, by_file["jpg"])
    frozen = bk.freeze_texts(docs / DOCX_NAME, docs / XLSX_NAME)
    amap, problems = bk.build_anchors_map(by_file, frozen, jpg_lines)
    n_rows = sum(len(rows) for _p, _s, rows in vf.form_sections(form))
    n_map = sum(1 for k in amap if not k.startswith("_"))
    if n_map != n_rows:
        problems.append(f"в карте {n_map} записей, в форме {n_rows}")
    # Якоря правил H0-К обязаны совпасть с H0 буква в букву: критерии
    # жили только в jpg, docx/xlsx не изменились (кроме имени
    # заказчика в шапке — она не запись).
    h0_map = json.loads((H0 / "etalon" / "anchors-map.json").read_text(encoding="utf-8"))
    for rid, anchors in amap.items():
        if rid.startswith("_"):
            continue
        if h0_map.get(rid) != anchors:
            problems.append(f"якорь {rid}: H0-К {anchors} ≠ H0 {h0_map.get(rid)}")
    if problems:
        raise SystemExit("САМОПРОВЕРКА КРАСНАЯ:\n  " + "\n  ".join(problems))
    src = H0 / "data" / DATA_NAME
    if not src.is_file():
        raise SystemExit(f"нет книги данных H0: {src}")
    shutil.copyfile(src, data / DATA_NAME)
    # Отпечатки источников — носителем базового сборщика (одна формула на
    # все сборщики формы H0): по факту ТОЛЬКО ЧТО собранных файлов, файла
    # нет — отказ словами.
    sources = bk.stamp_sources(form, docs)
    et = derive_etalon(json.loads((H0 / "etalon" / "VMP-H0.json").read_text(encoding="utf-8")),
                       form)
    # Обе проекции эталона — об одних и тех же файлах (как в базовом
    # сборщике): состав источников ВМП обязан совпасть с формой.
    check_sources(sources, et)

    def _dump(path: Path, obj) -> None:
        dump_json(path, obj)

    _dump(etalon / "VMP-H0K-form.json", form)
    _dump(etalon / "VMP-H0K.json", et)
    # Вопросы истории — по виду чтения семей эталона (каталог 0.5.0)
    EXPECT["questions"] = ["Q-3a-GOAL", "Q-3b-GOAL"] + bk.history_question_ids(et)
    _dump(etalon / "anchors-map.json", amap)
    _dump(etalon / "jpg-lines.json", jpg_lines)
    _dump(etalon / "ANSWER-BOOK-MACHINE.json", {
        "form_version": 1, "customer": CUSTOMER,
        "_comment": ("Набор «ИД H0-К»: законные вопросы — о целевой "
                     "(Q-3a-GOAL, Q-3b-GOAL), ответ авиакомпании — «критерия "
                     "оптимизации нет», и вопрос линии данных об истории "
                     "(1.17.2, решение владельца 05.09.2026): истории в книге "
                     "нет — «продолжить с нулевыми остатками». Любой другой "
                     "вопрос = дефект конвейера (несопоставленный вопрос — "
                     "красный отчёт). Сопоставление: exact по id, затем prefix."),
        "exact": {"Q-3a-GOAL": GOAL_ANSWER["3a"], "Q-3b-GOAL": GOAL_ANSWER["3b"]},
        "prefix": {"Q-3a-GOAL-T": GOAL_ANSWER["3a"], "Q-3b-GOAL-T": GOAL_ANSWER["3b"],
                   **bk.history_book_prefix()},
        "regex": []})
    def _rel(p: Path) -> str:
        # Пути эталона в манифесте — от корня репозитория (раннер читает
        # REPO / etalon_vmp); сборка вне репозитория (тесты) — абсолютно.
        try:
            return str(p.relative_to(REPO)).replace("\\", "/")
        except ValueError:
            return str(p).replace("\\", "/")

    # каким каталогом выпущен набор — сборщиком, не рукой (09.09.2026);
    # тем же блоком перевязывается набор Н0-КИ, выпущенный за Н0-К.
    catalog = bk.catalog_block(form)
    manifest = {
        "customer": CUSTOMER,
        "stage": "ИД H0-К (форма H0 без критерия оптимизации)",
        "catalog": catalog,
        "data_class": "synthetic",
        "llm_scope": "any",
        "llm_scope_basis": ("набор синтетический: вымышленная авиакомпания, "
                            "генератор в репозитории (build_kit_h0k.py поверх "
                            "build_kit_h0.py, build_data_h0.py); решение "
                            "владельца 03.09.2026 — для синтетических и "
                            "открытых данных любые модели, включая облачные"),
        "rules_files": [f"docs/{DOCX_NAME}", f"docs/{XLSX_NAME}", f"docs/{JPG_NAME}"],
        "data_files": [f"data/{DATA_NAME}"],
        "answer_book": "etalon/ANSWER-BOOK-MACHINE.json",
        # Раскладка моделей — путь ОТ КОРНЯ репозитория (паспорт прогона,
        # 10.09.2026): копия набора вне open-bench соседа
        # «../polyaris-h0» не имеет. Прежний ключ остаётся для читателей
        # старых наборов.
        "agents_layout_from_repo": bk.LAYOUT_FROM_REPO,
        "agents_layout": "../polyaris-h0/agents-layout-h0.json",
        "strict_questions": False,
        "etalon_vmp": _rel(etalon / "VMP-H0K.json"),
        "etalon_form": _rel(etalon / "VMP-H0K-form.json"),
        "expect": EXPECT,
        "note": ("ИДАК H0-К = H0 без критерия оптимизации (владелец 04.09.2026). "
                 "Три файла правил как у H0: docx — заголовок, общий раздел, "
                 "часть 1 а/б; xlsx — часть 2 а/б; jpg — разделы «в)» обеих "
                 "частей, пустые («(нет)»). Книга ответов закрывает вопросы о "
                 "целевой ответом «критерия оптимизации нет»; ожидания прогона "
                 "— ключ expect и EXPECTED.md. Книга данных — ID-H0-DATA.xlsx "
                 "набора H0."),
    }
    _dump(kit_dir / "kit-manifest.json", manifest)
    res = {"docx": len(by_file["docx"]), "xlsx": len(by_file["xlsx"]),
           "jpg": len(by_file["jpg"]), "rows": n_rows, "map": n_map,
           "rules": len(et["rules"]), "objectives": len(et["objectives"])}
    print(f"ИД H0-К собран: docx {res['docx']} абзацев, xlsx {res['xlsx']} строк, "
          f"jpg {res['jpg']} абзацев ({jpg_lines}); записей формы {res['rows']}, "
          f"карта якорей {res['map']} (= H0); эталон: правил {res['rules']}, "
          f"целевых {res['objectives']}; самопроверка чиста")
    print("отпечатки источников обновлены по факту сборки (форма и ВМП): "
          + ", ".join(f"{s['sid']} {s['file']} {s['sha256'][:12]}…"
                      for s in sources))
    # Один запуск — оба набора: Н0-КИ живёт копиями Н0-К, и переносит их
    # сборщик, а не рука. По умолчанию — набор-сосед по папке open-bench
    # (сборка во временную папку соседа не находит и переноса не делает).
    kh = Path(kh_dir) if kh_dir is not None else Path(kit_dir).parent / H0KH_NAME
    res["h0kh"] = sync_h0kh(form, et, docs, etalon, kh, catalog)
    return res


if __name__ == "__main__":
    # Переобёртка stdout — только при запуске из консоли (при импорте
    # из тестов она ломает захват вывода pytest).
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    build()
