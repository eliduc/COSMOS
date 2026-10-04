# -*- coding: utf-8 -*-
"""Заморозка PDF (отмашка владельца 2026-08-26): текстовый слой —
постранично в клаузы с якорями «стр.P, абз.N» и жирностью; страница
без текстового слоя (скан) — рендер в изображение и существующий
зрительный канал, дословно, с признаком происхождения и перечнем
распознанных страниц.

Охраняемые контракты: нумерованный пункт даёт якорь «п.X» (как у
docx/md); жирный шрифт сохраняется маркерами `**…**` (урок П.1.9);
скан без канала зрения и зашифрованный PDF — честные отказы с
названной причиной; пустая страница пропускается молча (нормы на ней
нет); смешанный PDF несёт recognized_pages только со сканами.
Зрительная модель везде подменная — живых вызовов тесты не делают."""
import json

import pytest

fitz = pytest.importorskip("fitz")

from cdpm import freeze                                    # noqa: E402

_PARAS = ["7.1. Скан-страница: отдых не менне 10 часов.",
          "Абзац без номера со скана."]
_GOOD_REPLY = json.dumps({"paragraphs": _PARAS}, ensure_ascii=False)


def _fake_vision(reply=_GOOD_REPLY):
    def fn(prompt, image_path):
        fn.calls += 1
        fn.paths.append(str(image_path))
        return reply
    fn.calls = 0
    fn.paths = []
    return fn


def _text_page(doc, lines, fontname="helv"):
    page = doc.new_page()
    y = 72
    for line, fname in lines:
        page.insert_text((72, y), line, fontname=fname or fontname,
                         fontsize=11)
        y += 40
    return page


def _scan_page(doc):
    """Страница-скан: только картинка, текстового слоя нет."""
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 40))
    pix.clear_with(200)
    page.insert_image(fitz.Rect(72, 72, 400, 300),
                      stream=pix.tobytes("png"))
    return page


def _make_pdf(path, builder):
    doc = fitz.open()
    builder(doc)
    doc.save(str(path))
    doc.close()


def test_pdf_text_layer_anchors_and_bold(tmp_path):
    p = tmp_path / "PRAVILA.pdf"
    _make_pdf(p, lambda d: _text_page(d, [
        ("1.9. Отдых на базе не менее 10 часов.", None),
        ("Абзац без номера про кириллицу.", None),
        # Латиница: встроенный жирный шрифт (hebo) не несёт кириллицы
        # — это свойство тестовой фикстуры, не кода.
        ("Bold rule item.", "hebo"),
    ]))
    got = freeze.extract_file(p)
    assert got.get("recognized") is None
    anchors = [c["anchor"] for c in got["clauses"]]
    assert "п.1.9" in anchors
    assert "стр.1, абз.2" in anchors
    texts = {c["anchor"]: c["text"] for c in got["clauses"]}
    # Жирный шрифт сохранён маркерами — норма не теряет вес.
    bold = [t for t in texts.values() if t.startswith("**")]
    assert bold and "Bold rule" in bold[0]


def test_pdf_scan_page_goes_to_vision_with_page_anchor(tmp_path):
    p = tmp_path / "SKAN.pdf"
    _make_pdf(p, lambda d: (
        _text_page(d, [("1.1. Текстовая страница один — довольно "
                        "длинная строка слоя.", None)]),
        _scan_page(d)))
    fake = _fake_vision()
    got = freeze.extract_file(p, vision_llm=fake)
    assert fake.calls == 1                 # зрению ушла ровно 1 стр.
    assert got["recognized"] == "image"
    assert got["recognized_pages"] == [2]
    anchors = [c["anchor"] for c in got["clauses"]]
    assert "п.1.1" in anchors              # текстовая страница
    assert "п.7.1" in anchors              # нумерованный пункт скана
    assert "стр.2, абз.2" in anchors       # безномерной абзац скана
    # Дословность: опечатка «менне» из ответа модели не исправлена.
    assert any("менне" in c["text"] for c in got["clauses"])


def test_pdf_scan_without_vision_refused_with_page(tmp_path):
    p = tmp_path / "SKAN2.pdf"
    _make_pdf(p, lambda d: _scan_page(d))
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    msg = str(e.value)
    assert "страница 1" in msg and "зрительной модели" in msg


def test_pdf_encrypted_refused(tmp_path):
    p = tmp_path / "CRYPT.pdf"
    doc = fitz.open()
    _text_page(doc, [("1.1. Секретная норма.", None)])
    doc.save(str(p), encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw="secret", owner_pw="secret")
    doc.close()
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    assert "зашифрован" in str(e.value)


def test_pdf_blank_page_skipped_silently(tmp_path):
    p = tmp_path / "BLANK.pdf"

    def build(d):
        _text_page(d, [("1.2. Единственная норма документа.", None)])
        d.new_page()                      # истинно пустая страница
    _make_pdf(p, build)
    got = freeze.extract_file(p)          # канал зрения не нужен
    assert [c["anchor"] for c in got["clauses"]] == ["п.1.2"]


def test_pdf_in_freeze_kit_manifest_and_projection(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    p = src / "PRAVILA-AK.pdf"
    _make_pdf(p, lambda d: (
        _text_page(d, [("2.3. Норма стыковки не менее 45 минут.",
                        None)]),
        _scan_page(d)))
    out = tmp_path / "kit"
    manifest = freeze.freeze([p], out, vision_llm=_fake_vision())
    entry = manifest["files"][0]
    assert entry["sha256"] == freeze.sha256_of(p)
    assert entry["recognized"] == "image"
    assert entry["recognized_pages"] == [2]
    proj = (out / "projections" /
            f"S1-{p.stem}.md").read_text(encoding="utf-8")
    assert "Страницы 2 без текстового слоя РАСПОЗНАНЫ" in proj
    assert "[п.2.3]" in proj
    from cdpm.gates import gate_freeze
    problems, counters = gate_freeze.check_kit(out, [p])
    assert problems == []
    assert counters["originals"] == 1


def test_pdf_text_only_kit_passes_gate_redeterminism(tmp_path):
    """Чисто текстовый PDF: гейт заморозки ПЕРЕЧИТЫВАЕТ оригинал и
    сверяет клаузы — извлечение обязано быть детерминированным."""
    src = tmp_path / "src"
    src.mkdir()
    p = src / "TEKST.pdf"
    _make_pdf(p, lambda d: _text_page(d, [
        ("3.1. Норма для гейта детерминизма.", None)]))
    out = tmp_path / "kit"
    freeze.freeze([p], out)
    from cdpm.gates import gate_freeze
    problems, _ = gate_freeze.check_kit(out, [p])
    assert problems == []
