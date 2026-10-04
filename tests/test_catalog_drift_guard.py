# -*- coding: utf-8 -*-
"""Сторож дрейфа каталога шаблонов при нажатии (шаг 3 переезда,
07.09.2026; §8 схемы `final-report/KATALOG-SHABLONOV-SHEMA-ZAPISI-CLD.md`).

Охраняемое: пакет объявляет ключом `manifest.catalog`, КАКИМ каталогом
посажены его правила; продукт знает свой. Сторож сверяет их перед
нажатием и отвечает одним из четырёх слов закрытого перечня
(`vmp.catalog_access.CHECK_*`):

  * `stop` — старшая (на 0.x и младшая) разница версий, несравнимая или
    кривая версия, одна версия выпуска с разными данными: расчёт НЕ
    начинается, отказ словами называет обе стороны — на панели это
    `PackageRunError` с кодом `catalog-version-mismatch` ДО занятия
    кнопки (попытки нет), в оркестраторе — остановка с журнальной
    строкой Ш-1кш;
  * `not-declared` — ключа у пакета нет (пакет до «Дополнения к
    1.18»): предупреждение, нажатие идёт;
  * `mismatch-fingerprint` — данные каталога другие (версия та же
    либо patch, а на версиях от 1.0.0 — и minor): предупреждение в
    состоянии, журнале и реестре исполнителей;
  * `match` — данные каталога те же.

Статус журнальной строки Ш-1кш выводится из ВЕРДИКТА читателя
(`catalog_access.check_status`), а не из равенства отпечатков: у patch
и minor от 1.0.0 отпечаток может совпасть, а вердикт — предупреждение.

Стенды идут на НАСТОЯЩЕМ пакете производителя (`manifest.catalog` и
сводка `fits_catalog` переписываются вместе — гейт пакета судит их
согласованность) и на настоящем нажатии — оркестраторе кнопки и ручке
панели.

Шаг 7, часть 7-Б (08.09.2026): режим СРЕЗА базы шаблонов
(`CREW_VMP_TEMPLATE_BASE`) снят — у продукта один источник, каталог.
Стенды, которым нужна мерка ВЫПУСКА (каталог репозитория — черновик,
а «одна версия у двух выпусков — разные данные» судится только у
выпусков), берут её не срезом, а НАСТОЯЩИМ каталогом-выпуском:
`_released_catalog()` — tmp-копия `catalog/` с версией `1.0.0`,
выпущенной владельцем (помощник `tests/catalog_copies.py`). Стенды
нечитаемого среза заменены стендами нечитаемого КАТАЛОГА: битый
каталог — честный стоп словами, а не выдуманный исход сторожа.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
for p in (str(HERE), str(HERE / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import catalog_copies as cc  # noqa: E402
from tests.test_run_button import (  # noqa: E402
    FakeSolver, _builder_fakes, _fakes, _h0_doc, _run_dir)
from tests.test_task_channel import _pkg_bytes, _ready_doc  # noqa: E402
from vmp import catalog_access as ca  # noqa: E402
from vmp.run import button as btn  # noqa: E402

FOREIGN_FP = "0" * 64


@pytest.fixture(autouse=True)
def _door(monkeypatch):
    """Каталог — настоящий (catalog/ репозитория); кэш двери
    сбрасывается до и после каждого стенда."""
    monkeypatch.delenv(ca.CATALOG_ENV, raising=False)
    ca.forget()
    yield
    ca.forget()


def _released_catalog(tmp_path, monkeypatch, version: str = "1.0.0",
                      name: str = "catalog") -> Path:
    """Продукт идёт по каталогу-ВЫПУСКУ: tmp-копия `catalog/` с версией
    без суффикса черновика, выпущенной владельцем (H01 читателя), и с
    той же версией в привязках копии (иначе B01 остановил бы дверь
    раньше проверяемого правила). Отпечаток копии — настоящий: стоп
    «одна версия — разные данные» судит ДАННЫЕ, и второй стороной
    остаётся отпечаток пакета."""
    from template_catalog.export import dump_json
    dst = cc.copy_catalog(tmp_path, name=name)
    head = dst / "catalog.json"
    doc = json.loads(head.read_text(encoding="utf-8"))
    doc["version"] = version
    doc["released_by"] = "владелец"
    head.write_bytes(dump_json(doc).encode("utf-8"))
    binds = dst / cc.BINDINGS_FILE
    if binds.is_file():
        bdoc = json.loads(binds.read_text(encoding="utf-8"))
        bdoc["catalog_version"] = version
        binds.write_bytes(dump_json(bdoc).encode("utf-8"))
    cc.use_catalog(monkeypatch, dst)
    return dst


def _draft_catalog(tmp_path, monkeypatch, version: str | None = None,
                   name: str = "draft") -> Path:
    """Продукт идёт по каталогу-ЧЕРНОВИКУ: tmp-копия `catalog/` с
    черновым номером (по умолчанию — следующий патч живой версии) и той
    же версией в привязках копии.

    Зачем: с 08.09 живой каталог — ВЫПУСК 0.2.0 (слово владельца), и
    черновые исходы сторожа («предупреждение вместо остановки») на нём
    не воспроизводятся. Закон обязан оставаться под гейтом обеими
    ветвями — выпускную мерку дают стенды на живом каталоге и
    `_released_catalog`, черновую — этот помощник. Отпечаток копии
    настоящий: сторож судит ДАННЫЕ."""
    from template_catalog.export import dump_json
    if version is None:
        version = _next_patch(ca.stamp()["version"]) + "-draft.1"
    dst = cc.copy_catalog(tmp_path, name=name, version=version)
    binds = dst / cc.BINDINGS_FILE
    if binds.is_file():
        bdoc = json.loads(binds.read_text(encoding="utf-8"))
        bdoc["catalog_version"] = version
        binds.write_bytes(dump_json(bdoc).encode("utf-8"))
    cc.use_catalog(monkeypatch, dst)
    return dst


def _stamp() -> dict:
    st = ca.stamp()
    return {"version": st["version"], "fingerprint": st["fingerprint"]}


def _manifest(stamp: dict | None) -> dict:
    """Манифест пакета, каким его отдаёт загрузчик кнопке: режимы задач
    и (при наличии) ключ каталога."""
    man = {"allowed_modes": {"pairing": ["feasibility-hard"],
                             "rostering": ["optimization"]},
           "objectives": {"rostering": ["G-1"]},
           "spec_version": "1.6"}
    if stamp is not None:
        man["catalog"] = stamp
    return man


# ------------------------------------------------------------------ 1
def test_сторож_судит_обе_стороны_словами(tmp_path, monkeypatch):
    """Четыре исхода на каталоге продукта-ЧЕРНОВИКЕ 0.x: ключа нет,
    версия та же и отпечаток тот же, версия та же и данные другие,
    версия старшая. Слова называют обе стороны — «какая у пакета, какая
    у продукта».

    Каталог здесь — tmp-копия с черновым номером: живой каталог с 08.09
    выпуск 0.2.0, и у выпуска «та же версия — другие данные» уже
    остановка (стенд 1а ниже), а не предупреждение."""
    _draft_catalog(tmp_path, monkeypatch)
    prod = _stamp()
    assert "-draft." in prod["version"], prod

    no_key = ca.manifest_check(_manifest(None))
    assert no_key["state"] == ca.CHECK_NOT_DECLARED
    assert no_key["severity"] == "warning"
    assert "не объявлена" in no_key["words"]

    # Версия и отпечаток сошлись; каталог продукта — черновик, и об
    # этом говорится словами (решение 0.9: у черновика одна версия
    # законно несёт разные данные — отпечаток называется всегда).
    same = ca.manifest_check(_manifest(prod))
    assert same["state"] == ca.CHECK_MATCH
    assert same["severity"] == "warning"
    assert "черновик" in same["words"]
    assert "тот же" in same["words"]

    other_data = ca.manifest_check(
        _manifest({"version": prod["version"], "fingerprint": FOREIGN_FP}))
    assert other_data["state"] == ca.CHECK_MISMATCH
    assert other_data["severity"] == "warning"
    assert FOREIGN_FP[:12] in other_data["words"]
    assert prod["fingerprint"][:12] in other_data["words"]

    # Выпуск 0.1.0 при черновике 0.1.0-draft.N — та же тройка чисел:
    # предупреждение «каталог в черновике», нажатие идёт (решение 0.9).
    release = ca.manifest_check(
        _manifest({"version": prod["version"].split("-")[0],
                   "fingerprint": prod["fingerprint"]}))
    assert release["state"] == ca.CHECK_MATCH
    assert release["severity"] == "warning"
    assert "черновик" in release["words"]
    assert prod["version"].split("-")[0] in release["words"]

    senior = ca.manifest_check(
        _manifest({"version": "1.0.0", "fingerprint": FOREIGN_FP}))
    assert senior["state"] == ca.CHECK_STOP
    assert senior["severity"] == "error"
    assert "1.0.0" in senior["words"] and prod["version"] in senior["words"]
    assert "расчёт не начинается" in senior["words"]

    # Младшая при 0.x у ВЫПУСКА — тоже старшая (соглашение semver для
    # 0.x): следующий младший выпуск вычисляется от версии продукта,
    # литерала версии здесь нет — каталог движется, закон нет.
    assert ca.manifest_check(
        _manifest({"version": _minor_shift(prod["version"], 1, draft=None),
                   "fingerprint": prod["fingerprint"]}))["state"] \
        == ca.CHECK_STOP
    # Несравнимая версия — стоп, а не молчание.
    assert ca.manifest_check(
        _manifest({"version": "0.1.0-срез",
                   "fingerprint": prod["fingerprint"]}))["state"] \
        == ca.CHECK_STOP


# ----------------------------------------------------------------- 1а
def test_выпуск_одна_версия_разные_данные_останавливает():
    """Живой каталог с 08.09 — ВЫПУСК 0.2.0, и закон «стоп защищает
    выпуски» на нём виден прямо: та же версия с другими данными —
    остановка словами, расчёт не начинается. У черновика тот же случай
    шёл предупреждением (стенд 1)."""
    prod = _stamp()
    assert "-draft." not in prod["version"], \
        "каталог продукта — черновик: выпускная мерка этим стендом не " \
        "проверяется"
    chk = ca.manifest_check(_manifest({"version": prod["version"],
                                       "fingerprint": FOREIGN_FP}))
    assert chk["state"] == ca.CHECK_STOP
    assert chk["severity"] == "error"
    assert FOREIGN_FP[:12] in chk["words"]
    assert prod["fingerprint"][:12] in chk["words"]
    assert "разные данные" in chk["words"]
    assert ca.check_status(chk) == ca.STATUS_STOP
    # Те же данные при той же версии выпуска — совпадение без шума.
    exact = ca.manifest_check(_manifest(prod))
    assert exact["state"] == ca.CHECK_MATCH and exact["quiet"] is True


def _split(version: str) -> tuple:
    triple, _, draft = version.partition("-")
    major, minor, patch = (int(x) for x in triple.split("."))
    return major, minor, patch, draft


def _minor_shift(version: str, step: int,
                 draft: str | None = "draft.1") -> str:
    """Версия, отличающаяся от данной СРЕДНИМ числом на step. Суффикс
    задаётся отдельно: None — ВЫПУСК, иначе черновик. Литералов версии
    в стендах нет: каталог движется, а закон сторожа — нет."""
    major, minor, patch, _ = _split(version)
    assert minor + step >= 0, version
    triple = "%d.%d.%d" % (major, minor + step, patch)
    return triple + ("-" + draft if draft else "")


def _next_patch(version: str) -> str:
    major, minor, patch = (int(x) for x in version.split("-")[0].split("."))
    return "%d.%d.%d" % (major, minor, patch + 1)


#: Дельта шага 4: её `base` называет каталог, которым выпущены ЖИВЫЕ
#: пакеты контура (пакет run-0089 и пакеты площадок). Файла нет —
#: каталог ещё стоит на этом основании, и случай неотличим от «та же
#: версия»; стенд говорит это словами, а не молчит.
_DELTA_FILE = HERE / "catalog" / "transition" / "step4_additions.json"


def _delta_base() -> dict | None:
    if not _DELTA_FILE.is_file():
        return None
    return (json.loads(_DELTA_FILE.read_text(encoding="utf-8"))
            .get("base") or None)


def test_два_черновика_разной_младшей_версии_идут_предупреждением(
        tmp_path, monkeypatch):
    """Принцип «стоп сторожа защищает выпуски» (решение шага 4): разница
    СРЕДНЕГО числа на версиях 0.x между ДВУМЯ ЧЕРНОВИКАМИ —
    предупреждение с обоими отпечатками, нажатие идёт. Черновик выпуском
    не является, и останавливать им живые пакеты контура не за что; как
    только хотя бы одна сторона — ВЫПУСК, стоп возвращается (стенд 1а и
    стенд «живой пакет прежнего черновика»).

    Каталог продукта — tmp-копия с черновым номером: живой каталог с
    08.09 выпуск 0.2.0."""
    _draft_catalog(tmp_path, monkeypatch)
    prod = _stamp()
    assert "-draft." in prod["version"], prod

    newer = ca.manifest_check(_manifest(
        {"version": _minor_shift(prod["version"], 1),
         "fingerprint": FOREIGN_FP}))
    assert newer["state"] == ca.CHECK_MISMATCH
    assert newer["severity"] == "warning"
    assert FOREIGN_FP[:12] in newer["words"]
    assert prod["fingerprint"][:12] in newer["words"]
    assert ca.check_status(newer) == ca.STATUS_WARNING

    older = ca.manifest_check(_manifest(
        {"version": _minor_shift(prod["version"], -1),
         "fingerprint": FOREIGN_FP}))
    assert older["state"] == ca.CHECK_MISMATCH
    assert older["severity"] == "warning"
    assert ca.check_status(older) == ca.STATUS_WARNING


def test_живой_пакет_прежнего_черновика_после_выпуска_останавливается(
        tmp_path, monkeypatch):
    """Пакет контура, выпущенный ПРЕЖНИМ черновиком каталога (образец —
    `cdpm-runs/run-0089/channel/task-package.zip` и пакеты площадок),
    сверяется по обеим меркам, и обе называются словами:

    * продукт на ЧЕРНОВИКЕ — предупреждение, нажатие идёт: ровно ради
      этого заведён принцип черновиков;
    * продукт на ВЫПУСКЕ (живой каталог с 08.09 — 0.2.0) — ОСТАНОВКА:
      младшая разница на 0.x с выпуском на одной из сторон и есть стоп.
      Следствие названо заранее, а не на демо: такие пакеты обязан
      перевыпустить производитель (CDPM)."""
    base = _delta_base()
    prod_live = _stamp()
    if base is None:
        assert prod_live["version"] != "", "версии каталога нет"
        pytest.skip("дельты шага 4 в дереве нет: каталог ещё на основании "
                    "%s — живой пакет сверяется как «та же версия»"
                    % prod_live["version"])
    assert _split(base["version"])[:3] != _split(prod_live["version"])[:3], \
        "каталог не сдвинулся с основания дельты"
    # Мерка ВЫПУСКА — на живом каталоге.
    assert "-draft." not in prod_live["version"], prod_live
    stop = ca.manifest_check(_manifest({"version": base["version"],
                                        "fingerprint": base["fingerprint"]}))
    assert stop["state"] == ca.CHECK_STOP
    assert stop["severity"] == "error"
    assert base["version"] in stop["words"]
    assert prod_live["version"] in stop["words"]
    assert ca.check_status(stop) == ca.STATUS_STOP
    # Мерка ЧЕРНОВИКА — на tmp-копии с черновым номером.
    _draft_catalog(tmp_path, monkeypatch)
    prod = _stamp()
    chk = ca.manifest_check(_manifest({"version": base["version"],
                                       "fingerprint": base["fingerprint"]}))
    assert chk["state"] == ca.CHECK_MISMATCH
    assert chk["severity"] == "warning"
    assert base["fingerprint"][:12] in chk["words"]
    assert prod["fingerprint"][:12] in chk["words"]
    assert ca.check_status(chk) == ca.STATUS_WARNING


def test_patch_идёт_предупреждением_а_журнал_не_спорит_с_вердиктом():
    """Разница только в МЛАДШЕМ числе версии (patch) — вердикт читателя
    «продолжаем с предупреждением», нажатие идёт. Слово исхода выбирается
    по отпечатку, а статус журнальной строки — по вердикту: иначе одна
    строка говорила бы «сошлось» рядом со словами «продолжаем с
    предупреждением»."""
    prod = _stamp()
    newer = _next_patch(prod["version"])

    other = ca.manifest_check(_manifest({"version": newer,
                                         "fingerprint": FOREIGN_FP}))
    assert other["state"] == ca.CHECK_MISMATCH
    assert other["severity"] == "warning"
    assert "patch" in other["words"]
    assert ca.check_status(other) == ca.STATUS_WARNING

    # Тот же patch, но отпечаток совпал: данные каталога те же (слово
    # match), вердикт читателя — предупреждение, и журнал говорит его.
    same_fp = ca.manifest_check(_manifest({"version": newer,
                                           "fingerprint": prod["fingerprint"]}))
    assert same_fp["state"] == ca.CHECK_MATCH
    assert same_fp["severity"] == "warning" and same_fp["quiet"] is False
    assert ca.check_status(same_fp) == ca.STATUS_WARNING

    # Совпадение (черновик, те же отпечатки) по-прежнему не шумит.
    exact = ca.manifest_check(_manifest(prod))
    assert exact["quiet"] is True
    assert ca.check_status(exact) == ca.STATUS_MATCH


# ------------------------------------------------------------------ 2
def test_каталог_не_читается_сторож_не_судит_а_отказывает(tmp_path,
                                                          monkeypatch):
    """Каталога нет либо он битый — ОТКАЗ словами с путём, а не
    выдуманный исход сверки: сторож до себя дела не доводит, отказ
    каталога переводят рубежи нажатия («сторож пропущен»).

    Прежде здесь стоял тот же стенд про нечитаемый СРЕЗ базы
    (`CREW_VMP_TEMPLATE_BASE`); режим среза снят частью 7-Б, и предмет
    стенда перенесён на битый каталог — источник у продукта один, и
    судить его отказ обязан тот же закон."""
    missing = tmp_path / "каталога-нет"
    cc.use_catalog(monkeypatch, missing)
    with pytest.raises(ca.CATALOG_ERRORS) as e:
        ca.product_stamp()
    assert str(missing) in str(e.value)
    # Сторож на отказе не подменяется исходом: пусть судят рубежи.
    with pytest.raises(ca.CATALOG_ERRORS):
        ca.manifest_check(_manifest({"version": "0.1.0",
                                     "fingerprint": FOREIGN_FP}))

    broken = tmp_path / "битый"
    broken.mkdir()
    (broken / "catalog.json").write_text("не json", encoding="utf-8")
    cc.use_catalog(monkeypatch, broken)
    with pytest.raises(ca.CATALOG_ERRORS) as e2:
        ca.product_stamp()
    assert str(broken) in str(e2.value)


def test_две_версии_выпуска_с_разными_данными_стоп(tmp_path, monkeypatch):
    """«Одна версия — разные данные» у двух ВЫПУСКОВ — стоп, а не
    предупреждение: законно нести разные данные под одной версией может
    только черновик. На каталоге репозитория ветка недостижима, пока он
    черновик, — мерку выпуска даёт tmp-копия каталога версии 1.0.0."""
    rel = _released_catalog(tmp_path, monkeypatch)
    prod = _stamp()
    assert prod["version"] == "1.0.0"
    chk = ca.manifest_check(_manifest({"version": "1.0.0",
                                       "fingerprint": "1" * 64}))
    assert chk["state"] == ca.CHECK_STOP and chk["severity"] == "error"
    assert "разные данные" in chk["words"]
    assert ca.check_status(chk) == ca.STATUS_STOP
    assert chk["product"] == {"version": "1.0.0",
                              "fingerprint": prod["fingerprint"],
                              "source": "catalog"}
    assert str(rel) == ca.stamp()["path"]
    # Тот же выпуск с теми же данными — тишина: стоп судит данные, а не
    # сам факт выпуска.
    same = ca.manifest_check(_manifest(prod))
    assert same["state"] == ca.CHECK_MATCH and same["severity"] is None
    assert ca.check_status(same) == ca.STATUS_MATCH


# ------------------------------------------------------------------ 3
def _orchestrate(tmp_path, stamp: dict | None, doc=None):
    run = _run_dir(tmp_path, doc if doc is not None else _h0_doc())
    fakes = _builder_fakes(_fakes(True))
    fakes["load_pkg"] = lambda path: {"manifest": _manifest(stamp)}
    solver = FakeSolver()
    d = btn.orchestrate(run, solver, task="pairing",
                        sleep=lambda *_: None, **fakes)
    log = json.loads((run / "button-pairing" / "log.json")
                     .read_text(encoding="utf-8"))
    return run, solver, d, {e["step"]: e for e in log}


def test_нажатие_останавливается_на_чужой_версии_каталога(tmp_path):
    """Стенд шага 3: пакет со старшей версией каталога — стоп словами.
    Расчёт не начинается (каталог цепочек не строится), журнал несёт
    строку сторожа, выдача — машинный код отказа и обе стороны сверки."""
    run, solver, d, log = _orchestrate(
        tmp_path, {"version": "1.0.0", "fingerprint": FOREIGN_FP})
    assert d["verdict"] == "остановка: каталог шаблонов пакета не тот", d
    assert d["reason_code"] == "catalog-version-mismatch"
    assert d["template_catalog"]["state"] == ca.CHECK_STOP
    assert d["template_catalog"]["package"]["version"] == "1.0.0"
    assert d["template_catalog"]["product"]["version"] == ca.stamp()["version"]
    assert "1.0.0" in d["detail"] and "расчёт не начинается" in d["detail"]
    assert log["Ш-1кш"]["status"] == "остановка"
    assert not (run / "button-pairing" / "builder-catalog.json").exists()
    assert not hasattr(solver, "catalog_call")


def _passed_guard(d: dict, log: dict) -> None:
    """Сторож пропустил нажатие: остановки каталога нет, вывод реестра
    (Ш-1р) состоялся. Чем кончится сам расчёт — предмет других стендов;
    здесь важно, что предупреждение не останавливает."""
    assert d["verdict"] != "остановка: каталог шаблонов пакета не тот", d
    assert d.get("reason_code") != "catalog-version-mismatch", d
    assert "Ш-1р" in log, log


def test_нажатие_идёт_с_предупреждением_и_пишет_исход_в_реестр(tmp_path):
    """Другие данные каталога при patch-разнице версий — предупреждение,
    а не остановка: нажатие идёт, слова стоят в журнале, а исход сверки —
    в блоке catalog реестра исполнителей этого нажатия (реестр говорит не
    только «под каким каталогом выведен», но и «сошёлся ли пакет»).

    Мерка взята patch-разницей, а не «та же версия»: живой каталог с
    08.09 — выпуск 0.2.0, и одна версия с другими данными у выпуска
    останавливает расчёт (стенд 1а). Предупреждение у выпуска даёт
    именно patch."""
    prod = _stamp()
    run, _solver, d, log = _orchestrate(
        tmp_path, {"version": _next_patch(prod["version"]),
                   "fingerprint": FOREIGN_FP})
    _passed_guard(d, log)
    assert log["Ш-1кш"]["status"] == "предупреждение"
    # Слова patch-разницы называют обе ВЕРСИИ (отпечатки называются там,
    # где сравнивались именно данные).
    assert _next_patch(prod["version"]) in log["Ш-1кш"]["detail"]
    assert prod["version"] in log["Ш-1кш"]["detail"]
    reg = json.loads((run / "registry" / "vmp-executors-registry.json")
                     .read_text(encoding="utf-8"))
    assert reg["catalog"]["manifest_check"] == ca.CHECK_MISMATCH
    assert reg["catalog"]["fingerprint"] == prod["fingerprint"]
    # Рядом с исходом — мерка, по которой он вынесен: каталог продукта
    # (источник у мерки один — с шага 7-Б это всегда каталог).
    assert reg["catalog"]["manifest_check_against"] == {
        **prod, "source": "catalog"}


def test_пакет_без_ключа_каталога_идёт_с_пометкой(tmp_path):
    """Пакет до «Дополнения к 1.18» читается прежним законом: нажатие
    идёт, но молчания нет — «версия каталога пакетом не объявлена» и в
    журнале, и в реестре."""
    run, _solver, d, log = _orchestrate(tmp_path, None)
    _passed_guard(d, log)
    assert log["Ш-1кш"]["status"] == "предупреждение"
    assert "не объявлена" in log["Ш-1кш"]["detail"]
    reg = json.loads((run / "registry" / "vmp-executors-registry.json")
                     .read_text(encoding="utf-8"))
    assert reg["catalog"]["manifest_check"] == ca.CHECK_NOT_DECLARED


def test_отказ_каталога_сторож_пропущен_словами(tmp_path, monkeypatch):
    """Отказ САМОГО каталога — не дело сторожа: строка
    журнала «пропущен» называет отказ словами, исход не выдумывается, и
    нажатие идёт дальше — каталог судят свои рубежи (Ш-1п2, вывод
    реестра)."""
    def _raise(_manifest):
        raise ca.CatalogUnavailable("каталог шаблонов: проба отказа")
    monkeypatch.setattr(ca, "manifest_check", _raise)
    _run, _solver, d, log = _orchestrate(tmp_path, _stamp())
    assert log["Ш-1кш"]["status"] == "пропущен"
    assert "проба отказа" in log["Ш-1кш"]["detail"]
    _passed_guard(d, log)
    reg = json.loads((_run / "registry" / "vmp-executors-registry.json")
                     .read_text(encoding="utf-8"))
    assert reg["catalog"]["manifest_check"] is None
    assert reg["catalog"]["manifest_check_against"] is None


def test_совпадение_не_шумит(tmp_path):
    """Сошлось — строка журнала «сошлось» и слово match в реестре;
    предупреждений на ровном месте нет."""
    run, _solver, d, log = _orchestrate(tmp_path, _stamp())
    _passed_guard(d, log)
    assert log["Ш-1кш"]["status"] == "сошлось"
    reg = json.loads((run / "registry" / "vmp-executors-registry.json")
                     .read_text(encoding="utf-8"))
    assert reg["catalog"]["manifest_check"] == ca.CHECK_MATCH


# ------------------------------------------------------------------ 4
def _package_released_by(base: Path, stamp: dict | None) -> bytes:
    """Байты НАСТОЯЩЕГО пакета, выпущенного каталогом stamp: ключ
    manifest.catalog и сводка fits_catalog переписываются вместе —
    гейт производителя судит их согласованность, и пакет остаётся
    законным (иначе стенд ловил бы кривой пакет, а не чужой каталог)."""
    from cdpm import export_task_package as tp
    body = _pkg_bytes(base, _ready_doc())
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    man = json.loads(files["manifest.json"])
    doc = json.loads(files["model.json"])
    if stamp is None:
        man.pop("catalog", None)
    else:
        man["catalog"] = dict(stamp)
    fits = tp.fits_catalog_of(doc, man.get("catalog"))
    if fits is None:
        man.pop("fits_catalog", None)
    else:
        man["fits_catalog"] = fits
    files["manifest.json"] = json.dumps(man, ensure_ascii=False).encode("utf-8")
    return tp.write_zip(files)


@pytest.fixture()
def _panel(tmp_path, monkeypatch):
    """Панель на своём каталоге данных; реестр исполнителей при нажатии
    — как в стендах кнопки (синтетический свод закрывается фикстурой)."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    for name in ("CREW_TASK_INBOX", "CREW_TASK_PACKAGE",
                 "CREW_VMP_REGISTRY", "CREW_SOLVER_URL"):
        monkeypatch.delenv(name, raising=False)
    from webapp import flow, task_channel
    from vmp import registry_check as _regc
    monkeypatch.setitem(flow._READY_CACHE, "key", None)
    monkeypatch.setitem(task_channel._CARD_CACHE, "key", None)
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task, manifest_check=None,
                             manifest_check_against=None):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "catalog": {**ca.stamp(), "manifest_check": manifest_check,
                           "manifest_check_against": manifest_check_against},
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)
    return tmp_path


