# -*- coding: utf-8 -*-
"""ИЗМЕРИТЕЛЬ ЦЕНЫ ГРАММАТИКИ K02 сам под пробой (тринадцатое
неавторское ревью, находки С-3, С-4, С-5).

Инструмент `tools/k02_grammar_price.py` называет цену закона числом, и
на это число ссылаются и запись коммита, и руководство 10. Своих проб у
него не было НИ ОДНОЙ — оттого и осталась незамеченной его главная
щель: сравнение полного дампа с дампом, снятым БЕЗ живых книг, печатало
«ЖИВЫЕ КНИГИ: 511 пар, расхождений: 0» и кончалось кодом 0. ПУСТОЙ
ЗАМЕР был неотличим от «ничего не изменилось» — то есть инструмент,
которым удостоверяют цену, сам удостоверял что угодно.

Здесь он судится своими пробами: сопоставимость замеров, полнота
наборов ключей, ошибки замера и охват корпуса."""
import copy
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOL = os.path.join(os.path.dirname(_HERE), "tools", "k02_grammar_price.py")
_spec = importlib.util.spec_from_file_location("k02_grammar_price", _TOOL)
price = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(price)

from cdpm import family_params as fp        # noqa: E402


def _dump(pairs, books=None, corpus_rows=None, module_sha="0" * 64):
    """Малый дамп того же вида, что пишет `cmd_dump`."""
    rows = list(dict.fromkeys(json.loads(k)[0] for k in pairs))
    corpus_rows = rows if corpus_rows is None else list(corpus_rows)
    books = rows if books is None else list(books)
    manifest = [{"path": "ANSWER-BOOK.json", "sha256": price._json_sha(books),
                 "answers": len(books), "blank_answers": 0}] if books else []
    sources = [{"path": "cdpm/family_params.py", "sha256": module_sha},
               {"path": "catalog/families.json", "sha256": "1" * 64},
               {"path": "template_catalog/reader.py", "sha256": "2" * 64}]
    body = {
        "schema": "k02-price/2",
        "package_root": "X", "books_root": "X",
        "module_sha256": module_sha,
        "environment": {"python": "synthetic test runtime", "tool_sha256": "3" * 64,
                        "sources": sources, "catalog_sha": price._json_sha([sources[1]])},
        "books": len(manifest), "book_lines": len(books),
        "books_manifest": manifest, "books_manifest_sha": price._json_sha(manifest),
        "corpus_rows": corpus_rows, "book_rows": books,
        "book_distinct": len(set(books)),
        "corpus": len(corpus_rows),
        "units": ["count"],  # one explicitly measured unit, complete Cartesian matrix
        "corpus_sha": price._rows_sha(corpus_rows),
        "books_sha": price._rows_sha(books),
        "corpus_pairs": dict(pairs), "book_pairs": dict(pairs),
        "corpus_status": {k: ("ОТКРЫТ" if v == "-" else "ЗНАЧЕНИЕ")
                          for k, v in pairs.items()},
        "book_status": {k: ("ОТКРЫТ" if v == "-" else "ЗНАЧЕНИЕ")
                        for k, v in pairs.items()},
        "causes": {k: "" for k in pairs},
    }
    return body


def _run(tmp_path, was, now):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(was, ensure_ascii=False), encoding="utf-8")
    b.write_text(json.dumps(now, ensure_ascii=False), encoding="utf-8")
    return price.main(["compare", "--was", str(a), "--now", str(b)])


K = json.dumps(["2 часа", "count"], ensure_ascii=False)
K2 = json.dumps(["3 часа", "count"], ensure_ascii=False)


def test_price_equal_dumps_are_not_evidence_of_a_change(tmp_path, capsys):
    """COSMOS: сравнение одного кода с самим собой не удостоверяет правку."""
    d = _dump({K: "2"})
    assert _run(tmp_path, d, copy.deepcopy(d)) == 2
    captured = capsys.readouterr()
    assert "одинаковый код модуля" in captured.err
    assert "расхождений:" not in captured.out


