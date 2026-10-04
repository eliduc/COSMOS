"""Test collection config (audit 2026-07-12): the legacy Python-Timefold
engine is an optional extra (does not install on Python 3.14); its tests
are excluded only when the package is actually absent, and the exclusion
is reported in the pytest header so it is visible, not silent."""
import importlib.util
import os
import sys

import pytest

# Личность стенда берётся ИМПОРТОМ из tests/_stand_identity.py, поэтому
# папка tests/ обязана быть в пути ДО него. В обычном прогоне pytest
# кладёт её туда сам (режим импорта prepend, пакета в tests/ нет);
# строка ниже — на случай чтения conftest вне такого прогона и
# одновременно объявление зависимости, а не молчаливое везение.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from _stand_identity import ADMIN_EMAIL                       # noqa: E402

# Обвязка сборки «один узел папки на прогон»: файл, названный в
# командной строке после файла из tests/, заставлял pytest 9.1 завести
# новый узел его подпапки, и фикстуры conftest подпапки пропадали
# («fixture 'service' not found»). Закон, причина (журнал изменений
# pytest 9.1.0, запись #14004) и свидетель названы в
# tests/_pytest_dir_node.py — здесь только регистрация хука. Модуль
# берётся по своему пути, а не по пути импорта: conftest читают и
# впрямую (свидетели сборки в подпроцессе), и там tests/ в пути нет.
_dir_node_spec = importlib.util.spec_from_file_location(
    "_pytest_dir_node",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "_pytest_dir_node.py"))
_dir_node = importlib.util.module_from_spec(_dir_node_spec)
_dir_node_spec.loader.exec_module(_dir_node)
pytest_collect_directory = _dir_node.pytest_collect_directory

# Умолчаний-админов в коде больше нет (пакет «умолчания-в-данные»,
# 2026-08-29): webapp/users.py сеет каталог из CREW_SUPER_ADMIN, а
# webapp/settings.py берёт супер-админа первым адресом оттуда же.
# Личность прогона — ВЫМЫШЛЕННЫЙ адрес _stand_identity.ADMIN_EMAIL
# (до 10.09.2026 здесь стояла личная почта владельца машины, и тот же
# литерал повторялся в ~40 файлах). Задаём её на ВЕСЬ прогон ДО первого
# импорта webapp.* (модули читают env при импорте). Поимённый allow-list
# обнуляется: значение из окружения машины сделало бы прогон
# недетерминированным. Тесты самого разбора env (test_users_admin,
# test_settings) подменяют значения monkeypatch'ем поверх этих.
os.environ["CREW_SUPER_ADMIN"] = ADMIN_EMAIL
os.environ["CREW_ALLOWLIST_EMAILS"] = ""

_LEGACY_TIMEFOLD_TEST_FILES = ["test_constraints.py", "test_feasible.py"]

collect_ignore = []
if importlib.util.find_spec("timefold") is None:
    collect_ignore += _LEGACY_TIMEFOLD_TEST_FILES
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_airline_config: тест проверяет проводку НАСТОЯЩЕГО пути "
        "webapp/config/airline_default.json — autouse-изоляция отключается")
    config.addinivalue_line(
        "markers",
        "real_observation_stores: тест работает с НАСТОЯЩИМИ путями "
        "хранилищ наблюдений — autouse-изоляция отключается")
    config.addinivalue_line(
        "markers",
        "hang_limit(seconds): поимённый предел времени для сторожа "
        "зависаний (умолчание COSMOS_TEST_HANG_LIMIT_S; 0 = без предела)")


def pytest_report_header(config):
    return [
        f"excluded: tests/{name} (requires optional python-timefold extra, "
        "not installed)"
        for name in collect_ignore
    ]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    # The header above is suppressed by -q; the terminal summary is not —
    # the exclusion must never be silent (audit round 6 verification).
    for name in collect_ignore:
        terminalreporter.write_line(
            f"excluded: tests/{name} (requires optional python-timefold "
            f"extra, not installed)")
    _report_artifact_skips(terminalreporter, config)
    _report_sac_block_skips(terminalreporter)


@pytest.fixture()
def admin_email():
    """Адрес супер-администратора прогона — ровно тот, которым задан
    `CREW_SUPER_ADMIN` выше. Тест, которому нужна личность, берёт её
    отсюда (или импортом `_stand_identity.ADMIN_EMAIL`), а не повторяет
    литералом: иначе ожидание совпадает с входом по случайности."""
    return ADMIN_EMAIL


# ---------------------------------------------------------------------------
# Сторож зависаний
# ---------------------------------------------------------------------------
#
# 2026-09-04: test_kpi_endpoints «висел» больше 20 минут без единой строки
# вывода — отчёт по парку обходил реестр на каждый набор, и при 573
# выгрузках один вызов шёл минуты. Прогон при этом молчал: pytest сам
# время теста не ограничивает, а faulthandler на Python 3.14 не
# разворачивает стеки чужих потоков (TestClient исполняет ручку в рабочем
# потоке AnyIO — в дампе был один кадр «<invalid frame>»). Сторож закрывает
# оба пробела: тест, не уложившийся в предел, печатает стеки ВСЕХ потоков
# (sys._current_frames — уровень Python, кадры полные) и завершает процесс
# кодом 70. Именно завершение, а не исключение: занятый рабочий поток
# исключением не прервать, а без os._exit процесс висел бы на выходе,
# дожидаясь его. Так же устроен pytest-timeout (method=thread); он не
# ставится, чтобы не менять общий venv без объявления.
#
# Предел: COSMOS_TEST_HANG_LIMIT_S (умолчание 900 с; 0 = выключить);
# поимённо — маркер @pytest.mark.hang_limit(seconds) для тестов, которые
# честно считают дольше (прогоны решателей).
_HANG_LIMIT_ENV = "COSMOS_TEST_HANG_LIMIT_S"
_HANG_LIMIT_DEFAULT_S = 900.0
_HANG_EXIT_CODE = 70


