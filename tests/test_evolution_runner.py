"""Evolution orchestrator: scan parsing, phase state machines and the
data-contour guarantees — all subprocesses faked through the single
_run() seam, no network, no real git/benchmarks.
"""
import json
import os
import time
from pathlib import Path

import pytest

from webapp import evolution, evolution_runner


def _countersign(monkeypatch, imp_id: int, phase: str) -> str:
    """Подтверждение человека для фазы продвижения.

    Продвижение перестало быть автоматическим (аудит R9, P0-04, пакет C):
    фаза не стартует без подписи, привязанной к правке, срезу и фазе.
    Сценарные тесты подписывают ровно тот срез, который записан у правки, —
    так же, как это делает оператор через tools/countersign.py.
    """
    monkeypatch.setenv(evolution_runner.COUNTERSIGN_ENV, "test-secret")
    head = evolution_runner.promotion_challenge(imp_id, phase)
    return evolution_runner.countersign_expected(imp_id, head, phase)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    monkeypatch.setattr(evolution_runner, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(evolution_runner, "LOG_PATH",
                        tmp_path / "cfg" / "evolution.log")
    monkeypatch.setattr(evolution_runner, "LAST_SCAN_PATH",
                        tmp_path / "cfg" / "last-scan.txt")
    # Отметку «когда наблюдали» пишет КАЖДЫЙ скан, в том числе неудачный:
    # без изоляции прогон тестов оставлял её в рабочем webapp/config, и
    # сводка контуров показывала бы время pytest вместо времени скана.
    monkeypatch.setattr(evolution_runner, "LAST_SCAN_AT_PATH",
                        tmp_path / "cfg" / "last-scan-at.txt")
    monkeypatch.setenv("COSMOS_TEST_DIR", str(tmp_path / "test-repo"))
    monkeypatch.setenv("COSMOS_STAGE_DIR", str(tmp_path / "stage-repo"))
    monkeypatch.setenv("COSMOS_PROD_DIR", str(tmp_path / "prod-repo"))
    # Эти тесты проверяют логику фаз, а не изоляцию — её отдельные тесты.
    # Заглушка ЯВНАЯ: честная isolation_problems() теперь никогда не
    # возвращает пустой список (запуск под отдельной записью в коде не
    # реализован), и объявленной переменной больше недостаточно.
    monkeypatch.setattr(evolution_runner, "isolation_problems", lambda: [])
    # Ключ подписи свидетельств (пятый аудит, C1) — в песочницу: иначе
    # тесты создали бы его в рабочей конфигурации Prod.
    from webapp import evidence
    monkeypatch.setenv("COSMOS_EVIDENCE_KEY", str(tmp_path / "evidence.key"))
    monkeypatch.setattr(evidence, "CONFIG_DIR", tmp_path / "cfg")
    for d in ("test-repo", "stage-repo", "prod-repo"):
        (tmp_path / d).mkdir()
    notified = []
    monkeypatch.setattr(evolution_runner, "_notify_finding",
                        lambda row: notified.append(("finding", row)))
    monkeypatch.setattr(
        evolution_runner, "_notify_report",
        lambda row, phase, text: notified.append((phase, row, text)))
    return tmp_path, notified


class FakeRun:
    """Scripted _run(): matches commands by predicate, records calls,
    optionally performs side effects (writing artifact files)."""

    def __init__(self):
        self.rules = []          # (predicate, responder)
        self.calls = []

    def rule(self, predicate, responder):
        self.rules.append((predicate, responder))

    def __call__(self, cmd, cwd, timeout_s, env_extra=None,
                 input_text=None, on_line=None, scrub_prefixes=()):
        # R8 P0-04г / R9: контур глушит опасные механизмы git НАБОРОМ
        # флагов «-c ключ=значение» на каждом вызове; правила сценариев
        # матчатся по логической команде, поэтому служебные пары срезаются
        # до сопоставления. Срез обобщён (любая пара «-c ...», а не только
        # известный ключ) намеренно: иначе добавление флага к набору
        # ломало бы полтора десятка сценарных тестов и подталкивало бы
        # оставить набор неполным.
        cmd = list(cmd)
        if cmd[:1] == ["git"]:
            while len(cmd) > 2 and cmd[1] == "-c" and "=" in cmd[2]:
                del cmd[1:3]
        self.calls.append((list(cmd), str(cwd), input_text))
        for predicate, responder in self.rules:
            if predicate(cmd):
                out = responder(cmd, cwd)
                return out if isinstance(out, tuple) else (0, out)
        return 0, ""

    def streaming(self, cmd, cwd, timeout_s, input_text=None, **_kw):
        """_run_streaming stand-in (the implement agent): same rule table,
        the {'type':'result','result':...} envelope unwrapped the way the
        real stream-json path delivers it. `**_kw` поглощает служебные
        ключи вроде env_deny — они на сценарий не влияют."""
        code, out = self(cmd, cwd, timeout_s, input_text=input_text)
        try:
            envelope = json.loads(out)
            result = (str(envelope.get("result", out))
                      if isinstance(envelope, dict) else out)
        except json.JSONDecodeError:
            result = out
        return code, result, ""


def _claude_envelope(result_text: str) -> str:
    return json.dumps({"type": "result", "result": result_text})


# --------------------------------------------------------------------- scan

def test_scan_creates_only_new_findings(env, monkeypatch):
    tmp, notified = env
    evolution.add_finding("Old method", "already known",
                          source_url="https://arxiv.org/abs/1")
    findings = [
        {"title": "Old method", "source_url": "https://arxiv.org/abs/1",
         "summary": "dup", "integration_plan": "-", "expected_gain": "-"},
        {"title": "New stabilization trick",
         "source_url": "https://arxiv.org/abs/2",
         "summary": "dual smoothing v2", "integration_plan": "patch colgen",
         "expected_gain": "gap -0.5%"},
    ]
    # run_scan streams the CLI via _run_streaming(cmd, cwd, timeout_s,
    # input_text=prompt).
    calls = []

    def fake_streaming(cmd, cwd, timeout_s, input_text=None):
        calls.append((cmd, str(cwd), input_text))
        return 0, ("Нашёл кое-что.\n```json\n" + json.dumps(findings)
                   + "\n```"), ""
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake_streaming)
    created = evolution_runner.run_scan()
    assert len(created) == 1
    assert created[0]["title"] == "New stabilization trick"
    assert [n[0] for n in notified] == ["finding"]
    # Contour: the scan CLI must run inside the TEST install.
    assert calls and calls[0][0][0] == evolution_runner.claude_cli()
    assert all(str(evolution_runner.test_dir()) in c[1] for c in calls)
    # The multi-line prompt must travel via stdin, NEVER as an argv
    # element: cmd.exe truncates a .cmd command line at the first newline,
    # silently dropping --output-format and every following flag.
    cmd, _cwd, prompt = calls[0]
    assert all("\n" not in str(arg) for arg in cmd)
    assert prompt and "Old method" in prompt          # known findings fed in
    assert "stream-json" in cmd


def test_scan_marks_observation_only_when_it_actually_observed(env, monkeypatch):
    """Отметку «последнее наблюдение» (её показывает сводка контуров)
    обновляет только УДАВШИЙСЯ скан.

    Пустой результат — тоже наблюдение: агент отработал и честно ничего не
    принёс. А тайм-аут, ненулевой код возврата и неразборчивый ответ —
    это НЕ наблюдение: обновляй мы отметку и на них, намертво сломанный
    контур выглядел бы на экране бодрым, и застой стал бы невидим."""
    _tmp, _notified = env
    path = evolution_runner.LAST_SCAN_AT_PATH

    def cli(code, text):
        monkeypatch.setattr(
            evolution_runner, "_run_streaming",
            lambda cmd, cwd, timeout_s, input_text=None: (code, text, ""))

    for code, text in ((124, ""), (2, ""), (0, "не json")):
        cli(code, text)
        assert evolution_runner.run_scan() == []
        assert not path.exists(), f"провал ({code}) обновил отметку"

    cli(0, "```json\n[]\n```")                     # отработал, но пусто
    assert evolution_runner.run_scan() == []
    assert path.read_text(encoding="utf-8").strip(), "пустой скан — тоже наблюдение"


