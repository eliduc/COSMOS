# -*- coding: utf-8 -*-
"""Судья реестра инвариантов (R9 — сходимость; R10 — против ложной зелени).

Аудит R10 предъявил пять сценариев, в которых прежний судья оставался
зелёным при лжи реестра: удаление строки; ссылка на несуществующую
функцию; писатель через псевдоним; один вызов канонизации, оправдывающий
второй небезопасный писатель того же файла; git-вызов в другой форме
кавычек. Этот судья закрывает их классово:

1. ТОЧНЫЙ СПИСОК: ожидаемый перечень идентификаторов объявлен здесь;
   исчезновение или незаявленное появление строки — падение.
2. ВОРОТА-ФУНКЦИИ: колонка тестов несёт «файл.py::функция»; проверяется
   существование И файла, И функции.
3-5. ПЕРЕПИСЬ ПО AST, не по регуляркам: форма кавычек и псевдонимы
   переменных неразличимы для дерева разбора по построению.

Мета-мутации всех пяти сценариев включены: судья, не красневший на
реинъекции дефекта, — театр.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "final-report" / "INVARIANTS.md"

# Точный список действующих инвариантов (сценарий №1: удаление строки
# реестра обязано ронять судью; добавление нового инварианта — осознанное
# действие в ДВУХ местах: реестр + этот список).
EXPECTED_ACTIVE = {
    "INV-TIME-01", "INV-TIME-02", "INV-TIME-03",
    "INV-PRICE-01", "INV-PRICE-02", "INV-PRICE-03", "INV-PRICE-04",
    "INV-SIG-01", "INV-RULES-01",
    "INV-INBOX-01", "INV-INBOX-02", "INV-INBOX-03",
    "INV-RULESCFG-01", "INV-CAND-02", "INV-CAND-04",
    "INV-EXPORT-01", "INV-EXPORT-02", "INV-EXPORT-03",
    # Пакет закрытия P0-02/P0-04 (2026-08-07): манифест постановки,
    # правила источника у второго движка, разделение субъектов на Test.
    "INV-PROBLEM-01", "INV-RULES-02", "INV-RULES-04", "INV-CAND-03",
}


def _rows(section: str, text: str | None = None) -> list[list[str]]:
    text = text if text is not None else REGISTRY.read_text(encoding="utf-8")
    start = text.index(section)
    end = len(text)
    for nxt in ("\n## ", "\n---"):
        p = text.find(nxt, start + len(section))
        if p != -1:
            end = min(end, p)
    out = []
    for line in text[start:end].splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0].startswith("`INV-"):
            out.append(cells)
    return out


def _idents(rows) -> set[str]:
    return {c[0].strip("`") for c in rows}


# ------------------------------------------------------- связность реестра

def test_registry_matches_the_expected_inventory():
    """Сценарий №1: реестр обязан содержать РОВНО заявленный перечень."""
    active = _idents(_rows("## Действующие инварианты"))
    missing = EXPECTED_ACTIVE - active
    unexpected = active - EXPECTED_ACTIVE
    assert not missing, f"строки исчезли из реестра: {sorted(missing)}"
    assert not unexpected, (
        f"незаявленные строки: {sorted(unexpected)} — новый инвариант "
        f"добавляется осознанно и в реестр, и в EXPECTED_ACTIVE судьи")


_STATUSES = ("закрыт", "частично", "открыт")


def test_every_invariant_has_a_known_status_and_names_its_tests():
    for cells in _rows("## Действующие инварианты"):
        ident, tests, rnd, status = cells[0], cells[4], cells[5], cells[6]
        clean = status.replace("*", "").strip()
        assert clean in _STATUSES, f"{ident}: неизвестный статус {status!r}"
        assert tests and tests != "—", f"{ident} не называет ни одного теста"
        if clean != "закрыт":
            assert "R" in rnd, (
                f"{ident} понижен, но не называет раунд опровержения")


def _gate_refs(tests_cell: str):
    """Ссылки-ворота «файл.py::функция» и голые «файл.py» из ячейки."""
    funcs = re.findall(r"`([\w.]+\.py)::(\w+)`", tests_cell)
    bare = re.findall(r"`([\w./]+\.py)`", tests_cell)
    return funcs, bare


def _gates_exist(tests_cell: str, tests_dir: Path) -> list[str]:
    """Сценарий №2: и файл, и функция обязаны существовать."""
    problems = []
    funcs, bare = _gate_refs(tests_cell)
    for fname, func in funcs:
        fp = tests_dir / Path(fname).name
        if not fp.exists():
            problems.append(f"{fname} отсутствует")
            continue
        if not re.search(r"^def %s\b" % re.escape(func),
                         fp.read_text(encoding="utf-8"), re.MULTILINE):
            problems.append(f"{fname}::{func} — функции нет")
    for fname in bare:
        if not (tests_dir / Path(fname).name).exists():
            problems.append(f"{fname} отсутствует")
    return problems


def test_every_named_gate_exists():
    for cells in _rows("## Действующие инварианты"):
        problems = _gates_exist(cells[4], REPO / "tests")
        assert not problems, f"{cells[0]}: {problems}"


def test_closed_invariants_name_function_level_gates():
    """У «закрыт» ворота обязаны быть функциями, не именами файлов:
    файл существует и тогда, когда нужный тест из него удалён."""
    for cells in _rows("## Действующие инварианты"):
        status = cells[6].replace("*", "").strip()
        if status != "закрыт":
            continue
        funcs, _bare = _gate_refs(cells[4])
        assert funcs, (f"{cells[0]} закрыт, но не называет ни одной "
                       f"функции-ворот (файл::функция)")


def test_every_deferral_carries_a_reason_and_a_round():
    rows = _rows("## Объявленные отсрочки")
    assert rows, "раздел отсрочек не должен исчезать молча"
    for cells in rows:
        ident, reason, rnd = cells[0], cells[2], cells[3]
        assert len(reason) > 20, f"{ident}: причина отсрочки не названа"
        assert rnd and rnd != "—", f"{ident}: раунд отсрочки не указан"


def test_invariant_identifiers_are_unique():
    seen = [c[0] for c in _rows("## Действующие инварианты")]
    seen += [c[0] for c in _rows("## Объявленные отсрочки")]
    assert len(seen) == len(set(seen)), "идентификаторы дублируются"


# ---------------------------------------------------- перепись по AST

def _ast_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def git_call_lines(source: str) -> list[int]:
    """Сценарий №5: вызовы git по AST — форма кавычек неразличима.

    Ловится любой вызов, чей первый позиционный аргумент — список,
    начинающийся строковым литералом git, либо строка вида git-команды
    (shell-форма)."""
    out = []
    for call in _ast_calls(ast.parse(source)):
        if not call.args:
            continue
        a0 = call.args[0]
        if isinstance(a0, ast.List) and a0.elts:
            e0 = a0.elts[0]
            if isinstance(e0, ast.Constant) and e0.value == "git":
                out.append(call.lineno)
        elif isinstance(a0, ast.Constant) and isinstance(a0.value, str) \
                and a0.value.startswith("git "):
            out.append(call.lineno)
    return out


# Модули, которым разрешено звать git напрямую (обёртки).
GIT_ALLOWED = {"evolution_runner.py", "change_requests.py",
               "build_identity.py"}   # обёртка идентичности сборки


def test_census_git_calls_only_in_declared_wrappers():
    offenders = []
    for py in sorted((REPO / "webapp").glob("*.py")):
        if py.name in GIT_ALLOWED:
            continue
        lines = git_call_lines(py.read_text(encoding="utf-8"))
        offenders += [f"{py.name}:{ln}" for ln in lines]
    assert not offenders, (
        "git зовётся вне объявленных обёрток: " + ", ".join(offenders))


CENSUS_OK = "census-ok:"


def writer_violations(source: str) -> list[int]:
    """Сценарии №3-4: запись данных на диск по AST на уровне ФУНКЦИИ.

    Нарушение: функция содержит запись json на диск (write_text от
    dumps либо json.dump), но НЕ содержит вызова canonicalize_for_store
    или write_payload. Псевдоним переменной не спасает (сценарий №3),
    чужой вызов канонизации в другой функции — тоже (сценарий №4)."""
    tree = ast.parse(source)
    bad: list[int] = []

    def fn_has_canon(fn) -> bool:
        for call in _ast_calls(fn):
            name = ""
            if isinstance(call.func, ast.Attribute):
                name = call.func.attr
            elif isinstance(call.func, ast.Name):
                name = call.func.id
            if name in ("canonicalize_for_store", "write_payload"):
                return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = []
        for call in _ast_calls(node):
            if isinstance(call.func, ast.Attribute) \
                    and call.func.attr == "write_text" and call.args:
                inner = call.args[0]
                if isinstance(inner, ast.Call) \
                        and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr == "dumps":
                    writes.append(call.lineno)
            if isinstance(call.func, ast.Attribute) \
                    and call.func.attr == "dump":
                writes.append(call.lineno)
        if writes and not fn_has_canon(node):
            src_lines = source.splitlines()
            for ln in writes:
                # Осознанное исключение объявляется МАРКЕРОМ на самой
                # строке записи — видимым в диффе, а не знанием автора.
                if CENSUS_OK in src_lines[ln - 1]:
                    continue
                bad.append(ln)
    return bad


# Писатели данных, разбираемые пофункционально. Граница записи
# (registry.py) сама реализует канонизацию; список расширяется осознанно.
WRITER_SCOPED = {"scenarios.py", "qualifications.py", "schedule_deltas.py",
                 "disruption.py"}


def test_census_payload_writers_function_level():
    offenders = []
    for name in sorted(WRITER_SCOPED):
        py = REPO / "webapp" / name
        if not py.exists():
            continue
        for ln in writer_violations(py.read_text(encoding="utf-8")):
            offenders.append(f"{name}:{ln}")
    assert not offenders, (
        "запись данных в функции без канонизации: " + ", ".join(offenders))


def test_census_dh_options_sink():
    """INV-PRICE-01: каждый вызов отбора перегонов передаёт приёмник."""
    src = (REPO / "_pairing_builder.py").read_text(encoding="utf-8")
    calls = list(re.finditer(r"\.dh_options\(", src))
    # Аудит A4 (04.09.2026): классификация непокрытых больше не зовёт
    # отбор перегонов (достижимость судится обходом графа рейсов) —
    # вызовов стало четыре; инвариант «каждый вызов с приёмником»
    # прежний.
    assert len(calls) >= 4, "вызовы отбора перегонов не найдены"
    missing = []
    for m in calls:
        i, depth = m.end(), 1
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        if "sink=" not in src[m.end():i]:
            missing.append(src[:m.start()].count("\n") + 1)
    assert not missing, f"отбор перегонов без приёмника, строки: {missing}"


def test_census_price_pass_returns_pricing_result():
    """R10: тройка (cols, exhausted, caps) — запрещённая форма возврата.
    Каждый прайсер обязан строить PricingResult."""
    for fname in ("_pairing_builder.py", "_gerad_colgen.py",
                  "_gerad_duty_pricing.py", "_gerad_pricing_mp.py"):
        src = (REPO / fname).read_text(encoding="utf-8")
        assert "PricingResult(" in src, f"{fname}: PricingResult не строится"
        assert not re.search(r"return\s+cols\s*,\s*exhausted", src), (
            f"{fname}: возврат запрещённой формы (cols, exhausted)")


# --------------------------------------- мета-мутации (пять сценариев R10)

def test_meta_deleting_a_row_is_caught():
    text = REGISTRY.read_text(encoding="utf-8")
    mutated = "\n".join(l for l in text.splitlines()
                        if "INV-TIME-02" not in l)
    active = _idents(_rows("## Действующие инварианты", mutated))
    assert EXPECTED_ACTIVE - active == {"INV-TIME-02"}, (
        "удаление строки реестра прошло бы незамеченным")


def test_meta_missing_gate_function_is_caught(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_real():\n    pass\n",
                                        encoding="utf-8")
    problems = _gates_exist("`test_x.py::test_ghost`", tmp_path)
    assert problems, "ссылка на несуществующую функцию прошла бы"


def test_meta_alias_writer_is_caught():
    src = ("def save(document, target):\n"
           "    target.write_text(json.dumps(document, indent=1))\n")
    assert writer_violations(src), (
        "писатель через псевдоним прошёл бы перепись")


def test_meta_one_canonize_does_not_cover_second_writer():
    src = ("def good(payload, t):\n"
           "    canonicalize_for_store(payload)\n"
           "    t.write_text(json.dumps(payload))\n"
           "def bad(data, t):\n"
           "    t.write_text(json.dumps(data))\n")
    bad = writer_violations(src)
    assert bad and all(ln >= 4 for ln in bad), (
        "чужая канонизация оправдала бы второй писатель")


def test_meta_git_call_in_any_quote_form_is_caught():
    samples = (
        'subprocess.run(["git", "status"])',
        "subprocess.run(['git', 'status'])",
        'os.system("git push origin main")',
    )
    for form in samples:
        assert git_call_lines(form), f"git-вызов невидим: {form}"
