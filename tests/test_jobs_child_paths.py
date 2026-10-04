# -*- coding: utf-8 -*-
"""Пути входов, уходящие в дочерний процесс, — РАЗРЕШЁННЫЕ.

Дефект 2026-08-13 (найден владельцем на контуре заказчика): ростеринг из
интерфейса падал сразу — «required input does not exist:
final-report\\gerad_colgen_selected_...json», хотя каталог существовал и
его отпечаток в команде совпадал с файлом. Реестр хранит путь ЛОГИЧЕСКИ
(«final-report/x.json»), физически файл лежит в корне данных, а дочерний
процесс разворачивал голую относительную запись от своего рабочего
каталога — корня репозитория. Отпечаток при этом считался по правильному
файлу (`registry.resolve_rel`), то есть родитель знал верный путь и всё
равно передавал неверный.

Класс, а не точка: то же касается набора для этапа 1, colgen-линий и
мультироли. Гейт ниже перебирает ВСЕ команды и требует, чтобы каждый
файловый аргумент существовал ровно так, как записан.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def contour(monkeypatch, tmp_path):
    """Контур с ВЫНЕСЕННЫМИ данными: набор в репозитории, результаты — в
    корне данных (ровно топология контура заказчика)."""
    import _data_paths
    from webapp import jobs as jobs_mod, registry
    data_root = tmp_path / "data"
    repo_root = tmp_path / "repo"
    (data_root / "final-report").mkdir(parents=True)
    (data_root / "rules").mkdir(parents=True)
    (repo_root / "data" / "uploads").mkdir(parents=True)
    monkeypatch.setattr(_data_paths, "data_root", lambda: data_root)
    monkeypatch.setattr(registry, "REPO_ROOT", repo_root)
    monkeypatch.setattr(registry, "FINAL_REPORT", data_root / "final-report")
    payload = repo_root / "data" / "uploads" / "p.json"
    payload.write_text("{}", encoding="utf-8")
    catalog = data_root / "final-report" / "gerad_colgen_selected_xx-a.json"
    catalog.write_text(json.dumps({"coverage": {"verdict": "OK"}}),
                       encoding="utf-8")
    return jobs_mod, data_root, repo_root


def _inst() -> dict:
    # Пути ЛОГИЧЕСКИЕ — такими их хранит реестр.
    return {"id": "upload-x--xx", "label": "xx-a", "source": "upload",
            "payload_path": "data/uploads/p.json",
            "catalog_path": "final-report/gerad_colgen_selected_xx-a.json"}


def _file_args(cmd: list) -> list:
    return [a for a in cmd
            if isinstance(a, str) and a.lower().endswith((".json", ".md"))
            and not a.endswith(".py")]


def test_stage2_catalog_resolved_to_data_root(contour):
    jobs_mod, data_root, _repo = contour
    cmd = jobs_mod._build_commands("stage2", _inst(),
                                   {"budget_s": 60, "seed": 7})[0]
    cat = cmd[cmd.index("--catalog") + 1]
    assert Path(cat).is_absolute(), cat
    assert Path(cat) == data_root / "final-report" \
        / "gerad_colgen_selected_xx-a.json"
    assert Path(cat).exists()
    print(f"catalog resolved into the data root: {cat}")


def test_stage1_payload_resolved_to_repo_root(contour):
    jobs_mod, _data, repo_root = contour
    cmd = jobs_mod._build_commands("stage1", _inst(),
                                   {"budget_min": 1, "pairing_level": 3})[0]
    pay = cmd[cmd.index("--payload") + 1]
    assert Path(pay).is_absolute(), pay
    assert Path(pay) == repo_root / "data" / "uploads" / "p.json"
    assert Path(pay).exists()
    print(f"payload resolved into the repo root: {pay}")


def test_every_child_file_argument_exists(contour):
    """Гейт класса: ни одна команда не уносит путь, которого нет.

    Печатает, сколько команд и сколько путей рассмотрено, — прогон,
    ничего не проверивший, пройденным не считается."""
    jobs_mod, data_root, _repo = contour
    (data_root / "rules" / "postanovka.md").write_text(
        "# правила", encoding="utf-8")
    cases = [
        ("stage1", {"budget_min": 1, "pairing_level": 3}),
        ("stage1", {"budget_min": 1}),                 # colgen-линия
        ("stage2", {"budget_s": 60, "seed": 7}),
        ("multirole", {"budget_min": 1, "budget_s": 60, "seed": 7}),
    ]
    seen_cmds = seen_paths = 0
    for jtype, params in cases:
        for cmd in jobs_mod._build_commands(jtype, _inst(), params):
            seen_cmds += 1
            for arg in _file_args(cmd):
                assert Path(arg).exists(), (jtype, arg)
                seen_paths += 1
    print(f"commands examined: {seen_cmds}, file paths: {seen_paths}")
    assert seen_cmds == 4
    assert seen_paths >= 8      # набор в каждой + каталог + правила ×4


def test_missing_input_refuses_at_build(contour):
    # Пропавший каталог — отказ ЗДЕСЬ, с путём в сообщении, а не смерть
    # дочернего процесса кодом 2 через полминуты.
    jobs_mod, data_root, _repo = contour
    (data_root / "final-report"
     / "gerad_colgen_selected_xx-a.json").unlink()
    with pytest.raises(jobs_mod.JobError) as exc:
        jobs_mod._build_commands("stage2", _inst(),
                                 {"budget_s": 60, "seed": 7})
    text = str(exc.value)
    assert "каталог этапа 1" in text, text
    print("refused while building the command")


def test_absolute_paths_pass_through(contour):
    # Прежние сохранённые состояния хранят абсолютные строки — их
    # разрешатель принимает как есть.
    jobs_mod, data_root, repo_root = contour
    inst = _inst()
    inst["payload_path"] = str(repo_root / "data" / "uploads" / "p.json")
    cmd = jobs_mod._build_commands("stage2", inst,
                                   {"budget_s": 60, "seed": 7})[0]
    assert cmd[cmd.index("--payload") + 1] == inst["payload_path"]
    print("absolute path passed through unchanged")
