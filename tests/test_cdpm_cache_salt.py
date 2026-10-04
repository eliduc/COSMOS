# -*- coding: utf-8 -*-
"""Соль ключей кэша по отпечатку канала (аудит 04.09, пункт A11).

Охраняемый контракт: ответ модели — функция И запроса, И канала, его
давшего. Ключи кэшей стадий (кандидаты извлечения, противоречия,
второе чтение, целевой проход логики, аудит цикла) включают отпечаток
канала — провайдер, модель после подстановки умолчания, усилие, класс
политики файла, конфигурация действующей роли. Смена модели, смена
политики файла «облако → локально» и смена модели второго читателя
не отдают ответы прежнего канала. Второй замок — отпечаток раскладки
в extract-cache/meta.json: расхождение сносит кэш целиком.
Живых вызовов моделей тесты не делают."""
import json

import pytest

from cdpm import extract as ex_mod
from cdpm import llm as llm_mod
from cdpm import providers as prov

from test_cdpm_extract import fake_llm as _fake  # noqa: E402


def _counting(identity: dict | None):
    """Подменный канал с отпечатком: считает вызовы."""
    def fn(prompt):
        fn.calls += 1
        return _fake(prompt)
    fn.calls = 0
    if identity is not None:
        llm_mod.stamp_identity(fn, **identity)
    return fn


@pytest.fixture()
def kit(tmp_path):
    from test_cdpm_extract import _RULES_A, _RULES_B
    from cdpm import freeze
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit_dir = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit_dir)
    return kit_dir


# ---------------------------------------------------------------------------
# Отпечаток канала
# ---------------------------------------------------------------------------

def test_stamp_identity_merges_fields_and_is_readable():
    fn = llm_mod.stamp_identity(lambda p: "x", provider="cloud",
                                model="claude-a")
    fn = llm_mod.stamp_identity(fn, role="extractor", klass="cloud")
    got = json.loads(llm_mod.channel_identity(fn))
    assert got == {"provider": "cloud", "model": "claude-a",
                   "role": "extractor", "klass": "cloud"}
    # Канал без отпечатка — пустая строка, не исключение.
    assert llm_mod.channel_identity(lambda p: "x") == ""


def test_role_channel_identity_names_model_and_policy_class(tmp_path,
                                                            monkeypatch):
    """Продуктовый путь: канал роли несёт провайдера, модель, усилие,
    класс политики файла и конфигурацию действующей роли; смена
    модели и смена класса политики меняют отпечаток."""
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    run = tmp_path / "run"
    run.mkdir()
    layout = {"extractor": {"channels": {
        "cloud": {"provider": "cloud", "model": "claude-a",
                  "effort": "high"},
        "local": {"provider": "local", "model": "qwen-local"}}}}
    (run / "agents.json").write_text(json.dumps(layout), encoding="utf-8")
    cloud = json.loads(llm_mod.channel_identity(
        prov.role_channel("any", run, "extractor", klass="cloud")))
    assert cloud["provider"] == "cloud"
    assert cloud["model"] == "claude-a"
    assert cloud["effort"] == "high"
    assert cloud["role"] == "extractor" and cloud["klass"] == "cloud"
    assert cloud["role_config"] == layout["extractor"]
    local = json.loads(llm_mod.channel_identity(
        prov.role_channel("any", run, "extractor", klass="local")))
    assert local["provider"] == "local" and local["model"] == "qwen-local"
    assert local["klass"] == "local"
    assert local != cloud
    # Смена модели слота — иной отпечаток при той же роли и классе.
    layout["extractor"]["channels"]["cloud"]["model"] = "claude-b"
    (run / "agents.json").write_text(json.dumps(layout), encoding="utf-8")
    changed = json.loads(llm_mod.channel_identity(
        prov.role_channel("any", run, "extractor", klass="cloud")))
    assert changed["model"] == "claude-b" and changed != cloud
    # Ключей в отпечатке нет.
    assert "key" not in json.dumps(cloud).lower()


