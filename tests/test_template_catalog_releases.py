# -*- coding: utf-8 -*-
"""T18: преемник гейта содержания — «каталог сам себе источник правды»
(§5 п.3 проекта шага 5).

Гейты содержания шага 1 (`test_template_catalog_parity.py` L01–L20 и
проекции обёртки) закреплены на СНИМКЕ КОДА и по построению покраснеют
при первой сборке живого каталога службой. Их преемник — этот файл: он
судит каталог его же журналом версий и папками выпусков, а не внешним
снимком.

Что проверяется у любой папки каталога:
 1) у каждой записи `versions.json` есть папка `releases/<v>/`, она
    открывается читателем без ошибок, её версия и отпечаток равны записи
    журнала, а её собственный журнал кончается той же версией;
 2) у соседних выпусков множество троек (раздел, id, вид) из
    `diff_bodies(предыдущее тело, текущее)` равно множеству из `changes[]`
    записи журнала (слова заметок не сверяются — они переформулируются);
 3) текущая папка: выпуск — версия и отпечаток равны последней записи
    журнала; черновик — его тройка строго больше последней записи;
 4) число проверенных выпусков печатается и равно числу папок
    `releases/` (гейт «не вхолостую»; на живом каталоге сегодня 0 = 0).

Живой каталог только читается. Случай «два выпуска» строится на
tmp-копии писателем — тем самым, которым будет писать `catalog_service/
releases.py` дорожки A.
"""
import json
import re
from pathlib import Path

import pytest

import catalog_copies as cc
import template_catalog as tc
from template_catalog import diff, writer
from template_catalog.manifest import parse_version
from template_catalog.history import same_schema_form

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "catalog"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def форма_своего_времени(папка: Path) -> bool:
    """Снимок выпуска написан НЫНЕШНЕЙ формой записи?

    Папка выпуска неизменяема и несёт схему своего времени
    (`schema/catalog.schema.json`). Правила сегодняшнего `validate()`
    судят снимок только при совпадении формы: выпуск 0.5.0 сменил её
    (три поля записи семьи стали обязательными, единицы нарушения —
    картой «код → слово»), и требовать сегодняшних граф от документа
    прошлого — не суд, а анахронизм. Что от формы НЕ зависит — номер,
    отпечаток канона, журнал и посчитанные правки — спрашивается с
    каждого выпуска одинаково, а подмена содержания снимка ловится
    отпечатком, а не этой веткой."""
    # The selected reader is versioned; legacy 0.6 and modern 0.7 differ.
    # Independent identity/journal/diff assertions below remain unchanged.
    return same_schema_form(tc.load(папка))