@pytest.mark.parametrize("case", ["same-file", "different-root", "digest-case"])
def test_price_same_code_is_refused_despite_artifact_or_path_changes(tmp_path, capsys, case):
    was = _dump({K: "2"}, module_sha="ab" * 32)
    now = copy.deepcopy(was)
    if case == "different-root":
        now["package_root"] = "Y:/another-checkout"
    elif case == "digest-case":
        now["module_sha256"] = was["module_sha256"].upper()
        now["environment"]["sources"][0]["sha256"] = now["module_sha256"]
    price._validate_dump(was)
    price._validate_dump(now)
    if case == "same-file":
        path = tmp_path / "same.json"
        path.write_text(json.dumps(was, ensure_ascii=False), encoding="utf-8")
        rc = price.main(["compare", "--was", str(path), "--now", str(path)])
    else:
        rc = _run(tmp_path, was, now)
    assert rc == 2
    captured = capsys.readouterr()
    assert "одинаковый код модуля" in captured.err
    assert "расхождений:" not in captured.out


@pytest.mark.parametrize("same_root", [True, False])
def test_price_distinct_code_with_equal_results_is_a_valid_zero_price(tmp_path, capsys, same_root):
    was = _dump({K: "2"}, module_sha="ab" * 32)
    now = _dump({K: "2"}, module_sha="cd" * 32)
    if not same_root:
        now["package_root"] = "Y:/another-checkout"
    assert _run(tmp_path, was, now) == 0
    captured = capsys.readouterr()
    assert "расхождений: 0" in captured.out
    assert "ОШИБОК ЗАМЕРА всего (было + стало): 0" in captured.out
    assert was["module_sha256"] in captured.out
    assert now["module_sha256"] in captured.out
    assert not captured.err


@pytest.mark.parametrize("path", ["cdpm/model.py", "cdpm/catalog_bridge.py",
                                  "template_catalog/reader.py"])
def test_price_dependency_only_change_is_a_valid_measurement(tmp_path, capsys, path):
    was = _dump({K: "2"})
    if not any(source["path"] == path for source in was["environment"]["sources"]):
        was["environment"]["sources"].append({"path": path, "sha256": "ab" * 32})
    now = copy.deepcopy(was)
    next(source for source in now["environment"]["sources"]
         if source["path"] == path)["sha256"] = "cd" * 32
    assert was["module_sha256"] == now["module_sha256"]
    price._validate_dump(was)
    price._validate_dump(now)
    assert _run(tmp_path, was, now) == 0
    captured = capsys.readouterr()
    assert "расхождений: 0" in captured.out
    assert not captured.err


@pytest.mark.parametrize("remove", [False, True])
def test_price_added_or_removed_dependency_is_a_change(tmp_path, capsys, remove):
    was = _dump({K: "2"})
    now = copy.deepcopy(was)
    now["environment"]["sources"].append({"path": "cdpm/new_rule.py", "sha256": "ab" * 32})
    if remove:
        was, now = now, was
    assert _run(tmp_path, was, now) == 0
    assert "расхождений: 0" in capsys.readouterr().out


def test_price_source_order_and_hash_case_do_not_create_a_change(tmp_path, capsys):
    was = _dump({K: "2"}, module_sha="ab" * 32)
    was["environment"]["sources"].append({"path": "cdpm/model.py", "sha256": "cd" * 32})
    now = copy.deepcopy(was)
    now["environment"]["sources"].reverse()
    for source in now["environment"]["sources"]:
        if not source["path"].startswith("catalog/"):
            source["sha256"] = source["sha256"].upper()
    now["module_sha256"] = now["module_sha256"].upper()
    assert _run(tmp_path, was, now) == 2
    assert "расхождений:" not in capsys.readouterr().out


