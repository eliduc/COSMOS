"""Реестр точек вызова ИИ (строгий режим, §8-бис документа формализации):
корректность записей, кодовый сторож (точка вызова модели без записи в
реестре роняет тесты) и админ-API."""
import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import llm_call_registry as reg
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"

WEBAPP = Path(reg.__file__).resolve().parent

# The two egress primitives every model call in the codebase goes through:
# the pluggable backend (`llm_backend.complete(...)`) and the direct cloud
# CLI handle (`claude_cli()`). A new primitive means updating this scan.
_EGRESS = re.compile(r"llm_backend\.complete\(|claude_cli\(\)")


def _modules_with_llm_calls() -> set[str]:
    found = set()
    for p in sorted(WEBAPP.glob("*.py")):
        if p.name in reg.PLUMBING_MODULES or p.name == "llm_call_registry.py":
            continue
        if _EGRESS.search(p.read_text(encoding="utf-8")):
            found.add(p.name)
    return found


def test_entries_well_formed():
    assert reg.CALL_SITES, "реестр не может быть пустым"
    ids = [c["id"] for c in reg.CALL_SITES]
    assert len(ids) == len(set(ids)), "дубликаты id в реестре"
    for c in reg.CALL_SITES:
        assert c["routing"] in reg.ROUTINGS, c["id"]
        for field in ("module", "function", "backends", "data", "purpose"):
            assert c.get(field), (c["id"], field)


def test_every_call_site_is_declared():
    """The registry cannot rot: modules that call a model must be declared,
    and declared modules must still call one."""
    found = _modules_with_llm_calls()
    declared = reg.declared_modules()
    missing = sorted(found - declared)
    stale = sorted(declared - found)
    assert not missing, (
        f"модули зовут модель, но не описаны в реестре точек ИИ: {missing} "
        "— добавьте запись в webapp/llm_call_registry.py")
    assert not stale, (
        f"в реестре описаны модули без вызовов модели: {stale} — "
        "удалите устаревшие записи")


def test_root_adapters_are_model_free():
    """Адаптеры импорта в корне репозитория запускаются веб-приложением
    как subprocess и обрабатывают самый чувствительный вход системы —
    но лежат вне webapp/ и потому вне скана выше (ревью 2026-07-24).
    Их контракт жёстче: НОЛЬ обращений к моделям — ни через примитивы
    выхода, ни импортом llm_backend, ни прямыми сетевыми клиентами."""
    repo = WEBAPP.parent
    app_src = (WEBAPP / "app.py").read_text(encoding="utf-8")
    adapters = re.findall(
        r'^[A-Z_]*ADAPTER\s*=\s*REPO_ROOT\s*/\s*"([^"]+)"', app_src,
        flags=re.MULTILINE)
    assert adapters, "в app.py не найдено ни одного *_ADAPTER — скан сломан"
    forbidden = re.compile(
        r"llm_backend|claude_cli|import httpx|import requests"
        r"|urllib\.request|http\.client")
    # Рамка импорта правил подключается не как *_ADAPTER, а строкой
    # `import _rules_import` (ревью 2026-07-24) — контракт тот же:
    # ноль модельного/сетевого выхода во всей цепочке импорта.
    extra = ["_rules_import.py", "_rule_templates.py",
             "_solver_to_payload.py", "_payload_to_solver.py",
             "_vendor_rules_check.py"]
    for name in adapters + extra:
        src = (repo / name).read_text(encoding="utf-8")
        hit = forbidden.search(src)
        assert hit is None, (
            f"{name}: адаптер обязан быть свободен от модельного/сетевого "
            f"выхода, найдено {hit.group(0)!r}")


def test_stage2_runner_talks_only_to_local_solver():
    """Расчётный конвейер (этап 2) ходит по сети ТОЛЬКО к собственной
    машине решателя на этом хосте: ни модельного выхода, ни внешних
    адресов. Проверка добавлена перепроверкой 2026-07-24 — на данных
    заказчика весь расчёт обязан оставаться локальным."""
    src = (WEBAPP.parent / "_gerad_stage2_roster.py").read_text(
        encoding="utf-8")
    model_out = re.compile(r"llm_backend|claude_cli|anthropic|openai")
    hit = model_out.search(src)
    assert hit is None, f"модельный выход в этапе 2: {hit.group(0)!r}"
    urls = re.findall(r'["\'](https?://[^"\']+)', src)
    external = [u for u in urls
                if not re.match(r"https?://(127\.0\.0\.1|localhost)[:/]",
                                u)]
    assert not external, (
        f"этап 2 обращается к внешним адресам: {external}")
    assert urls, "скан сломан: в этапе 2 не найдено ни одного адреса"


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_admin_only(client, monkeypatch):
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/llm-registry").status_code == 403


def test_api_returns_registry(client):
    r = client.get("/api/llm-registry")
    assert r.status_code == 200
    body = r.json()
    assert body["routings"] == list(reg.ROUTINGS)
    assert len(body["items"]) == len(reg.CALL_SITES)
    imp = next(i for i in body["items"] if i["id"] == "cr.implementer")
    assert imp["routing"] == "external-only"