def _make_current(body: bytes) -> str:
    from webapp import task_channel
    sha = hashlib.sha256(body).hexdigest()
    code, brief = task_channel.receive(body, sha)
    assert code in (200, 201, 202), brief
    return sha


def test_панель_отказывает_словами_до_занятия_кнопки(_panel):
    """Пакет, выпущенный чужой (старшей) версией каталога, принимается
    как прежде — приём каталога не судит; отказывает НАЖАТИЕ: словами,
    машинным кодом и ДО занятия кнопки — попытки нет, кнопка свободна,
    расчёт не начат."""
    from webapp import package_runs as pr
    sha = _make_current(_package_released_by(
        _panel / "src", {"version": "1.0.0", "fingerprint": FOREIGN_FP}))
    assert pr.status()["present"]
    with pytest.raises(pr.PackageRunError) as e:
        pr.press("pairing", solver=FakeSolver(), start_thread=False)
    assert e.value.reason_code == "catalog-version-mismatch"
    assert "1.0.0" in str(e.value) and ca.stamp()["version"] in str(e.value)
    st = pr.status()
    assert st["state"] != "кнопка: идёт" and not st.get("attempts")
    wd = pr.workdir_for(sha)
    assert not (wd / "button-pairing").exists()


def test_панель_ведёт_предупреждение_в_паспорт_попытки(_panel):
    """Другие данные каталога при patch-разнице версий — нажатие идёт, и
    предупреждение видно в состоянии: паспорт попытки несёт исход
    сверки рядом с версией и отпечатком каталога продукта. Мерка —
    patch: у ВЫПУСКА 0.2.0 (живой каталог с 08.09) та же версия с
    другими данными останавливает расчёт (стенд 1а)."""
    from tests.test_package_runs import _pairing_kwargs
    from webapp import package_runs as pr
    prod = _stamp()
    _make_current(_package_released_by(
        _panel / "src", {"version": _next_patch(prod["version"]),
                         "fingerprint": FOREIGN_FP}))
    fakes = _pairing_kwargs()
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None, **fakes)
    assert st["done"] is True and st["error"] is None, st
    # Паспорт попытки (7.3) несёт каталог продукта и исход сверки.
    cat = st["attempts"][0]["template_catalog"]
    assert cat["version"] == prod["version"]
    assert cat["fingerprint"] == prod["fingerprint"]
    assert cat["manifest_check"] == ca.CHECK_MISMATCH
    assert _next_patch(prod["version"]) in cat["manifest_words"]
    assert prod["version"] in cat["manifest_words"]
    assert cat["manifest_check_against"] == {**prod, "source": "catalog"}
    assert cat["manifest_check_reason"] is None


