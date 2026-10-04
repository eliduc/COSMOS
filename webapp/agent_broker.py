"""Посредник запуска агента внедрения под отдельной учётной записью.

Пятый аудит, пакет C2. Пять раундов подряд повторяли одно: агент
внедрения исполняет недоверенный план (текст находки приходит из веба), а
запускается обычным `subprocess.Popen` из веб-процесса — то есть под той
же учётной записью, что и приложение, с доступом к секретам, рабочей базе
пользователей и деревьям Stage и Prod. Объявленная переменная
`COSMOS_AGENT_ACCOUNT` границей не была: она НАЗЫВАЛА запись, под которой
никто не запускался.

Посредник строит границу средствами операционной системы:

* работу выполняет ЗАРЕГИСТРИРОВАННАЯ задача планировщика, у которой в
  свойстве «от чьего имени» стоит отдельная запись. Задачу регистрирует
  человек с правами администратора один раз (см.
  `docs/ops/agent-isolation-RU.md`); приложение её только запускает и
  не может ни создать, ни переписать;
* передача задания идёт файлом в каталоге ПЕРЕДАЧИ внутри дерева Test —
  единственном месте, куда пишут обе стороны;
* после старта проверяется ФАКТИЧЕСКИЙ владелец запущенного процесса: не
  объявление, не свойство задачи, а идентификатор безопасности маркера
  живого процесса. Несовпадение — процесс снимается, запуск отменяется.

Предел проверки назван вслух: посредник доверяет планировщику в том, что
запущенный им процесс — это и есть процесс задачи. Подмену номера
процесса в файле передачи ловит именно сверка владельца: подсунуть можно
только процесс, УЖЕ работающий под записью агента.

Задачи нет — посредник недоступен, и конвейер идёт прежним честным
путём: предупреждение о неизолированной среде и явное подтверждение
администратора.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


class BrokerError(RuntimeError):
    """Посредник не может гарантировать заявленную границу. Во всех
    случаях запуск отменяется, а не продолжается «как раньше»."""


def task_name() -> str:
    return (os.environ.get("COSMOS_AGENT_TASK") or "").strip()


def declared_account() -> str:
    return (os.environ.get("COSMOS_AGENT_ACCOUNT") or "").strip()


def handoff_dir() -> Path:
    from . import evolution_runner as er
    return Path(os.environ.get("COSMOS_AGENT_HANDOFF")
                or (er.test_dir() / "agent-handoff"))


def _ps(script: str, timeout_s: float = 60) -> tuple[int, str]:
    """Одна точка вызова PowerShell (тесты подменяют её)."""
    try:
        r = subprocess.run([*PS, script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout_s, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def account_sid(name: str) -> str:
    """Идентификатор безопасности учётной записи по её имени.

    Имя изменяемо и локализуемо, идентификатор — нет; сверка идёт только
    по нему (тот же урок, что и в ротации ключа: R5-01)."""
    if not name:
        return ""
    safe = name.replace("'", "''")
    code, out = _ps(
        f"(New-Object System.Security.Principal.NTAccount('{safe}'))"
        f".Translate([System.Security.Principal.SecurityIdentifier]).Value")
    line = (out or "").strip().splitlines()[-1].strip() if out else ""
    return line if code == 0 and line.startswith("S-1-") else ""


def task_principal() -> str:
    """От чьего имени зарегистрирована задача. Через `Get-ScheduledTask`,
    а не `schtasks /query`: вывод последнего локализован, и разбор по
    русской подписи «От имени пользователя» сломался бы на английской
    системе."""
    name = task_name()
    if not name:
        return ""
    safe = name.replace("'", "''")
    code, out = _ps(f"(Get-ScheduledTask -TaskName '{safe}')"
                    f".Principal.UserId")
    return (out or "").strip().splitlines()[-1].strip() if code == 0 else ""


def process_owner_sid(pid: int) -> str:
    """Владелец ЖИВОГО процесса по идентификатору безопасности маркера."""
    code, out = _ps(
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
        f"if ($p) {{ (Invoke-CimMethod -InputObject $p "
        f"-MethodName GetOwnerSid).Sid }}")
    line = (out or "").strip().splitlines()[-1].strip() if out else ""
    return line if code == 0 and line.startswith("S-1-") else ""


def problems() -> list[str]:
    """Что мешает считать посредника готовым. Пустой список — граница
    построена и проверяема."""
    out: list[str] = []
    name = task_name()
    declared = declared_account()
    if not declared:
        out.append("не объявлена учётная запись агента "
                   "(COSMOS_AGENT_ACCOUNT)")
    if not name:
        out.append("не объявлена задача планировщика, запускающая агента "
                   "(COSMOS_AGENT_TASK): запуск пойдёт обычным subprocess "
                   "под учётной записью приложения")
        return out
    principal = task_principal()
    if not principal:
        out.append(f"задача планировщика «{name}» не найдена")
        return out
    want = account_sid(declared)
    have = account_sid(principal)
    if not want or not have:
        out.append(f"не удалось разрешить учётные записи в идентификаторы "
                   f"безопасности (объявлена «{declared}», задача — "
                   f"«{principal}»)")
    elif want != have:
        out.append(f"задача «{name}» зарегистрирована от имени «{principal}», "
                   f"а объявлена «{declared}» — это разные записи")
    else:
        from . import settings
        if have == settings.process_token_sid():
            out.append(f"задача «{name}» работает от имени той же записи, "
                       f"что и приложение — границы нет")
    return out


def available() -> bool:
    return not problems()


def _wait_for_pid(pid_file: Path, timeout_s: float) -> int:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                return int(raw)
        except OSError:
            pass
        time.sleep(0.5)
    raise BrokerError(
        f"задача не сообщила номер процесса за {int(timeout_s)} с — "
        f"проверьте, что запускающий сценарий пишет {pid_file.name}")


def kill(pid: int) -> None:
    _ps(f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue")
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                   capture_output=True, check=False)


def launch(payload: str, *, run_id: str, start_timeout_s: float = 120,
           exec_script: str | None = None) -> dict:
    """Передать задание задаче планировщика и убедиться, что работающий
    процесс принадлежит записи агента.

    Два режима задания (пакет C, аудит R10 P0-04): без `exec_script` —
    прежний, запускающий сценарий кормит `task.txt` клиенту модели;
    с `exec_script` — сценарий исполняет `exec.ps1` (проверка кандидата:
    pytest/замер под записью агента, а не под подписантом). Владелец
    процесса сверяется одинаково в обоих режимах.

    Возвращает описание запуска: номер процесса, его владелец и каталог
    передачи. Любое несовпадение — снятие процесса и `BrokerError`."""
    probs = problems()
    if probs:
        raise BrokerError("посредник не готов: " + "; ".join(probs))
    root = handoff_dir() / run_id
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "pid.txt"
    for stale in (pid_file, root / "done.txt", root / "exec.ps1"):
        try:
            stale.unlink()
        except OSError:
            pass
    (root / "task.txt").write_text(payload, encoding="utf-8")
    if exec_script is not None:
        # BOM обязателен: без него powershell читает файл в ANSI и
        # кириллица в путях ломает сценарий.
        (root / "exec.ps1").write_text(exec_script, encoding="utf-8-sig")
    # Точный идентификатор прогона для запускающего сценария (внешний
    # аудит R7, P0-06в): прежде сценарий брал «самую свежую папку» — при
    # соседнем каталоге задание могло быть подменено. Указатель пишется
    # ДО старта задачи; сценарий читает ровно его.
    (root.parent / "current-run.txt").write_text(run_id, encoding="ascii")
    safe = task_name().replace("'", "''")
    code, out = _ps(f"Start-ScheduledTask -TaskName '{safe}'")
    if code != 0:
        raise BrokerError(f"не удалось запустить задачу: {out[-300:]}")

    pid = _wait_for_pid(pid_file, start_timeout_s)
    owner = process_owner_sid(pid)
    want = account_sid(declared_account())
    if not owner:
        kill(pid)
        raise BrokerError(
            f"не удалось прочитать владельца процесса {pid} — запуск "
            f"отменён (недоказанная изоляция хуже объявленной)")
    if owner != want:
        kill(pid)
        raise BrokerError(
            f"процесс {pid} принадлежит {owner}, а ожидалась запись агента "
            f"{want} — процесс снят, запуск отменён")
    from . import settings
    if owner == settings.process_token_sid():
        kill(pid)
        raise BrokerError(
            f"процесс {pid} работает под учётной записью приложения — "
            f"границы нет, процесс снят")
    return {"pid": pid, "owner_sid": owner, "handoff": str(root)}
