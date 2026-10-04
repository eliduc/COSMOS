# -*- coding: utf-8 -*-
"""Допуск моделей комплекта действует и на зрение при заморозке
(аудит 04.09, пункт A12).

Охраняемый контракт: действующий допуск вычисляется по признаку
комплекта ДО заморозки и передаётся в выбор зрительного канала;
«local-only» + cloud_images=true + нет локальной зрительной модели —
честный отказ словами (422, с названием политики), облачный канал
даже не строится; синтетический класс (допуск any) — облако законно.
Отказ дисциплины контура COSMOS (ScopeViolation) переводится в тот же
отказ заморозки. Зрительная модель везде подменная."""
import json

import pytest
from fastapi.testclient import TestClient

from cdpm import llm as llm_mod
from cdpm import webui


@pytest.fixture(autouse=True)
def _declared_layout(declared_test_layout):
    """Гейт «Новый прогон» (шаг 2 проекта «Запись о прогоне», 11.1): при
    допуске, разрешающем облако, прогон не создаётся без объявленной
    раскладки моделей. Тесты этого файла объявляют её явно
    (conftest.declared_test_layout); роль «зрение» фикстура не
    объявляет — поведение «зрительной модели нет» судится по-прежнему."""


def _make_png(path):
    from PIL import Image
    Image.new("RGB", (60, 30), "white").save(str(path))