def test_scan_survives_garbage_output(env, monkeypatch):
    _tmp, notified = env
    monkeypatch.setattr(
        evolution_runner, "_run_streaming",
        lambda cmd, cwd, timeout_s, input_text=None:
            (0, "todo: not json", "plain text the CLI printed"))
    assert evolution_runner.run_scan() == []
    assert notified == []
    # The feed's error event carries the raw-output tail for diagnosis.
    snap = evolution_runner.get_scan_activity(0)
    errors = [e["detail"] for e in snap["events"] if e["kind"] == "error"]
    assert errors and "plain text the CLI printed" in errors[0]


# --------------------------------------------------------------- test phase

def _seed(title="Trick"):
    _created, row = evolution.add_finding(
        title, "summary", source_url="https://x.io/p",
        integration_plan="apply", expected_gain="gap down")
    return row


def _metrics(commit="c1"):
    return json.dumps({"schema": "evolution-bench/v1", "commit": commit,
                       "profile": "quick", "instances": {}})


def _wire_happy_test_phase(fake, tmp, *, pytest_rc=0, agent_result="done",
                           head_advances=True):
    """Standard fake wiring for run_test_phase.

    rev-parse HEAD order: (1) ensure_baseline -> prod HEAD, (2) base_head
    after checking out the improvement branch, (3) post-agent HEAD.
    """
    post_agent = "after" if head_advances else "base"
    heads = iter(["prodhead", "base", post_agent, post_agent, post_agent])
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: next(heads, post_agent))
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")
    fake.rule(lambda cmd: cmd[0] == evolution_runner.claude_cli(),
              lambda cmd, cwd: _claude_envelope(agent_result))
    fake.rule(lambda cmd: cmd[-1] == "-q" and "-m" in cmd,
              lambda cmd, cwd: (pytest_rc, "1 failed" if pytest_rc else "ok"))

    def bench(cmd, cwd):
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(_metrics())
        return ""
    fake.rule(lambda cmd: "_evolution_bench.py" in cmd and "run" in cmd,
              bench)

    def compare(cmd, cwd):
        report = cmd[cmd.index("--out") + 1]
        with open(report, "w", encoding="utf-8") as f:
            f.write("| report |")
        with open(report[:-3] + ".json", "w", encoding="utf-8") as f:
            json.dump({"verdict": "improved", "better": 2, "worse": 0,
                       "equal": 10, "highlights": ["inst1 gap -0.3%"]}, f)
        return ""
    fake.rule(lambda cmd: "_evolution_bench.py" in cmd and "compare" in cmd,
              compare)


def test_approve_stage_requires_countersign_on_the_action(env, monkeypatch):
    """Аудит R10 (P0-04): рабочего пути «токен → фаза» не существовало —
    интерфейс токен не принимал, статус переключался, фаза молча умирала
    на гейте. Теперь без токена действие отклоняется ДО смены статуса, а
    с верным токеном фаза стартует и получает его."""
    tmp, _ = env
    row = _seed()
    evolution.set_status(row["id"], evolution.STATUS_TEST_IMPROVED)
    res = evolution_runner.approve_action(row["id"], "s", "adm@x")
    assert res["ok"] is False
    assert res["error"] == "countersign_required"
    assert evolution.get(row["id"])["status"] \
        == evolution.STATUS_TEST_IMPROVED          # статус не тронут
    # неверный токен — тот же отказ
    res = evolution_runner.approve_action(row["id"], "s", "adm@x",
                                          countersign="мусор")
    assert res["ok"] is False and res["error"] == "countersign_required"
    # верный токен — фаза стартует и получает его
    token = _countersign(monkeypatch, row["id"], "stage")
    got = {}
    import threading as _th
    started = _th.Event()

    def fake_stage(imp_id, countersign=None):
        got.update(imp_id=imp_id, countersign=countersign)
        started.set()

    monkeypatch.setattr(evolution_runner, "run_stage_phase", fake_stage)
    res = evolution_runner.approve_action(row["id"], "s", "adm@x",
                                          countersign=token)
    assert res["ok"] is True
    assert started.wait(10)
    assert got == {"imp_id": row["id"], "countersign": token}


def _wire_materialize(monkeypatch):
    """Материализация в фейковом репозитории тестов: настоящий git archive
    невозможен (репозитория нет) — подменяем создание одноразовой среды,
    сохраняя контракт (каталог существует, .git отсутствует). Возвращает
    журнал вызовов для ассертов."""
    calls = {"materialized": [], "laid": []}

    def fake_mat(repo, sha, dest):
        Path(dest).mkdir(parents=True, exist_ok=True)
        calls["materialized"].append((str(sha), str(dest)))
        return {"dest": str(dest), "carried_runtime": [],
                "missing_runtime": [], "has_git": False}

    def fake_lay(dst, repo, base):
        calls["laid"].append((str(dst), str(base)))
        return ["tests/conftest.py"]

    monkeypatch.setattr(evolution_runner, "materialize", fake_mat)
    monkeypatch.setattr(evolution_runner, "_lay_trusted_harness", fake_lay)
    return calls


def test_test_phase_happy_path(env, monkeypatch):
    tmp, notified = env
    row = _seed()
    fake = FakeRun()
    _wire_happy_test_phase(fake, tmp)
    mat_calls = _wire_materialize(monkeypatch)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_TEST_IMPROVED
    assert final["branch"] == f"improvement-{row['id']}"
    assert final["test_metrics"]["schema"] == "evolution-bench/v1"
    # The analytical deploy review is stored (advisory; here the mocked CLI
    # yields no JSON, so the conservative metric fallback is used).
    assert final["review"]["recommendation"] in ("deploy", "hold", "reject")
    # Honest grade: mocked compare has better=2, worse=0 -> full improvement.
    assert final["review"]["grade"] == "full"
    phases = [n[0] for n in notified]
    assert "test" in phases
    cli_calls = [c for c in fake.calls
                 if c[0][0] == evolution_runner.claude_cli()]
    # The IMPLEMENT call (stream-json) runs in the TEST install WITH the
    # sandbox-only autonomy flag, prompt via stdin (cmd.exe truncates a
    # multi-line argv element at the first newline).
    impl = [c for c in cli_calls if "--output-format" in c[0]]
    assert impl and all(str(evolution_runner.test_dir()) in c[1] for c in impl)
    assert all("--dangerously-skip-permissions" in c[0] for c in impl)
    # Инструменты ожидания отключены МЕХАНИЧЕСКИ (находка 27, дважды):
    # агент делал рабочую интеграцию, запускал фоновый свип и уходил
    # ждать уведомлений, которых в одноразовой сессии не бывает. Пока
    # инструмент доступен, «подождать» выглядит законным планом — промпт
    # этого не перевешивает.
    for c in impl:
        i = c[0].index("--disallowedTools")
        banned = c[0][i + 1]
        assert "Monitor" in banned and "ScheduleWakeup" in banned
    assert all(all("\n" not in str(arg) for arg in c[0]) for c in impl)
    assert all(c[2] and "Trick" in c[2] for c in impl)
    # Контракт «сначала коммит готового» и одноразовость сессии — в
    # промпте: без них агент откладывает коммит до конца эксперимента,
    # которому не суждено кончиться.
    assert all("ОДНОРАЗОВАЯ" in c[2] for c in impl)
    assert all("СНАЧАЛА КОММИТ ГОТОВОГО" in c[2] for c in impl)
    # The deploy-REVIEW call is analysis only: no autonomy flag, still in
    # the external Test contour.
    review_calls = [c for c in cli_calls if "--output-format" not in c[0]]
    assert review_calls
    assert all("--dangerously-skip-permissions" not in c[0]
               for c in review_calls)
    # Sandbox self-clean: the baseline and improvement checkouts of
    # origin/main use `-f`, so a dirty Test working tree (a stray
    # uncommitted file) can never stall the pipeline and the baseline is of
    # UNMODIFIED origin/main.
    # Считаем только переключения ВЕТКИ (`-B`): выборочные checkout по пути
    # — это доверенная батарея (шаг 3 плана укрепления), у неё `-f` не имеет
    # смысла и она нарочно не трогает остальное дерево.
    checkouts = [c[0] for c in fake.calls
                 if c[0][:2] == ["git", "checkout"] and "-B" in c[0]]
    assert checkouts and all("-f" in cmd for cmd in checkouts)
    # Пакет C (аудит R10, P0-04): кандидат исполняется в МАТЕРИАЛИЗОВАННОЙ
    # ревизии без .git, доверенная батарея кладётся из аттестованной
    # базовой ревизии, и pytest/замер идут именно в этой среде.
    assert mat_calls["materialized"], "материализация не выполнялась"
    assert mat_calls["laid"] and \
        all(base == "base" for _dst, base in mat_calls["laid"]), \
        "доверенная батарея не положена из базовой ревизии"
    battery = [c for c in fake.calls
               if c[0][-1] == "-q" or "_evolution_bench.py" in c[0]]
    in_mat = [c for c in battery if "candidate-runs" in str(c[1] or "")]
    assert in_mat, "батарея кандидата шла не в материализованной среде"


