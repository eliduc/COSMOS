# -*- coding: utf-8 -*-
"""Набор «ИД H0-ВМП» (базовый): отпечатки источников эталона обязаны быть
отпечатками ФАЙЛОВ комплекта.

Дефект 11.09.2026: сборщик build_kit_h0.py пересобирал docx/xlsx/jpg, но
блок `sources` формы эталона (etalon/VMP-H0-form.json) и ВМП эталона
(etalon/VMP-H0.json) оставлял с отпечатками ПРЕЖНЕЙ сборки — эталон
называл sha, которого в комплекте уже нет, и это число уходило в паспорт
прогона и в сводку как отпечаток поданного файла. Сборщики вариаций
(H0-B, H0-C, H0-К) отпечатки ставили; базовый — нет.

Стенд судит два: выложенный комплект (sources = sha файлов docs/) и сам
сборщик (пересборка ставит отпечатки по факту сборки, а расхождение
источников формы и ВМП — отказ словами). Равенство пересобранного jpg с
выложенным судится только теми же шрифтами, что у выложенной сборки
(на Linux их нет — пропуск словами, находка CI № 81 11.09.2026).
"""
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KIT = ROOT / "open-bench" / "polyaris-h0"
FORM = KIT / "etalon" / "VMP-H0-form.json"
VMP = KIT / "etalon" / "VMP-H0.json"


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_kit_h0", KIT / "build_kit_h0.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_sources_формы_эталона_h0_согласованы_с_документами_комплекта():
    """Выложенный комплект: у каждого источника формы отпечаток — sha
    файла docs/<file>, лежащего в комплекте. Расхождение = эталон о
    файлах, которых в наборе нет (пересобрали документы, эталон не
    перевязали)."""
    form = _read(FORM)
    srcs = form.get("sources") or []
    assert srcs, "в форме эталона H0 нет блока sources"
    for s in srcs:
        p = KIT / "docs" / s["file"]
        assert p.is_file(), f"источник формы не выложен: {p}"
        assert s["sha256"] == _sha(p), (
            f"{s['sid']} {s['file']}: форма называет {s['sha256'][:12]}…, "
            f"файл комплекта — {_sha(p)[:12]}… (пересоберите набор: "
            f"python open-bench/polyaris-h0/build_kit_h0.py)")


def test_sources_вмп_эталона_h0_те_же_что_у_формы():
    """Обе проекции эталона (ВМП и форма) — об одних и тех же файлах с
    одними и теми же отпечатками, а порядок sid — порядок подачи файлов
    правил манифеста (так же нумерует заморозка, cdpm.freeze)."""
    form, vmp = _read(FORM), _read(VMP)
    assert vmp.get("sources") == form.get("sources")
    mani = _read(KIT / "kit-manifest.json")
    assert [s["file"] for s in form["sources"]] == \
        [Path(rel).name for rel in mani["rules_files"]]
    assert [s["sid"] for s in form["sources"]] == \
        [f"S{i}" for i in range(1, len(mani["rules_files"]) + 1)]