def test_price_unrelated_manifest_entry_does_not_create_product_change(tmp_path, capsys):
    was = _dump({K: "2"})
    now = copy.deepcopy(was)
    now["environment"]["sources"].append({"path": "notes/report.md", "sha256": "ab" * 32})
    assert _run(tmp_path, was, now) == 2
    assert "расхождений:" not in capsys.readouterr().out


def test_price_empty_measurement_is_not_a_zero_price(tmp_path, capsys):
    """С-5, ГЛАВНАЯ щель. Дамп, снятый БЕЗ живых книг, прежде сравнивался
    с полным и печатал «511 пар, расхождений: 0» при коде 0: обход шёл по
    ключам `was`, а `now.get(key, before)` объявлял пропавший ключ
    неизменным. Теперь замеры несопоставимы и сравнение падает."""
    was = _dump({K: "2"}, books=("книга",))
    now = _dump({}, books=())
    now["corpus_pairs"] = {K: "2"}
    now["corpus_status"] = {K: "ЗНАЧЕНИЕ"}
    now["corpus_sha"] = was["corpus_sha"]
    assert _run(tmp_path, was, now) == 2
    assert "несопоставимы" in capsys.readouterr().err


def test_price_a_vanished_key_is_named_not_swallowed(tmp_path, capsys):
    """С-5. Пропавший ключ при ТЕХ ЖЕ объявленных размерах больше не
    считается «не изменился»: он называется поимённо."""
    was = _dump({K: "2", K2: "3"})
    now = copy.deepcopy(was)
    for field in ("corpus_pairs", "book_pairs", "corpus_status", "book_status"):
        now[field].pop(K2)
    now["corpus_sha"] = was["corpus_sha"]
    now["books_sha"] = was["books_sha"]
    now["corpus"] = was["corpus"]
    assert _run(tmp_path, was, now) == 2
    err = capsys.readouterr().err
    assert "не совпадают" in err and "ПРОПАЛ" in err


def test_price_a_new_key_is_named_not_ignored(tmp_path, capsys):
    """С-5. Обход шёл по `was`, и НОВЫЙ ключ в `now` не смотрели вовсе."""
    was = _dump({K: "2"})
    now = copy.deepcopy(was)
    for field in ("corpus_pairs", "book_pairs"):
        now[field][K2] = "3"
    for field in ("corpus_status", "book_status"):
        now[field][K2] = "ЗНАЧЕНИЕ"
    now["corpus_sha"] = was["corpus_sha"]
    now["books_sha"] = was["books_sha"]
    now["corpus"] = was["corpus"]
    assert _run(tmp_path, was, now) == 2
    assert "НОВЫЙ" in capsys.readouterr().err


def test_price_a_replaced_value_is_a_substitution(tmp_path, capsys):
    """Положительный контроль: при РАВНЫХ наборах ключей подмена значения
    считается подменой, а не пропадает."""
    was = _dump({K: "2"})
    now = _dump({K: "600"}, module_sha="4" * 64)
    assert _run(tmp_path, was, now) == 0
    out = capsys.readouterr().out
    assert "ПОДМЕНА)   : 1" in out


def test_price_equal_errors_do_not_certify_a_zero_price(tmp_path, capsys):
    """С-5, замечание аудитора. `_outcome` превращает исключение в
    «!ИмяИсключения». Равные ошибки по ОБЕИМ сторонам прежде давали
    «расхождений: 0» и код 0 — то есть ошибка удостоверяла нулевую цену.
    Ошибка не значение и не объяснённая потеря: код возврата ненулевой."""
    was = _dump({K: "!TypeError"})
    now = _dump({K: "!TypeError"}, module_sha="4" * 64)
    assert _run(tmp_path, was, now) == 3
    cap = capsys.readouterr()
    # по две матрицы значений (перебор и книги) на каждой стороне
    assert "ОШИБОК ЗАМЕРА всего (было + стало): 4" in cap.out
    assert "НЕ УДОСТОВЕРЯЕТСЯ" in cap.err