def test_test_phase_red_pytest_fails(env, monkeypatch):
    tmp, notified = env
    row = _seed("Red")
    fake = FakeRun()
    _wire_happy_test_phase(fake, tmp, pytest_rc=1)
    _wire_materialize(monkeypatch)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_TEST_FAILED
    assert "красные" in (final["error"] or "")


def test_test_phase_agent_abort(env, monkeypatch):
    tmp, notified = env
    row = _seed("Aborted")
    fake = FakeRun()
    _wire_happy_test_phase(
        fake, tmp, agent_result="ABORT: метод неприменим к set covering")
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_TEST_FAILED
    assert "ABORT" in (final["error"] or "")
    # The candidate never got benchmarked/compared after the abort (the
    # baseline bench legitimately ran before the agent).
    assert not any("_evolution_bench.py" in c[0] and "compare" in c[0]
                   for c in fake.calls)
    assert not any(f"evolution-metrics-{row['id']}-quick.json" in " ".join(c[0])
                   for c in fake.calls)


def test_test_phase_no_commit_preserves_the_agents_answer(env, monkeypatch):
    """Агент вышел чисто, но без коммита и без «ABORT:» — его финальный
    ответ обязан попасть в ошибку.

    Сбой находки 27 (2026-07-23): агент работал 45 минут, объяснил итог в
    финальном ответе — и это объяснение было выброшено. Админ видел
    «изменений нет» и не мог отличить «уже реализовано» от «не смог»."""
    tmp, _notified = env
    row = _seed("NoCommit")
    fake = FakeRun()
    _wire_happy_test_phase(
        fake, tmp, head_advances=False,
        agent_result="Изучил репозиторий: приём уже реализован в "
                     "_gerad_colgen через adaptive mp, коммит не нужен.")
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_TEST_FAILED
    err = final["error"] or ""
    assert "не сделал коммита" in err
    assert "уже реализован" in err, "финальный ответ агента снова выброшен"


def test_stage_phase_no_ai_calls(env, monkeypatch):
    """INTERNAL contour: the stage phase must never invoke the CLI."""
    tmp, notified = env
    row = _seed("Staged")
    evolution.set_status(row["id"], evolution.STATUS_TEST_IMPROVED,
                         branch=f"improvement-{row['id']}")
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: "head")
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")
    fake.rule(lambda cmd: cmd[-1] == "-q" and "-m" in cmd,
              lambda cmd, cwd: "ok")

    def bench(cmd, cwd):
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(_metrics())
        return ""
    fake.rule(lambda cmd: "_evolution_bench.py" in cmd and "run" in cmd,
              bench)

    def compare(cmd, cwd):
        report = cmd[cmd.index("--out") + 1]
        with open(report, "w", encoding="utf-8") as f:
            f.write("| stage report |")
        with open(report[:-3] + ".json", "w", encoding="utf-8") as f:
            json.dump({"verdict": "improved", "better": 1, "worse": 0,
                       "equal": 3, "highlights": []}, f)
        return ""
    fake.rule(lambda cmd: "_evolution_bench.py" in cmd and "compare" in cmd,
              compare)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    # Stage больше не верит Test на слово (пятый аудит, C1): без
    # подписанного свидетельства фаза не начинается.
    from webapp import evidence
    monkeypatch.setattr(evidence, "diff_fingerprint",
                        lambda repo, base, head: "diff-fp")
    evidence.issue(row["id"], "test", base_sha="basesha",
                   candidate_sha="head", diff_hash="diff-fp",
                   checks={"unit_tests": "green", "isolation": "broker"})
    evolution_runner.run_stage_phase(
        row["id"], countersign=_countersign(monkeypatch, row["id"], "stage"))
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_STAGE_PASSED
    assert final["stage_metrics"] is not None
    assert not any(c[0][0] == evolution_runner.claude_cli()
                   for c in fake.calls)


def test_deploy_rolls_back_on_red_tests(env, monkeypatch):
    tmp, notified = env
    row = _seed("Rollback")
    evolution.set_status(row["id"], evolution.STATUS_STAGE_PASSED,
                         branch=f"improvement-{row['id']}")
    # Stage pins the reviewed commit; deploy merges exactly that SHA.
    evolution_runner._reviewed_commit_path(row["id"]).parent.mkdir(
        parents=True, exist_ok=True)
    evolution_runner._reviewed_commit_path(row["id"]).write_text(
        "reviewedsha", encoding="utf-8")
    # Prod проверяет свидетельство фазы Stage ДО слияния (пятый аудит, C1).
    from webapp import evidence
    monkeypatch.setattr(evidence, "diff_fingerprint",
                        lambda repo, base, head: "diff-fp")
    evidence.issue(row["id"], "stage", base_sha="basesha",
                   candidate_sha="reviewedsha", diff_hash="diff-fp",
                   checks={"unit_tests": "green"})
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: "prevhead")
    fake.rule(lambda cmd: cmd[:2] == ["git", "cat-file"],
              lambda cmd, cwd: (0, ""))          # reviewed SHA present
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")
    fake.rule(lambda cmd: cmd[-1] == "-q" and "-m" in cmd,
              lambda cmd, cwd: (1, "2 failed"))
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    restarted = []
    monkeypatch.setattr(evolution_runner, "_detached_self_restart",
                        lambda: restarted.append(True))
    evolution_runner.deploy_prod(
        row["id"], countersign=_countersign(monkeypatch, row["id"], "prod"))
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_DEPLOY_FAILED
    assert "ОТКАЧЕНО" in (final["error"] or "")
    assert any(c[0][:3] == ["git", "reset", "--hard"] for c in fake.calls)
    # The merge targeted the reviewed SHA, not FETCH_HEAD.
    assert any(c[0][:3] == ["git", "merge", "--no-edit"]
               and "reviewedsha" in c[0] for c in fake.calls)
    assert restarted == []


# ---------------------------------------------------------------- scheduler

def test_contour_guard_blocks_overlapping_test_dir(env, monkeypatch):
    """The sandbox agent must refuse to run when Test overlaps prod/stage."""
    tmp, notified = env
    row = _seed("Contour")
    # Point Test at the prod dir — a misconfiguration that would let the
    # gate-disabled agent loose in the internal contour.
    monkeypatch.setenv("COSMOS_TEST_DIR", str(tmp / "prod-repo"))
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[0] == evolution_runner.claude_cli(),
              lambda cmd, cwd: (_ for _ in ()).throw(
                  AssertionError("CLI must not run on contour violation")))
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_TEST_FAILED
    # The CLI was never invoked.
    assert not any(c[0][0] == evolution_runner.claude_cli()
                   for c in fake.calls)


def test_trigger_scan_dedup_and_disabled(env, monkeypatch):
    import threading as _t
    import time as _time
    # disabled -> refused
    monkeypatch.delenv("CREW_EVOLUTION_ENABLED", raising=False)
    assert evolution_runner.trigger_scan() is False
    # enabled -> starts once; a second call while running is refused
    monkeypatch.setenv("CREW_EVOLUTION_ENABLED", "1")
    gate = _t.Event()
    calls = []
    monkeypatch.setattr(evolution_runner, "run_scan",
                        lambda: (calls.append(1), gate.wait(5)))
    assert evolution_runner.trigger_scan() is True
    assert evolution_runner.scan_running() is True
    assert evolution_runner.trigger_scan() is False        # deduped
    gate.set()
    for _ in range(100):
        if not evolution_runner.scan_running():
            break
        _time.sleep(0.02)
    assert calls == [1]
    assert evolution_runner.scan_running() is False


