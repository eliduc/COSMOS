"""Записи наблюдений волны 2 (2026-07-20).

Волна 2 сознательно сделана волной ЗАПИСИ, а не предложений: три из
четырёх хранилищ наблюдений на боевых контурах пусты, и контур с порогом
«пять повторов» показывал бы ноль месяцами. Здесь проверяются сами
записи — то, что теряется безвозвратно, если их не завести.

  * конфигурация прогона: раньше умолчания разрешались на лету и не
    записывались, поэтому пустой `params` означал «то, что было
    умолчанием ТОГДА» — прогоны разных месяцев были неотличимы;
  * ключ факта в строках план-факта: он был в области видимости и не
    выводился, из-за чего следующий потребитель переписывал бы правило
    трёхуровневой адресации заново.
"""
import pytest

from webapp import jobs, plan_fact, run_profile


# --- конфигурация прогона ---------------------------------------------------

_CMD = ["python.exe", "_gerad_colgen.py", "1",
        "--bases", "BASE1,BASE2,BASE3", "--pricer", "duty",
        "--max-landings", "5", "--mp", "10", "--price-cap", "600",
        "--final-tl", "900", "--dump-selected", "--dump-pool",
        "--cert-budget", "60", "--total-cap", "4500"]


def test_resolved_config_captures_defaults_that_params_never_had():
    """Умолчания видны в команде — значит, и в описании конфигурации."""
    cfg = jobs.resolved_config([_CMD])
    assert cfg["script"] == "_gerad_colgen.py"
    assert cfg["pricer"] == "duty"          # умолчание, в params его нет
    assert cfg["max_landings"] == "5"
    assert cfg["total_cap"] == "4500"


def test_flags_that_describe_the_problem_are_not_configuration():
    """`--payload`/`--name` говорят ЧТО считаем, а не КАК.

    Иначе два прогона одной конфигурации над разными наборами получили бы
    разные подписи, и сравнивать их стало бы нельзя — ровно наоборот
    задуманному."""
    a = jobs.resolved_config([_CMD + ["--payload", "/x/a.json",
                                      "--name", "a"]])
    b = jobs.resolved_config([_CMD + ["--payload", "/x/b.json",
                                      "--name", "b"]])
    assert a == b
    assert jobs.config_signature(a) == jobs.config_signature(b)


def test_signature_separates_different_configurations():
    base = jobs.config_signature(jobs.resolved_config([_CMD]))
    other = list(_CMD)
    other[other.index("duty")] = "leg"
    assert jobs.config_signature(jobs.resolved_config([other])) != base


def test_signature_is_stable_across_flag_order():
    """Подпись описывает НАБОР значений, а не порядок аргументов."""
    shuffled = (_CMD[:3] + ["--pricer", "duty", "--bases", "BASE1,BASE2,BASE3"]
                + ["--mp", "10", "--max-landings", "5", "--price-cap", "600",
                   "--final-tl", "900", "--cert-budget", "60",
                   "--total-cap", "4500", "--dump-selected", "--dump-pool"])
    assert (jobs.config_signature(jobs.resolved_config([shuffled]))
            == jobs.config_signature(jobs.resolved_config([_CMD])))


def test_trailing_flag_without_value_does_not_crash():
    """argv приходит извне — обрезанная команда не должна ронять запись."""
    assert "pricer" not in jobs.resolved_config([["py", "x.py", "--pricer"]])


def test_run_profile_row_carries_the_configuration():
    row = run_profile.record_run(
        jtype="stage1", label="gerad-1", outcome="succeeded",
        instance_id="gerad-1", config_sig="abc123",
        resolved_config={"pricer": "duty", "mp": "10"})
    assert row["instance_id"] == "gerad-1"
    assert row["config_sig"] == "abc123"
    assert row["config"] == {"pricer": "duty", "mp": "10"}