def _press_pairing(pr, stamp: dict | None = None, **kw):
    """Нажатие панели с обычными подменами стендов пейринга. Загрузчик
    пакета — шов (`_pairing_kwargs` отдаёт манифест без ключа каталога),
    поэтому здесь он ставится явно: оркестратор внутри нажатия должен
    видеть ровно тот ключ, что объявил пакет."""
    from tests.test_package_runs import _pairing_kwargs
    fakes = _pairing_kwargs()
    fakes["load_pkg"] = lambda path: {"manifest": _manifest(stamp)}
    st = pr.press("pairing", engine="cpsat", solver=FakeSolver(),
                  start_thread=False, sleep=lambda *_: None,
                  **{**fakes, **kw})
    assert st["done"] is True and st["error"] is None, st
    return st["attempts"][0]["template_catalog"]


def test_панель_ведёт_непонятое_объявление_в_паспорт(_panel):
    """Пакет без ключа каталога («Дополнение к 1.18» его не требует)
    проходит и через панель: нажатие идёт, паспорт попытки несёт
    `not-declared` со словами, журнал — строку Ш-1кш."""
    from webapp import package_runs as pr
    sha = _make_current(_package_released_by(_panel / "src", None))
    cat = _press_pairing(pr)
    assert cat["manifest_check"] == ca.CHECK_NOT_DECLARED
    assert "не объявлена" in cat["manifest_words"]
    assert cat["manifest_check_against"]["source"] == "catalog"
    log = json.loads((pr.workdir_for(sha) / "button-pairing" / "log.json")
                     .read_text(encoding="utf-8"))
    assert {e["step"]: e for e in log}["Ш-1кш"]["status"] == "предупреждение"