def _dump_all_threads(out) -> None:
    import sys
    import threading
    import traceback
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident, frame in sys._current_frames().items():
        out.write(f"\n--- поток {names.get(ident, '?')} ({ident}) ---\n")
        traceback.print_stack(frame, file=out)


@pytest.fixture(autouse=True)
def _hang_watchdog(request):
    import sys
    import threading
    marker = request.node.get_closest_marker("hang_limit")
    if marker and marker.args:
        limit = float(marker.args[0])
    else:
        raw = os.environ.get(_HANG_LIMIT_ENV, "").strip()
        limit = float(raw) if raw else _HANG_LIMIT_DEFAULT_S
    if limit <= 0:
        yield
        return

    capman = request.config.pluginmanager.getplugin("capturemanager")

    def fire():
        try:
            # pytest перехватывает дескрипторы 1/2 на уровне ОС: запись
            # даже в sys.__stderr__ уходит в его временный файл и после
            # os._exit пропадает. Снимаем глобальный перехват — так же
            # поступает pytest-timeout — и только потом печатаем.
            if capman is not None:
                capman.suspend_global_capture(in_=True)
            err = sys.stderr
            err.write(f"\n=== СТОРОЖ ЗАВИСАНИЙ: {request.node.nodeid} не "
                      f"уложился в {limit:g} с — стеки всех потоков, затем "
                      f"выход {_HANG_EXIT_CODE} ===\n")
            _dump_all_threads(err)
            err.flush()
        finally:
            os._exit(_HANG_EXIT_CODE)

    timer = threading.Timer(limit, fire)
    timer.daemon = True
    timer.name = "hang-watchdog"
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


@pytest.fixture(autouse=True)
def _isolate_roster_manifests(tmp_path, monkeypatch):
    """Ревью пакета (аудит-2): публикация active-roster манифестов не должна
    писать в НАСТОЯЩИЙ final-report репозитория из тестов — иначе тестовая
    метка оставляет указатель, а реальная одноимённая метка становится
    stale. Тесты, которым нужен свой каталог, перекрывают этот autouse
    своим monkeypatch."""
    import _roster_manifest
    _roster_manifest_dir = tmp_path / "roster-manifests-isolated"
    monkeypatch.setattr(_roster_manifest, "FINAL_REPORT",
                        _roster_manifest_dir)
    yield


@pytest.fixture(autouse=True)
def _isolate_airline_config(request, tmp_path, monkeypatch):
    """Волна 1 (2026-07-20): тесты НЕ должны писать в НАСТОЯЩИЙ рабочий
    конфиг правил webapp/config/airline_default.json — а писали (например,
    test_scenarios дёргал scenarios.AIRLINE_CONFIG напрямую), и правки
    оседали в репозитории, молча меняя правила боевого стенда.

    Прежде путь приходилось подменять в ТРЁХ модулях, которые его знали.
    С P1-10 (2026-07-26) объявление одно — `config_scope.INSTALL_FILES`, и
    подмена тоже одна. Заодно уводятся в сторону конфигурации ЭКЗЕМПЛЯРА:
    иначе тест, записавший правила набору, оставил бы файл в репозитории.

    Опт-аут: маркер @pytest.mark.real_airline_config — для тестов, которые
    проверяют саму ПРОВОДКУ реального пути (равенство окружения задач и
    стенда Evolution), а не поведение поверх конфига."""
    # Фоновый перенос конфигурации на старте приложения в тестах выключен:
    # поток, пишущий файлы параллельно проверкам, даёт ровно ту
    # недетерминированность, которую 2026-07-26 пришлось выкорчёвывать.
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    if request.node.get_closest_marker("real_airline_config"):
        yield
        return
    from webapp import config_scope as _cs
    iso = tmp_path / "config-isolated"
    iso.mkdir(parents=True, exist_ok=True)
    monkeypatch.setitem(_cs.INSTALL_FILES, _cs.DOMAIN_RULES,
                        iso / "airline_default.json")
    for domain in _cs.DOMAINS:
        monkeypatch.setitem(_cs.INSTANCE_DIRS, domain,
                            iso / f"instance-{domain}")
    monkeypatch.setitem(_cs.SCENARIO_DIRS, _cs.DOMAIN_RULES,
                        iso / "scenario-rules")
    yield