def test_trigger_scan_refused_while_pipeline_busy(env, monkeypatch):
    """A scan must not start while a phase holds _pipeline_lock — otherwise
    it would reset the phase's live feed. The «Начать R&D» press is a no-op
    (409) until the pipeline frees up."""
    monkeypatch.setenv("CREW_EVOLUTION_ENABLED", "1")
    monkeypatch.setattr(evolution_runner, "run_scan", lambda: None)
    assert evolution_runner.pipeline_busy() is False
    with evolution_runner._pipeline_lock:              # simulate a live phase
        assert evolution_runner.pipeline_busy() is True
        assert evolution_runner.trigger_scan() is False
    # lock released -> a scan may start again
    assert evolution_runner.pipeline_busy() is False


def test_activity_store_and_stream_parse(env):
    evolution_runner._activity_reset()
    evolution_runner._activity_add("start", "запуск")
    # An assistant event with a WebSearch and a WebFetch tool use.
    evolution_runner._stream_event_to_activity({
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebSearch",
             "input": {"query": "column generation crew pairing 2026"}},
            {"type": "tool_use", "name": "WebFetch",
             "input": {"url": "https://arxiv.org/abs/2601.00001"}},
            {"type": "text", "text": "Похоже на кандидата по стабилизации."},
        ]}})
    snap = evolution_runner.get_scan_activity(0)
    kinds = [e["kind"] for e in snap["events"]]
    assert kinds == ["start", "search", "fetch", "thinking"]
    assert snap["running"] is True
    assert any("column generation" in e["detail"]
               for e in snap["events"] if e["kind"] == "search")
    # Incremental polling: since=last_seq returns nothing new.
    assert evolution_runner.get_scan_activity(snap["last_seq"])["events"] == []
    evolution_runner._activity_finish()
    assert evolution_runner.get_scan_activity(0)["running"] is False


def test_run_scan_streams_activity(env, monkeypatch):
    """run_scan drives the live feed: start -> search/fetch -> finding -> done."""
    tmp, notified = env
    fake = FakeRun()
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming",
                        fake.streaming)  # baseline path unused
    # stream-json lines the fake CLI "emits", then a final result with a
    # findings JSON array.
    stream_lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebSearch",
             "input": {"query": "branch-and-price stabilization"}}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "WebFetch",
             "input": {"url": "https://example.org/paper"}}]}}),
        json.dumps({"type": "result", "result":
                    "```json\n" + json.dumps([{
                        "title": "Новый приём стабилизации",
                        "source_url": "https://example.org/paper",
                        "summary": "ускоряет сходимость",
                        "integration_plan": "патч прайсинга",
                        "expected_gain": "-10% времени"}]) + "\n```"}),
    ]

    def fake_streaming(cmd, cwd, timeout_s, input_text=None):
        for ln in stream_lines:
            evt = json.loads(ln)
            if evt.get("type") == "result":
                result = str(evt.get("result"))
            evolution_runner._stream_event_to_activity(evt)
        return 0, result, ""
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake_streaming)

    created = evolution_runner.run_scan()
    assert len(created) == 1
    snap = evolution_runner.get_scan_activity(0)
    kinds = [e["kind"] for e in snap["events"]]
    assert kinds[0] == "start"
    assert "search" in kinds and "fetch" in kinds
    assert "finding" in kinds and kinds[-1] == "done"
    assert snap["running"] is False           # finally: _activity_finish


def test_run_streaming_stdin_real_subprocess(env):
    """Real-subprocess check of the stdin path: a python child echoes what
    it read from stdin as stream-json; the multi-line UTF-8 prompt must
    arrive intact and the feed must pick the events up."""
    import sys
    child = (
        "import json,sys\n"
        "p = sys.stdin.read()\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': ["
        "{'type': 'tool_use', 'name': 'WebSearch',"
        " 'input': {'query': 'q1'}}]}}))\n"
        "print('this line is not json')\n"
        "print(json.dumps({'type': 'result', 'result': p}))\n"
    )
    evolution_runner._activity_reset()
    prompt = "строка один\nстрока два — юникод ✓\nстрока три"
    code, result, diag = evolution_runner._run_streaming(
        [sys.executable, "-c", child], cwd=".", timeout_s=60,
        input_text=prompt)
    assert code == 0
    assert result == prompt                     # survived the pipe intact
    assert "not json" in diag                   # raw tail captured
    kinds = [e["kind"]
             for e in evolution_runner.get_scan_activity(0)["events"]]
    assert "search" in kinds
    evolution_runner._activity_finish()


def test_run_streaming_watchdog_covers_blocked_stdin_write(env):
    """A child that never drains stdin + a prompt bigger than the ~4KB
    Windows pipe buffer used to block the scan thread forever BEFORE the
    watchdog even started (holding _pipeline_lock until a server
    restart). Now the watchdog runs first and the feeder thread takes the
    blocking write, so the deadline holds."""
    import sys
    evolution_runner._activity_reset()
    big_prompt = ("строка наполнения промпта — " * 40 + "\n") * 100  # ~64KB
    t0 = time.monotonic()
    code, _result, _diag = evolution_runner._run_streaming(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=".", timeout_s=2, input_text=big_prompt)
    elapsed = time.monotonic() - t0
    evolution_runner._activity_finish()
    assert code == 124                       # deadline enforced, not 30s
    assert elapsed < 20


@pytest.mark.skipif(os.name != "nt",
                    reason="both halves are Windows-only: the .cmd trampoline "
                           "cannot even spawn on POSIX (_stream_proc honestly "
                           "returns 127), and the tree kill is taskkill /T")
def test_run_streaming_kills_grandchild_behind_cmd_trampoline(env,
                                                              tmp_path):
    """claude.cmd is a batch trampoline: plain proc.kill() terminates only
    cmd.exe while the real agent survives and keeps the stdout pipe open,
    so the read loop used to run arbitrarily past the deadline. The tree
    kill must end the whole family at timeout_s.

    Known debt, deliberately NOT covered here: on POSIX _kill_tree
    degrades to killing the direct child only (taskkill is absent, its
    FileNotFoundError is swallowed), so a grandchild would survive and
    hold the pipe just like node behind claude.cmd does on Windows. A
    POSIX variant of this test needs process-group kill support in the
    product first (start_new_session + os.killpg)."""
    import sys
    child = ("import time\n"
             "print('alive', flush=True)\n"
             "time.sleep(30)\n")
    script = tmp_path / "grandchild.py"
    script.write_text(child, encoding="utf-8")
    tramp = tmp_path / "tramp.cmd"
    tramp.write_text(f'@echo off\r\n"{sys.executable}" "{script}"\r\n',
                     encoding="ascii")
    evolution_runner._activity_reset()
    t0 = time.monotonic()
    code, _result, _diag = evolution_runner._run_streaming(
        [str(tramp)], cwd=str(tmp_path), timeout_s=2)
    elapsed = time.monotonic() - t0
    evolution_runner._activity_finish()
    assert code == 124
    assert elapsed < 20                      # not the grandchild's 30s


def test_approve_action_gates_and_launches(env, monkeypatch):
    """approve_action drives the same transitions as the Telegram buttons:
    valid predecessor -> approved status + phase thread; anything else is
    refused without touching the row."""
    import threading as _t
    _tmp, _notified = env
    row = _seed("Web-approved")
    started = _t.Event()
    monkeypatch.setattr(evolution_runner, "run_test_phase",
                        lambda imp_id: started.set())
    res = evolution_runner.approve_action(row["id"], "t", "admin@x.io")
    assert res["ok"] and res["status"] == evolution.STATUS_APPROVED_FOR_TEST
    assert started.wait(5)
    # Same press again: approved_for_test is NOT a valid predecessor.
    res = evolution_runner.approve_action(row["id"], "t", "admin@x.io")
    assert res == {"ok": False, "error": "invalid_status",
                   "current": evolution.STATUS_APPROVED_FOR_TEST}
    # Stage from approved_for_test — also gated.
    assert evolution_runner.approve_action(
        row["id"], "s", "admin@x.io")["ok"] is False
    # Reject works from any non-terminal status and needs no thread.
    res = evolution_runner.approve_action(row["id"], "x", "admin@x.io")
    assert res["ok"] and res["status"] == evolution.STATUS_REJECTED
    # Unknown id / unknown action.
    assert evolution_runner.approve_action(
        999999, "t", "admin@x.io") == {"ok": False, "error": "not_found"}
    assert evolution_runner.approve_action(
        row["id"], "q", "admin@x.io")["ok"] is False


