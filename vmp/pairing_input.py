# -*- coding: utf-8 -*-
"""Г-П4: транслятор «пакет постановки → вход ortools-построителя»
(директива владельца 2026-08-26: пейринг — только лучшим найденным
способом; Timefold-пейринг исключён).

Рейсы построитель ест ШТАТНЫМ payload-ом набора решателя (проверено
живьём стороной решателя — конвертации нет). Транслятор отвечает за
ПОРОГИ: ключи запуска построителя берутся ТОЛЬКО из применённых
порогов пакета (applied отчёта /schedules/coverage — авторитет);
недостающий ключ — честный отказ с перечнем, никаких молчаливых
умолчаний. Базы мира — из данных payload (не из констант).

След: карта происхождения каждого ключа возвращается вместе с
аргументами и пишется артефактом кнопки (builder-input.json)."""
from __future__ import annotations


class PairingInputError(ValueError):
    """Трансляция невозможна; причина названа (fail-closed)."""


class CriterionGapError(PairingInputError):
    """Пробел шаблона (этап 2): вид критерия известен спецификации,
    исполнителя в COSMOS нет. Несёт .gap (vmp.criteria.CriterionGap);
    маршрут — vmp.gaps: извещение пользователя, по отмашке — LLM
    пишет исполнителя через контур самоулучшения."""

    def __init__(self, gap):
        self.gap = gap
        super().__init__(
            f"пробел шаблона: вид критерия '{gap.kind}' "
            f"({gap.title}) известен спецификации, исполнителя нет — "
            f"{gap.needs}")


# Ключ CLI построителя ← имена применённого порога пакета.
# Правило выбора: список имён пробуется по порядку, берётся первое
# существующее; для min_sit действует «строже из двух» (дом/вне базы).
_THRESHOLDS = {
    "--duty-span": ("max_duty_span_min", "max_duty_duration_min",
                    "max_duty_duration"),
    "--rest-base": ("min_rest_home_min", "min_rest_home"),
    "--rest-off": ("min_rest_outstation_min", "min_rest_outstation"),
}
_SIT_KEYS = ("min_sit_home_min", "min_sit_home",
             "min_sit_outstation_min", "min_sit_outstation",
             "min_sit_min", "min_sit")
#: Ярус отдыха после смены СВЕРХ базового предела: ключ CLI ← имена
#: применённого порога, и флаг обычного яруса той же стороны, чьё
#: значение действует, когда отдельной нормы ВМП не устанавливает.
_REST_LONG = {
    "--rest-base-long": (("min_rest_home_long_min", "min_rest_home_long"),
                         "--rest-base"),
    "--rest-off-long": (("min_rest_outstation_long_min",
                         "min_rest_outstation_long"), "--rest-off"),
}


