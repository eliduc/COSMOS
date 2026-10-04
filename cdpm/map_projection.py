# -*- coding: utf-8 -*-
"""Проекция книги данных прогона в формат общего движка карты сети
(NetMap.draw, webapp/static/net-map.js) — пакет трёх работ владельца
2026-08-30, п.1 «карта в панели CDPM»; ревизия 05.09.2026 (слово
владельца с репетиции: «визуализация карты и персонала — отдельной
визуальной кнопкой после блока загрузки, сейчас ненаглядно») —
кнопка «Карта сети и персонал» у блока операционных данных, а
проекция несёт сводку сети и персонала: рейсы и сотрудники по
станциям, горизонт и флот, должности и допуски, разрез база × должность.

Источник — ДЕТЕРМИНИРОВАННЫЕ проекции data-freeze (никакой модели):
лист рейсов даёт станции и рёбра сети (пары вылет→прилёт со счётом),
лист экипажей — сотрудников с базами и должностями, лист допусков —
типы ВС сотрудника. Распознавание листов и колонок — словарями модуля
export_meridian (одна дисциплина синонимов на обе выгрузки, дубля
нет). Координаты — общая точка COSMOS webapp.geo (режим «карта мира»
у движка рисуется только при complete — включает человек, умолчание
всегда «схема»)."""
from __future__ import annotations

from cdpm.export_meridian import (_COLS, _SHEET_KINDS, _col, _find_sheet,
                                  _iso)

# Лист допусков и колонки, которых нет в словаре Meridian: маркеры
# подстрочно, без регистра (та же дисциплина синонимов).
_QUAL_SHEET_MARKS = ("допуск", "qualif", "квалиф", "rating")
_POSITION_MARKS = ("должност", "position", "роль", "role")
_NAME_MARKS = ("фамили", "фио", "имя", "name")
_LAT_MARKS = ("широта", "широт", "latitude", "lat")
_LON_MARKS = ("долгота", "долгот", "longitude", "lon", "lng")
_CODE_MARKS = ("код", "iata", "иата")
NO_POSITION = "без должности"

# Координаты. Книга авиакомпании — первый источник (лист аэропортов с
# колонками широты/долготы), справочник webapp.geo — второй. У
# СИНТЕТИЧЕСКОГО класса данных справочник не применяется вовсе: коды
# вымышлены и случайно совпадают с настоящими (живой случай 05.09:
# PLY/SVG/GRT набора Н0 «нашлись» как Плимут, Ставангер и Гуджрат, и
# «карта мира» рисовала чужую географию).
GEO_SOURCE_BOOK = "книга"
GEO_SOURCE_REFERENCE = "справочник"


class MapProjectionError(ValueError):
    """Проекция не построилась; недостающее названо словами."""


def _col_by_marks(sheet: dict, marks: tuple) -> str | None:
    """Колонка по маркерам — тремя проходами по убыванию строгости:
    точное совпадение заголовка, начало заголовка, подстрока (подстрока
    только для маркеров длиннее четырёх знаков — иначе «роль» ловит
    «Контроль», «код» — «Код ИКАО», «lat» — что угодно)."""
    cols = [(c, str(c).strip().lower()) for c in sheet.get("columns", [])]
    for c, low in cols:
        if any(low == m for m in marks):
            return c
    for c, low in cols:
        if any(low.startswith(m) for m in marks):
            return c
    for c, low in cols:
        if any(len(m) > 4 and m in low for m in marks):
            return c
    return None


def _cell(row: dict, col: str | None) -> str:
    return str(row.get(col) or "").strip() if col else ""


def _code(row: dict, col: str | None) -> str:
    """Код станции — в одном регистре везде (узлы, рёбра, базы,
    координаты): книга может писать «ply» и «PLY» вперемешку."""
    return _cell(row, col).upper()