def test_recover_interrupted_marks_running_phases_failed(env):
    """A restart mid-phase leaves testing/staging/deploying rows orphaned
    (their threads died with the process); boot recovery fails them so the
    admin can re-approve. The approved_for_* window counts too: the phase
    thread flips to a running status only after winning _pipeline_lock, so
    a restart while it is still queued must not strand the row (it would
    have ZERO UI buttons)."""
    _tmp, _notified = env
    stuck = _seed("Stuck")
    evolution.set_status(stuck["id"], evolution.STATUS_APPROVED_FOR_TEST)
    evolution.set_status(stuck["id"], evolution.STATUS_TESTING)
    # queued-but-not-started: approved_for_stage, thread never ran
    _c, queued = evolution.add_finding(
        "Queued", "summary", source_url="https://z.io/queued")
    evolution.set_status(queued["id"], evolution.STATUS_APPROVED_FOR_STAGE)
    _created, untouched = evolution.add_finding(
        "Fresh", "summary", source_url="https://y.io/fresh")
    evolution_runner._recover_interrupted()
    assert evolution.get(stuck["id"])["status"] == \
        evolution.STATUS_TEST_FAILED
    assert "перезапуском" in (evolution.get(stuck["id"])["error"] or "")
    assert evolution.get(queued["id"])["status"] == \
        evolution.STATUS_STAGE_FAILED         # recovered, not orphaned
    assert evolution.get(untouched["id"])["status"] == evolution.STATUS_FOUND


def test_test_phase_feeds_live_activity(env, monkeypatch):
    """The Test phase streams its steps into the same live feed the scan
    uses: start -> baseline/bench -> agent -> tests -> verdict/done."""
    tmp, notified = env
    row = _seed("Feed")
    fake = FakeRun()
    _wire_happy_test_phase(fake, tmp)
    _wire_materialize(monkeypatch)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    evolution_runner.run_test_phase(row["id"])
    snap = evolution_runner.get_scan_activity(0)
    kinds = [e["kind"] for e in snap["events"]]
    assert kinds[0] == "start" and "фаза Test" in snap["events"][0]["detail"]
    assert "note" in kinds                      # baseline / pytest / LLM steps
    assert kinds[-1] == "done"
    done = snap["events"][-1]
    assert done.get("data", {}).get("t") == "verdict"
    assert done["data"]["recommendation"] in ("deploy", "hold", "reject")
    assert snap["running"] is False


def test_translate_progress_covers_both_stages():
    # Now returns (russian_detail, structured_data) — dual-level wording for
    # the operator plus a payload the web UI localizes.
    tp = evolution_runner._translate_progress
    s1 = tp("inst1", "  it   7: LP 116589.0  + 60000 cols (best rc -37.2) "
                     "pricing 912.3s  pool 431,502")
    assert s1 and "Инстанс 1" in s1[0] and "итер. 7" in s1[0]
    assert s1[1]["t"] == "cg_iter" and s1[1]["pool"] == 431502 \
        and s1[1]["lp"] == 116589
    cps = tp("inst1", "  CP-SAT: 201 selected (OPTIMAL) in 4.1s obj=116589")
    assert cps and "201 смен" in cps[0] and "OPTIMAL" in cps[0]
    assert cps[1]["t"] == "cg_select" and cps[1]["n"] == 201
    ch = tp("inst1", "Construction Heuristic phase (0) ended: time spent "
                     "(894), best score (-800hard/-176674soft)")
    assert ch and "эвристика" in ch[0] and ch[1]["t"] == "s2_ch"
    ls = tp("inst1", "Local Search phase (1) ended: best score "
                     "(-230hard/-235799soft)")
    assert ls and "локальным поиском" in ls[0] and ls[1]["t"] == "s2_ls"
    s2 = tp("inst1", "stage2=0hard/18400soft x")
    assert s2 and s2[1]["t"] == "s2_score" \
        and s2[1]["score"] == "0hard/18400soft"
    assert tp("inst1", "an unrelated line") is None


def test_instance_verdict_classifies_quality_and_time(tmp_path):
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    base.write_text(json.dumps({"instances": {
        "inst1": {"stage1": {"ip_objective": 1000, "wall_s": 100}},
        "inst2": {"stage1": {"ip_objective": 1000, "wall_s": 100}}}}),
        encoding="utf-8")
    # inst1: quality unchanged, time within noise -> equal.
    # inst2: quality worse -> worse (dominates time).
    cand.write_text(json.dumps({"instances": {
        "inst1": {"stage1": {"ip_objective": 1000, "wall_s": 100.3}},
        "inst2": {"stage1": {"ip_objective": 1100, "wall_s": 90}}}}),
        encoding="utf-8")
    v1 = evolution_runner._instance_verdict(cand, base, "inst1")
    assert v1[1]["cls"] == "equal" and v1[1]["q_cls"] == "equal"
    v2 = evolution_runner._instance_verdict(cand, base, "inst2")
    assert v2[1]["cls"] == "worse" and v2[1]["q_pct"] == 10.0


def test_bench_on_line_routes_progress_events(env):
    evolution_runner._activity_reset()
    on_line = evolution_runner._bench_on_line(None)          # baseline run
    on_line("[inst2] progress:   it   3: LP 5000.0  + 12 cols "
            "(best rc -2.0) pool 9,000")
    on_line("[inst2] progress: Construction Heuristic phase (0) ended: "
            "best score (-10hard/-50soft)")
    on_line("[inst2] progress: totally unrecognized noise")   # dropped
    events = evolution_runner.get_scan_activity(0)["events"]
    evolution_runner._activity_finish()
    bench = [e for e in events if e["kind"] == "bench"]
    assert len(bench) == 2                               # noise dropped
    assert any("итер. 3" in e["detail"] for e in bench)
    assert any(e.get("data", {}).get("t") == "cg_iter" for e in bench)
    assert any("эвристика" in e["detail"] for e in bench)


def test_bench_on_line_emits_pinned_summary_vs_baseline(env, tmp_path):
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    base.write_text(json.dumps({"instances": {
        "inst1": {"stage1": {"ip_objective": 1000, "wall_s": 100}}}}),
        encoding="utf-8")
    cand.write_text(json.dumps({"instances": {
        "inst1": {"stage1": {"ip_objective": 900, "wall_s": 100}}}}),
        encoding="utf-8")
    evolution_runner._activity_reset()
    evolution_runner._bench_on_line(cand, base)("[inst1] done: x")
    events = evolution_runner.get_scan_activity(0)["events"]
    evolution_runner._activity_finish()
    summ = [e for e in events if e["kind"] == "summary"]
    assert len(summ) == 1 and summ[0]["data"]["cls"] == "better"
    assert summ[0]["data"]["q_pct"] == -10.0


def test_bench_on_line_emits_instance_results(env, tmp_path):
    """Per-instance benchmark lines become feed events; the 'done' line is
    replaced by the instance's metrics summary."""
    out = tmp_path / "m.json"
    out.write_text(json.dumps({
        "instances": {"inst1": {
            "stage1": {"ip_objective": 18600, "gap_vs_lp_pct": 1.2,
                       "wall_s": 34.5},
            "stage2": {"result_status": "OK", "stage2_score": -120},
        }}}), encoding="utf-8")
    evolution_runner._activity_reset()
    on_line = evolution_runner._bench_on_line(out)
    on_line("[inst1] stage1: budget 10 min -> bench_inst1.log")
    on_line("[inst1] done: stage1=ok stage2=OK")
    on_line("metrics -> ignored.json")          # non-instance line: skipped
    events = evolution_runner.get_scan_activity(0)["events"]
    evolution_runner._activity_finish()
    bench = [e["detail"] for e in events if e["kind"] == "bench"]
    assert len(bench) == 2
    assert "inst1" in bench[0] and "stage1" in bench[0]
    assert "18600" in bench[1] and "OK" in bench[1]


def test_should_scan_rule():
    from datetime import datetime
    should = evolution_runner._should_scan
    assert should(datetime(2026, 7, 14, 3, 30), None) is True
    assert should(datetime(2026, 7, 14, 3, 29), None) is False
    assert should(datetime(2026, 7, 14, 4, 30), None) is False
    assert should(datetime(2026, 7, 14, 3, 45), "2026-07-14") is False
    assert should(datetime(2026, 7, 14, 3, 45), "2026-07-13") is True


