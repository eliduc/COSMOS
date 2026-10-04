"""Ротация ключа (R4 §4.4): права сужаются до записи секрета, отказ
сужения — отказ ротации, каждый код возврата icacls проверяется."""
import subprocess

import pytest

from webapp import settings


def test_rotation_fails_closed_when_icacls_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY_PATH", tmp_path / "claude_api_key")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    (tmp_path / "claude_api_key").write_text("sk-ant-old", "utf-8")
    # Эмуляция Windows — подменой СОБСТВЕННОГО флага модуля, не
    # глобального os.name: тот протекал в диспетчеризацию классов
    # pathlib, и на Linux ротация строила WindowsPath с несуществующим
    # «обратнослэшным» именем.
    monkeypatch.setattr(settings, "_IS_WINDOWS", True)

    def bad_run(args, **kw):
        class R:
            returncode = 0
            stdout = b'"STAND-owner","S-1-5-21-000"'
            stderr = b""
        if args and args[0] == "whoami":
            return R()                      # SID процесса определился
        R.returncode = 5
        R.stdout = b"Access is denied."
        return R()
    monkeypatch.setattr(subprocess, "run", bad_run)
    with pytest.raises(RuntimeError):
        settings.set_api_key("sk-ant-new-key")
    assert (tmp_path / "claude_api_key").read_text("utf-8") == "sk-ant-old", \
        "при отказе сужения прежний ключ обязан остаться нетронутым"


def test_rotation_narrows_before_writing_the_secret(tmp_path, monkeypatch):
    """Секрет не имеет права появиться в файле с широкими правами даже на
    мгновение: сужение идёт по ПУСТОМУ временному файлу."""
    monkeypatch.setattr(settings, "API_KEY_PATH", tmp_path / "claude_api_key")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    # Эмуляция Windows — подменой СОБСТВЕННОГО флага модуля, не
    # глобального os.name: тот протекал в диспетчеризацию классов
    # pathlib, и на Linux ротация строила WindowsPath с несуществующим
    # «обратнослэшным» именем.
    monkeypatch.setattr(settings, "_IS_WINDOWS", True)
    seen = []

    def spy_run(args, **kw):
        if args and args[0] == "whoami":
            class W:
                returncode = 0
                stdout = b'"STAND-owner","S-1-5-21-000"'
                stderr = b""
            return W()
        if len(args) == 2:
            # чтение итогового списка прав (_assert_narrow_acl) — не
            # запись; секрет в файле на этом шаге УЖЕ ДОЛЖЕН быть
            class A:
                returncode = 0
                stdout = b"narrow"
                stderr = b""
            return A()
        target = args[1]
        try:
            body = settings.Path(target).read_text("utf-8")
        except OSError:
            body = "<нет файла>"
        seen.append(body)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()
    monkeypatch.setattr(subprocess, "run", spy_run)
    settings.set_api_key("sk-ant-fresh")
    assert seen, "icacls обязан вызываться"
    assert all("sk-ant-fresh" not in b for b in seen), \
        "во время сужения прав секрета в файле быть не должно"
    assert (tmp_path / "claude_api_key").read_text("utf-8") == "sk-ant-fresh"