def _pick(applied: dict, names: tuple[str, ...]):
    for n in names:
        v = applied.get(n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return n, int(v)
    return None, None


def _horizon_days(payload: dict) -> int:
    """Календарные дни горизонта планирования — от первой даты вылета
    до последней даты прилёта включительно. Рейсы обязаны быть: без
    них ёмкость баз (и сам пейринг) не существует."""
    from datetime import date
    days: list[date] = []
    for f in payload.get("flights", []):
        for k in ("departureUTCDateTime", "arrivalUTCDateTime"):
            v = f.get(k)
            if isinstance(v, str) and len(v) >= 10:
                try:
                    days.append(date.fromisoformat(v[:10]))
                except ValueError:
                    pass
    if not days:
        raise PairingInputError(
            "горизонт не вычисляется: в payload нет рейсов с датами "
            "(flights.departure/arrivalUTCDateTime)")
    return (max(days) - min(days)).days + 1


# Норма выходных — правило пакета, не константа транслятора: имена
# порога из словаря (ревизия 1.6 ввела min_days_off).
_DAYS_OFF_KEYS = ("min_days_off", "min_days_off_month",
                  "min_monthly_days_off")


def _base_day_cap(payload: dict, bases: list[str],
                  applied: dict) -> tuple[str, dict]:
    """(значение --base-day-cap, след) — виток «путь к нулю»
    (отмашка владельца 2026-08-26): ёмкость базы в КАЛЕНДАРНЫХ ДНЯХ
    на роль = штат роли на базе × рабочие дни горизонта; связывающая
    ёмкость по паре ролей — минимум (цепочку исполняют обе роли).
    Построитель держит Σ дней цепочек, стартующих с базы, в ёмкости.

    Рабочие дни: дни горизонта МИНУС норма выходных, если норма
    машинно-читаема в применённых порогах пакета (ёмкость
    планирования — раздача гарантированно оставляет выходные);
    иначе полный горизонт с пометкой «безнормовая верхняя граница»
    (жёсткой цифры нормы в трансляторе не бывает)."""
    n_days = _horizon_days(payload)
    norm_name, norm = _pick(applied, _DAYS_OFF_KEYS)
    eff_days = max(0, n_days - norm) if norm_name else n_days
    by_base_role: dict[str, dict[str, int]] = {b: {} for b in bases}
    roles_all: set[str] = set()
    for e in payload.get("employees", []):
        base = e.get("homeAirport") or e.get("base")
        if base not in by_base_role:
            continue
        skills = e.get("skills")
        role = skills[0] if isinstance(skills, list) and skills \
            else "*"
        roles_all.add(role)
        by_base_role[base][role] = by_base_role[base].get(role, 0) + 1
    parts = []
    for b in sorted(bases):
        # Минимум по ВСЕМ ролям мира: база без какой-то роли даёт 0 —
        # цепочка со стартом оттуда не соберёт пару ролей на месте.
        counts = [by_base_role[b].get(r, 0) for r in sorted(roles_all)]
        parts.append(f"{b}={min(counts) * eff_days}")
    if norm_name:
        rule = (f"мин. штат по ролям базы × ({n_days} календ. дн. "
                f"горизонта − {norm} нормы выходных [{norm_name}]) "
                f"= ×{eff_days}")
    else:
        rule = (f"мин. штат по ролям базы × {n_days} календ. дн. "
                f"горизонта рейсов (безнормовая верхняя граница — "
                f"норма выходных в порогах не найдена)")
    if roles_all == {"*"}:
        rule += " (ролей в данных нет — штат базы целиком)"
    return ",".join(parts), {
        "from": "payload.employees × горизонт flights", "rule": rule}


# Замороженный словарь единого контракта построителя (31 ключ из
# живого as_dict решателя 2026-08-27 + duty_span_matrix) —
# СПРАВОЧНЫЙ перечень. Кнопочный профиль шлёт только покрытые
# пакетом ключи и БЕЗ строгого режима (вердикт владельца словаря:
# источник один — applied, след полный; null-заполнение отклонено —
# словарь не выражает «правила нет» для активных по умолчанию).
_CONTRACT_KEYS = (
    "max_dh_per_flight", "max_dh_per_day", "dh_day_scope",
    "dh_limit_counts", "monthly_flight_time_limit_min",
    "no_relay_rules", "work_time_limit_by_tier", "night_start_min",
    "night_end_min", "night_ratio", "dark_start_min", "dark_end_min",
    "dark_check_window", "dark_seq_max", "dark_definition",
    "trans_leg_min", "trans_tz_hours", "dh_board_after_debrief_min",
    "layover_tiers", "night_seq_max", "night_next_duty_start_min",
    "night_seq_layover_on_base_min", "night_seq_layover_off_base_min",
    "max_landings_per_duty", "min_sit_min", "max_pairing_days",
    "split_duty_allowed", "extended_duty_allowed",
    "transfer_deadheads", "teleportation", "base_tz_offset_min",
    "duty_span_matrix")


def calendar_days_hull(span_min: int) -> int:
    """Календарная оболочка непрерывного размаха: сколько дат может
    затронуть интервал длиной span_min минут (наихудшее положение
    относительно полуночей). 11520 мин (8 суток) → 9 дат."""
    return (int(span_min) - 1) // 1440 + 2


def source_rules_contract(forms: dict | None, applied: dict,
                          span_min: int | None = None,
                          horizon_days: int | None = None,
                          manifest_time: dict | None = None,
                          duty_cap: int | None = None) -> dict | None:
    """Г-П5 (ревизия 1.10, словарь решателя 2026-08-27, заморожен):
    контракт правил построителя из машинных форм пакета.

    duty_span_matrix — плоские пятёрки ЦЕЛЫХ [start_from_min,
    start_to_min, sectors_from, sectors_to, limit_min]; окна —
    полуинтервалы [from, to) в минутах от полуночи, пояс окон — поле
    base_tz_offset_min контракта (наше словарное base-local ложится
    1:1). trans_tz_hours — порог разницы поясов условного пола.
    Заполняются ТОЛЬКО ключи, покрытые формами/порогами пакета;
    непокрытые не пишутся (неизвестный ключ у приёмника — отказ,
    словарь закрыт). Колонки layover_tiers — после ответа решателя
    о раскладке (7 колонок, семантика его контракта)."""
    if not isinstance(forms, dict):
        if not applied:
            return None
        # Мир без машинных форм (чистый ВМП: пороги живут только в
        # посадках): контракт строится из applied-порогов. Прежний
        # ранний выход ронял построитель «таблица отдыха не задана»
        # при живых applied-порогах отдыха (находка аудита H0
        # 2026-09-01) — файл контракта и есть носитель layover-таблицы.
        forms = {}
    # Вердикт владельца словаря (2026-08-27): контракт кнопочного
    # профиля несёт ТОЛЬКО покрытые пакетом ключи (без null-заполнения
    # — словарь не выражает «правила нет» для активных по умолчанию;
    # выдумывать значения нельзя) и подаётся БЕЗ строгого режима:
    # источник один (applied), след полный — двусмысленности нет.
    out: dict = {}
    rows_flat: list[list[int]] = []
    for mat in forms.get("matrices") or []:
        if mat.get("threshold") != "max_duty_span_min":
            continue
        for r in mat.get("rows") or []:
            rows_flat.append([int(r["start_from_min"]),
                              int(r["start_to_min"]),
                              int(r["sectors_from"]),
                              int(r["sectors_to"]),
                              int(r["value_min"])])
    if rows_flat:
        out["duty_span_matrix"] = rows_flat
    tz_floors = [f for f in (forms.get("conditional_floors") or [])
                 if (f.get("condition") or {}).get("kind")
                 == "tz_diff_hours"]
    if tz_floors:
        out["trans_tz_hours"] = float(min(
            int(f["condition"]["value"]) for f in tz_floors))
    # Сшитый вердикт владельца словаря (2026-08-27): незаданный ключ
    # контракта — НЕ «правила нет», а калибровка класса (чужой а/к);
    # включённый файл применил бы чужие активные правила. Отсутствующие
    # у авиакомпании правила гасятся ЯВНЫМИ НЕЙТРАЛИЗАТОРАМИ
    # (согласованная таблица), «нет предела» — честный null только у
    # трёх ключей, где дефолт класса и есть «нет предела».
    out.setdefault("trans_tz_hours", 99.0)   # «дальней» стоянки нет
    # Реестр соответствия 2026-08-28 (группа В): метка ночи теперь
    # честная — от определения условий (night_duty_window); гасится
    # НЕ метка, а неприменимые в пейринге правила серии: правило
    # «после ночного — дневной» у авиакомпании есть, но в зоне
    # РОСТЕРИНГА (R-S5-табл-2-строка-44-кол-2) — для пейринга серия
    # нейтрализуется явно.
    out["night_ratio"] = 2.0     # запасной путь без файла условий:
    #                              контрактная метка остаётся гашеной
    out["night_seq_max"] = 999   # серия ночных — правило ростеринга
    out["night_next_duty_start_min"] = 0  # следствие той же серии
    out["dark_seq_max"] = 999    # тёмных правил нет
    out["no_relay_rules"] = []   # эстафет нет
    out["teleportation"] = []    # равнозначных станций нет
    out["max_dh_per_flight"] = None      # нет предела (= дефолт)
    out["max_dh_per_day"] = None
    out["monthly_flight_time_limit_min"] = None
    for k_app, k_c in (("max_pairing_days", "max_pairing_days"),
                       ("dh_board_after_debrief_min",
                        "dh_board_after_debrief_min")):
        v = applied.get(k_app)
        if isinstance(v, int) and not isinstance(v, bool):
            out[k_c] = v
    # Шкала времени мира (линия нано, 2026-08-29): сдвиг шкалы данных
    # от местного времени базы — параметром правила пакета через
    # applied-rules решателя. НОЛЬ — законное значение (шкала данных =
    # местное время базы), поэтому суд по типу, не по истинности.
    tz = applied.get("base_tz_offset_min")
    # Спецификация 1.16 (A9): манифест объявляет систему времени пакета
    # объектом manifest.time — он старше параметра applied (одно и то же
    # число у пакета, собранного гейтом ≥ 1.16; расхождение — дефект
    # пакета, и побеждает объявление манифеста как контракт).
    if isinstance(manifest_time, dict):
        mt = manifest_time.get("base_tz_offset_min")
        if isinstance(mt, int) and not isinstance(mt, bool):
            tz = mt
    if isinstance(tz, int) and not isinstance(tz, bool):
        out["base_tz_offset_min"] = tz
        # A9 (внешний аудит 04.09.2026): сдвиг ОБЪЯВЛЕН пакетом — у
        # построителя он побеждает пояс листа «Аэропорты» (прежде лист
        # молча перекрывал объявленный сдвиг: два смысла одного ключа).
        out["base_tz_offset_declared"] = True
    # Зазор «разбор → посадка на перегон»: у мира без норм подготовки
    # и разбора (нано: явка и послеполётные работы в свод не входят по
    # построению) правила нет — гасится явным нейтрализатором той же
    # таблицы, что ночная серия: минимум без нормы = 0, не молчаливое
    # умолчание класса. Область — ТОЛЬКО мир без файла условий
    # (его маркер — переданный horizon_days): у миров с условиями
    # (линия BA) зазор судится файлом условий, и лишний ключ менял бы
    # байты золотого контракта (гейт N30 поймал живьём). Applied-норма
    # главнее (setdefault после петли applied).
    if horizon_days:
        out.setdefault("dh_board_after_debrief_min", 0)
        # Пересадочные перегоны: умолчание False (noTransfers) — класс-
        # умолчание чужого мира. Свод мира без файла условий их НЕ
        # запрещает, а подавление способа доставки, которого ВМП не
        # ограничивал, сужало бы пространство решений сверх ВМП.
        out.setdefault("transfer_deadheads", True)
    # Живой брак dobor2 (2026-08-27 вечер): без явного
    # max_pairing_days приёмник судил чужим умолчанием класса
    # (зашитым числом чужого первоисточника). Минутный предел
    # размаха из условий даёт честную КАЛЕНДАРНУЮ ОБОЛОЧКУ (минутная
    # форма строже и судится файлом условий; оболочка — страховка,
    # не ослабление).
    if "max_pairing_days" not in out and span_min:
        out["max_pairing_days"] = calendar_days_hull(span_min)
    # Мир без нормы границы командировки И без файла условий (линия
    # нано, 2026-08-29): весь горизонт рейсов — безнормовая верхняя
    # граница той же семантики, что chain_max_days в условиях. Горизонт
    # — факт данных (цепочка физически не длиннее его), граница поиска,
    # не выдуманная норма; приоритет: applied > оболочка размаха >
    # горизонт.
    if "max_pairing_days" not in out and horizon_days:
        out["max_pairing_days"] = int(horizon_days)
    sit = [(n, int(applied[n])) for n in _SIT_KEYS
           if isinstance(applied.get(n), (int, float))
           and not isinstance(applied.get(n), bool)]
    if sit:
        out["min_sit_min"] = max(v for _n, v in sit)
    land = applied.get("max_landings_per_duty")
    if isinstance(land, int) and not isinstance(land, bool):
        out["max_landings_per_duty"] = land

    # Таблица отдыха (раскладка LayoverTier решателя, 2026-08-27):
    # [duty_upto, база, вне, база-ночная, вне-ночная, база-дальняя,
    # вне-дальняя], все минуты. Ночной/дальней дифференциации в
    # пакете нет — значение обычной колонки той же стороны («нет
    # ужесточения», не ноль); вне-дальняя — условный пол из форм.
    def _flat(name):
        v = applied.get(name)
        return v if isinstance(v, int) \
            and not isinstance(v, bool) else None
    home = _flat("min_rest_home_min") or _flat("min_rest_home")
    off = (_flat("min_rest_outstation_min")
           or _flat("min_rest_outstation"))
    if home and off:
        trans_off = max((int(f["value_min"]) for f in tz_floors),
                        default=off)
        out["layover_tiers"] = [[1440, home, off, home, off,
                                 home, trans_off]]
        # ЛОВУШКА контракта (решатель, 2026-08-27): from_dict при
        # layover_tiers БЕЗ явного work_time_limit_by_tier выводит
        # предел смены из duty_upto (=1440 → молчаливое ослабление
        # до 24 ч). Явный предел обязателен — из applied.
        span = (_flat("max_duty_span_min")
                or _flat("max_duty_duration_min")
                or _flat("max_duty_duration"))
        if span and isinstance(duty_cap, int) \
                and not isinstance(duty_cap, bool) and duty_cap > int(span):
            # Потолок продления без адреса (--duty-cap, мера 100):
            # жёсткий предел смены контракта — потолок, иначе путь
            # пакета ВМП судит смену базой и продление теряется
            # (дефект Stage 720/780, РВ-98 03.10). База остаётся
            # ключом --duty-span — порогом штрафа продления.
            span = int(duty_cap)
        if span:
            out["work_time_limit_by_tier"] = [span]
        else:
            raise PairingInputError(
                "таблица отдыха контракта без явного предела "
                "рабочего времени (max_duty_span в applied "
                "отсутствует) — контракт ослабил бы предел смены "
                "до 24 ч молчаливо")
    return out or None


def builder_args(applied: dict, payload: dict,
                 budget_s: int, *, criterion: dict | None = None,
                 base_cap_active: bool = False,
                 level: int = 3,
                 method: str = "columns",
                 seed: int = 7,
                 deterministic_time: float | None = None,
                 soft_penalties: list[dict] | None = None,
                 duty_extensions: list | None = None
                 ) -> tuple[list[str], dict]:
    """(аргументы CLI построителя, карта происхождения ключей).

    applied — применённые пороги пакета из отчёта покрытия решателя;
    payload — набор решателя (базы мира читаются из employees).
    deterministic_time — профиль Д1 плана прямого хода: предел
    детерминированного времени CP-SAT; построитель получает
    --deterministic-time и --workers 1 (вызывающая сторона не вправе
    добавлять после них другой --workers: последний ключ побеждает).
    duty_extensions — политики продления смены из того же отчёта (applied max_duty_span_min.dutyExtensions):
    потолок политики без адреса (без баз и согласия) едет построителю ключом --duty-cap (мера 100).
    Отказ PairingInputError, если недостаёт порога или баз."""
    if not isinstance(applied, dict) or not applied:
        raise PairingInputError(
            "applied-пороги пакета отсутствуют — отчёт покрытия "
            "решателя обязателен (авторитет порогов)")
    args: list[str] = []
    prov: dict[str, dict] = {}
    missing: list[str] = []

    sit_vals = [(n, int(applied[n])) for n in _SIT_KEYS
                if isinstance(applied.get(n), (int, float))
                and not isinstance(applied.get(n), bool)]
    if sit_vals:
        # «Строже из двух»: минимальная стыковка — наибольший из
        # применённых порогов (дом/вне), построитель един.
        name, val = max(sit_vals, key=lambda p: p[1])
        args += ["--sit-min", str(val)]
        prov["--sit-min"] = {"value": val, "from": name,
                            "rule": "строже из применённых (max)"}
    else:
        missing.append("min_sit (ни одного из "
                       + ", ".join(_SIT_KEYS) + ")")

    for cli, names in _THRESHOLDS.items():
        name, val = _pick(applied, names)
        if name is None:
            missing.append(f"{cli[2:]} (ни одного из "
                           + ", ".join(names) + ")")
            continue
        args += [cli, str(val)]
        prov[cli] = {"value": val, "from": name}

    _apply_duty_cap(duty_extensions, args, prov)

    # Ярус отдыха ПОСЛЕ ДЛИННОЙ СМЕНЫ (смены сверх базового предела).
    # Построитель судит отдых двумя ярусами и без полной таблицы
    # отказывается — неполная таблица опаснее пустой. ВМП называет
    # отдельную норму — подаётся она; не называет — действует ОБЩАЯ
    # норма той же стороны: правило «отдых не менее N» не обусловлено
    # длиной предыдущей смены и потому покрывает и длинную. Это чтение
    # нормы, а не умолчание из воздуха, и тот же канон уже применён к
    # таблице решателя (layover_tiers: «дифференциации в пакете нет —
    # колонка равна обычной»). Живой отказ построителя на наборе H0
    # 2026-09-03: два флага из четырёх.
    for cli, (names, base_cli) in _REST_LONG.items():
        if base_cli not in prov:
            continue                       # базового яруса нет — молчим
        name, val = _pick(applied, names)
        if name is not None:
            args += [cli, str(val)]
            prov[cli] = {"value": val, "from": name}
            continue
        val = prov[base_cli]["value"]
        args += [cli, str(val)]
        prov[cli] = {"value": val, "from": prov[base_cli]["from"],
                     "rule": "ВМП не устанавливает отдельного пола "
                             "отдыха после смены сверх базового "
                             "предела — действует общая норма той же "
                             "стороны (нет ужесточения, не ноль)"}

    # Живой payload несёт базу полем homeAirport (конвертер пакета);
    # base — форма тестовых фикстур.
    bases = sorted({e.get("homeAirport") or e.get("base")
                    for e in payload.get("employees", [])
                    if e.get("homeAirport") or e.get("base")})
    if not bases:
        raise PairingInputError(
            "базы мира не извлекаются из payload (employees пуст "
            "или без поля homeAirport/base)")
    args += ["--bases", ",".join(bases)]
    prov["--bases"] = {"value": ",".join(bases),
                       "from": "payload.employees.homeAirport"}

    # Живой урок v5 (2026-08-26): жёсткая ёмкостная связка радикально
    # утяжеляет точное покрытие — честный отказ за 1800 с при
    # выполнимом ограничении (каталог v3 — свидетель). Решение
    # сторон: ёмкость ВЫЧИСЛЯЕТСЯ и пишется в след всегда, но в
    # запуск подаётся только после тёплого старта точной фазы у
    # построителя; до того баланс баз держит сцепка линий.
    cap_val, cap_prov = _base_day_cap(payload, bases, applied)
    if base_cap_active:
        args += ["--base-day-cap", cap_val]
        prov["--base-day-cap"] = dict(cap_prov, value=cap_val)
    else:
        prov["--base-day-cap"] = dict(
            cap_prov, value=cap_val,
            note="вычислено, в запуск не подано (решение сторон "
                 "2026-08-26: ждёт тёплого старта точной фазы)")

    if missing:
        raise PairingInputError(
            "пороги пакета не покрывают вход построителя: "
            + "; ".join(missing))

    # Порядок отказов (слияние A1 05.09): пределы посадок и суток
    # транслируются ПОСЛЕ проверок баз и горизонта — отказ по
    # пустому набору называет первопричину (нет рейсов / нет баз).
    # Внешний аудит 04.09.2026, A1 (класс ошибки: умолчания площадки
    # молча подменяли нормы ВМП): предел посадок за смену и предел
    # цепочки в сутках прежде НЕ транслировались — построитель добирал
    # 5 посадок и 4 суток из настройки площадки (rules_config), и ВМП
    # «две посадки» исполнялся как «пять». Оба порога едут явными
    # ключами. Норма посадок в ВМП может отсутствовать (ручка решателя
    # «выключена», как у набора H0) — тогда предел перебора не норма, а
    # факт данных: смена физически не длиннее числа рабочих участков
    # набора (та же семантика, что горизонт данных у max_pairing_days);
    # ни настройка площадки, ни умолчание класса здесь не читаются.
    land = applied.get("max_landings_per_duty")
    if isinstance(land, int) and not isinstance(land, bool) and land > 0:
        args += ["--max-segs", str(land)]
        prov["--max-segs"] = {"value": land,
                              "from": "max_landings_per_duty"}
    else:
        n_legs = sum(1 for f in payload.get("flights", [])
                     if isinstance(f, dict))
        if n_legs <= 0:
            raise PairingInputError(
                "граница перебора посадок не вычисляется: в payload нет "
                "рейсов (flights)")
        args += ["--max-segs", str(n_legs)]
        prov["--max-segs"] = {
            "value": n_legs, "from": "payload.flights (число участков)",
            "rule": "ВМП не устанавливает предела посадок за смену "
                    "(ручка решателя выключена) — граница перебора = "
                    "число рабочих участков набора: факт данных, не "
                    "норма; настройка площадки не читается"}
    days = applied.get("max_pairing_days")
    if isinstance(days, int) and not isinstance(days, bool) and days > 0:
        args += ["--pairing-span", str(days * 1440)]
        prov["--pairing-span"] = {"value": days * 1440,
                                  "from": "max_pairing_days",
                                  "rule": "сутки ВМП × 1440 минут"}
    # Без нормы суток в applied ключ подаёт кнопка из контракта
    # source-rules (оболочка условий либо горизонт данных) — см.
    # vmp/run/button.py; здесь молчаливого умолчания нет.

    # Спецификация 1.9: критерий пейринга — данные пакета (решение
    # владельца 2026-08-26). Судья вида — словарь vmp.criteria
    # (закрытый перечень; чистота цели: при объявленном критерии
    # собственные коэффициенты способа явно обнуляются — целевая
    # функция построителя РАВНА критерию авиакомпании). Вид без
    # исполнителя — пробел шаблона: builder_args продолжить не
    # вправе (критерий объявлен, целевая ему не равна), маршрут
    # предупреждения/остановки — vmp.gaps у вызывающей стороны.
    if criterion is not None:
        from vmp import catalog_access as _ca
        from vmp import criteria as _criteria
        try:
            gap = _criteria.apply_criterion(criterion, args, prov)
        except (_criteria.CriteriaError, *_ca.CATALOG_ERRORS) as e:
            # Словарь видов — проекция каталога шаблонов (шаг 2):
            # без каталога вход построителя отказывает словами.
            raise PairingInputError(str(e)) from None
        if gap is not None:
            raise CriterionGapError(gap)
    # Фаза эскалации сирот (поручение владельца 2026-08-28): кнопочный
    # прогон доводит каждого сироту до развязки — покрыт, невозможность
    # доказана либо бюджет фазы честно исчерпан. Бюджет — четверть
    # кнопочного, не меньше 300 с.
    esc_s = max(300, int(budget_s) // 4)
    args += ["--escalate-s", str(esc_s)]
    prov["--escalate-s"] = {
        "value": esc_s,
        "from": "фаза эскалации сирот (доработка 2026-08-28): "
                "четверть бюджета кнопки, не меньше 300 с"}
    args += ["--level", str(level), "--method", method,
             "--seed", str(seed), "--budget-s", str(int(budget_s))]
    prov["--level"] = {"value": level, "from": "постановка Г-П4"}
    prov["--method"] = {"value": method, "from": "постановка Г-П4"}
    prov["--budget-s"] = {"value": int(budget_s),
                          "from": "spentLimitSeconds кнопки"}
    # Композиция целевой (канон ВМП 2026-09-02; исправление 2026-09-03:
    # построитель минимизировал только критерий): мягкие правила части
    # «Пейринг» — слагаемые цены цепочки. Идут ПОСЛЕ критерия: его
    # исполнитель обнуляет собственные веса способа, а вес ночёвки из
    # пакета (последний ключ побеждает) возвращает его канонное значение.
    if soft_penalties is not None:
        _apply_soft_penalties(soft_penalties, args, prov,
                              base_duty_threshold=applied.get("max_duty_span_min"))
    if deterministic_time is not None:
        dt = float(deterministic_time)
        if not dt > 0:
            raise PairingInputError(
                "профиль Д1: предел детерминированного времени CP-SAT "
                "обязан быть > 0, получено %r" % (deterministic_time,))
        args += ["--deterministic-time", str(dt), "--workers", "1"]
        prov["--deterministic-time"] = {
            "value": dt,
            "from": "профиль Д1 плана прямого хода: счётный предел "
                    "CP-SAT вместо стенных часов; дедлайны фаз "
                    "построителя выключены"}
        prov["--workers"] = {"value": 1,
                             "from": "профиль Д1: один поток поиска"}
    return args, prov


# ---------------------------------------------- композиция целевой

#: Дни недели окна календаря пакета -> числа построителя (понедельник 0).
_WEEKDAY_NAMES = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6,
                  "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
                  "пятница": 4, "суббота": 5, "воскресенье": 6}


def _apply_duty_cap(duty_extensions, args: list, prov: dict) -> None:
    """Мера 100 (НАДЗОР 11:2x): потолок продления смены — жёсткий предел построителя пейринга.

    Семья duty_extension_penalty: смена длиннее базы и не длиннее потолка допустима, штраф — от базы. Цепочки
    пейринга безымянны: исполнима лишь политика без адреса (без extension_bases и extension_consent); её потолок
    едет ключом --duty-cap, база остаётся --duty-span (порог штрафа и граница «длинной» смены яруса отдыха).
    Адресная политика построителем не исполняется — предел для неё база, это сказано в провенансе, а ведомость
    исполнения (execution_manifest, ключ --duty-cap) назовёт её неисполненной. Нечитаемая политика, два
    разных потолка без адреса и потолок ниже базы — отказ словами."""
    if not duty_extensions:
        return
    if not isinstance(duty_extensions, list):
        raise PairingInputError("политики продления смены (dutyExtensions) — не список: %r"
                                % (duty_extensions,))
    free, addressed = [], []
    for pol in duty_extensions:
        cap = pol.get("cap_min") if isinstance(pol, dict) else None
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise PairingInputError("политика продления смены без целого потолка cap_min > 0: %r" % (pol,))
        (addressed if pol.get("extension_bases") or pol.get("extension_consent") else free).append(pol)
    caps = sorted({int(p["cap_min"]) for p in free})
    if len(caps) > 1:
        raise PairingInputError(
            "политики продления без адреса с разными потолками (%s: %s) — какой предел действует, решает "
            "производитель постановки" % (", ".join(str(p.get("rule")) for p in free),
                                          ", ".join(map(str, caps))))
    base = (prov.get("--duty-span") or {}).get("value")
    if caps:
        if base is not None and caps[0] < base:
            raise PairingInputError("потолок продления %d ниже базового предела смены %d (%s)"
                                    % (caps[0], base, ", ".join(str(p.get("rule")) for p in free)))
        args += ["--duty-cap", str(caps[0])]
        prov["--duty-cap"] = {
            "value": caps[0],
            "from": "applied max_duty_span_min.dutyExtensions: " + ", ".join(str(p.get("rule")) for p in free),
            "rule": "смена длиннее базы %s и не длиннее потолка допустима; штраф продления — от базы" % base}
    if addressed and "--duty-span" in prov:
        prov["--duty-span"]["note"] = (
            "адресные политики продления (%s: базы / согласие) построителем пейринга не исполняются — цепочка "
            "безымянна; предел для них — база" % ", ".join(str(p.get("rule")) for p in addressed))


def pairing_soft_penalties(pkg: dict) -> list[dict]:
    """Мягкие правила части «Пейринг» из пакета (словарь
    webapp.task_package.load): [{rule_id, family, weight, params, days,
    tz}]. Семья — из посадки (template_fit.family) либо export_solver.family;
    вес — машинное поле weight; параметры — из посадки; окно дней —
    export_solver.days/tz (семья calendar_windows). Правила, исключённые
    до получения данных, не входят. Пакет без формы/модели — пусто."""
    from vmp.registry_check import is_excluded_until_data
    out: list[dict] = []
    for r in ((pkg.get("model") or {}).get("rules") or []):
        if not isinstance(r, dict) or r.get("kind") != "SOFT":
            continue
        if r.get("zone") not in ("PAIRING", "BOTH") or is_excluded_until_data(r):
            continue
        fit = r.get("template_fit") if isinstance(r.get("template_fit"), dict) else {}
        exp = r.get("export_solver") if isinstance(r.get("export_solver"), dict) else {}
        fam = fit.get("family") or exp.get("family")
        params = {}
        for name, cell in (fit.get("params") or {}).items():
            params[str(name)] = cell.get("value") if isinstance(cell, dict) else cell
        if fam == "duty_extension_penalty":
            from vmp.chain_price import ChainPriceError, extension_alias_params
            try:
                params.update(extension_alias_params(r))
            except ChainPriceError as exc:
                raise PairingInputError(str(exc)) from exc
        out.append({"rule_id": str(r.get("id")), "family": fam,
                    "weight": r.get("weight"), "params": params,
                    "days": list(exp.get("days") or []), "tz": exp.get("tz")})
    return out


#: НОСИТЕЛЬ «семья мягкого правила части «Пейринг» → её исполнитель у
#: построителя»: ключи запуска, которыми вес и параметры семьи уезжают
#: в цену цепочки, и место исполнения словами. Ведомость исполнения
#: (`vmp.execution_manifest.FAMILY_EXECUTION`) читает ЭТОТ перечень —
#: второго списка нет: до 10.09.2026 тех же трёх семей было записано
#: три раза, и семья, пришедшая конвейером пополнения, попала бы не во
#: все.
#:
#: Перечень — опись ИСПОЛНИТЕЛЕЙ, а не свод каталога: он растёт вместе
#: с ветвями ниже. Мягкая семья части «Пейринг» из каталога без записи
#: здесь — красный сторож (tests/vmp/test_family_carriers.py), а на
#: прогоне — отказ словами: её штраф иначе выпал бы из цели молча.
SOFT_EXECUTION: dict[str, dict] = {
    "layover_penalty": {
        "cli": ("--w-night",),
        "place": "цена цепочки (отбор): коэффициент за ночёвку вне базы"},
    "calendar_windows": {
        "cli": ("--w-weekend-night", "--weekend-days"),
        "place": "цена цепочки (отбор): надбавка за ночёвку, начатую "
                 "в день окна"},
    "duty_extension_penalty": {
        "cli": ("--w-long-duty", "--long-duty-threshold"),
        "place": "цена цепочки (отбор): штраф смены длиннее порога"},
}


def _apply_soft_penalties(penalties: list[dict], args: list, prov: dict,
                          *, base_duty_threshold=None) -> None:
    """Штрафы мягких правил части в ключи построителя. Fail-closed:
    мягкое правило пейринга без исполнителя у построителя (SOFT_EXECUTION)
    — отказ (его штраф иначе выпал бы из цели молча); отсутствие правила
    семьи — вес 0 явно. Названный исполнитель, не передавший своего
    ключа, — тоже отказ: опись без ветви молчала бы так же."""
    seen: dict[str, dict] = {}
    for p in penalties or []:
        fam = p.get("family")
        w = p.get("weight")
        if fam not in SOFT_EXECUTION:
            raise PairingInputError(
                "мягкое правило части «Пейринг» %s семьи %r не имеет "
                "исполнителя у построителя — его штраф выпал бы из цели; "
                "композиция целевой неполна" % (p.get("rule_id"), fam))
        if not isinstance(w, int) or isinstance(w, bool) or w < 0:
            raise PairingInputError(
                "вес мягкого правила %s не целое ≥ 0: %r"
                % (p.get("rule_id"), w))
        if fam in seen:
            raise PairingInputError(
                "две записи семьи %s в части «Пейринг» (%s и %s): "
                "какой вес действует, решает производитель постановки"
                % (fam, seen[fam].get("rule_id"), p.get("rule_id")))
        seen[fam] = p
    lay = seen.get("layover_penalty")
    w_night = int(lay["weight"]) if lay else 0
    args += ["--w-night", str(w_night)]
    prov["--w-night"] = {
        "value": w_night,
        "from": ("мягкое правило %s (семья layover_penalty): коэффициент "
                 "за ночь вне базы" % lay["rule_id"]) if lay
        else "в части «Пейринг» нет правила семьи layover_penalty — 0"}
    cal = seen.get("calendar_windows")
    if cal:
        if str(cal.get("tz") or "base-local") != "base-local":
            raise PairingInputError(
                "окно календаря %s объявлено в поясе %r — построитель "
                "судит дни по местному времени базы (base-local)"
                % (cal["rule_id"], cal.get("tz")))
        days = []
        for d in cal.get("days") or []:
            key = str(d).strip().lower()
            if key not in _WEEKDAY_NAMES:
                raise PairingInputError(
                    "окно календаря %s: день %r не распознан"
                    % (cal["rule_id"], d))
            days.append(_WEEKDAY_NAMES[key])
        if not days:
            raise PairingInputError(
                "окно календаря %s без дней недели" % cal["rule_id"])
        names = ",".join(str(d) for d in days)
        args += ["--w-weekend-night", str(int(cal["weight"])),
                 "--weekend-days", names]
        prov["--w-weekend-night"] = {
            "value": int(cal["weight"]),
            "from": "мягкое правило %s (семья calendar_windows): надбавка "
                    "за ночёвку, начатую в день окна" % cal["rule_id"]}
        prov["--weekend-days"] = {"value": names,
                                  "from": "export_solver.days правила %s"
                                          % cal["rule_id"]}
    ext = seen.get("duty_extension_penalty")
    if ext:
        from vmp.chain_price import ChainPriceError, extension_threshold
        try:
            thr = extension_threshold(base_duty_threshold,
                                      ext.get("params") or {}, ext["rule_id"])
        except ChainPriceError as exc:
            raise PairingInputError(str(exc)) from exc
        args += ["--w-long-duty", str(int(ext["weight"])),
                 "--long-duty-threshold", str(int(thr))]
        prov["--w-long-duty"] = {
            "value": int(ext["weight"]),
            "from": "мягкое правило %s (семья duty_extension_penalty): "
                    "штраф за смену длиннее порога" % ext["rule_id"]}
        prov["--long-duty-threshold"] = {
            "value": int(thr),
            "from": "применённый базовый предел max_duty_span_min; "
                    "long_duty_threshold правила %s — только равный базе alias"
                    % ext["rule_id"]}
    # Опись исполнителей без ветви молчала бы так же, как перечень без
    # семьи: правило принято по описи — ключ обязан уехать построителю.
    for fam, p in seen.items():
        key = SOFT_EXECUTION[fam]["cli"][0]
        if key not in prov:
            raise PairingInputError(
                "мягкое правило части «Пейринг» %s семьи %r названо в описи "
                "исполнителей построителя, но ключ %s ему не передан — вес "
                "выпал бы из цели молча" % (p.get("rule_id"), fam, key))