def test_child_env_scrubs_gate_prefixes(monkeypatch):
    """The gate pytest run must NOT inherit the live Prod env (auth mode,
    evolution/telegram flags) — that reddens env-sensitive tests and trips
    the Test/Stage/Prod gate for reasons unrelated to the candidate."""
    monkeypatch.setenv("CREW_AUTH_MODE", "access")
    monkeypatch.setenv("CREW_EVOLUTION_ENABLED", "1")
    monkeypatch.setenv("CREW_TG_PRIMARY", "1")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret")
    monkeypatch.setenv("COSMOS_TEST_DIR", "somewhere")
    monkeypatch.setenv("KEEP_ME", "kept")

    scrubbed = evolution_runner._child_env(
        scrub_prefixes=evolution_runner._GATE_ENV_SCRUB)
    for gone in ("CREW_AUTH_MODE", "CREW_EVOLUTION_ENABLED",
                 "CREW_TG_PRIMARY", "CLAUDE_CODE_OAUTH_TOKEN",
                 "COSMOS_TEST_DIR"):
        assert gone not in scrubbed
    assert scrubbed.get("KEEP_ME") == "kept"
    assert scrubbed["PYTHONIOENCODING"] == "utf-8"

    # Without scrub_prefixes the live env passes through unchanged (the
    # agent CLI needs CLAUDE_CODE_OAUTH_TOKEN, so it never scrubs).
    passthrough = evolution_runner._child_env()
    assert passthrough.get("CREW_AUTH_MODE") == "access"
    assert passthrough.get("CLAUDE_CODE_OAUTH_TOKEN") == "secret"


def test_parse_llm_review_extracts_json_or_none():
    p = evolution_runner._parse_llm_review
    ok = p('бла бла {"recommendation": "hold", "rationale": "шум"} конец')
    assert ok == {"recommendation": "hold", "rationale": "шум"}
    # last valid object wins
    two = p('{"recommendation":"deploy","rationale":"a"} '
            '{"recommendation":"reject","rationale":"b"}')
    assert two["recommendation"] == "reject"
    assert p("no json here") is None
    assert p('{"recommendation": "maybe", "rationale": "x"}') is None  # bad verb


def test_fallback_review_is_conservative():
    fb = evolution_runner._fallback_review
    assert fb({"better": 0, "worse": 0}, False)["recommendation"] == "hold"
    assert fb({"better": 1, "worse": 0}, False)["recommendation"] == "deploy"
    assert fb({"better": 1, "worse": 3}, False)["recommendation"] == "hold"
    r = fb({"better": 0, "worse": 0}, True)
    assert "остановлен" in r["rationale"] and r["source"] == "fallback"


def test_request_stop_only_while_testing(env):
    tmp, _ = env
    row = _seed("Stoppable")
    assert evolution_runner.request_stop(row["id"]) is False   # status=found
    evolution.set_status(row["id"], evolution.STATUS_TESTING)
    assert evolution_runner.request_stop(row["id"]) is True
    assert evolution_runner._cancel.is_set()
    evolution_runner._cancel.clear()


def test_build_comparison_structured_rows():
    base = {"instances": {
        "inst1": {"stage1": {"ip_objective": 1000, "wall_s": 100,
                             "covered": 500},
                  "stage2": {"error": "boom"}},
    }}
    cand = {"instances": {
        "inst1": {"stage1": {"ip_objective": 950, "wall_s": 110,
                             "covered": 500},
                  "stage2": {"error": "boom"}},
        "inst2": {"stage1": {"ip_objective": 200}},
    }}
    cmp_ = evolution_runner._build_comparison(base, cand)
    assert [c["inst"] for c in cmp_] == ["inst1", "inst2"]
    s1 = cmp_[0]["stages"][0]
    assert s1["stage"] == "stage1"
    rows = {r["metric"]: r for r in s1["rows"]}
    assert rows["ip_objective"]["cls"] == "better"       # 950 < 1000
    assert rows["ip_objective"]["delta_pct"] == -5.0
    assert rows["wall_s"]["cls"] == "worse"
    assert rows["covered"]["cls"] == "equal"
    s2 = cmp_[0]["stages"][1]
    assert s2["base_error"] == "boom" and s2["cand_error"] == "boom"
    # inst2 exists only on the candidate side: metrics n/a, never crash.
    inst2_rows = {r["metric"]: r
                  for r in cmp_[1]["stages"][0]["rows"]}
    assert inst2_rows["ip_objective"]["cls"] == "n/a"


def test_grade_three_way():
    g = evolution_runner._grade
    assert g({"better": 2, "worse": 0}) == "full"      # gains, no regressions
    assert g({"better": 1, "worse": 3}) == "partial"   # some better, some worse
    assert g({"better": 0, "worse": 0}) == "none"       # nothing moved
    assert g({"better": 0, "worse": 2}) == "none"       # only regressions


def test_rejected_finding_can_be_retested(env):
    row = _seed("Revive")
    evolution.set_status(row["id"], evolution.STATUS_REJECTED)
    # approve_action does not run the phase thread in this unit test path,
    # but the gate must accept the transition rejected -> approved_for_test.
    assert "t" in [a for a, froms in evolution.ACTION_VALID_FROM.items()
                   if evolution.STATUS_REJECTED in froms]


def test_agent_env_drops_service_secrets(monkeypatch):
    """Агент внедрения исполняет недоверенный план. Служебные токены
    туннеля и ключи облачных моделей ему не нужны — и потому не достаются
    (аудит 2026-07-26, P0-01). Токен запуска самого агента остаётся:
    без него он не стартует."""
    monkeypatch.setenv("TUNNEL_SERVICE_TOKEN_ID", "секрет-1")
    monkeypatch.setenv("TUNNEL_SERVICE_TOKEN_SECRET", "секрет-2")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "секрет-3")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "нужен-агенту")
    env = evolution_runner._drop_secrets(
        evolution_runner._child_env(), evolution_runner._AGENT_ENV_DENY)
    for key in ("TUNNEL_SERVICE_TOKEN_ID", "TUNNEL_SERVICE_TOKEN_SECRET",
                "ANTHROPIC_API_KEY"):
        assert key not in env, f"{key} не должен доставаться агенту"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "нужен-агенту"


def test_candidate_may_not_touch_its_own_verification(tmp_path):
    """Кандидат, правящий батарею тестов, конвейер продвижения или
    доступ, сам определяет доказательство своей пригодности
    (аудит 2026-07-26, P0-02)."""
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True,
                       text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "webapp").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "safe.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "conftest.py").write_text("\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()

    # безобидная правка — запретных путей нет
    (tmp_path / "safe.py").write_text("x = 2\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "safe")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()
    assert evolution_runner._forbidden_changes(tmp_path, base, head) == []

    # правка собственной проверки — ловится
    (tmp_path / "tests" / "conftest.py").write_text("# hack\n",
                                                    encoding="utf-8")
    (tmp_path / "webapp" / "users.py").write_text("# hack\n",
                                                  encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "tamper")
    head2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                           capture_output=True, text=True).stdout.strip()
    got = evolution_runner._forbidden_changes(tmp_path, base, head2)
    assert "tests/conftest.py" in got and "webapp/users.py" in got


def test_unreadable_diff_stops_instead_of_passing(tmp_path):
    """Не смогли прочитать состав изменений — это повод остановиться, а
    не повод пропустить проверку."""
    got = evolution_runner._forbidden_changes(tmp_path, "нет", "такого")
    assert got and "не удалось" in got[0]


