# -*- coding: utf-8 -*-
"""Заморозка изображений документов (ПИ-1): изображение → зрительная
модель → клаузы с якорями «стр-1-абз-N» → штатная заморозка.

Охраняемые контракты: дословный текст с признаком происхождения
`"recognized": "image"` и SHA-256 самого изображения в манифесте;
невалидный ответ модели — одна повторная попытка, затем честный провал
файла; изображение без канала зрения (в т.ч. класс «только локально»
без локальной зрительной модели) — честный отказ, не тихая деградация.
Зрительная модель везде подменная — живых вызовов тесты не делают."""
import json

import pytest
from fastapi.testclient import TestClient

from cdpm import freeze
from cdpm import llm as llm_mod
from cdpm import webui
from cdpm.gates import gate_freeze


# ---------------------------------------------------------------------------
# Фикстуры: изображение (PIL есть в venv) и подменные зрительные модели
# ---------------------------------------------------------------------------

def _make_png(path):
    from PIL import Image
    Image.new("RGB", (60, 30), "white").save(str(path))


# Дословный текст «с изображения» — с намеренной опечаткой («менне»):
# контракт дословности запрещает модели её исправлять, а тест
# фиксирует, что код текст не «улучшает».
_PARAS = ["1.9. Отдых на базе не менне 10 часов.",
          "Вторая строка абзаца два."]

_GOOD_REPLY = json.dumps({"paragraphs": _PARAS}, ensure_ascii=False)


def _fake_vision(reply=_GOOD_REPLY):
    """Подменная зрительная модель: (промпт, путь) -> заготовленный
    ответ; счётчик вызовов — в атрибуте calls."""
    def fn(prompt, image_path):
        fn.calls += 1
        fn.last_prompt = prompt
        return reply
    fn.calls = 0
    return fn


# ---------------------------------------------------------------------------
# Извлечение клауз из изображения
# ---------------------------------------------------------------------------

def test_image_clauses_verbatim_anchors_and_kind(tmp_path):
    p = tmp_path / "SKAN-PRAVIL.png"
    _make_png(p)
    fake = _fake_vision()
    got = freeze.extract_file(p, vision_llm=fake)
    assert got["recognized"] == "image"
    assert got["converted"] is None
    assert got["clauses"] == [
        {"anchor": "стр-1-абз-1", "kind": "p", "text": _PARAS[0]},
        {"anchor": "стр-1-абз-2", "kind": "p", "text": _PARAS[1]},
    ]
    assert fake.calls == 1
    # Промпт требует дословности и строго одного JSON-объекта.
    assert "ДОСЛОВНО" in fake.last_prompt
    assert "СТРОГО один JSON-объект" in fake.last_prompt


def test_image_without_vision_channel_is_honest_refusal(tmp_path):
    p = tmp_path / "skan.jpg"
    _make_png(p)
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p)
    assert "зрительной модели" in str(e.value)
    assert "skan.jpg" in str(e.value)


def test_invalid_reply_twice_is_honest_file_failure(tmp_path):
    p = tmp_path / "skan.png"
    _make_png(p)
    fake = _fake_vision(reply="это не JSON вовсе")
    with pytest.raises(freeze.FreezeError) as e:
        freeze.extract_file(p, vision_llm=fake)
    assert fake.calls == 2                  # одна повторная попытка
    assert "skan.png" in str(e.value)
    assert "после 2 попыток" in str(e.value)


def test_empty_or_blank_paragraphs_are_invalid(tmp_path):
    p = tmp_path / "skan.png"
    _make_png(p)
    for bad in ('{"paragraphs": []}',
                '{"paragraphs": ["ок", "  "]}',
                '{"paragraphs": ["ок", 7]}',
                '{"абзацы": ["не та схема"]}'):
        fake = _fake_vision(reply=bad)
        with pytest.raises(freeze.FreezeError):
            freeze.extract_file(p, vision_llm=fake)
        assert fake.calls == 2


def test_retry_after_one_invalid_reply_succeeds(tmp_path):
    p = tmp_path / "skan.png"
    _make_png(p)

    def fn(prompt, image_path):
        fn.calls += 1
        return "мусор" if fn.calls == 1 else _GOOD_REPLY
    fn.calls = 0
    got = freeze.extract_file(p, vision_llm=fn)
    assert fn.calls == 2
    assert [c["text"] for c in got["clauses"]] == _PARAS