# Хранилища НАБЛЮДЕНИЙ: модуль -> имя атрибута с путём. Список ведётся
# здесь, а не в каждом тесте, потому что дыру заклеивали уже трижды и
# каждый раз — постфактум, когда файл уже оседал в репозитории. Новый
# контур обязан появиться в этом списке, и тест ниже это проверяет.
_OBSERVATION_STORES = (
    ("webapp.data_health", "SNAPSHOT_PATH"),
    ("webapp.planner_feedback", "OVERRIDE_PATH"),
    ("webapp.run_profile", "PROFILE_PATH"),
    ("webapp.connection_buffer", "SNAPSHOT_PATH"),
    ("webapp.repair_episodes", "EPISODES_PATH"),
    ("webapp.evolution_runner", "LAST_SCAN_AT_PATH"),
    # вход контура «Отзывы» и суточный счётчик Ask AI — тоже наблюдения,
    # а не настройки; найдены сканом исходников, когда проверка списка
    # перестала быть формальной (ревью волны 2)
    ("webapp.help_center", "SUGGESTIONS_PATH"),
    ("webapp.help_center", "ASK_STATS_PATH"),
    # хранилище Пакетов правил: эндпоинт-тесты импорта правил создавали
    # и удаляли пакеты в НАСТОЯЩЕМ webapp/config/rule_packs.json —
    # при падении теста мусор оседал в рабочем файле (ревью 2026-07-24)
    ("webapp.rule_packs", "PACKS_PATH"),
)


@pytest.fixture(autouse=True)
def _isolate_observation_stores(request, tmp_path, monkeypatch):
    """Волна 2 (2026-07-20): НИ ОДИН тест не пишет в рабочие хранилища
    наблюдений webapp/config/*.json.

    Раньше каждый контур изолировался руками в своей фикстуре, и каждый
    раз про какой-нибудь забывали: отметку скана «Алгоритмов» и журнал
    эпизодов ремонта тесты уже успели оставить в репозитории. Изоляция
    по умолчанию убирает весь класс: забыть теперь можно только одно —
    внести новый контур в _OBSERVATION_STORES, а это проверяется тестом.

    Опт-аут — маркер @pytest.mark.real_observation_stores."""
    if request.node.get_closest_marker("real_observation_stores"):
        yield
        return
    import importlib
    for mod_name, attr in _OBSERVATION_STORES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:                                # noqa: BLE001
            continue
        current = str(getattr(mod, attr))
        suffix = ".txt" if current.endswith(".txt") else ".json"
        # имя файла включает и АТРИБУТ: у одного модуля может быть
        # несколько хранилищ (help_center), и общее имя склеило бы их в
        # один файл, отчего один стор молча затирал бы другой
        stem = f"{mod_name.rsplit('.', 1)[-1]}-{attr.lower()}"
        monkeypatch.setattr(mod, attr, tmp_path / f"{stem}-isolated{suffix}")
    yield