def test_price_a_value_turned_into_an_error_is_not_a_substitution(tmp_path,
                                                                  capsys):
    """С-5, ВТОРАЯ половина того же закона. Прежний исход был значением, а
    новый — исключением: это не ПОДМЕНА значения (значения там нет), а
    ОШИБКА ЗАМЕРА. Считать её подменой значило бы назвать ценой то, что
    ценой не является, и спрятать поломку замера среди расхождений."""
    was = _dump({K: "2"})
    now = _dump({K: "!TypeError"}, module_sha="4" * 64)
    assert _run(tmp_path, was, now) == 3
    out = capsys.readouterr().out
    assert "ПОДМЕНА)   : 0" in out
    assert "ОШИБОК ЗАМЕРА (не значение и не потеря): 1" in out


def test_price_the_key_set_guard_is_a_first_line_and_the_table_a_second(
        tmp_path):
    """С-5, ВТОРОЙ РУБЕЖ у таблицы, объявленный вслух. Сверка наборов
    ключей (`_same_keys`) стоит ПЕРВОЙ и до таблицы не пускает ни одной
    несопоставимой пары, поэтому мутация «вернуть `now.get(key, before)`»
    набора не краснит: рубеж недостижим через `cmd_compare`.

    Клауза оставлена рубежом, а не украшением: `_table` — отдельная
    функция, и прямой её вызов с неполной матрицей обязан ПАДАТЬ, а не
    молча объявлять пропавший ключ неизменным. Проба зовёт её прямо."""
    was = {K: "2", K2: "3"}
    now = {K: "2"}
    assert not price._same_keys(was, now, "проба")        # первый рубеж
    with pytest.raises(KeyError):                         # второй рубеж
        price._table(was, now, {}, "проба")


def test_price_a_corpus_of_the_same_size_but_other_rows_is_refused(tmp_path):
    """С-5. Прежде сверялись РАЗМЕР корпуса и список единиц. Два разных
    перебора одного размера «сопоставимыми» больше не считаются:
    отпечаток берётся с самих входов."""
    was = _dump({K: "2"}, corpus_rows=("2 часа",))
    now = _dump({K: "2"}, corpus_rows=("3 часа",))
    assert was["corpus"] == now["corpus"]
    assert _run(tmp_path, was, now) == 2


def test_price_a_dump_without_status_is_refused(tmp_path, capsys):
    """С-3. Дамп, снятый ПРЕЖНИМ замером (без матрицы статуса), молча
    сравнивать нельзя — иначе сквозной замер пропал бы и цена снова
    считалась бы по одному `parse_value`."""
    was = _dump({K: "2"})
    now = copy.deepcopy(was)
    now.pop("corpus_status")
    assert _run(tmp_path, was, now) == 2
    assert "без статуса" in capsys.readouterr().err


def test_price_status_sees_what_the_value_table_cannot(tmp_path, capsys):
    """С-3, ради чего статус и заведён. Ответ, ставший БЕЗУСЛОВНЫМ
    ОТКАЗОМ, значения не даёт ни до правки, ни после — таблица значений
    показывает «-» → «-» и молчит, — а вопрос при этом ЗАКРЫВАЕТСЯ
    решением и блокировка с носителей снимается."""
    was = _dump({K: "-"})
    now = _dump({K: "-"}, module_sha="4" * 64)
    now["corpus_status"] = {K: "РЕШЕНИЕ"}
    now["book_status"] = {K: "РЕШЕНИЕ"}
    assert _run(tmp_path, was, now) == 0
    out = capsys.readouterr().out
    assert "расхождений: 0" in out            # таблица значений слепа
    assert out.count("сдвигов статуса: 1") == 2   # перебор и книги
    assert "ОТКРЫТ    → РЕШЕНИЕ" in out
    assert "СНИМАЕТ БЛОКИРОВКУ" in out