def test_cloud_channel_without_model_names_default_substitution(
        monkeypatch, tmp_path):
    """Модель в отпечатке — ПОСЛЕ подстановки умолчания cosmos_llm:
    модель площадки и с ключом (API), и без ключа (командная строка);
    отпечатки различаются транспортом. Без модели — отказ (РВ-140 п. 3)."""
    from webapp import llm_backend as _lb
    monkeypatch.setattr(prov, "provider_key", lambda name: "sk-test")
    monkeypatch.setattr(prov, "load_settings",
                        lambda: {"cloud_default_model": "claude-default-x"})
    monkeypatch.setattr(_lb._settings, "has_api_key", lambda: True)
    with_key = json.loads(llm_mod.channel_identity(
        llm_mod.cosmos_llm("any", requested="cloud")))
    assert with_key["model"] == "claude-default-x"
    monkeypatch.setattr(prov, "provider_key", lambda name: None)
    monkeypatch.setattr(_lb._settings, "has_api_key", lambda: False)
    no_key = json.loads(llm_mod.channel_identity(
        llm_mod.cosmos_llm("any", requested="cloud")))
    assert no_key["model"] == "claude-default-x"
    assert no_key["transport"] == "cli" and no_key != with_key
    monkeypatch.setattr(prov, "load_settings", lambda: {})
    with pytest.raises(llm_mod.LLMCallError):
        llm_mod.cosmos_llm("any", requested="cloud")


# ---------------------------------------------------------------------------
# Кэш извлечения: кандидаты, противоречия, второе чтение
# ---------------------------------------------------------------------------

def test_changing_model_changes_candidate_and_contradiction_keys(kit,
                                                                 tmp_path):
    """Смена модели канала извлечения — ключи кандидатов И
    противоречий иные: повтор идёт в канал, не из кэша."""
    cache = tmp_path / "cache"
    a = _counting({"provider": "cloud", "model": "claude-a"})
    ex_mod.run_extract(kit, tmp_path / "out1", a, "Полярис (тест)",
                       cache_dir=cache)
    assert a.calls > 0
    names_a = sorted(p.name for p in cache.glob("*.json"))
    assert any(n.startswith("contra-") for n in names_a)
    # Тот же канал — из кэша.
    a2 = _counting({"provider": "cloud", "model": "claude-a"})
    ex_mod.run_extract(kit, tmp_path / "out2", a2, "Полярис (тест)",
                       cache_dir=cache)
    assert a2.calls == 0, "тот же канал обязан идти из кэша"
    # Иная модель — ни один ключ не совпал, все чанки спрошены заново.
    b = _counting({"provider": "cloud", "model": "claude-b"})
    ex_mod.run_extract(kit, tmp_path / "out3", b, "Полярис (тест)",
                       cache_dir=cache)
    assert b.calls == a.calls, \
        "смена модели обязана обойти кэш прежней модели целиком"
    names_b = sorted(p.name for p in cache.glob("*.json"))
    assert len(names_b) == 2 * len(names_a)
    assert set(names_a) < set(names_b)


def test_file_policy_cloud_to_local_changes_key(kit, tmp_path):
    """Политика файла «облако → локально»: канал файла иной (класс
    политики в отпечатке) — кэшированные ответы облачной модели не
    отдаются."""
    entries = ex_mod._load_kit(kit)
    sids = [e["sid"] for e in entries]
    cache = tmp_path / "cache"
    cloud = _counting({"provider": "cloud", "model": "claude-a",
                       "klass": "cloud"})
    ex_mod.run_extract(kit, tmp_path / "out1", cloud, "Полярис (тест)",
                       llm_by_sid={s: cloud for s in sids},
                       cross_llm=cloud, cache_dir=cache)
    assert cloud.calls > 0
    local = _counting({"provider": "local", "model": "qwen",
                       "klass": "local"})
    cloud2 = _counting({"provider": "cloud", "model": "claude-a",
                        "klass": "cloud"})
    # Первый файл переведён на «только локально»; перекрёстная стадия
    # — строжайший класс, тоже локальный.
    by_sid = {s: (local if s == sids[0] else cloud2) for s in sids}
    ex_mod.run_extract(kit, tmp_path / "out2", cloud2, "Полярис (тест)",
                       llm_by_sid=by_sid, cross_llm=local,
                       cache_dir=cache)
    assert local.calls > 0, \
        "чанки файла с новой политикой обязаны идти новым каналом"
    assert cloud2.calls == 0, \
        "файл с прежней политикой — из кэша прежнего канала"