def test_trusted_harness_replaces_the_candidates_measuring_tools(tmp_path):
    """Код продукта даёт кандидат, но всё, чем его судят, берётся из
    доверенной базы: подменить измерительный прибор кандидат не может
    (аудит 2026-07-26, P0-02; шаг 3 плана укрепления)."""
    import subprocess

    def git(*a):
        subprocess.run(["git", *a], cwd=tmp_path, capture_output=True,
                       text=True, check=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("ЧЕСТНЫЙ СБОР\n",
                                                    encoding="utf-8")
    (tmp_path / "_evolution_bench.py").write_text("ЧЕСТНЫЙ ПРИБОР\n",
                                                  encoding="utf-8")
    (tmp_path / "tests" / "test_feature.py").write_text("# базовый\n",
                                                        encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True).stdout.strip()

    # кандидат подменил прибор и сбор, но добавил и честный тест на фичу
    (tmp_path / "tests" / "conftest.py").write_text("ПОДМЕНА\n",
                                                    encoding="utf-8")
    (tmp_path / "_evolution_bench.py").write_text("ПОДМЕНА\n",
                                                  encoding="utf-8")
    (tmp_path / "tests" / "test_feature.py").write_text("# тест кандидата\n",
                                                        encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "candidate")

    restored = evolution_runner._restore_trusted_harness(tmp_path, base)
    assert set(restored) == {"tests/conftest.py", "_evolution_bench.py"}
    assert (tmp_path / "tests" / "conftest.py").read_text(
        encoding="utf-8").strip() == "ЧЕСТНЫЙ СБОР"
    assert (tmp_path / "_evolution_bench.py").read_text(
        encoding="utf-8").strip() == "ЧЕСТНЫЙ ПРИБОР"
    # тест кандидата на его же фичу НЕ выбрасывается: иначе проверка
    # зелена ровно там, где ничего не проверено
    assert (tmp_path / "tests" / "test_feature.py").read_text(
        encoding="utf-8").strip() == "# тест кандидата"

    # после проверки дерево возвращается к коммиту кандидата
    evolution_runner._release_trusted_harness(tmp_path)
    assert (tmp_path / "tests" / "conftest.py").read_text(
        encoding="utf-8").strip() == "ПОДМЕНА"


def test_isolation_preflight_blocks_an_unhardened_machine(monkeypatch,
                                                          tmp_path):
    """Агент внедрения исполняет недоверенный план. Пока он работает под
    учётной записью, видящей секреты и боевые деревья, запускать его
    нельзя (шаг 0 плана укрепления).

    Охраняемый файл создаётся ЗДЕСЬ ЖЕ: проверка смотрит только на
    существующие файлы, и на чистом клоне (сборка CI, свежая машина) ни
    одного из охраняемых нет — прежняя редакция теста зависела от
    состояния площадки и падала перемежающе."""
    (tmp_path / "run-webapp.cmd").write_text("", encoding="utf-8")
    monkeypatch.setattr(evolution_runner, "REPO_ROOT", tmp_path)

    monkeypatch.delenv("COSMOS_AGENT_ACCOUNT", raising=False)
    monkeypatch.setattr(evolution_runner,
                        "_acl_lets_ordinary_users_write", lambda p: False)
    problems = evolution_runner.isolation_problems()
    assert any("COSMOS_AGENT_ACCOUNT" in p for p in problems)

    # права на секреты проверяются отдельно от учётной записи
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "cosmos-agent")
    monkeypatch.setattr(evolution_runner,
                        "_acl_lets_ordinary_users_write", lambda p: True)
    problems = evolution_runner.isolation_problems()
    assert any("может изменить любой" in p for p in problems)


def test_preflight_is_not_fooled_by_a_declared_but_unused_account(monkeypatch):
    """Повторный аудит 2026-07-26: прежняя проверка принимала НАЛИЧИЕ
    переменной за изоляцию и возвращала «проблем нет», тогда как кандидат
    запускался обычным subprocess из веб-процесса и наследовал его
    учётную запись. Настройка объявлена — граница не построена.

    Пятый аудит (пакет C2) построил границу посредником, и формулировка
    причины сменилась: теперь её называет посредник. Утверждение
    осталось прежним по СУТИ — объявленная переменная сама по себе не
    имеет права давать зелёный свет, пока задача планировщика не
    зарегистрирована."""
    monkeypatch.setattr(evolution_runner,
                        "_acl_lets_ordinary_users_write", lambda p: False)
    monkeypatch.delenv("COSMOS_AGENT_TASK", raising=False)
    # объявлена ЧУЖАЯ запись — но запуска под ней в коде нет
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "cosmos-agent")
    problems = evolution_runner.isolation_problems()
    assert problems, "объявленная переменная не имеет права давать зелёный"
    assert any("COSMOS_AGENT_TASK" in p for p in problems)
    # объявлена ТА ЖЕ запись, под которой работает процесс
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT",
                       evolution_runner.current_process_account())
    problems = evolution_runner.isolation_problems()
    assert any("одна и та же запись" in p for p in problems)


def test_unisolated_env_bypass_needs_an_explicit_one(monkeypatch):
    """Повторный аудит 2026-07-26: обход изоляции принимал ЛЮБУЮ непустую
    строку, включая «0» — оператор, выключающий обход нулём, на деле его
    включал. Годится только явное «1»."""
    for value, allowed in (("", False), ("0", False), ("false", False),
                           ("да", False), ("1", True), (" 1 ", True)):
        if value:
            monkeypatch.setenv(evolution_runner._UNISOLATED_OK, value)
        else:
            monkeypatch.delenv(evolution_runner._UNISOLATED_OK,
                               raising=False)
        assert evolution_runner._unisolated_allowed_by_env() is allowed, \
            f"значение {value!r} должно давать {allowed}"


def test_unisolated_environment_asks_for_confirmation_not_refuses(env,
                                                                  monkeypatch):
    """Глухой отказ выключал контур самоулучшения целиком. Правильно —
    назвать препятствия и спросить подтверждение: решение принимает
    человек, но принимает ЯВНО (правка 2026-07-26)."""
    tmp, _notified = env
    row = _seed()
    # фикстура env глушит isolation_problems -> []; здесь нужна как раз
    # НЕизолированная среда — возвращаем настоящий список проблем
    monkeypatch.setattr(evolution_runner, "isolation_problems", lambda: [
        "не объявлена отдельная учётная запись агента "
        "(COSMOS_AGENT_ACCOUNT): агент работает под той же учётной "
        "записью, что и приложение, и видит Prod, Stage и секреты"])
    monkeypatch.delenv(evolution_runner._UNISOLATED_OK, raising=False)
    started = []
    monkeypatch.setattr(evolution_runner.threading, "Thread",
                        lambda **kw: type("T", (), {
                            "start": lambda self, kw=kw: started.append(kw)})())

    # первое нажатие: статус НЕ меняется, возвращаются препятствия
    res = evolution_runner.approve_action(row["id"], "t", "admin@x")
    assert res["ok"] is False
    assert res["error"] == "isolation_unconfirmed"
    assert res["problems"] and any("COSMOS_AGENT_ACCOUNT" in p
                                   for p in res["problems"])
    assert evolution.get(row["id"])["status"] == row["status"], \
        "отказ в подтверждении не должен менять статус находки"
    assert not started, "фаза не должна запускаться без подтверждения"

    # повторное нажатие с подтверждением: фаза запускается, и признак
    # подтверждения доезжает до самой фазы
    res2 = evolution_runner.approve_action(row["id"], "t", "admin@x",
                                           acknowledged_unisolated=True)
    assert res2["ok"] is True
    assert started and started[0]["kwargs"] == {
        "acknowledged_unisolated": True}


def test_isolated_environment_needs_no_confirmation(env, monkeypatch):
    """На укреплённой машине лишнего вопроса быть не должно."""
    tmp, _notified = env
    row = _seed()
    started = []
    monkeypatch.setattr(evolution_runner.threading, "Thread",
                        lambda **kw: type("T", (), {
                            "start": lambda self, kw=kw: started.append(kw)})())
    res = evolution_runner.approve_action(row["id"], "t", "admin@x")
    assert res["ok"] is True
    assert started and started[0]["kwargs"] == {}


def test_confirmed_run_proceeds_and_says_so(env, monkeypatch):
    """Подтверждённый запуск идёт до конца, а факт неизолированной среды
    попадает в ленту фазы — молчаливого обхода нет."""
    tmp, _notified = env
    row = _seed()
    # среда именно НЕизолированная — перекрываем заглушку фикстуры env
    monkeypatch.setattr(evolution_runner, "isolation_problems",
                        lambda: ["агент работает под той же учётной "
                                 "записью, что и приложение"])
    monkeypatch.delenv(evolution_runner._UNISOLATED_OK, raising=False)
    fake = FakeRun()
    _wire_happy_test_phase(fake, tmp)
    _wire_materialize(monkeypatch)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    evolution_runner.run_test_phase(row["id"], acknowledged_unisolated=True)
    snap = evolution_runner.get_scan_activity(0)
    notes = [e["detail"] for e in snap["events"] if e["kind"] == "note"]
    assert any("не изолирована" in n and "подтверждено" in n for n in notes)
    assert snap["events"][-1]["kind"] == "done"


# --- скрипт укрепления среды агента ------------------------------------------

import pathlib

_HARDEN = (pathlib.Path(__file__).resolve().parents[1]
           / "docs" / "ops" / "harden-agent-account.ps1")