def _rebuild_in_tmp(b, tmp_path: Path, monkeypatch) -> tuple:
    """Пересборка комплекта во временной папке по копии эталона, у формы
    которой заведомо чужие отпечатки; возвращает (docs, путь формы)."""
    docs, etalon = tmp_path / "docs", tmp_path / "etalon"
    etalon.mkdir(parents=True, exist_ok=True)
    form = _read(FORM)
    for s in form["sources"]:
        s["sha256"] = "0" * 64                 # заведомо чужие отпечатки
    form_path = etalon / "VMP-H0-form.json"
    form_path.write_text(json.dumps(form, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    shutil.copyfile(VMP, etalon / "VMP-H0.json")
    monkeypatch.setattr(b, "HERE", tmp_path)
    monkeypatch.setattr(b, "DOCS", docs)
    monkeypatch.setattr(b, "ETALON", etalon)
    monkeypatch.setattr(b, "FORM_H0", form_path)
    assert b.main() == 0
    return docs, form_path


def test_сборщик_h0_ставит_отпечатки_источников_по_факту_сборки(tmp_path,
                                                                monkeypatch):
    """Пересборка обязана перевязать `sources` обеих проекций на только
    что собранные файлы, не трогая остальную форму. Судится на любой
    машине: отпечаток — sha файла, который сборщик только что записал."""
    b = _builder()
    docs, form_path = _rebuild_in_tmp(b, tmp_path, monkeypatch)
    out, et = _read(form_path), _read(form_path.parent / "VMP-H0.json")
    assert out["sources"] == et["sources"]
    for s in out["sources"]:
        assert s["sha256"] == _sha(docs / s["file"]), s["file"]
    # кроме отпечатков форма не тронута
    was = _read(FORM)
    out.pop("sources"), was.pop("sources")
    assert out == was


def test_сборщик_h0_воспроизводит_выложенный_jpg_теми_же_шрифтами(tmp_path,
                                                                  monkeypatch):
    """jpg собирается байт в байт (в изображении нет меток времени) — но
    только теми же шрифтами, что у выложенной сборки (JPG_FONTS сборщика):
    без них Pillow рисует запасным шрифтом, байты и отпечаток другие. Так
    упало задание Linux в CI № 81 (11.09.2026): на машине без этих шрифтов
    равенство с выложенным файлом не судится, а пропускается словами;
    отпечаток по факту сборки судит тест выше на любой машине."""
    b = _builder()
    if not b.jpg_fonts_present():
        pytest.skip("на машине нет шрифтов выложенной сборки jpg "
                    f"({', '.join(b.JPG_FONTS)}): запасной шрифт Pillow "
                    "даёт другие байты — равенство с выложенным файлом "
                    "не судится")
    _docs, form_path = _rebuild_in_tmp(b, tmp_path, monkeypatch)
    jpg = next(s for s in _read(form_path)["sources"]
               if s["file"].endswith(".jpg"))
    assert jpg["sha256"] == _sha(KIT / "docs" / jpg["file"])


def test_сборщик_h0_отказывает_когда_эталон_и_форма_о_разных_источниках(
        tmp_path, monkeypatch):
    """ВМП эталона о других файлах, чем форма, — отказ словами: молча
    переписать чужой блок источников сборщик не имеет права."""
    b = _builder()
    docs, etalon = tmp_path / "docs", tmp_path / "etalon"
    etalon.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FORM, etalon / "VMP-H0-form.json")
    vmp = _read(VMP)
    vmp["sources"][0]["file"] = "ЧУЖОЙ.docx"
    (etalon / "VMP-H0.json").write_text(
        json.dumps(vmp, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    monkeypatch.setattr(b, "HERE", tmp_path)
    monkeypatch.setattr(b, "DOCS", docs)
    monkeypatch.setattr(b, "ETALON", etalon)
    monkeypatch.setattr(b, "FORM_H0", etalon / "VMP-H0-form.json")
    assert b.main() == 1
    assert not (tmp_path / "kit-manifest.json").is_file()


def test_stamp_sources_отказывает_когда_файла_источника_нет(tmp_path):
    """Формула одна на все сборщики формы H0: файла из `sources` нет на
    диске — отказ, а не молчаливый пропуск; формы без блока источников
    сборщику не бывает."""
    b = _builder()
    form = {"sources": [{"sid": "S1", "file": "нет-такого.docx",
                         "sha256": "0" * 64}]}
    with pytest.raises(SystemExit):
        b.stamp_sources(form, tmp_path)
    (tmp_path / "нет-такого.docx").write_bytes(b"x")
    assert b.stamp_sources(form, tmp_path)[0]["sha256"] == \
        hashlib.sha256(b"x").hexdigest()
    with pytest.raises(SystemExit):
        b.stamp_sources({}, tmp_path)
