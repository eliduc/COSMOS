# -*- coding: utf-8 -*-
"""Гейты деинсталлятора: что он считает продуктом, а что чужим.

Зачем этот файл существует. Все дефекты сноса, найденные на живой
машине, были одного класса — «тихий пропуск»: неопределённость
трактовалась как «делать нечего», а не как отказ или вопрос. Пробы,
которые их поймали, жили в песочнице сессии и исчезли бы вместе с ней.
Здесь они закреплены: каждая проверка отвечает конкретному дефекту,
названному в заголовке теста.

Ничего настоящего эти тесты не трогают: дерево, журнал и опись строятся
во временном каталоге, каталог состояния подменяется через окружение.
"""
import gzip
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uninstall_cosmos as un                                    # noqa: E402


# --------------------------------------------------------------- оснастка

@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """Каталог журналов и описей — во временном месте, а не в профиле."""
    base = tmp_path / "state"
    base.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    return base


def make_tree(root: Path) -> list[Path]:
    """Небольшое дерево «установки»: продукт, слепая зона, рабочие файлы."""
    (root / "webapp" / "config").mkdir(parents=True)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / "webapp" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / ".venv" / "Scripts" / "python.exe").write_bytes(b"\x00")
    return [p for p in root.rglob("*")]


def write_manifest(path: Path, root: Path, objects) -> None:
    """Опись в нынешнем формате: сжатая, с относительными путями."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"корень": str(root), "формат": 2},
                            ensure_ascii=False) + "\n")
        for p in objects:
            rel = str(p.relative_to(root))
            if p.is_dir():
                fh.write(json.dumps([rel], ensure_ascii=False) + "\n")
            else:
                st = p.stat()
                fh.write(json.dumps([rel, st.st_size, int(st.st_mtime_ns)],
                                    ensure_ascii=False) + "\n")


class Log:
    """Тихий приёмник вывода: тесту важен вердикт, а не печать."""

    def __init__(self):
        self.lines = []

    def say(self, text=""):
        self.lines.append(text)

    def step(self, title):
        self.lines.append(title)

    def counted(self, gate, examined, **facts):
        self.lines.append((gate, examined, facts))

    def record(self, kind, **payload):
        pass


# ------------------------------------------------- опись: чтение форматов

def test_manifest_reads_both_formats(tmp_path):
    """Снос обязан читать и нынешнюю опись, и снятую прежней версией:
    отказ работать с прежней установкой оставил бы её неудаляемой."""
    root = tmp_path / "cosmos"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")

    new = tmp_path / "manifest-new.jsonl.gz"
    write_manifest(new, root, [root / "a.txt"])
    got_new = un.load_manifest(new)
    assert got_new is not None
    assert str(root / "a.txt").lower() in got_new

    old = tmp_path / "manifest-old.jsonl"
    old.write_text(json.dumps({"п": str(root / "a.txt"), "р": 1, "и": 5},
                              ensure_ascii=False) + "\n", encoding="utf-8")
    got_old = un.load_manifest(old)
    assert got_old is not None
    assert str(root / "a.txt").lower() in got_old


def test_manifest_absent_is_not_a_crash(tmp_path):
    """Описи может не быть — это не отказ, а возврат к прежней
    осторожности."""
    assert un.load_manifest(tmp_path / "нет-такого.jsonl.gz") is None


# ------------------------------------------------------ гейт постороннего

def test_manifest_gate_accepts_only_what_was_installed(tmp_path):
    """Чистое дерево проходит целиком: посторонних быть не должно."""
    root = tmp_path / "cosmos"
    root.mkdir()
    objects = make_tree(root)
    man = tmp_path / "m.jsonl.gz"
    write_manifest(man, root, objects)
    log = Log()
    un.gate_by_manifest(root, un.load_manifest(man), log)
    gate = [l for l in log.lines if isinstance(l, tuple)][0]
    assert gate[2][un.T("cnt.foreign")] == 0


def test_manifest_gate_catches_alien_in_former_blind_zone(tmp_path):
    """ДЕФЕКТ: поддеревья .git, .venv, .tools принимались целиком, и
    перенесённый туда документ владельца снос уничтожил бы молча."""
    root = tmp_path / "cosmos"
    root.mkdir()
    objects = make_tree(root)
    man = tmp_path / "m.jsonl.gz"
    write_manifest(man, root, objects)
    alien = root / ".venv" / "договор-заказчика.docx"
    alien.write_text("документ владельца", encoding="utf-8")

    with pytest.raises(un.Stop):
        un.gate_by_manifest(root, un.load_manifest(man), Log())


def test_manifest_gate_catches_substitution_under_product_name(tmp_path):
    """ДЕФЕКТ: имя из описи считалось доказательством. Под именем файла
    программы может лежать чужой документ — ловится по размеру и времени."""
    root = tmp_path / "cosmos"
    root.mkdir()
    objects = make_tree(root)
    man = tmp_path / "m.jsonl.gz"
    write_manifest(man, root, objects)
    (root / "pyproject.toml").write_text(
        "это мой документ, а не файл программы\n", encoding="utf-8")

    with pytest.raises(un.Stop):
        un.gate_by_manifest(root, un.load_manifest(man), Log())


def test_manifest_gate_lets_through_traces_of_work(tmp_path):
    """Журналы, кеши интерпретатора и рабочие файлы приложения появляются
    ПОСЛЕ описи. Считать их посторонними — значит спотыкаться о работу
    самой программы."""
    root = tmp_path / "cosmos"
    root.mkdir()
    objects = make_tree(root)
    man = tmp_path / "m.jsonl.gz"
    write_manifest(man, root, objects)
    (root / "webapp-server.log").write_text("работа\n", encoding="utf-8")
    (root / "webapp" / "__pycache__").mkdir()
    (root / "webapp" / "__pycache__" / "app.pyc").write_bytes(b"\x00")
    (root / "webapp" / "config" / "auth.json").write_text("{}", encoding="utf-8")

    log = Log()
    un.gate_by_manifest(root, un.load_manifest(man), log)
    gate = [l for l in log.lines if isinstance(l, tuple)][0]
    assert gate[2][un.T("cnt.foreign")] == 0
    assert gate[2][un.T("cnt.runtime")] >= 3


def test_recorded_objects_do_not_blind_whole_subtrees(tmp_path):
    """ДЕФЕКТ: «продукт = объект журнала ИЛИ всё вложенное в него»
    превращало .venv и сам каталог цели в слепые зоны и сводило проверку
    к нулю. Совпадение обязано быть точным."""
    root = tmp_path / "cosmos"
    root.mkdir()
    make_tree(root)
    man = tmp_path / "m.jsonl.gz"
    write_manifest(man, root, [])          # опись пуста
    known = {str(root), str(root / ".venv")}   # как их пишет журнал

    with pytest.raises(un.Stop):
        un.gate_by_manifest(root, {"нет": None}, Log(), known)


# --------------------------------------------------------- отбор процессов

def test_launcher_invocation_is_not_a_substring_match():
    """ДЕФЕКТ: признак «путь есть в командной строке» ловил посторонние
    процессы, включая тот, что исполняет сам снос."""
    launchers = [r"C:\cosmos\run-cosmos.cmd"]
    assert un.invokes_launcher(
        r'C:\Windows\system32\cmd.exe  /K C:\cosmos\run-cosmos.cmd', launchers)
    assert not un.invokes_launcher(
        r'cmd.exe /c type C:\cosmos\run-cosmos.cmd', launchers)
    assert not un.invokes_launcher(
        r'powershell -Command "Get-Content C:\cosmos\run-cosmos.cmd"', launchers)


@pytest.mark.skipif(os.name != "nt",
                    reason="снимок WMI и пути C:\\ непереводимы: на POSIX "
                           "префикс цели строится через родной os.sep, и "
                           "обратный слэш в нём — обычный символ имени")
def test_process_of_the_application_is_recognised_by_argv0():
    """ДЕФЕКТ: виртуальное окружение Python 3.14 запускает БАЗОВЫЙ
    интерпретатор, поэтому путь образа у приложения ведёт в системный
    Python из охранного списка. Отбор по одному пути образа промахнулся
    бы мимо самого приложения."""
    target = Path(r"C:\cosmos")
    snap = [{
        "ProcessId": 1, "ParentProcessId": 0,
        "ExecutablePath": r"C:\Python314\python.exe",
        "CommandLine": r'"C:\cosmos\.venv\Scripts\python.exe" -m uvicorn webapp.app:app',
        "Created": 1,
    }]
    groups = un.classify_processes(snap, target, [str(target / "run-cosmos.cmd")])
    assert len(groups["наши"]) == 1


def test_argv0_recognition_with_native_paths(tmp_path):
    """Тот же признак на РОДНЫХ путях площадки: на POSIX снимок процессов
    вообще не заполняет путь образа, и argv[0] — единственный работающий
    признак; без переносимого двойника он там не покрыт ничем."""
    target = tmp_path / "cosmos"
    interp = target / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    snap = [{
        "ProcessId": 1, "ParentProcessId": 0,
        "ExecutablePath": "",
        "CommandLine": '"%s" -m uvicorn webapp.app:app' % (interp / "python"),
        "Created": 1,
    }]
    groups = un.classify_processes(snap, target, [])
    assert len(groups["наши"]) == 1


def test_unreadable_image_path_is_not_suspicion():
    """ДЕФЕКТ: у половины процессов машины путь образа не читается без
    прав администратора. Это обычное состояние, а не подозрение."""
    snap = [{"ProcessId": 2, "ParentProcessId": 0, "ExecutablePath": None,
             "CommandLine": "", "Created": 1}]
    groups = un.classify_processes(snap, Path(r"C:\cosmos"), [])
    assert groups["путь_нечитаем"] == 1
    assert groups["наши"] == []


def test_reused_pid_breaks_the_ancestor_chain():
    """Номер процесса переиспользуется: «родитель» моложе ребёнка
    родителем не является, и цепочка предков обязана на нём обрываться."""
    by_pid = {
        10: {"ProcessId": 10, "ParentProcessId": 20, "Created": 100},
        20: {"ProcessId": 20, "ParentProcessId": 0, "Created": 500},
    }
    assert un.ancestors_of(10, by_pid) == []


# ------------------------------------------------------------ прочие гейты

def test_data_dir_with_owner_files_stops(tmp_path):
    """Каталог данных удаляется, только если он в точности такой, каким
    его создала установка: любой файл внутри — данные владельца."""
    data = tmp_path / "cosmos-data"
    (data / "final-report").mkdir(parents=True)
    (data / "rules").mkdir()
    (data / "final-report" / "мой-отчёт.json").write_text("{}", encoding="utf-8")

    with pytest.raises(un.Stop):
        un.gate_data_dir(data, Log())


def test_data_dir_stops_by_name_on_catalog_request_queues(tmp_path):
    """Очереди заявок каталога шаблонов (шаг 6 каталога) — стоп ПОИМЁННО.

    Папки `cdpm-catalog-requests/` и `cosmos-catalog-requests/` создаёт
    работа программ, а не установка, и в `INSTALLER_DATA_DIRS` их нет.
    Общий стоп «в каталоге данных есть содержимое — это данные
    владельца» тут был бы безымянным: владелец не узнал бы, ЧТО именно
    перенести. Гейт обязан назвать пути и сказать «перенесите очереди
    заявок каталога».
    """
    data = tmp_path / "cosmos-data"
    (data / "final-report").mkdir(parents=True)
    (data / "rules").mkdir()
    (data / "cdpm-runs").mkdir()
    # Каталог данных без очередей — тот, каким его создала установка.
    assert un.gate_data_dir(data, Log()) == [
        p for p in sorted(data.iterdir())]

    queue = data / "cdpm-catalog-requests" / "outbox"
    queue.mkdir(parents=True)
    (queue / "req-00000000000000000001.json").write_text(
        "{}", encoding="utf-8")
    log = Log()
    with pytest.raises(un.Stop) as e:
        un.gate_data_dir(data, log)
    words = str(e.value)
    assert "перенесите очереди заявок каталога" in words, words
    assert str(data / "cdpm-catalog-requests") in words, words
    assert un.T("stop.data_not_empty") not in words, words
    # Путь очереди назван и в выводе гейта, не только в отказе.
    assert any("cdpm-catalog-requests" in str(x) for x in log.lines)

    # Очередь COSMOS под тем же каталогом данных — тот же поимённый стоп.
    import shutil
    shutil.rmtree(data / "cdpm-catalog-requests")
    (data / "cosmos-catalog-requests" / "outbox").mkdir(parents=True)
    with pytest.raises(un.Stop) as e2:
        un.gate_data_dir(data, Log())
    assert "перенесите очереди заявок каталога" in str(e2.value)
    assert str(data / "cosmos-catalog-requests") in str(e2.value)
    # Обе папки объявлены гейту поимённо и в перечень установщика не
    # входят (иначе снос удалил бы неотданные заявки).
    assert un.CATALOG_QUEUE_DIRS == {"cdpm-catalog-requests",
                                     "cosmos-catalog-requests"}
    assert not (un.CATALOG_QUEUE_DIRS & un.INSTALLER_DATA_DIRS)


def test_data_dir_journaled_carrier_passes_only_with_same_bytes(tmp_path):
    """Ревизия 1.18.5 (GPT-060/062): носитель раскладки ролей, который
    установка создала из образца и записала в журнал с отпечатком байтов,
    не считается данными владельца — пока его байты те же. Изменённый
    владельцем носитель — его данные: снос останавливается; запись без
    отпечатка (старый журнал) — тоже данные владельца."""
    import hashlib
    data = tmp_path / "cosmos-data"
    (data / "final-report").mkdir(parents=True)
    (data / "rules").mkdir()
    (data / "cdpm-runs").mkdir()
    carrier = data / "agents-default.json"
    carrier.write_bytes(b'{"worker": {}}\n')
    same = {str(carrier): hashlib.sha256(carrier.read_bytes()).hexdigest()}
    # Те же байты — проходит, носитель попадает в состав удаления.
    got = un.gate_data_dir(data, Log(), same)
    assert carrier in got
    # Владелец правил носитель — стоп.
    carrier.write_bytes(b'{"worker": {"channels": {"cloud": {"model": "my"}}}}\n')
    with pytest.raises(un.Stop):
        un.gate_data_dir(data, Log(), same)
    # Журнал без отпечатка — данные владельца, стоп.
    with pytest.raises(un.Stop):
        un.gate_data_dir(data, Log(), {str(carrier): None})
    # Без журнала — как прежде, стоп.
    with pytest.raises(un.Stop):
        un.gate_data_dir(data, Log())


def test_readonly_file_with_second_name_stops(tmp_path):
    """Удаление снимает атрибут «только чтение» с записи файла, а запись
    в NTFS одна на все его имена: у чужого оригинала со вторым именем
    внутри установки защита пропала бы."""
    if os.name != "nt":
        pytest.skip("жёсткие ссылки проверяются на Windows")
    root = tmp_path / "cosmos"
    root.mkdir()
    original = tmp_path / "чужой-оригинал.txt"
    original.write_text("важное", encoding="utf-8")
    link = root / "второе-имя.txt"
    os.system('mklink /H "%s" "%s" >nul 2>&1' % (link, original))
    if not link.exists():
        pytest.skip("жёсткая ссылка не создалась (файловая система)")
    os.system('attrib +R "%s"' % original)
    try:
        with pytest.raises(un.Stop):
            un.gate_hardlinks([link], Log())
    finally:
        os.system('attrib -R "%s"' % original)


def test_launchers_cover_every_service_the_installer_creates():
    """Пусковые файлы всех служб пакета известны сносу поимённо.

    Перечень решает два дела сразу: по нему снос узнаёт СВОИ процессы
    (иначе служба переживёт удаление) и по нему же строится слепая зона
    гейта постороннего (иначе созданный установщиком файл был бы объявлен
    чужим). Со шага 5 каталога служб три — приложение, панель данных и
    служба каталога; журнал работы каждой службы тоже перечислен.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import install_cosmos as ic

    checked = 0
    for name in ("run-cosmos", "run-solver", "run-cdpm", "run-catalog"):
        for ext in (".cmd", ".sh"):
            checked += 1
            assert name + ext in un.LAUNCHERS, name + ext
    for log in ("webapp-server.log", "solver-server.log", "cdpm-server.log",
                "catalog-server.log"):
        checked += 1
        assert log in un.BLIND_FILES, log
    # Порт службы каталога по умолчанию — тот же, что у самой службы:
    # установщик и служба обязаны говорить об одном порте, и судится это
    # носителем (catalog_service.settings.DEFAULT_PORT), а не числом,
    # повторяющим константу кода (разошлись бы молча — сторож литералов
    # среды, волна 9).
    from catalog_service import settings as catalog_settings
    assert ic.CATALOG_PORT == catalog_settings.DEFAULT_PORT
    print("рассмотрено имён: %d" % checked)
    assert checked == 12


def test_missing_translation_does_not_crash_on_substitution():
    """ДЕФЕКТ: при отсутствующем переводе функция возвращала ключ и
    падала на подстановке. Недостающий перевод не должен ронять снос."""
    assert "значение" in un.T("нет-такого-ключа", "значение")
