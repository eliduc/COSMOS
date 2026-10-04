"""What-if scenarios (2026-07-15, gap item #11).

A scenario is an ISOLATED clone of an instance: its own payload copy under a
new label (=> its own artifacts: catalogs, journals, results never touch the
base), its own tier-1 rule set (fed to every stage of ITS runs via a
per-scenario CREW_RULES_CONFIG file) and optional cost-rate overrides (used
by costing/KPI). This is the industrial what-if pattern (Jeppesen elastic
what-if, NetLine parallel scenario evaluation) built on our existing pieces:
the clone is a first-class instance, so the regular pipeline, results,
costing and KPIs all work on it unchanged; comparison is then an honest
side-by-side of two instances plus a rules/cost diff.

Registry: webapp/config/scenarios.json  {scenario_inst_id: meta}
Rules:    webapp/config/scenario-rules/<scenario_inst_id>.json
          (same {"parameters": {...}} layout as airline_default.json —
          rules_config.py accepts it as-is).
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime

import rules_config

import _data_paths
from . import costing, kpi, registry

SCENARIOS_PATH = registry.REPO_ROOT / "webapp" / "config" / "scenarios.json"
# Каталог сценарных правил и файл установки объявлены ОДИН раз — в
# config_scope (P1-10, 2026-07-26): пока объявлений было несколько, тестовая
# изоляция чинила каждое по отдельности, и одно всегда отставало.


def _rules_dir():
    from . import config_scope
    return config_scope.scenario_dir(config_scope.DOMAIN_RULES)


_lock = threading.Lock()

_COST_OVERRIDE_FIELDS = ("block_hour_rate", "per_diem_hour",
                         "hotel_default_rate")


def effective_global_rules(base_inst=None) -> dict:
    """Правила, под которыми РЕАЛЬНО считается основа сценария.

    Аудит 2026-07-15: `compare()`/`create` обязаны отсчитывать от ЭТОГО, а
    не от `rules_config.DEFAULTS` — иначе после первой же правки правил
    администратором разница выдумывает несуществующие изменения, а
    сценарий, созданный через API с частичным набором правил, считается по
    другим правилам, чем его основа.

    P1-10 (2026-07-26): с приходом уровня экземпляра «правила основы» —
    это правила КОНКРЕТНОГО набора, а не установки. Без основы (вызов без
    аргумента) отвечаем правилами установки, как и прежде."""
    from . import config_scope
    try:
        return config_scope.effective(config_scope.DOMAIN_RULES, base_inst)
    except ValueError:
        # битый файл: писатель с ним тоже не смог бы — отдаём встроенные,
        # чтобы разница была видна, а не подменялась пятисоткой
        return dict(rules_config.DEFAULTS)


class ScenarioError(ValueError):
    """User-facing scenario failure."""


def _load() -> dict:
    try:
        raw = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_locked(data: dict) -> None:
    SCENARIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCENARIOS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),  # census-ok: журнал модуля, не набор
                   encoding="utf-8")
    tmp.replace(SCENARIOS_PATH)


def meta_for(inst_id: str) -> dict | None:
    return _load().get(inst_id)


def rules_path_for(inst_id: str):
    """Per-scenario rules file for jobs' CREW_RULES_CONFIG, or None.

    Выбор уровня целиком у `config_scope`; здесь остаётся только правило
    «осиротевший файл без записи в реестре сценариев власти не имеет»."""
    from . import config_scope
    return config_scope.scenario_path(config_scope.DOMAIN_RULES, inst_id)


def cost_overrides_for(inst_id: str) -> dict:
    meta = meta_for(inst_id)
    if not meta:
        return {}
    ov = meta.get("cost_overrides") or {}
    return {k: ov[k] for k in _COST_OVERRIDE_FIELDS if k in ov}


def validate_rules(rules: dict) -> list[str]:
    """Валидация сценарных правил ТОЙ ЖЕ семантикой, что и загрузчик
    (rules_config.validate_value) — ревью 2026-07-19: собственная проверка
    «положительное число» пропускала недопустимые comp-значения (сценарий
    создавался, а каждый его запуск падал на старте движков) и отвергала
    легальный 0 (= правило выключено)."""
    errors = []
    if not isinstance(rules, dict):
        return ["'rules' must be an object"]
    for k, v in rules.items():
        if k not in rules_config.DEFAULTS:
            errors.append(f"unknown rule parameter '{k}'")
            continue
        err = rules_config.validate_value(k, v)
        if err:
            errors.append(f"rule {err}")
    return errors


def _purge_label_artifacts(label: str) -> None:
    """Best-effort removal of every stage artifact keyed by a scenario's
    label. Labels are unique per creation (see create_scenario), so this
    can never touch another live instance's files — it is pure disk
    hygiene for throwaway what-ifs."""
    fr = registry.FINAL_REPORT
    bd = registry.BEST_DIR
    paths = [
        bd / f"meta_{label}.json",
        bd / f"gerad_best_catalog_{label}.json",
        bd / f"gerad_best_pool_{label}.json.gz",
        fr / f"gerad_colgen_selected_{label}.json",
        fr / f"gerad_pool_{label}.json.gz",
        fr / "stage1" / f"{label}.json",
        fr / f"gerad_stage2_assignment_{label}.json",
        _data_paths.gerad_data_dir()
        / f"p2_two_stage_solution_{label}.json",
        # P0-1 (ревью 2026-07-20): указатель активного ростера сценария
        fr / f"active_roster_{label}.json",
    ]
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # P0-1: манифесты прогонов и диагностические копии метки сценария
    import re as _re
    import shutil as _sh
    _sh.rmtree(fr / "runs" / "roster-manifests" / label, ignore_errors=True)
    _diag = fr / "runs" / "stage2-diagnostics"
    _tail = _re.compile(r"^\d{8}-\d{6}-(assignment|solution)\.json$")
    try:
        for p in _diag.glob(f"{label}-*"):
            if _tail.match(p.name[len(label) + 1:]):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def create_scenario(email: str, name: str, base_inst: dict,
                    rules: dict, cost_overrides: dict | None = None) -> dict:
    """Clone base_inst's payload into a new scenario instance owned by
    `email`, write its rule file, register it. Returns the new instance id."""
    if cost_overrides is not None and not isinstance(cost_overrides, dict):
        raise ScenarioError("'cost_overrides' must be an object")
    if rules is not None and not isinstance(rules, dict):
        raise ScenarioError("'rules' must be an object")
    # slugify_or_hash: Cyrillic names transliterate, any other script gets a
    # stable digest — scenario creation must work in every UI language. A
    # short random suffix makes the LABEL unique per creation, so a
    # delete-then-recreate (even same name, different base) can never alias
    # the prior run's label-keyed artifacts/catalog/gate (audit 2026-07-15).
    slug = f"scenario-{registry.slugify_or_hash(name)}-{secrets.token_hex(3)}"
    inst_id, _label, target = registry.upload_target(email, slug)
    if registry.get_instance(inst_id) is not None:
        raise ScenarioError(f"id '{inst_id}' already exists")
    errs = validate_rules(rules or {})
    if cost_overrides:
        errs += costing.validate_cost_config(
            {k: v for k, v in cost_overrides.items()
             if k in _COST_OVERRIDE_FIELDS})
    if errs:
        raise ScenarioError("; ".join(errs))

    payload = registry.load_payload(registry.instance_payload_path(base_inst))
    registry.write_payload(target, payload, "scenario_clone")
    registry.set_owner(inst_id, email)
    # scenarios inherit the base dataset's L/C attribute (same data!)
    registry.set_llm_scope(inst_id, base_inst.get("llm_scope", "local-only"))

    # Baseline the full rule set on the rules THE BASE actually runs under,
    # not the code defaults — so a partial scenario runs the same rules as
    # its base except where explicitly overridden (audit 2026-07-15; с
    # P1-10 основой служат правила самого набора, а не установки).
    full_rules = effective_global_rules(base_inst)
    full_rules.update({k: (rules or {})[k] for k in (rules or {})
                       if k in full_rules})
    _rules_dir().mkdir(parents=True, exist_ok=True)
    (_rules_dir() / f"{inst_id}.json").write_text(
        json.dumps({"parameters": full_rules}, indent=1), encoding="utf-8")

    meta = {
        "name": str(name)[:120],
        "base_instance": base_inst["id"],
        "owner": email,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rules": full_rules,
        "cost_overrides": {k: v for k, v in (cost_overrides or {}).items()
                           if k in _COST_OVERRIDE_FIELDS},
    }
    with _lock:
        data = _load()
        data[inst_id] = meta
        _save_locked(data)
    return {"id": inst_id, **meta}


def delete_scenario(inst_id: str, email: str, is_admin: bool) -> bool:
    """Owner-or-admin removal: payload + ownership + scope + rules +
    registry row + the scenario's own label-keyed stage artifacts. False
    when not found / not yours."""
    meta = meta_for(inst_id)
    if meta is None:
        return False
    if not is_admin and meta.get("owner") != email:
        return False
    inst = registry.get_instance(inst_id)
    if inst is not None:
        try:
            registry.instance_payload_path(inst).unlink(missing_ok=True)
        except OSError:
            pass
        _purge_label_artifacts(inst["label"])
    registry.remove_owner(inst_id)
    registry.remove_llm_scope(inst_id)
    (_rules_dir() / f"{inst_id}.json").unlink(missing_ok=True)
    with _lock:
        data = _load()
        data.pop(inst_id, None)
        _save_locked(data)
    return True


def root_base_instance(inst_id: str) -> dict | None:
    """Walk the base chain to the first NON-scenario instance (a scenario
    can be based on another scenario). Returns the root instance dict, or
    None if the chain breaks. Used so a scenario-of-a-GERAD-scenario still
    runs the anchored GERAD lane (audit 2026-07-15)."""
    seen = set()
    cur = inst_id
    while cur and cur not in seen:
        seen.add(cur)
        m = meta_for(cur)
        if m is None:
            return registry.get_instance(cur)
        cur = m.get("base_instance")
    return None


def list_scenarios(user: dict) -> list[dict]:
    """Scenario records visible to the user, newest first, annotated with
    live instance data (stage availability, last gate status)."""
    out = []
    for inst_id, meta in _load().items():
        inst = registry.get_instance(inst_id)
        if inst is None or not registry.visible_to(inst, user):
            continue
        out.append({
            "id": inst_id,
            "name": meta.get("name"),
            "base_instance": meta.get("base_instance"),
            "owner": meta.get("owner"),
            "created_at": meta.get("created_at"),
            "rules": meta.get("rules"),
            "cost_overrides": meta.get("cost_overrides") or {},
            "has_catalog": inst.get("catalog_path") is not None,
            "last_gate_status": inst.get("last_gate_status"),
        })
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def _side(inst_id: str) -> dict:
    """One comparison side: rules in effect, stage-1 objective, KPIs."""
    inst = registry.get_instance(inst_id)
    if inst is None:
        return {"instance": inst_id, "missing": True}
    meta = meta_for(inst_id)
    # A non-scenario base runs under ITS OWN effective config, not the code
    # defaults — baseline against it so the diff shows only the scenario's
    # real changes (audit 2026-07-15; P1-10: уровень набора).
    rules = (meta or {}).get("rules") or effective_global_rules(inst)
    k = kpi.instance_kpi_for(inst)     # запись уже разрешена выше
    return {
        "instance": inst_id,
        "missing": False,
        "rules": rules,
        "catalog_objective": inst.get("catalog_objective"),
        "last_gate_status": inst.get("last_gate_status"),
        "kpi": k if k.get("available") else None,
    }


def compare(inst_id: str) -> dict | None:
    """Side-by-side of a scenario against its base + rules/cost diffs."""
    meta = meta_for(inst_id)
    if meta is None:
        return None
    base_id = meta["base_instance"]
    scen = _side(inst_id)
    base = _side(base_id)
    rules_diff = []
    b_rules = base.get("rules") or {}
    s_rules = scen.get("rules") or {}
    # Отсутствующий ключ = значение по умолчанию С ОБЕИХ сторон (ревью
    # 2026-07-19: сценарии, созданные до появления comp_*-ключей, хранят
    # шесть ключей — без дефолта сравнение рисовало три фантомных диффа
    # «base 0 -> scenario null» о правилах, которых сценарий не трогал).
    for key, dflt in rules_config.DEFAULTS.items():
        bv = b_rules.get(key, dflt)
        sv = s_rules.get(key, dflt)
        if bv != sv:
            rules_diff.append({"param": key, "base": bv, "scenario": sv})
    cost_diff = []
    base_cfg = costing.load_cost_config()
    for key, val in (meta.get("cost_overrides") or {}).items():
        if base_cfg.get(key) != val:
            cost_diff.append({"param": key, "base": base_cfg.get(key),
                              "scenario": val})
    return {"scenario": scen, "base": base,
            "name": meta.get("name"),
            "rules_diff": rules_diff, "cost_diff": cost_diff}