def проверить_выпуски(folder: Path) -> dict:
    """Гейт §5 п.3 над папкой каталога. Возврат — счётчики для печати;
    любое расхождение — assert словами. Функция вынесена отдельно, чтобы
    ею же судить копию, собранную службой."""
    cat = tc.load(folder)
    журнал = cat.versions()
    папки = sorted(p.name for p in (folder / "releases").iterdir()
                   if p.is_dir()) if (folder / "releases").is_dir() else []
    assert sorted(v["version"] for v in журнал) == папки, \
        (f"журнал версий и папки выпусков расходятся: журнал "
         f"{[v['version'] for v in журнал]}, папки {папки}")
    предыдущее = None
    сверено_правок = 0
    другой_формы: list = []
    for запись in журнал:
        v = запись["version"]
        папка = folder / "releases" / v
        assert папка.is_dir(), f"выпуска {v} нет папкой"
        снимок = tc.load(папка)
        if форма_своего_времени(папка):
            ошибки = [p for p in снимок.validate() if p.severity == "error"]
            assert ошибки == [], f"выпуск {v}: {[str(p) for p in ошибки[:3]]}"
        else:
            другой_формы.append(v)
        assert снимок.version() == v, \
            f"папка releases/{v} несёт версию {снимок.version()}"
        assert снимок.fingerprint() == запись["fingerprint"] == \
            снимок.declared_fingerprint(), \
            f"выпуск {v}: отпечаток снимка не равен записи журнала"
        assert _SHA.match(запись["fingerprint"]), f"выпуск {v}: не 64 hex"
        assert _COMMIT.match(str(запись.get("code") or "")), \
            f"выпуск {v}: поле code не срез кода"
        свой = снимок.versions()
        assert свой and свой[-1]["version"] == v, \
            (f"папка выпуска {v} обязана нести журнал с собственной "
             f"записью, последняя — {свой[-1]['version'] if свой else None}")
        # (2) заявленные правки равны посчитанным
        тройки_журнала = {(c["section"], c["id"], c["kind"])
                          for c in запись.get("changes") or []}
        тройки_разницы = {(c.section, c.id, c.kind)
                          for c in diff.diff_bodies(предыдущее, снимок.body())}
        assert тройки_журнала == тройки_разницы, (
            f"выпуск {v}: журнал называет правки "
            f"{sorted(тройки_журнала - тройки_разницы)}, которых нет, и "
            f"молчит о {sorted(тройки_разницы - тройки_журнала)}")
        сверено_правок += len(тройки_разницы)
        предыдущее = снимок.body()
    # (3) текущая папка против последней записи журнала
    if журнал:
        последняя = журнал[-1]
        if cat.is_draft():
            assert parse_version(cat.version())[0] > \
                parse_version(последняя["version"])[0], \
                (f"черновик {cat.version()} не старше последнего выпуска "
                 f"{последняя['version']} тройкой")
        else:
            assert cat.version() == последняя["version"], \
                (f"текущая версия {cat.version()} не равна последней записи "
                 f"журнала {последняя['version']}")
            assert cat.fingerprint() == последняя["fingerprint"], \
                "отпечаток текущего выпуска не равен записи журнала"
    return {"выпусков": len(журнал), "папок": len(папки),
            "правок": сверено_правок, "версия": cat.version(),
            "черновик": cat.is_draft(), "другой формы": другой_формы}


def test_живой_каталог_сам_себе_источник_правды(capsys):
    итог = проверить_выпуски(LIVE)
    assert итог["выпусков"] == итог["папок"]
    старые = итог["другой формы"]
    print(f"\nT18 живой каталог {итог['версия']}"
          f"{' (черновик)' if итог['черновик'] else ''}: выпусков "
          f"{итог['выпусков']}, папок releases/ {итог['папок']}, сверено "
          f"правок {итог['правок']}"
          + (f"; правилами сегодняшней формы не судились {старые} — "
             f"снимки прежней формы записи" if старые else ""))


# ------------------------------------------------- копия с выпусками
def _выпустить(folder: Path, version: str, note: str, *, code: str) -> dict:
    """Выпуск на копии писателем: журнал дописывается ДО снимка, снимок
    несёт собственную запись (§2.6)."""
    cat = tc.load(folder)
    предыдущий = cat.versions()
    основа = None
    if предыдущий:
        основа = tc.load(folder / "releases" /
                         предыдущий[-1]["version"]).body()
    тело = cat.body()
    head = writer.build_header(
        cat.header, version=version, released_by="владелец",
        released_at="2026-09-08", note=note, body=тело,
        legacy_registry_rev=writer.legacy_registry_rev(cat.families()))
    запись = {"version": version, "fingerprint": head["fingerprint"],
              "released_at": "2026-09-08", "released_by": "владелец",
              "code": code,
              "changes": diff.as_journal(diff.diff_bodies(основа, тело))}
    порядок = json.loads((folder / "transition" /
                          "families_order.json").read_text(encoding="utf-8"))
    writer.write_catalog(folder, header=head, families=тело["families"],
                         criteria=тело["criteria"],
                         dictionaries=тело["dictionaries"],
                         requests=cat.requests(),
                         versions=предыдущий + [запись],
                         families_order=порядок)
    writer.snapshot_release(folder, version)
    return запись


