# -*- coding: utf-8 -*-
"""Повтор прогона из журнала (проект повтора, этап Р2): водитель на стенде
панели с поддельными каналами — рождение → извлечение → политика файлов →
аудит-цикл → повтор из журнала → артефакты байт в байт, вызовов сети нет,
источник не тронут; расхождения (запрос вне журнала, изменённый артефакт)
называются словами; доступность и коды отказов; остановка; гейт списка
нормализации; гейт секретов; детерминизм между процессами."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdpm import actions_journal as aj
from cdpm import call_journal as cj
from cdpm import file_replace as fr
from cdpm import replay as rp
from cdpm import webui
from test_cdpm_exchange_run import _make_auditor
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    cj.forget()
    aj.forget()
    aj.set_current(None)
    yield
    cj.forget()
    aj.forget()
    aj.set_current(None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def _create(client) -> str:
    r = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic"})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _busy(st: dict) -> bool:
    return any(str(st.get(k) or "").endswith(("идёт", "идут"))
               for k in ("state", "data_state", "smt_state"))


def _journal_open(run_dir: Path) -> list:
    """Номера действий журнала (`journal/actions.jsonl`), у которых есть
    строка `start`/`sync`, но ещё нет строки `end`/`refused`. Строку
    `end` фоновой стадии нить пишет уже ПОСЛЕ того, как освободит линию
    статуса (`webui._start_stage_thread`), и следом переписывает запись
    о прогоне и блок `resources` статуса — снимок папки, взятый по одной
    свободной линии, ловил этот хвост (плавающее падение полного прогона
    12.09.2026: PermissionError на `status.json.<pid>-<tid>.tmp`)."""
    rows = aj.read(run_dir)
    closed = {r.get("n") for r in rows if r.get("kind") in ("end", "refused")}
    return [r.get("n") for r in rows
            if r.get("kind") in ("start", "sync") and r.get("n") not in closed]


def _wait(client, run_id: str, timeout_s: float = 60.0) -> dict:
    """Стадия завершена: линии статуса свободны И у каждого действия
    журнала есть строка `end` (см. `_journal_open`)."""
    deadline = time.monotonic() + timeout_s
    run_dir = webui._run_dir(run_id)
    st: dict = {}
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        if not _busy(st) and not _journal_open(run_dir):
            return st
        time.sleep(0.1)
    raise AssertionError(f"стадия не завершилась: {st.get('state')!r}, "
                         f"действия без строки end: {_journal_open(run_dir)}")


def _wait_replay(client, run_id: str, timeout_s: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        rep = (st.get("passport") or {}).get("replay") or {}
        if rep.get("verdict"):
            return st
        time.sleep(0.1)
    raise AssertionError("повтор не завершился")


def _record_simple(client) -> str:
    run_id = _create(client)
    assert client.post(f"/api/runs/{run_id}/extract").status_code == 200
    st = _wait(client, run_id)
    assert st["state"] == "извлечено", st.get("error")
    return run_id


def _record_full(client, monkeypatch) -> str:
    run_id = _record_simple(client)
    assert client.put(f"/api/runs/{run_id}/files-config", json={
        "files": {"RULES-B.md": {"processing": "local"}}}).status_code == 200
    clean_auditor = _make_auditor([[], []])
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: clean_auditor)
    r = client.post(f"/api/runs/{run_id}/exchange",
                    params={"auditor": "cloud", "worker": "cloud"})
    assert r.status_code == 200, r.text
    st = _wait(client, run_id)
    assert st["state"] == "аудит-цикл: успех", st.get("error")
    return run_id


#: Снимок каталога прогона для проверки «источник не тронут». Статус и
#: его замок — указатель повтора, а `run-record.json` — ЛЕТОПИСЬ (шаг 1
#: записи о прогоне): её переписывает нить стадии уже ПОСЛЕ того, как
#: освободит линию статуса, — стенд ждёт линию и ловил бы запись на
#: полуслове. Тем же законом её исключает из сверки сам повтор
#: (`replay.EXCLUDE_FILES`): повтор судит артефакты и журнал, а запись
#: у потомка своя.
_TREE_SKIP = ("status.json", "status.lock", "run-record.json")
#: Временные файлы замены — `status.json.<pid>-<tid>.tmp`,
#: `run-record.json.tmp`, `manifest.json.<pid>-<tid>.tmp`
#: (`webui._write_status`, `run_record._write_json`, `call_journal`):
#: живут мгновение между записью и переименованием, и пока писатель
#: держит такой файл с правом DELETE, обычный `open()` читателя отвечает
#: PermissionError (нарушение совместного доступа, WinError 32 → errno 13)
#: — так падал снимок в полном прогоне 12.09.2026. Повтор их тоже не
#: судит (`replay.artifacts`).
_TMP_SUFFIX = ".tmp"


def _read_bytes(p: Path, window_s: float = 5.0) -> bytes:
    """Байты файла под писателем: FILE_SHARE_DELETE (нить стадии заменяет
    файл, не дожидаясь читателя) и повтор при PermissionError по времени,
    как у `webui._read_status`; после окна — исключение как есть."""
    deadline = time.monotonic() + window_s
    while True:
        try:
            return fr.read_bytes_shared(p)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def _files(run_dir: Path, skip=_TREE_SKIP):
    """Файлы папки прогона парами (путь относительно папки, байты): без
    имён из `skip` и без временных файлов замены (`_TMP_SUFFIX`)."""
    for p in run_dir.rglob("*"):
        if p.name in skip or p.name.endswith(_TMP_SUFFIX) or not p.is_file():
            continue
        yield p.relative_to(run_dir).as_posix(), _read_bytes(p)


def _tree(run_dir: Path, skip=_TREE_SKIP) -> dict:
    return {rel: hashlib.sha256(data).hexdigest()
            for rel, data in _files(run_dir, skip)}


def _zip_or_error(run_dir: Path):
    from cdpm import export_task_package as etp
    try:
        return etp.export_zip(run_dir), None
    except Exception as e:                                 # noqa: BLE001
        return None, str(e)


def test_replay_from_journal_matches_byte_for_byte(client, monkeypatch):
    src = _record_full(client, monkeypatch)
    src_dir = webui._run_dir(src)
    before = _tree(src_dir)
    n_calls = cj.count(src_dir)
    assert n_calls > 0
    st = client.get(f"/api/runs/{src}").json()
    assert st["replay"]["journal"] == {"available": True, "reason_code": None,
                                       "reason_words": None}
    assert st["replay"]["fresh"]["available"] is True
    assert st["replay"]["of"] is None
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    got = r.json()
    child = got["run"]
    assert child != src and got["mode"] == "replay-journal"
    assert got["source_run"] == src and got["state"] == "повтор идёт"
    st = _wait_replay(client, child)
    rep = st["passport"]["replay"]
    assert rep["verdict"] == "совпадает", rep
    assert rep["source_run"] == src and rep["mode"] == "replay-journal"
    assert rep["report"] == "journal/replay-report.json"
    assert rep["started_at"] and rep["finished_at"]
    assert rep["source_made_by"]["run"] == src
    assert st["replay"]["of"]["verdict"] == "совпадает"
    assert st["replay"]["journal"]["reason_code"] == "replay-of-replay"
    assert st["replay"]["progress"] is None
    assert st["title"].startswith("Повтор из журнала: ")
    assert st["state"] == "аудит-цикл: успех"
    report = client.get(f"/api/runs/{child}/replay-report").json()
    assert report["source_run"] == src and report["mode"] == "replay-journal"
    report = report["report"]
    assert report["schema"] == rp.SCHEMA and report["verdict"] == "совпадает"
    assert report["first_divergence"] is None
    assert report["calls"]["source"] == n_calls
    assert report["calls"]["served_from_journal"] == n_calls
    assert report["calls"]["fresh"] == 0 and report["calls"]["unconsumed"] == 0
    assert report["actions"] == {"replayed": 3, "skipped": [], "stopped": False}
    assert report["environment"]["differs"] == []
    assert all(row["same"] for row in report["artifacts"]), [
        r for r in report["artifacts"] if not r["same"]]
    paths = [row["path"] for row in report["artifacts"]]
    assert "extract/model.json" in paths and "package.zip" in paths
    assert "status:state" in paths and "status:extract_counters" in paths
    assert paths.index("freeze/manifest.json") < paths.index("extract/model.json")
    assert not any(p.startswith("journal/") or p.startswith("extract-cache/")
                   for p in paths)
    # Потомок: все вызовы из журнала, живых нет; стадии — «ответы записанные».
    child_dir = webui._run_dir(child)
    idx = [row for row in cj.read_index(child_dir) if row.get("kind") != "end"]
    assert idx and all(row["served_from"] == "journal" for row in idx)
    assert cj.manifest(child_dir)["mode"] == "replay-journal"
    assert cj.manifest(child_dir)["source_run"] == src
    assert all(s["calls_code"] == "replayed-from-journal"
               for s in st["passport"]["stages"])
    assert st["passport"]["journals"]["calls"] == n_calls
    # Источник не тронут (кроме указателя в статусе).
    assert _tree(src_dir) == before
    src_st = client.get(f"/api/runs/{src}").json()
    assert src_st["replays"] == [{"run": child, "mode": "replay-journal",
                                  "verdict": "совпадает"}]
    listed = {x["run"]: x for x in client.get("/api/runs").json()}
    assert listed[child]["replay_of"] == {"mode": "replay-journal",
                                          "source_run": src,
                                          "verdict": "совпадает"}
    assert listed[src]["replays"][0]["run"] == child
    assert listed[src]["replay_of"] is None
    # Пакет потомка — байт в байт с исходным (made_by из снимка, 2.15).
    zs, es = _zip_or_error(src_dir)
    zc, ec = _zip_or_error(child_dir)
    assert (zs, es) == (zc, ec)
    if zc is not None:
        from cdpm import export_task_package as etp
        made = json.loads(etp.build_package(child_dir)["manifest.json"])["made_by"]
        assert made["run"] == src
    # Отчёт источника — не повтор.
    r = client.get(f"/api/runs/{src}/replay-report")
    assert r.status_code == 404 and r.json()["reason_code"] == "not-a-replay"


def test_release_gate_check_is_answered_from_source_not_replayed(
        client, monkeypatch):
    """Рубеж непротиворечивости при выпуске пакета (11.09.2026) пишет в
    журнал СИНХРОННОЕ действие «smt-verify» (то же имя, что у кнопки, с
    происхождением «гейт выпуска пакета»). Повтор его не играет: он
    берёт вердикт ИСТОЧНИКА — тем же законом, каким берёт его сам рубеж
    (своды байт в байт, Ф-1.7).

    Играть заново было бы нечестно: проверка позвала бы модель,
    переписала бы `extract/smt-report.json` СВОИМ моментом проверки и
    заняла бы линию SMT статуса, которой у источника нет, — повтор
    разошёлся бы с источником на ровном месте."""
    from cdpm import smt_verify as sv
    src = _record_full(client, monkeypatch)
    src_dir = webui._run_dir(src)
    # Источник выпускал пакет: у него отчёт о СВОЁМ своде и действие
    # рубежа в журнале (стенд повтора пакет не собирает — у его правил
    # нет шаблонов каталога, — поэтому след рубежа кладётся руками).
    model = (src_dir / "extract" / "model.json").read_bytes()
    sha = sv.model_fingerprint(model)
    sv.write_report(src_dir, {
        "schema_version": sv.REPORT_SCHEMA,        # 12.09.2026 (A01)
        "model_sha256": sha, "checked_at": "2026-09-11T09:00:00+02:00",
        "verdict": "sat", "core": [], "n_rules": 1, "n_translated": 1,
        "n_untranslatable": 0, "untranslated": []})
    n = aj.note(src_dir, "smt-verify",
                params={"scope": None, "origin": aj.SMT_GATE_ORIGIN},
                sync=True, state_before=None)
    aj.set_current(None)
    got = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert got.status_code == 202, got.text
    child = got.json()["run"]
    child_dir = webui._run_dir(child)
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "совпадает", \
        client.get(f"/api/runs/{child}/replay-report").json()
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    assert report["actions"]["skipped"] == [
        {"n": n, "action": "smt-verify", "code": rp.SMT_GATE_CODE}]
    assert report["first_divergence"] is None
    assert all(row["same"] for row in report["artifacts"]), [
        r for r in report["artifacts"] if not r["same"]]
    # Вердикт потомка — вердикт источника, байт в байт; модель не звалась
    # и линия SMT статуса не занималась.
    assert (child_dir / "extract" / "smt-report.json").read_bytes() \
        == (src_dir / "extract" / "smt-report.json").read_bytes()
    assert client.get(f"/api/runs/{child}").json().get("smt_state") is None
    assert report["calls"]["fresh"] == 0 and report["calls"]["unconsumed"] == 0


def test_replay_names_prompt_divergence_when_journal_lacks_request(client):
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    # Порча журнала источника: одна запись извлечения исчезла — у повтора
    # тот запрос без ответа (как после изменения задания модели кодом).
    idx_path = cj.index_path(src_dir)
    lines = idx_path.read_bytes().decode("utf-8").splitlines()
    assert len(lines) >= 2
    idx_path.write_bytes(("\n".join(lines[:1] + lines[2:]) + "\n").encode("utf-8"))
    cj.forget(src_dir)
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "расходится"
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    fd = report["first_divergence"]
    assert fd["kind"] == "prompt" and fd["action"] == "extract"
    assert fd["stage"] == "извлечение" and "extractor" in fd["role"]
    assert "в журнале источника нет" in fd["words"]
    assert report["actions"]["replayed"] == 0
    assert str(st["state"]).startswith("отказ")
    src_st = client.get(f"/api/runs/{src}").json()
    assert src_st["replays"][0]["verdict"] == "расходится"


def test_replay_names_first_differing_artifact_by_pipeline_order(client):
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    # Артефакт источника подменён (как если бы код сборки свода изменился).
    svod = src_dir / "extract" / "svod.md"
    svod.write_bytes(svod.read_bytes() + "лишняя строка у источника\n".encode("utf-8"))
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "расходится"
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    fd = report["first_divergence"]
    assert fd["path"] == "extract/svod.md" and fd["kind"] == "text"
    assert fd["line"] and fd["source"] == "лишняя строка у источника"
    assert fd["replay"] is None                       # у повтора строки нет
    rows = {row["path"]: row for row in report["artifacts"]}
    assert rows["extract/svod.md"]["same"] is False
    assert rows["extract/model.json"]["same"] is True
    assert report["calls"]["fresh"] == 0


def test_availability_codes(client, monkeypatch):
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    # Режим вне словаря — 400.
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "кривой"})
    assert r.status_code == 400
    # Прогон до журнала: «из журнала» — слова, «заново» — по восстановленной
    # цепочке (Р3).
    other = _create(client)
    aj.path_of(webui._run_dir(other)).unlink()
    r = client.post(f"/api/runs/{other}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "pre-journal"
    st = client.get(f"/api/runs/{other}").json()
    assert st["replay"]["journal"]["reason_code"] == "pre-journal"
    assert st["replay"]["fresh"]["available"] is True
    assert st["replay"]["fresh"]["estimate"]["reconstructed"] is True
    # Входы источника пропали.
    doc = src_dir / "src" / "RULES-A.md"
    body = doc.read_bytes()
    doc.unlink()
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "inputs-missing"
    assert "src/RULES-A.md" in r.json()["detail"]
    doc.write_bytes(body + b"\n")
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and "отпечаток" in r.json()["detail"]
    doc.write_bytes(body)
    # Прогон занят стадией.
    webui._update_status(src_dir, state="извлечение идёт")
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "run-busy"
    webui._update_status(src_dir, state="извлечено")
    # Выключено площадкой.
    monkeypatch.setenv(rp.ENV_DISABLED, "0")
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "disabled-by-site"
    monkeypatch.delenv(rp.ENV_DISABLED)
    # Журнал неполон (тела отключены на записи) — только «из журнала».
    monkeypatch.setenv(cj.ENV_BODIES, "0")
    degraded = _record_simple(client)
    monkeypatch.delenv(cj.ENV_BODIES)
    r = client.post(f"/api/runs/{degraded}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "journal-incomplete"
    # Повтор повтора запрещён.
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202
    child = r.json()["run"]
    _wait_replay(client, child)
    r = client.post(f"/api/runs/{child}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "replay-of-replay"
    # Отчёта у потомка до завершения нет — «повтор идёт»/«не завершил».
    r = client.get(f"/api/runs/{src}/replay-report")
    assert r.status_code == 404


def test_stop_interrupts_replay(client):
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    got = rp.start(src_dir, "journal", autostart=False)
    child_dir = webui._run_dir(got["run"])
    webui._control_path(child_dir).write_text(json.dumps({"command": "stop"}),
                                              encoding="utf-8")
    report = rp.drive(child_dir)
    assert report["verdict"] == "прервано"
    assert report["actions"] == {"replayed": 0, "skipped": [], "stopped": True}
    assert webui._read_status(child_dir)["passport"]["replay"]["verdict"] == "прервано"
    assert webui._read_status(src_dir)["replays"][0]["verdict"] == "прервано"
    assert rp.read_report(child_dir)["verdict"] == "прервано"


def test_normalize_list_is_sufficient(client, monkeypatch):
    """Гейт: в артефактах стендового прогона нет меток времени панели,
    номеров прогонов и абсолютных путей вне явного списка нормализации."""
    src = _record_full(client, monkeypatch)
    src_dir = webui._run_dir(src)
    iso = re.compile(r"20\d\d-\d\d-\d\dT\d\d:\d\d")
    run_no = re.compile(r"\brun-\d{4}\b")
    abspath = re.compile(r"[A-Za-z]:\\\\|[A-Za-z]:/|/home/|/Users/")
    offenders = []
    for rel in rp.artifacts(src_dir):
        if rel in rp.NORMALIZE or rel.startswith("src/"):
            continue
        try:
            text = (src_dir / rel).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        for name, rx in (("метка времени", iso), ("номер прогона", run_no),
                         ("абсолютный путь", abspath)):
            m = rx.search(text)
            if m:
                offenders.append(f"{rel}: {name} «{text[max(0, m.start()-20):m.end()+20]}»")
    assert offenders == [], offenders


def test_replay_carries_no_environment_secrets(client, monkeypatch):
    secret = "s3cr3t-token-value-0xDEADBEEF"
    monkeypatch.setenv("CDPM_TEST_API_TOKEN", secret)
    src = _record_simple(client)
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202
    child = r.json()["run"]
    _wait_replay(client, child)
    child_dir = webui._run_dir(child)
    for rel, data in _files(child_dir, skip=()):
        if rel.endswith(".gz"):
            import gzip
            data = gzip.decompress(data)
        assert secret.encode("utf-8") not in data, rel


def test_record_and_replay_in_different_processes(tmp_path):
    """2.13: запись при PYTHONHASHSEED=0, повтор из журнала при
    PYTHONHASHSEED=1 — артефакты байт в байт."""
    script = REPO / "tests" / "_cdpm_replay_driver.py"
    runs_root = tmp_path / "runs"
    env = {k: v for k, v in os.environ.items()
           if k not in ("CDPM_RUNS_DIR", "CDPM_SETTINGS_FILE")}
    env["PYTHONHASHSEED"] = "0"
    out = subprocess.run([sys.executable, str(script), "record", str(runs_root)],
                         env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600,
                         cwd=str(REPO))
    assert out.returncode == 0, out.stderr[-2000:]
    rec = json.loads(out.stdout.strip().splitlines()[-1])
    assert rec["state"] == "извлечено", rec
    env["PYTHONHASHSEED"] = "1"
    out = subprocess.run([sys.executable, str(script), "replay", str(runs_root),
                          rec["run"]],
                         env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600,
                         cwd=str(REPO))
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["verdict"] == "совпадает", got
    assert got["calls"]["fresh"] == 0


# ------------------------------------------------- Р3: «повтор заново»

def test_fresh_replay_compares_outcomes_same_answers(client, monkeypatch):
    """«Повтор заново» (Р3): те же входы и раскладка, вызовы моделей
    заново (поддельный канал отвечает так же) — итоги совпадают, вердикт
    эквивалентности EQUIVALENT, вызовы у потомка живые (из журнала — 0),
    пакет потомка свой (made_by потомка)."""
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    st = client.get(f"/api/runs/{src}").json()
    fresh = st["replay"]["fresh"]
    assert fresh["available"] is True and fresh["reason_code"] is None
    est = fresh["estimate"]
    assert est["calls"] == cj.count(src_dir) and est["stages"] == ["extract"]
    assert est["duration_s"] is not None and est["reconstructed"] is False
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "fresh"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    assert r.json()["mode"] == "replay-fresh"
    st = _wait_replay(client, child)
    rep = st["passport"]["replay"]
    assert rep["verdict"] == "итоги совпадают", rep
    assert rep["mode"] == "replay-fresh" and rep["source_made_by"] is None
    assert st["title"].startswith("Повтор заново: ")
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    assert report["verdict"] == "итоги совпадают"
    assert report["artifacts"] == [] and report["first_divergence"] is None
    assert report["note"] and "по итогам" in report["note"]
    names = [row["name"] for row in report["outcomes"]]
    assert "правил по видам" in names and "вопросов всего" in names
    # Пакет у извлечения без готовности не собирается — строка «пакет»
    # с причиной; у готового прогона — строки манифеста.
    assert any(n == "пакет" or n.startswith("манифест: ") for n in names)
    assert "эквивалентность ВМП" in names
    assert all(row["same"] for row in report["outcomes"]), [
        row for row in report["outcomes"] if not row["same"]]
    assert report["equivalence"]["verdict"] == "EQUIVALENT"
    assert report["calls"]["served_from_journal"] == 0
    assert report["calls"]["fresh"] == cj.count(src_dir) > 0
    child_dir = webui._run_dir(child)
    assert cj.manifest(child_dir)["mode"] == "replay-fresh"
    assert all(s["calls_code"] == "live" for s in st["passport"]["stages"])
    from cdpm import export_task_package as etp
    try:
        made = json.loads(etp.build_package(child_dir)["manifest.json"])["made_by"]
        assert made["run"] == child                    # пакет потомка — свой
    except etp.TaskPackageError:
        pass                                           # постановка не готова — законно
    src_st = client.get(f"/api/runs/{src}").json()
    assert src_st["replays"][-1] == {"run": child, "mode": "replay-fresh",
                                     "verdict": "итоги совпадают"}


def test_fresh_replay_names_first_differing_outcome(client, monkeypatch):
    """Модель ответила иначе — итоги расходятся, первая строка названа
    (число правил), эквивалентность NOT_EQUIVALENT с перечнем."""
    src = _record_simple(client)

    def fewer(prompt: str) -> str:
        out = fake_llm(prompt)
        try:
            got = json.loads(out)
        except ValueError:
            return out
        if isinstance(got, dict) and isinstance(got.get("candidates"), list) \
                and len(got["candidates"]) > 1:
            got["candidates"] = got["candidates"][:-1]
            return json.dumps(got, ensure_ascii=False)
        return out
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fewer)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fewer)
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "fresh"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "итоги расходятся"
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    fd = report["first_divergence"]
    assert fd["kind"] == "outcome" and fd["name"] == "правил по видам"
    assert fd["source"] != fd["replay"]
    assert report["equivalence"]["verdict"] == "NOT_EQUIVALENT"
    assert report["equivalence"]["problems"]
    bad = [row["name"] for row in report["outcomes"] if not row["same"]]
    assert "правил по видам" in bad and "эквивалентность ВМП" in bad


def test_pre_journal_run_replays_fresh_by_reconstructed_chain(client):
    """Прогон до журнала: «из журнала» недоступен (ответов нет), «заново»
    идёт по цепочке, восстановленной из артефактов (рождение → извлечение),
    и отчёт так и говорит."""
    src = _record_simple(client)
    src_dir = webui._run_dir(src)
    aj.path_of(src_dir).unlink()
    import shutil
    shutil.rmtree(cj.journal_dir(src_dir))
    cj.forget(src_dir)
    aj.forget(src_dir)
    st = client.get(f"/api/runs/{src}").json()
    assert st["replay"]["journal"]["reason_code"] == "pre-journal"
    fresh = st["replay"]["fresh"]
    assert fresh["available"] is True
    assert "восстановлена" in fresh["reason_words"]
    assert fresh["estimate"]["reconstructed"] is True
    assert fresh["estimate"]["calls"] is None and fresh["estimate"]["stages"] == ["extract"]
    acts = rp.reconstruct_actions(src_dir, webui._read_status(src_dir))
    assert [a["action"] for a in acts] == ["create", "extract"]
    assert acts[0]["params"]["customer"] == "Полярис (тест)"
    assert [f["name"] for f in acts[0]["files"]] == ["RULES-A.md", "RULES-B.md"]
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 409 and r.json()["reason_code"] == "pre-journal"
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "fresh"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "итоги совпадают"
    assert st["passport"]["replay"]["reconstructed"] is True
    assert "actions" not in st["passport"]["replay"]
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    assert report["reconstructed"] is True
    assert report["actions"]["replayed"] == 1


def test_journal_replay_serves_birth_vision_call(client, monkeypatch, tmp_path):
    """Живой урок run-0088 → run-0090: вызов зрения при рождении потомка
    берётся из журнала источника ДО водителя, и повторный set_mode
    водителя не возвращает отданную запись — неизрасходованных нет,
    вердикт «совпадает»; манифест пакета потомка несёт имя источника."""
    from test_cdpm_freeze_image import _GOOD_REPLY, _make_png
    seen: list = []

    def fake_vision(scope, requested=None, **kw):
        def fn(prompt, image_path):
            seen.append(str(image_path))
            return _GOOD_REPLY
        return fn
    monkeypatch.setattr(webui, "vision_llm_factory", fake_vision)
    png = tmp_path / "skan.png"
    _make_png(png)
    r = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("skan.png", png.read_bytes(), "image/png")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic",
               "cloud_images": "true"})
    assert r.status_code == 200, r.text
    src = r.json()["run"]
    src_dir = webui._run_dir(src)
    assert len(seen) == 1                                # зрение — один раз
    idx = [x for x in cj.read_index(src_dir) if x.get("kind") != "end"]
    assert idx and idx[0]["kind"] == "vision" and idx[0]["action_n"] == 1
    assert client.post(f"/api/runs/{src}/extract").status_code == 200
    st = _wait(client, src)
    assert st["state"] == "извлечено", st.get("error")
    n_calls = cj.count(src_dir)
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "journal"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert len(seen) == 1                                # зрение потомка — из журнала
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    assert report["verdict"] == "совпадает", report["first_divergence"]
    assert report["calls"] == {**report["calls"], "source": n_calls,
                               "served_from_journal": n_calls,
                               "fresh": 0, "unconsumed": 0}
    child_idx = [x for x in cj.read_index(webui._run_dir(child))
                 if x.get("kind") != "end"]
    assert child_idx[0]["kind"] == "vision" and child_idx[0]["served_from"] == "journal"


def _answer_book(path, answered: dict[str, str]):
    """Книга ответов с одним листом части 2: заголовки анкеты и по строке
    на вопрос (идентификатор в первой колонке, ответ — в колонке ответа)."""
    from openpyxl import Workbook
    from cdpm import anketa_xlsx
    wb = Workbook()
    ws = wb.active
    ws.title = anketa_xlsx.SHEET_TITLES["2"]
    ws.append(list(anketa_xlsx.HEADERS))
    for qid, raw in answered.items():
        row = [qid, "вопрос", "choice", "", ""]
        row.append(raw)
        ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def _pre_journal_run(tmp_path, answered_by_rev: dict[str, dict[str, str]]):
    run_dir = tmp_path / "run-0001"
    (run_dir / "src").mkdir(parents=True)
    (run_dir / "src" / "RULES.md").write_text("# правила\n", encoding="utf-8")
    (run_dir / "extract").mkdir()
    (run_dir / "extract" / "model.json").write_text("{}", encoding="utf-8")
    for rev, answered in answered_by_rev.items():
        _answer_book(run_dir / "answers" / rev / "anketa-otvety.xlsx", answered)
    (run_dir / "data-src").mkdir()
    (run_dir / "data-src" / "data.xlsx").write_bytes(b"xlsx")
    (run_dir / "data-report").mkdir()
    return run_dir


def test_reconstruct_puts_data_line_before_book_answering_data_question(tmp_path):
    """Книга ответов с ответом на вопрос линии данных (`Q-2-дл-…`) заведомо
    подана после сверки данных: восстановленная цепочка ставит книги данных
    и сверку перед ней (находка COSMOS 08.09: иначе при повторе ответ
    пропадал как «ответ на неизвестный вопрос»). Книга без таких ответов
    остаётся перед линией данных."""
    run_dir = _pre_journal_run(tmp_path, {
        "rev-001": {"Q-1-старт": "а"},
        "rev-002": {"Q-2-дл-ДЛ-1": "б"},
    })
    acts = rp.reconstruct_actions(run_dir, {})
    assert [a["action"] for a in acts] == [
        "create", "extract", "answers", "data", "data-run", "answers"]
    assert acts[2]["files"][0]["stored"] == "answers/rev-001/anketa-otvety.xlsx"
    assert acts[5]["files"][0]["stored"] == "answers/rev-002/anketa-otvety.xlsx"
    assert all(a["reconstructed"] for a in acts)


def test_reconstruct_treats_history_and_data_questions_as_data_line(tmp_path):
    """Вопросы кода по книге данных (`Q-3b-история-…`, `Q-3a/3b-данные-…`,
    `Q-3a/3b-нет-данных-…`) рождаются на сверке данных — книга с ответом
    на них тоже встаёт после линии данных (живой случай 08.09 на Test:
    run-0015 пропускал ответ на `Q-3b-история-нет`). Вопрос о целевой
    (`Q-3b-GOAL`) строит код на извлечении — он линии данных не
    принадлежит."""
    from cdpm import answers as answers_mod
    assert answers_mod.is_data_line_question("Q-3b-история-нет")
    assert answers_mod.is_data_line_question("Q-3a-данные-состав")
    assert answers_mod.is_data_line_question("Q-3b-нет-данных-квалификации")
    assert answers_mod.is_data_line_question("Q-2-дл-ДЛ-1")
    assert not answers_mod.is_data_line_question("Q-3b-GOAL")
    assert not answers_mod.is_data_line_question("Q-1-старт")
    run_dir = _pre_journal_run(tmp_path, {
        "rev-001": {"Q-3b-GOAL": "нет критерия"},
        "rev-002": {"Q-3b-история-нет": "истории нет, начинаем с чистого"},
    })
    acts = rp.reconstruct_actions(run_dir, {})
    assert [a["action"] for a in acts] == [
        "create", "extract", "answers", "data", "data-run", "answers"]


def test_reconstruct_keeps_pipeline_order_without_data_line_answers(tmp_path):
    """Без ответов на вопросы линии данных порядок — порядок конвейера:
    ответы, затем книги данных и сверка (прежнее поведение)."""
    run_dir = _pre_journal_run(tmp_path, {"rev-001": {"Q-1-старт": "а"}})
    acts = rp.reconstruct_actions(run_dir, {})
    assert [a["action"] for a in acts] == [
        "create", "extract", "answers", "data", "data-run"]


def test_reconstruct_unreadable_book_keeps_pipeline_order(tmp_path):
    """Нечитаемая книга не ломает восстановление: порядок конвейера,
    книга уходит в цепочку как есть и откажет словами при применении."""
    run_dir = _pre_journal_run(tmp_path, {})
    bad = run_dir / "answers" / "rev-001" / "anketa-otvety.xlsx"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a workbook")
    acts = rp.reconstruct_actions(run_dir, {})
    assert [a["action"] for a in acts] == [
        "create", "extract", "answers", "data", "data-run"]


def test_fresh_replay_continues_after_state_divergence(client, monkeypatch):
    """Повтор «заново» (живой урок 09.09, run-0099): расхождение СТРОКИ
    состояния недетерминированной стадии («…; серьёзных в бэклоге: 5»
    после цикла) не прекращает цепочку — запоминается первое, все
    ложатся в отчёт, итоги судятся по концу; стоп — только отказ, ошибка,
    таймаут и кнопка. Раньше водитель обрывал повтор, и потомок оставался
    без линии данных и ответов (пакет stop/stop)."""
    src = _record_full(client, monkeypatch)
    src_dir = webui._run_dir(src)
    # Источник «помнит» иную строку состояния после извлечения.
    jp = aj.path_of(src_dir)
    lines = jp.read_bytes().decode("utf-8").splitlines()
    out = []
    n_extract = next(a["n"] for a in aj.actions(src_dir)
                     if a.get("action") == "extract")
    for ln in lines:
        rec = json.loads(ln)
        if rec.get("kind") == "end" and rec.get("n") == n_extract \
                and isinstance(rec.get("state_after"), dict):
            rec["state_after"]["state"] = "извлечено; серьёзных в бэклоге: 5"
        out.append(json.dumps(rec, ensure_ascii=False))
    aj.forget()
    jp.write_bytes(("\n".join(out) + "\n").encode("utf-8"))
    # Повтор зовёт модели заново: извлечение — тем же поддельным каналом,
    # аудит — свежим чистым аудитором (сценарий записи исчерпан
    # источником); канал ролей один, различаем по заданию.
    fresh_auditor = _make_auditor([[], []])

    def channel(prompt: str) -> str:
        if "Клаузы документа" in prompt:
            return fresh_auditor(prompt)
        return fake_llm(prompt)
    monkeypatch.setattr(webui, "llm_factory", lambda scope: channel)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: channel)
    r = client.post(f"/api/runs/{src}/replay", json={"mode": "fresh"})
    assert r.status_code == 202, r.text
    child = r.json()["run"]
    st = _wait_replay(client, child)
    assert st["passport"]["replay"]["verdict"] == "итоги расходятся"
    assert st["state"] == "аудит-цикл: успех"          # цепочка дошла до конца
    report = client.get(f"/api/runs/{child}/replay-report").json()["report"]
    fd = report["first_divergence"]
    assert fd["kind"] == "state" and fd["action"] == "extract"
    assert fd["source"] == "извлечено; серьёзных в бэклоге: 5" \
        and fd["replay"] == "извлечено"
    # Все три действия источника (извлечение, политика файлов, цикл)
    # исполнены — цепочка не оборвалась на расхождении.
    assert report["actions"]["replayed"] == 3 and not report["actions"]["stopped"]
    assert [d["action"] for d in report["state_divergences"]] == ["extract"]
    assert report["outcomes"], "итоги строятся и после расхождения по пути"
    assert all(row["same"] for row in report["outcomes"])
