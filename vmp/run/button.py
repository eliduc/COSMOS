# -*- coding: utf-8 -*-
"""Г-C1 (этап Г): оркестратор кнопки «документы → расписание».

Швы Ш-0…Ш-5 согласованной постановки (handoff 2026-08-24-ETAP-G):
каждый шаг пишет журнал; отказ — словами СВОЕГО шва; вопрос анкеты —
законная остановка «ожидает ответа человека» (после ответа человек
запускает кнопку заново — новый прогон, «висящих» нет); выдача —
машинная: verdict (допустимо / недопустимо / остановка …) + сертификат
+ counted_share + сквозная трассировка (идентификатор прогона cdpm,
runId решателя, отпечаток пакета).

Зависимости внедряются вызывающей стороной (решатель — HTTP-клиент,
сертификатор, сборщик входа) — оркестрация тестируется подменами без
сети и моделей; боевые реализации собирает webui.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cdpm import model as m


class ButtonStop(Exception):
    """Честная остановка кнопки: выдача с verdict и причиной."""

    def __init__(self, verdict: str, detail: str, **extra):
        super().__init__(f"{verdict}: {detail}")
        self.verdict = verdict
        self.detail = detail
        self.extra = extra


def _journal_preview(detail: str, limit: int) -> str:
    """Mark a journal-only cut within its limit; leave the full source intact."""
    return detail if len(detail) <= limit else detail[:limit - 1] + "…"


class SolverHttp:
    """Боевой клиент решателя (ручки Г-S1, прогон по runId)."""

    def __init__(self, base_url: str, timeout_s: float = 120.0):
        import httpx
        self.base = base_url.rstrip("/")
        self._c = httpx.Client(timeout=timeout_s)

    def set_timeout(self, timeout_s: float) -> None:
        """Потолок ОДНОГО обращения — на лету, тем же пулом соединений.

        Нужен вызывающему, у которого есть СВОЙ предел времени (стенд
        каталога шаблонов под ключом `--budget-s`): начатая у самой
        границы предела строка иначе ждала бы полный потолок клиента на
        каждом обращении. Пул соединений при этом не пересобирается —
        клиент остаётся один, как и задумано.
        """
        import httpx
        self._c.timeout = httpx.Timeout(max(0.001, float(timeout_s)))

    def health(self) -> bool:
        # Живость — по РАБОЧЕЙ ручке кнопки, не по расширению
        # здоровья (у решателя нет /q/health — живая служба
        # отвечала 404, и Ш-0 не видел настоящий решатель; находка
        # прогона кнопки COSMOS 2026-08-24). Любой HTTP-ответ < 500
        # значит «служба жива»; не встало соединение — мертва.
        try:
            r = self._c.get(self.base + "/schedules/applied-rules")
            return r.status_code < 500
        except Exception:                                  # noqa: BLE001
            return False

    def start(self, payload: dict, zip_b64: str, mode: str | None,
              objective_id: str | None, limit_s: int,
              task: str = "rostering",
              pairing_run_id: str | None = None,
              unimproved_limit_s: int | None = None,
              deterministic: dict | None = None,
              moves: str | None = None) -> dict:
        params = {"spentLimitSeconds": limit_s, "task": task}
        if moves:
            # Шаг 4 плана 05.09.2026: набор ходов поиска — явным
            # параметром прогона (chain — ходы по цепочкам пейринга).
            params["moves"] = str(moves)
        if deterministic:
            # Д1 плана «прямой ход» (спецификация COSMOS 03.09):
            # остановка по СЧЁТНЫМ событиям, зерно, один поток ходов,
            # воспроизводимый режим. Стенные часы из остановки Д1 НЕ
            # исчезают (правка решателя 08.09.2026): предел
            # spentLimitSeconds уезжает туда СТРАХОВКОЙ — счётный предел
            # остаётся главным, но на мире, где шаг поиска не
            # завершается, прогон кончится именно ею
            # (`termination.kind = wall_clock_safety`,
            # `reproducible: false`), а не будет считать вечно.
            params["deterministic"] = "true"
            params["seed"] = int(deterministic.get("seed", 0))
            step = int(deterministic.get("step_limit") or 0)
            unimproved = int(deterministic.get("unimproved_step_limit") or 0)
            if step <= 0 and unimproved <= 0:
                raise ButtonStop(
                    "остановка: детерминированный режим без счётного предела",
                    "нужен хотя бы один из пределов: step_limit либо "
                    "unimproved_step_limit (стенные часы воспроизводимости "
                    "не дают: в режиме Д1 они остаются только страховкой)")
            if step > 0:
                params["stepLimit"] = step
            if unimproved > 0:
                params["unimprovedStepLimit"] = unimproved
        if pairing_run_id:
            params["pairingRunId"] = pairing_run_id
        # Слово владельца 30.08 («конкретно для демо сделай 5
        # секунд»): остановка решателя после N секунд без улучшения —
        # ЯВНЫЙ параметр, по умолчанию выключен (боевые прогоны и
        # сравнимость замеров не задеты).
        if unimproved_limit_s is not None:
            params["unimprovedSpentLimitSeconds"] = unimproved_limit_s
        if mode:
            params["mode"] = mode
        if objective_id:
            params["objective_id"] = objective_id
        r = self._c.post(self.base + "/schedules/run", params=params,
                         json={"problem": payload,
                               "packageZipBase64": zip_b64})
        if r.status_code != 200:
            raise ButtonStop("остановка: решатель отверг пакет",
                             r.text[:800])
        return r.json()

    def status(self, run_id: str) -> dict:
        r = self._c.get(f"{self.base}/schedules/run/{run_id}/status")
        r.raise_for_status()
        return r.json()

    def pairing_catalog(self, payload: dict, zip_b64: str,
                        catalog_b64: str, catalog_sha256: str) -> dict:
        # Г-П4: регистрация каталога цепочек ortools-построителя
        # прогоном пейринга (ручка решателя; fail-closed гейты —
        # на её стороне: sha байт-в-байт, белый список вердиктов,
        # разбиение без повторов).
        r = self._c.post(self.base + "/schedules/pairing-catalog",
                         json={"problem": payload,
                               "packageZipBase64": zip_b64,
                               "catalogBase64": catalog_b64,
                               "catalogSha256": catalog_sha256})
        if r.status_code != 200:
            raise ButtonStop("остановка: решатель отверг каталог "
                             "пейринга", r.text[:800])
        return r.json()

    def coverage(self, zip_b64: str) -> dict | None:
        # П-1: предпрогонный отчёт покрытия БЕЗ счёта (контракт
        # решателя @ 2694c4f). Служба без ручки (404/405) — None:
        # шов дремлет, кнопка идёт прежним путём.
        r = self._c.post(self.base + "/schedules/coverage",
                         json={"packageZipBase64": zip_b64})
        if r.status_code in (404, 405):
            return None
        if r.status_code != 200:
            raise ButtonStop("остановка: отчёт покрытия не отдан",
                             r.text[:800])
        return r.json()

    def score_breakdown(self, run_id: str, problem: dict) -> dict:
        # Повкладовый счёт решения ПОД ПАКЕТОМ прогона run_id
        # (контракт Г-S1: POST /schedules/run/{id}/score-breakdown).
        r = self._c.post(
            f"{self.base}/schedules/run/{run_id}/score-breakdown",
            json=problem)
        if r.status_code != 200:
            raise ButtonStop("остановка: повкладовый счёт не отдан",
                             r.text[:800])
        return r.json()

    def result(self, run_id: str) -> dict:
        r = self._c.get(f"{self.base}/schedules/run/{run_id}/result")
        if r.status_code != 200:
            # HTTP-код обязателен в причине: транзиент нано
            # 2026-08-25 пришёл с пустым телом, и без кода отказ
            # был неразличим (запрос решателя).
            raise ButtonStop("остановка: решение не отдано",
                             f"HTTP {r.status_code}: {r.text[:800]}")
        return r.json()

    def applied(self, run_id: str) -> dict:
        r = self._c.get(
            f"{self.base}/schedules/run/{run_id}/applied-rules")
        if r.status_code != 200:
            raise ButtonStop("остановка: показания не отданы",
                             f"HTTP {r.status_code}: {r.text[:800]}")
        return r.json()


#: Ключи манифеста, которые двигает САМ ПОВТОРНЫЙ ЭКСПОРТ, а не
#: постановка. Расхождением постановки они не считаются — но и не
#: молчат: вторая половина возврата `_package_diff` называет их словами.
#:
#: `created` — метка времени экспорта; CDPM снял её по Ф-1.7, ключ
#: назван ради пакетов, собранных до Ф-1.7.
#:
#: `declaration_sha256` — отпечаток ЗАПИСИ О ПРОГОНЕ (шаг 1 проекта
#: «Запись о прогоне», 10.09.2026). Он ломает Ф-1.7 заново: объявление
#: берёт `created_at` из status.json прогона, а у папки без него
#: (стенды, синтетические прогоны, папка до объявления) — из ТЕКУЩЕЙ
#: СЕКУНДЫ, и два экспорта ОДНОЙ постановки по разные стороны границы
#: секунды дают разный отпечаток при байт-идентичных model.json и
#: списке файлов. Воспроизведено 10.09.2026: export_zip, пауза 1 с,
#: export_zip — расхождение 3 раза из 3, единственный уплывающий ключ.
#: ЛИЧНОСТЬ записи сравнивается ключом `record_id`, и он в перечень НЕ
#: входит: сменился прогон записи — это расхождение постановки.
_PACKAGE_RESTAMP_KEYS = ("created", "declaration_sha256")


def _package_diff(prev_bytes: bytes,
                  fresh_bytes: bytes) -> tuple[str, str]:
    """Содержательное расхождение двух пакетов постановки: имена
    файлов и их байты; manifest.json сравнивается БЕЗ ключей повторного
    экспорта (`_PACKAGE_RESTAMP_KEYS`).

    Возврат — пара слов. Первое: расхождение ПОСТАНОВКИ (пусто — пакеты
    одинаковы по смыслу; непусто — названы файлы и ключи манифеста).
    Второе: пометка о ключах, разошедшихся самим фактом повторного
    экспорта. Остановкой второе не бывает — пейринг на нём не устарел, —
    но и не пропадает: нажатие называет его в журнале."""
    import io
    import zipfile
    prev = zipfile.ZipFile(io.BytesIO(prev_bytes))
    fresh = zipfile.ZipFile(io.BytesIO(fresh_bytes))
    names_prev = set(prev.namelist())
    names_fresh = set(fresh.namelist())
    if names_prev != names_fresh:
        gone = sorted(names_prev - names_fresh)
        new = sorted(names_fresh - names_prev)
        return f"состав файлов (пропало {gone}, появилось {new})", ""
    diffs = []
    restamped = []
    for name in sorted(names_prev):
        a, b = prev.read(name), fresh.read(name)
        if name == "manifest.json":
            ma = json.loads(a.decode("utf-8"))
            mb = json.loads(b.decode("utf-8"))
            for key in _PACKAGE_RESTAMP_KEYS:
                if ma.pop(key, None) != mb.pop(key, None):
                    restamped.append(key)
            if ma != mb:
                keys = sorted(k for k in set(ma) | set(mb)
                              if ma.get(k) != mb.get(k))
                diffs.append("manifest.json (ключи: %s)"
                             % ", ".join(keys[:5]))
        elif a != b:
            diffs.append(name)
    note = ""
    if restamped:
        note = ("постановка та же, ключи манифеста пересобраны "
                "повторным экспортом: " + ", ".join(restamped))
    return ", ".join(diffs), note


def _rule_ids(items) -> list[str]:
    """Ссылки на правила из корзин отчёта покрытия: строки либо
    объекты с rule / rule_id."""
    out = []
    for x in items or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            rid = x.get("rule") or x.get("rule_id")
            if rid:
                out.append(str(rid))
    return out


def subprocess_pairing_builder(label: str, deterministic: bool = False,
                               select_engine: str | None = None):
    """Боевой построитель Г-П4: ortools `_pairing_builder.py`
    подпроцессом (тот же интерпретатор, корень репозитория). Вход —
    payload файлом, ключи — из транслятора; выход — байты каталога.
    Отказ построителя — ButtonStop его словами.

    deterministic (профиль Д1): среда подпроцесса получает
    PYTHONHASHSEED=0 — порядок обхода множеств перестаёт зависеть от
    запуска; сам счётный предел и один поток приходят ключами
    транслятора (vmp.pairing_input.builder_args).

    select_engine — движок точной фазы отбора (cpsat | cplex | highs;
    контракт независимости от решателя, 04.09.2026): чистый проброс
    ключа --select-engine построителю, ничего больше не меняется."""
    import subprocess
    import sys as _sys
    root = Path(__file__).resolve().parents[2]

    def build(payload: dict, args: list[str], out: Path) -> bytes:
        ppath = out / "builder-payload.json"
        ppath.write_text(json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")
        cpath = out / "builder-catalog.json"
        cmd = [_sys.executable, str(root / "_pairing_builder.py"),
               "--payload", str(ppath), "--label", label,
               "--out", str(cpath)] + list(args)
        if select_engine:
            cmd += ["--select-engine", str(select_engine)]
        env = None
        if deterministic:
            env = dict(os.environ, PYTHONHASHSEED="0")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              cwd=str(root), env=env)
        # Коды построителя — семантика ПОЛНОТЫ (0 полное, 4 честно
        # непокрываемое, 2/3 каталог отложен .pending), не сбой
        # процесса: опубликованный каталог принимается, вердикт судит
        # белый список кнопки; сбой = каталога нет.
        if not cpath.is_file():
            tail = ((proc.stderr or "") + "\n"
                    + (proc.stdout or ""))[-800:]
            raise ButtonStop("остановка: построитель пейринга "
                             "отказал", tail)
        return cpath.read_bytes()

    return build


def catalog_gap_rows(gaps, model, run_name: str, out: Path, task: str, *,
                     criterion: dict | None = None, submit: bool = True,
                     step: str = "Ш-1п", log=None, records=None,
                     records_note: str = "") -> list:
    """Что кнопка говорит о пробелах каталога шаблонов и что подаёт.

    Классификатор (`vmp.catalog_requests`) отвечает на два вопроса по
    каждому пробелу: это пробел ОТНОСИТЕЛЬНО записи каталога (тогда
    COSMOS сам подаёт заявку на вид исполнителя — в ней нет ни слова
    авиакомпании) или пробел БЕЗ записи (тогда заявку составляет CDPM
    либо человек, а кнопка называет номер и состояние). Возврат — строки
    для артефакта, выдачи и остановки; при `submit` заявки на вид
    подаются в очередь продукта и доставляются в службу.

    `records` — пробелы, названные не правилом, а ЗАПИСЬЮ каталога
    («<раздел>/<id>»): так называет предмет отказ рубежа выпуска пакета
    (`support-missing`). Они классифицируются тем же экземпляром
    каталога и той же таблицей клеток, что и пробелы по правилам
    (правило шага 2: операция читает каталог один раз), и подаются той
    же очередью.

    Отказ каталога, очереди или службы гасится здесь строкой журнала:
    пробел покрытия и так останавливает нажатие, и вторая беда не имеет
    права спрятать первую."""
    from vmp import catalog_access as vca
    from vmp import catalog_requests as vcr
    try:
        cat = vca.current()
        states = vca.bindings_states(cat)
        rows = vcr.classify_gaps(gaps, model=model, cat=cat, states=states,
                                 run_name=run_name, task=task,
                                 criterion=criterion)
        if records:
            rows = rows + vcr.classify_records(records, cat=cat, states=states,
                                               note=records_note)
        art = vcr.artifact_rows(rows)
        if submit:
            receipt = vcr.submit_kind_requests(rows, run_name=run_name,
                                               out_dir=out)
            for row in art:
                item = (receipt.get("rows") or {}).get(row["rule_id"]) or {}
                row["words"] = item.get("words") or row["words"]
                row["state"] = item.get("state", row.get("state"))
                row["delivered"] = item.get("delivered")
        return art
    except Exception as e:                                   # noqa: BLE001
        if log:
            log(step, "заявки каталога пропущены",
                "заявки каталога шаблонов не собрались: %s: %s"
                % (type(e).__name__, str(e)[:200]))
        return []


def gaps_with_requests(gaps, rows) -> list:
    """Пробелы остановки с полем `request` у каждого: номер и состояние
    заявки каталога либо `null`, когда заявки быть не может.

    ОДНА функция на оба места, где кнопка останавливается пробелом:
    ранний отказ рубежа поддержки (Ш-1) и глубокая сверка (Ш-1п2).
    Прежде закон был написан дословно дважды, и половины уже разошлись:
    мутация закона на позднем месте не красила ни одной пробы, потому
    что проба того пути судила только ветку `null` (находка С-1
    неавторского ревью 13.09.2026). Строки самой сверки не трогаются —
    обогащается КОПИЯ пробела, уезжающая в остановку."""
    from vmp import catalog_requests as _vcr
    by_rule = _vcr.requests_of(rows)
    return [dict(g, request=by_rule.get(str(g.get("rule_id"))))
            for g in gaps or ()]


#: Код отказа рубежа выпуска пакета, по которому COSMOS подаёт заявку на
#: вид исполнителя (контракты П0 §12.11, K06): у ТОЧНОЙ записи каталога
#: нет подтверждённой поддержки — объявленный ею вид не исполняет ни
#: один продукт. Записи едут в `items` отказа («families/<id>»).
SUPPORT_MISSING = "support-missing"
#: Вид пробела, которым кнопка называет правило свода, посаженное на
#: запись без поддержки: «механизма нет» (словарь RULE_GAPS
#: классификатора заявок `vmp.catalog_requests`).
SUPPORT_GAP_KIND = "no_mechanic"


def _rules_by_record(doc: dict, addresses) -> dict:
    """{адрес записи: [идентификаторы правил свода]} — какие правила
    посажены на записи, названные отказом рубежа.

    Семья правила считается ТОЙ ЖЕ функцией, что у классификатора
    заявок (`catalog_requests.family_of`: посадка A/B первична, запас —
    `export_solver.family` и словарь параметров): двух законов о семье
    одного правила не бывает."""
    from vmp import catalog_access as _ca
    from vmp import catalog_requests as _vcr
    want = {str(a) for a in addresses or ()}
    out: dict = {}
    cat = _ca.current()
    for rule in (doc or {}).get("rules") or ():
        if not isinstance(rule, dict):
            continue
        fam = _vcr.family_of(rule, cat)
        address = "families/%s" % fam if fam else ""
        if address in want:
            out.setdefault(address, []).append(str(rule.get("id")))
    return out


def _support_note(address: str | None) -> str:
    """Слова пробела ОБ ЭТОЙ записи — короткие и свои у каждой.

    Прежде сюда клали общие слова отказа рубежа, обрезанные на 300
    знаках. Две беды сразу (находка С-3 неавторского ревью 13.09.2026):
    слова отказа при четырёх семьях склеены в одну строку, поэтому у
    второй, третьей и четвёртой записи в машинном поле `note` ехал
    обрывок про ПЕРВУЮ; и обрезка резала строку посреди перечня, унося
    последний вид, режим прогона и весь совет. Полные слова отказа
    никуда не делись — они в `detail` остановки, целиком и без
    обрезки."""
    где = ("у записи %s" % address) if address else "у записи каталога"
    return ("выпуск пакета остановлен: %s нет подтверждённой поддержки — "
            "объявленный ею вид исполнителя не исполняет ни один продукт; "
            "полные слова отказа — в словах остановки" % где)


def package_refusal_stop(error, doc: dict, run_dir: Path, out: Path,
                         task: str, *, log=None) -> ButtonStop:
    """Остановка нажатия по отказу рубежа выпуска пакета производителя.

    Отказ несёт не только слова: у него есть машинный код (`.code`) и
    перечень предметов (`.items`) — `str(e)` их теряет, и пробел тонул
    бы в чужом отказе. Код `support-missing` (контракты П0 §12.11, K06)
    называет ТОЧНЫЕ записи каталога, у которых нет подтверждённой
    поддержки; по ним COSMOS подаёт заявку на вид исполнителя ТЕМ ЖЕ
    путём, каким её подаёт шаг покрытия, и называет номера в словах
    остановки. Прочие коды — «остановка производителя» его словами:
    противоречие свода, расхождение носителей параметра и неполноту
    семьи лечат не решением владельца о записи каталога.

    Fail-closed в каждой развилке: перечня записей нет — заявка не
    строится и это сказано словами; запись названа, а правил свода на
    ней нет — она названа отдельной строкой заявки, а не пропадает
    молча; классификатор не собрался — остановка всё равно случается
    (вторая беда не прячет первую)."""
    code = str(getattr(error, "code", "") or "")
    items = getattr(error, "items", None)
    words = str(error)
    if code != SUPPORT_MISSING:
        return ButtonStop("остановка производителя", words)
    if not isinstance(items, (list, tuple)) or not items:
        if log:
            log("Ш-1", "остановка",
                "рубеж не назвал семьи: заявка на вид не строится")
        return ButtonStop(
            "остановка производителя",
            "рубеж выпуска пакета отказал кодом %s, а семьи не назвал "
            "(перечень items пуст либо не перечень): заявка на вид "
            "исполнителя не строится — %s" % (SUPPORT_MISSING, words))
    # Повтор адреса в перечне — не повод считать запись дважды: строка
    # заявки у неё одна, и в словах она названа один раз.
    addresses = list(dict.fromkeys(str(x) for x in items))
    try:
        by_record = _rules_by_record(doc, addresses)
    except Exception as e:                                   # noqa: BLE001
        by_record = {}
        if log:
            log("Ш-1", "семьи правил свода не сопоставлены",
                "%s: %s" % (type(e).__name__, str(e)[:200]))
    gaps = [{"kind": SUPPORT_GAP_KIND, "rule_id": rid,
             "note": _support_note(address)}
            for address in addresses for rid in by_record.get(address, ())]
    # Запись без правил свода заявку всё равно получает: пробел в
    # каталоге, а не в правиле, и ключом строки служит адрес записи.
    bare = [a for a in addresses if not by_record.get(a)]
    rows = catalog_gap_rows(gaps, doc, run_dir.name, out, task,
                            criterion=None, step="Ш-1", log=log,
                            records=bare, records_note=_support_note(None))
    said = "; ".join(str(r.get("words")) for r in rows if r.get("words"))
    detail = words
    if bare:
        detail += ("; правил свода на записях нет: " + ", ".join(bare)
                   + " (заявка считается по самой записи)")
    if said:
        detail += "; заявки каталога: " + said
    else:
        detail += ("; заявки каталога не собрались — подайте их кнопкой "
                   "«Заявить в каталог»")
    if log:
        log("Ш-1", "остановка",
            "поддержка записей не подтверждена: %s; правил свода %d, "
            "строк заявок %d" % (", ".join(addresses), len(gaps), len(rows)))
    return ButtonStop("остановка: ВМП не закрыт шаблонами COSMOS", detail,
                      conformance_gaps=gaps_with_requests(gaps, rows),
                      catalog_requests=rows)


def _coverage_report(solver, zip_bytes: bytes, task: str,
                     kinds: dict | None = None) -> dict | None:
    """Предпрогонный отчёт покрытия задачи нажатия по контракту
    решателя (@ 2694c4f): «критичные» для авторизации человека =
    refused + notHard корзин logic + unmappedSoftRules; вычислимость
    целевых — отдельным разделом (выбор режима кнопкой — второй гейт
    службы виден ДО нажатия). Службы без ручки — None (шов дремлет).

    kinds — вид правила по id из модели пакета: корзина notHard —
    ЗАКОННОЕ место всякого не-жёсткого правила (канон logic_gate:
    SOFT/DEF/DATA обязаны быть в notHard), пробелом она считается
    ТОЛЬКО для HARD; правило вне модели — fail-closed (пробел).
    Живой ложный стоп: SOFT продления смены с посадкой A браковался
    «вне жёсткого исполнения» (обратная задача H0, run-0005 Теста,
    2026-09-02)."""
    cov = getattr(solver, "coverage", None)
    if cov is None:
        return None
    import base64
    raw = cov(base64.b64encode(zip_bytes).decode("ascii"))
    if raw is None:
        return None
    t = (raw.get("tasks") or {}).get(task) or {}
    logic = t.get("logic") or {}
    uncovered = []
    for rid in _rule_ids(logic.get("refused")):
        uncovered.append({"rule_id": rid,
                          "reason": "logic: отказ компилятора "
                                    "(refused)"})
    for rid in _rule_ids(logic.get("notHard")):
        if kinds is not None and rid in kinds \
                and kinds.get(rid) != "HARD":
            continue        # не-жёсткое правило в notHard — норма
        uncovered.append({"rule_id": rid,
                          "reason": "logic: вне жёсткого исполнения "
                                    "(notHard)"})
    for rid in _rule_ids(t.get("unmappedSoftRules")):
        uncovered.append({"rule_id": rid,
                          "reason": "мягкое правило без "
                                    "исполнителя (unmappedSoftRules)"})
    seen = set()
    dedup = []
    for u in uncovered:
        if u["rule_id"] not in seen:
            seen.add(u["rule_id"])
            dedup.append(u)
    # Применённые пороги пакета — авторитет транслятора Г-П4 (вход
    # ortools-построителя). Живой контракт службы: список записей
    # tasks.<задача>.thresholds.applied[{threshold, value, …}] —
    # сворачивается в плоский словарь имя→значение; правила-источники
    # каждого порога (rules записи) — отдельной картой applied_rules:
    # провенанс границы «стыковка / отдых» называет правило пакета
    # (vmp.sit_boundary, 06.09.2026).
    applied = {}
    applied_rules = {}
    applied_extensions = {}
    for e in (t.get("thresholds") or {}).get("applied", []) or []:
        if isinstance(e, dict) and e.get("threshold") is not None:
            applied[str(e["threshold"])] = e.get("value")
            # Мера 100: политики продления смены (потолок duty_extension_cap) — рядом с порогом базы
            if e.get("dutyExtensions") is not None:
                applied_extensions[str(e["threshold"])] = e["dutyExtensions"]
            if isinstance(e.get("rules"), list) and e["rules"]:
                applied_rules[str(e["threshold"])] = [
                    str(r) for r in e["rules"]]
    if not applied:
        flat = t.get("applied") or raw.get("applied")
        if isinstance(flat, dict):
            applied = flat
    return {"uncovered": dedup,
            "objectives": t.get("objectives") or [],
            "applied": applied or None,
            "applied_rules": applied_rules or None,
            "applied_extensions": applied_extensions or None,
            "note": raw.get("note")}


def mode_statement(mode: str, objective: str | None) -> str:
    """Режим задачи словами для человека (журнал Ш-1к, выдача,
    состояние страницы). Без критерия оптимизации в пакете режим
    optimization не бывает (гейт allowed_modes CDPM снимает его), и
    человек обязан прочитать, ЧТО именно будет искаться. Это описание
    поиска до запуска, а не свидетельство допустимости или оптимума
    (единая версия 06.09.2026, W-20/H19: честные формулировки GPT)."""
    evidence_note = (". Допустимость подтверждается только после расчёта "
                     "и проверки; стоимость и качество поиска оцениваются "
                     "отдельно, оптимум не гарантируется")
    if mode == "optimization":
        return ("оптимизация по критерию авиакомпании %s: поиск решения, "
                "выполняющего все жёсткие ограничения, с улучшением "
                "критерия с учётом взвешенных штрафов мягких правил"
                % (objective or "пакета")) + evidence_note
    if mode == "feasibility-soft":
        return ("критерия оптимизации в пакете нет — возможен только "
                "поиск допустимого решения (feasibility-soft): требуется "
                "выполнить все жёсткие ограничения; целевая поиска "
                "соответствует взвешенной сумме штрафов мягких правил "
                "в расчётной части модели") + evidence_note
    if mode == "feasibility-hard":
        return ("критерия оптимизации и мягких правил в пакете нет — "
                "ищется любое решение, выполняющее все жёсткие "
                "ограничения (feasibility-hard)") + evidence_note
    return "режим задачи %r" % (mode,)


def _default_mode(manifest: dict, task: str,
                  coverage: dict | None = None) -> tuple[str,
                                                         str | None]:
    """(режим, целевая) для ЗАДАННОЙ задачи из манифеста пакета:
    веса и режимы — только из семейства своей задачи (граница
    постановки; контракт решателя @ 750ec18). При наличии отчёта
    покрытия невычислимая целевая честно снимает optimization с
    умолчания (второй гейт службы виден до нажатия — закрытие
    развилки (б) 2026-08-24)."""
    modes = (manifest.get("allowed_modes") or {}).get(task) or []
    # Вычислимость целевой судит ТОТ, КТО ЕЁ ИСПОЛНЯЕТ. Целевую
    # пейринга исполняет построитель (vmp.criteria), а отчёт покрытия
    # решателя честно говорит о СЕБЕ: «стоимость пейрингов на
    # объединённой модели невычислима». Кнопка принимала этот ответ
    # за вердикт по задаче пейринга, отступала на режим выполнимости
    # и запускала построитель без критерия — каталог стоил ноль, и
    # CP-SAT доказывал оптимум нулевой целевой (первый сертификат
    # H0, 03.09; вопрос владельца «почему нет доказательства
    # оптимальности пейринга»). Для пейринга вычислимость решает
    # наличие исполнителя вида критерия у построителя — его кнопка
    # проверяет ниже, перед трансляцией (стоп словами при пробеле).
    objectives_info = {str(o.get("id")): o
                       for o in (coverage or {}).get("objectives", [])} \
        if task != "pairing" else {}
    for mode in ("optimization", "feasibility-soft",
                 "feasibility-hard"):
        if mode not in modes:
            continue
        objective = None
        if mode == "optimization":
            objs = (manifest.get("objectives") or {}) \
                .get(task) or None
            objective = objs[0] if objs else None
            info = objectives_info.get(str(objective))
            if info is not None and info.get("computable") is False:
                continue        # целевая честно невычислима службой
        return mode, objective
    raise ButtonStop(
        "остановка производителя",
        f"манифест пакета не разрешает ни одного режима задачи "
        f"{task} — постановка не готова (либо все целевые "
        f"невычислимы службой)")


# Имя флага тёплого старта точной фазы построителя (отмашка
# владельца 2026-08-26 «давай сделаем»); окончательное имя — за
# построителем, менять здесь одной строкой.
WARM_CATALOG_FLAG = "--warm-catalog"


def press_registry(run_dir: Path, model: dict, task: str,
                   manifest_check: str | None = None,
                   manifest_check_against: dict | None = None) -> Path:
    """Реестр исполнителей — снимок КОДА на момент нажатия, выведенный
    под модель ЭТОГО пакета (поручение владельца 2026-09-03: панель
    выводит реестр сама, статический файл площадки CREW_VMP_REGISTRY не
    читается — иначе кнопка зависела от того, чей файл лежит на
    площадке, и его подменяли руками под каждый набор). Файл кладётся в
    каталог прогона (registry/vmp-executors-registry.json) — след
    нажатия; сторож дрейфа сверяет отпечатки исполнителей с этим же
    снимком. Отказ вывода — остановка словами, расчёт не начинается.
    manifest_check — исход сторожа каталога шаблонов (Ш-1кш): реестр
    несёт не только «под каким каталогом выведен», но и «сошёлся ли
    он с каталогом пакета»; manifest_check_against — мерка того
    исхода: каталог продукта (версия, отпечаток, источник — с шага 7-Б
    всегда `catalog`; записи прежних прогонов несут `slice`), без
    которой слово «сошлось» не проверить."""
    from vmp import registry_derive as _rd
    path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
    try:
        _rd.derive_and_write(model, Path(__file__).resolve().parent.parent.parent, path,
                             scope="кнопка: %s / %s" % (Path(run_dir).name, task),
                             manifest_check=manifest_check,
                             manifest_check_against=manifest_check_against)
    except Exception as e:                                   # noqa: BLE001
        raise ButtonStop("остановка: реестр исполнителей не выведен",
                         "%s: %s" % (type(e).__name__, str(e)[:300]))
    return path


def _inject_assumptions(report, assume: dict | None) -> None:
    """A7 (очередь 3, ступень M): заявление о непереданной истории
    сотрудника едет в сертификат — той же записью, что в выдаче."""
    if isinstance(report, dict) and isinstance(assume, dict) \
            and isinstance(report.get("certificate"), dict):
        report["certificate"]["assumptions"] = assume


def selected_pairing_fields(pairing_run_id: str, pairing_selection: dict | None,
                            result: dict | None,
                            selection_note: str | None = None) -> dict:
    """Поля выдачи ростеринга НА выбранном пейринге (единая версия
    06.09.2026, W-3): область допустимости — каталог выбранного прогона
    (`feasibility_scope`), запись выбора (`pairing_selection`); при любом
    исходе, кроме подтверждённой допустимости, — следующие шаги
    (`next_actions`: пересмотреть выбор / расширить каталог новым
    пейрингом) и явное «невозможность исходной задачи не доказана»
    (`original_problem_infeasibility_proven=False`): неудача назначения
    выбранного каталога — свойство этого каталога, не постановки."""
    out: dict = {"feasibility_scope": "selected_pairing_catalog"}
    if pairing_selection is not None:
        out["pairing_selection"] = pairing_selection
    if (result or {}).get("feasibility") != "confirmed":
        out["next_actions"] = ["reselect_pairing", "expand_catalog"]
        out["original_problem_infeasibility_proven"] = False
        if selection_note:
            out["selection_note"] = selection_note
    return out


def orchestrate(run_dir: Path, solver, *,
                task: str = "rostering",
                pairing_run_id: str | None = None,
                moves: str | None = None,
                pairing_selection: dict | None = None,
                selection_note: str | None = None,
                package_from: str | None = None,
                base_capacity: bool = False,
                warm_from: str | None = None,
                source_rules: bool = True,
                conditions: bool = True,
                inject_catalogs: list[str] | None = None,
                out_dir: str | None = None,
                accepted_uncovered: list[str] | None = None,
                coverage_fn=None,
                mode: str | None = None,
                objective_id: str | None = None,
                spent_limit_s: int = 60,
                unimproved_spent_limit_s: int | None = None,
                deterministic: dict | None = None,
                build_input=None, certify_fn=None,
                pairing_builder=None, pairing_certify_fn=None,
                load_pkg=None, to_payload=None,
                sleep=time.sleep, poll_s: float = 5.0,
                timeout_s: float = 3600.0,
                log=None,
                package_path: Path | None = None) -> dict:
    """Полный проход кнопки для одной задачи (rostering — умолчание,
    pairing — первое нажатие двухфазного потока заказчика: сначала
    пейринг, после утверждения — ростеринг). Возвращает машинную
    выдачу (Ш-4); артефакты — run_dir/button/ для ростеринга и
    run_dir/button-<задача>/ для остальных задач, чтобы нажатия не
    затирали друг друга. pairing_run_id — второе нажатие двухфазного
    потока: ростеринг НА УТВЕРЖДЁННОМ прогоне пейринга (контракт
    решателя @ 0156b31: жёсткая фиксация цепочек, предусловия судит
    решатель, отказ словами); артефакты — button-on-pairing/, чтобы
    не затирать сквозной ростеринг. package_from — имя каталога
    прежнего нажатия, чьи БАЙТЫ пакета переиспользуются (равенство
    отпечатков между сравниваемыми прогонами); out_dir — явный
    каталог артефактов. Любая остановка — тоже выдача.
    pairing_selection / selection_note — запись явно выбранной попытки
    пейринга и подсказка после неудачи на её каталоге (единая версия
    06.09.2026, W-3): едут в выдачу вместе с feasibility_scope и
    next_actions через selected_pairing_fields; при выборе прежней
    попытки package_from называет её снимок (attempts/<id>/button-pairing),
    и выдача пейринга читается оттуда же.

    source_rules / conditions — СТЕНДОВЫЕ переключатели (внешний аудит
    04.09.2026, A1): на кнопочном пути контракт правил построителя
    (--source-rules с отпечатком) и файл условий (--conditions) подаются
    ВСЕГДА; прежде флаги с умолчанием False были включателями, ни один
    боевой вызов их не включал, и построитель считал по настройке
    площадки. Выключение — только стендам, называется в следе вслух."""
    run_dir = Path(run_dir)
    if pairing_run_id and task != "rostering":
        raise ValueError("pairing_run_id применим только к задаче "
                         "rostering (ростеринг на утверждённом "
                         "пейринге)")
    if (base_capacity or warm_from) and task != "pairing":
        raise ValueError("base_capacity/warm_from "
                         "применимы только к задаче pairing")
    if out_dir:
        out = run_dir / out_dir
    elif pairing_run_id:
        out = run_dir / "button-on-pairing"
    else:
        out = run_dir / ("button" if task == "rostering"
                         else f"button-{task}")
    out.mkdir(exist_ok=True)
    journal: list[dict] = []

    def _log(step: str, status: str, detail: str = ""):
        entry = {"step": step, "status": status, "detail": detail,
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        journal.append(entry)
        (out / "log.json").write_text(
            json.dumps(journal, ensure_ascii=False, indent=1),
            encoding="utf-8")
        if log:
            log(step, status, detail)

    def _deliver(verdict: str, **fields) -> dict:
        delivery = {"verdict": verdict, **fields,
                    "journal": journal}
        if pairing_run_id:
            # W-3: область допустимости и следующие шаги — в самой
            # выдаче (любой исход, включая остановку), не дописыванием
            # файла после оркестратора.
            delivery.update(selected_pairing_fields(
                pairing_run_id, pairing_selection, fields.get("result"),
                selection_note))
        (out / "delivery.json").write_text(
            json.dumps(delivery, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return delivery

    try:
        # Ш-0: живой решатель ДО экспорта.
        if not solver.health():
            raise ButtonStop("остановка: решатель недоступен",
                             getattr(solver, "base", "<служба>"))
        _log("Ш-0", "пройден", "служба решателя отвечает")

        # Ш-1: постановка готова? Вопрос анкеты — законная остановка.
        # Два входа: прогон CDPM (свод в run_dir/extract, пакет
        # экспортируется здесь) либо ГОТОВЫЙ пакет постановки
        # (package_path — путь COSMOS, решение владельца 04.09.2026:
        # CDPM заканчивается пакетом; модель читается из его байтов).
        zip_bytes = None
        if package_path is not None:
            try:
                zip_bytes = Path(package_path).read_bytes()
                import io as _io0
                import zipfile as _zf0
                doc = json.loads(_zf0.ZipFile(_io0.BytesIO(zip_bytes))
                                 .read("model.json"))
            except Exception as e:                           # noqa: BLE001
                raise ButtonStop("остановка: пакет постановки не читается",
                                 "%s: %s" % (type(e).__name__,
                                             str(e)[:200]))
        else:
            model_path = run_dir / "extract" / "model.json"
            if not model_path.is_file():
                raise ButtonStop("остановка производителя",
                                 "свода нет — сначала извлечение")
            doc = json.loads(model_path.read_text(encoding="utf-8"))
        # Use the same document-aware active editions as the producer.
        # A question in the other task must not stop this task; unknown
        # scope still blocks both. Later package/mode gates remain mandatory.
        open_qs = [q for q in m.open_questions(doc)
                   if task in m.question_tasks(doc, q)]
        if open_qs:
            _log("Ш-1", "ожидает ответа человека",
                 f"открытых вопросов: {len(open_qs)}")
            return _deliver(
                "ожидает ответа человека",
                questions=[{"id": q.get("id"),
                            "title": q.get("title"),
                            "text": q.get("text")}
                           for q in open_qs],
                note=("ответьте на вопросы анкеты и нажмите кнопку "
                      "снова — уже заданные вопросы не дублируются"))
        if zip_bytes is None:
            from cdpm import export_task_package as tp
            try:
                zip_bytes = tp.export_zip(run_dir)
            except tp.TaskPackageError as e:
                # Отказ рубежа выпуска читается КОДОМ и перечнем
                # предметов, а не одними словами: `support-missing`
                # (контракты П0 §12.11) уходит заявкой на вид
                # исполнителя, прочие коды — остановкой производителя.
                raise package_refusal_stop(e, doc, run_dir, out, task,
                                           log=_log)
        src_dir = package_from or ("button-pairing" if pairing_run_id
                                   else None)
        if src_dir:
            # Прогон обязан ехать на ТОМ ЖЕ пакете, что прежнее
            # нажатие (равенство отпечатков: четвёртое предусловие
            # pairingRunId у решателя; честность сравнения способов;
            # свежий экспорт даёт другой отпечаток при той же
            # постановке — живой отказ пятого нажатия 2026-08-24).
            # Байты переиспользуются; перед этим свежий экспорт
            # сверяется ПО СОДЕРЖИМОМУ (манифест — без ключей
            # повторного экспорта, `_PACKAGE_RESTAMP_KEYS`):
            # изменилась постановка — прежнее нажатие устарело.
            prev_path = run_dir / src_dir / "package.zip"
            if not prev_path.is_file():
                if pairing_run_id:
                    raise ButtonStop(
                        "остановка: прогона пейринга нет",
                        "сначала нажатие задачи pairing — его пакет "
                        "и утверждённый прогон нужны второму нажатию")
                raise ButtonStop(
                    "остановка: пакета прежнего нажатия нет",
                    f"каталог {src_dir} не содержит package.zip")
            diff, restamp = _package_diff(prev_path.read_bytes(),
                                          zip_bytes)
            if diff:
                raise ButtonStop(
                    "остановка: постановка изменилась после "
                    "утверждённого пейринга",
                    "пакет разошёлся с пакетом пейринга: " + diff
                    + " — прогоните пейринг заново и утвердите его")
            if restamp:
                # Не остановка: постановка совпала по содержимому, а
                # запись о прогоне пересобралась. Молчать нельзя —
                # ключ мог сдвинуться и не от часов (сменились каталог,
                # модели, документы прогона), — но и останавливать
                # нечего: в расчёт идут ПРЕЖНИЕ байты пакета.
                _log("Ш-1з", "запись прогона пересобрана", restamp)
            zip_bytes = prev_path.read_bytes()
        pkg_path = out / "package.zip"
        pkg_path.write_bytes(zip_bytes)
        from cdpm.gates import gate_task_package as gate
        problems, _c = gate.check(pkg_path)
        if problems:
            raise ButtonStop("остановка производителя",
                             "красный гейт пакета: "
                             + "; ".join(problems[:3]))
        _log("Ш-1", "пройден", f"пакет {len(zip_bytes)} байт, гейт "
                               f"зелёный")

        # Ш-1п (П-1, постановка владельца 2026-08-24): предпрогонная
        # сверка покрытия — решатель ДО счёта называет правила, чьих
        # исполнителей у него нет; человек авторизует счёт без них
        # ПОИМЁННО (accepted_uncovered), неавторизованные считаются
        # критичными («без них оптимизация бессмысленна») и счёт не
        # запускается. Шов дремлет до ручки службы: coverage_fn не
        # передан либо вернул None — журнальная пометка, прежний путь.
        coverage_auth = None
        if coverage_fn is not None:
            report = coverage_fn(solver, zip_bytes, task)
        else:
            # Виды правил — из модели постановки (та же, что прочитана
            # на Ш-1: свод прогона либо модель из байтов пакета).
            kinds = {str(r.get("id")): r.get("kind")
                     for r in doc.get("rules") or ()
                     if isinstance(r, dict)} or None
            report = _coverage_report(solver, zip_bytes, task,
                                      kinds=kinds)
        if report is None:
            _log("Ш-1п", "пропущен",
                 "предпрогонный отчёт покрытия недоступен "
                 "(служба без П-1)")
        else:
            unc = report.get("uncovered") or []
            accepted = {str(x) for x in (accepted_uncovered or [])}
            missing = [u for u in unc
                       if str(u.get("rule_id")) not in accepted]
            # Этап 2 переноса ВМП (приоритет 2 владельца 2026-08-28):
            # сверка правил ВМП с шаблонами COSMOS — непокрытые
            # обогащаются машинной формой правила из модели пакета и
            # извещением поимённо. Со шага 6 каталога рядом ложатся
            # СТРОКИ ЗАЯВОК каталога (классификатор): пробел
            # относительно записи уходит заявкой на вид исполнителя
            # сразу (в ней нет данных авиакомпании), пробел без записи —
            # называется номером и ссылкой. Артефакт вспомогательный:
            # его сбой не останавливает авторизованный счёт, но и не
            # молчит.
            gaps_notify = None
            catalog_rows: list = []
            if unc:
                try:
                    from vmp import gaps as vgaps
                    import io as _io2
                    import zipfile as _zf2
                    _mdoc = json.loads(_zf2.ZipFile(
                        _io2.BytesIO(zip_bytes)).read("model.json"))
                    rgaps = vgaps.rule_gaps(unc, _mdoc)
                    gaps_notify = vgaps.notify_rules_text(rgaps)
                    catalog_rows = catalog_gap_rows(
                        [{"kind": "coverage", "rule_id": g.rule_id,
                          "note": g.reason} for g in rgaps],
                        _mdoc, run_dir.name, out, task,
                        step="Ш-1п", log=_log)
                    art = {
                        "notify_text": gaps_notify,
                        "gaps": [{"rule_id": g.rule_id,
                                  "reason": g.reason,
                                  "rule": g.rule} for g in rgaps],
                        "catalog": catalog_rows,
                        "accepted": sorted(
                            accepted & {str(u.get("rule_id"))
                                        for u in unc}),
                    }
                except Exception as e:            # noqa: BLE001
                    art = {"error": f"извещение этапа 2 не "
                                    f"собралось: {e}"}
                (out / "vmp-rule-gaps.json").write_text(
                    json.dumps(art, ensure_ascii=False, indent=1),
                    encoding="utf-8")
            if missing:
                _log("Ш-1п", "ожидает решения человека",
                     f"без исполнителя: {len(unc)}, "
                     f"не авторизовано: {len(missing)}")
                return _deliver(
                    "ожидает решения человека: покрытие",
                    uncovered=unc,
                    unauthorized=[u.get("rule_id") for u in missing],
                    vmp_gaps_notify=gaps_notify,
                    catalog_requests=catalog_rows,
                    note=("решатель не умеет исполнять "
                          "перечисленные правила; повторите "
                          "нажатие, поимённо перечислив "
                          "авторизованные (acceptedUncovered) — "
                          "неавторизованные считаются критичными "
                          "(пробелы каталога шаблонов: заявка на вид "
                          "исполнителя подаётся кнопкой, новая запись "
                          "— из CDPM либо вручную), "
                          "счёт без них не запускается"))
            if unc:
                coverage_auth = {
                    "accepted": sorted(str(u.get("rule_id"))
                                       for u in unc)}
                _log("Ш-1п", "авторизовано человеком",
                     f"правил без исполнителя: {len(unc)}")
            else:
                _log("Ш-1п", "пройден",
                     "все правила покрыты исполнителями")

        # Ш-2: пакет → решатель, прогон по идентификатору.
        if load_pkg is None or to_payload is None:
            from webapp import task_package as wtp
            load_pkg = load_pkg or (
                lambda path: wtp.load(path, run_gate=False))
            to_payload = to_payload or wtp.to_payload
        from webapp.task_package import TaskPackageRefused
        try:
            pkg = load_pkg(pkg_path)
        except TaskPackageRefused as exc:
            raise ButtonStop("остановка: пакет постановки не принят",
                             str(exc), code=exc.code, problems=exc.problems) from exc
        # A08: the consumer's archive boundary must reach the human journal,
        # including a later stop. Reading an old package is not projection PASS.
        projection = pkg.get("objective_projection")
        if isinstance(projection, dict) and projection.get("judged") is False:
            _log("Ш-1ц", "не судится", projection.get("words") or
                 "сверка objective.json с моделью не выполнялась; "
                 "это не разрешение исполнения")
        elif isinstance(projection, dict) and projection.get("judged") is True:
            _log("Ш-1ц", "пройден",
                 "objective.json сверён с моделью в судимой части; "
                 "это не общий допуск к исполнению")
        else:
            _log("Ш-1ц", "не установлено",
                 "читатель не сообщил результат сверки objective.json "
                 "с моделью — прохождение сверки не подтверждено")
        try:
            payload = to_payload(pkg)
        except TaskPackageRefused as exc:
            raise ButtonStop("остановка: пакет не преобразован для исполнителя",
                             str(exc), code=exc.code, problems=exc.problems) from exc
        if task == "rostering" and isinstance(payload, dict):
            # Ревизия 1.17: утверждённые назначения раздела истории —
            # закрепления набора ТОЛЬКО у ростеринга (пейринг и карта
            # набор не закрепляют — ревью CDPM 05.09); отказ — словами.
            from webapp import task_package as _wtp_pins
            try:
                _wtp_pins.apply_history_pins(pkg, payload)
            except _wtp_pins.TaskPackageRefused as e:
                raise ButtonStop(
                    "остановка: утверждённые назначения раздела истории "
                    "не закрепляются", str(e)[:1200])
        import base64
        # Гейт режимов пакета — ДО выбора исполнителя: постановка,
        # не разрешающая задачу, останавливает кнопку одинаково для
        # обоих путей (Timefold-ростеринг / ortools-пейринг).
        req_mode, req_obj = (mode, objective_id) if mode \
            else _default_mode(pkg.get("manifest") or {}, task,
                               report)
        # Ш-1к: режим задачи — словами, до расчёта (задание владельца
        # 04.09, ИДАК H0-К без критерия: «должно быть сообщение, что
        # критерия оптимизации нет и возможен только поиск допустимого
        # решения с целевой = взвешенной суммой штрафов»). Режим и
        # пояснение едут в выдачу обеих задач и в состояние страницы.
        mode_info = {"mode": req_mode, "objective": req_obj,
                     "mode_note": mode_statement(req_mode, req_obj)}
        _log("Ш-1к", "режим задачи", mode_info["mode_note"])

        # Ш-2м: матрица готовности ВМП (директива владельца
        # 2026-08-29). Матрицу формирует CDPM в манифесте (ревизия
        # 1.12), COSMOS верифицирует независимо: пересчёт клетки из
        # содержимого пакета + реестр исполнителей зоны + фактическая
        # полнота данных. Расхождение любой клетки, пробелы зоны или
        # нехватка данных = остановка ОБЕИХ задач одинаково; пакеты
        # младше 1.12 (без матрицы) идут прежними гейтами.
        _manifest2 = pkg.get("manifest") or {}
        # Ш-1кш: СТОРОЖ ДРЕЙФА КАТАЛОГА ШАБЛОНОВ (§8 схемы каталога,
        # шаг 3 переезда, 07.09.2026). Пакет объявляет ключом
        # manifest.catalog, каким каталогом посажены его правила;
        # продукт знает свой. Разница СТАРШЕГО числа версии (в любую
        # сторону; на 0.x — и среднего), несравнимая версия и одна
        # версия у двух ВЫПУСКОВ с разными данными значат, что шаблоны
        # разные: правила, сойдясь по именам, исполнялись бы не тем,
        # чем посажены, — расчёт не начинается. Ключа нет (пакет до
        # «Дополнения к 1.18»), тот же черновик с другим отпечатком,
        # patch (и minor от 1.0.0) — предупреждение: нажатие идёт,
        # расхождение названо словами в журнале, состоянии и реестре
        # исполнителей.
        from vmp import catalog_access as _ca_g
        try:
            _cat_chk = _ca_g.manifest_check(_manifest2)
        except _ca_g.CATALOG_ERRORS as e:                    # noqa: BLE001
            # Отказ самого каталога — не дело сторожа: его судят
            # словами рубеж Ш-1п2 и вывод реестра.
            _cat_chk = None
            _log("Ш-1кш", "пропущен",
                 "каталог шаблонов недоступен: %s" % _journal_preview(str(e), 200))
        if _cat_chk is not None:
            # Статус строки — по ВЕРДИКТУ читателя (check_status), а не
            # по равенству отпечатков: patch и minor от 1.0.0 отпечаток
            # менять не обязаны, а вердикт у них — предупреждение.
            _log("Ш-1кш", _ca_g.check_status(_cat_chk),
                 _cat_chk["words"] or "версия и отпечаток каталога у пакета "
                 "и у продукта совпали")
            if _cat_chk["state"] == _ca_g.CHECK_STOP:
                raise ButtonStop("остановка: каталог шаблонов пакета не тот",
                                 _cat_chk["words"],
                                 reason_code=_ca_g.STOP_REASON_CODE,
                                 template_catalog=_cat_chk)
        # Реестр исполнителей — под модель этого пакета, при каждом
        # нажатии (press_registry); площадочный файл не читается.
        # Модель — из БАЙТОВ пакета (те же, что едут в расчёт), как у
        # рубежа соответствия ниже: загрузчик пакета может отдать
        # манифест без модели, реестр же строится по модели.
        try:
            import io as _io2
            import zipfile as _zf2
            _model2 = json.loads(_zf2.ZipFile(_io2.BytesIO(zip_bytes))
                                 .read("model.json"))
        except Exception as e:                               # noqa: BLE001
            raise ButtonStop("остановка: реестр исполнителей не выведен",
                             "модель пакета не прочитана: %s" % str(e)[:200])
        # Исход сторожа едет и в реестр этого нажатия (блок catalog):
        # реестр говорит не только «под каким каталогом выведен», но и
        # «сошёлся ли с ним пакет». Вывод реестра — шов (стенды его
        # подменяют); шов прежней формы реестр всё равно пишет, просто
        # без поля сверки — сторож от этого не слепнет, его слова уже
        # в журнале и в состоянии.
        import inspect as _insp
        _pr_kw = {}
        _pr_pars = _insp.signature(press_registry).parameters
        if "manifest_check" in _pr_pars:
            _pr_kw["manifest_check"] = (_cat_chk or {}).get("state")
        if "manifest_check_against" in _pr_pars:
            # Мерка исхода — каталог продукта (источник `catalog`: с
            # шага 7-Б второго источника у продукта нет) — рядом с
            # исходом: без неё слово «сошлось» не проверить.
            _pr_kw["manifest_check_against"] = (_cat_chk or {}).get("product")
        reg_path = press_registry(run_dir, _model2, task, **_pr_kw)
        # K06 support/1: the package snapshot is not an execution permit.
        # Judge actual package model bytes, selected task/mode and live
        # bindings before either builder or solver receives the package.
        from webapp.task_package import check_support_execution, TaskPackageRefused
        try:
            check_support_execution({"model": _model2, "manifest": _manifest2},
                                    task=task, mode=req_mode)
        except TaskPackageRefused as exc:
            raise ButtonStop("остановка: поддержка исполнителя не подтверждена",
                             str(exc), code=exc.code, support_problems=exc.problems) from exc
        _log("Ш-1р", "выведен", "реестр исполнителей под модель пакета: %s"
             % reg_path.name)
        if isinstance(_manifest2.get("readiness_matrix"), dict):
            from vmp import readiness_verify as _rv
            # Аудит A1 (04.09.2026): четвёртая сторона клетки — пороги
            # применённых правил решателя (applied П-1): исполнитель
            # покрывает правило только при переданном пороге.
            _ver = _rv.verify(
                _manifest2, pkg.get("model") or {},
                pkg.get("clean_data"),
                Path(__file__).resolve().parent.parent.parent,
                reg_path,
                form=pkg.get("form"),
                applied=((report or {}).get("applied")
                         if isinstance(report, dict) else None))
            _cell = (_ver.get("columns") or {}).get(task) or {}
            if _ver.get("verdict") != "OK" \
                    or not _cell.get("launch_allowed") \
                    or not _rv.state_allows(
                        _cell.get("declared_state"), req_mode) \
                    or (req_mode == "optimization"
                        and _cell.get("criteria_tiers")):
                _why = "; ".join(_cell.get("why_blocked") or ()) \
                    or _ver.get("verdict") or "матрица не прошла"
                if req_mode == "optimization" \
                        and _cell.get("criteria_tiers"):
                    _why += ("; ярусы критериев части: "
                             + "; ".join(_cell["criteria_tiers"]))
                if not _rv.state_allows(
                        _cell.get("declared_state"), req_mode):
                    _why += ("; состояние клетки %r не допускает "
                             "режим %r" % (_cell.get(
                                 "declared_state"), req_mode))
                _log("Ш-2м", "остановка",
                     "матрица готовности: задача %s — %s"
                     % (task, _why))
                # Пробелы зоны реестра — те же пробелы каталога: заявка
                # на вид подаётся здесь, а не откладывается до Ш-1п2
                # (до него нажатие уже не дойдёт).
                _cat_rows = catalog_gap_rows(
                    (_cell.get("registry") or {}).get("gap_rows") or [],
                    _model2, run_dir.name, out, task,
                    step="Ш-2м", log=_log)
                raise ButtonStop(
                    "остановка: матрица готовности ВМП запрещает "
                    "запуск задачи", _why, catalog_requests=_cat_rows)
            _log("Ш-2м", "пройден",
                 "матрица готовности: %s = %s (клетка сверена "
                 "пересчётом; реестр зоны: правил %s, пробелов %s; "
                 "данные фактом полны)"
                 % (task, _cell.get("declared_state"),
                    (_cell.get("registry") or {}).get(
                        "rules_in_zone"),
                    (_cell.get("registry") or {}).get("gaps")))

        # A2.5 (внешний аудит 04.09.2026; слово владельца 04.09 «делать
        # A2.5», ключи пакета — спецификация 1.15, CDPM): замок
        # измерителя постановки и проверки постановки НА КНОПКЕ,
        # fail-closed. Стоп, если измеритель не отработал
        # (measure.available=false) или не согласен (measure.ok=false),
        # если покрытие содержания несёт значения без адресата или
        # нормативные клаузы без строк, или если проверка постановки
        # COSMOS (vmp/formulation_check по форме и модели пакета) даёт
        # уровень stop. form.problems — показание, не замок (строгая
        # форма считает нарушением любой открытый вопрос, предварительный
        # ВМП законно едет с ними). Пакеты без measure/coverage (младше
        # 1.14) судятся прежним законом: нет ключей — не стоп.
        _lock_why: list[str] = []
        _meas = _manifest2.get("measure")
        if isinstance(_meas, dict):
            if _meas.get("available") is False:
                _lock_why.append("измеритель постановки не отработал: %s"
                                 % (_meas.get("reason") or "причина не названа"))
            elif _meas.get("ok") is False:
                _probs = [str(x) for x in (_meas.get("problems") or ())]
                _lock_why.append("измеритель постановки не согласен: "
                                 + ("; ".join(_probs)[:400] or "ok=false"))
        _cov = _manifest2.get("coverage")
        if isinstance(_cov, dict):
            for _key, _words in (("values_without_addressee",
                                  "значений без адресата"),
                                 ("normative_without_rows",
                                  "нормативных клауз без строк")):
                _n = _cov.get(_key)
                if isinstance(_n, int) and not isinstance(_n, bool) and _n > 0:
                    _lock_why.append("покрытие содержания: %s %d" % (_words, _n))
        _fc_note = "форма ВМП в пакете отсутствует — проверка постановки не проводилась"
        if isinstance(pkg.get("form"), dict):
            from vmp import formulation_check as _fchk
            try:
                _fc = _fchk.check({"form": pkg.get("form"), "model": _model2,
                                   "clean_data": pkg.get("clean_data"),
                                   "manifest": _manifest2})
            except Exception as e:                           # noqa: BLE001
                _lock_why.append("проверка постановки не отработала: %s"
                                 % str(e)[:200])
                _fc = None
            if _fc is not None:
                _stops = [f for f in _fc.get("findings") or ()
                          if f.get("level") == _fchk.STOP]
                _fc_note = ("проверка постановки: остановок %d, "
                            "предупреждений %d"
                            % (len(_stops), _fc.get("warnings") or 0))
                if _stops:
                    _lock_why.append(
                        "проверка постановки: %d остановок — %s"
                        % (len(_stops), "; ".join(
                            "%s %s: %s" % (f.get("class"), f.get("id") or "",
                                           f.get("message"))
                            for f in _stops[:3])[:500]))
        if _lock_why:
            _log("Ш-2и", "остановка",
                 "замок измерителя и проверки постановки: "
                 + "; ".join(_lock_why))
            raise ButtonStop(
                "остановка: измеритель или проверка постановки "
                "запрещают запуск", "; ".join(_lock_why))
        _log("Ш-2и", "пройден",
             "измеритель: %s; покрытие содержания: %s; %s"
             % ("ok" if isinstance(_meas, dict) else "ключа нет (пакет до 1.14)",
                "без пробелов" if isinstance(_cov, dict) else "ключа нет",
                _fc_note))

        # A7 (внешний аудит 04.09.2026, очередь 3, ступень M): история
        # сотрудника (остатки на начало периода) пакетом не передаётся —
        # правила накопителей, полов отдыха, серий и выравнивания судятся
        # только внутри горизонта. Допущение «остаток = 0» называется
        # словами и поимённо в выдаче и сертификате, а не подразумевается.
        from vmp import history_assumption as _hist
        _assume = _hist.statement(pkg.get("model") or _model2, _manifest2,
                                  task)
        _log("Ш-2с", "история передана" if _assume["history"]["passed"]
             else "допущение", _journal_preview(_assume["history"]["note"], 300))
        # Закрепления из утверждённых назначений раздела истории — в
        # журнал и в заявление (набор решателя кнопка не сохраняет, а
        # решатель чужие ключи не возвращает — счётчик обязан быть виден
        # в выдаче; замечание CDPM на проходе КИ-полный, 05.09.2026).
        _pins = ((payload.get("history") or {}).get("approved_assignments_pinned")
                 if task == "rostering" and isinstance(payload, dict)
                 and isinstance(payload.get("history"), dict)
                 else None)
        if _pins:
            _assume["history"]["approved_pinned"] = [
                {k: p.get(k) for k in ("employee", "flight", "seat", "role")}
                for p in _pins]
            _log("Ш-2с", "закрепления истории",
                 "утверждённых назначений закреплено: %d — %s"
                 % (len(_pins), "; ".join("%s → %s (%s)" % (
                     p.get("employee"), p.get("flight"), p.get("role"))
                     for p in _pins[:10])))
        # Ревизия 1.17: раздел history передан, а у части сотрудников
        # записи нет и производитель не разрешил нулевую подстановку
        # (assume_zero_when_missing=false) — при правилах, зависящих от
        # истории, остановка словами: неизвестное не превращается в
        # отсутствие ограничения.
        if _assume["history"]["passed"] and _assume["history"]["rules"]:
            _no_hist = _hist.missing_history(_manifest2, pkg.get("clean_data"))
            if _no_hist:
                # Ревизия 1.17.2 (решение владельца 05.09.2026): нулевую
                # подстановку разрешает решение пользователя в анкете
                # («продолжить по частичным» / «продолжить с нулевыми»)
                # либо признак производителя — тогда допущение названо
                # поимённо и замок снят словами; открытый вопрос анкеты
                # (данные запрошены) или отсутствие решения — остановка.
                _zero_ok, _why = _hist.zero_allowed(_manifest2)
                if _zero_ok:
                    _assume["history"]["assumed_zero_for"] = list(_no_hist)
                    _log("Ш-2с", "допущение",
                         "у %d сотрудников без записи истории остаток на "
                         "начало периода принят нулевым — %s: %s%s"
                         % (len(_no_hist), _why, ", ".join(_no_hist[:10]),
                            " …" if len(_no_hist) > 10 else ""))
                else:
                    _log("Ш-2с", "остановка",
                         "история передана не по всем сотрудникам (%s): %s"
                         % (_why, ", ".join(_no_hist[:10])))
                    raise ButtonStop(
                        "остановка: у части сотрудников нет истории, а "
                        "нулевая подстановка не разрешена — " + _why,
                        "правила %s требуют остатков на начало периода; "
                        "сотрудники без записи в разделе history: %s%s"
                        % (", ".join(r["rule_id"] for r in _assume["history"]["rules"]),
                           ", ".join(_no_hist[:20]),
                           " …" if len(_no_hist) > 20 else ""))

        if task == "pairing":
            # Г-П4 (директива владельца 2026-08-26): пейринг — ТОЛЬКО
            # ortools-построителем (лучший найденный способ);
            # Timefold-пейринг исключён из исполнителей.
            if pairing_builder is None:
                raise ButtonStop(
                    "остановка: пейринг исполняется только "
                    "ortools-построителем (директива Г-П4)",
                    "построитель не сконфигурирован у кнопки")
            # Перенос ВМП (2026-08-28, шаг 5): кнопка зовёт приёмную
            # часть COSMOS напрямую; обёртки cdpm/* остаются для
            # прочих потребителей на переходный период.
            from vmp import pairing_input as pin
            applied = (report or {}).get("applied")
            # Спецификация 1.9: критерий пейринга — из objective.json
            # пакета (req_mode=optimization без критерия не бывает —
            # гейт allowed_modes снял бы режим).
            criterion = None
            forms = None
            try:
                import hashlib as _hl
                import io as _io
                import zipfile as _zf
                _oz = _zf.ZipFile(_io.BytesIO(zip_bytes))
                # Ревизия 1.10: формы порогов читаются ТОЛЬКО при
                # объявленности манифестом с совпавшей суммой
                # (железное правило приёмника решателя, зеркалим).
                _mani = json.loads(_oz.read("manifest.json"))
                _decl = {f_.get("name"): f_.get("sha256")
                         for f_ in (_mani.get("files") or [])}
                if "threshold-forms.json" in _oz.namelist():
                    _tfb = _oz.read("threshold-forms.json")
                    if _decl.get("threshold-forms.json") \
                            == _hl.sha256(_tfb).hexdigest():
                        forms = json.loads(_tfb.decode("utf-8"))
                criterion = (json.loads(_oz.read("objective.json"))
                             .get("pairing") or {}).get("criterion")
            except Exception:                      # noqa: BLE001
                criterion = None
            if req_mode == "optimization" and criterion is None:
                raise ButtonStop(
                    "остановка: оптимизация пейринга без критерия "
                    "авиакомпании невозможна",
                    "пакет не несёт objectives.pairing.criterion "
                    "(спецификация 1.9)")
            # Виток «путь к нулю» (отмашка владельца 2026-08-26):
            # ёмкость баз законна в аргументах ТОЛЬКО с тёплым
            # стартом точной фазы — без него живой урок v5: честный
            # отказ поиска за весь бюджет при выполнимом ограничении.
            if base_capacity and not warm_from:
                raise ButtonStop(
                    "остановка: ёмкость баз включается только с "
                    "тёплым стартом точной фазы",
                    "решение сторон 2026-08-26 (урок v5): без "
                    "warm_from жёсткая ёмкость приводит к отказу "
                    "поиска — укажите warmFrom=<каталог прежнего "
                    "нажатия пейринга>")
            warm_path = None
            if warm_from:
                warm_path = run_dir / warm_from / "builder-catalog.json"
                if not warm_path.is_file():
                    raise ButtonStop(
                        "остановка: тёплый старт — каталог прежнего "
                        "прогона не найден",
                        f"нет файла {warm_path} (warm_from="
                        f"{warm_from!r}; ожидается штатный "
                        f"builder-catalog.json прежнего нажатия)")
            # Этап 2 конвейера (поручение владельца 2026-08-28):
            # ЕДИНАЯ проверка соответствия ВМП и шаблонов COSMOS ДО
            # начала расчёта — оба рубежа по реестру исполнителей
            # (глубокая сверка правил расчётных зон и критерия +
            # сторож дрейфа кода). Fail-closed: отсутствующий реестр
            # или любой пробел — поимённая остановка, расчёт не
            # начинается. Модель — из БАЙТОВ пакета (те же, что едут
            # в расчёт), не из каталога прогона.
            from vmp import registry_check as _rc
            try:
                import io as _io3
                import zipfile as _zf3
                _zip3 = _zf3.ZipFile(_io3.BytesIO(zip_bytes))
                _mdoc3 = json.loads(_zip3.read("model.json"))
                # Форма ВМП пакета (структура владельца 2026-09-02):
                # ярусы критериев судит приёмник — несколько критериев
                # или ярус > 1 — честный стоп до пакета решателя.
                _form3 = (json.loads(_zip3.read("vmp-form.json"))
                          if "vmp-form.json" in _zip3.namelist() else None)
                conf = _rc.conformance_gate(
                    _mdoc3,
                    criterion if req_mode == "optimization" else None,
                    Path(__file__).resolve().parent.parent.parent,
                    reg_path,
                    form=_form3)
            except _rc.ConformanceError as e:
                if e.gaps and all(g.get("kind") == "catalog_unavailable"
                                  for g in e.gaps):
                    # Каталог шаблонов (шаг 2) недоступен — это не пробел
                    # ВМП, а среда: остановка называет причину словами.
                    _log("Ш-1п2", "остановка",
                         "каталог шаблонов недоступен")
                    raise ButtonStop(
                        "остановка: каталог шаблонов недоступен",
                        str(e), conformance_gaps=e.gaps)
                _log("Ш-1п2", "остановка",
                     f"соответствие ВМП: пробелов {len(e.gaps)}")
                # Каждый пробел называет свою заявку каталога: номер и
                # состояние (0.16 шага 6) — иначе человек ищет её сам.
                # Заявки на вид подаются здесь же; строки реестра не
                # трогаются (обогащается копия остановки).
                _cat_rows = catalog_gap_rows(
                    e.gaps, _mdoc3, run_dir.name, out, task,
                    criterion=(criterion if req_mode == "optimization"
                               else None), step="Ш-1п2", log=_log)
                raise ButtonStop(
                    "остановка: ВМП не закрыт шаблонами COSMOS",
                    str(e),
                    conformance_gaps=gaps_with_requests(e.gaps, _cat_rows),
                    catalog_requests=_cat_rows)
            _log("Ш-1п2", "пройден",
                 "соответствие ВМП: правил %d, критерий %s, реестр "
                 "от %s%s" % (conf["rules_checked"],
                              "проверен" if conf["criterion_checked"]
                              else "не задан",
                              conf.get("registry_created"),
                              ("; исключено до получения данных по "
                               "решению авиакомпании: %s" % ", ".join(
                                   conf["rules_excluded_until_data"]))
                              if conf.get("rules_excluded_until_data")
                              else ""))
            # Композиция целевой пейринга (канон ВМП, COSMOS 88f75d0):
            # цена цепочки = критерий + Σ штрафов мягких правил части
            # «Пейринг» (ночёвка, окно дней недели, продление). Штрафы
            # едут в построитель в режимах optimization и
            # feasibility-soft; в feasibility-hard мягкие не судятся.
            # Мягкое правило чужой семьи или окно не в местном времени
            # базы — отказ транслятора словами (штраф иначе выпал бы
            # из цели молча).
            soft_pens = None
            if req_mode in ("optimization", "feasibility-soft"):
                try:
                    soft_pens = pin.pairing_soft_penalties(pkg)
                except pin.PairingInputError as e:
                    raise ButtonStop("остановка: мягкие правила пейринга "
                                     "не транслируются в цель построителя",
                                     str(e))
            # Ш-1п3 (внешний аудит 04.09.2026, A1): пороги жёстких
            # правил зоны ДО трансляции — правило с параметром, чей порог
            # решатель не применил (нет в applied), исполнителя не
            # получит; остановка называет правило и параметр, а не
            # безымянный «недостающий ключ» транслятора.
            from vmp import execution_manifest as _xm
            _thr_gaps = _xm.thresholds_gaps(
                _mdoc3.get("rules") or [], applied or {}, task="pairing")
            if _thr_gaps:
                _log("Ш-1п3", "остановка",
                     "пороги ВМП не применены решателем: "
                     + "; ".join("%s (%s)" % (g["rule_id"], g["param"])
                                 for g in _thr_gaps))
                raise ButtonStop(
                    "остановка: порог правила ВМП не передан построителю",
                    "; ".join(g["note"] for g in _thr_gaps),
                    conformance_gaps=_thr_gaps)
            # Граница «стыковка / отдых» — из правил пакета (решение
            # владельца 06.09.2026, P3-1): одна у построителя, решателя и
            # проверки по ИДАК; вычисляется ЗДЕСЬ, один раз при нажатии,
            # из применённых порогов решателя для этого пакета
            # (vmp.sit_boundary.derive): ручка max_sit_duration
            # (day_break_threshold_min > 0) — порог из неё, иначе пол
            # минимального отдыха пакета по месту (min_rest_home_min /
            # min_rest_outstation_min; показанием T — меньший); ни ручки,
            # ни пола — остановка словами, подстановок нет (литерал
            # chain_seed.SOLVER_DAY_BREAK_MIN — контракт Kotlin вне
            # пакета, здесь не читается). При ручке значение едет
            # построителю ключом --sit-max ниже; без ручки построитель
            # режет по полу отдыха станции сам; провенанс называет
            # источник и модельное упрощение.
            from vmp import sit_boundary as _sb
            _sit_bnd = _sb.derive(
                applied or {},
                rules_of=((report or {}).get("applied_rules")
                          if isinstance(report, dict) else None))
            if _sit_bnd["value"] is None:
                _log("Ш-1п3", "остановка", _sit_bnd["source"])
                raise ButtonStop("остановка: " + _sb.UNDEFINED,
                                 _sit_bnd["source"])
            _log("Ш-1п3", "граница смены",
                 "T=%d мин — %s" % (_sit_bnd["value"], _sit_bnd["source"]))
            try:
                bargs, prov = pin.builder_args(
                    applied or {}, payload,
                    criterion=(criterion
                               if req_mode == "optimization"
                               else None),
                    soft_penalties=soft_pens,
                    duty_extensions=((report or {}).get("applied_extensions") or {}
                                     ).get("max_duty_span_min"),
                    base_cap_active=base_capacity,
                    budget_s=spent_limit_s,
                    deterministic_time=((deterministic or {})
                                        .get("deterministic_time")))
            except pin.PairingInputError as e:
                raise ButtonStop("остановка: трансляция порогов "
                                 "построителю невозможна", str(e))
            # Приёмка №27 (2026-08-28, рекомендация COSMOS «а»):
            # глубина вариантов перегонов перечислителя — 4 (умолчание
            # построителя не строило законные кандидаты одного
            # семейства рейсов; углубление СОБСТВЕННОГО перечислителя
            # — законный добор, данные внешнего свидетеля не
            # передаются).
            bargs += ["--dh-options", "4"]
            prov["--dh-options"] = {
                "value": 4,
                "from": "приёмка №27: глубина перегонов собственного "
                        "перечислителя (рекомендация COSMOS)"}
            # Пакет улучшений после №30, пункт (а): обходы патологий
            # CP-SAT 9.15 в профиле кнопки. Без --no-presolve
            # однопоточная предобработка рвёт крышку перерешиваний
            # изнутри (№30: заходы по 50 мин вместо ≤33); workers > 8
            # — известная регрессия многопоточного поиска 9.15
            # (or-tools#5025, обход подтверждён вендором).
            # В профиле Д1 бригада поиска — один поток (ключ уже
            # добавлен транслятором): второй --workers перебил бы его,
            # и детерминизм пропал бы молча.
            bargs += (["--no-presolve"] if deterministic
                      else ["--no-presolve", "--workers", "8"])
            prov["--no-presolve"] = {
                "value": True,
                "from": "пакет улучшений №30 (а): предобработка "
                        "CP-SAT 9.15 патологична на плотном "
                        "покрытии — лечение причины, не симптома"}
            prov["--workers"] = {
                "value": 1 if deterministic else 8,
                "from": "пакет улучшений №30 (а): предел бригады "
                        "поиска ≤ 8 (регрессия or-tools#5025, обход "
                        "вендора)"}
            # Точечный добор (пакет решателя 2a8e508, отмашка
            # владельца «догон PCDPM+»): конструкции каталогов
            # добавляются КАНДИДАТАМИ в пул (отбор волен — жёсткое
            # закрепление вытесняло соседей, смок 26→28). Файл
            # обязан существовать: молча пропущенная порция дала бы
            # «добор не сработал» без причины.
            for cat in (inject_catalogs or []):
                cpath4 = Path(cat)
                if not cpath4.is_file():
                    raise ButtonStop(
                        "остановка: каталог добора не найден",
                        f"нет файла {cpath4}")
                bargs += ["--inject-catalog", str(cpath4)]
                prov.setdefault("--inject-catalog", []).append({
                    "value": str(cpath4),
                    "from": "каталог конструкций добора (кандидаты "
                            "в пул, отбор волен)"})
            if warm_path is not None:
                # Подсказка решателю (не восстановление): построитель
                # сопоставляет цепочки прежнего каталога кандидатам по
                # множеству рабочих плеч, частичная подсказка законна;
                # его отчёт несёт warm_hints_matched/total.
                bargs += [WARM_CATALOG_FLAG, str(warm_path)]
                prov[WARM_CATALOG_FLAG] = {
                    "value": str(warm_path),
                    "from": f"каталог прежнего нажатия {warm_from}"}
            # Закрепления из утверждённых назначений раздела истории
            # (1.17.1): цепочки-данность построителя (--pin-catalog) —
            # что заказчик утвердил заранее, входит в решение
            # неприкосновенным со своей честной ценой (A5, слово
            # владельца 05.09.2026 «нужно»). Отказ транслятора — стоп
            # словами, не молчаливый пейринг без закреплений.
            from webapp import task_package as _wtp_hp
            try:
                _hist_pins = (_wtp_hp.history_pin_catalog(pkg, payload)
                              if isinstance(payload, dict) else None)
            except _wtp_hp.TaskPackageRefused as e:
                _log("Ш-2с", "остановка", _journal_preview(str(e), 300))
                raise ButtonStop(
                    "остановка: утверждённые назначения истории не "
                    "переносятся в закрепления пейринга", str(e))
            if _hist_pins:
                pin_path = out / "history-pins.json"
                pin_path.write_text(
                    json.dumps(_hist_pins, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                bargs += ["--pin-catalog", str(pin_path)]
                prov["--pin-catalog"] = {
                    "value": str(pin_path),
                    "from": "утверждённые назначения раздела history "
                            "(1.17.1): цепочек %d"
                            % len(_hist_pins["selected"])}
                _assume["history"]["approved_pinned_chains"] = [
                    {"sourceUnit": r["sourceUnit"], "legs": r["fns"],
                     "employees": r.get("employees")}
                    for r in _hist_pins["selected"]]
                _log("Ш-2с", "закрепления истории",
                     "утверждённых цепочек для пейринга: %d — %s"
                     % (len(_hist_pins["selected"]),
                        "; ".join("%s: %s" % (r["sourceUnit"],
                                              ", ".join(r["fns"][:4]))
                                  for r in _hist_pins["selected"][:10])))
            # Этап 5б: файл УСЛОВИЙ собирается ДО контракта — его
            # минутный предел размаха командировки даёт контракту
            # честную календарную оболочку (живой брак dobor2:
            # чужое умолчание класса судило 6 дат).
            from vmp import conditions_export as cex
            conds = None
            conds_err = None
            try:
                conds = cex.build_conditions(
                    pkg.get("model") or {}, forms,
                    pkg.get("manifest") or {}, payload)
            except cex.ConditionsExportError as e:
                conds_err = e
            span_min = None
            if conds is not None:
                sp = (conds.get("A") or {}).get("max_pairing_span_min")
                if isinstance(sp, dict) \
                        and isinstance(sp.get("value"), int):
                    span_min = sp["value"]
            # Г-П5 (ревизия 1.10): контракт правил построителя из
            # машинных форм пакета — собирается, пишется в след и
            # ПОДАЁТСЯ В ЗАПУСК ВСЕГДА (внешний аудит 04.09.2026, A1;
            # класс ошибки: умолчания площадки молча подменяли нормы
            # ВМП — прежде контракт лежал в следе «ждёт приёмника», а
            # приёмник duty_span_matrix у построителя давно есть).
            # Безнормовая верхняя граница командировки (ночь демо
            # 29→30.08, согласовано с решателем): у мира без нормы
            # границы и без ответа о размахе пределом поиска служит
            # весь горизонт данных — факт данных, не выдуманная
            # норма (та же семантика, что у chain_max_days условий).
            # Приоритет в контракте: applied > оболочка > горизонт.
            horizon_days = pin._horizon_days(payload)
            contract = pin.source_rules_contract(
                forms, applied or {}, span_min=span_min,
                horizon_days=horizon_days,
                # Спецификация 1.16: система времени пакета — объектом
                # манифеста (A9); у пакетов младше ключа нет.
                manifest_time=(_manifest2.get("time")
                               if isinstance(_manifest2.get("time"), dict)
                               else None),
                duty_cap=(prov.get("--duty-cap") or {}).get("value"))
            if contract is None:
                raise ButtonStop(
                    "остановка: контракт правил построителя не собран",
                    "пакет не несёт ни форм порогов, ни применённых "
                    "порогов — норм ВМП для построителя нет")
            import hashlib as _hl2
            cbytes = (json.dumps(contract, ensure_ascii=False,
                                 indent=1) + "\n").encode("utf-8")
            cpath2 = out / "source-rules.json"
            cpath2.write_bytes(cbytes)
            fp = _hl2.sha256(cbytes).hexdigest()
            contract_passed = None
            if source_rules:
                # Без --source-rules-strict: вердикт владельца
                # словаря 2026-08-27 — в кнопочной схеме источник
                # один (applied) и след полный, угрозы подмены
                # умолчаниями нет по построению; strict — для
                # полноконтрактных прогонов, где файл — един-
                # ственный источник. Путь и отпечаток — атомарно.
                bargs += ["--source-rules", str(cpath2),
                          "--source-rules-fp", fp]
                prov["--source-rules"] = {
                    "value": str(cpath2), "fp": fp,
                    "from": "threshold-forms.json и applied-пороги "
                            "пакета (объявлены манифестом)"}
                contract_passed = contract
                # Профиль пакета у построителя: настройка площадки не
                # читается, незаданный порог — отказ словами.
                bargs += ["--vmp-package"]
                prov["--vmp-package"] = {
                    "value": True,
                    "from": "аудит A1 (04.09.2026): умолчания площадки "
                            "на пути пакета запрещены"}
                # Предел суток цепочки без нормы в applied — из
                # контракта (оболочка условий / горизонт данных);
                # транслятор такого умолчания не выдумывает.
                if "--pairing-span" not in bargs \
                        and isinstance(contract.get("max_pairing_days"),
                                       int):
                    _days = int(contract["max_pairing_days"])
                    bargs += ["--pairing-span", str(_days * 1440)]
                    prov["--pairing-span"] = {
                        "value": _days * 1440,
                        "from": "контракт source-rules: max_pairing_days "
                                "(applied > оболочка условий > горизонт "
                                "данных)"}
            else:
                prov["--source-rules"] = {
                    "value": str(cpath2), "fp": fp,
                    "note": "СТЕНДОВЫЙ переключатель source_rules=False: "
                            "контракт собран, в запуск не подан — "
                            "боевого пути с выключенным контрактом нет"}
            # Этап 5б: файл УСЛОВИЙ расчёта (точная ярусная форма
            # пределов; замер решателя 2026-08-27 — снимает
            # большинство ложных «перебором»). Собирается и пишется
            # в след всегда; в запуск — при собираемости (аудит A1).
            # Несобираемость — пробелы в след; нужен ли файл условий
            # какому-либо правилу, судит ведомость исполнения ниже
            # (правило без исполнителя — остановка с его именем).
            if conds is not None:
                cb3 = (json.dumps(conds, ensure_ascii=False,
                                  indent=1) + "\n").encode("utf-8")
                cpath3 = out / "conditions.json"
                cpath3.write_bytes(cb3)
                import hashlib as _hl3
                fp3 = _hl3.sha256(cb3).hexdigest()
                if conditions:
                    bargs += ["--conditions", str(cpath3)]
                    prov["--conditions"] = {
                        "value": str(cpath3), "fp": fp3,
                        "from": "экспорт условий из пакета (машинные "
                                "формы + ответы анкеты)"}
                else:
                    prov["--conditions"] = {
                        "value": str(cpath3), "fp": fp3,
                        "note": "СТЕНДОВЫЙ переключатель "
                                "conditions=False: собран, в запуск не "
                                "подан"}
            else:
                (out / "conditions-gaps.json").write_text(
                    json.dumps({"gaps": conds_err.gaps},
                               ensure_ascii=False, indent=1),
                    encoding="utf-8")
                prov["--conditions"] = {
                    "note": "не собрался — пробелы в "
                            "conditions-gaps.json; правила, которым он "
                            "нужен, судит ведомость исполнения"}
            # Одна граница «стыковка / отдых» у построителя, решателя и
            # проверки по ИДАК — из правил пакета (решение владельца
            # 06.09.2026; прежняя правка 998d57e передавала литерал 360
            # контракта Kotlin, а решатель того же пакета без ручки читал
            # 0 — каталог резался с мёртвой зоной [360, пол), P3-1).
            # Граница вычислена выше (_sit_bnd, vmp.sit_boundary): правило
            # максимальной стыковки (ручка) — порог едет ключом --sit-max;
            # без ручки ключ НЕ подаётся — построитель режет смену по полу
            # отдыха станции сам (--rest-base / --rest-off из тех же
            # applied: критерий границы смены = пол по месту, как у
            # решателя), и это заявляется как модельное упрощение
            # (GPT-053 / CDPM-038). Явный --sit-max пакета/оператора
            # сильнее.
            if "--sit-max" not in bargs:
                _knob = _sit_bnd["basis"] == "knob"
                if _knob:
                    bargs += ["--sit-max", str(_sit_bnd["value"])]
                prov["--sit-max"] = {
                    "value": _sit_bnd["value"] if _knob else None,
                    "passed": _knob,
                    "boundary_min": _sit_bnd["value"],
                    "basis": _sit_bnd["basis"],
                    "reading": _sit_bnd["reading"],
                    "by_place": _sit_bnd["by_place"],
                    "rules": _sit_bnd["rules"],
                    "assumption": _sit_bnd["assumption"],
                    "from": _sit_bnd["source"],
                    "note": (None if _knob else
                             "ключ не подан: правила максимальной стыковки в "
                             "пакете нет — построитель режет смену по полу "
                             "отдыха станции (--rest-base / --rest-off из "
                             "тех же применённых порогов); критерий границы "
                             "смены = пол по месту, как у решателя"),
                    "terms": _sb.TERMS,
                    "rule": "граница «стыковка / отдых» одна у построителя, "
                            "решателя и проверки по ИДАК: правило "
                            "максимальной стыковки пакета, иначе пол "
                            "минимального отдыха по месту (решение "
                            "владельца 06.09.2026)"}
                # Допущение — в выдаче и сертификате той же записью, что
                # заявление об истории (A7): assumptions.sit_boundary.
                if isinstance(_assume, dict):
                    _assume["sit_boundary"] = {
                        "basis": _sit_bnd["basis"],
                        "boundary_min": _sit_bnd["value"],
                        "by_place": _sit_bnd["by_place"],
                        "rules": _sit_bnd["rules"],
                        "source": _sit_bnd["source"],
                        "assumption": _sit_bnd["assumption"],
                        "terms": _sb.TERMS}
            (out / "builder-input.json").write_text(
                json.dumps({"args": bargs, "provenance": prov},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
            # Ш-1п3 (аудит A1): ведомость исполнения правил — общий
            # контракт (план 04.09, раздел 5): правило → применимость →
            # параметры → исполнитель по реестру → фактически переданные
            # построителю значения → место исполнения. Пишется в след
            # всегда; жёсткое применимое правило без переданного
            # значения или исполнителя — остановка словами.
            _reg_doc = _rc.load_registry(reg_path)
            manifest_doc = _xm.build(
                _mdoc3.get("rules") or [], _reg_doc, "pairing", req_mode,
                bargs, prov, contract_passed,
                source_rules_fp=fp if contract_passed is not None
                else None)
            (out / "execution-manifest.json").write_text(
                json.dumps(manifest_doc, ensure_ascii=False, indent=1)
                + "\n", encoding="utf-8")
            try:
                _xsum = _rc.execution_gate(manifest_doc)
            except _rc.ConformanceError as e:
                _log("Ш-1п3", "остановка",
                     "ведомость исполнения: правил без исполнения %d"
                     % len(e.gaps))
                raise ButtonStop(
                    "остановка: правило ВМП без исполнения у построителя",
                    str(e) + " — " + "; ".join(
                        "%s: %s" % (g["rule_id"], g["note"])
                        for g in e.gaps),
                    conformance_gaps=e.gaps)
            _log("Ш-1п3", "пройден",
                 "ведомость исполнения: правил зоны %d, исполняется с "
                 "переданными значениями %d (execution-manifest.json)"
                 % (_xsum["rules_total"], _xsum["rules_executed"]))
            _log("Ш-2", "построитель запущен",
                 "ortools: " + " ".join(bargs))
            catalog_bytes = pairing_builder(payload, bargs, out)
            catalog = json.loads(catalog_bytes.decode("utf-8"))
            # Отчёт проверки построителя (pairing_builder_check_*.json
            # рядом с каталогом) — сертификатору: сверка целевой
            # пейринга (ip_objective = Σ cost выбранных = total_cost
            # отчёта) без него не проводится. Регистрация у решателя
            # идёт БАЙТАМИ каталога, поэтому запись в словарь на неё
            # не влияет.
            _checks = sorted(out.glob("pairing_builder_check_*.json"))
            if _checks:
                try:
                    catalog["builder_report"] = json.loads(
                        _checks[-1].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
            verdict = str((catalog.get("coverage") or {})
                          .get("verdict") or "<нет вердикта>")
            chains = len(catalog.get("selected") or [])
            if verdict not in ("OK", "INFEASIBLE_INPUT",
                               "INCOMPLETE_SEARCH"):
                _log("Ш-2", "каталог отвергнут",
                     f"вердикт {verdict} вне белого списка")
                return _deliver(
                    "остановка: каталог пейринга вне белого списка",
                    coverage_verdict=verdict, chains=chains,
                    note=("ослабленный каталог не регистрируется — "
                          "правьте вход или бюджет построителя"))
            import hashlib as _hl
            sha = _hl.sha256(catalog_bytes).hexdigest()
            reg = solver.pairing_catalog(
                payload,
                base64.b64encode(zip_bytes).decode("ascii"),
                base64.b64encode(catalog_bytes).decode("ascii"),
                sha)
            solver_run = str(reg.get("runId"))
            _log("Ш-3", "каталог зарегистрирован",
                 f"runId {solver_run}, цепочек {chains}, "
                 f"вердикт {verdict}")
            # Ш-4: сертификация пейринга (поручение владельца
            # 2026-09-01) — тем же механизмом, что у ростеринга;
            # карта рейсов — из набора решателя (тот же источник,
            # что у построителя и ФПР). Без сертификатора — прежнее
            # поведение (доставка каталога).
            # A8: трёхчастный результат едет в выдаче ОБЕИХ задач; без
            # сертификатора допустимость честно «не установлена».
            from vmp.run import verdicts as _vd
            _opt = _vd.pairing_optimality(catalog.get("ip_status"))
            _inc = verdict == "INCOMPLETE_SEARCH"
            cert_extra: dict = {
                "result": _vd.result_of_certificate(
                    None, optimality=_opt, search_incomplete=_inc),
                "uncovered": [], "certificate_gate": []}
            if pairing_certify_fn is not None:
                fmap = {}
                for f in payload.get("flights") or []:
                    fmap[f.get("flightNumber")] = {
                        "t0": f.get("departureUTCDateTime"),
                        "t1": f.get("arrivalUTCDateTime"),
                        "dep": f.get("departureAirport"),
                        "arr": f.get("arrivalAirport")}
                report = pairing_certify_fn(
                    pkg.get("model") or {}, catalog, fmap)
                _inject_assumptions(report, _assume)
                (out / "certificate.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=1)
                    + "\n", encoding="utf-8")
                from vmp.run import roster_certify as rc
                (out / "certificate.md").write_text(
                    rc.render_certificate(report), encoding="utf-8")
                v = report.get("certificate") or {}
                _log("Ш-4", "сертифицирован" if v.get("certified")
                     else "не сертифицирован",
                     _journal_preview(str(v.get("note") or ""), 200))
                # Гейт сертификата — против свода-среза зоны пейринга
                # (тот же срез, по которому строился сертификат).
                _scoped = rc.pairing_scope_svod(pkg.get("model") or {})[0]
                _res, _gate = _certificate_result(
                    _scoped, report, optimality=_opt,
                    search_incomplete=_inc, log=_log)
                cert_extra = {
                    "certified": bool(v.get("certified")) and not _gate,
                    "findings": v.get("findings"),
                    "judged_on_rostering":
                        v.get("judged_on_rostering") or None,
                    "result": _res,
                    "uncovered": _res["uncovered"],
                    "certificate_gate": _gate,
                    "certificate_verdict": _verdict_words(_res, v, _gate)}
            # Три исхода по непокрытым в выдаче (аудит A4, 04.09.2026):
            # доказано невозможно (excluded_proven, с доказательствами) /
            # поиск не завершён или не найдено (search_incomplete, с
            # причиной словами). Частичное покрытие ≠ выполненный план:
            # complete=false виден получателю прямо в выдаче.
            _cov = catalog.get("coverage") or {}
            coverage_out = {
                "complete": bool(_cov.get("complete", verdict == "OK")),
                "excluded_proven": list(_cov.get("excluded_proven")
                                        or catalog.get(
                                            "proven_impossible_legs")
                                        or []),
                "search_incomplete": dict(_cov.get("not_built_reasons")
                                          or {})}
            return _deliver(
                "пейринг построен",
                **cert_extra, **mode_info,
                assumptions=_assume,
                coverage_verdict=verdict, chains=chains,
                coverage=coverage_out,
                proven_impossible_legs=(
                    catalog.get("proven_impossible_legs") or None),
                proven_impossible_proofs=(
                    catalog.get("proven_impossible_proofs") or None),
                catalog_sha256=sha,
                schedule={"solver_run_id": solver_run},
                trace={"cdpm_run": run_dir.name,
                       "solver_run_id": solver_run,
                       "task": "pairing",
                       "package_fingerprint":
                           reg.get("packageFingerprint"),
                       "spec_version": (pkg.get("manifest") or {})
                           .get("spec_version"),
                       "coverage_authorization": coverage_auth})

        # Ярусы критериев части «Ростеринг» (структура ВМП 2026-09-02):
        # несколько критериев или ярус > 1 в форме пакета — честный
        # стоп при оптимизации, а не молчаливый выбор первой целевой.
        if task == "rostering" and req_mode == "optimization":
            import io as _io4
            import zipfile as _zf4
            from vmp import registry_check as _rc4
            _zip4 = _zf4.ZipFile(_io4.BytesIO(zip_bytes))
            _form4 = (json.loads(_zip4.read("vmp-form.json"))
                      if "vmp-form.json" in _zip4.namelist() else None)
            _tiers = _rc4.criteria_tiers_gaps(_form4, parts=("rostering",))
            if _tiers:
                _log("Ш-1п2", "остановка",
                     "ярусы критериев части «Ростеринг»: "
                     + _tiers[0]["note"])
                raise ButtonStop(
                    "остановка: ярусы критериев не реализованы",
                    _tiers[0]["note"], conformance_gaps=_tiers)
        # Необязательный предел «без улучшения» передаётся ТОЛЬКО
        # заданным: клиенты без параметра (двойники набора, прежние
        # реализации) остаются валидными по построению.
        _kw = ({"unimproved_limit_s": unimproved_spent_limit_s}
               if unimproved_spent_limit_s is not None else {})
        if deterministic:
            _kw["deterministic"] = deterministic
        if moves:
            _kw["moves"] = moves
        started = solver.start(
            payload, base64.b64encode(zip_bytes).decode("ascii"),
            req_mode, req_obj, spent_limit_s, task, pairing_run_id,
            **_kw)
        solver_run = str(started.get("runId"))
        fingerprint = started.get("packageFingerprint")
        _log("Ш-2", "принят решателем",
             f"runId {solver_run}, задача {task}, "
             f"режим {started.get('mode')}"
             + (f", на пейринге {pairing_run_id}"
                if pairing_run_id else ""))

        # Ш-2а: счёт по идентификатору.
        t0 = time.monotonic()
        while True:
            st = solver.status(solver_run)
            state = st.get("state")
            if state == "готов":
                break
            if state == "упал":
                raise ButtonStop(
                    "прогон упал",
                    str(st.get("failure") or "причина не названа"),
                    solver_run_id=solver_run)
            if time.monotonic() - t0 > timeout_s:
                raise ButtonStop(
                    "остановка: превышено время ожидания счёта",
                    f"состояние «{state}», runId {solver_run} живёт "
                    f"— статус можно спросить позже",
                    solver_run_id=solver_run)
            sleep(poll_s)
        _log("Ш-2а", "решён", f"runId {solver_run}")
        # Система прогонов (07.09.2026, раздел 7.8): последний ответ
        # статуса прогона — артефактом нажатия (счёт, профиль, ходы,
        # перегоны — что отдаёт решатель); источник паспорта попытки,
        # не журнал вызовов. Отказ записи не роняет нажатие.
        try:
            (out / "solver-status.json").write_text(
                json.dumps(st, ensure_ascii=False, indent=1, default=repr)
                + "\n", encoding="utf-8")
        except Exception:                                  # noqa: BLE001
            pass

        solved = solver.result(solver_run)
        applied = solver.applied(solver_run)
        # Ш-3: показания ИМЕННО этого прогона.
        if str(applied.get("runId") or solver_run) != solver_run:
            raise ButtonStop(
                "остановка: расхождение прогона и показаний",
                f"показания несут runId {applied.get('runId')!r}, "
                f"ожидался {solver_run}")
        # Раздел 7.8: показания applied как есть — артефактом нажатия.
        try:
            (out / "applied-rules.json").write_text(
                json.dumps(applied, ensure_ascii=False, indent=1,
                           default=repr) + "\n", encoding="utf-8")
        except Exception:                                  # noqa: BLE001
            pass
        # Шапка прогона — в сертификацию: solver_hard при
        # допустимости обязан быть явным нулём, не null (замечание
        # первого живого прогона кнопки COSMOS).
        # Счёт — из СТАТУСА прогона по идентификатору (показания
        # прогона lastRun не несут — живая мелочь кнопки COSMOS:
        # solver_hard оставался null при допустимости).
        run_info = {"score": st.get("score")
                    or (applied.get("lastRun") or {}).get("score"),
                    "package_fingerprint": fingerprint,
                    "spec_version": (pkg.get("manifest") or {})
                    .get("spec_version"),
                    "source_run": run_dir.name}
        if build_input is None:
            from webapp import certify_input as ci
            build_input = ci.build
        from webapp.certify_input import CertifyInputError
        # H11-WIRE-1: отчёт цены ночёвок решателя едет в сборку входа
        # вместе с решением и показаниями ИМЕННО этого runId (сверены
        # выше) — сборщик берёт его из решения, иначе из этих показаний;
        # глобального «последнего отчёта» у кнопки нет.
        try:
            solution, coverage = build_input(
                pkg, payload, solved, applied,
                f"кнопка {run_dir.name}/{solver_run}")
        except CertifyInputError as e:
            raise ButtonStop(
                "остановка: показания не подтверждены гейтом",
                str(e)[:1500], solver_run_id=solver_run)
        _log("Ш-3", "показания подтверждены",
             f"покрытие ручек: {len(coverage or {})}")

        # Сертификация против всего свода.
        if certify_fn is None:
            raise ButtonStop("остановка", "сертификатор не передан "
                                          "вызывающей стороной")
        if isinstance(solution, dict)                 and "runInfo" not in solution                 and run_info.get("score"):
            solution["runInfo"] = run_info
        if isinstance(solution, dict):
            # Сертификатору — всё, что кнопка знает о прогоне, тем же
            # каналом, что и runInfo (данные едут внутри решения, а
            # контракт certify_fn(doc, solution) не меняется):
            # справочники входной книги — иначе допуск по типу
            # «не проверяем: нет данных о типе ВС и допусках»; пороги
            # решателя — иначе применённые ручки не засчитываются.
            # Первые сертификаты H0 (03.09) показали оба пробела.
            tables = (pkg.get("clean_data") or {}).get("tables")
            if tables and "reference_tables" not in solution:
                solution["reference_tables"] = tables
            thr = (applied.get("lastRun") or {}).get("thresholds")
            if isinstance(thr, dict) and "thresholds" not in solution:
                solution["thresholds"] = thr
            # Показание решателя о перегонах — в сертификат словами
            # (единый план 05.09.2026, раздел 3).
            if isinstance(st, dict) and isinstance(st.get("deadheads"), dict) \
                    and "deadheads" not in solution:
                solution["deadheads"] = dict(st["deadheads"])
            # Сводка резерва «база × сутки» — оракулом по набору и
            # решению (webapp.reserve_oracle: свободные по ролям после
            # занятости, норма из применённых порогов). Без неё правило
            # о нормах резерва оставалось единственным непокрытым в
            # сертификате ростеринга H0 (03.09): «листа-сводки резерва
            # нет» — а посчитать её кнопка могла всегда.
            if "reserve" not in solution:
                try:
                    from webapp import certify_input as _ci
                    _rs = _ci.reserve_sheet(payload, solved, applied)
                    if _rs.get("rows"):
                        solution["reserve"] = _rs["rows"]
                except Exception as e:                    # noqa: BLE001
                    _log("Ш-3р", "сводка резерва не собрана", _journal_preview(str(e), 200))
        report = certify_fn(doc, solution)
        _inject_assumptions(report, _assume)
        # Д4, пункт 3 — штрафная сверка: штраф по ВМП против разбора
        # счёта решателя под пакетом прогона, по каждой мягкой строке
        # и целевой. Штатная кнопка её не проводила (penalty_reconciliation
        # оставался null), хотя решатель разбор отдаёт.
        _hard_breakdown = None
        try:
            from vmp import penalty_reconcile as _pr
            _bd = solver.score_breakdown(solver_run, solved)
            # Разложение жёсткого счёта по ограничениям (живой проход
            # H0-КИ-окна 05.09.2026: «−12hard» без имён — вопрос «какие
            # именно ограничения» оставался без ответа): в выдачу идут
            # ограничения с отрицательным hard — имя, hard, совпадений.
            if isinstance(_bd, dict) and isinstance(_bd.get("constraints"), dict):
                _hard_breakdown = {
                    str(name): {"hard": c.get("hard"), "matches": c.get("matches")}
                    for name, c in _bd["constraints"].items()
                    if isinstance(c, dict) and isinstance(c.get("hard"), (int, float))
                    and not isinstance(c.get("hard"), bool) and c.get("hard") < 0} or None
                if _hard_breakdown:
                    _log("Ш-4р", "жёсткие нарушения решателя",
                         "; ".join(f"{n}: {c['hard']}hard, совпадений {c.get('matches')}"
                                   for n, c in sorted(_hard_breakdown.items(),
                                                      key=lambda kv: kv[1]["hard"])[:12]))
            _cert = report.get("certificate") if isinstance(report, dict) else None
            if isinstance(_cert, dict) and isinstance(_bd, dict):
                # Все части формы, не только «Ростеринг»: решатель
                # исполняет мягкие правила ОБЕИХ частей по уже
                # зафиксированным цепочкам (ночёвки, окно выходных,
                # продление — часть «Пейринг»), и их вклады стоят в
                # разборе счёта этого прогона.
                _rec = _pr.reconcile(pkg.get("form"), applied.get("lastRun"),
                                     _bd, report, part=None)
                # A8: сверка кладётся в шапку с ПЕРЕСЧЁТОМ флага и
                # результата — прежде она ложилась после вердикта, и
                # «разошлось» оставляло прежний положительный статус.
                from vmp.run import roster_certify as _rc
                _rc.attach_penalty_reconciliation(report, _rec)
                _log("Ш-4п", "штрафная сверка",
                     f"{_rec.get('verdict')}: строк {len(_rec.get('rows') or [])}")
        except Exception as e:                            # noqa: BLE001
            _log("Ш-4п", "штрафная сверка не проведена", _journal_preview(str(e), 200))
        # ВЫГРУЗКА РЕШЕНИЯ — артефактами нажатия, рядом с сертификатом:
        # решение решателя как есть (solved.json) и вход сертификатора
        # (solution.json: назначения, смены, сводка резерва, пороги,
        # шапка прогона). До этого кнопка хранила только сертификат и
        # доставку, а само расписание оставалось у решателя — конец
        # конвейера «…проверка → выгрузка» был не замкнут (задача
        # владельца 03.09).
        (out / "solved.json").write_text(
            json.dumps(solved, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        (out / "solution.json").write_text(
            json.dumps(solution, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        (out / "certificate.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        from vmp.run import roster_certify as rc
        (out / "certificate.md").write_text(
            rc.render_certificate(report), encoding="utf-8")
        v = report.get("certificate") or {}
        _log("Ш-4", "сертифицирован" if v.get("certified")
             else "не сертифицирован",
             _journal_preview(str(v.get("note") or ""), 200))

        # A8 (аудит 04.09.2026, класс ошибки «итог сильнее
        # доказательства»): вердикт кнопки выводится из ПОКРЫТИЯ и
        # трёхчастного результата, не из флага certified; «допустимо»
        # — только при подтверждённой допустимости, иначе третий исход
        # словами с перечнем непроверенного. Гейт сертификата
        # (cdpm.gates.gate_certify) судит честность шапки на штатном
        # пути: красный гейт — «не удостоверено», не «допустимо».
        from vmp.run import verdicts as _vd
        _hard = v.get("solver_hard")
        result, gate_problems = _certificate_result(
            doc, report,
            optimality=_vd.rostering_optimality(mode_info.get("mode")),
            search_incomplete=bool(isinstance(_hard, int)
                                   and not isinstance(_hard, bool)
                                   and _hard < 0),
            log=_log)
        verdict = _verdict_words(result, v, gate_problems)
        manifest = pkg.get("manifest") or {}
        # Двухфазный поток: подтверждение фиксации — из СТАТУСА
        # прогона (решатель кладёт pairingRunId и число цепочек).
        chained = {}
        if pairing_run_id:
            chained = {"pairing_run_id":
                       st.get("pairingRunId") or pairing_run_id,
                       "pairing_chains": st.get("chains")}
            # Шаг 3 (05.09.2026): соответствие пакет ↔ каталог ↔ прогон
            # — в выдаче поимённо: отпечаток каталога и вердикт
            # утверждённого пейринга из его выдачи в этом же каталоге
            # прогона (W-4: у прежней попытки — из её снимка package_from).
            try:
                _pd = json.loads((run_dir / (package_from or "button-pairing")
                                  / "delivery.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _pd = {}
            chained["pairing_catalog_sha256"] = _pd.get("catalog_sha256")
            chained["pairing_verdict"] = _pd.get("verdict")
            chained["pairing_run_matches_delivery"] = (
                str((_pd.get("schedule") or {}).get("solver_run_id") or "")
                == str(pairing_run_id))
        return _deliver(
            verdict,
            **chained, **mode_info,
            assumptions=_assume,
            # certified выдачи = флаг сертификата (допустимость
            # подтверждена И сверка стоимости сошлась) И зелёный гейт.
            certified=bool(v.get("certified")) and not gate_problems,
            result=result,
            uncovered=result["uncovered"],
            rules_partially_covered=v.get("rules_partially_covered") or [],
            certificate_gate=gate_problems,
            solver_hard=v.get("solver_hard"),
            solver_hard_breakdown=_hard_breakdown,
            # Показание о перегонах (решение владельца 05.09.2026):
            # разрешены ли, основание, кресел на рейс, использовано.
            deadheads=st.get("deadheads") if isinstance(st, dict) else None,
            findings=v.get("findings"),
            counted_share=v.get("counted_share"),
            # H11-6: область учёта ночёвок и паритет событий с решателем
            # — в выдаче кнопки; попутно определённость стоимости шапки
            # (partial — полная стоимость не установлена, словами).
            cost_certainty=v.get("cost_certainty"),
            cost_uncertain_events=v.get("cost_uncertain_events"),
            cost_assessment=v.get("cost_assessment"),
            # C-6 (план §3.8 / C-11): покрытие окон проверок допустимости
            # (ожидается = проверено + неизвестно, перечень проверок с
            # окнами) — в выдаче кнопки, как в шапке сертификата.
            window_coverage=v.get("window_coverage"),
            schedule={"solver_run_id": solver_run,
                      "score": run_info.get("score")},
            trace={"cdpm_run": run_dir.name,
                   "solver_run_id": solver_run,
                   # Задача — из СТАТУСА прогона (контракт решателя:
                   # task едет в статусе), не из просьбы кнопки.
                   "task": st.get("task") or task,
                   "package_fingerprint": fingerprint,
                   "spec_version": manifest.get("spec_version"),
                   # П-1: выбор человека — машинным полем
                   # трассировки (null = сверка не проводилась или
                   # непокрытых нет).
                   "coverage_authorization": coverage_auth},
            artifacts={"certificate": f"{out.name}/certificate.json",
                       "certificate_md": f"{out.name}/certificate.md",
                       "package": f"{out.name}/package.zip",
                       "log": f"{out.name}/log.json"})
    except ButtonStop as e:
        _log("остановка", e.verdict, _journal_preview(e.detail, 300))
        return _deliver(e.verdict, detail=e.detail, **e.extra)


def _certificate_result(svod: dict, report: dict, *, optimality: str,
                        search_incomplete: bool, log=None) -> tuple[dict, list[str]]:
    """Трёхчастный результат по сертификату + вердикт гейта сертификата
    (аудит A8, 04.09.2026). Гейт cdpm.gates.gate_certify судит вид
    допустимости (без мер мягких правил); его красный лишает
    подтверждения — итог не вправе быть сильнее доказательства."""
    from cdpm.gates import gate_certify
    from vmp.run import roster_certify as rc
    from vmp.run import verdicts as _vd
    cert = report.get("certificate") if isinstance(report, dict) else None
    try:
        problems, _ = gate_certify.check(svod or {}, rc.feasibility_view(report))
    except Exception as e:                                    # noqa: BLE001
        problems = [f"гейт сертификата не отработал: {type(e).__name__}: "
                    f"{str(e)[:300]}"]
    if log:
        log("Ш-4г", "гейт сертификата " + ("зелёный" if not problems
                                          else "КРАСНЫЙ"),
            _journal_preview("; ".join(problems), 400) if problems
            else "полнота и счётчики сходятся")
    result = _vd.result_of_certificate(cert, optimality=optimality,
                                       search_incomplete=search_incomplete)
    if problems and result["feasibility"] == "confirmed":
        result = dict(result, feasibility="not_established",
                      optimality="not_proven")
    return result, list(problems)


def _verdict_words(result: dict, cert: dict | None,
                   gate_problems: list[str]) -> str:
    """Слова вердикта кнопки из трёхчастного результата; красный гейт
    при отсутствии нарушений называется своими словами."""
    from vmp.run import verdicts as _vd
    words = _vd.verdict_words(result, cert)
    if gate_problems and result["feasibility"] == "not_established" \
            and not result.get("uncovered"):
        return (f"не удостоверено: гейт сертификата красный "
                f"({len(gate_problems)}): {gate_problems[0][:160]}")
    return words


def _is_rostering_outcome(verdict) -> bool:
    """Законный исход ноги ростеринга для сравнения способов:
    «допустимо», «недопустимо» либо «не удостоверено: …» (A8) —
    остановки шва сравнению не подлежат."""
    v = str(verdict or "")
    return (v == "недопустимо" or v.startswith("допустимо")
            or v.startswith("не удостоверено"))


def margin_pct_setting() -> float:
    """Порог приемлемости пейринга — НАСТРОЙКА производителя
    (решение владельца 2026-08-24, умолчание 5%)."""
    from webapp import way_compare as wc
    from cdpm import providers
    raw = providers.load_settings().get("way_margin_pct")
    if raw is None:
        return wc.DEFAULT_MARGIN_PCT
    val = float(raw)
    if val < 0:
        raise ValueError(f"way_margin_pct отрицателен: {val}")
    return val


def _pairing_costs(solver, pairing_run_id: str,
                   run_id: str, components: list,
                   manifest: dict | None = None) -> dict:
    """Стоимости пейринга РЕШЕНИЯ прогона run_id под пакетом
    пейринг-прогона: полный мягкий счёт (справочно; верхнее поле
    score повкладового ЦЕЛИКОМ — рецепт решателя, сверен по коду
    службы: ручная сумма компонент разойдётся на новом ограничении)
    и ЗОННАЯ метрика Г-П3 (webapp.way_compare.zone_cost — словарь и
    формула одни у обеих сторон; веса пакета в обмене не ездят, обе
    стороны мерены литеральными весами под одним пакетом).

    manifest — манифест пакета ноги пейринга: пакет ревизии 1.14
    несёт словарь исполнителей зоны СЕКЦИЕЙ rule_maps.zone_executors
    — тогда зона мерится картой ПАКЕТА; без подсекции (и без
    манифеста) — легаси-словарь way_compare.ZONE_EXECUTORS. Кривая
    секция — отказ словами (WayCompareError)."""
    from webapp import rule_maps
    from webapp import way_compare as wc
    solved = solver.result(run_id)
    bd = solver.score_breakdown(pairing_run_id, solved)
    score = bd.get("score")
    if not score:
        raise ButtonStop(
            "остановка: повкладовый счёт без поля score",
            json.dumps(bd, ensure_ascii=False)[:400])
    executors = rule_maps.zone_executors(manifest or {},
                                         error=wc.WayCompareError)
    # Живой ответ службы несёт карту ограничений полем constraints
    # (сверено эмпирически 2026-08-25); byName — прежнее имя из
    # переписки, принимаем оба.
    zone = wc.zone_cost(bd.get("constraints") or bd.get("byName")
                        or {}, components, executors=executors)
    return {"full_soft": wc.cost_from_score(score),
            "score": score, "zone": zone,
            "source": (f"run/{pairing_run_id}/score-breakdown на "
                       f"решении {run_id} (литеральные веса, "
                       f"score={score})")}


def orchestrate_compare(run_dir: Path, solver, *,
                        margin_pct: float | None = None,
                        pairing_mode: str | None = None,
                        spent_limit_s: int = 60,
                        sleep=time.sleep, poll_s: float = 5.0,
                        timeout_s: float = 3600.0,
                        log=None, **seams) -> dict:
    """Г-П2: сравнение способов (директива владельца 2026-08-24).

    Три прогона на ОДНОМ пакете (байты пакета пейринга): (1) пейринг;
    (2) классика — ростеринг НА утверждённом пейринге (pairingRunId);
    (3) сквозной — объединённый ростеринг. Вердикт — формулой
    webapp.way_compare (одна у обеих сторон): сквозной побеждает,
    только если его ростеринг лучше классического И его пейринг не
    хуже порога (настройка, умолчание 5%). Недопустимые счета — вход
    сравнения (лексикографика формулы), остановки швов — остановка
    сравнения словами. Выдача — run_dir/button-compare/."""
    from webapp import way_compare as wc
    run_dir = Path(run_dir)
    out = run_dir / "button-compare"
    out.mkdir(exist_ok=True)

    def _deliver(verdict: str, **fields) -> dict:
        delivery = {"verdict": verdict, **fields}
        (out / "delivery.json").write_text(
            json.dumps(delivery, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return delivery

    if margin_pct is None:
        margin_pct = margin_pct_setting()
    legs: dict[str, dict] = {}

    def _leg(name: str, **kw) -> dict | None:
        if log:
            log("сравнение", "идёт", f"нога «{name}»")
        d = orchestrate(run_dir, solver, spent_limit_s=spent_limit_s,
                        sleep=sleep, poll_s=poll_s,
                        timeout_s=timeout_s, log=log, **seams, **kw)
        legs[name] = {"verdict": d.get("verdict"),
                      "solver_run_id": (d.get("schedule") or {})
                      .get("solver_run_id"),
                      "score": (d.get("schedule") or {}).get("score"),
                      "certified": d.get("certified"),
                      # A8: трёхчастный результат ноги — в карточке
                      # сравнения, а не только флаг.
                      "result": d.get("result"),
                      "trace": d.get("trace")}
        return d

    # Нога 1: пейринг. Якорь классики — только «допустимо»:
    # неутверждаемый пейринг сравнивать не с чем.
    p = _leg("пейринг", task="pairing", mode=pairing_mode)
    # Г-П4: пейринг строит ortools-построитель; якорный вердикт ноги —
    # «пейринг построен» (Timefold-пейринг исключён).
    if p.get("verdict") != "пейринг построен":
        return _deliver("сравнение остановлено: пейринг — "
                        + str(p.get("verdict")),
                        detail=str(p.get("detail") or "")[:800],
                        legs=legs, margin_pct=margin_pct)
    p_run = p["schedule"]["solver_run_id"]

    # Ноги 2 и 3: «недопустимо» — законный вход сравнения (жёсткие
    # сравнивает формула), остановка шва — остановка сравнения.
    c = _leg("классический ростеринг", task="rostering",
             pairing_run_id=p_run)
    if not _is_rostering_outcome(c.get("verdict")):
        return _deliver("сравнение остановлено: классический "
                        "ростеринг — " + str(c.get("verdict")),
                        detail=str(c.get("detail") or "")[:800],
                        legs=legs, margin_pct=margin_pct)
    t = _leg("сквозной ростеринг", task="rostering",
             package_from="button-pairing", out_dir="button-through")
    if not _is_rostering_outcome(t.get("verdict")):
        return _deliver("сравнение остановлено: сквозной ростеринг — "
                        + str(t.get("verdict")),
                        detail=str(t.get("detail") or "")[:800],
                        legs=legs, margin_pct=margin_pct)

    # Один пакет у всех трёх ног — иначе сравнение обман.
    fps = {(d.get("trace") or {}).get("package_fingerprint")
           for d in legs.values()}
    if len(fps) != 1 or None in fps:
        return _deliver("сравнение остановлено: пакеты ног разошлись",
                        detail=f"отпечатки: {sorted(map(str, fps))}",
                        legs=legs, margin_pct=margin_pct)

    try:
        # Г-П4 (решение сторон 2026-08-26): судья сравнения — ТОЛЬКО
        # счёт финального ростеринга. Стоимость пейринга — метрика
        # построителя из сертификата каталога, СПРАВОЧНО: решатель
        # каталог не судит (число из его правил было бы подделкой
        # происхождения), а метрики движков несравнимы порогом.
        builder_cert = None
        cat_path = run_dir / "button-pairing" / "builder-catalog.json"
        if cat_path.is_file():
            _cat = json.loads(cat_path.read_text(encoding="utf-8"))
            builder_cert = {
                "ip_objective": _cat.get("ip_objective"),
                "ip_status": _cat.get("ip_status"),
                "config_sig": _cat.get("config_sig"),
                "verdict": (_cat.get("coverage") or {}).get("verdict"),
                "chains": len(_cat.get("selected") or [])}
            chk = (run_dir / "button-pairing" /
                   "pairing_builder_check_button-"
                   f"{run_dir.name}.json")
            if chk.is_file():
                _rep = json.loads(chk.read_text(encoding="utf-8"))
                builder_cert["total_cost"] = _rep.get("total_cost")
                builder_cert["person_nights"] = \
                    _rep.get("person_nights")
        judged_by = ("счёт финального ростеринга; стоимость пейринга "
                     "— метрика построителя, справочно (Г-П4)")
        result = wc.compare(
            classical_rostering_score=c["schedule"]["score"],
            through_rostering_score=t["schedule"]["score"],
            classical_pairing_cost=0.0,
            through_pairing_cost=0.0,
            margin_pct=margin_pct)
    except (ButtonStop, wc.WayCompareError) as e:
        verdict = getattr(e, "verdict",
                          "остановка: сравнение не посчиталось")
        return _deliver("сравнение остановлено: " + str(verdict),
                        detail=str(getattr(e, "detail", e))[:800],
                        legs=legs, margin_pct=margin_pct)

    lines = [f"# Сравнение способов ({run_dir.name})", "",
             f"Победитель: **{result['winner']}** "
             f"(судья: {judged_by})", ""]
    lines += [f"- {r}" for r in result["reasons"]]
    if builder_cert:
        lines += ["", "Сертификат каталога построителя (справочно):",
                  f"- вердикт покрытия: {builder_cert['verdict']}, "
                  f"цепочек {builder_cert['chains']}, стоимость "
                  f"{builder_cert.get('total_cost')}"]
    (out / "compare.md").write_text("\n".join(lines) + "\n",
                                    encoding="utf-8")
    return _deliver(
        f"сравнение: победитель — {result['winner']}",
        **result,
        pairing_judged_by=judged_by,
        pairing_builder_certificate=builder_cert,
        legs=legs,
        artifacts={"compare": "button-compare/delivery.json",
                   "compare_md": "button-compare/compare.md",
                   "pairing": "button-pairing/delivery.json",
                   "classical": "button-on-pairing/delivery.json",
                   "through": "button-through/delivery.json"})