# ---------------------------------------------------------------------------
# Комплект заморозки, манифест, гейт
# ---------------------------------------------------------------------------

@pytest.fixture()
def kit(tmp_path):
    md = tmp_path / "DOP.md"
    md.write_text("2.4. Ночной рейс требует усиленного состава.\n",
                  encoding="utf-8")
    img = tmp_path / "SKAN-PRAVIL.png"
    _make_png(img)
    out = tmp_path / "kit"
    manifest = freeze.freeze([md, img], out, vision_llm=_fake_vision())
    return [md, img], out, manifest


def test_freeze_marks_provenance_and_image_sha(kit):
    files, out, manifest = kit
    by_name = {f["file"]: f for f in manifest["files"]}
    img = by_name["SKAN-PRAVIL.png"]
    # Манифест несёт SHA-256 самого изображения и признак распознания.
    assert img["sha256"] == freeze.sha256_of(files[1])
    assert img["recognized"] == "image"
    assert img["clause_count"] == 2
    # Текстовый файл признака не получает.
    assert "recognized" not in by_name["DOP.md"]
    entry = json.loads((out / "clauses" / f"{img['sid']}.json")
                       .read_text(encoding="utf-8"))
    assert entry["recognized"] == "image"
    proj = (out / "projections" / f"{img['sid']}-SKAN-PRAVIL.md") \
        .read_text(encoding="utf-8")
    assert "РАСПОЗНАН" in proj              # происхождение видно и людям
    assert _PARAS[0] in proj


def test_gate_green_with_image_original(kit, capsys):
    files, out, _ = kit
    rc = gate_freeze.run(str(out), [str(p) for p in files])
    outtxt = capsys.readouterr().out
    assert rc == 0, outtxt
    assert "файлов 2" in outtxt
    assert "оригиналов сверено 2" in outtxt


def test_gate_red_on_changed_image_original(kit, capsys):
    files, out, _ = kit
    files[1].write_bytes(files[1].read_bytes() + b"x")
    rc = gate_freeze.run(str(out), [str(p) for p in files])
    assert rc == 1
    assert "изменён после заморозки" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Веб-интерфейс: правило строжайшего доступного класса при заморозке
# ---------------------------------------------------------------------------

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