def test_harden_script_description_fits_the_windows_limit():
    """У `New-LocalUser` описание ограничено 48 знаками. Прежний текст был
    51, и скрипт падал на первом же шаге — УЖЕ спросив пароль (проверено на
    legion 2026-07-26). Оператор при этом остаётся с наполовину
    выполненным укреплением и без учётной записи."""
    import re
    body = _HARDEN.read_text(encoding="utf-8-sig")
    found = re.findall(r'-Description\s+"([^"]*)"', body)
    assert found, "в скрипте не найден аргумент -Description"
    for text in found:
        assert len(text) <= 48, \
            f"описание длиной {len(text)} знаков не примет New-LocalUser"


def test_harden_script_grants_the_service_account_before_removing_groups():
    """Порядок критичен: служебная запись состоит в группе администраторов,
    но у НЕПОВЫШЕННОГО процесса эта группа в токене только запрещающая, и
    весь фактический доступ держится на `Authenticated Users` — ровно на
    той записи, которую скрипт убирает. Без явного права приложение
    потеряло бы доступ к базе пользователей, то есть вход отказал бы всем."""
    body = _HARDEN.read_text(encoding="utf-8-sig")
    grant = body.find('/grant "${AppUser}:(F)"')
    remove = body.find('/remove:g $who')
    assert grant != -1, "нет явного права служебной записи"
    assert remove != -1, "нет снятия широких групп"
    assert grant < remove, \
        "право служебной записи обязано выдаваться ДО снятия широких групп"


def test_agent_env_denylist_covers_the_secrets_the_product_actually_reads():
    """ТРЕТИЙ аудит 2026-07-26 (§5.3): список запрета дважды проверялся на
    выдуманных именах — продукт читает CREW_ANTHROPIC_API_KEY и
    CREW_SMTP_PASS, а тест чистил ANTHROPIC_API_KEY. Страж сканирует
    НАСТОЯЩИЕ чтения секретов в webapp/ и требует их присутствия в
    запретном списке; новое секретное имя без записи в списке провалит
    тест, а не молча уедет агенту."""
    import re

    webapp = pathlib.Path(__file__).resolve().parents[1] / "webapp"
    # Четвёртый аудит 2026-07-27 (§4.3): прежний шаблон видел только
    # двойные кавычки у environ.get и только файлы верхнего уровня —
    # одинарные кавычки, os.getenv, environ[...] и подкаталоги проходили
    # мимо. Теперь охвачены все четыре формы и всё дерево webapp/.
    # Вычисляемые имена шаблоном не ловятся принципиально — это предел
    # текстового стража, он назван в ответе на аудит.
    name = r"[A-Z_]*(?:KEY|TOKEN|SECRET|PASS)[A-Z_]*"
    # R5-05: продукт читает секреты и через помощники (`_env`, `read_env`)
    # — ловится ЛЮБОЙ вызов, чьё имя оканчивается на env, с литеральным
    # секретным именем первым аргументом. Вычисляемые имена текстовому
    # стражу недоступны принципиально — окончательный ответ только белый
    # список среды у посредника.
    pattern = re.compile(
        r'(?:environ(?:\.get)?\(|\b[A-Za-z_]*env\(|environ\[)\s*["\']('
        + name + r')["\']'
        r'|_ENV_TOKEN\s*=\s*["\']([A-Z_]+)["\']')
    found: set = set()
    for py in webapp.rglob("*.py"):
        for m in pattern.finditer(py.read_text(encoding="utf-8")):
            found.add(m.group(1) or m.group(2))
    # Токен запуска CLI наследуется агентом НАМЕРЕННО — без него агент
    # не работает вовсе (решение пакета 4, 2026-07-26).
    allowed = {"CLAUDE_CODE_OAUTH_TOKEN"}
    missing = sorted(found - allowed
                     - set(evolution_runner._AGENT_ENV_DENY))
    assert not missing, \
        f"секреты читаются продуктом, но не запрещены агенту: {missing}"
    # и известные имена присутствуют поимённо
    for name in ("CREW_ANTHROPIC_API_KEY", "CREW_SMTP_PASS",
                 "CREW_OLLAMA_KEY", "CREW_TG_BOT_TOKEN",
                 # Пятый аудит, пакет C1: путь к ключу подписи
                 # свидетельств. Агент, знающий его, подписал бы себе
                 # любое свидетельство.
                 "COSMOS_EVIDENCE_KEY"):
        assert name in evolution_runner._AGENT_ENV_DENY, name


# ---- свидетельство проверки как ПРЕГРАДА (пятый аудит, пакет C1) ------

def test_stage_refuses_without_evidence_from_test(env, monkeypatch):
    """Свидетельство должно быть преградой, а не украшением: без него
    фаза Stage не начинается вовсе."""
    tmp, _ = env
    row = _seed("NoEvidence")
    evolution.set_status(row["id"], evolution.STATUS_TEST_IMPROVED,
                         branch=f"improvement-{row['id']}")
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: "head")
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    evolution_runner.run_stage_phase(
        row["id"], countersign=_countersign(monkeypatch, row["id"], "stage"))
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_STAGE_FAILED
    assert "свидетельств" in (final["error"] or "")
    # и полная батарея даже не запускалась: отказ обязан быть дешёвым,
    # а полный замер идёт часами
    assert not any("_evolution_bench.py" in c[0] for c in fake.calls)


def test_stage_refuses_when_diff_differs_from_the_signed_one(env,
                                                             monkeypatch):
    """Ветку переписали после проверки: ревизия та же, состав другой."""
    tmp, _ = env
    row = _seed("SwappedDiff")
    evolution.set_status(row["id"], evolution.STATUS_TEST_IMPROVED,
                         branch=f"improvement-{row['id']}")
    from webapp import evidence
    evidence.issue(row["id"], "test", base_sha="basesha",
                   candidate_sha="head", diff_hash="signed-fp",
                   checks={"isolation": "broker"})
    monkeypatch.setattr(evidence, "diff_fingerprint",
                        lambda repo, base, head: "OTHER-fp")
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: "head")
    fake.rule(lambda cmd: cmd[:2] == ["git", "cat-file"],
              lambda cmd, cwd: (0, ""))
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")

    def bench(cmd, cwd):
        out = cmd[cmd.index("--out") + 1]
        with open(out, "w", encoding="utf-8") as f:
            f.write(_metrics())
        return ""
    fake.rule(lambda cmd: "_evolution_bench.py" in cmd and "run" in cmd,
              bench)
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    evolution_runner.run_stage_phase(
        row["id"], countersign=_countersign(monkeypatch, row["id"], "stage"))
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_STAGE_FAILED
    assert "состав изменений" in (final["error"] or "")


def test_prod_refuses_without_stage_evidence_before_merging(env, monkeypatch):
    """Отказ обязан случиться ДО слияния: откатывать нечего, если ничего
    не сливали."""
    tmp, _ = env
    row = _seed("NoStageEvidence")
    evolution.set_status(row["id"], evolution.STATUS_STAGE_PASSED,
                         branch=f"improvement-{row['id']}")
    evolution_runner._reviewed_commit_path(row["id"]).parent.mkdir(
        parents=True, exist_ok=True)
    evolution_runner._reviewed_commit_path(row["id"]).write_text(
        "reviewedsha", encoding="utf-8")
    fake = FakeRun()
    fake.rule(lambda cmd: cmd[:2] == ["git", "rev-parse"],
              lambda cmd, cwd: "prevhead")
    fake.rule(lambda cmd: cmd[:2] == ["git", "cat-file"],
              lambda cmd, cwd: (0, ""))
    fake.rule(lambda cmd: cmd[0] == "git", lambda cmd, cwd: "")
    monkeypatch.setattr(evolution_runner, "_run", fake)
    monkeypatch.setattr(evolution_runner, "_run_streaming", fake.streaming)
    restarted = []
    monkeypatch.setattr(evolution_runner, "_detached_self_restart",
                        lambda: restarted.append(True))
    evolution_runner.deploy_prod(
        row["id"], countersign=_countersign(monkeypatch, row["id"], "prod"))
    final = evolution.get(row["id"])
    assert final["status"] == evolution.STATUS_DEPLOY_FAILED
    assert "свидетельств" in (final["error"] or "")
    assert not any(c[0][:2] == ["git", "merge"] for c in fake.calls)
    assert restarted == []