# ---------------------------------------------------------------------------
# Тесты, которым нужны ОПУБЛИКОВАННЫЕ АРТЕФАКТЫ
# ---------------------------------------------------------------------------
#
# Часть набора проверяет поведение ПОВЕРХ результатов настоящих прогонов:
# каталогов цепочек, решений этапа 2, манифестов активного ростера. По
# устройству системы результаты живут в каталоге данных ВНЕ репозитория
# (`<репозиторий>-data`, см. `_data_paths`), поэтому в свежем клоне их нет.
#
# Чем это было: на машине разработчика такие тесты проходят, а на чистом
# клоне — падают. Первая же сборка на GitHub дала 69 падений и выглядела
# как поломка кода, хотя код был цел: не хватало артефактов, которых в
# репозитории и не должно быть.
#
# Чем стало: зависимость ОБЪЯВЛЕНА. Нет артефактов — такие тесты
# пропускаются с названной причиной, а число пропущенных печатается в
# итоге прогона. Молчаливым пропуск не бывает: это то же правило, по
# которому исключение тестов устаревшего движка выводится в итог.
#
# Список поимённый, а не помодульный: ни один модуль не зависит от
# артефактов целиком (в test_roster_board таких 13 из 33), и пропуск
# модулем спрятал бы полторы сотни здоровых проверок.
#
# Реестр охраняется: если перечисленного теста в наборе больше нет,
# итог прогона об этом скажет — список не должен тихо устаревать.
_NEEDS_PUBLISHED_ARTIFACTS = frozenset([
    "test_artifact_public_library.py::test_plain_user_downloads_public_reference_corpus",
    "test_base_balance.py::test_balance_score_in_range",
    "test_base_balance.py::test_base_balance_endpoint_on_seed",
    "test_base_balance.py::test_fleet_summary_present",
    "test_base_balance.py::test_headcount_matches_payload",
    "test_base_balance.py::test_inter_base_links_are_between_real_bases",
    "test_base_balance.py::test_over_base_flagged_when_util_exceeds_one",
    "test_base_balance.py::test_report_has_all_bases",
    "test_base_balance.py::test_transfer_total_bounded_by_surplus",
    "test_base_balance.py::test_transfers_go_from_surplus_to_deficit",
    "test_base_balance.py::test_utilization_and_required_are_consistent",
    "test_config_scope.py::test_costing_uses_the_instance_rates",
    "test_costing.py::test_costing_endpoint_on_seed_instance",
    "test_crew_portal.py::test_ack_and_staleness",
    "test_crew_portal.py::test_api_ack_flow",
    "test_crew_portal.py::test_api_cabinet_link_grants_cabinet_only",
    "test_crew_portal.py::test_board_move_is_reflected_in_cabinet",
    "test_crew_portal.py::test_cabinet_schedule_matches_solver_assignment",
    "test_disruption.py::test_api_flow_and_tenancy",
    "test_disruption.py::test_apply_is_reversible_board_moves",
    "test_disruption.py::test_extra_pids_manual_recrew",
    "test_disruption.py::test_pinned_broken_pairing_is_unpinned_and_repaired",
    "test_disruption.py::test_repair_covers_all_and_reduces_violations",
    "test_disruption.py::test_repair_prefers_reserves",
    "test_disruption.py::test_sickness_reaches_board_validator",
    "test_disruption.py::test_unbroken_pinned_pairings_never_move",
    "test_disruption_repair.py::test_api_event_and_status",
    "test_disruption_repair.py::test_prepare_rebuild_end_to_end_prep",
    "test_disruption_repair.py::test_surgery_preview_on_seed",
    "test_evolution_bench.py::test_stage1_cmd_matches_webapp_jobs",
    "test_evolution_bench.py::test_stage2_cmd_matches_webapp_jobs",
    "test_fair_share.py::test_api_plan_and_apply",
    "test_fair_share.py::test_apply_is_idempotent_at_optimum",
    "test_fair_share.py::test_apply_sequence_every_prefix_is_legal",
    "test_fair_share.py::test_apply_writes_reversible_board_moves",
    "test_fair_share.py::test_guarantee_zero_means_no_payroll_moves",
    "test_fair_share.py::test_plan_fairness_never_worsens_payroll",
    "test_fair_share.py::test_plan_improves_payroll_without_new_violations",
    "test_fair_share.py::test_plan_moves_are_individually_legal",
    "test_fair_share.py::test_plan_respects_pins",
    "test_kpi.py::test_fleet_kpi_scans_registry_once",
    "test_kpi.py::test_fleet_kpi_sorted_and_available_only",
    "test_kpi.py::test_instance_kpi_for_does_not_touch_registry",
    "test_kpi.py::test_instance_kpi_gerad1_end_to_end",
    "test_kpi.py::test_kpi_endpoints",
    "test_onboarding.py::test_demo_comparison_shape",
    "test_onboarding.py::test_demo_pipeline_shape",
    "test_pay_credit.py::test_api_pay_report",
    "test_pay_credit.py::test_board_move_shifts_credit",
    "test_pay_credit.py::test_instance_report_invariants",
    "test_pay_credit.py::test_instance_report_uses_active_fns",
    "test_pay_credit.py::test_roster_payroll_invariants",
    "test_roster_board.py::test_api_lifecycle",
    "test_roster_board.py::test_board_builds_from_stage2",
    "test_roster_board.py::test_board_truncation_is_self_consistent",
    "test_roster_board.py::test_fair_share_apply_does_not_fill_the_feedback_journal",
    "test_roster_board.py::test_move_back_to_solver_clears_edit",
    "test_roster_board.py::test_move_to_unqualified_crew_flags_type_rating",
    "test_roster_board.py::test_move_unknown_pid_or_crew_rejected",
    "test_roster_board.py::test_moving_two_pairings_onto_one_crew_flags_overlap",
    "test_roster_board.py::test_orphan_edits_are_pruned",
    "test_roster_board.py::test_owner_recorded_on_first_edit",
    "test_roster_board.py::test_pin_protects_from_move",
    "test_roster_board.py::test_reset_restores_solver_roster",
    "test_roster_board.py::test_unassign_flags_uncovered",
    "test_scenarios.py::test_compare_diffs_and_sides",
    "test_webapp_api.py::test_artifact_whitelisted_download",
    "test_webapp_api.py::test_instances_registry",
    "test_webapp_api.py::test_results_gerad1_pairing_view",
    "test_webapp_api.py::test_results_gerad1_roster_csv",
    "test_webapp_api.py::test_results_gerad1_roster_view",
    "test_webapp_api.py::test_results_gerad1_stage2_ok",
])

# Признак «артефакты опубликованы»: каталог цепочек и решение этапа 2
# витринного набора. Проверяются ОБА — половина цепочки не годится:
# часть тестов строит доску и расчёт оплаты поверх решения этапа 2.
_SHOWCASE_ARTIFACTS = ("gerad_colgen_selected_inst1.json",
                       "gerad_stage2_assignment_inst1.json")


def _published_artifacts_present():
    """Есть ли в каталоге данных опубликованные артефакты витринного набора."""
    try:
        import _data_paths
        final_report = _data_paths.data_root() / "final-report"
    except Exception:                                          # noqa: BLE001
        return False
    return all((final_report / name).is_file() for name in _SHOWCASE_ARTIFACTS)


def pytest_collection_modifyitems(config, items):
    """Пропустить зависимые от артефактов тесты, когда артефактов нет."""
    config._artifacts_present = _published_artifacts_present()
    seen = set()
    skipped = 0
    mark = pytest.mark.skip(
        reason="нужны опубликованные артефакты витринного набора "
               "(каталог цепочек и решение этапа 2); в каталоге данных их "
               "нет — результаты прогонов не хранятся в репозитории")
    for item in items:
        short = item.nodeid.split("tests/", 1)[-1].replace("\\", "/")
        if short in _NEEDS_PUBLISHED_ARTIFACTS:
            seen.add(short)
            if not config._artifacts_present:
                item.add_marker(mark)
                skipped += 1
    config._artifacts_skipped = skipped
    config._artifacts_missing_from_suite = sorted(
        _NEEDS_PUBLISHED_ARTIFACTS - seen)


