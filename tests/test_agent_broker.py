"""Посредник запуска агента внедрения (пятый аудит, пакет C2).

Проверяется главное свойство: посредник отказывает всегда, кроме случая,
когда граница ДОКАЗАНА — задача зарегистрирована от имени отдельной
записи и владелец живого процесса совпал с ней. Всё остальное — отказ, а
не «продолжим как раньше».
"""
from __future__ import annotations

import pytest

from webapp import agent_broker as ab

#: Записи стенда. Имя машины (`STAND`) и запись владельца (`owner`)
#: ВЫМЫШЛЕННЫЕ: у каждой установки они свои, а стенд подставляет
#: их сам — task_principal и account_sid подменены. Имя настоящей
#: машины разработчика создавало бы вид зависимости от неё.
APP_SID = "S-1-5-21-1-1-1-1000"
AGENT_SID = "S-1-5-21-1-1-1-1500"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in ("COSMOS_AGENT_TASK", "COSMOS_AGENT_ACCOUNT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("COSMOS_AGENT_HANDOFF", str(tmp_path / "handoff"))
    from webapp import settings
    monkeypatch.setattr(settings, "process_token_sid", lambda: APP_SID)


def _configure(monkeypatch, *, principal="STAND\\cosmos-agent",
               sids=None):
    monkeypatch.setenv("COSMOS_AGENT_TASK", "COSMOS Agent")
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "STAND\\cosmos-agent")
    table = sids if sids is not None else {
        "STAND\\cosmos-agent": AGENT_SID}
    monkeypatch.setattr(ab, "task_principal", lambda: principal)
    monkeypatch.setattr(ab, "account_sid",
                        lambda name: table.get(name, ""))


def test_without_task_broker_is_unavailable(monkeypatch):
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "STAND\\cosmos-agent")
    probs = ab.problems()
    assert not ab.available()
    assert any("COSMOS_AGENT_TASK" in p for p in probs)


def test_task_registered_for_another_account_is_refused(monkeypatch):
    _configure(monkeypatch, principal="STAND\\owner",
               sids={"STAND\\cosmos-agent": AGENT_SID,
                     "STAND\\owner": APP_SID})
    assert any("разные записи" in p for p in ab.problems())


def test_task_running_as_the_app_account_is_refused(monkeypatch):
    """Задача есть, имя совпало — но это запись самого приложения."""
    _configure(monkeypatch, sids={"STAND\\cosmos-agent": APP_SID})
    assert any("границы нет" in p for p in ab.problems())


def test_configured_broker_reports_no_problems(monkeypatch):
    _configure(monkeypatch)
    assert ab.problems() == []
    assert ab.available()


def test_launch_refuses_when_started_process_has_wrong_owner(monkeypatch,
                                                             tmp_path):
    """Сердцевина пакета: сверяется владелец ФАКТИЧЕСКИ запущенного
    процесса, а не свойство задачи."""
    _configure(monkeypatch)
    killed = []
    monkeypatch.setattr(ab, "_ps", lambda script, timeout_s=60: (0, ""))
    monkeypatch.setattr(ab, "kill", lambda pid: killed.append(pid))
    monkeypatch.setattr(ab, "_wait_for_pid", lambda p, t: 4242)
    monkeypatch.setattr(ab, "process_owner_sid", lambda pid: APP_SID)
    with pytest.raises(ab.BrokerError, match="4242"):
        ab.launch("сделай", run_id="r1")
    assert killed == [4242], "чужой процесс обязан быть снят, а не оставлен"


def test_launch_refuses_when_owner_unreadable(monkeypatch):
    _configure(monkeypatch)
    killed = []
    monkeypatch.setattr(ab, "_ps", lambda script, timeout_s=60: (0, ""))
    monkeypatch.setattr(ab, "kill", lambda pid: killed.append(pid))
    monkeypatch.setattr(ab, "_wait_for_pid", lambda p, t: 7)
    monkeypatch.setattr(ab, "process_owner_sid", lambda pid: "")
    with pytest.raises(ab.BrokerError, match="владельца"):
        ab.launch("сделай", run_id="r1")
    assert killed == [7]


def test_launch_hands_off_the_task_and_returns_owner(monkeypatch):
    _configure(monkeypatch)
    calls = []

    def _ps(script, timeout_s=60):
        calls.append(script)
        return 0, ""

    monkeypatch.setattr(ab, "_ps", _ps)
    monkeypatch.setattr(ab, "_wait_for_pid", lambda p, t: 99)
    monkeypatch.setattr(ab, "process_owner_sid", lambda pid: AGENT_SID)
    info = ab.launch("текст задания", run_id="r7")
    assert info == {"pid": 99, "owner_sid": AGENT_SID,
                    "handoff": str(ab.handoff_dir() / "r7")}
    assert (ab.handoff_dir() / "r7" / "task.txt").read_text(
        encoding="utf-8") == "текст задания"
    assert any("Start-ScheduledTask" in c for c in calls)