def test_old_rows_stay_valid_without_a_configuration():
    """Старые наблюдения остаются читаемыми — у них просто нет метки.

    Это важно записать явно: любое будущее сравнение обязано ПРОПУСКАТЬ
    такие строки, а не считать их одинаковыми между собой."""
    row = run_profile.record_run(jtype="stage1", label="x",
                                 outcome="succeeded")
    assert row["config_sig"] is None and row["config"] is None
    prof = run_profile.profile()
    assert prof["total_runs"] >= 1        # профиль не сломался


# --- ключ факта в план-факте ------------------------------------------------

def _payload(flights):
    return {"flights": flights, "employees": [], "flightAssignments": []}


def _flight(fn, dep, leg_id=None):
    f = {"flightNumber": fn, "departureAirport": "AAA",
         "arrivalAirport": "BBB", "departureUTCDateTime": dep,
         "arrivalUTCDateTime": dep.replace("T06", "T08")}
    if leg_id:
        f["flightLegId"] = leg_id
    return f


def test_rows_expose_the_fact_key_and_the_occurrence_address():
    payload = _payload([_flight("F1", "2026-03-01T06:00:00", "L1")])
    row = plan_fact.deviations(payload, {})[0]
    assert row["factKey"] == "F1"
    assert row["flightLegId"] == "L1"


def test_unaddressable_flight_reports_a_null_key_not_a_guess():
    """Два вылета одного номера в одну минуту адресовать нечем.

    None здесь означает «адресовать невозможно» и НЕ равно «факта нет»:
    потребитель обязан такие рейсы исключать, а не молча считать их
    выполненными по плану."""
    payload = _payload([_flight("F1", "2026-03-01T06:00:00"),
                        _flight("F1", "2026-03-01T06:00:00")])
    keys = [r["factKey"] for r in plan_fact.deviations(payload, {})]
    assert keys == [None, None]


def test_repeated_number_on_different_days_is_addressable_by_day():
    payload = _payload([_flight("F1", "2026-03-01T06:00:00"),
                        _flight("F1", "2026-03-02T06:00:00")])
    keys = sorted(r["factKey"] for r in plan_fact.deviations(payload, {}))
    assert keys == ["F1@2026-03-01", "F1@2026-03-02"]


_BUILDER_CMD = ["python.exe", "_pairing_builder.py",
                "--payload", "data/uploads/x.json", "--label", "x",
                "--level", "3", "--method", "enumerate",
                "--budget-s", "600", "--seed", "7"]


def test_builder_knobs_are_part_of_the_run_configuration():
    """Аудит 2026-07-26 (P1-16): настройки построителя цепочек не входили
    в подпись задачи, и два разных прогона первого этапа получали ОДНУ
    подпись — сравнить их было не по чему."""
    cfg = jobs.resolved_config([_BUILDER_CMD])
    assert cfg["script"] == "_pairing_builder.py"
    assert cfg["level"] == "3" and cfg["method"] == "enumerate"
    assert cfg["budget_s"] == "600" and cfg["seed"] == "7"


def test_two_different_builder_runs_get_different_signatures():
    enumerate_run = jobs.resolved_config([_BUILDER_CMD])
    columns_run = list(_BUILDER_CMD)
    columns_run[columns_run.index("enumerate")] = "columns"
    assert jobs.config_signature(enumerate_run) != \
        jobs.config_signature(jobs.resolved_config([columns_run]))
    level2 = list(_BUILDER_CMD)
    level2[level2.index("3")] = "2"
    assert jobs.config_signature(enumerate_run) != \
        jobs.config_signature(jobs.resolved_config([level2]))


def test_builder_weights_and_rescue_are_configuration_too():
    a = jobs.resolved_config([_BUILDER_CMD])
    b = jobs.resolved_config([_BUILDER_CMD + ["--w-night", "9",
                                              "--rescue-dh", "2"]])
    assert b["w_night"] == "9" and b["rescue_dh"] == "2"
    assert jobs.config_signature(a) != jobs.config_signature(b)