def _num(text: str) -> float | None:
    try:
        return float(str(text).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def book_coords(tables: dict) -> dict[str, list[float]]:
    """Координаты станций из листа аэропортов книги (код + широта +
    долгота); нет листа или колонок — пусто."""
    apt = _find_sheet(tables, dict(_SHEET_KINDS)["airports"])
    if not apt:
        return {}
    code_c = _col_by_marks(apt, _CODE_MARKS)
    lat_c = _col_by_marks(apt, _LAT_MARKS)
    lon_c = _col_by_marks(apt, _LON_MARKS)
    if not code_c or not lat_c or not lon_c:
        return {}
    out: dict[str, list[float]] = {}
    for row in apt.get("rows", []):
        code = _code(row, code_c)
        lat = _num(_cell(row, lat_c))
        lon = _num(_cell(row, lon_c))
        if code and lat is not None and lon is not None \
                and -90 <= lat <= 90 and -180 <= lon <= 180:
            out[code] = [lat, lon]
    return out


def resolve_geo(tables: dict, codes: list[str],
                data_class: str | None = None) -> dict:
    """{coords, missing, complete, source, note}: книга первой; справочник
    по коду — ТОЛЬКО при явно открытом или проприетарном классе данных
    (настоящие коды). Синтетический класс и класс «не указан» —
    только книга: вымышленные коды случайно совпадают с настоящими, и
    непомеченный комплект нельзя считать настоящим."""
    book = book_coords(tables)
    wanted = [str(c or "").strip().upper() for c in codes]
    wanted = [c for c in wanted if c]
    coords: dict[str, list[float]] = {}
    if data_class in ("open", "proprietary"):
        from webapp import geo
        resolved = geo.resolve(wanted)
        coords.update(resolved.get("coords") or {})
        source = GEO_SOURCE_REFERENCE
        note = "справочник координат по коду; лист аэропортов книги сильнее"
    elif data_class == "synthetic":
        source = GEO_SOURCE_BOOK
        note = ("синтетический класс данных: коды станций вымышлены, "
                "справочник координат не применяется — координаты "
                "только из листа аэропортов книги (широта/долгота)")
    else:
        source = GEO_SOURCE_BOOK
        note = ("класс данных комплекта не указан: справочник координат "
                "по коду не применяется — координаты только из листа "
                "аэропортов книги (широта/долгота); задайте класс "
                "«открытый»/«проприетарный» для справочника")
    for c in wanted:
        if c in book:
            coords[c] = book[c]
    missing = [c for c in wanted if c not in coords]
    return {"coords": coords, "missing": missing,
            "complete": bool(coords) and not missing,
            "source": source, "note": note}


def build_map_projection(tables: dict,
                         data_class: str | None = None) -> dict:
    """{nodes, arcs, employees, geo, summary, by_base} для NetMap.draw
    + таблиц окна «Карта сети и персонал».

    nodes: [{code, base?, flights, employees}] — станции сети; base=True
    у баз приписки сотрудников; flights — вылетов+прилётов станции за
    горизонт; employees — сотрудников с этой базой.
    arcs: [{a, b, n, per_day?}] — ненаправленные пары станций со счётом
    рейсов (per_day — при известном числе суток горизонта).
    employees: [{id, name, base, position, quals}] в порядке (base, id).
    summary: {flights, stations, bases, employees, days, date_from,
    date_to, fleet} — горизонт по различным датам листа рейсов, флот —
    рейсов по типу ВС. by_base: {база: {должность: n}}.
    geo: {coords, missing, complete} от webapp.geo.resolve."""
    legs = _find_sheet(tables, dict(_SHEET_KINDS)["legs"])
    if not legs:
        raise MapProjectionError(
            "в книге данных не распознан лист рейсов — карте не из "
            "чего строить сеть")
    dep_c = _col(legs, "depStation")
    arr_c = _col(legs, "arrStation")
    if not dep_c or not arr_c:
        raise MapProjectionError(
            "в листе рейсов не распознаны колонки вылета/прилёта "
            f"(ищу синонимы {_COLS['depStation']} и "
            f"{_COLS['arrStation']})")
    date_c = _col(legs, "date")
    ac_c = _col(legs, "aircraft")

    codes: list[str] = []
    pair_n: dict[tuple, int] = {}
    node_flights: dict[str, int] = {}
    dates: set[str] = set()
    fleet: dict[str, int] = {}
    flights = 0
    for row in legs.get("rows", []):
        a = _code(row, dep_c)
        b = _code(row, arr_c)
        if not a or not b:
            continue
        flights += 1
        for c in (a, b):
            if c not in codes:
                codes.append(c)
            node_flights[c] = node_flights.get(c, 0) + 1
        key = (a, b) if a <= b else (b, a)
        pair_n[key] = pair_n.get(key, 0) + 1
        d = _cell(row, date_c)
        if d:
            dates.add(d)
        t = _cell(row, ac_c)
        if t:
            fleet[t] = fleet.get(t, 0) + 1

    # Допуски: табельный → типы ВС (лист не обязателен).
    quals: dict[str, list[str]] = {}
    qsheet = _find_sheet(tables, _QUAL_SHEET_MARKS)
    if qsheet:
        qid_c = _col(qsheet, "employeeId")
        qac_c = _col(qsheet, "aircraft")
        if qid_c and qac_c:
            for row in qsheet.get("rows", []):
                eid = _cell(row, qid_c)
                t = _cell(row, qac_c)
                if eid and t and t not in quals.setdefault(eid, []):
                    quals[eid].append(t)

    employees: list[dict] = []
    bases: set = set()
    emp = _find_sheet(tables, dict(_SHEET_KINDS)["employees"])
    if emp:
        id_c = _col(emp, "employeeId")
        base_c = _col(emp, "base")
        name_c = _col_by_marks(emp, _NAME_MARKS)
        pos_c = _col_by_marks(emp, _POSITION_MARKS)
        for row in emp.get("rows", []):
            base = _code(row, base_c)
            eid = _cell(row, id_c)
            employees.append({
                "id": eid,
                "name": _cell(row, name_c),
                "base": base,
                "position": _cell(row, pos_c),
                "quals": sorted(quals.get(eid, [])),
            })
            if base:
                bases.add(base)
                if base not in codes:
                    codes.append(base)
    employees.sort(key=lambda e: (e["base"], e["id"]))

    by_base: dict[str, dict[str, int]] = {}
    node_emp: dict[str, int] = {}
    for e in employees:
        if not e["base"]:
            continue
        node_emp[e["base"]] = node_emp.get(e["base"], 0) + 1
        pos = e["position"] or NO_POSITION
        row = by_base.setdefault(e["base"], {})
        row[pos] = row.get(pos, 0) + 1

    # Сутки горизонта — РАЗЛИЧНЫЕ календарные даты после нормализации
    # (книга пишет «04.09.2026», «2026-09-04» и «04.09.2026 10:00» об
    # одном дне).
    iso_dates = sorted({x[:10] for x in (_iso(d) for d in dates) if x})
    days = len(iso_dates) or None

    resolved = resolve_geo(tables, codes, data_class)

    return {
        "nodes": [{"code": c, **({"base": True} if c in bases else {}),
                   "flights": node_flights.get(c, 0),
                   "employees": node_emp.get(c, 0)}
                  for c in codes],
        "arcs": [{"a": a, "b": b, "n": n,
                  **({"per_day": round(n / days, 1)} if days else {})}
                 for (a, b), n in sorted(pair_n.items())],
        "employees": employees,
        "summary": {
            "flights": flights,
            "stations": len(codes),
            "bases": len(bases),
            "employees": len(employees),
            "days": days,
            "date_from": iso_dates[0] if iso_dates else None,
            "date_to": iso_dates[-1] if iso_dates else None,
            "fleet": dict(sorted(fleet.items())),
        },
        "by_base": {b: dict(sorted(v.items()))
                    for b, v in sorted(by_base.items())},
        "geo": resolved,
    }