def test_upload_image_without_local_vision_is_honest_refusal(
        client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    resp = client.post("/api/runs", files=[
        ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 422, resp.text
    got = resp.json()
    assert got["state"] == "отказ заморозки"
    assert ("распознавание изображений локальной моделью недоступно"
            in got["error"])
    assert client.vision_calls == []        # канал даже не строился


def test_upload_image_with_cloud_permission_freezes_green(
        client, tmp_path, monkeypatch, declared_test_layout):
    # Раскладка объявлена явно: с шага 2 проекта «Запись о прогоне» гейт
    # «Новый прогон» не создаёт прогон при допуске, разрешающем облако, и
    # необъявленной раскладке моделей (роль «зрение» фикстура не
    # объявляет — канал зрения судится по-прежнему).
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: False)
    # Признак комплекта (A12): облако для сканов законно только в
    # пределах допуска комплекта — синтетический класс даёт «any».
    resp = client.post(
        "/api/runs?cloud_images=true&data_class=synthetic", files=[
            ("files", ("SKAN.png", _png_bytes(tmp_path), "image/png"))])
    assert resp.status_code == 200, resp.text
    got = resp.json()
    assert got["state"] == "заморожено"
    assert got["freeze_problems"] == []
    assert got["freeze_counters"]["files"] == 1
    assert got["freeze_counters"]["clauses"] == 2
    # Явное разрешение — канал строился облачным классом в допуске
    # комплекта.
    assert client.vision_calls == [{"scope": "any",
                                    "requested": "cloud"}]


def test_upload_image_prefers_local_vision_when_available(
        client, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_available",
                        lambda: True)
    resp = client.post("/api/runs", files=[
        ("files", ("SKAN.jpeg", _png_bytes(tmp_path), "image/jpeg"))])
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "заморожено"
    # Строжайший доступный класс: локальный, облако не запрашивалось;
    # допуск — по признаку комплекта (без признака — local-only).
    assert client.vision_calls == [{"scope": "local-only",
                                    "requested": "local"}]


# ---------------------------------------------------------------------------
# Дисциплина контура зрительного канала (без сети)
# ---------------------------------------------------------------------------

def test_vision_channel_local_only_without_local_vision_refuses(
        monkeypatch):
    monkeypatch.setattr(llm_mod, "local_vision_model", lambda: None)
    with pytest.raises(llm_mod.LLMCallError) as e:
        llm_mod.vision_channel(scope="local-only")
    assert ("распознавание изображений локальной моделью недоступно"
            in str(e.value))


def test_vision_channel_local_only_forbids_cloud(monkeypatch):
    from webapp.llm_backend import ScopeViolation
    with pytest.raises(ScopeViolation):
        llm_mod.vision_channel(scope="local-only", requested="cloud")


def test_vision_role_overrides_settings(monkeypatch, tmp_path):
    """Полная независимость ролей (2.09): модель зрения берётся из
    роли «vision» раскладки (площадочный дефолт при заморозке),
    ключ настроек cloud_vision_model — лишь умолчание."""
    from cdpm import providers as prov
    from cdpm import webui
    from webapp import llm_backend
    default = tmp_path / "agents-default.json"
    default.write_text(json.dumps({"vision": {"channels": {
        "cloud": {"provider": "cloud", "model": "claude-fable-5"}}}}),
        encoding="utf-8")
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", str(default))
    run = tmp_path / "run"
    run.mkdir()
    assert webui._vision_role_models(run) == ("claude-fable-5", None)
    calls: list = []
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "claude")
    monkeypatch.setattr(prov, "provider_key", lambda name: "sk-test")
    monkeypatch.setattr(prov, "load_settings",
                        lambda: {"cloud_vision_model": "claude-opus-5"})
    monkeypatch.setattr(
        llm_mod, "_api_vision",
        lambda prompt, path, key, model, t: calls.append(model) or "т")
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    llm_mod.vision_channel(scope="any", requested="cloud",
                           cloud_model="claude-fable-5")("п", img)
    assert calls == ["claude-fable-5"]


def test_vision_channel_prefers_owner_layout_over_cli(monkeypatch,
                                                      tmp_path):
    """Блок 1 обратной задачи H0 (Тест, 2.09): при заданной модели
    зрения (раскладка владельца) и ключе API облачное зрение идёт
    Messages API этой моделью, даже если Claude CLI найден на машине
    (CLI не залогинен у процесса панели — ронял заморозку при живом
    ключе). Без раскладки — прежний порядок: CLI первым."""
    from cdpm import providers as prov
    from webapp import llm_backend
    calls: list = []
    monkeypatch.setattr(llm_backend, "resolve_claude_cli",
                        lambda: "claude")
    monkeypatch.setattr(prov, "provider_key", lambda name: "sk-test")
    monkeypatch.setattr(prov, "load_settings",
                        lambda: {"cloud_vision_model": "claude-opus-5"})
    monkeypatch.setattr(
        llm_mod, "_api_vision",
        lambda prompt, path, key, model, t: calls.append(
            ("api", model)) or "текст")
    monkeypatch.setattr(
        llm_mod, "_cli_vision",
        lambda prompt, path, t: calls.append(("cli", None)) or "текст")
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    llm_mod.vision_channel(scope="any", requested="cloud")("п", img)
    assert calls == [("api", "claude-opus-5")]
    # РВ-140 п. 3: без раскладки — отказ словами (ни CLI со своей
    # моделью, ни «новейшей из перечня ключа»); с моделью без ключа —
    # тоже отказ: CLI заданную модель не исполняет, подмены нет.
    calls.clear()
    monkeypatch.setattr(prov, "load_settings", lambda: {})
    with pytest.raises(llm_mod.LLMCallError) as e:
        llm_mod.vision_channel(scope="any", requested="cloud")
    assert "модель не задана" in str(e.value)
    monkeypatch.setattr(prov, "load_settings",
                        lambda: {"cloud_vision_model": "claude-opus-5"})
    monkeypatch.setattr(prov, "provider_key", lambda name: None)
    with pytest.raises(llm_mod.LLMCallError) as e:
        llm_mod.vision_channel(scope="any", requested="cloud")
    assert "ключа API облака нет" in str(e.value)
    assert calls == []