def test_копия_с_двумя_выпусками(tmp_path, capsys):
    folder = cc.copy_catalog(tmp_path)
    первый = _выпустить(folder, "0.3.0", "первый выпуск на копии",
                        code="a" * 40)
    assert {c["kind"] for c in первый["changes"]} == {"добавлена"}
    # правка между выпусками: одна семья меняет заголовок (патч) и одна
    # уходит в «снята» (старшая) — обе обязаны попасть в changes[]
    cat = tc.load(folder)
    семьи = [r for r in cat.body()["families"] if r["status"] == "есть"]
    правленая, снимаемая = семьи[0]["id"], семьи[1]["id"]
    тело = cat.body()
    for r in тело["families"]:
        if r["id"] == правленая:
            r["title"] = r["title"] + " (правка между выпусками)"
        if r["id"] == снимаемая:
            r["status"] = "снята"
            r["complements"] = []
    for r in тело["families"]:
        r["complements"] = [c for c in r.get("complements") or []
                            if c != снимаемая]
    тело["dictionaries"]["lexicon_order"] = writer.refresh_lexicon_order(
        тело["dictionaries"]["lexicon_order"], тело["families"])
    порядок = writer.refresh_families_order(
        json.loads((folder / "transition" / "families_order.json")
                   .read_text(encoding="utf-8")),
        [r["id"] for r in тело["families"] if r["status"] == "есть"])
    head = writer.build_header(
        cat.header, version="0.3.1-draft.1", released_by="владелец",
        released_at="2026-09-08", note="черновик между выпусками",
        body=тело, legacy_registry_rev=None)
    writer.write_catalog(folder, header=head, families=тело["families"],
                         criteria=тело["criteria"],
                         dictionaries=тело["dictionaries"],
                         requests=cat.requests(), versions=cat.versions(),
                         families_order=порядок)
    итог_черновика = проверить_выпуски(folder)
    assert итог_черновика["выпусков"] == 1 and итог_черновика["черновик"]

    второй = _выпустить(folder, "0.4.0", "второй выпуск на копии",
                        code="b" * 40)
    виды = {(c["section"], c["id"]): c["kind"] for c in второй["changes"]}
    assert виды.get(("families", правленая)) == "изменена", виды
    assert виды.get(("families", снимаемая)) == "удалена", виды
    итог = проверить_выпуски(folder)
    # Оба выпуска копии написаны писателем СЕГОДНЯШНЕЙ формой — оба
    # судятся сегодняшними правилами, и гейт не вхолостую.
    assert итог == {"выпусков": 2, "папок": 2, "правок":
                    len(первый["changes"]) + len(второй["changes"]),
                    "версия": "0.4.0", "черновик": False, "другой формы": []}
    print(f"\nT18 копия: выпусков {итог['выпусков']}, правок первого "
          f"{len(первый['changes'])}, второго {len(второй['changes'])}, "
          f"снята семья {снимаемая}, правлена {правленая}")


def test_гейт_видит_подмену_отпечатка_снимка(tmp_path):
    """Гейт не декоративен: испорченная копия выпуска ловится."""
    folder = cc.copy_catalog(tmp_path)
    _выпустить(folder, "0.3.0", "выпуск для порчи", code="c" * 40)
    проверить_выпуски(folder)
    head = folder / "releases" / "0.3.0" / "catalog.json"
    doc = json.loads(head.read_text(encoding="utf-8"))
    doc["fingerprint"] = "0" * 64
    head.write_bytes(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                indent=1).encode("utf-8") + b"\n")
    with pytest.raises(AssertionError):
        проверить_выпуски(folder)


def test_гейт_видит_пропавшую_папку_выпуска(tmp_path):
    import shutil
    folder = cc.copy_catalog(tmp_path)
    _выпустить(folder, "0.3.0", "выпуск без папки", code="d" * 40)
    shutil.rmtree(folder / "releases" / "0.3.0")
    with pytest.raises(AssertionError) as ei:
        проверить_выпуски(folder)
    assert "папки выпусков расходятся" in str(ei.value)


def test_гейт_видит_приписанную_правку_журнала(tmp_path):
    folder = cc.copy_catalog(tmp_path)
    _выпустить(folder, "0.3.0", "выпуск с припиской", code="e" * 40)
    for место in (folder / "versions.json",
                  folder / "releases" / "0.3.0" / "versions.json"):
        doc = json.loads(место.read_text(encoding="utf-8"))
        doc["versions"][0]["changes"].append(
            {"section": "families", "id": "нет_такой_семьи",
             "kind": "добавлена", "note": "приписка"})
        место.write_bytes(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                     indent=1).encode("utf-8") + b"\n")
    with pytest.raises(AssertionError) as ei:
        проверить_выпуски(folder)
    assert "нет_такой_семьи" in str(ei.value)
