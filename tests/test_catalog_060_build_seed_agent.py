"""B1/L01/L19 and K3: isolated catalogue preparation, not a release or stand.

Only the crew_unavailability record below is transferred from tree
360d3f52ffdf78fb5c901d9f6e50a6ee054ff718/catalog/families.json. The rest
comes from this checkout. modern_docs supplies the existing synthetic 0.7
unit-map fixture: it is not production vocabulary or independent approval.
Kotlin is read as source by the real builder, never executed.
"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import template_catalog as tc
from template_catalog import canon
from template_catalog.validate import schema_path_for_version
from catalog.tools import build_from_code as bfc
from catalog.tools import parity
from vmp import bindings_build as bb
from vmp import bindings_gate as bg
from vmp import catalog_access as ca
from test_catalog_060_versioned_schema import modern_docs

ROOT = Path(__file__).resolve().parents[1]
CREW = "families/crew_unavailability"
AT = "2026-09-17T20:30:00Z"
COMMIT = "49ce4c445c60dcf9220c78123bc6eedf5c4b92f5"
DIRECTIONS = {
    "credit_hours_spread": "minimize",
    "layover_nights_total": "minimize",
    "min_person_days": "minimize",
    "positioning_cost": "minimize",
    "preferred_days_off_violations": "minimize",
    "rank_normalized_cost": "minimize",
    "total_pairing_cost": "minimize",
    "uncovered_seats": "minimize",
}
# Exact single-record data transfer, not a fabricated acceptance.
_CREW_RECORD = json.loads(r'''{
  "acceptance": null,
  "check_templates": [],
  "class": "core",
  "complements": [],
  "conditions": [],
  "executor_kinds": [
    "check_code",
    "kotlin_constraint"
  ],
  "history": [
    {
      "by": "аналитик",
      "date": "2026-09-17",
      "kind": "заявка",
      "note": "заявка req-03139728775952845650: посадка категории C; документ свода правил авиакомпании / прогон CDPM",
      "ref": "req-03139728775952845650"
    },
    {
      "by": "владелец",
      "date": "2026-09-17",
      "kind": "решение владельца",
      "note": "Слово владельца 17.09.2026 («Даю отмашку COSMOS выпустить каталог самому»): заявка принята — семья недоступности членов экипажа для правила VN-027 «Отпуск» набора Н0-C; исполнители уже в коде, новых параметров нет; выпуск каталога 0.6.0",
      "ref": "req-03139728775952845650"
    },
    {
      "by": "аналитик",
      "date": "2026-09-17",
      "kind": "создание",
      "note": "кандидат конвейера по заявке req-03139728775952845650 (ветка demo-h0c-vacation-rule, коммит f372ea320388, привязки сторож привязок по коду после сборки: символы найдены)",
      "ref": "req-03139728775952845650"
    },
    {
      "by": "аналитик",
      "date": "2026-09-17",
      "kind": "изменение",
      "note": "правка записи по закону паритета: природа «расписание» по перечням кода (свидетель — отрицательный), одна стендовая задача положительного контроля",
      "ref": "req-03139728775952845650"
    }
  ],
  "id": "crew_unavailability",
  "kind": "HARD",
  "nature": "schedule",
  "nature_confirmed_by": "none",
  "note": "природа названа по перечням кода: в перечне людских семей сертификатора ростера семьи нет, поэтому «расписание» с отрицательным свидетелем; черновик заявки называл природу «люди» по смыслу (свойство сотрудника) — перенос в людские семьи требует записи в перечень сертификатора, а не каталога",
  "origin": {
    "confirmed_by": "владелец",
    "date": "2026-09-17",
    "kind": "заявка",
    "note": "родилась конвейером шаблонов: честный стоп покрытия → заявка → отмашка владельца → шаблон (правило VN-027 «Отпуск» набора Н0-C: член экипажа не может быть назначен на рейс в дни своего отпуска; исполнители уже в коде — ограничение решателя «Employee unavailable» и проверка сверки unavailability)"
  },
  "param_sets": [],
  "params": [],
  "reads_history": null,
  "rule_form": null,
  "semantics": {
    "measure": "назначение сотрудника на рейс, вылет или прилёт которого приходится на его недоступный день, — недопустимо",
    "scope": "назначения сотрудника на рейсы в дни, названные для него недоступными книгой данных авиакомпании (лист «Отпуска»: табельный номер, с, по)",
    "text": "член экипажа не назначается на рейсы в дни своего отпуска и иных объявленных дней отсутствия; дни берутся из книги данных авиакомпании, параметров у правила нет",
    "unit": "назначение на рейс"
  },
  "stand_tasks": [
    {
      "expected": {
        "kotlin_constraint": {
          "amount": 0,
          "constraint": "Employee unavailable",
          "matches": 0
        }
      },
      "id": "st-unavail-0",
      "note": "мир без недоступных дней: ограничение решателя молчит — положительный контроль; мир с недоступным днём стенд сегодня не строит (лист «Отпуска» пакета стенда пуст по построению), отрицательный контроль остаётся за сверкой решения с ИДАК на данных набора Н0-C",
      "world": {
        "crews": [
          {
            "base": "ST1",
            "id": "P1",
            "rank": "CP",
            "ratings": [
              "T1"
            ]
          }
        ],
        "flights": [
          {
            "arr": "2026-10-01T08:00",
            "dep": "2026-10-01T06:00",
            "fleet_type": "T1",
            "from": "ST1",
            "id": "F1",
            "to": "ST2"
          }
        ],
        "form": "schedule",
        "history": null,
        "horizon": {
          "from": "2026-10-01",
          "to": "2026-10-02"
        },
        "params": {},
        "stand": {
          "nights_per_layover": 2,
          "w_night": 1
        }
      }
    }
  ],
  "status": "есть",
  "subject": "отпуска и дни недоступности членов экипажа",
  "subject_words": [
    "отпуск",
    "отпуска",
    "недоступ",
    "отсутств",
    "больничн",
    "отгул"
  ],
  "title": "недоступность членов экипажа: отпуска и иные дни отсутствия",
  "violation_size": null,
  "violation_unit": null,
  "zone": "ROSTERING"
}''')


def _catalog(tmp_path, *, version="0.5.0", crew=False, direction=None):
    docs = {
        name: json.loads((ROOT / "catalog" / name).read_text(encoding="utf-8"))
        for name in ("catalog.json", "families.json", "criteria.json",
                     "dictionaries.json", "requests.json", "versions.json")
    }
    docs["versions.json"]["versions"] = []
    if version != "0.5.0":
        modern_docs(docs, version)
    if crew:
        docs["families.json"]["families"].append(copy.deepcopy(_CREW_RECORD))
        docs["dictionaries.json"]["lexicon_order"].append("crew_unavailability")
    if direction is not None:
        next(r for r in docs["criteria.json"]["criteria"]
             if r["id"] == "min_person_days")["direction"] = direction
    body = {
        "families": docs["families.json"]["families"],
        "criteria": docs["criteria.json"]["criteria"],
        "dictionaries": {k: v for k, v in docs["dictionaries.json"].items()
                         if k != "form_version"},
    }
    docs["catalog.json"]["fingerprint"] = canon.fingerprint_of(body)
    docs["catalog.json"]["legacy_registry_rev"] = bfc.legacy_registry_rev(
        {r["id"]: sorted(p["name"] for p in r["params"])
         for r in body["families"]})
    folder = tmp_path / "catalog"
    folder.mkdir()
    for name, doc in docs.items():
        (folder / name).write_text(json.dumps(doc, ensure_ascii=False),
                                   encoding="utf-8")
    cat = tc.current(folder, validated=True)
    assert not [p for p in cat.validate() if p.severity == "error"]
    return cat


def _seed(cat):
    return bfc.curated_of(
        cat, hard=bfc.load_seed(),
        order=[r["id"] for r in cat.families_full(status=None)])


def _build(cat, seed):
    return bfc.build(cat.version(), "2026-09-17", cat.header["released_by"],
                     "isolated preparation proof", seed=seed)


def _errors(files):
    return [p for p in bfc.validate_built(files) if p.severity == "error"]


def test_modern_curated_criteria_have_explicit_agreed_directions(tmp_path):
    # P0 §3.3 / existing test_w17b_catalog_060_direction.py: all eight,
    # not a default invented by the builder for an unknown criterion.
    cat = _catalog(tmp_path, version="0.7.0")
    assert {cid: row.get("direction")
            for cid, row in _seed(cat)["criteria"].items()} == DIRECTIONS


@pytest.mark.parametrize("version", ["0.7.0", "0.7.0-draft.1"])
@pytest.mark.parametrize("direction", ["minimize", "maximize"])
def test_modern_reverse_projection_and_build_preserve_declared_direction(
        tmp_path, version, direction):
    cat = _catalog(tmp_path, version=version, direction=direction)
    before = cat.canonical_json()
    seed = _seed(cat)
    assert seed["criteria"]["min_person_days"].get("direction") == direction
    files = _build(cat, seed)
    actual = {r["id"]: r.get("direction")
              for r in json.loads(files["criteria.json"])["criteria"]}
    assert actual == {**DIRECTIONS, "min_person_days": direction}
    assert _errors(files) == []
    assert json.loads(files["schema/catalog.schema.json"]) == json.loads(
        schema_path_for_version(version).read_text(encoding="utf-8"))
    assert cat.canonical_json() == before


def test_legacy_closed_schema_and_missing_direction_remain_legacy(tmp_path):
    cat = _catalog(tmp_path)
    seed = _seed(cat)
    assert all("direction" not in row for row in seed["criteria"].values())
    # The curated future metadata must not force a 0.5 contract upgrade.
    for cid, direction in DIRECTIONS.items():
        seed["criteria"][cid]["direction"] = direction
    files = _build(cat, seed)
    assert all("direction" not in row
               for row in json.loads(files["criteria.json"])["criteria"])
    assert _errors(files) == []
    assert json.loads(files["schema/catalog.schema.json"]) == json.loads(
        schema_path_for_version("0.5.0").read_text(encoding="utf-8"))


def test_l18_checks_the_modern_schema_copy_not_the_legacy_schema(tmp_path):
    cat = _catalog(tmp_path, version="0.7.0")
    files = _build(cat, _seed(cat))
    assert _errors(files) == []
    for name, text in files.items():
        target = cat.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    cat = tc.load(cat.path)
    # Actual refresh upgrades only the isolated catalogue's own seed, not
    # the builtin 0.5 seed and not the catalogue payload or approval status.
    own_seed = cat.path / bfc.SEED_REL
    own_seed.parent.mkdir(parents=True)
    own_seed.write_bytes(bfc.dump_json(bfc.load_seed()).encode("utf-8"))
    assert bfc.main(["--out", str(cat.path), "--refresh"]) == 0
    refreshed = json.loads(own_seed.read_text(encoding="utf-8"))
    assert {cid: row.get("direction")
            for cid, row in refreshed["criteria"].items()} == DIRECTIONS
    assert bfc.main(["--out", str(cat.path), "--refresh", "--check"]) == 0
    judged = SimpleNamespace(path=cat.path, cat=cat)
    actual = parity.rule_l18(judged, bfc.ktab.tables())
    assert actual.ok is True, actual.as_dict()
    # This wrong copy must remain a refusal even when all catalogue data match.
    (cat.path / "schema/catalog.schema.json").write_bytes(
        schema_path_for_version("0.5.0").read_bytes())
    wrong_schema = parity.rule_l18(judged, bfc.ktab.tables())
    assert wrong_schema.ok is False, wrong_schema.as_dict()
    assert "catalog.schema.json" in wrong_schema.words


@pytest.mark.parametrize("bad", [None, "up"])
def test_modern_missing_or_invalid_direction_is_not_defaulted(tmp_path, bad):
    cat = _catalog(tmp_path, version="0.7.0")
    seed = _seed(cat)
    for cid, direction in DIRECTIONS.items():
        seed["criteria"][cid]["direction"] = direction
    if bad is None:
        del seed["criteria"]["min_person_days"]["direction"]
    else:
        seed["criteria"]["min_person_days"]["direction"] = bad
    try:
        files = _build(cat, seed)
    except bfc.BuildError as exc:
        assert "min_person_days" in str(exc) and "direction" in str(exc)
    else:
        problems = _errors(files)
        assert problems, "a modern direction omission must not become minimize"
        assert any("direction" in str(p) for p in problems)


def test_l01_parameterless_family_uses_addition_not_changed_historical_base(tmp_path):
    cat = _catalog(tmp_path, version="0.7.0", crew=True)
    seed = _seed(cat)
    snapshot = json.loads(parity.SNAPSHOT_FILE.read_text(encoding="utf-8"))
    before = copy.deepcopy(snapshot)
    assert "crew_unavailability" not in snapshot["families"]
    assert "crew_unavailability" not in seed["base"]["families"]
    delta = json.loads(_build(cat, seed)["transition/step4_additions.json"])
    assert "crew_unavailability" in delta["families_added"]
    assert not any(v["family"] == "crew_unavailability"
                   for v in delta["params_to_families"].values())
    result = parity.rule_l01(cat, seed["base"], delta, snapshot)
    assert result.ok is True, result.as_dict()
    assert snapshot == before, "historical witness must not be edited"
    without_addition = copy.deepcopy(delta)
    del without_addition["families_added"]["crew_unavailability"]
    negative = parity.rule_l01(cat, seed["base"], without_addition, snapshot)
    assert negative.ok is False and "crew_unavailability" in negative.words


def test_modern_bindings_have_both_real_executors_but_no_stand_claim(tmp_path):
    cat = _catalog(tmp_path, version="0.7.0", crew=True)
    doc = bb.build(cat, commit=COMMIT, generated_at=AT)
    row = next(r for r in doc["bindings"]
               if (r["section"], r["id"]) == ("families", "crew_unavailability"))
    assert {(x["kind"], x["symbol"]) for x in row["executors"]} == {
        ("check_code", "_run_unavailability"),
        ("kotlin_constraint",
         "FlightCrewSchedulingConstraintProvider.employeeUnavailability"),
    }
    assert row["stand_verified"] == []
    for executor in row["executors"]:
        assert bg.resolve(executor) == [], executor
    wrong_symbol = {**row["executors"][0], "symbol": "missing_fixture_symbol"}
    assert bg.resolve(wrong_symbol), "a declared but absent symbol must refuse"
    assert doc["catalog_version"] == "0.7.0"
    assert doc["catalog_fingerprint"] == cat.fingerprint()
    missing = bb.load_seed()
    missing["records"][CREW] = [
        x for x in missing["records"][CREW] if x["kind"] != "check_code"]
    with pytest.raises(bb.BindingsBuildError, match="check_code"):
        bb.build(cat, commit=COMMIT, generated_at=AT, seed=missing)


def test_legacy_pending_record_is_explicit_and_nonpending_omission_refuses(tmp_path):
    cat = _catalog(tmp_path)
    seed = bb.load_seed()
    assert CREW in seed["records"] and CREW in seed["pending_records"]
    doc = bb.build(cat, commit=COMMIT, generated_at=AT, seed=seed)
    assert not any(r["id"] == "crew_unavailability" for r in doc["bindings"])
    assert all(r["stand_verified"] == [] for r in doc["bindings"])
    seed["pending_records"].remove(CREW)
    with pytest.raises(bb.BindingsBuildError, match="crew_unavailability"):
        bb.build(cat, commit=COMMIT, generated_at=AT, seed=seed)


def test_real_prune_pending_cli_changes_only_the_isolated_seed(tmp_path, monkeypatch):
    cat = _catalog(tmp_path, version="0.7.0", crew=True)
    actual_seed_before = bb.SEED_FILE.read_bytes()
    seed = bb.load_seed()
    assert CREW in seed["pending_records"]
    own_seed = tmp_path / "bindings-seed.json"
    own_seed.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "bindings.json"
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(cat.path))
    # Input location only: no measured stand results exist in this fixture.
    # The builder, validator and pruning command are the real implementations.
    monkeypatch.setattr(bb, "STAND_RESULTS_FILE", tmp_path / "no-stand-results.json")
    ca.forget()
    try:
        code = bb.main(["--out", str(out), "--seed", str(own_seed),
                        "--commit", COMMIT, "--generated-at", AT,
                        "--prune-pending"])
        assert code == 0
        after = json.loads(own_seed.read_text(encoding="utf-8"))
        assert after["pending_records"] == [
            p for p in seed["pending_records"] if p != CREW]
        assert after["records"] == seed["records"]
        built = json.loads(out.read_text(encoding="utf-8"))
        assert any(r["id"] == "crew_unavailability" for r in built["bindings"])
        assert all(r["stand_verified"] == [] for r in built["bindings"])
        assert bb.SEED_FILE.read_bytes() == actual_seed_before
    finally:
        ca.forget()