def test_launch_exec_mode_writes_script_and_cleans_stale(monkeypatch):
    """Режим exec (пакет C, аудит R10 P0-04): проверка кандидата идёт под
    записью агента — exec.ps1 пишется свежим, а несвежий сценарий прежнего
    прогона убирается и в обычном режиме (иначе задача исполнила бы чужую
    команду)."""
    _configure(monkeypatch)
    monkeypatch.setattr(ab, "_ps", lambda s, timeout_s=60: (0, ""))
    monkeypatch.setattr(ab, "_wait_for_pid", lambda p, t: 99)
    monkeypatch.setattr(ab, "process_owner_sid", lambda pid: AGENT_SID)
    root = ab.handoff_dir() / "rx"
    root.mkdir(parents=True, exist_ok=True)
    (root / "exec.ps1").write_text("stale", encoding="utf-8")
    ab.launch("exec: pytest", run_id="rx",
              exec_script="Set-Location 'C:\\x'; & 'py' '-m' 'pytest'")
    text = (root / "exec.ps1").read_text(encoding="utf-8-sig")
    assert "pytest" in text and "stale" not in text
    ab.launch("обычное задание", run_id="rx")
    assert not (root / "exec.ps1").exists()


def test_launch_without_configuration_never_starts_anything(monkeypatch):
    started = []
    monkeypatch.setattr(ab, "_ps",
                        lambda s, timeout_s=60: (started.append(s), (0, ""))[1])
    with pytest.raises(ab.BrokerError, match="не готов"):
        ab.launch("сделай", run_id="r1")
    assert not any("Start-ScheduledTask" in s for s in started)


def test_isolation_flags_explicit_agent_grant(monkeypatch, tmp_path):
    """Слепое пятно с живой площадки (2026-08-07): после отключения
    наследования на users.db осталась ЯВНАЯ строка `cosmos-agent:(F)` —
    групповая проверка молчала, а запись агента могла править базу
    пользователей. Явная выдача агенту — препятствие изоляции; запрет
    (deny) при этом ложно не срабатывает."""
    from webapp import evolution_runner as er
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "STAND\\cosmos-agent")
    listing = (r"C:\x\users.db BUILTIN\Administrators:(F)" "\n"
               r"              NT AUTHORITY\SYSTEM:(F)" "\n"
               r"              STAND\owner:(M)" "\n"
               r"              STAND\cosmos-agent:(F)" "\n")
    monkeypatch.setattr(er, "_run",
                        lambda cmd, cwd, timeout_s: (0, listing))
    assert er._acl_lets_agent_write("C:\\x\\users.db") is True
    # запрет — не разрешение
    denied = listing.replace("STAND\\cosmos-agent:(F)",
                             "STAND\\cosmos-agent:(DENY)(F)")
    monkeypatch.setattr(er, "_run",
                        lambda cmd, cwd, timeout_s: (0, denied))
    assert er._acl_lets_agent_write("C:\\x\\users.db") is False
    # без явной строки агента — чисто
    clean = "\n".join(ln for ln in listing.splitlines()
                      if "cosmos-agent" not in ln)
    monkeypatch.setattr(er, "_run",
                        lambda cmd, cwd, timeout_s: (0, clean))
    assert er._acl_lets_agent_write("C:\\x\\users.db") is False


def test_isolation_report_names_the_broker(monkeypatch):
    """Проверка изоляции обязана называть причину словами посредника, а
    не прежним «не реализовано»."""
    from webapp import evolution_runner as er
    monkeypatch.setenv("COSMOS_AGENT_ACCOUNT", "STAND\\cosmos-agent")
    monkeypatch.setattr(er, "_acl_lets_ordinary_users_write", lambda p: False)
    monkeypatch.setattr(er, "current_process_account", lambda: "STAND\\owner")
    probs = er.isolation_problems()
    assert any(p.startswith("посредник запуска:") for p in probs)


def test_isolation_clean_when_broker_ready(monkeypatch):
    from webapp import evolution_runner as er
    _configure(monkeypatch)
    monkeypatch.setattr(er, "_acl_lets_ordinary_users_write", lambda p: False)
    monkeypatch.setattr(er, "current_process_account", lambda: "STAND\\owner")
    assert er.isolation_problems() == []