def test_price_status_is_taken_in_the_order_of_the_real_application():
    """С-3. Статус берётся теми же двумя вызовами и в том же ПОРЯДКЕ,
    что в `apply_param_answer`: сперва разбор отказа, затем значение.
    Отказ С ПОМЕХОЙ решением не является — вопрос остаётся открытым."""
    assert price._status(fp, "Не применяется.", "count") == "РЕШЕНИЕ"
    assert price._status(fp, "Не применяется для базы SVO.",
                         "count") == "ОТКРЫТ"
    assert price._status(fp, "600 минут. Не применяется.",
                         "duration_minutes") == "ОТКРЫТ"
    assert price._status(fp, "600 минут", "duration_minutes") == "ЗНАЧЕНИЕ"
    assert price._status(fp, "2 календарных недель", "count") == "ОТКРЫТ"


def test_price_period_refusal_has_the_actual_named_cause():
    raw = "10 часов. В неделю."
    assert price._status(fp, raw, "duration_minutes") == "ОТКРЫТ"
    assert price._outcome(fp, raw, "duration_minutes") == "-"
    assert price._cause(fp, raw, "duration_minutes") == "неподдержанный период нормы"


def test_price_corpus_covers_both_new_laws():
    """С-4. Корпус был ОДНОСТОРОНЕН: входов ОБОИХ новых классов в нём не
    было ни одного, и «потерь без причины 0» ничего не говорило о
    классах, где правка стоит денег. Классы обязаны быть в корпусе — и
    обе их стороны, законная и та, что должна отказывать."""
    import re
    rows = price.corpus()

    def own_then_window(raw):
        for mm in fp._GAP_UNIT_RE.finditer(raw):
            if fp._ANY_UNIT_RE.match(raw, mm.start("num")):
                return True
        return False

    def lead_then_dash(raw, tail_re):
        for mm in re.finditer(r"[^\W\d_]+", raw):
            if re.search(r"[A-Za-z0-9]", mm.group()):
                continue
            if re.match(r"[" + re.escape(fp._DASHES) + r"]+" + tail_re,
                        raw[mm.end():]):
                return True
        return False

    assert sum(1 for r in rows if own_then_window(r)) > 100
    assert sum(1 for r in rows if lead_then_dash(r, "[A-Za-z]")) > 100
    assert sum(1 for r in rows if lead_then_dash(r, r"\d")) > 100
    # и обе стороны класса С-1: со СВОИМ словом и с ЧУЖИМ
    assert "не более 75 часов в месяц" in rows
    assert "не более 75 процентов в месяц" in rows
    assert "Тип судна—A320" in rows and "Ту-154" in rows


@pytest.mark.parametrize("raw,unit,want", [
    ("не более 75 часов в месяц", "credit_hours", "ОТКРЫТ"),
    ("Тип судна—A320", "code_list", "ЗНАЧЕНИЕ"),
    ("Ту-154", "code_list", "ОТКРЫТ"),
    ("10 в процентах", "hours_per_credit_hour", "ОТКРЫТ"),
])
def test_price_tool_and_module_agree_on_the_new_classes(raw, unit, want):
    """Шов «инструмент — модуль»: замер и грамматика обязаны говорить об
    одном и том же входе одно и то же."""
    assert price._status(fp, raw, unit) == want
    got = price._outcome(fp, raw, unit)
    assert (got != "-") == (want == "ЗНАЧЕНИЕ")


# --- РЕШЁТКА ОКНА: числа с рецептом (остаток 5 ревью 13) -------------

_LAT = os.path.join(os.path.dirname(_HERE), "tools", "k02_window_lattice.py")
_lspec = importlib.util.spec_from_file_location("k02_window_lattice", _LAT)
lattice = importlib.util.module_from_spec(_lspec)
_lspec.loader.exec_module(lattice)


