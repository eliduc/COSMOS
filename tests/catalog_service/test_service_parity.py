# -*- coding: utf-8 -*-
"""Двенадцатая предпроверка выпуска — «паритет с кодом COSMOS» (0.13).

Служба не судит паритет сама: закон живёт в `catalog/tools/parity.py` и
зовётся ПОДПРОЦЕССОМ (движок читает таблицы решателя и код CDPM, а
служба продуктов не импортирует). Предмет этих стендов — не движок, а
служба: как она зовёт судью (папка, привязки, переменные среды), как
читает коды возврата и вердикт и что делает с каждым исходом:

* красный (`rc: 1`) — отказ `parity-red`; подтверждением НЕ снимается:
  версия, противоречащая таблицам решателя, не имеет права называться
  версией;
* «не судился» (инструмента рядом нет, код 2, вердикт не прочитан) —
  `parity-unconfirmed`, снимается `confirm_parity: true`;
* зелёный С ПРЕДУПРЕЖДЕНИЕМ (`skipped` с признаком `warning`, сегодня
  L08b «CDPM ещё не знает метрику …») — тоже `parity-unconfirmed`:
  молчаливо пропустить непроверенное выпуск не вправе.

Судья подменён заглушкой (`conftest.write_parity_tool`, §5 проекта):
настоящий движок дорожки А эти стенды ничему бы не научил, а его
свидетелей (Kotlin, CDPM) в tmp-копии каталога нет.

Ни одного числа живого каталога здесь нет: версия и отпечаток в словах
чек-листа сверяются с шапкой копии, а слова красного — с теми, что
заглушка положила в вердикт.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalog_service import assemble as asm
from catalog_service import releases
from catalog_service.errors import CODES, ServiceError
from tests.catalog_service.conftest import (GREEN_RULES, RED_RULES,
                                            WARNING_RULES, detail,
                                            folder_bytes, write_parity_tool)


def _release(svc, version="0.2.0", note="выпуск со стендом паритета", **kw):
    body = {"version": version, "note": note}
    body.update(kw)
    return svc.post("/api/versions/release", json=body)


def _row(got: dict) -> dict:
    return next(c for c in got["checks"] if c["key"] == "parity")


def _journal(svc) -> list[dict]:
    path = Path(svc.settings.journal_path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ═══════════════════════════════ зелёный вердикт

def test_зелёный_паритет_называет_вердикт_словами_и_числами(service,
                                                            git_repo, capsys):
    """Строка 12 чек-листа — вердикт судьи, а не пересказ службы: версия
    и отпечаток судимой папки, счётчики правил."""
    git_repo(service.folder)
    cat = service.catalog()
    got = service.get("/api/versions/release/checks").json()
    row = _row(got)
    assert row["n"] == 12 and row["ok"] is True and row["code"] is None
    assert row["title"] == "паритет с кодом COSMOS"
    assert cat.version() in row["words"]
    assert cat.fingerprint()[:12] in row["words"]
    assert got["confirm_parity_needed"] is False
    assert got["parity"]["ok"] is True and got["parity"]["rc"] == 0
    print(f"\n12: {row['words']}")


def test_служба_зовёт_судью_папкой_привязками_и_переменными(service, git_repo,
                                                            tmp_path,
                                                            monkeypatch,
                                                            capsys):
    """Двери продуктов читают `CREW_TEMPLATE_CATALOG` — переменную
    подпроцессу обязана отдать ЗОВУЩАЯ сторона (0.13, критика A 3.3):
    без неё правило L10 отвечало бы «двери читают другой каталог» не по
    делу. Файл привязок COSMOS — тот же, что у остальных проверок
    (`executors.product_paths`)."""
    git_repo(service.folder)
    log = tmp_path / "parity-call.json"
    monkeypatch.setenv("PARITY_STUB_LOG", str(log))
    assert service.get("/api/versions/release/checks").json()["ok"] is True
    call = json.loads(log.read_text(encoding="utf-8"))
    argv = call["argv"]
    assert Path(call["tool"]) == releases.parity_tool(service.folder), \
        "закон паритета зовётся из папки каталога, а не из дерева службы"
    assert argv[argv.index("--catalog") + 1] == str(service.folder)
    assert call["catalog_env"] == str(service.folder), \
        "CREW_TEMPLATE_CATALOG подпроцессу не отдан"
    assert call["utf8_env"] == "1"
    assert Path(call["bindings"]) == Path(service.settings.cosmos_bindings)
    assert call["quiet"] is True, "службе построчный отчёт судьи не нужен"
    assert Path(call["cwd"]) == Path(releases.repo_of(service.folder))
    assert "--json" in argv
    # Вердикт пишется во временную папку: дерево каталога остаётся чистым,
    # иначе следующая же предпроверка 6 отказала бы `git-dirty`.
    out = Path(argv[argv.index("--json") + 1])
    assert not str(out).startswith(str(service.folder))
    assert not out.exists(), "временный файл вердикта не убран"
    assert not releases.dirty_files(service.folder)
    print(f"\n12: судья зван {Path(call['tool']).name} над "
          f"{Path(argv[argv.index('--catalog') + 1]).name}, "
          f"CREW_TEMPLATE_CATALOG отдан, дерево чисто")


# ═══════════════════════════════ красный вердикт

def test_красный_паритет_отказывает_словами_и_не_пишет_ни_байта(make_service,
                                                                git_repo,
                                                                capsys):
    """`parity-red`: слова судьи доходят до владельца дословно (риск 6
    §8: свидетельство — копия вердикта, а не второй закон), и на диск не
    легло ничего."""
    svc = make_service("owner", parity=RED_RULES, parity_rc=1)
    git_repo(svc.folder)
    before = folder_bytes(svc.folder)
    got = svc.get("/api/versions/release/checks").json()
    row = _row(got)
    assert got["ok"] is False and row["ok"] is False
    assert row["code"] == "parity-red"
    assert RED_RULES["L05"]["words"] in row["words"]
    assert row["extra"]["parity"] == {"ok": False, "rc": 1, "warnings": []}
    # Красный подтверждением не снимается — признак говорит только о том,
    # что снимается полем confirm_parity.
    assert got["confirm_parity_needed"] is False
    r = _release(svc)
    assert r.status_code == 412
    words = detail(r)
    assert words["code"] == "parity-red"
    assert words["words"] == row["words"], \
        "чек-лист и отказ говорят одними словами — закон один"
    assert "подтверждением это не снимается" in words["words"]
    assert folder_bytes(svc.folder) == before
    assert svc.catalog().versions() == []
    print(f"\n12 красный: {words['words'][:120]}…")


def test_красный_паритет_не_снимается_подтверждением(make_service, git_repo):
    """`confirm_parity: true` — про «не судился» и предупреждение;
    красный вердикт им не отменяется ни на одном пути."""
    svc = make_service("owner", parity=RED_RULES, parity_rc=1)
    git_repo(svc.folder)
    with pytest.raises(ServiceError) as e:
        releases.release(svc.store, svc.app.state.journal,
                         version="0.2.0", note="проба",
                         confirm_parity=True, confirm_stop=True)
    assert e.value.code == "parity-red" and e.value.status == 412
    assert svc.catalog().versions() == []


def test_отказ_паритета_не_несёт_путей_машины(make_service, git_repo, capsys):
    """Судья упал следом с путями машины — наружу уходят слова, а не
    раскладка диска владельца (правило `errors.scrub`)."""
    svc = make_service("owner", parity=RED_RULES, parity_rc=1)
    write_parity_tool(svc.folder, rules=RED_RULES, rc=2, crash=True)
    git_repo(svc.folder)
    verdict = releases.parity_verdict(svc.settings)
    assert verdict["judged"] is False and verdict["ok"] is None
    assert str(svc.folder) not in verdict["words"]
    assert ":\\" not in verdict["words"] and "код 2" in verdict["words"]
    print(f"\n12 не судился: {verdict['words'][:140]}")


# ═══════════════════════════════ «не судился»

def test_судьи_рядом_нет_выпуск_требует_подтверждения(make_service, git_repo,
                                                      capsys):
    """Инструмента рядом с каталогом нет — это честный исход «не
    судился», а не молчаливый проход: строка чек-листа красная словами,
    выпуск отказывает, и снимается это только осознанным
    `confirm_parity`."""
    svc = make_service("owner", parity=False)
    git_repo(svc.folder)
    before = folder_bytes(svc.folder)
    got = svc.get("/api/versions/release/checks").json()
    row = _row(got)
    assert row["ok"] is False and row["code"] == "parity-unconfirmed"
    assert "tools/parity.py" in row["words"] and "не судился" in row["words"]
    assert "confirm_parity" in row["words"]
    assert got["ok"] is False and got["confirm_parity_needed"] is True
    r = _release(svc)
    assert r.status_code == 412 and detail(r)["code"] == "parity-unconfirmed"
    assert folder_bytes(svc.folder) == before
    # Осознанное продолжение владельца: выпуск делается, но исход в
    # журнале — «warning», и вердикт назван словами.
    out = releases.release(svc.store, svc.app.state.journal,
                           version="0.2.0", note="выпуск без судьи",
                           confirm_parity=True)
    assert out["version"] == "0.2.0" and out["confirmed_parity"] is True
    assert out["parity"]["ok"] is None
    row = [x for x in _journal(svc) if x["op"] == "release"][-1]
    assert row["outcome"] == "warning"
    assert "не судился" in row["words"] or "нет" in row["words"]
    assert "осознанное продолжение владельца" in row["words"]
    print(f"\n12 без судьи: {row['words'][:160]}")


def test_код_2_читается_как_не_судился(make_service, git_repo, capsys):
    """Договор команды: 0 — красных нет, 1 — есть красные, 2 — не
    судилось. Код 2 не делает выпуск разрешённым и не выдаёт себя за
    красный."""
    svc = make_service("owner", parity=False)
    write_parity_tool(svc.folder, rc=2, crash=True)
    git_repo(svc.folder)
    verdict = releases.parity_verdict(svc.settings)
    assert verdict["rc"] == 2 and verdict["ok"] is None
    assert releases.parity_confirm_needed(verdict) is True
    bad = releases._check_parity(verdict)
    assert bad is not None and bad.code == "parity-unconfirmed"
    assert releases._check_parity(verdict, confirm=True) is None
    print(f"\n12 код 2: {verdict['words'][:120]}")


# ═══════════════════════════════ предупреждение судьи

def test_предупреждение_судьи_требует_подтверждения(make_service, git_repo,
                                                    capsys):
    """L08b «CDPM ещё не знает метрику …» — `skipped` с признаком
    предупреждения: вердикт зелёный, но выпуск объявил бы версией то,
    чего судья не проверил (критика A 1.4). Отсюда — подтверждение."""
    svc = make_service("owner", parity=WARNING_RULES)
    git_repo(svc.folder)
    got = svc.get("/api/versions/release/checks").json()
    row = _row(got)
    ждали = WARNING_RULES["L08b"]["skipped"]
    assert row["ok"] is False and row["code"] == "parity-unconfirmed"
    assert ждали in row["words"] and "L08b" in row["words"]
    assert got["confirm_parity_needed"] is True
    assert any(ждали in w for w in got["warnings"])
    assert _release(svc).status_code == 412
    out = releases.release(svc.store, svc.app.state.journal,
                           version="0.2.0", note="выпуск с предупреждением",
                           confirm_parity=True)
    assert out["confirmed_parity"] is True and out["parity"]["ok"] is True
    assert any(ждали in w for w in out["warnings"])
    row = [x for x in _journal(svc) if x["op"] == "release"][-1]
    assert row["outcome"] == "warning" and "паритет" in row["words"]
    print(f"\n12 предупреждение: {row['words'][:160]}")


def test_пропуск_без_предупреждения_вердикт_не_красит(service, git_repo):
    """`skipped` без признака `warning` (L23 «папка без tools/») —
    честный пропуск: он не красит вердикт и подтверждения не требует."""
    git_repo(service.folder)
    verdict = releases.parity_verdict(service.settings)
    assert verdict["ok"] is True and verdict["warnings"] == []
    assert verdict["verdict"]["counts"]["skipped"] == 1
    assert releases.parity_confirm_needed(verdict) is False
    assert releases._check_parity(verdict) is None


# ═══════════════════════════════ зелёный выпуск и журнал

def test_зелёный_вердикт_попадает_в_журнал_выпуска(service, git_repo, capsys):
    """Чем версия сверена с кодом решателя, обязано быть видно из записи
    журнала, а не из памяти того, кто нажимал."""
    git_repo(service.folder)
    r = _release(service)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parity"]["ok"] is True and body["confirmed_parity"] is False
    row = [x for x in _journal(service) if x["op"] == "release"][-1]
    assert row["outcome"] == "ok"
    assert "паритет с кодом COSMOS зелёный" in row["words"]
    assert body["version"] in row["words"]
    print(f"\n12 в журнале: {row['words'][:160]}")


# ═══════════════════════════════ цепочка команд

def test_цепочка_начинается_с_refresh_и_кончается_паритетом():
    """0.14: и `NEXT_COMMANDS` сборки, и чек-лист после выпуска
    начинаются пересчётом посева и дельты, а кончаются законом
    паритета. Посев — проекция каталога: после сборки его правит
    инструмент, а не рука."""
    assert asm.NEXT_COMMANDS[0] == asm.REFRESH_COMMAND
    assert asm.NEXT_COMMANDS[-1] == asm.PARITY_COMMAND
    assert "--refresh --out catalog" in asm.REFRESH_COMMAND
    assert "parity.py" in asm.PARITY_COMMAND
    steps = releases.next_steps("0.2.1")
    assert steps[0].startswith(asm.REFRESH_COMMAND)
    assert steps[1].startswith('git add catalog && git commit -m "каталог: '
                               'выпуск 0.2.1"')
    commands = [s for s in steps if s.startswith(".venv")]
    assert commands[0].startswith(asm.REFRESH_COMMAND)
    assert commands[-1].startswith(asm.PARITY_COMMAND)
    # Release has already approved the raw binding hashes. Assembly still
    # builds evidence; the post-release path only checks that evidence.
    assert commands[1:-1] == [asm.CHAIN_COMMANDS[-1]]
    assert "0 красных" in commands[-1]
    # Слов «новая заморозка гейтов содержания» больше нет: база заморозки
    # выпуском не меняется (0.14).
    text = " | ".join(steps)
    assert "новая заморозка гейтов содержания" not in text
    assert "база заморозки" in text and "снимка по слову владельца" in text
    print(f"\nцепочка после выпуска: {len(steps)} строк, команд "
          f"{len(commands)}")


def test_коды_паритета_в_закрытом_перечне_службы():
    """Новый код отказа — правка закрытого перечня и контракта, а не
    строка в обработчике."""
    assert "parity-red" in CODES and "parity-unconfirmed" in CODES


# ═══════════════════════════ швы: ручка выпуска и настоящий закон

def test_подтверждение_паритета_снимает_отказ_ручкой_выпуска(make_service,
                                                             git_repo,
                                                             capsys):
    """Шов «служба ↔ HTTP»: поле `confirm_parity` обязано доходить до
    предпроверки 12 РУЧКОЙ, а не только вызовом функции в процессе.

    Без строки в `app.py::release_version` отказ `parity-unconfirmed`
    («судьи рядом нет», код 2, предел, предупреждение L08b) по HTTP снять
    нечем — на машине без `catalog/tools/parity.py` кнопка выпуска встала
    бы намертво. Красный паритет ручкой не снимается и здесь."""
    svc = make_service("owner", parity=False)
    git_repo(svc.folder)
    r = _release(svc)
    assert r.status_code == 412 and detail(r)["code"] == "parity-unconfirmed"
    r = _release(svc, confirm_parity=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["confirmed_parity"] is True and body["parity"]["ok"] is None
    row = [x for x in _journal(svc) if x["op"] == "release"][-1]
    assert row["outcome"] == "warning"
    print(f"\n12 ручкой: подтверждение прошло, исход журнала "
          f"{row['outcome']}")

    # Красный ручкой НЕ снимается: версия, противоречащая таблицам
    # решателя, не имеет права называться версией.
    svc2 = make_service("owner", parity=RED_RULES, parity_rc=1)
    git_repo(svc2.folder)
    r2 = _release(svc2, confirm_parity=True)
    assert r2.status_code == 412 and detail(r2)["code"] == "parity-red"
    print("12 ручкой: красный подтверждением не снят")


def test_служба_читает_вердикт_настоящего_закона_дерева(capsys):
    """Живой контрактный шов дорожек А и Б: `releases.parity_verdict`
    читает вердикт НАСТОЯЩЕГО `catalog/tools/parity.py` дерева, а не
    только заглушки этих стендов.

    Стенды службы стоят на заглушке намеренно (§5 проекта: движок читает
    таблицы решателя и код CDPM, службе он не нужен), но тогда никто не
    сверял бы форму заглушки с формой движка — а служба читает поля
    вердикта поимённо. Здесь судится ровно шов: судимая папка — живой
    каталог дерева, судья — движок дорожки А, читатель — служба.
    Ни одного числа живого каталога: версия и отпечаток берутся из
    шапки, счётчики только печатаются."""
    from catalog_service.settings import Settings

    root = Path(__file__).resolve().parents[2]
    law = root / "catalog" / "tools" / "parity.py"
    assert law.is_file(), f"закона паритета дерева нет: {law}"
    settings = Settings(catalog_dir=root / "catalog",
                        work_dir=root / "catalog",
                        cosmos_bindings=root / "vmp" / "bindings.json",
                        cdpm_bindings=root / "cdpm" / "bindings.json")
    verdict = releases.parity_verdict(settings)
    assert verdict["judged"] is True, verdict["words"]
    # Служба судит ЖИВОЙ каталог — так и надо: выпускают живой, а не
    # временный. Пока рядом с ним лежит кандидат ветки (`catalog/
    # candidates/**`), живой каталог законно красен: таблицы решателя
    # ветки знают запись, которую владелец ещё не принял. Рост таблиц
    # под кандидатом — судит закон паритета на временном каталоге
    # «живой ⊕ кандидаты» (решение 0.3 проекта паритета), и здесь
    # утверждается ШОВ (форма вердикта и поля, которые служба читает
    # поимённо), а не зелень. На чистом дереве кандидатов нет, и зелень
    # утверждается по-прежнему.
    ветка = (root / "catalog" / "candidates").is_dir() and any(
        (root / "catalog" / "candidates").rglob("*.json"))
    if ветка:
        print(f"\nрядом с живым каталогом кандидаты ветки — зелень живого "
              f"вердикта не утверждается: {verdict['words']}")
    else:
        assert verdict["ok"] is True, verdict["words"]
        assert verdict["rc"] == 0
    assert verdict["tool"] == "parity.py"
    doc = verdict["verdict"]
    head = json.loads((root / "catalog" / "catalog.json")
                      .read_text(encoding="utf-8"))
    assert doc["catalog"]["version"] == head["version"]
    assert doc["catalog"]["fingerprint"] == head["fingerprint"]
    # Номер версии служба называет в СЛОВАХ ЗЕЛЁНОГО вердикта; у красного
    # слова — сами красные правила (их и читает человек).
    if not ветка:
        assert head["version"] in verdict["words"]
    # Поля, которые служба читает поимённо (`_read_verdict`,
    # `_parity_warnings`, `_green_words`, блок `parity` ответа), обязаны
    # быть у ЖИВОГО вердикта, а не только у заглушки.
    assert set(doc) >= {"rc", "ok", "catalog", "counts", "rules", "words"}
    assert set(doc["counts"]) >= {"rules", "green", "red", "skipped",
                                  "warning"}
    if not ветка:
        assert doc["words"] is None and doc["counts"]["red"] == 0
        assert releases.parity_confirm_needed(verdict) is False
        assert releases._check_parity(verdict) is None
    # Форма заглушки этих стендов ≡ форме движка там, где служба читает:
    # разойдись они — стенды учили бы службу читать несуществующее.
    stub = {"rc": 0, "ok": True, "catalog": {"version": None,
                                             "fingerprint": None,
                                             "path": None},
            "counts": {"rules": 0, "green": 0, "red": 0, "skipped": 0,
                       "warning": 0},
            "rules": GREEN_RULES, "words": None}
    assert set(stub) == set(doc) - {"growth_judged"}, \
        "заглушка и движок разошлись формой вердикта"
    assert set(stub["catalog"]) <= set(doc["catalog"])
    assert set(stub["counts"]) == set(doc["counts"])
    for key, rule in GREEN_RULES.items():
        assert set(rule) <= {"ok", "words", "numbers", "skipped", "warning",
                             "beyond_request"}, key
    print(f"\nживой закон дерева: {verdict['words']}; правил "
          f"{doc['counts']['rules']} | зелёных {doc['counts']['green']} | "
          f"красных {doc['counts']['red']} | пропусков "
          f"{doc['counts']['skipped']}")


def test_на_площадке_судья_не_зовётся_словами(make_service, git_repo,
                                              tmp_path, monkeypatch, capsys):
    """Площадка (`--read-only`) и роль без права выпуска судью НЕ зовут.

    Проект §4: «служба на площадках выпусков не делает — предпроверка 12
    там не зовётся». Прежде `checks()` считал вердикт БЕЗУСЛОВНО, и
    каждое чтение чек-листа (диалог просит его до нажатия и заново при
    каждой правке номера версии) поднимало подпроцесс `.venv` и ждало
    до предела 120 с — в том числе там, где рядом может не быть кода
    COSMOS. Строка чек-листа при этом не молчит: она говорит, почему
    судьи не было, и подтверждения не требует — выпуск там остановит
    первая же строка (право роли)."""
    svc = make_service(None, read_only=True)
    git_repo(svc.folder)
    log = tmp_path / "parity-call.json"
    monkeypatch.setenv("PARITY_STUB_LOG", str(log))
    got = svc.get("/api/versions/release/checks").json()
    row = _row(got)
    assert not log.exists(), "судья зван на площадке только для чтения"
    assert row["ok"] is True and row["code"] is None
    assert "не судится" in row["words"] and "только чтение" in row["words"]
    assert got["confirm_parity_needed"] is False
    assert got["parity"]["ok"] is None
    print(f"\n12 на площадке: {row['words']}")


def test_у_владельца_судья_зовётся_как_прежде(service, git_repo, tmp_path,
                                              monkeypatch, capsys):
    """Обратная половина: у роли с правом выпуска подпроцесс судьи
    поднимается — «не зовётся» не должно стать тихим пропуском там, где
    выпуск возможен."""
    git_repo(service.folder)
    log = tmp_path / "parity-call.json"
    monkeypatch.setenv("PARITY_STUB_LOG", str(log))
    got = service.get("/api/versions/release/checks").json()
    assert log.exists(), "у владельца судья обязан быть зван"
    assert _row(got)["ok"] is True and got["parity"]["ok"] is True
    print(f"\n12 у владельца: {_row(got)['words']}")