def test_панель_называет_причину_когда_сверки_не_было(_panel, monkeypatch):
    """Сверки не было вовсе (манифест пакета не прочитан, каталог
    недоступен): паспорт не молчит и не выдаёт это за «сошлось» —
    исход `null`, причина названа машинным кодом."""
    from webapp import package_runs as pr
    prod = _stamp()
    _make_current(_package_released_by(_panel / "src", prod))
    monkeypatch.setattr(pr, "_catalog_manifest_check", lambda p: {
        "state": None, "words": None, "reason_code": "manifest-unreadable",
        "error": "проба"})
    cat = _press_pairing(pr, prod)
    assert cat["manifest_check"] is None and cat["manifest_words"] is None
    assert cat["manifest_check_reason"] == "manifest-unreadable"
    assert cat["manifest_check_against"] is None


def test_панель_останавливает_две_версии_выпуска_словами(_panel, monkeypatch):
    """Стоп «одна версия — разные данные» у двух ВЫПУСКОВ доходит до
    нажатия панели: отказ словами и машинным кодом, попытки нет. Мерку
    выпуска даёт tmp-копия каталога версии 1.0.0 — каталог репозитория
    пока черновик (прежде мерку давал снятый режим среза базы)."""
    from webapp import package_runs as pr
    body = _package_released_by(_panel / "src",
                                {"version": "1.0.0", "fingerprint": "1" * 64})
    sha = _make_current(body)
    rel = _released_catalog(_panel, monkeypatch)
    with pytest.raises(pr.PackageRunError) as e:
        pr.press("pairing", solver=FakeSolver(), start_thread=False)
    assert e.value.reason_code == "catalog-version-mismatch"
    assert "разные данные" in str(e.value)
    assert ca.stamp()["fingerprint"][:12] in str(e.value)
    assert str(rel) == ca.stamp()["path"]
    assert not (pr.workdir_for(sha) / "button-pairing").exists()