def test_lattice_shape_matches_the_number_it_is_named_by():
    """Числа решётки прежде называли ФОРМУ («3 × 4 × 7 × 4 × 6 × 4 =
    8064»), но не СОСТАВ, и воспроизвести их было нечем — тот же род,
    что у отозванного «1336 профиль». Теперь состав объявлен поимённо, и
    форма обязана сходиться с ним, а не с памятью."""
    rows = lattice.lattice()
    assert len(rows) == len(set(rows)), "в решётке есть повторы ответов"
    assert len(rows) == (len(lattice.CLAUSES) * len(lattice.NUMBERS)
                         * len(lattice.OWN_WORDS) * len(lattice.PREPS)
                         * len(lattice.SPANS))
    assert len(rows) * len(lattice.UNITS) == 8064
    # решётка обязана РАЗЛИЧАТЬ варианты: без пустого слова и без
    # предмета счёта окно рвут оба, и число ничего не говорит
    assert "" in lattice.OWN_WORDS
    assert "раз" in lattice.OWN_WORDS and "рейсов" in lattice.OWN_WORDS
    assert "10 часов в месяц" in rows and "10 в месяц" in rows


def test_lattice_variant_patch_must_actually_apply(tmp_path, monkeypatch):
    """СТОРОЖ МУТАЦИИ. Заплата варианта «ПРЕДЛОГ» кладётся по ТОЧНОЙ
    строке головы. Если строка разойдётся с кодом, скрипт обязан ПАСТЬ,
    а не напечатать число молча: пустая мутация — это цена, названная
    ни о чём (урок круга 13)."""
    ok = lattice._prep_source(str(tmp_path))
    src = open(ok, encoding="utf-8").read()
    assert lattice._PATCH_TO in src and lattice._PATCH_FROM not in src
    assert "_prep_breaks_gap" in src
    assert lattice._PERIOD_PATCH_TO in src

    bad = tmp_path / "bad"
    bad.mkdir()
    monkeypatch.setattr(lattice, "_PATCH_FROM", "такой строки в коде нет")
    with pytest.raises(SystemExit) as exc:
        lattice._prep_source(str(bad))
    assert "НЕ ПРИМЕНИЛАСЬ" in str(exc.value)


def test_lattice_includes_and_refuses_forms_with_and_without_own_units():
    rows = lattice.lattice(lattice.FORM_WORDS)
    assert len(rows) == len(set(rows)) == 3696
    assert len(rows) * len(lattice.UNITS) == 14784
    for tail in ("процентах", "долях", "секунду", "неделях"):
        assert f"10 часов в {tail}" in rows
        assert f"10 в {tail}" in rows
    assert lattice.read_set(fp, rows) == set()


def test_lattice_calendar_pairs_cannot_discard_the_period():
    # The complete named 8064-pair set, not only its shape or a sample.
    rows = lattice.lattice()
    assert len(rows) * len(lattice.UNITS) == 8064
    assert lattice.read_set(fp, rows) == set()


def test_lattice_period_variant_patch_must_actually_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(lattice, "_PERIOD_PATCH_FROM", "missing period guard")
    with pytest.raises(SystemExit, match="глобальный рубеж периода"):
        lattice._prep_source(str(tmp_path))


def test_lattice_variant_prep_opens_the_hundredfold_error(tmp_path):
    """ЦЕНА варианта «ПРЕДЛОГ», названная измерением, а не словом: он
    открывает ту самую ошибку в сто раз, ради которой писался закон Б2.
    Принятый закон эти входы отказывает."""
    prep = lattice._load(lattice._prep_source(str(tmp_path)), "k02lat_probe")
    for raw, unit in lattice.FORM_CASES:
        assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
        got = prep.parse_value(raw, unit, prep.default_domain(unit))
        assert got is not None, (raw, unit)