def test_second_reader_key_follows_its_own_channel(kit, tmp_path):
    """Второе чтение — отпечаток СВОЕГО канала: смена модели второго
    читателя обходит кэш второго чтения, кэш первого остаётся."""
    cache = tmp_path / "cache"
    first = _counting({"provider": "cloud", "model": "claude-a"})
    second = _counting({"provider": "gemini", "model": "gemini-a"})
    ex_mod.run_extract(kit, tmp_path / "out1", first, "Полярис (тест)",
                       cache_dir=cache, second_llm=second)
    assert first.calls > 0 and second.calls > 0
    first2 = _counting({"provider": "cloud", "model": "claude-a"})
    second2 = _counting({"provider": "gemini", "model": "gemini-b"})
    ex_mod.run_extract(kit, tmp_path / "out2", first2, "Полярис (тест)",
                       cache_dir=cache, second_llm=second2)
    assert first2.calls == 0, "первое чтение — из кэша"
    assert second2.calls == second.calls, \
        "второй читатель с иной моделью обязан читать заново"


# ---------------------------------------------------------------------------
# Целевой проход логики и аудит цикла
# ---------------------------------------------------------------------------

def test_logic_pass_key_includes_channel_identity(tmp_path):
    from test_cdpm_logic_pass import _CLEAN, _doc, _write
    out = _write(tmp_path, _doc())

    def _llm(identity):
        def fn(prompt):
            fn.calls += 1
            return json.dumps({"logic": _CLEAN}, ensure_ascii=False)
        fn.calls = 0
        return llm_mod.stamp_identity(fn, **identity)
    cache = tmp_path / "cache"
    a = _llm({"model": "claude-a"})
    ex_mod.produce_logic(out, a, cache_dir=cache)
    assert a.calls > 0
    # Свежая копия модели без logic — иначе целей не останется.
    out2 = _write(tmp_path / "second", _doc())
    b = _llm({"model": "claude-b"})
    ex_mod.produce_logic(out2, b, cache_dir=cache)
    assert b.calls == a.calls, "иная модель обязана обойти кэш прохода"


def test_audit_round_key_includes_channel_identity(tmp_path):
    from cdpm import exchange as ex
    from cdpm import model as m
    from test_cdpm_exchange_run import _REMARK_DISTORT
    from test_cdpm_extract import _RULES_A, _RULES_B
    from cdpm import freeze
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)
    out = tmp_path / "out"
    ex_mod.run_extract(kit, out, _fake, "Полярис (тест)")
    kit_entries = ex_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")

    def _auditor(identity):
        def fn(prompt):
            fn.calls += 1
            if "Клаузы документа S1" in prompt:
                return json.dumps({"remarks": [dict(_REMARK_DISTORT)]},
                                  ensure_ascii=False)
            return json.dumps({"remarks": []}, ensure_ascii=False)
        fn.calls = 0
        return llm_mod.stamp_identity(fn, **identity)
    acache = tmp_path / "audit-cache"
    a = _auditor({"model": "claude-a"})
    ex._audit_round(a, doc, kit_entries, 1, cache_dir=acache)
    assert a.calls > 0
    b = _auditor({"model": "claude-b"})
    ex._audit_round(b, doc, kit_entries, 1, cache_dir=acache)
    assert b.calls == a.calls, "иная модель аудитора обязана обойти кэш"


# ---------------------------------------------------------------------------
# Второй замок: отпечаток раскладки в extract-cache/meta.json
# ---------------------------------------------------------------------------