def _report_artifact_skips(terminalreporter, config):
    """Итог по артефактам — вызывается из общего pytest_terminal_summary.

    Имя намеренно БЕЗ префикса `pytest_`: любую такую функцию в conftest
    pytest считает хуком и падает на неизвестном имени.
    """
    if getattr(config, "_artifacts_present", True):
        return
    n = getattr(config, "_artifacts_skipped", 0)
    terminalreporter.write_line(
        "пропущено %d: нужны опубликованные артефакты витринного набора "
        "(%s) — результаты прогонов не хранятся в репозитории"
        % (n, ", ".join(_SHOWCASE_ARTIFACTS)))
    stale = getattr(config, "_artifacts_missing_from_suite", [])
    if stale:
        terminalreporter.write_line(
            "ВНИМАНИЕ: в реестре зависимых тестов %d устаревших записей: %s"
            % (len(stale), ", ".join(stale[:5])))


@pytest.fixture(autouse=True)
def _isolate_anthropic_key_env(monkeypatch):
    """МП-2 (2026-08-21): set_provider_key("cloud", …) прокидывает ключ
    каналу выполнения переменной окружения ПРОЦЕССА — в тестах без
    изоляции она протекала между наборами (cdpm-тест задавал ключ,
    webapp-тесты settings видели «ключ задан»). Каждому тесту —
    чистая переменная; тест, которому ключ нужен, задаёт его сам."""
    monkeypatch.delenv("CREW_ANTHROPIC_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _test_site_local_model(monkeypatch):
    """РВ-119 / РВ-138, исправление 2: имени локальной модели в коде нет —
    площадка называет её сама (пусковик задаёт CREW_OLLAMA_MODEL). Тестовая
    площадка делает то же явно; тест, который судит закон «нет настройки —
    отказ», снимает переменную сам (`test_llm_backend.py`)."""
    monkeypatch.setenv("CREW_OLLAMA_MODEL", "qwen3.6:35b-a3b")


@pytest.fixture(autouse=True)
def _isolate_ensure_questions_contour(monkeypatch, tmp_path):
    """Зачистка 2026-08-29: гарантированные вопросы анкеты — данные
    КОНТУРА (cdpm-ensure-questions.json в каталоге данных). Набор
    судит МЕХАНИЗМ, а не содержимое контура машины — без изоляции
    результат тестов зависел бы от того, какой заказчик развёрнут.
    Пустое значение переменной = механизм спит; тест механизма задаёт
    свой файл сам."""
    monkeypatch.setenv("CDPM_ENSURE_QUESTIONS", "")
    # Ревизия 1.13: легаси-тексты форм — тоже носитель контура;
    # пустое значение = «не задан», случайная пересборка легаси
    # (≤1.12) в наборе падает громко и детерминированно.
    monkeypatch.setenv("CDPM_LEGACY_FORM_TEXTS", "")
    # Решение владельца 2026-08-29 (пункты 1-2): темы файла условий и
    # параметры миграций — носители контура; пусто = механизмы спят,
    # тест механизма задаёт свой файл сам.
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", "")
    monkeypatch.setenv("CDPM_MIGRATION_PARAMS", "")
    # Ревизия 1.14 (пакет Б): карты правил — носитель контура.
    monkeypatch.setenv("CDPM_RULE_MAPS", "")
    # 05.09: раскладка моделей ролей (agents-default.json в каталоге
    # данных) — тоже носитель контура. С живым носителем на машине
    # тесты панели шли мимо заглушек в настоящие каналы (26 красных
    # по таймауту за 11 минут). Пусто = площадочного дефолта нет;
    # тест механизма задаёт свой файл сам.
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", "")
    # Канал ВМП+МГ: адрес/токен машины не должны делать набор
    # недетерминированным (и тесты не должны слать пакеты живому
    # приёмнику) — каждому тесту канал выключен, тест канала задаёт
    # свои значения сам.
    monkeypatch.delenv("CREW_CHANNEL_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    # Заявки в каталог шаблонов (шаг 6, 07.09.2026) — новый носитель в
    # каталоге данных: правило изоляции 05.09 требует строки изоляции в
    # тот же коммит. Автоподача выключена (иначе существующие тесты
    # панели получали бы фоновые составления с чужими заглушками
    # моделей), очередь уведена во временную папку теста, ручка службы
    # выключена; тест механизма задаёт свои значения сам.
    monkeypatch.setenv("CDPM_CATALOG_REQUESTS_AUTO", "0")
    monkeypatch.setenv("CDPM_CATALOG_OUTBOX",
                       str(tmp_path / "cdpm-catalog-requests"))
    monkeypatch.delenv("CDPM_CATALOG_SERVICE_URL", raising=False)
    monkeypatch.delenv("CDPM_CATALOG_SERVICE_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _isolate_template_catalog_env(monkeypatch):
    """Каталог шаблонов (шаг 2, 07.09.2026): путь каталога — настройка
    продукта CREW_TEMPLATE_CATALOG, и значение из окружения машины сделало
    бы набор недетерминированным (правило изоляции носителей контура
    05.09). Каждому тесту — чистая переменная (каталог по умолчанию рядом
    с читателем); тест, которому нужен свой каталог, задаёт её сам
    (tests/catalog_copies.use_catalog). Сброс кэша двери на каждый тест
    не делается: экземпляр судится по идентичности."""
    monkeypatch.delenv("CREW_TEMPLATE_CATALOG", raising=False)


@pytest.fixture(autouse=True)
def _isolate_catalog_service_env(monkeypatch, tmp_path):
    """Служба каталога (шаг 5, 08.09.2026): её настройки — носители
    рабочего места машины, и значения из окружения сделали бы набор
    недетерминированным (правило изоляции 05.09). Рабочая папка уводится
    во временную папку теста (без неё умолчание указало бы на живой
    <репозиторий>-data и тест писал бы черновики владельцу), роль и режим
    снимаются (иначе набор судил бы права того, кто запустил pytest),
    пути привязок и перечня маркеров — тоже. Токен машинного канала
    (шаг 6, `CATALOG_MACHINE_TOKEN`) снимается по той же причине: он
    носитель рабочего места владельца, и при заданной переменной
    `Settings.from_env` в тестах молча получил бы ВКЛЮЧЁННЫЙ машинный
    канал. `CREW_DATA_DIR` НЕ снимается: при заданной CATALOG_WORK_DIR
    служба корень данных не читает вовсе, а глобальное снятие изменило
    бы поведение чужих наборов. Тест службы задаёт свои значения сам
    (tests/catalog_service/conftest.py)."""
    monkeypatch.setenv("CATALOG_WORK_DIR", str(tmp_path / "catalog-work"))
    for name in ("CATALOG_ACTOR", "CATALOG_READ_ONLY", "CREW_CATALOG_URL",
                 "CREW_COSMOS_BINDINGS", "CREW_CDPM_BINDINGS",
                 "CATALOG_MARKERS_FILE", "CATALOG_MACHINE_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_cosmos_catalog_requests_env(monkeypatch, tmp_path):
    """Заявки COSMOS в каталог шаблонов (шаг 6, 08.09.2026) — носители
    контура рабочего места: адрес и токен машинного канала службы,
    очередь заявок под каталогом данных и признак автоподачи. Значения
    из окружения машины сделали бы набор недетерминированным (правило
    изоляции 05.09) и повели бы тесты кнопки в живую службу владельца:
    автоподача выключена, очередь уведена во временную папку теста,
    машинный канал снят. Тест механизма задаёт свои значения сам."""
    monkeypatch.setenv("CREW_CATALOG_REQUESTS_AUTO", "0")
    monkeypatch.setenv("CREW_CATALOG_OUTBOX",
                       str(tmp_path / "cosmos-catalog-requests"))
    monkeypatch.delenv("CREW_CATALOG_API_URL", raising=False)
    monkeypatch.delenv("CREW_CATALOG_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _isolate_solver_url_env(monkeypatch):
    """Адрес решателя `CREW_SOLVER_URL` — носитель рабочего места
    машины: на машине разработчика в ней стоит ЖИВОЙ решатель, на
    чистом раннере переменной нет вовсе. Каждый файл, которому это
    мешало, снимал её сам (десяток мест) — то есть дыра закрывалась
    поимённо и постфактум, как когда-то изоляция хранилищ наблюдений.
    Снимаем на весь набор: тест, которому адрес нужен, задаёт его сам
    (autouse-фикстуры отрабатывают раньше собственных фикстур теста,
    поэтому его `setenv` остаётся в силе)."""
    monkeypatch.delenv("CREW_SOLVER_URL", raising=False)


#: Имя файла настроек продукта под изоляцию: путь уводится в папку
#: КАЖДОГО теста, и стенд изоляции судит по этому имени.
SETTINGS_FILE_NAME = "cdpm-settings-isolated.json"


@pytest.fixture(autouse=True)
def _isolate_cdpm_settings_file(monkeypatch, tmp_path):
    """Настройки продукта (`cdpm-settings.json` в каталоге данных) —
    носитель РАБОЧЕГО МЕСТА машины, и читают его оба продукта: ключи
    провайдеров, канал сертификатора, порог приемлемости пейринга
    (`way_margin_pct` — его берёт сравнение способов у кнопки).

    Без изоляции набор зависел от живого файла площадки, а живые службы
    машины пишут его ОДНИМ куском (`providers.save_settings` —
    `write_text`, не атомарная замена): чтение в миг записи получает
    обрезанный файл и падает разбором JSON. Так рождалась плавающая
    краснота — зелено поодиночке, красно в наборе, где окно ожидания
    длиннее (разбор 11.09.2026 по
    tests/test_run_button.py::test_compare_forwards_coverage_authorization:
    он зовёт сравнение ДВАЖДЫ, то есть читает настройки дважды).

    Каждому тесту — своя папка: файла нет, настройки пусты, поведение
    детерминированно. Тест, которому настройки нужны, задаёт файл сам
    (autouse-фикстуры отрабатывают раньше собственных фикстур теста,
    поэтому его `setenv` остаётся в силе)."""
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / SETTINGS_FILE_NAME))


@pytest.fixture(autouse=True)
def _isolate_run_record_env(monkeypatch):
    """Носители площадки записи о прогоне (проект 10.09.2026, раздел
    10): `CREW_CONTOUR` — площадка прогона словом словаря,
    `CREW_CDPM_RECORD_API` — базовый адрес ручек записи на панели CDPM.
    На площадке они заданы, на машине разработчика — как повезёт, а
    гейт «от пакета» по ним объявляет прогон и шьёт журнал к хозяину:
    прогон набора не имеет права зависеть от того, что стоит в
    окружении машины. Тест, которому носитель нужен, задаёт его сам."""
    monkeypatch.delenv("CREW_CONTOUR", raising=False)
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)


@pytest.fixture()
def declared_test_layout(tmp_path, monkeypatch):
    """Явная ТЕСТОВАЯ раскладка ролей (S06-R2, ревизия 1.18.5): без
    объявленной раскладки панель не строит облачный канал — отказ
    словами до вызова модели. Тесты панели, играющие облачные стадии,
    объявляют раскладку явно этой фикстурой (модульная autouse-обёртка
    в файле теста), а не получают молчаливое облако «по умолчанию
    канала», как до 06.09.2026: носитель площадки — временный файл со
    всеми ролями (слоты cloud и local, модели-заглушки), а сборка канала
    настроенной роли (cdpm.llm.channel) направляется в тот же шов, что
    подменяют тесты, — webui.exchange_llm_factory(scope, provider), —
    поэтому ни один запрос не уходит в настоящую модель. Тест механизма
    «раскладки нет» (test_cdpm_config_source) фикстуру не берёт."""
    import json as _json
    from cdpm import llm as _llm
    from cdpm import providers as _prov
    from cdpm import webui as _webui
    # Слот облака объявлен моделью без провайдера: провайдер тогда —
    # просьба вызывающей стороны (auditor=cloud / worker=local у ручки
    # аудит-цикла), как и на прежнем пути без раскладки, — стабы
    # тестов, различающие роли по просьбе, работают без изменений.
    # Зрение не объявляется: тесты заморозки судят поведение «зрительной
    # модели нет» (предупреждение статуса), а не вызывают её.
    layout = {"form_version": 1, "customer": "тестовая площадка"}
    for role in _prov.ROLES:
        if role == "vision":
            continue
        layout[role] = {"channels": {
            "cloud": {"model": "test-stub"},
            "local": {"provider": "local", "model": "test-local"}}}
    path = tmp_path / "agents-default-test.json"
    path.write_text(_json.dumps(layout, ensure_ascii=False),
                    encoding="utf-8")
    monkeypatch.setenv("CDPM_AGENTS_DEFAULT", str(path))

    def _channel_via_test_seam(scope="any", provider=None, model=None, **kw):
        return _webui.exchange_llm_factory(scope, provider or "cloud")
    monkeypatch.setattr(_llm, "channel", _channel_via_test_seam)
    return path


@pytest.fixture(autouse=True)
def _isolate_task_inbox(monkeypatch):
    """Урок живого e2e канала 2026-08-29: первый же обмен оставил
    НАСТОЯЩИЙ указатель current.json в каталоге данных машины, и два
    теста без изоляции увидели канальный пакет там, где ждали
    «пакета нет». Набор судит механизм, а не носитель машины:
    пустое CREW_TASK_INBOX отключает инбокс; тесты канала задают
    свой каталог сами."""
    monkeypatch.setenv("CREW_TASK_INBOX", "")


@pytest.fixture(autouse=True)
def _isolate_airline_ingest_norms(monkeypatch):
    """Зачистка 2026-08-29: нормы состава приёмника выгрузки — данные
    КОНТУРА (airline-ingest-norms.json в каталоге данных). Набор судит
    механизм, а не носитель машины разработчика: пустое значение =
    «носителя нет», приёмник отказывает словами (нормы ему обязательны,
    сна у этого механизма нет); тест механизма задаёт свой файл сам."""
    monkeypatch.setenv("AIRLINE_INGEST_NORMS", "")


@pytest.fixture
def conditions_topics_contour(tmp_path, monkeypatch):
    """Синтетический носитель тем условий: БОЕВЫЕ ключи тем (id
    вопросов — машинная связка применения ответов) и триггеры,
    минимальные тексты. Тесты механики вопросов условий запрашивают
    эту фикстуру; содержимое живого контура набор не читает."""
    import json as _json
    entries = [
        ("условия-прил4",
         r"неопределенн\w+\s+(?:состояни\w+\s+)?акклиматизаци"),
        ("условия-прил6",
         r"класс\w*\s+(?:мест\w*|экипаж\w*|кресел)|"
         r"отдых\w*\s+в\s+полете"),
        ("условия-акклиматизация", r"акклиматизаци"),
        ("условия-размах-командировки",
         r"командировк|пребыван\w+\s+вне\s+баз|"
         r"продолжительн\w+\s+ротаци"),
    ]
    p = tmp_path / "conditions-topics.json"
    p.write_text(_json.dumps({
        "form_version": 1, "customer": "синтетика", "_comment": "т",
        "topics": [{"key": k, "trigger_regex": rx,
                    "title": "Тема " + k, "text": "Уточните значение.",
                    "note": "значение"} for k, rx in entries]},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_CONDITIONS_TOPICS", str(p))
    return p


# ---------------------------------------------------------------------------
# Машинная блокировка неподписанных нативных модулей (Smart App Control)
# ---------------------------------------------------------------------------
#
# 2026-08-25: на машине разработки (PROART) Smart App Control в режиме
# принуждения заблокировал НЕПОДПИСАННЫЕ нативные модули venv — сперва
# ortools._pywraplp (LP/GLOP) в 09:01, затем z3/libz3.dll (SMT) в 09:07.
# Облачная репутация модулей сменилась на стороне Microsoft; файлы и
# конфигурация машины НЕ менялись (colgen и SMT работали ночью). CP-SAT
# (cp_model) цел. Это МАШИННАЯ ПОЛИТИКА, не код.
#
# Гейт устроен в два шага, чтобы оставаться СТРОГИМ:
#   1) ОДИН раз при старте набора пробуем импортировать каждый уязвимый
#      модуль и ловим ТОЧНУЮ сигнатуру блока «Application Control policy
#      has blocked» (у ortools она в тексте ImportError; у z3 — в stderr
#      загрузчика, само исключение чистое «libz3.dll not found»). Что
#      реально заблокировано ЭТОЙ машиной СЕЙЧАС — попадает в реестр с
#      токеном (_pywraplp / libz3) и громкой причиной.
#   2) Хук судит падение тестов ТОЛЬКО по этому реестру: отказ, чей текст
#      называет подтверждённо-заблокированный модуль, становится пропуском
#      с причиной; любой ИНОЙ отказ (в т.ч. genuinely отсутствующий модуль
#      — тогда сигнатуры в шаге 1 не будет) остаётся красным.
# Число пропущенных печатается в итоге прогона (та же дисциплина, что у
# артефактов и движка python-timefold — пропуск не бывает молчаливым).
# Снять гейт: вернуть репутацию модулям (подпись/allowlist) ЛИБО выключить
# SAC — решение владельца; тогда шаг 1 никого не найдёт, и тесты снова
# прогоняются сами, без правки этого файла.
_SAC_BLOCK_SIGNATURE = "Application Control policy has blocked"


def _detect_sac_blocked() -> dict:
    """Токен модуля -> громкая причина, для модулей, чью блокировку SAC
    подтвердил пробный ИМПОРТ/ЗАГРУЗКА ЭТОЙ сессии (сигнатура поймана
    вживую). Строго: без пойманной сигнатуры модуль в реестр не попадает
    — тогда его отказ считается настоящим и остаётся красным."""
    import ctypes
    import glob
    import os
    import sys
    blocked: dict = {}
    # ortools LP/GLOP: сигнатура — в тексте ImportError.
    try:
        from ortools.linear_solver import pywraplp  # noqa: F401
    except ImportError as e:
        if _SAC_BLOCK_SIGNATURE in str(e) and "_pywraplp" in str(e):
            blocked["_pywraplp"] = (
                "ortools._pywraplp (LP/GLOP) заблокирован политикой Smart "
                "App Control машины — машинная политика, не код (2026-08-25)")
    except Exception:                                    # noqa: BLE001
        pass
    # z3/SMT: исключение импорта чистое («libz3.dll not found»), а текст
    # сигнатуры уходит мимо sys.stderr; поэтому грузим саму библиотеку
    # напрямую через ctypes — её OSError несёт точную сигнатуру.
    z3base = os.path.join(sys.prefix, "Lib", "site-packages", "z3")
    for dll in glob.glob(os.path.join(z3base, "**", "libz3.dll"),
                         recursive=True):
        try:
            ctypes.CDLL(dll)
        except OSError as e:
            if _SAC_BLOCK_SIGNATURE in str(e):
                blocked["libz3"] = (
                    "z3/libz3.dll (SMT-проверка) заблокирован политикой "
                    "Smart App Control машины — машинная политика, не код "
                    "(2026-08-25)")
        break
    return blocked


_SAC_BLOCKED = _detect_sac_blocked()
_sac_blocked_nodes: list = []


def _sac_block_reason(exc):
    """Причина, если текст исключения называет ПОДТВЕРЖДЁННО-заблокированный
    модуль; иначе None (отказ настоящий и остаётся красным)."""
    text = str(exc)
    for token, reason in _SAC_BLOCKED.items():
        if token in text:
            return reason
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if (rep.when in ("setup", "call") and rep.failed
            and call.excinfo is not None):
        reason = _sac_block_reason(call.excinfo.value)
        if reason is not None:
            rep.outcome = "skipped"
            rep.longrepr = (str(item.path), item.location[1] or 0,
                            "Skipped: " + reason)
            if item.nodeid not in _sac_blocked_nodes:
                _sac_blocked_nodes.append(item.nodeid)
    return rep


def _report_sac_block_skips(terminalreporter):
    if not _sac_blocked_nodes:
        return
    terminalreporter.write_line(
        "пропущено %d (машинная блокировка Smart App Control, модули: %s) — "
        "машинная политика, не код; решение по разблокировке за владельцем "
        "(2026-08-25)"
        % (len(_sac_blocked_nodes), ", ".join(sorted(_SAC_BLOCKED))))


# ── Рубеж непротиворечивости при выпуске пакета постановки (СМТ-1,
# слово владельца 11.09.2026) ─────────────────────────────────────────
# Сборка пакета (cdpm/export_task_package.py) обязана опираться на
# свежий машинный вердикт о совместности жёстких правил: отчёта нет
# или он о прежнем своде — рубеж сам зовёт переводчика SMT и решателя
# Z3. Набор судит МЕХАНИЗМ, а не модель: подменяется ровно одно —
# обращение к языковой модели; Z3 в окружении настоящий, отчёт
# настоящий, отказы рубежа настоящие.

@pytest.fixture(autouse=True)
def _smt_gate_translator(monkeypatch):
    """Канал переводчика SMT для рубежа выпуска пакета — подменный
    (шов `export_task_package.smt_channel_factory`). Без него выпуск
    пакета в наборе отказывал бы словами «вердикта о
    непротиворечивости нет»: настоящая модель из набора не зовётся
    никогда. Сам переводчик — в `tests/_smt_gate_stub.py`: один
    носитель у набора и у драйверов подпроцессов (гейты детерминизма
    повтора), где фикстуры conftest не действуют."""
    try:
        from cdpm import export_task_package as _etp
    except Exception:                                     # noqa: BLE001
        yield                      # набор без CDPM — подменять нечего
        return
    import _smt_gate_stub
    monkeypatch.setattr(_etp, "smt_channel_factory",
                        _smt_gate_stub.channel_factory)
    yield
