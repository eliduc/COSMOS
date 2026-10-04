"""Изоляция хранилищ наблюдений от рабочего webapp/config (волна 2).

Дыру заклеивали трижды и каждый раз постфактум: тесты писали в рабочий
конфиг правил, потом оставили отметку скана «Алгоритмов», потом журнал
эпизодов ремонта. Общая причина одна — изоляция была делом каждой
отдельной фикстуры, и про новый контур просто забывали.

Здесь проверяется само СРЕДСТВО: что autouse-изоляция действительно
уводит пути в tmp и что список хранилищ в conftest не отстал от кода.
"""
import importlib
import re
import pathlib

from tests.conftest import _OBSERVATION_STORES, SETTINGS_FILE_NAME


def test_product_settings_file_is_isolated_per_test():
    """Настройки продукта читаются обоими продуктами (ключи провайдеров,
    канал сертификатора, порог приемлемости пейринга) и лежат в каталоге
    данных машины, куда ПИШУТ живые службы — причём одним куском, без
    атомарной замены. Набор не имеет права ни читать этот файл, ни
    зависеть от того, что в нём лежит: чтение в миг записи получает
    обрезанный файл, и краснота становится плавающей."""
    from cdpm import providers

    path = pathlib.Path(str(providers.settings_path())).resolve()
    assert path.name == SETTINGS_FILE_NAME, (
        "настройки продукта не уведены фикстурой: %s" % path)
    assert not path.exists(), "изолированный файл настроек пуст по умолчанию"
    assert providers.load_settings() == {}


def test_every_store_is_redirected_out_of_the_working_config():
    """Ни один путь из списка не смотрит в рабочий webapp/config.

    Сравнение идёт с НАСТОЯЩИМ каталогом конфига, а не с подстрокой:
    достаточно один раз ошибиться в шаблоне, и проверка станет
    всегда-истинной, то есть бесполезной ровно там, где нужна."""
    from webapp import registry

    real_config = (registry.REPO_ROOT / "webapp" / "config").resolve()
    for mod_name, attr in _OBSERVATION_STORES:
        mod = importlib.import_module(mod_name)
        path = pathlib.Path(str(getattr(mod, attr))).resolve()
        assert real_config not in path.parents, (
            f"{mod_name}.{attr} пишет в рабочий конфиг: {path}")
        assert "isolated" in path.name, (
            f"{mod_name}.{attr} не подменён фикстурой: {path}")


def test_store_list_covers_every_observation_module():
    """Новый контур обязан попасть в _OBSERVATION_STORES.

    Кандидаты ищутся ПО ИСХОДНИКАМ, а не переписываются вторым списком.
    Первая версия этого теста сверяла _OBSERVATION_STORES с рукописным
    набором из тех же имён — то есть проверяла список сам собой и не
    могла провалиться никогда (ревью волны 2 назвало это театром, и
    справедливо). Здесь модуль признаётся хранилищем наблюдений, если в
    его тексте есть константа-путь, ведущая в webapp/config."""
    listed_mods = {m for m, _ in _OBSERVATION_STORES}
    webapp_dir = pathlib.Path(
        importlib.import_module("webapp").__file__).parent

    # Константа верхнего уровня вида  ИМЯ = ... "webapp" ... "config" ...
    # либо ... / "config" / "<файл>"  — ровно так объявлены все хранилища.
    decl = re.compile(
        r'^([A-Z][A-Z0-9_]*)\s*=\s*[^\n]*?"config"[^\n]*?"([^"]+\.(?:json|txt))"',
        re.M)

    found: set[tuple[str, str]] = set()
    for src in sorted(webapp_dir.glob("*.py")):
        text = src.read_text(encoding="utf-8")
        for const, filename in decl.findall(text):
            found.add((f"webapp.{src.stem}", const, filename))

    # Отсекаем РАБОЧИЕ конфиги: их изолирует отдельная фикстура, и они
    # не являются наблюдениями контуров. Список исключений явный и
    # короткий — новый файл в него сам не попадёт.
    NOT_OBSERVATIONS = {
        "airline_default.json", "users.db", "auth.json", "sessions.json",
        "ownership.json", "llm_scope.json", "cost_config.json",
        "scenarios.json", "vacation_bids.json", "qualifications.json",
        "roster_edits.json", "base_config.json", "crew_links.json",
        "crew_acks.json", "repairs.json", "audit_reports.json",
        "ops_log.json", "disruptions.json", "change_requests.json",
        "rule_packs.json", "studio_sessions.json",
        "schedule_deltas.json", "app_settings.json", "activity.jsonl",
        "activity_nav.jsonl", "evolution-last-scan.txt",
        "pay_config.json",           # ставки оплаты — настройка
        "instance_country.json",     # атрибут экземпляра — настройка
    }
    candidates = {(m, c) for m, c, f in found if f not in NOT_OBSERVATIONS}

    missing = {m for m, _ in candidates} - listed_mods
    assert not missing, (
        "хранилища наблюдений без изоляции в conftest._OBSERVATION_STORES: "
        f"{sorted(missing)} — добавьте их туда")

    # и обратно: перечисленное должно существовать
    for mod_name, attr in _OBSERVATION_STORES:
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, attr), f"{mod_name} потерял {attr}"


def test_repair_apply_records_into_the_isolated_store(tmp_path):
    """Сквозная проверка средства: запись эпизода уходит в tmp.

    Именно этот путь и протёк — apply_repair начал писать журнал, а
    фикстура теста сбоев про него не знала."""
    from webapp import repair_episodes

    path = str(repair_episodes.EPISODES_PATH)
    assert "isolated" in path, f"журнал эпизодов не изолирован: {path}"

    plan = {"available": True,
            "apply_sequence": [{"pid": "C_1", "slot": 0, "to": "42"}],
            "repairs": [{"pid": "C_1", "slot": 0, "from": "7", "to": "42",
                         "via": "direct", "alts": ["9", "11"],
                         "alts_total": 12, "skey": "abc123"}]}
    made = repair_episodes.record_plan("i1", plan, by="t@example.com",
                                       applied=1)
    assert len(made) == 1 and made[0]["alts"] == ["9", "11"]
    assert repair_episodes.EPISODES_PATH.exists()
    assert repair_episodes.counts()["situations"] == 1


def test_unapplied_plan_is_not_an_episode():
    """План, который человек не применил, наблюдением не является."""
    from webapp import repair_episodes

    plan = {"available": True,
            "apply_sequence": [{"pid": "C_1", "slot": 0, "to": "42"}],
            "repairs": [{"pid": "C_1", "slot": 0, "from": "7", "to": "42",
                         "via": "direct", "alts": [], "alts_total": 0,
                         "skey": "abc"}]}
    assert repair_episodes.record_plan("i1", plan, applied=0) == []
    assert repair_episodes.episodes() == []


def test_only_the_applied_prefix_is_recorded():
    """Ремонт обрывается на первой незаконной правке — записывается
    ровно применённый префикс, а не весь задуманный план."""
    from webapp import repair_episodes

    plan = {"available": True,
            "apply_sequence": [{"pid": f"C_{i}"} for i in range(4)],
            "repairs": [{"pid": f"C_{i}", "slot": 0, "from": "1", "to": "2",
                         "via": "direct", "alts": [], "alts_total": 0,
                         "skey": f"k{i}"} for i in range(4)]}
    made = repair_episodes.record_plan("i1", plan, applied=2)
    assert [r["skey"] for r in made] == ["k0", "k1"]