_GOOD_REPLY = json.dumps({"paragraphs": ["1.9. Отдых не менее 10 часов."]},
                         ensure_ascii=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    calls = []

    def fake_factory(scope, requested=None):
        calls.append({"scope": scope, "requested": requested})
        return lambda prompt, image_path: _GOOD_REPLY

    monkeypatch.setattr(webui, "vision_llm_factory", fake_factory)
    c = TestClient(webui.app)
    c.vision_calls = calls
    return c


def _png_bytes(tmp_path):
    p = tmp_path / "img.png"
    _make_png(p)
    return p.read_bytes()


def test_local_only_cloud_images_without_local_vision_is_refusal(
        client, tmp_path, monkeypatch):
    """Признак не выставлен → local-only; cloud_images=true не
    разрешает облако: отказ словами, облачный канал не строился."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post("/api/runs?cloud_images=true", files=[
        ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 422, resp.text
    got = resp.json()
    assert got["state"] == "отказ заморозки"
    assert "local-only" in got["error"]
    assert "признаком комплекта" in got["error"]
    assert "cloud_images" in got["error"]
    assert client.vision_calls == [], "облачный канал не строится"
    # Признак комплекта записан и при отказе — источник допуска виден.
    status = client.get(f"/api/runs/{got['run']}").json()
    assert status["kit"]["llm_scope"] is None
    assert status["error"] == got["error"]


def test_explicit_local_only_with_proprietary_class_is_refusal(
        client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post(
        "/api/runs?cloud_images=true&data_class=proprietary"
        "&llm_scope=local-only", files=[
            ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 422, resp.text
    assert "local-only" in resp.json()["error"]
    assert client.vision_calls == []


def test_synthetic_class_allows_cloud_images(client, tmp_path,
                                             monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post(
        "/api/runs?cloud_images=true&data_class=synthetic", files=[
            ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "заморожено"
    assert client.vision_calls == [{"scope": "any", "requested": "cloud"}]


def test_synthetic_class_allows_cloud_images_without_flag(client, tmp_path,
                                                          monkeypatch):
    """Допуск «any» по классу данных сам разрешает облако для изображений:
    отдельный флаг cloud_images не обязан повторять класс данных (живой
    урок 05.09: ручная заморозка синтетического набора с jpg в панели
    отказывала «зрительная модель не настроена»)."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post(
        "/api/runs?data_class=synthetic", files=[
            ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "заморожено"
    assert client.vision_calls == [{"scope": "any", "requested": "cloud"}]


def test_proprietary_with_consent_allows_cloud_images(client, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post(
        "/api/runs?cloud_images=true&data_class=proprietary&llm_scope=any"
        "&llm_scope_basis=согласие%20от%2001.09", files=[
            ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 200, resp.text
    assert client.vision_calls == [{"scope": "any", "requested": "cloud"}]


def test_local_only_with_local_vision_builds_local_channel_in_scope(
        client, tmp_path, monkeypatch):
    """Локальная зрительная модель есть — канал строится в допуске
    комплекта (local-only), облако не запрашивается даже при
    cloud_images=true."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: True)
    resp = client.post("/api/runs?cloud_images=true", files=[
        ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 200, resp.text
    assert client.vision_calls == [{"scope": "local-only",
                                    "requested": "local"}]


def test_scope_violation_from_factory_is_honest_422(tmp_path,
                                                    monkeypatch):
    """Отказ дисциплины контура COSMOS (ScopeViolation не наследует
    LLMCallError) — тот же честный отказ заморозки словами, не 500."""
    from webapp.llm_backend import ScopeViolation
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)

    def raising_factory(scope, requested=None):
        raise ScopeViolation("dataset is marked local-only")

    monkeypatch.setattr(webui, "vision_llm_factory", raising_factory)
    # Допуск any — отказ ниже, у самого канала: перехват обязателен,
    # а слова — по значению допуска: признак комплекта облако НЕ
    # запрещал, это отказ дисциплины контура при допуске any.
    c = TestClient(webui.app)
    resp = c.post("/api/runs?cloud_images=true&data_class=synthetic",
                  files=[("files", ("SKAN.png", _png_bytes(tmp_path),
                                    "image/png"))])
    assert resp.status_code == 422, resp.text
    got = resp.json()
    assert got["state"] == "отказ заморозки"
    assert "дисциплины контура COSMOS" in got["error"]
    assert "допуске комплекта any" in got["error"]
    assert "dataset is marked local-only" in got["error"]
    assert "запрещено признаком комплекта" not in got["error"]


def test_scope_violation_at_local_only_names_kit_sign(tmp_path,
                                                      monkeypatch):
    """Контроль к предыдущему: тот же ScopeViolation при допуске
    «только локально» (локальная зрительная модель есть, канал
    строится в допуске и отказывает) — источник запрета назван:
    «запрещено признаком комплекта … local-only»."""
    from webapp.llm_backend import ScopeViolation
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: True)
    seen = []

    def raising_factory(scope, requested=None):
        seen.append((scope, requested))
        raise ScopeViolation("dataset is marked local-only")

    monkeypatch.setattr(webui, "vision_llm_factory", raising_factory)
    c = TestClient(webui.app)
    resp = c.post("/api/runs?cloud_images=true&data_class=proprietary"
                  "&llm_scope=local-only",
                  files=[("files", ("SKAN.png", _png_bytes(tmp_path),
                                    "image/png"))])
    assert resp.status_code == 422, resp.text
    got = resp.json()
    assert seen == [("local-only", "local")]
    assert "запрещено признаком комплекта" in got["error"]
    assert "local-only" in got["error"]
    assert "дисциплины контура COSMOS" not in got["error"]
    assert c.get(f"/api/runs/{got['run']}").json()["error"] == got["error"]


def test_vision_for_images_scope_reaches_factory(monkeypatch):
    """Прямой контракт _vision_for_images: допуск передаётся фабрике
    как есть; при local-only просьба облака — отказ до фабрики."""
    calls = []

    def fake_factory(scope, requested=None, **extra):
        calls.append((scope, requested))
        return lambda prompt, image_path: _GOOD_REPLY

    monkeypatch.setattr(webui, "vision_llm_factory", fake_factory)
    monkeypatch.setattr(webui, "_vision_role_models",
                        lambda run_dir=None: (None, None))
    monkeypatch.setattr(llm_mod, "local_vision_available", lambda: False)
    webui._vision_for_images(True, None, scope="any")
    assert calls == [("any", "cloud")]
    with pytest.raises(llm_mod.LLMCallError) as e:
        webui._vision_for_images(True, None, scope="local-only",
                                 scope_reading="признак не выставлен")
    assert "local-only" in str(e.value)
    assert "признак не выставлен" in str(e.value)
    assert calls == [("any", "cloud")]
    # Без cloud_images — прежний отказ «локальная модель не настроена».
    with pytest.raises(llm_mod.LLMCallError) as e2:
        webui._vision_for_images(False, None, scope="local-only")
    assert "локальной моделью недоступно" in str(e2.value)


# ---------------------------------------------------------------------------
# PDF со страницей-сканом: отказ политики виден словами, не «канал не
# передан» (замечание ревизии A12)
# ---------------------------------------------------------------------------

def _scan_pdf_bytes(tmp_path):
    """PDF из одной страницы без текстового слоя, с картинкой (скан)."""
    import fitz
    png = tmp_path / "scan-page.png"
    _make_png(png)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(40, 40, 300, 200), filename=str(png))
    data = doc.tobytes()
    doc.close()
    return data


def _text_pdf_bytes():
    """PDF из одной страницы с текстовым слоем (зрение не нужно)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "1.9. Rest at home base is not less "
                               "than 10 hours.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_scan_page_at_local_only_names_policy_in_refusal(
        client, tmp_path, monkeypatch):
    """Признак не выставлен → local-only; cloud_images=true; локальной
    зрительной модели нет; PDF со страницей-сканом → 422, и в словах
    отказа — настоящая причина (допуск комплекта), не только «канал
    зрения не передан»; облачный канал не строился."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post("/api/runs?cloud_images=true", files=[
        ("files", ("SKAN.pdf", _scan_pdf_bytes(tmp_path),
                   "application/pdf"))])
    assert resp.status_code == 422, resp.text
    got = resp.json()
    assert got["state"] == "отказ заморозки"
    err = got["error"]
    assert "страница 1 без текстового слоя" in err
    assert "канал зрения не построен" in err
    assert "local-only" in err
    assert "признаком комплекта" in err
    assert "«только локально»" in err
    assert "запрещает облако для изображений" in err
    assert client.vision_calls == [], "облачный канал не строится"
    status = client.get(f"/api/runs/{got['run']}").json()
    assert status["error"] == err
    assert status["kit"]["llm_scope"] is None


def test_pdf_scan_page_without_cloud_request_names_missing_local_model(
        client, tmp_path, monkeypatch):
    """Контроль: без cloud_images причина иная — локальная зрительная
    модель не настроена; слов о запрете признаком комплекта нет."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post("/api/runs", files=[
        ("files", ("SKAN.pdf", _scan_pdf_bytes(tmp_path),
                   "application/pdf"))])
    assert resp.status_code == 422, resp.text
    err = resp.json()["error"]
    assert "канал зрения не построен" in err
    assert "локальной моделью недоступно" in err
    assert "признаком комплекта" not in err
    assert client.vision_calls == []


def test_pdf_with_text_layer_passes_at_local_only(client, tmp_path,
                                                  monkeypatch):
    """Контроль: текстовый PDF при тех же условиях проходит заморозку —
    отказ канала зрения помнится, но применяется только к скану."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post("/api/runs?cloud_images=true", files=[
        ("files", ("TEXT.pdf", _text_pdf_bytes(), "application/pdf"))])
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "заморожено"
    assert client.vision_calls == []


def test_pdf_scan_page_synthetic_allows_cloud(client, tmp_path,
                                              monkeypatch):
    """Контроль: при допуске any (синтетический класс) скан в PDF
    уходит облаку по явной просьбе."""
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post(
        "/api/runs?cloud_images=true&data_class=synthetic", files=[
            ("files", ("SKAN.pdf", _scan_pdf_bytes(tmp_path),
                       "application/pdf"))])
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "заморожено"
    assert client.vision_calls == [{"scope": "any", "requested": "cloud"}]
