# -*- coding: utf-8 -*-
"""Закон паритета каталога шаблонов с кодом COSMOS (зона COSMOS).

Один движок судит ЛЮБУЮ папку каталога — живой каталог дерева, «живой ⊕
кандидаты», временный каталог попытки изменения (CR), папку выпуска
`catalog/releases/<версия>/`. До пакета паритета закон жил в pytest-файле
и судил только каталог дерева, а числа гейта были литералами: первая же
сборка службой краснила их по построению, и правка класса «рост таблиц
решателя» доходила до `needs_human` не по делу, а по устройству гейта.

Левая сторона правила всегда одна — каталог, прочитанный читателем
`template_catalog.load(<папка>)`. Правая сторона — СВИДЕТЕЛИ, которые
движок получает данными: таблицы решателя (`vmp.kotlin_tables`),
`cdpm.data_checks`, `vmp.run.roster_certify`, `cdpm.model` (зоны,
категории годности, метрики), файл привязок, снимок заморозки шага 1,
жёсткие блоки посева (база, словарь CDPM, отношения по слову владельца)
и заявки каталога. Свидетеля можно подменить данными — на этом стоят
стенды «решатель вырос на ручку»; ключа командной строки для подмены
нет.

Ни одно правило не сравнивает число с литералом: числа ПЕЧАТАЮТСЯ
(`numbers` вердикта), а утверждается равенство множеств с именами. Оно
строже числа и не запрещает законный рост.

    python catalog/tools/parity.py [--catalog <папка>] [--bindings <файл>]
                                   [--with-candidates] [--candidate <файл>]
                                   [--json <файл>] [--quiet]

Кандидаты приходят двумя путями. `--with-candidates` — движок сам
находит файлы `<папка>/candidates/**`, материализует их поверх папки во
временный каталог и строит привязки В ПАМЯТИ (`--bindings` тогда не
читается). `--candidate` — папку уже собрал зовущий (пятый гейт CR):
движок берёт из файла адрес записи и номер заявки, чтобы правило L21
«запись ≡ заявке» судило, а не отвечало «сверять не с чем».

Коды возврата: 0 — красных правил нет; 1 — есть красные; 2 — не судилось
(папка, таблицы решателя или посев не читаются). Правило, которое судить
нечем (двери читают другой каталог, папка без `tools/`, описи нет в
снимке выпуска, CDPM не знает метрику, файла привязок нет), отвечает
`skipped` СЛОВАМИ: пропуск вердикт не красит, но и не молчит, а пропуск с
признаком `warning` требует подтверждения владельца при выпуске.

Контракт команды и формы вердикта — catalog/tools/PARITY-CONTRACT.md.
Инструмент — не читатель: импорты из cdpm/vmp ему разрешены.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATALOG_DIR = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Корень дерева ищет инструмент извлечения (`repo_root`): каталог вправе
# лежать НЕ прямо под корнем — служба каталога держит раскладку
# `<репозиторий>/data/catalog`, и родитель папки каталога там не несёт
# ни `template_catalog`, ни `vmp`. Импорт идёт после того, как он положил
# найденный корень в `sys.path`.
import build_from_code as bfc                                   # noqa: E402

REPO = bfc.REPO
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import template_catalog as tc                                   # noqa: E402
from template_catalog import candidates as tcc                   # noqa: E402
from template_catalog import growth as tcg                       # noqa: E402
from template_catalog import writer as tcw                       # noqa: E402
from vmp import kotlin_tables as ktab                            # noqa: E402

SNAPSHOT_FILE = REPO / "tests" / "fixtures" / "registry_step1.json"
DEFAULT_BINDINGS = REPO / "vmp" / "bindings.json"
CATALOG_ENV = "CREW_TEMPLATE_CATALOG"

#: Что именно судит паритет из роста таблиц решателя, а что остаётся за
#: гейтом привязок: слова свидетельства, чтобы «зелёный паритет» не
#: читался шире, чем он есть (критика A 1.5 проекта пакета).
GROWTH_JUDGED = {
    "knobs": "L05/L18", "metrics": "L08/L22",
    "family_to_constraint": "L14/L22", "history_families": "L15",
    "history_units": "L05", "logic_forms": "L22 (привязки)",
    "constants": "только гейт привязок", "sit_knobs": "L05",
    "sit_param": "L05",
}

#: Порядок правил вердикта. L12 снят частью 7-А шага 7 (снимок словарей
#: CDPM и его инструмент — зона CDPM); L24 («два читателя Kotlin») живёт
#: только в pytest-файле: у команды читатель один, и правило было бы
#: тавтологией.
RULE_ORDER = ("L01", "L02", "L03", "L04", "L05", "L05b", "L06", "L07",
              "L08", "L08b", "L09", "L10", "L11", "L13", "L14", "L15",
              "L16", "L17", "L18", "L19", "L20", "L21", "L22", "L23")

#: Источник имени параметра вне таблиц решателя.
SRC_CDPM = "словарь CDPM"
SRC_SOLVER = "словарь решателя"
SRC_FAMILY = "семья"


class ParityError(RuntimeError):
    """Судить нечем: код возврата 2, а не красный вердикт."""


# ---------------------------------------------------------------- форма
@dataclass
class Rule:
    """Исход одного правила. `ok` — True | False | None (не судилось)."""
    id: str
    ok: bool | None
    words: str | None = None
    numbers: dict = field(default_factory=dict)
    skipped: str | None = None
    warning: bool = False
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out: dict = {"ok": self.ok}
        if self.ok is False:
            out["words"] = self.words
        if self.numbers:
            out["numbers"] = dict(self.numbers)
        if self.ok is None:
            out["skipped"] = self.skipped
            if self.warning:
                out["warning"] = True
        out.update(self.extra)
        return out

    def line(self) -> str:
        mark = {True: "✓", False: "✗", None: "—"}[self.ok]
        tail = self.words if self.ok is False else (
            f"skipped: {self.skipped}" if self.ok is None else "")
        nums = " | ".join(f"{k} {v}" for k, v in self.numbers.items())
        parts = [p for p in (tail, nums) if p]
        return f"{self.id} {mark} " + " | ".join(parts)


@dataclass
class Verdict:
    rules: list
    catalog: dict
    rc: int = 0

    @property
    def ok(self) -> bool:
        return not any(r.ok is False for r in self.rules)

    @property
    def words(self) -> str | None:
        red = [f"{r.id}: {r.words}" for r in self.rules if r.ok is False]
        return "; ".join(red) if red else None

    def counts(self) -> dict:
        return {"rules": len(self.rules),
                "green": sum(1 for r in self.rules if r.ok is True),
                "red": sum(1 for r in self.rules if r.ok is False),
                "skipped": sum(1 for r in self.rules if r.ok is None),
                "warning": sum(1 for r in self.rules if r.warning)}

    def as_dict(self) -> dict:
        return {"rc": self.rc if self.rc else (0 if self.ok else 1),
                "ok": self.ok, "catalog": dict(self.catalog),
                "growth_judged": dict(GROWTH_JUDGED),
                "counts": self.counts(),
                "rules": {r.id: r.as_dict() for r in self.rules},
                "words": self.words}


def failed(path, words: str) -> dict:
    """Вердикт «не судилось» (код 2) — тем же объектом, что и обычный."""
    return {"rc": 2, "ok": False, "catalog": {"path": str(path)},
            "growth_judged": dict(GROWTH_JUDGED),
            "counts": {"rules": 0, "green": 0, "red": 0, "skipped": 0,
                       "warning": 0},
            "rules": {}, "words": words}


# ----------------------------------------------------------- свидетели
def load_witnesses() -> dict:
    """Свидетели закона — снимаются с кода COSMOS один раз."""
    from cdpm import data_checks as dc
    from cdpm import model as cm
    from vmp import bindings_gate as bg
    from vmp import stand_tasks as st
    from vmp.run import roster_certify as rc
    try:
        snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ParityError(f"снимок заморозки {SNAPSHOT_FILE} не читается: {e}")
    try:
        hard = bfc.load_seed()
    except (OSError, ValueError) as e:
        raise ParityError(f"посев {bfc.SEED} не читается: {e}")
    try:
        kotlin = ktab.tables()
    except Exception as e:                                    # noqa: BLE001
        raise ParityError(f"таблицы решателя не прочитаны: {e}")
    try:
        from vmp import bindings_build as bb
        bindings_seed = bb.load_seed()
    except Exception:                                         # noqa: BLE001
        bindings_seed = None
    return {
        "kotlin": kotlin,
        "snapshot": snapshot,
        "hard": hard,
        # Посев привязок — свидетель строк «есть»: движок берёт его
        # ДАННЫМИ, чтобы стенд мог назвать запись, которой на дереве ещё
        # нет. Ключа командной строки для подмены нет (как и у прочих
        # свидетелей).
        "bindings_seed": bindings_seed,
        "data_checks": {"templates": dict(dc.TEMPLATES),
                        "wave_soft": set(dc.WAVE_SOFT_TEMPLATES),
                        "history_form": set(dc.HISTORY_FORM_TEMPLATES),
                        "area_trio": sorted(dc._AREA_TRIO)},
        "certify": {"people": set(rc.PEOPLE_FAMILIES),
                    "world_param": set(rc.WORLD_PARAM_FAMILIES),
                    "conditions": dict(rc.EXTENSION_CONDITIONS),
                    "soft_measure": rc.SOFT_MEASURE_TEMPLATE,
                    "world_param_template": rc.WORLD_PARAM_TEMPLATE},
        "cdpm": {"zones": list(cm.ZONES),
                 "fit_categories": list(cm.FIT_CATEGORIES),
                 "metrics": copy.deepcopy(cm.METRICS)},
        "kinds": {"kotlin": set(bg.KOTLIN_KINDS), "pure": set(st.PURE_KINDS),
                  "solver": set(st.SOLVER_KINDS),
                  "world_forms": set(st.BUILDABLE_WORLD_FORMS)},
    }


def cdpm_metrics_of(cat, wit: dict) -> dict:
    """Метрики, которые CDPM знает у СУДИМОГО каталога, — проекция моста по
    экземпляру (`cdpm.catalog_bridge.metrics_of`, 09.09.2026: критерий
    «есть» с `kotlin_metric` в `computable_by`, задачи по зоне записи).

    Паритет судит временный каталог «живой ⊕ кандидат», а словарь модели
    CDPM снят с ЖИВОГО каталога (по переменной среды либо умолчанию), и с
    ним L08b предупреждал «CDPM ещё не знает метрику» о ЛЮБОМ
    кандидате-метрике по построению (сквозная проверка 09.09.2026:
    duty_days_total). Второго закона в COSMOS нет: проекцию считает мост
    CDPM; старый мост без функции — словарь модели из свидетелей."""
    try:
        from cdpm import catalog_bridge as cb
    except ImportError:                                 # pragma: no cover
        return wit["cdpm"]["metrics"]
    fn = getattr(cb, "metrics_of", None)
    if fn is None:
        return wit["cdpm"]["metrics"]
    return copy.deepcopy(fn(cat))


def _snapshot_families(snapshot: dict) -> dict:
    return {f: sorted(ps) for f, ps in snapshot["families"].items()}


def legacy_rev_of(families_map: dict) -> str:
    canon = json.dumps({f: sorted(ps) for f, ps in families_map.items()},
                       ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------- судимый каталог
def candidate_files_of(folder) -> list:
    """Файлы кандидатов рядом с каталогом, по адресу записи."""
    root = Path(folder) / "candidates"
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.json") if p.is_file())


class Judged:
    """Судимый каталог: папка на диске, читатель и слова о кандидатах.

    Кандидаты приходят двумя путями. `with_candidates` — движок сам
    находит файлы `<папка>/candidates/**` и МАТЕРИАЛИЗУЕТ их поверх
    папки во временный каталог (`materialized = True`): привязки такому
    каталогу он строит в памяти, файла привязок у него нет и быть не
    может. `candidate_files` (ключ `--candidate`) — папка УЖЕ собрана
    зовущим («живой ⊕ кандидат» пятого гейта CR): движок ничего не
    материализует, а берёт из файлов адрес записи и номер заявки, чтобы
    правило L21 «запись ≡ заявке» судило, а не отвечало «сверять не с
    чем». Без этого ключа гейт CR оставлял единственный независимый
    якорь владельца непросуженным."""

    def __init__(self, folder, *, with_candidates: bool = False,
                 candidate_files=None):
        self.source = Path(folder)
        self.tmp = None
        self.candidates = []
        self.candidate_requests = {}
        self.materialized = False
        path = self.source
        explicit = list(candidate_files or [])
        files = explicit or (candidate_files_of(self.source)
                             if with_candidates else [])
        loaded = []
        for p in files:
            # Разбор — ДО временной папки: негодный файл-кандидат отвечает
            # словами со своим адресом и не оставляет за собой мусора.
            try:
                loaded.append((Path(p), tcc.load_candidate(p)))
            except Exception as e:                            # noqa: BLE001
                raise ParityError(f"кандидат {p} не разобран: {e}")
        for p, cand in loaded:
            addr = f"{cand.get('section')}/{cand.get('id')}"
            self.candidates.append(addr)
            self.candidate_requests[addr] = (
                str(cand.get("request_id")) if cand.get("request_id")
                else None)
        if loaded and not explicit:
            self.tmp = Path(tempfile.mkdtemp(prefix="parity-"))
            self.materialized = True
            cur, where = self.source, loaded[0][0]
            try:
                for i, (p, cand) in enumerate(loaded):
                    where = p
                    cur = tcc.materialize(cur, cand, self.tmp / f"cat{i}")
            except Exception as e:                            # noqa: BLE001
                self.close()
                raise ParityError(f"кандидат {where} не лёг на каталог: {e}")
            path = cur
        self.path = path
        try:
            self.cat = tc.load(path)
        except Exception as e:                                # noqa: BLE001
            self.close()
            raise ParityError(f"каталог {path} не прочитан: {e}")
        self.source_cat = (self.cat if path == self.source
                           else tc.load(self.source))

    def close(self) -> None:
        if self.tmp is not None and self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def in_memory_bindings(cat, seed=None) -> tuple[dict | None, str | None]:
    """Привязки судимого каталога В ПАМЯТИ: временный каталог «живой ⊕
    кандидаты» своего файла привязок не имеет и иметь не может, а без
    строк правила L19 и L22 судить нечем.

    Отказ генератора здесь — СВОЙСТВО КАНДИДАТА, а не «судить нечем»:
    слова возвращаются, и правила L19/L22 краснеют ими (`strict`).
    Прежде любой отказ становился молчаливым пропуском, и кандидат,
    объявивший вид без строки посева, проходил свидетеля дерева
    зелёным."""
    try:
        from vmp import bindings_build as bb
        doc = bb.build(cat, commit="0" * 40,
                       generated_at="2026-01-01T00:00:00+00:00",
                       stand_results=None, seed=seed)
        return doc, None
    except Exception as e:                                    # noqa: BLE001
        return None, f"привязки в памяти не собрались: {e}"


def read_bindings(path) -> tuple[dict | None, str | None]:
    p = Path(path)
    if not p.is_file():
        return None, f"файла привязок {p} нет — строки судит гейт привязок"
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return None, f"{p} не читается: {e}"
    if not isinstance(doc, dict) or not isinstance(doc.get("bindings"), list):
        return None, f"{p}: нет перечня bindings"
    return doc, None


# ------------------------------------------------------------- правила
def _addr_records(cat) -> list:
    out = [(f"families/{r['id']}", "families", r)
           for r in cat.families_full(status=None)]
    out += [(f"criteria/{r['id']}", "criteria", r)
            for r in cat.criteria(status=None).values()]
    return out


def _gate_line(entry: dict) -> bool:
    return (entry.get("by") == tcc.GATE_HISTORY_BY
            and entry.get("note") == tcc.GATE_HISTORY_NOTE)


def rule_l01(cat, base, delta, snapshot) -> Rule:
    """Семьи, критерии и статусы: база заморозки ⊕ вычисленная дельта."""
    left = cat.families()
    right = dict(_snapshot_families(snapshot))
    for name, add in (delta.get("params_to_families") or {}).items():
        right.setdefault(add["family"], []).append(name)
    # Семья сверх базы БЕЗ параметров (например, crew_unavailability)
    # не попадёт в params_to_families. Она названа families_added —
    # дельтой по имени семьи: иначе L01 запрещал бы законный рост
    # беспараметрических семей по построению.
    for fid in (delta.get("families_added") or {}):
        right.setdefault(fid, [])
    right = {f: sorted(ps) for f, ps in right.items()}
    withdrawn = {r["id"] for r in cat.families_full(status=None)
                 if r.get("status") != "есть"}
    lost = sorted(set(base["families"]) - set(left) - withdrawn)
    if lost:
        return Rule("L01", False, f"семьи базы заморозки пропали из каталога "
                                  f"и не сняты записью: {lost}")
    if left != right:
        bad = sorted(set(left) ^ set(right)) or [
            f for f in sorted(set(left) & set(right)) if left[f] != right[f]]
        return Rule("L01", False, f"карта семей разошлась с базой заморозки "
                                  f"⊕ дельтой: {bad}")
    crits = cat.criteria()
    right_crits = sorted(set(base["criteria"])
                         | set(delta.get("criteria_added") or []))
    if sorted(crits) != right_crits:
        return Rule("L01", False, f"критерии каталога {sorted(crits)} ≠ базе "
                                  f"⊕ дельте {right_crits}")
    return Rule("L01", True, numbers={
        "families": len(left), "base": len(base["families"]),
        "added": len(delta.get("families_added") or {}),
        "withdrawn": len(withdrawn),
        "params": sum(len(v) for v in left.values()),
        "criteria": len(crits),
        "criteria_added": len(delta.get("criteria_added") or [])})


def rule_l02_l03(cat, snapshot) -> Rule:
    """Заморозка атрибутов базовых записей: снимок шага 1 — жёстко."""
    rmt = dict(snapshot["rule_mechanic_types"])
    meta = cat.families_meta(status=None)
    bad = []
    for fid, spec in sorted(rmt.items()):
        m = meta.get(fid)
        if m is None:
            bad.append(f"{fid}: записи нет")
            continue
        kind, zone = snapshot["family_kind_zone"][fid]
        for what, left, right in (
                ("class", m["class"], spec["class"]),
                ("title", m["title"] if "title" in m else
                 cat.family(fid)["title"], spec["title"]),
                ("kind", m["kind"], kind), ("zone", m["zone"], zone),
                ("violation_unit", m["violation_unit"],
                 snapshot["family_violation_unit"].get(fid))):
            if left != right:
                bad.append(f"{fid}.{what}: в каталоге {left!r}, в снимке "
                           f"{right!r}")
    spec_bad = []
    for cid, spec in sorted(snapshot["criteria_spec"].items()):
        try:
            rec = cat.criterion(cid)
        except Exception:                                     # noqa: BLE001
            spec_bad.append(f"{cid}: записи нет")
            continue
        sem = spec.get("semantics") or {}
        if rec["title"] != spec["title"]:
            spec_bad.append(f"{cid}.title")
        if rec.get("needs") != spec.get("needs"):
            spec_bad.append(f"{cid}.needs")
        if (rec["semantics"] or {}).get("formula") != sem.get("formula"):
            spec_bad.append(f"{cid}.formula")
        want = sem.get("rates_form")
        if (rec["semantics"] or {}).get("rates_form") != want:
            spec_bad.append(f"{cid}.rates_form")
        if rec["class"] != snapshot["criterion_types"][cid]["class"]:
            spec_bad.append(f"{cid}.class")
    problems = bad + spec_bad
    if problems:
        return Rule("L02", False,
                    "пересмотр заморозки базовых записей — новым файлом "
                    "снимка по слову владельца: " + "; ".join(problems[:5]))
    return Rule("L02", True, numbers={"families": len(rmt),
                                      "criteria": len(snapshot["criteria_spec"])})


def rule_l04(cat, base, delta) -> Rule:
    """Карта параметр → семья: база ⊕ дельта, без числа-литерала."""
    params = cat.params(status=None)
    bound = {n for n, p in params.items() if p["family"] is not None}
    right = dict(base["param_to_family"])
    for name, add in (delta.get("params_to_families") or {}).items():
        right[name] = add["family"]
    withdrawn = {r["id"] for r in cat.families_full(status=None)
                 if r.get("status") != "есть"}
    right = {n: f for n, f in right.items() if f not in withdrawn}
    if bound != set(right):
        return Rule("L04", False,
                    f"связанные имена каталога и база ⊕ дельта разошлись: "
                    f"лишние {sorted(bound - set(right))}, потерянные "
                    f"{sorted(set(right) - bound)}")
    wrong = [n for n, f in sorted(right.items()) if params[n]["family"] != f]
    if wrong:
        return Rule("L04", False, f"имена в чужой семье: {wrong}")
    return Rule("L04", True, numbers={"bound": len(bound),
                                      "base": len(base["param_to_family"]),
                                      "added": len(right) - len(base["param_to_family"])})


def rule_l05a(cat, base, kotlin, hard, requests) -> Rule:
    """Вселенная имён: таблицы решателя ⊆ каталог ⊆ словарь ∪ решатель ∪
    заявки. Ручка, которой каталог не знает, — красный словами."""
    params = cat.params(status=None)
    cdpm_names = set(hard["cdpm_dictionaries"]["params"])
    solver = dict(kotlin["knobs"])
    hist = dict(kotlin["history_units"])
    sit = kotlin["sit_param"]
    req = bfc.request_params(requests)
    missing = []
    for name, knob in sorted(solver.items()):
        if name not in params:
            missing.append(f"ручка решателя {name} ({knob['unit']}, "
                           f"{knob['relation']}) — параметра нет ни в "
                           f"записи, ни в словаре")
    for name, (unit, rel) in sorted(hist.items()):
        if name not in params:
            missing.append(f"имя истории решателя {name} ({unit}, {rel}) — "
                           f"параметра нет ни в записи, ни в словаре")
    if sit not in params:
        missing.append(f"параметр стыковки решателя {sit} — параметра нет "
                       f"ни в записи, ни в словаре")
    unknown = sorted(set(params) - cdpm_names - set(solver) - set(hist)
                     - {sit} - set(req))
    # Числа ПЕЧАТАЮТСЯ, а не утверждаются, — и у красного правила тоже:
    # без них красный L05 молчал бы о том, сколько имён он рассмотрел, а
    # свидетель чисел выпуска (`NUMBERS_RELEASE`) не смог бы их прочесть
    # на ветке, где решатель ушёл вперёд папки выпуска.
    bound = {n for n, p in params.items() if p["family"] is not None}
    nums = {
        "params": len(params), "cdpm": len(cdpm_names), "knobs": len(solver),
        "history": len(hist), "bound": len(bound),
        "unbound": len(params) - len(bound),
        "solver_only": len(sorted(set(solver) - cdpm_names - bound)),
        "by_request": len(sorted(set(req) & set(params) - cdpm_names
                                 - set(solver) - set(hist)))}
    if missing or unknown:
        words = "; ".join(missing[:4])
        if unknown:
            addr = {n: (params[n]["family"] or "unbound_params")
                    for n in unknown}
            words = (words + ("; " if words else "")
                     + "; ".join(f"имя {n} записи {addr[n]}: единицу не "
                                 f"подтверждает ни словарь CDPM, ни таблицы "
                                 f"решателя, ни заявка" for n in unknown[:4]))
        return Rule("L05", False, words, numbers=nums)
    return Rule("L05", True, numbers=nums)


def rule_l05b(cat, kotlin, hard, requests) -> Rule:
    """Единицы, отношения, уточнители и пояс — из кода решателя, а имени
    вне решателя — из словаря CDPM либо из черновика принятой заявки."""
    params = cat.params(status=None)
    cdpm_units = dict(hard["cdpm_dictionaries"]["params"])
    owner_rel = dict(hard.get("params_relation_set") or {})
    area = set(hard["cdpm_dictionaries"]["vocabularies"]["area_param_names"])
    req = bfc.request_params(requests)
    knobs, hist = dict(kotlin["knobs"]), dict(kotlin["history_units"])
    sit_rels = {k["relation"] for k in kotlin["sit_knobs"].values()}
    bad = []
    for name, unit in sorted(cdpm_units.items()):
        if name in params and params[name]["unit"] != unit:
            bad.append(f"{name}: единица {params[name]['unit']!r}, в словаре "
                       f"CDPM {unit!r}")
    for name, knob in sorted(knobs.items()):
        p = params.get(name)
        if p is None:
            continue
        if p["unit"] != knob["unit"]:
            bad.append(f"{name}: единица {p['unit']!r}, по таблице решателя "
                       f"{knob['unit']!r}")
        if p["relation"] != knob["relation"]:
            bad.append(f"{name}: отношение {p['relation']!r}, по таблице "
                       f"решателя {knob['relation']!r}")
        if knob["keyed"] and p["qualifier"] != "base_code":
            bad.append(f"{name}: ключевая ручка решателя без уточнителя "
                       f"base_code")
    for name, (unit, rel) in sorted(hist.items()):
        p = params.get(name)
        if p is None:
            continue
        if (p["unit"], p["relation"]) != (unit, rel):
            bad.append(f"{name}: ({p['unit']!r}, {p['relation']!r}), по "
                       f"таблице истории решателя ({unit!r}, {rel!r})")
    sit = kotlin["sit_param"]
    if sit in params and len(sit_rels) == 1 \
            and params[sit]["relation"] != next(iter(sit_rels)):
        bad.append(f"{sit}: отношение стыковки разошлось с решателем")
    keyed = {n for n, k in knobs.items() if k["keyed"]}
    base_code = {n for n, p in params.items() if p["qualifier"] == "base_code"}
    if not keyed:
        bad.append("ни одной ключевой ручки в таблицах решателя не прочитано")
    elif keyed != base_code:
        bad.append(f"ключевые ручки решателя {sorted(keyed)} ≠ именам с "
                   f"уточнителем base_code {sorted(base_code)}")
    for name in sorted(area & set(params)):
        if params[name]["qualifier"] != "area":
            bad.append(f"{name}: имя области без уточнителя area")
    for name, qual in sorted(bfc.QUALIFIERS.items()):
        if name in params and params[name]["qualifier"] != qual:
            bad.append(f"{name}: уточнитель {params[name]['qualifier']!r}, по "
                       f"коду CDPM {qual!r}")
    outside = sorted(set(params) - set(knobs) - set(hist) - {sit})
    for name in outside:
        p = params[name]
        if p["unit"] is not None and name not in cdpm_units \
                and not (name in req and req[name][0] == p["unit"]):
            bad.append(f"имя {name} записи "
                       f"{p['family'] or 'unbound_params'}: единицу не "
                       f"подтверждает ни словарь CDPM, ни таблицы решателя, "
                       f"ни заявка")
        if p["relation"] is None:
            continue
        if owner_rel.get(name) == p["relation"]:
            continue
        if name in req and req[name][1] == p["relation"]:
            continue
        bad.append(f"имя {name}: отношение {p['relation']!r} вне таблиц "
                   f"решателя не подтверждает ни слово владельца, ни заявка")
    for name in sorted(owner_rel):
        if name in knobs or name in hist or name == sit:
            bad.append(f"{name}: отношение назначено словом владельца поверх "
                       f"таблиц решателя — отношение пишет код")
    for name, p in sorted(params.items()):
        if p["tz_required"] != (p["unit"] == "time_of_day_minutes"):
            bad.append(f"{name}: признак пояса не по единице")
    if bad:
        return Rule("L05b", False, "; ".join(bad[:5]))
    return Rule("L05b", True, numbers={
        "keyed": len(keyed), "area": len(area & set(params)),
        "outside_solver": len(outside), "by_owner": len(owner_rel),
        "by_request": len(req)})


def rule_l06(cat, order_seed) -> Rule:
    """Лексикон: симметрия дополнений и порядок — формулой писателя."""
    lx = cat.lexicon(status=None)
    words = lx["subject_words"]
    comps = lx["complements"]
    bad = []
    for a, bs in sorted(comps.items()):
        for b in bs:
            if a not in comps.get(b, []):
                bad.append(f"дополнение {a} → {b} не симметрично")
    families_with_words = sorted(words)
    if sorted(lx["order"]) != families_with_words:
        bad.append(f"порядок лексикона не перестановка семей со словами: "
                   f"{sorted(set(lx['order']) ^ set(families_with_words))}")
    want = tcw.refresh_lexicon_order(order_seed,
                                     cat.families_full(status=None))
    if list(lx["order"]) != list(want):
        bad.append("порядок лексикона не равен формуле писателя "
                   "refresh_lexicon_order")
    if bad:
        return Rule("L06", False, "; ".join(bad[:5]))
    return Rule("L06", True, numbers={"subject_words": len(words),
                                      "complements": len(comps),
                                      "order": len(lx["order"])})


def rule_l07(cat, base, snapshot, hard, wit) -> Rule:
    """Словари-перечни, зоны, категории, виды и формы миров."""
    d = cat.dictionaries()
    voc = hard["cdpm_dictionaries"]["vocabularies"]
    meta = cat.families_meta(status=None)
    bad = []
    for key in ("param_units", "code_units", "param_relations", "param_tz",
                "area_qualifiers", "fit_categories"):
        if sorted(d.get(key) or []) != sorted(voc[key]):
            bad.append(f"словарь {key} разошёлся со словарём CDPM")
    if d.get("area_param_names") != sorted(voc["area_param_names"]):
        bad.append("имена области разошлись со словарём CDPM")
    if sorted(d.get("violation_units") or []) != sorted(snapshot["violation_units"]):
        bad.append("единицы нарушения разошлись со снимком заморозки")
    if set(d.get("zones") or []) != set(wit["cdpm"]["zones"]):
        bad.append("зоны разошлись с cdpm.model.ZONES")
    if sorted(d.get("fit_categories") or []) != sorted(wit["cdpm"]["fit_categories"]):
        bad.append("категории годности разошлись с cdpm.model.FIT_CATEGORIES")
    kinds = {m["kind"] for m in meta.values()}
    if not kinds <= set(d.get("kinds") or []):
        bad.append(f"виды записей {sorted(kinds)} шире словаря kinds")
    zones = {m["zone"] for m in meta.values()}
    if not zones <= set(d.get("zones") or []):
        bad.append(f"зоны записей {sorted(zones)} шире словаря zones")
    classes = {m["class"] for m in meta.values()} | {
        r["class"] for r in cat.criteria(status=None).values()}
    if not classes <= set(d.get("classes") or []):
        bad.append(f"классы записей {sorted(classes)} шире словаря classes")
    need_kinds = (wit["kinds"]["kotlin"] | wit["kinds"]["pure"]
                  | wit["kinds"]["solver"])
    if not need_kinds <= set(d.get("executor_kinds") or []):
        bad.append(f"словарь видов исполнителей уже кода: не хватает "
                   f"{sorted(need_kinds - set(d.get('executor_kinds') or []))}")
    need_forms = wit["kinds"]["world_forms"] | set(base.get("world_forms") or [])
    if not need_forms <= set(d.get("world_forms") or {}):
        bad.append(f"формы миров уже кода стенда и базы: не хватает "
                   f"{sorted(need_forms - set(d.get('world_forms') or {}))}")
    need_codes = set(base.get("synthetic_codes") or [])
    if not need_codes <= set(d.get("synthetic_codes") or {}):
        bad.append(f"синтетические коды уже базы: не хватает "
                   f"{sorted(need_codes - set(d.get('synthetic_codes') or {}))}")
    if bad:
        return Rule("L07", False, "; ".join(bad[:5]))
    return Rule("L07", True, numbers={
        "zones": len(d.get("zones") or []),
        "executor_kinds": len(d.get("executor_kinds") or []),
        "world_forms": len(d.get("world_forms") or {}),
        "violation_units": len(d.get("violation_units") or [])})


def rule_l08(cat, kotlin) -> Rule:
    """Критерии-метрики решателя: перечень, зона по задачам, отказ."""
    metrics = kotlin["metrics"]
    crits = cat.criteria(status=None)
    named = sorted(cid for cid, rec in crits.items()
                   if "kotlin_metric" in (rec.get("computable_by") or []))
    if named != sorted(metrics):
        return Rule("L08", False,
                    f"критерии-метрики каталога {named} ≠ словарю метрик "
                    f"решателя {sorted(metrics)}")
    bad = []
    for cid in named:
        rec, m = crits[cid], metrics[cid]
        zone = bfc.ZONE_BY_TASKS.get(tuple(sorted(m["tasks"])))
        if zone is None:
            bad.append(f"{cid}: задачи метрики {sorted(m['tasks'])} — зона не "
                       f"выводится")
        elif rec["zone"] != zone:
            bad.append(f"{cid}: зона {rec['zone']!r}, по задачам решателя "
                       f"{zone!r}")
        if rec["class"] != "extension":
            bad.append(f"{cid}: класс {rec['class']!r}, у метрики решателя "
                       f"ожидается extension")
        if rec.get("refusal") != m["refusal"]:
            bad.append(f"{cid}: отказ записи не дословен отказу решателя")
    if bad:
        return Rule("L08", False, "; ".join(bad[:5]))
    return Rule("L08", True, numbers={"metrics": len(named)})


def rule_l08b(cat, kotlin, wit) -> Rule:
    """Второй свидетель зоны метрики — задачи, которые знает CDPM."""
    known = wit["cdpm"]["metrics"]
    metrics = kotlin["metrics"]
    unknown = sorted(set(metrics) - set(known))
    if unknown:
        return Rule("L08b", None,
                    skipped=f"CDPM ещё не знает метрику {', '.join(unknown)} "
                            f"— зону подтверждает только словарь решателя",
                    warning=True)
    bad = [cid for cid in sorted(metrics)
           if tuple(sorted(known[cid]["tasks"])) != tuple(sorted(metrics[cid]["tasks"]))]
    if bad:
        return Rule("L08b", False, f"задачи метрик у CDPM и решателя "
                                   f"разошлись: {bad}")
    return Rule("L08b", True, numbers={"metrics_known_to_cdpm": len(metrics)})


def rule_l09(cat, wit) -> Rule:
    """Шаблоны проверок ≡ cdpm.data_checks ∪ сертификатные ∪ префиксы."""
    ct = cat.check_templates()
    dcw = wit["data_checks"]
    cert = {wit["certify"]["soft_measure"], wit["certify"]["world_param_template"]}
    prefixes = {"история-*", "горизонт-*"}
    want = set(dcw["templates"]) | cert | prefixes
    bad = []
    if set(ct) != want:
        bad.append(f"перечень шаблонов разошёлся с кодом: лишние "
                   f"{sorted(set(ct) - want)}, потерянные "
                   f"{sorted(want - set(ct))}")
    for name, fields in sorted(dcw["templates"].items()):
        spec = ct.get(name)
        if spec is None:
            continue
        if sorted(spec["fields"]) != sorted(fields):
            bad.append(f"{name}: поля разошлись с data_checks")
        group = ("soft_wave" if name in dcw["wave_soft"]
                 else "history_form" if name in dcw["history_form"]
                 else "data")
        if spec["group"] != group:
            bad.append(f"{name}: группа {spec['group']!r}, по коду {group!r}")
        opt = bfc.CHECK_TEMPLATE_OPTIONALS.get(name, {})
        groups = [list(g) for g in opt.get("optional_groups", [])]
        if name == "минимальный-разрыв-часов":
            groups = [list(dcw["area_trio"])]
        if sorted(spec["optional_fields"]) != sorted(opt.get("optional_fields", [])):
            bad.append(f"{name}: необязательные поля разошлись")
        if sorted(sorted(g) for g in spec["optional_groups"]) != \
                sorted(sorted(g) for g in groups):
            bad.append(f"{name}: группы необязательных полей разошлись")
        if sorted(spec["at_least_one_of"]) != sorted(opt.get("at_least_one_of", [])):
            bad.append(f"{name}: «хотя бы одно из» разошлось")
    for name in sorted(cert):
        if ct.get(name, {}).get("group") != "certificate":
            bad.append(f"{name}: сертификатный шаблон не в группе certificate")
    for name in sorted(prefixes):
        if ct.get(name, {}).get("group") != "builtin_prefix":
            bad.append(f"{name}: встроенный префикс не в своей группе")
    if bad:
        return Rule("L09", False, "; ".join(bad[:5]))
    return Rule("L09", True, numbers={"templates": len(ct),
                                      "data_checks": len(dcw["templates"])})


def rule_l10(judged, base, delta, snapshot) -> Rule:
    """Отпечаток, старая ревизия и двери продуктов."""
    cat = judged.cat
    fp = cat.fingerprint()
    bad = []
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        bad.append(f"отпечаток {fp!r} не 64 знака hex")
    if fp != cat.declared_fingerprint():
        bad.append("отпечаток тела и шапки разошлись")
    old = legacy_rev_of(cat.families())
    if old != cat.header.get("legacy_registry_rev"):
        bad.append(f"старая ревизия шапки {cat.header.get('legacy_registry_rev')!r} "
                   f"≠ формуле над семьями {old!r}")
    # Две сверки, а НЕ цепное сравнение: `a != b != c` в Python значит
    # `a != b and b != c`, а снимок и блок `base` несут одну ревизию по
    # построению — второй операнд был ложен всегда, и свидетель формулы
    # молчал при любом её расхождении.
    step1_old = legacy_rev_of(_snapshot_families(snapshot))
    if step1_old != snapshot["registry_rev"]:
        bad.append(f"формула старой ревизии над семьями снимка даёт "
                   f"{step1_old}, а снимок называет "
                   f"{snapshot['registry_rev']} — разошлась формула, а не "
                   f"данные")
    if snapshot["registry_rev"] != base["legacy_registry_rev"]:
        bad.append(f"ревизия снимка заморозки {snapshot['registry_rev']} и "
                   f"ревизия блока base {base['legacy_registry_rev']} "
                   f"разошлись — база заморозки и снимок описывают разные "
                   f"каталоги")
    if delta and delta.get("legacy_registry_rev") != old:
        bad.append("старая ревизия дельты отстала от каталога")
    if bad:
        return Rule("L10", False, "; ".join(bad[:5]))
    env = os.environ.get(CATALOG_ENV)
    same = (Path(env).resolve() == judged.path.resolve()) if env else \
        (judged.path.resolve() == CATALOG_DIR.resolve())
    if not same:
        return Rule("L10", None, numbers={"fingerprint_head": old},
                    skipped=f"двери читают другой каталог "
                            f"({env or CATALOG_DIR}) — отпечаток продуктов "
                            f"судить нечем")
    from cdpm import model as cm
    from vmp import template_registry as tr
    doors = []
    if tr.registry_rev() != fp:
        doors.append(f"обёртка COSMOS называет {tr.registry_rev()}")
    if cm.registry_fingerprint() != fp:
        doors.append(f"штамп CDPM называет {cm.registry_fingerprint()}")
    if doors:
        return Rule("L10", False, f"двери читают судимый каталог, а отпечаток "
                                  f"{fp} не назвали: {'; '.join(doors)}")
    return Rule("L10", True, numbers={"legacy_rev_head": old})


def rule_l11(cat) -> Rule:
    """Годность каталога: ноль ошибок и ноль предупреждений validate().

    Маркеры заказчика — свидетель запретных слов. Их отсутствие на
    машине законно (перечень лежит вне дерева) и молчаливо; а вот
    негодный перечень, неизвестный вид проверки или сломанный импорт —
    НЕ то же самое: правило судило бы без запретных слов и зеленело бы
    тем охотнее, чем хуже свидетель. Такой случай — пропуск словами с
    признаком предупреждения (на выпуске он требует подтверждения
    владельца)."""
    patterns: list = []
    try:
        from tools import customer_markers as cmk
    except Exception as e:                                    # noqa: BLE001
        return Rule("L11", None, warning=True,
                    skipped=f"перечень маркеров заказчика не подключился "
                            f"({type(e).__name__}: {e}) — запретные слова "
                            f"судить нечем")
    try:
        spec = cmk.load_checks()
    except Exception as e:                                    # noqa: BLE001
        return Rule("L11", None, warning=True,
                    skipped=f"перечень маркеров заказчика не прочитан "
                            f"({type(e).__name__}: {e}) — запретные слова "
                            f"судить нечем")
    try:
        for check in (spec or {}).get("checks") or []:
            patterns += cmk._patterns(check)
    except Exception as e:                                    # noqa: BLE001
        return Rule("L11", None, warning=True,
                    skipped=f"перечень маркеров заказчика не разобран "
                            f"({type(e).__name__}: {e}) — запретные слова "
                            f"судить нечем")
    problems = cat.validate(forbidden_terms=patterns)
    errors = [p for p in problems if p.severity == "error"]
    if errors:
        return Rule("L11", False, "; ".join(str(p) for p in errors[:5]))
    if problems:
        return Rule("L11", False,
                    "предупреждения validate() на дереве не законны: "
                    + "; ".join(str(p) for p in problems[:5]))
    return Rule("L11", True, numbers={"markers": len(patterns),
                                      "problems": len(problems)})


def rule_l13(judged, base, snapshot) -> Rule:
    """Переходные носители: опись исполнителей и порядок семей."""
    folder = judged.path
    legacy_path = folder / "transition" / "executors_legacy.json"
    order_path = folder / "transition" / "families_order.json"
    if not legacy_path.is_file():
        if (folder / "tools").is_dir():
            return Rule("L13", False,
                        f"опись исполнителей {legacy_path} пропала с рабочего "
                        f"каталога — она левая сторона гейта «ничего не "
                        f"потеряно»")
        return Rule("L13", None,
                    skipped="описи нет в снимке выпуска — переходные носители "
                            "судит рабочий каталог")
    else:
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            return Rule("L13", False, f"{legacy_path} не читается: {e}")
    bad = []
    want = {f: list(s["executors"]) for f, s in snapshot["rule_mechanic_types"]}
    if legacy != want:
        bad.append("опись исполнителей разошлась со снимком заморозки")
    if base["families"] != sorted(want):
        bad.append("перечень семей базы разошёлся с описью снимка")
    if order_path.is_file():
        order = [str(x) for x in
                 json.loads(order_path.read_text(encoding="utf-8-sig"))]
        present = [str(r["id"]) for r in judged.cat.families_full()]
        seed_order = [str(f) for f, _s in snapshot["rule_mechanic_types"]]
        # Судится ИНВАРИАНТ порядка, а не пересчёт формулы над порядком
        # снимка. Формула `refresh_families_order` накопительна: служба и
        # `materialize` применяют её к ПРЕЖНЕМУ порядку на диске, и после
        # двух сборок подряд (сначала семья `zeta`, потом `alpha`) на
        # диске законно лежит […, zeta, alpha], а пересчёт от снимка дал
        # бы […, alpha, zeta]. Прежнее правило краснело бы со второй
        # прибавленной семьи — по построению, а не по делу.
        if sorted(order) != sorted(present):
            bad.append(f"порядок семей и записи каталога разошлись: лишние "
                       f"{sorted(set(order) - set(present))}, потерянные "
                       f"{sorted(set(present) - set(order))}")
        elif len(set(order)) != len(order):
            bad.append("в порядке семей есть повтор")
        else:
            base_ids = set(seed_order)
            got_base = [f for f in order if f in base_ids]
            want_base = [f for f in seed_order if f in set(present)]
            if got_base != want_base:
                bad.append("порядок базовых семей разошёлся с порядком "
                           "снимка заморозки")
            tail_at = [i for i, f in enumerate(order) if f not in base_ids]
            if tail_at and tail_at[0] < len(got_base):
                bad.append("прибавленная семья стоит среди базовых — "
                           "формула писателя ставит новые в конец")
    else:
        bad.append(f"нет файла {order_path}")
    if bad:
        return Rule("L13", False, "; ".join(bad[:5]))
    return Rule("L13", True, numbers={"legacy": len(legacy),
                                      "snapshot": len(want)})


def rule_l14(cat, kotlin) -> Rule:
    """Мягкие семьи ≡ TaskPackage.FAMILY_TO_CONSTRAINT, с единицей."""
    meta = cat.families_meta()
    soft = sorted(f for f, m in meta.items() if m["kind"] == "SOFT")
    want = sorted(kotlin["family_to_constraint"])
    if soft != want:
        return Rule("L14", False,
                    f"мягкие семьи каталога {soft} ≠ семьям решателя {want}")
    без = [f for f in soft if meta[f]["violation_unit"] is None]
    if без:
        return Rule("L14", False, f"мягкая семья без единицы нарушения: {без}")
    return Rule("L14", True, numbers={"soft": len(soft)})


#: Свидетели природы семьи (словарь каталога `nature_witnesses`, выпуск
#: 0.5.0) — КТО подтверждает природу записи закону L15. Слово называет
#: сама запись (`nature_confirmed_by`), и закон спрашивает ИМЕННО
#: названного.
WITNESS_WORDS = {
    "solver_tables": "таблицы решателя (LogicModel.HISTORY_FIT_FAMILIES)",
    "certifier_registry": "перечни сертификата ростера "
                          "(PEOPLE_FAMILIES / WORLD_PARAM_FAMILIES)",
    "change_request": "черновик принятой владельцем заявки на изменение",
    "none": "отрицательное свидетельство: ни таблицы решателя, ни "
            "перечни сертификата ростера семьи не знают",
}


def _request_nature(requests, fid: str) -> str | None:
    """Природа семьи по ЧЕРНОВИКУ ПРИНЯТОЙ ЗАЯВКИ — свидетель
    `change_request`.

    Заявку ищет тот же подбор, что у правила L21 (`_request_for`):
    свидетелем считается только заявка в состоянии принятия
    (`REQUEST_WITNESS_STATES`), то есть та, по которой владелец сказал
    слово; тот же след лежит в записи каталога строкой журнала
    `history[]` вида «решение владельца» со ссылкой на её номер. Заявки
    нет либо её черновик природы не называет — None: свидетель семьи не
    знает, и это красное.
    """
    rq = _request_for(requests, "families", fid, None)
    draft = (rq or {}).get("draft")
    nat = draft.get("nature") if isinstance(draft, dict) else None
    return str(nat) if isinstance(nat, str) and nat else None


def rule_l15(cat, kotlin, wit, requests) -> Rule:
    """Природу семьи подтверждает НАЗВАННЫЙ свидетель (выпуск 0.5.0).

    До 0.5.0 закон выводил природу из членства: семья в таблицах
    решателя — «история», в перечнях сертификата ростера — «люди» либо
    «параметр мира», всё остальное — «расписание». Свидетеля выбирал сам
    закон, и завести людскую семью конвейером пополнения было нельзя:
    перечни сертификата ведёт тот же контур, что заводит запись, а
    править их агенту запрещено гейтом `PARITY_WITNESS_CONSTS` — заявка
    краснела по построению.

    С 0.5.0 свидетеля называет ЗАПИСЬ (`nature_confirmed_by`), и закон
    спрашивает его одного. `none` — не «никого не спрашивать», а
    ОТРИЦАТЕЛЬНОЕ свидетельство: семьи нет ни у решателя, ни у
    сертификатора (ровно прежнее «всё остальное — расписание»), и
    красное, если она там есть. Красное и тогда, когда свидетель семьи
    не знает либо называет другую природу.

    Обратная сторона закона осталась: имя, названное таблицами решателя
    или перечнями сертификата, обязано иметь запись ТОЙ ЖЕ природы —
    иначе рост кода уехал бы мимо каталога молча.
    """
    meta = cat.families_meta()
    cert = wit["certify"]
    solver = set(kotlin["history_families"])
    people = set(cert["people"])
    world = set(cert["world_param"])
    bad: list = []
    counted: dict = {}
    for fid, m in sorted(meta.items()):
        nat = m["nature"]
        who = m.get("nature_confirmed_by")
        counted[str(who)] = counted.get(str(who), 0) + 1
        if who == "solver_tables":
            want = "history" if fid in solver else None
        elif who == "certifier_registry":
            want = ("world_param" if fid in world else
                    "people" if fid in people else None)
        elif who == "change_request":
            want = _request_nature(requests, fid)
        elif who == "none":
            seen = []
            if fid in solver:
                seen.append("таблицы решателя (природа «history»)")
            if fid in world:
                seen.append("перечень параметров мира сертификата ростера "
                            "(природа «world_param»)")
            if fid in people:
                seen.append("перечень людских семей сертификата ростера "
                            "(природа «people»)")
            if seen:
                bad.append(f"{fid}: запись назвала свидетелем «none» — "
                           f"отрицательное свидетельство, — а семью знают: "
                           f"{'; '.join(seen)}")
                continue
            want = "schedule"
        else:
            bad.append(f"{fid}: свидетель природы записью не назван "
                       f"({who!r}) — спрашивать некого")
            continue
        who_words = WITNESS_WORDS.get(str(who), str(who))
        if want is None:
            bad.append(f"{fid}: свидетель {who_words} семьи не знает — "
                       f"природу {nat!r} подтвердить нечем")
        elif nat != want:
            bad.append(f"{fid}: природа {nat!r}, свидетель {who_words} "
                       f"называет {want!r}")
    # Обратная сторона: имя, названное кодом, без записи той же природы
    lost = sorted(solver - set(meta))
    if lost:
        bad.append(f"семьи истории решателя без записи каталога: {lost}")
    for fid in sorted(solver & set(meta)):
        if meta[fid]["nature"] != "history":
            bad.append(f"{fid}: таблицы решателя назвали семью историей, "
                       f"запись — {meta[fid]['nature']!r}")
    for fid in sorted(world & set(meta)):
        if meta[fid]["nature"] != "world_param":
            bad.append(f"{fid}: перечень сертификата ростера назвал семью "
                       f"параметром мира, запись — {meta[fid]['nature']!r}")
    for fid in sorted(people & set(meta)):
        if meta[fid]["nature"] != "people":
            bad.append(f"{fid}: перечень сертификата ростера назвал семью "
                       f"людской, запись — {meta[fid]['nature']!r}")
    if bad:
        return Rule("L15", False, "; ".join(bad[:5]))
    natures = {m["nature"] for m in meta.values()}
    numbers = {"natures": len(natures),
               "history": len(kotlin["history_families"])}
    numbers.update({"свидетель " + k: v for k, v in sorted(counted.items())})
    return Rule("L15", True, numbers=numbers)


def rule_l16(cat, wit) -> Rule:
    """Условия семьи потолка продления ≡ roster_certify."""
    conds = cat.conditions()
    want = wit["certify"]["conditions"]
    if set(conds) != set(want):
        return Rule("L16", False,
                    f"условия каталога {sorted(conds)} ≠ условиям кода "
                    f"{sorted(want)}")
    params = cat.params(status=None)
    home = params.get("duty_extension_cap", {}).get("family")
    bad = []
    for name, (_col, rel) in sorted(want.items()):
        c = conds[name]
        if c["relation"] != rel:
            bad.append(f"{name}: отношение {c['relation']!r}, по коду {rel!r}")
        if c["unit"] != bfc.CONDITION_UNITS[rel][0]:
            bad.append(f"{name}: единица {c['unit']!r} не по отношению")
        if home is not None and c["family"] != home:
            bad.append(f"{name}: условие не в семье потолка продления")
    if bad:
        return Rule("L16", False, "; ".join(bad[:5]))
    return Rule("L16", True, numbers={"conditions": len(conds)})


def rule_l17(cat, base, delta, snapshot) -> Rule:
    """История: префикс базы заморозки ⊕ вычисленный хвост дельты."""
    known = set(snapshot["known_dates"]) | set(delta.get("dates") or [])
    added = delta.get("history_added") or {}
    marker = base["history_marker"]
    base_ids = {"families": set(base["families"]),
                "criteria": set(base["criteria"])}
    bad = []
    for addr, section, rec in _addr_records(cat):
        tail = added.get(addr) or []
        history = [h for h in (rec.get("history") or [])]
        head = history[:len(history) - len(tail)]
        if history[len(head):] != tail:
            bad.append(f"{addr}: хвост истории не равен вычисленной прибавке")
            continue
        for h in head:
            if h.get("date") not in known and not _gate_line(h):
                bad.append(f"{addr}: дата {h.get('date')} — правка мимо "
                           f"инструмента либо нужна новая заморозка")
        if rec["id"] in base_ids[section]:
            if not head or head[-1] != marker:
                bad.append(f"{addr}: префикс базы заморозки не кончается "
                           f"отметкой шага 1")
            elif tail and tail[0]["date"] < marker["date"]:
                bad.append(f"{addr}: прибавка датирована раньше заморозки")
        else:
            if head:
                bad.append(f"{addr}: у записи вне базы заморозки есть префикс "
                           f"снимка")
            elif marker in history:
                bad.append(f"{addr}: запись вне базы заморозки несёт отметку "
                           f"шага 1 — либо она базовая, либо отметка чужая")
            # Дата снизу: запись, которой заморозка не знает, не вправе
            # выглядеть старше её. Законны либо даты окна заморозки
            # (`snapshot.known_dates` — их знает снимок), либо всё, что
            # ПОСЛЕ `snapshot.frozen_at`. Множество `known` здесь не
            # годится: в него входит `delta.dates`, выведенная из тех же
            # хвостов, — проверка была бы тавтологией.
            first = next((h for h in history if not _gate_line(h)), None)
            if first is not None:
                date = str(first.get("date") or "")
                if date not in set(snapshot["known_dates"]) \
                        and date < str(snapshot["frozen_at"]):
                    bad.append(
                        f"{addr}: первая дата истории {date} раньше "
                        f"заморозки {snapshot['frozen_at']} и вне её окна — "
                        f"запись вне базы датирована задним числом")
        dates = [h.get("date") for h in history if not _gate_line(h)]
        if dates != sorted(dates):
            bad.append(f"{addr}: даты истории убывают")
        origin = (rec.get("origin") or {}).get("date")
        if origin is not None and origin not in known \
                and rec["id"] in base_ids[section]:
            bad.append(f"{addr}: дата происхождения {origin} вне снимка")
    if bad:
        return Rule("L17", False, "; ".join(bad[:5]))
    return Rule("L17", True, numbers={"records": len(_addr_records(cat)),
                                      "with_tail": len(added),
                                      "dates": len(known)})


def rule_l18(judged, kotlin) -> Rule:
    """Воспроизводимость: папка ≡ курируемое(папка) ⊕ код.

    Пересборка идёт по ТОМУ ЖЕ свидетелю решателя, что и остальные
    правила: иначе стенд «решатель вырос на ручку» краснел бы здесь не по
    делу, а потому, что инструмент читает Kotlin дерева."""
    try:
        rc, diff, info = bfc.check_from_catalog(judged.path, kt=kotlin)
    except Exception as e:                                    # noqa: BLE001
        return Rule("L18", False, f"инструмент извлечения отказался: {e}")
    nums = {"stand_tasks": info.get("stand_tasks"), "diff": len(diff),
            "warnings": info.get("warnings")}
    if rc:
        # Числа рядом с красным: сколько задач стенда и расхождений
        # рассмотрено. Красный без чисел молчал бы о размере суда.
        return Rule("L18", False,
                    "папка не воспроизводится из своих курируемых полей ⊕ "
                    "код: " + "; ".join(diff[:4]),
                    numbers={k: v for k, v in nums.items() if v is not None})
    schema = judged.path / "schema" / "catalog.schema.json"
    src = bfc.schema_path_for_version(judged.cat.version())
    if schema.is_file() and src.is_file():
        if schema.read_bytes().replace(bfc.CRLF, bfc.LF) !=                 src.read_bytes().replace(bfc.CRLF, bfc.LF):
            return Rule("L18", False, "копия схемы рядом с данными разошлась "
                                      "со схемой читателя", numbers=nums)
    return Rule("L18", True, numbers=nums)


def rule_l19(cat, delta, bindings, words, strict: bool = False) -> Rule:
    """Виды исполнителей записей ↔ строки привязок (B04 одной функцией).

    `strict` — привязки движок строил САМ по судимому каталогу «живой ⊕
    кандидаты»: отказ генератора тогда красный, а не пропуск (судить
    есть чем — отказ и есть суждение о кандидате)."""
    if bindings is None:
        return (Rule("L19", False, words) if strict
                else Rule("L19", None, skipped=words))
    from vmp import bindings_gate as bg
    states = bg.best_states(bindings)
    rows = {}
    for row in bindings.get("bindings") or []:
        addr = f"{row.get('section')}/{row.get('id')}"
        rows[addr] = row
    bad, cells, present = [], 0, 0
    for addr, section, rec in _addr_records(cat):
        if rec.get("status") != "есть":
            continue
        declared = bg.declared_kinds(rec, section)
        row = rows.get(addr)
        if row is None:
            if declared:
                bad.append(f"{addr}: запись объявила виды {declared}, а строк "
                           f"привязок нет")
            continue
        cell = states.get((section, rec["id"])) or {}
        for kind in declared:
            cells += 1
            state = cell.get(kind)
            if state is None:
                bad.append(f"{addr}: вид {kind} объявлен, клетки нет")
            elif state == "есть":
                present += 1
    added = delta.get("executor_kinds_added") or {}
    for fid, kinds in sorted(added.items()):
        row = rows.get(f"families/{fid}")
        have = {ex.get("kind") for ex in (row or {}).get("executors") or []
                if ex.get("state") == "есть"}
        missing = sorted(set(kinds) - have)
        if missing:
            bad.append(f"families/{fid}: вид прибавлен, исполнителя нет: "
                       f"{missing}")
    if bad:
        return Rule("L19", False, "; ".join(bad[:5]))
    return Rule("L19", True, numbers={"cells": cells, "present": present,
                                      "rows": len(rows),
                                      "kinds_added": len(added)})


def rule_l20(cat, delta) -> Rule:
    """Стендовые задачи, гейты и прибавки словарей ≡ вычисленной дельте."""
    got = {addr: [t["id"] for t in rec.get("stand_tasks") or []]
           for addr, _s, rec in _addr_records(cat)}
    bad = []
    if got != (delta.get("stand_tasks") or {}):
        bad.append("перечень стендовых задач разошёлся с дельтой")
    ids = [t for v in got.values() for t in v]
    if len(set(ids)) != len(ids):
        bad.append("повтор идентификатора стендовой задачи")
    if dict(cat.header.get("gates") or {}) != dict(delta.get("gates") or {}):
        bad.append("гейты шапки разошлись с дельтой")
    empty = sorted(a for a, v in got.items() if not v)
    if empty != sorted(delta.get("records_without_stand_tasks") or []):
        bad.append(f"записи без задач {empty} разошлись с дельтой")
    d = cat.dictionaries()
    dd = delta.get("dictionaries") or {}
    for form in dd.get("world_forms_added") or []:
        if form not in (d.get("world_forms") or {}):
            bad.append(f"форма мира {form} прибавлена дельтой, а в словаре нет")
    for key in dd.get("synthetic_codes_added") or []:
        if key not in (d.get("synthetic_codes") or {}):
            bad.append(f"синтетический код {key} прибавлен, а в словаре нет")
    for name in dd.get("unbound_params_removed") or []:
        if name in (d.get("unbound_params") or {}):
            bad.append(f"имя {name} и связано записью, и лежит в unbound")
    if bad:
        return Rule("L20", False, "; ".join(bad[:5]))
    return Rule("L20", True, numbers={"records": len(got),
                                      "stand_tasks": len(ids),
                                      "without_tasks": len(empty)})


#: Поля черновика заявки, которые обязана повторить запись кандидата.
REQUEST_FIELDS = ("class", "kind", "zone", "title", "subject")


def _request_for(requests, section: str, rid: str, request_id: str | None):
    """Заявка кандидата: сперва по её НОМЕРУ (кандидат несёт `request_id`
    и это точный адрес), затем по адресу записи.

    Запись заявки адресуется полем `record_id` — так её пишет служба
    (`catalog_service/request_queue.py`) и так требует схема читателя
    (`$defs/request`: `additionalProperties: false`, `record_id` в
    `required`, поля `id` у заявки нет). Прежний подбор по `q["id"]` не
    находил НИ ОДНОЙ настоящей заявки: правило зеленело с нулём сверок, а
    зелёным его держали стенды, писавшие заявку несуществующей формы.
    Свидетелем считается только заявка, которую владелец принял, —
    состояния `REQUEST_WITNESS_STATES` (решение 0.6)."""
    rows = [q for q in ((requests or {}).get("requests") or [])
            if isinstance(q, dict)
            and q.get("state") in bfc.REQUEST_WITNESS_STATES]
    if request_id:
        for q in rows:
            if str(q.get("request_id")) == str(request_id):
                return q
    for q in rows:
        if q.get("section") != section:
            continue
        draft = q.get("draft") if isinstance(q.get("draft"), dict) else {}
        if str(q.get("record_id")) == rid or str(draft.get("id")) == rid:
            return q
    return None


def rule_l21(judged, requests) -> Rule:
    """Запись кандидата ≡ черновику принятой заявки владельца."""
    if not judged.candidates:
        return Rule("L21", None,
                    skipped="кандидатов рядом с каталогом нет — сверять "
                            "запись не с чем")
    bad, beyond, judged_n = [], [], 0
    for addr in judged.candidates:
        section, rid = addr.split("/", 1)
        rec = (judged.cat.family(rid) if section == "families"
               else judged.cat.criterion(rid))
        rq = _request_for(requests, section, rid,
                          judged.candidate_requests.get(addr))
        if rq is None or not isinstance(rq.get("draft"), dict):
            beyond.append(f"{addr}: заявки с черновиком нет — запись судят "
                          f"остальные правила")
            continue
        judged_n += 1
        draft = rq["draft"]
        for fld in REQUEST_FIELDS:
            if draft.get(fld) is not None and rec.get(fld) != draft[fld]:
                bad.append(f"{addr}.{fld}: в записи {rec.get(fld)!r}, в "
                           f"заявке {draft[fld]!r}")
        want = {str(p["name"]): p for p in (draft.get("params") or [])
                if isinstance(p, dict) and p.get("name")}
        have = {str(p["name"]): p for p in (rec.get("params") or [])}
        for name, p in sorted(want.items()):
            if name not in have:
                bad.append(f"{addr}: заявка называет параметр {name}, записи "
                           f"его нет")
                continue
            for fld in ("unit", "relation"):
                if p.get(fld) is not None and have[name].get(fld) != p[fld]:
                    bad.append(f"{addr}.{name}.{fld}: в записи "
                               f"{have[name].get(fld)!r}, в заявке "
                               f"{p[fld]!r}")
        for name in sorted(set(have) - set(want)):
            beyond.append(f"params: +{name} ({have[name].get('unit')}, "
                          f"{have[name].get('relation')})")
        asked = set(rq.get("executor_kinds_requested") or [])
        lost = sorted(asked - set(rec.get("executor_kinds") or [])
                      - set(rec.get("computable_by") or []))
        if lost:
            bad.append(f"{addr}: заявка просила виды {lost}, запись их не "
                       f"объявила")
    extra = {"beyond_request": beyond} if beyond else {}
    if bad:
        return Rule("L21", False, "; ".join(bad[:5]), extra=extra)
    return Rule("L21", True, numbers={"candidates": len(judged.candidates),
                                      "judged": judged_n}, extra=extra)


def _metric_name(value) -> str:
    """Имя метрики строки привязок — для СОРТИРОВКИ и для слов вердикта.

    Строка вида `kotlin_metric` без поля `metric` — расхождение, о
    котором закон обязан сказать словами. `sorted` на смеси `None` и
    строк ронял закон `TypeError`, и вместо вердикта человек получал
    трассировку — ровно на красной ветке, где вердикт и нужен."""
    return value if isinstance(value, str) else "<без поля metric>"


def rule_l22(cat, kotlin, bindings, words, strict: bool = False) -> Rule:
    """Kotlin ↔ привязки: ограничения провайдера, мягкие семьи, метрики.

    `strict` — как у L19: привязки собраны движком по судимому каталогу,
    и отказ генератора здесь красный."""
    if bindings is None:
        return (Rule("L22", False, words) if strict
                else Rule("L22", None, skipped=words))
    try:
        literals = ktab.constraint_literals()
    except Exception as e:                                    # noqa: BLE001
        return Rule("L22", None, skipped=f"литералы провайдера не прочитаны: "
                                         f"{e}")
    rows = [(f"{r.get('section')}/{r.get('id')}", r)
            for r in bindings.get("bindings") or []]
    bad = []
    for addr, row in rows:
        for ex in row.get("executors") or []:
            if ex.get("kind") == "kotlin_constraint" and ex.get("state") == "есть":
                if ex.get("constraint") not in literals:
                    bad.append(f"{addr}: ограничения {ex.get('constraint')!r} "
                               f"среди литералов провайдера нет")
    by_family = {r.get("id"): {ex.get("constraint")
                               for ex in r.get("executors") or []}
                 for _a, r in rows if r.get("section") == "families"}
    for family, constraint in sorted(kotlin["family_to_constraint"].items()):
        if constraint not in by_family.get(family, set()):
            bad.append(f"мягкая семья решателя {family} связана с "
                       f"ограничением {constraint}, а в привязках его нет")
    metric_rows = {ex.get("metric"): (addr, ex) for addr, r in rows
                   for ex in r.get("executors") or []
                   if ex.get("kind") == "kotlin_metric"}
    known = kotlin["metrics"]
    # Безымянная строка метрики называется СЛОВАМИ. Прежде `sorted` на
    # смеси `None` и строк ронял закон `TypeError` — вместо вердикта
    # человек получал трассировку (сквозная проверка 08.09.2026).
    for name, (addr, _ex) in metric_rows.items():
        if not isinstance(name, str):
            bad.append(f"{addr}: строка вида kotlin_metric без поля metric")
    if set(metric_rows) != set(known):
        имена = sorted(map(_metric_name, metric_rows))
        bad.append(f"строки метрик привязок {имена} ≠ словарю "
                   f"метрик решателя {sorted(known)}")
    for metric, (addr, ex) in sorted(metric_rows.items(),
                                     key=lambda kv: _metric_name(kv[0])):
        spec = known.get(metric)
        if spec is None:
            continue
        if spec["refusal"]:
            if ex.get("state") != "нет" or ex.get("note") != spec["refusal"]:
                bad.append(f"{addr}: у метрики с отказом решателя строка "
                           f"обязана быть «нет» его словами дословно")
            continue
        if ex.get("state") != "есть":
            bad.append(f"{addr}: метрика без отказа — строка «есть»")
            continue
        if ex.get("constraint") != spec["constraint"]:
            bad.append(f"{addr}: ограничение метрики разошлось с решателем")
        if sorted(ex.get("tasks") or []) != sorted(spec["tasks"]):
            bad.append(f"{addr}: задачи метрики разошлись с решателем")
        if spec["approximation"] and ex.get("approximation") != spec["approximation"]:
            bad.append(f"{addr}: приближение метрики не дословно")
    if bad:
        return Rule("L22", False, "; ".join(bad[:5]))
    return Rule("L22", True, numbers={
        "constraints": len(literals), "soft": len(kotlin["family_to_constraint"]),
        "metrics": len(metric_rows)})


def _body_of(cat) -> dict:
    """Тело каталога для вычисления дельты — тем же составом, каким его
    собирает инструмент (семьи, критерии, словари)."""
    return {"families": cat.families_full(status=None),
            "criteria": list(cat.criteria(status=None).values()),
            "dictionaries": cat.dictionaries()}


def rule_l23(judged, kotlin) -> Rule:
    """Свежесть носителей: посев ≡ проекции каталога, дельта ≡ пересчёту.

    Сравниваются ПРОЕКЦИИ, а сборки каталога здесь нет намеренно.
    Прежде правило звало `build_from_code.py --refresh --check`, а тот
    внутри собирает каталог целиком по таблицам решателя ДЕРЕВА: на
    ветке класса «рост таблиц решателя» новая ручка попадала в словарь
    несвязанных имён, отпечаток проекции расходился с диском, инструмент
    отвечал отказом — и L23 краснел на живом каталоге по построению, а не
    по делу (проект §2.3: «Kotlin ветки на неё не влияет: сравнение
    проекций без сборки»). Посев и дельта от роста Kotlin не зависят:
    посев — проекция курируемых полей каталога, дельта — разность
    каталога и базы; свидетель решателя нужен ей лишь для источника
    имени, и ручка без записи в неё не попадает."""
    folder = judged.path
    if not (folder / "tools").is_dir():
        return Rule("L23", None,
                    skipped="папка без tools/ — свежесть носителей судит "
                            "рабочий каталог дерева")
    cat = judged.cat
    diff = []
    try:
        # Формула ОДНА с инструментом (`--refresh` пишет ровно это):
        # разойдись они — правило и команда спорили бы о свежести.
        want_seed, want_delta = bfc.refreshed(folder, kt=kotlin)
    except Exception as e:                                    # noqa: BLE001
        return Rule("L23", False, f"проекция носителей не сосчитана: {e}")
    seed_path = folder / bfc.SEED_REL
    if not seed_path.is_file():
        diff.append(f"нет файла {seed_path}")
    elif seed_path.read_bytes().replace(bfc.CRLF, bfc.LF) \
            != want_seed.encode("utf-8"):
        diff.append(f"посев {seed_path} отстал от каталога")
    delta_path = folder / bfc.DELTA_REL
    if not delta_path.is_file():
        diff.append(f"нет файла {delta_path}")
    else:
        doc = json.loads(delta_path.read_text(encoding="utf-8-sig"))
        if doc.get("fingerprint") != cat.fingerprint():
            diff.append("дельта посчитана от другого отпечатка каталога")
        elif delta_path.read_bytes().replace(bfc.CRLF, bfc.LF) \
                != want_delta.encode("utf-8"):
            diff.append(f"дельта {delta_path} отстала от каталога")
    if diff:
        return Rule("L23", False, "; ".join(diff[:4]) + " — выполните "
                    "build_from_code.py --refresh --out " + str(folder))
    return Rule("L23", True, numbers={
        "diff": 0, "delta_keys": len(json.loads(want_delta))})


# ------------------------------------------------------------- вердикт
def _seed_beside(folder: Path, fallback):
    """Посев привязок РЯДОМ с судимой папкой каталога, если он там есть.

    Судимый каталог называется ключом `--catalog`, файл привязок —
    ключом `--bindings`, а посев привязок назвать было нечем: движок
    брал посев ДЕРЕВА и на записи, которой в дереве ещё нет, отказывал
    «запись каталога в посеве не названа» — правила L19/L22 краснели по
    построению стенда, а не по существу. Посев рядом с папкой — третий
    носитель того же знания и называется тем же способом, что первые
    два. У живого каталога такого файла нет, и вердикт дерева прежний."""
    p = Path(folder) / tcg.BESIDE_SEED
    if not p.is_file():
        return fallback
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return fallback
    return doc if isinstance(doc, dict) and isinstance(
        doc.get("records"), dict) else fallback


def judge(catalog_dir=None, *, bindings=None, with_candidates: bool = False,
          candidate_files=None, witnesses: dict | None = None) -> Verdict:
    """Вердикт закона по одной папке каталога. Отказ судить — ParityError."""
    folder = Path(catalog_dir or CATALOG_DIR)
    wit = witnesses if witnesses is not None else load_witnesses()
    with Judged(folder, with_candidates=with_candidates,
                candidate_files=candidate_files) as judged:
        cat = judged.cat
        # Метрики CDPM — по СУДИМОМУ каталогу, а не по живому (см.
        # cdpm_metrics_of); свидетели снаружи не правятся — своя копия.
        wit = dict(wit)
        wit["bindings_seed"] = _seed_beside(folder, wit.get("bindings_seed"))
        wit["cdpm"] = {**wit["cdpm"], "metrics": cdpm_metrics_of(cat, wit)}
        hard = wit["hard"]
        base = hard["base"]
        kotlin = wit["kotlin"]
        snapshot = wit["snapshot"]
        requests = {"form_version": 1, "requests": cat.requests()}
        try:
            delta = bfc.compute_delta(
                _body_of(cat), cat.header, base, kotlin,
                hard["cdpm_dictionaries"],
                dict(hard.get("params_relation_set") or {}),
                bfc.request_params(requests), strict=False)
            delta_words = None
        except Exception as e:                                # noqa: BLE001
            delta, delta_words = None, f"дельта не вычислена: {e}"

        # Каталог, который движок СОБРАЛ сам из кандидатов, своего файла
        # привязок не имеет: строки ему собираются в памяти, а `--bindings`
        # при этом не читается (контракт §1). Прежде ключи, переданные
        # вместе, судили временный каталог строками ЖИВОГО файла — строк
        # нового параметра там нет, и L19/L22 краснели не по делу.
        bindings_words = None
        if judged.materialized:
            doc, bwords = in_memory_bindings(cat, wit.get("bindings_seed"))
            bindings_words = "в памяти по судимому каталогу"
            if bindings is not None:
                bindings_words += (f" (--bindings {bindings} не читается: "
                                   f"каталог собран из кандидатов)")
        else:
            path = Path(bindings or DEFAULT_BINDINGS)
            doc, bwords = read_bindings(path)
            bindings_words = str(path)

        def _needs_delta(rid: str) -> Rule:
            return Rule(rid, False, delta_words)

        rules = []
        if delta is None:
            for rid in ("L01", "L04", "L17", "L19", "L20"):
                rules.append(_needs_delta(rid))
            rules.append(rule_l02_l03(cat, snapshot))
        else:
            rules.append(rule_l01(cat, base, delta, snapshot))
            rules.append(rule_l02_l03(cat, snapshot))
            rules.append(rule_l04(cat, base, delta))
        rules.append(rule_l05a(cat, base, kotlin, hard, requests))
        rules.append(rule_l05b(cat, kotlin, hard, requests))
        rules.append(rule_l06(cat, list(hard["cdpm_dictionaries"]["lexicon_order"])))
        rules.append(rule_l07(cat, base, snapshot, hard, wit))
        rules.append(rule_l08(cat, kotlin))
        rules.append(rule_l08b(cat, kotlin, wit))
        rules.append(rule_l09(cat, wit))
        rules.append(rule_l10(judged, base, delta or {}, snapshot))
        rules.append(rule_l11(cat))
        rules.append(rule_l13(judged, base, snapshot))
        rules.append(rule_l14(cat, kotlin))
        rules.append(rule_l15(cat, kotlin, wit, requests))
        rules.append(rule_l16(cat, wit))
        if delta is not None:
            rules.append(rule_l17(cat, base, delta, snapshot))
        rules.append(rule_l18(judged, kotlin))
        strict = judged.materialized
        if delta is not None:
            rules.append(rule_l19(cat, delta, doc, bwords, strict=strict))
            rules.append(rule_l20(cat, delta))
        rules.append(rule_l21(judged, requests))
        rules.append(rule_l22(cat, kotlin, doc, bwords, strict=strict))
        rules.append(rule_l23(judged, kotlin))
        order = {rid: i for i, rid in enumerate(RULE_ORDER)}
        rules.sort(key=lambda r: order.get(r.id, len(order)))
        info = {"version": cat.version(), "fingerprint": cat.fingerprint(),
                "path": str(judged.path.resolve()),
                "bindings": bindings_words}
        if judged.candidates:
            info["with_candidates"] = list(judged.candidates)
            if judged.materialized:
                info["source_fingerprint"] = judged.source_cat.fingerprint()
        return Verdict(rules, info)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalog", default=None,
                    help="судимая папка каталога (умолчание — catalog/)")
    ap.add_argument("--bindings", default=None,
                    help="файл привязок судимого каталога")
    ap.add_argument("--with-candidates", action="store_true",
                    help="судить «живой ⊕ кандидаты catalog/candidates/**»")
    ap.add_argument("--candidate", action="append", default=None,
                    metavar="ФАЙЛ",
                    help="файл кандидата, УЖЕ положенного в судимую папку "
                         "(временный каталог попытки CR): движок его не "
                         "материализует, а берёт адрес записи и номер "
                         "заявки — чтобы правило L21 судило запись")
    ap.add_argument("--json", default=None, help="записать вердикт файлом")
    ap.add_argument("--quiet", action="store_true",
                    help="не печатать построчный отчёт")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):                  # pragma: no cover
            pass
    folder = Path(a.catalog or CATALOG_DIR)
    try:
        verdict = judge(folder, bindings=a.bindings,
                        with_candidates=a.with_candidates,
                        candidate_files=a.candidate)
        doc = verdict.as_dict()
    except ParityError as e:
        doc = failed(folder, str(e))
    except Exception as e:                                    # noqa: BLE001
        doc = failed(folder, f"каталог не судился: {e}")
    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                  indent=1) + "\n").encode("utf-8"))
    if not a.quiet:
        cat = doc["catalog"]
        print(f"паритет {cat.get('path')}: версия {cat.get('version')} | "
              f"отпечаток {cat.get('fingerprint')}")
        if cat.get("with_candidates"):
            print("  кандидаты: " + ", ".join(cat["with_candidates"]))
        for rid in RULE_ORDER:
            spec = doc["rules"].get(rid)
            if spec is None:
                continue
            mark = {True: "✓", False: "✗", None: "—"}[spec["ok"]]
            tail = spec.get("words") or spec.get("skipped") or ""
            nums = " | ".join(f"{k} {v}" for k, v
                              in (spec.get("numbers") or {}).items())
            print(f"  {rid} {mark} " + " | ".join(x for x in (tail, nums) if x))
        c = doc["counts"]
        print(f"итог: правил {c['rules']} | зелёных {c['green']} | красных "
              f"{c['red']} | пропусков {c['skipped']} | предупреждений "
              f"{c['warning']}")
        if doc["words"]:
            print("красные: " + doc["words"])
    return int(doc["rc"])


if __name__ == "__main__":
    raise SystemExit(main())