def test_extract_cache_meta_resets_on_layout_change(tmp_path, monkeypatch):
    from cdpm import webui
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    run = tmp_path / "run"
    run.mkdir()
    cache = run / "extract-cache"
    cache.mkdir()
    (cache / "S1-000-abc.json").write_text("{}", encoding="utf-8")
    fp1 = webui._extract_cache_fingerprint(run, "any", {})
    # Кэш прежней версии (без meta.json) — сносится: отпечатка нет.
    note = webui._reset_stale_extract_cache(run, fp1)
    assert note and "сброшен" in note
    assert not (cache / "S1-000-abc.json").exists()
    assert json.loads((cache / "meta.json").read_text(
        encoding="utf-8"))["fingerprint"] == fp1
    # Тот же отпечаток — кэш остаётся.
    (cache / "S1-000-abc.json").write_text("{}", encoding="utf-8")
    assert webui._reset_stale_extract_cache(run, fp1) is None
    assert (cache / "S1-000-abc.json").exists()
    # Смена раскладки ролей — иной отпечаток, кэш сброшен.
    (run / "agents.json").write_text(json.dumps({"extractor": {"channels": {
        "cloud": {"provider": "cloud", "model": "claude-b"}}}}),
        encoding="utf-8")
    fp2 = webui._extract_cache_fingerprint(run, "any", {})
    assert fp2 != fp1
    note = webui._reset_stale_extract_cache(run, fp2)
    assert note and "изменилась раскладка/настройки моделей" in note
    assert not (cache / "S1-000-abc.json").exists()
    # Смена политики файла и допуска — тоже иной отпечаток.
    assert webui._extract_cache_fingerprint(
        run, "any", {"A.md": {"processing": "local"}}) != fp2
    assert webui._extract_cache_fingerprint(run, "local-only", {}) != fp2


def test_respond_round_key_includes_channel_identity(tmp_path):
    """Кэш ответов обработчика внутри раунда: имя чанка несёт хэш
    отпечатка канала — иная модель обработчика обходит кэш; тот же
    канал читает из кэша (положительный контроль)."""
    from cdpm import exchange as ex
    from cdpm import model as m
    from test_cdpm_exchange_run import (_REMARK_DISTORT, _PATCH_FIX,
                                        _make_worker)
    from test_cdpm_extract import _RULES_A, _RULES_B
    from cdpm import freeze
    src = tmp_path / "src"
    src.mkdir()
    (src / "RULES-A.md").write_text(_RULES_A, encoding="utf-8")
    (src / "RULES-B.md").write_text(_RULES_B, encoding="utf-8")
    kit = tmp_path / "kit"
    freeze.freeze([src / "RULES-A.md", src / "RULES-B.md"], kit)
    out = tmp_path / "out"
    ex_mod.run_extract(kit, out, _fake, "Полярис (тест)")
    kit_entries = ex_mod._load_kit(kit)
    doc = m.load_model(out / "model.json")

    def auditor(prompt):
        if "Клаузы документа S1" in prompt:
            return json.dumps({"remarks": [dict(_REMARK_DISTORT)]},
                              ensure_ascii=False)
        return json.dumps({"remarks": []}, ensure_ascii=False)
    pack, _rej = ex._audit_round(auditor, doc, kit_entries, 1)
    assert pack.get("remarks"), "аудит обязан дать замечание"
    clause_by_key = {(e["sid"], c["anchor"]): c["text"]
                     for e in kit_entries for c in e["clauses"]}
    plain = _make_worker({
        "Потеряно уточнение": {"verdict": "ПРИНЯТО",
                               "basis": "цитата: «на базе» — принято",
                               "change": "строка уточнена",
                               "patch": _PATCH_FIX}})

    def _worker(identity):
        def fn(prompt):
            fn.calls += 1
            return plain(prompt)
        fn.calls = 0
        return llm_mod.stamp_identity(fn, **identity)
    rcache = tmp_path / "respond-cache"
    a = _worker({"model": "claude-a"})
    resp_a = ex._respond_round(a, doc, pack, clause_by_key,
                               cache_dir=rcache)
    assert a.calls > 0
    a2 = _worker({"model": "claude-a"})
    assert ex._respond_round(a2, doc, pack, clause_by_key,
                             cache_dir=rcache) == resp_a
    assert a2.calls == 0, "тот же канал обязан читать ответы из кэша"
    b = _worker({"model": "claude-b"})
    ex._respond_round(b, doc, pack, clause_by_key, cache_dir=rcache)
    assert b.calls == a.calls, "иная модель обработчика обязана обойти кэш"
    suffixes = {p.name.rsplit("-", 1)[1] for p in rcache.glob("chunk-*.json")}
    assert len(suffixes) == 2, "два канала — два набора чанков в кэше"
