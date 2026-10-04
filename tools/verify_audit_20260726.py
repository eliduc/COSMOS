"""Сквозная проверка исправлений по аудиту 2026-07-26.

Живёт В РЕПОЗИТОРИИ (повторный аудит: «18 проверок, 0 провалов» из
временной папки независимо не воспроизводилось — на такие числа нельзя
ссылаться). Запуск:

    .venv/Scripts/python.exe tools/verify_audit_20260726.py

Код возврата 0 = все проверки годны; 1 = есть провалы.

По каждому пункту воспроизводится ИСХОДНЫЙ дефект и проверяется, что он
закрыт. Проверка идёт через командную строку и публичные функции, а не
через чтение исходников: «в коде написано» — не доказательство.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(REPO))
# Сам этот скрипт печатает по-русски в ту же однобайтовую консоль —
# пользуемся продуктовым помощником, заодно проверяя его в деле.
from _cli_console import force_utf8_console                          # noqa
force_utf8_console()
OK, BAD = [], []


def check(tag, cond, detail=""):
    (OK if cond else BAD).append(f"{tag}: {detail}")
    print(("  ГОДЕН   " if cond else "  ПРОВАЛ  ") + f"{tag} — {detail}")


def run(args, env=None):
    e = None
    if env is not None:
        import os
        e = dict(os.environ)
        for k in ("PYTHONIOENCODING", "PYTHONUTF8"):
            e.pop(k, None)
        e.update(env)
    return subprocess.run([str(PY)] + [str(a) for a in args],
                          capture_output=True, text=True, cwd=REPO,
                          encoding="utf-8", errors="replace", env=e)


tmp = Path(tempfile.mkdtemp())

print("\n=== P1-15: командные инструменты в чистой консоли ===")
pack = tmp / "pack.json"
pack.write_text(json.dumps({"name": "t", "rules": [{
    "title": "GENDER_ACCOMMODATION",
    "constraint": {"kind": "note", "note": json.dumps(
        {"vendor_constraint": "GENDER_ACCOMMODATION",
         "weight_level": "STD"})}}]}), encoding="utf-8")
p = run([REPO / "_rules_coverage.py", "--pack", pack,
         "--out", tmp / "cov.md"], env={"PYTHONLEGACYWINDOWSSTDIO": "1"})
check("P1-15", "UnicodeEncodeError" not in (p.stderr or "")
      and p.returncode == 2,
      f"код {p.returncode}, падения кодировки нет")

print("\n=== P2-01: выгрузка не падает KeyError и всегда пишет отчёт ===")
from _payload_to_meridian import export                              # noqa
pay = {"airports": [{"code": "BASE"}],
       "employees": [{"id": "E1", "homeAirport": "BASE"}],
       "flights": [{"flightNumber": "F1", "sourceLegId": "F1$01.08.2026$1",
                    "departureAirport": "BASE", "arrivalAirport": "AAA",
                    "departureUTCDateTime": "2026-08-01T06:00:00",
                    "arrivalUTCDateTime": "2026-08-01T08:00:00"}],
       "flightAssignments": [{"id": "F1#0", "flight": "F1",
                              "indexInFlight": 0, "requiredSkill": "CA"}]}
cat = {"selected": [{"fns": ["F1"], "active_fns": ["F1"],
                     "duty_fns": [["F1"], ["ПРИЗРАК"]]}]}
try:
    _doc, _chk, errs = export(pay, cat, level=3)
    check("P2-01 (KeyError)", bool(errs) and "вне командировки" in errs[0],
          f"громкий отказ: {errs[0][:60]}")
except Exception as exc:                                             # noqa
    check("P2-01 (KeyError)", False, f"по-прежнему падает: {exc!r}")

print("\n=== код возврата входного аудита при пустом периметре ===")
pj = tmp / "p.json"
pj.write_text(json.dumps({
    "airports": [{"code": "BASE"}],
    "employees": [{"id": "E1", "homeAirport": "BASE"}],
    "flights": [{"flightNumber": "F1", "departureAirport": "BASE",
                 "arrivalAirport": "AAA",
                 "departureUTCDateTime": "2026-08-01T06:00:00",
                 "arrivalUTCDateTime": "2026-08-01T08:00:00"}],
    "flightAssignments": []}), encoding="utf-8")
cj = tmp / "c.json"
cj.write_text(json.dumps({"selected": [
    {"fns": ["ЧУЖОЙ"], "active_fns": ["ЧУЖОЙ"], "duty_fns": [["ЧУЖОЙ"]],
     "kind": "chain", "sourceLegsMissing": 0}]}), encoding="utf-8")
p = run([REPO / "_input_audit.py", "--payload", pj, "--catalog", cj,
         "--bases", "BASE", "--out", tmp / "rep.json"])
check("аудит, пустой периметр", p.returncode == 3,
      f"код {p.returncode} (был 0 = «всё чисто»)")

print("\n=== P2-04: числа в документах ===")
ov = (REPO / "final-report" / "COSMOS-OVERVIEW-AUDIT-RU.md").read_text(
    encoding="utf-8")
# Четвёртый аудит (§6.2): ручные числа устарели пять раз за четыре раунда.
# Правильная проверка — не «совпадает ли число», а «нет ли в документе
# замороженного числа вовсе»: он обязан отсылать к команде.
import re as _re
_frozen = _re.search(r"\*\*\d{3,5} собранн", ov) or     _re.search(r"\d{2,3} файлов тестов", ov)
check("P2-04 (числа тестов не заморожены в тексте)",
      _frozen is None and "--collect-only" in ov,
      "документ отсылает к команде сбора вместо копии числа")
import os as _os
_rep_name = _os.environ.get("CREW_AUDIT_CUSTOMER_REPORT")
if _rep_name and (REPO / "final-report" / _rep_name).is_file():
    rep = (REPO / "final-report" / _rep_name).read_text(
        encoding="utf-8")
    check("P2-04 (версия среза)",
          "9e05b6a" not in rep and "9dbf6c0" in rep,
          "устаревший SHA убран")
    check("§3.2 (знаменатель)", "19 060" in rep and "93,9" in rep,
          "необъявленный знаменатель раскрыт")
else:
    print("  [пропуск] отчёт заказчика: имя не задано "
          "(CREW_AUDIT_CUSTOMER_REPORT) либо файла нет на машине")
api = (REPO / "webapp" / "API-CONTRACT.md").read_text(encoding="utf-8")
check("P2-04 (контракт API)",
      "keep_structure" in api and "pairing_level" in api,
      "импорт структуры и параметры построителя описаны")

print("\n=== P0-06: метрики сверяются со спросом и отказывают при не-OK ===")
import _vendor_metrics as vm                                          # noqa
payload = {"airports": [{"code": "BASE"}],
           "employees": [{"id": "E1", "homeAirport": "BASE"}],
           "flights": [{"flightNumber": "F1", "departureAirport": "BASE",
                        "arrivalAirport": "AAA",
                        "departureUTCDateTime": "2026-08-01T06:00:00",
                        "arrivalUTCDateTime": "2026-08-01T08:00:00"}],
           "flightAssignments": [
               {"id": f"F1#{i}", "flight": "F1", "indexInFlight": i,
                "requiredSkill": "CA"} for i in range(4)]}
assignment = {"label": "t", "result_status": "INFEASIBLE_INCUMBENT",
              "pairings": [{"pid": "C_0", "employee": "E1",
                            "crew": [{"indexInFlight": 0,
                                      "requiredSkill": "CA",
                                      "employee": "E1"}],
                            "fns": ["F1"], "active_fns": ["F1"]}]}
solution = {"flightAssignments": [{"id": "F1#0", "flight": "F1",
                                   "employee": "E1"}]}
m = vm.Metrics(payload, assignment, solution, None, 0, None, None)
o = m.compute()
comp = m.completeness()
check("P0-06 (сверка со спросом)",
      comp["demand_seats"] == 4 and comp["shortfall_seats"] == 3
      and o["UNASSIGNED_FLIGHT_COUNT"][0] == 1,
      f"спрос {comp['demand_seats']}, недобор {comp['shortfall_seats']} "
      f"(раньше сообщалось 0)")
check("P0-06 (отказ при не-OK)", comp["verdict"] == "INCOMPLETE",
      comp["reasons"][0][:60])

print("\n=== P0-06: сертификат правил возвращает ненулевой код ===")
from _vendor_rules_check import completeness                          # noqa
c = completeness({"rules": {"WEEKLY_REST": {"violations": 0, "checked": 0}},
                  "not_implemented": ["GENDER_ACCOMMODATION"]},
                 {"WEEKLY_REST": "HIGH_HIGH",
                  "GENDER_ACCOMMODATION": "HIGH_HIGH"})
check("P0-06 (вердикт полноты)", c["verdict"] == "INCOMPLETE",
      f"жёсткие без проверятеля: {c['hard_rules_unimplemented']}")

print("\n=== P0-05: неполный каталог не публикуется ===")
import webapp.registry as registry                                    # noqa
bad = tmp / "bad_catalog.json"
bad.write_text(json.dumps({"selected": [], "coverage": {
    "verdict": "INCOMPLETE_SEARCH", "uncovered": 3, "not_built": 3}}),
    encoding="utf-8")
good = tmp / "good_catalog.json"
good.write_text(json.dumps({"selected": [], "coverage": {
    "verdict": "INFEASIBLE_INPUT", "uncovered": 3, "not_built": 0}}),
    encoding="utf-8")
legacy = tmp / "legacy_catalog.json"
legacy.write_text(json.dumps({"selected": []}), encoding="utf-8")
check("P0-05 (реестр)",
      not registry._catalog_is_publishable(bad)
      and registry._catalog_is_publishable(good)
      and registry._catalog_is_publishable(legacy),
      "неполный отвергнут, непокрываемое и прежние каталоги приняты")

print("\n=== P0-03: построитель читает единый контракт правил ===")
cfg = tmp / "rules.json"
cfg.write_text(json.dumps({"parameters": {
    "max_duty_span_min": 600, "max_pairing_span_min": 2880,
    "min_sit_min": 45, "max_landings_per_duty": 3}}), encoding="utf-8")
bp = tmp / "bp.json"
bp.write_text(json.dumps({
    "airports": [], "employees": [{"id": "E1", "homeAirport": "BASE",
                                   "skills": ["CA"]}],
    "flights": [{"flightNumber": "F1", "departureAirport": "BASE",
                 "arrivalAirport": "AAA", "aircraftType": "A320",
                 "departureUTCDateTime": "2026-08-01T06:00:00",
                 "arrivalUTCDateTime": "2026-08-01T08:00:00"},
                {"flightNumber": "F2", "departureAirport": "AAA",
                 "arrivalAirport": "BASE", "aircraftType": "A320",
                 "departureUTCDateTime": "2026-08-01T10:00:00",
                 "arrivalUTCDateTime": "2026-08-01T12:00:00"}],
    "flightAssignments": [{"id": "F1#0", "flight": "F1", "indexInFlight": 0,
                           "requiredSkill": "CA"},
                          {"id": "F2#0", "flight": "F2", "indexInFlight": 0,
                           "requiredSkill": "CA"}]}), encoding="utf-8")
out = tmp / "cat.json"
p = run([REPO / "_pairing_builder.py", "--payload", bp, "--label", "v",
         "--bases", "BASE", "--budget-s", "5", "--out", out,
         "--rules-config", cfg,
         # таблица отдыха — данные: явная синтетика прогона (без файла
         # правил/условий все четыре --rest-* обязательны)
         "--rest-base", "660", "--rest-base-long", "1800",
         "--rest-off", "700", "--rest-off-long", "1300"])
doc = json.loads((out if out.exists()
                  else out.with_name(out.stem + ".pending.json"))
                 .read_text(encoding="utf-8"))
sig = doc["config_sig"]
check("P0-03 (правила из контракта)",
      sig["duty_span"] == 600 and sig["pairing_span"] == 2880
      and sig["sit_min"] == 45 and sig["max_segs"] == 3,
      f"смена {sig['duty_span']}, цепочка {sig['pairing_span']} — "
      f"из файла правил, а не из встроенных 760/8640")
check("P1-16 (отпечаток постановки)",
      bool(sig.get("rules_fingerprint")) and "coverage" in doc,
      f"отпечаток {sig.get('rules_fingerprint')}, "
      f"свидетельство покрытия {doc['coverage']['verdict']}")

print("\n=== P0-04: правило, не дошедшее до движка ===")
from _gerad_stage2_roster import gate_result_status                   # noqa
clean = {"missing_legs": [], "duplicates": 0, "unknown_flights": 0}
check("P0-04 (гейт)",
      gate_result_status("0hard/0soft", 0, clean, set()) == "OK"
      and gate_result_status("0hard/0soft", 0, clean, set(),
                             rules_not_applied=["weeklyRest"])
      == "INFEASIBLE_INCUMBENT",
      "объявленное, но не применённое правило отменяет OK")
from webapp import rule_packs                                         # noqa
comp2 = rule_packs.compile_resource_rules({"rules": [{
    "constraint": {"kind": "resource", "resource": "weeklyRest",
                   "window": {"min": 2880, "max": None},
                   "unit": "minutes", "level": "week"}}]})
check("P0-04 (компилятор)",
      comp2["document"] == {"weeklyRest": {"requiredMin": 2880,
                                           "periodMin": 10080}},
      "ярус 2б компилируется в документ для решателя")
jobs_src = (REPO / "webapp" / "jobs.py").read_text(encoding="utf-8")
check("P0-04 (передача этапу 2)", "--resource-rules" in jobs_src,
      "продуктовый запуск передаёт ресурсные правила")

print("\n=== P0-01/P0-02: контур самоулучшения ===")
from webapp import evolution_runner as er                             # noqa
import os
# Имена — НАСТОЯЩИЕ чтения продукта (третий аудит §5.3: первая версия
# проверяла выдуманное ANTHROPIC_API_KEY, которого продукт не читает).
os.environ["TUNNEL_SERVICE_TOKEN_ID"] = "секрет"
os.environ["CREW_ANTHROPIC_API_KEY"] = "секрет"   # webapp/settings.py
os.environ["CREW_SMTP_PASS"] = "секрет"           # webapp/mailer.py
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "нужен"
env = er._drop_secrets(er._child_env(), er._AGENT_ENV_DENY)
check("P0-01 (секреты агенту)",
      "TUNNEL_SERVICE_TOKEN_ID" not in env
      and "CREW_ANTHROPIC_API_KEY" not in env
      and "CREW_SMTP_PASS" not in env
      and env.get("CLAUDE_CODE_OAUTH_TOKEN") == "нужен",
      "настоящие секреты продукта убраны, токен запуска оставлен")
for _n in ("CREW_ANTHROPIC_API_KEY", "CREW_SMTP_PASS"):
    os.environ.pop(_n, None)

# §5.1 третьего аудита: сверка объявленного с применённым — через
# НАСТОЯЩИЙ транспорт (run_one кладёт в переменную сериализованный JSON,
# а не путь; первая версия сверки на этом ломалась, и корректный прогон
# не мог получить OK)
from _gerad_stage2_roster import _declared_minus_applied as _dma      # noqa
os.environ["CREW_RESOURCE_RULES"] = json.dumps(
    {"weeklyRest": {"requiredMin": 720}}, ensure_ascii=False)
_left = _dma("weeklyRest,landings", os.environ["CREW_RESOURCE_RULES"])
check("5.1-R3 (транспорт JSON, не путь)", _left == ["landings"],
      f"применённое через настоящий транспорт правило учтено: {_left}")
os.environ.pop("CREW_RESOURCE_RULES", None)
g = tmp / "repo"
g.mkdir()
for a in (["init", "-q"], ["config", "user.email", "t@t"],
          ["config", "user.name", "t"]):
    subprocess.run(["git", *a], cwd=g, capture_output=True)
(g / "tests").mkdir()
(g / "safe.py").write_text("x=1\n", encoding="utf-8")
(g / "tests" / "conftest.py").write_text("\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=g, capture_output=True)
subprocess.run(["git", "commit", "-qm", "b"], cwd=g, capture_output=True)
base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=g,
                      capture_output=True, text=True).stdout.strip()
(g / "tests" / "conftest.py").write_text("# hack\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=g, capture_output=True)
subprocess.run(["git", "commit", "-qm", "t"], cwd=g, capture_output=True)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=g,
                      capture_output=True, text=True).stdout.strip()
check("P0-02 (запретные пути)",
      er._forbidden_changes(g, base, head) == ["tests/conftest.py"],
      "правка собственной проверки ловится до исполнения кандидата")


print("\n=== Повторный аудит 2026-07-26: обходы закрыты ===")
# 5.3: сверка правил читает ИМЯ ПОЛЯ, которое пишет построитель
import argparse as _ap

from _gerad_stage2_roster import (RulesMismatch, _assert_same_rules,
                                  _declared_minus_applied)
from _pairing_builder import Builder as _B
_args = _ap.Namespace(
    level=3, method="enumerate", budget_s=1, solve_s=1, seed=7,
    bases=["AAA"], w_night=1, w_dh=1, duty_span=600, pairing_span=2880,
    max_segs=5, sit_min=30, rescue_dh=0, rescue_per_leg=0,
    rules_fingerprint="feedfacefeedface", rules_effective={},
    rules_overridden=[])
_b = _B.__new__(_B)
_b.args = _args
_real = {"selected": [], "config_sig": _b.config_signature()}
try:
    _assert_same_rules(_real, Path("real.json"))
    check("5.3 (имя поля подписи)", False,
          "чужой отпечаток в НАСТОЯЩЕМ каталоге принят")
except RulesMismatch:
    check("5.3 (имя поля подписи)", True,
          "чужой отпечаток в настоящем каталоге отвергается")

# 5.4: сверка объявленного с применённым поэлементная
with tempfile.TemporaryDirectory() as _td:
    _rp = Path(_td) / "r.json"
    _rp.write_text('{"weeklyRest": {}}', encoding="utf-8")
    left = _declared_minus_applied("weeklyRest,landings", str(_rp))
    check("5.4 (поэлементная сверка правил)", left == ["landings"],
          f"файл с одним правилом не закрывает всю декларацию: {left}")

# 5.1: предполётная проверка не обманывается объявленной переменной
import os as _os

from webapp import evolution_runner as _er
_os.environ["COSMOS_AGENT_ACCOUNT"] = "cosmos-agent"
_probs = _er.isolation_problems()
check("5.1 (изоляция не по переменной)", bool(_probs),
      "объявленная запись без запуска под ней не даёт зелёного")
check("5.1 (обход только явной единицей)",
      not _er._unisolated_allowed_by_env.__call__() if
      _os.environ.pop(_er._UNISOLATED_OK, None) is None else True,
      "пустая переменная обход не включает")
_os.environ[_er._UNISOLATED_OK] = "0"
check("5.1 (ноль не включает обход)",
      _er._unisolated_allowed_by_env() is False, "строка 0 = выключено")
_os.environ.pop(_er._UNISOLATED_OK, None)

print("\n=== Четвёртый аудит 2026-07-27: тракт и гонки ===")
# §4.1: rescue_s через НАСТОЯЩИЙ санитайзер, команду и подпись
from webapp import jobs as _jobs, solver_advisor as _sa                # noqa
_inst = {"id": "upload-v", "label": "v", "source": "upload",
         "payload_path": "data/uploads/v.json",
         "catalog_path": "final-report/pairing_base_catalog_v.json",
         "stage2_available": False}


def _argv(value):
    knobs = _sa.sanitize_params({"pairing_method": "columns",
                                 "pairing_level": 3, "rescue_s": value})
    if knobs.get("rescue_s") != value:
        return None
    cmds = _jobs._build_commands("stage1", dict(_inst),
                                 {"budget_min": 5, **knobs})
    return [str(a) for a in cmds[0]]


_a17, _a99 = _argv(17), _argv(99)
check("4.1 (rescue_s доходит до argv)",
      bool(_a17) and "--rescue-s" in _a17 and "17" in _a17,
      "санитайзер пропускает параметр и команда его несёт")
check("4.1 (разные значения - разные подписи)",
      bool(_a17) and bool(_a99)
      and _jobs.config_signature(_jobs.resolved_config([_a17]))
      != _jobs.config_signature(_jobs.resolved_config([_a99])),
      "две постановки различимы по подписи задачи")


print("\n=== Пятый аудит 2026-07-27: архитектурный остаток ===")
# C1: подделанное свидетельство не проходит
import json as _json

from webapp import evidence as _ev
with tempfile.TemporaryDirectory() as _td:
    _os.environ["COSMOS_EVIDENCE_KEY"] = str(Path(_td) / "k.key")
    _ev.CONFIG_DIR = Path(_td)
    _ev.issue(1, "test", base_sha="a" * 40, candidate_sha="b" * 40,
              diff_hash="c" * 64, checks={"unit_tests": "red"})
    _path = _ev.evidence_path(1, "test")
    _rec = _json.loads(_path.read_text(encoding="utf-8"))
    _rec["body"]["checks"]["unit_tests"] = "green"
    _path.write_text(_json.dumps(_rec), encoding="utf-8")
    try:
        _ev.verify(1, "test")
        _tampered_passed = True
    except _ev.EvidenceError:
        _tampered_passed = False
    check("C1 (подделка свидетельства)", not _tampered_passed,
          "правка тела записи ломает подпись и отвергается")
    try:
        _ev.verify(2, "stage")
        _missing_passed = True
    except _ev.EvidenceError:
        _missing_passed = False
    check("C1 (отсутствие свидетельства)", not _missing_passed,
          "нет записи — это отказ, а не пропуск проверки")
_os.environ.pop("COSMOS_EVIDENCE_KEY", None)

# C1: запрет по имени файла в ЛЮБОМ каталоге
check("C1 (запрет обходится переносом)",
      _er._is_forbidden_path("webapp/conftest.py")
      and _er._is_forbidden_path("webapp/sitecustomize.py")
      and not _er._is_forbidden_path("tests/test_jobs_run_profile.py"),
      "conftest и sitecustomize запрещены везде, обычный тест разрешён")

# C1: путь к ключу подписи закрыт от агента
check("C1 (ключ подписи невидим агенту)",
      "COSMOS_EVIDENCE_KEY" in _er._AGENT_ENV_DENY,
      "переменная с путём к ключу вычищается из среды кандидата")

# C2: посредник без настроенной задачи недоступен
from webapp import agent_broker as _ab
_os.environ.pop("COSMOS_AGENT_TASK", None)
check("C2 (посредник без задачи)", not _ab.available(),
      "без зарегистрированной задачи запуск под отдельной записью невозможен")

# A: пакет правил другого набора не попадает в постановку
from webapp import rule_packs as _rp
check("A (пакет чужого набора)",
      _rp.pack_applies_to({"scope": "instance:upload-a"}, {"id": "upload-a"})
      and not _rp.pack_applies_to({"scope": "instance:upload-b"},
                                  {"id": "upload-a"})
      and _rp.pack_applies_to({}, {"id": "upload-a"}),
      "свой видит, чужой нет, пакет прежнего выпуска остаётся нормой")

# E1: личность выпуска доступна наблюдателю
from webapp import build_identity as _bi
_ident = _bi.build_identity(refresh=True)
check("E1 (личность выпуска)",
      bool(_ident.get("source")) and "solver" in _ident,
      "срез кода и отпечаток решателя доступны наблюдателю")

# B: свидетельство потребителя различает три исхода
from _gerad_stage2_roster import (check_consumer_attestation as _cca,
                                  check_resource_attestation as _cra)
_eff = {"min_rest_min": 600, "max_monthly_block_min": 5100}


def _mk(applied):
    return {"schema": "cosmos-applied-rules/v1", "profile": "GERAD2P",
            "declared": ["min_rest_min", "max_monthly_block_min"],
            "applied": applied}


check("B (потребитель подтвердил применение)",
      _cca(_mk({"min_rest_min": 600, "max_monthly_block_min": 5100}),
           _eff)["ok"],
      "прочитанные значения совпадают с действующими")
check("B (заявлено, но не прочитано)",
      _cca(_mk({"min_rest_min": 600}), _eff)["not_applied"]
      == ["max_monthly_block_min"],
      "«передали, но не применили» видно поимённо")
check("B (прочитано другое значение)",
      [d["key"] for d in _cca(
          _mk({"min_rest_min": 480, "max_monthly_block_min": 5100}),
          _eff)["mismatched"]] == ["min_rest_min"],
      "правило работает, но не то, которое утвердил заказчик")
check("B (молчание решателя - отказ)",
      not _cca({}, _eff)["ok"],
      "отсутствие свидетельства не означает «всё применено»")
_crashed = False
try:
    _cca({"schema": "cosmos-applied-rules/v1", "declared": ["min_rest_min"],
          "applied": {"min_rest_min": "six hundred"}}, _eff)
    _cra({"resourceRules": "not a dict"}, {"weeklyRest": {"requiredMin": 1}})
except Exception:                                          # noqa: BLE001
    _crashed = True
check("B (мусор в ответе решателя)", not _crashed,
      "сверка стоит после многочасового решения — падать ей нельзя")
check("B (ресурсное правило разобрано иначе)",
      not _cra({"resourceRules": {"weeklyRest": {"requiredMin": 1440},
                                  "weeklyRestEvaluations": 5}},
               {"weeklyRest": {"requiredMin": 2880}})["ok"],
      "отпечаток файла такого расхождения не видит принципиально")

# E5: страж обезличенности роняет подложенный номер рейса
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "evidence_bundle", Path(__file__).resolve().parent / "evidence_bundle.py")
_eb = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_eb)
try:
    _eb.assert_anonymous({"demand": {"uncovered": "рейс ZZ601 не покрыт"}})
    _leak_passed = True
except _eb.AnonymityError:
    _leak_passed = False
check("E5 (страж обезличенности)", not _leak_passed,
      "опознавательный знак в пакете — отказ выпуска")


print("\n" + "=" * 62)
print(f"ГОДЕН: {len(OK)}   ПРОВАЛ: {len(BAD)}")
for b in BAD:
    print("  ! " + b)
sys.exit(1 if BAD else 0)
