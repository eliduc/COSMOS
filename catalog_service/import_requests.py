# -*- coding: utf-8 -*-
"""Приём очередей заявок с площадок — `python -m catalog_service
import-requests --inbox <папка> [--reply]`.

Почтового ящика у службы НЕТ (решение 0.12 проекта шага 6): на Test,
Stage, Prod и у заказчика служба стоит в режиме просмотра и не пишет на
диск ни байта. Заявки, рождённые там, копятся в ОЧЕРЕДЯХ ПРОДУКТОВ под
корнем данных (`CDPM_CATALOG_OUTBOX`, `CREW_CATALOG_OUTBOX`); на рабочее
место владельца они переносятся копией папок по чек-листу, а принимает
их этот инструмент.

Правило, которое здесь держится: единственный писатель каталога — сама
служба. Инструмент НИЧЕГО не пишет под `catalog/` — он ходит в службу по
HTTP тем же машинным каналом, что и продукты, и раскладывает рядом с
файлами очереди файлы-ответы `<имя>.reply.json` в той форме, которую
CDPM уже читает (`accepted`, `state`, `problems[].message`).

Коды возврата: 0 — все файлы приняты; 1 — были отказы (ответы записаны);
2 — служба не поднята либо папки очереди нет (словами, ничего не
сделано).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from template_catalog.receipts import REPEAT_NOTE, validate_receipt
from template_catalog.requests import draft_canon

from .identity import PRODUCT_HEADER, TOKEN_HEADER
from .settings import DEFAULT_PORT

#: Кто подписывает файл-ответ. Строка контракта: CDPM читает её как
#: имя канала доставки, а не как роль каталога.
IMPORTER = "инструмент импорта COSMOS (catalog_service import-requests)"
DEFAULT_API_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
REPLY_SUFFIX = ".reply.json"


def reply_path(path: Path) -> Path:
    """Имя файла-ответа: `<имя без .json>.reply.json` рядом с заявкой."""
    return path.parent / (path.stem + REPLY_SUFFIX)


def queue_files(inbox: Path) -> list[Path]:
    """Файлы очереди: `*.json`, кроме самих ответов. Порядок — по имени,
    чтобы повторный прогон читался так же, как первый."""
    return sorted(p for p in Path(inbox).glob("*.json")
                  if p.is_file() and not p.name.endswith(REPLY_SUFFIX))


def http_post(url: str, headers: dict, body: bytes) -> tuple:
    """Один POST службе. Отказ службы — это ОТВЕТ (код и тело), а не
    исключение: тело отказа несёт слова и проблемы, ради которых всё и
    затевалось. Исключением остаётся только «не дозвонились».

    Третьим членом возвращается тип содержимого: закон квитанции требует
    `application/json` (П0 §4.1 п. «г»), и страница входа, отданная со
    статусом 200, приёмом не является. Подставной `post` несёт тот же
    заголовок: отсутствие третьего члена означает неизвестный тип,
    а не освобождает ответ от проверки квитанции."""
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            # `errors="replace"` и на успешной ветке: тело не в UTF-8 —
            # это «не квитанция» словами, а не трассировка. Строгий
            # разбор здесь ронял бы инструмент на ответе чужой службы,
            # который он и должен был отвергнуть.
            raw = resp.read().decode("utf-8", errors="replace")
            return (int(resp.status), _json_or_words(raw),
                    resp.headers.get("Content-Type"))
    except urllib.error.HTTPError as e:                     # 4xx и 5xx
        raw = e.read().decode("utf-8", errors="replace")
        return int(e.code), _json_or_words(raw), e.headers.get("Content-Type")


def _json_or_words(raw: str) -> dict:
    try:
        got = json.loads(raw)
    except ValueError:
        return {"detail": {"code": "bad-form",
                           "words": f"ответ службы не JSON: {raw[:200]}"}}
    return got if isinstance(got, dict) else {"items": got}


def _nullstr(value):
    if value is None:
        return None
    return str(value).strip() or None


def _decision_number(value):
    """Тот же закон числовой версии, что у CDPM: bool/NaN/inf не версия.

    Целые не переводятся во float: соседние версии после 2**53 различны.
    Отсутствующая версия вложения 0.5.0 не становится версией 0."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    text = _nullstr(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def nested_request_problems(body) -> list[str]:
    """Связь необязательного request с квитанцией — закон CDPM K07C-F1.

    Писатель receipt() выводит оба носителя из одной записи; смешанное
    тело не доказывает её решение. Служба не импортирует продукт CDPM:
    равенство этих клиентских проверок закреплено интегральной матрицей.
    Форму и внешнюю идентичность по-прежнему судит validate_receipt()."""
    if not isinstance(body, dict) or "request" not in body:
        return []
    nested = body.get("request")
    if not isinstance(nested, dict):
        return [f"вложенная запись заявки не объект JSON: "
                f"{type(nested).__name__}"]
    problems: list[str] = []
    for field in ("request_id", "section", "record_id"):
        if nested.get(field) != body.get(field):
            problems.append(f"{field} вложенной записи {nested.get(field)!r} не "
                            f"равен {body.get(field)!r} самой квитанции")
    if "draft" in nested:
        canon = draft_canon(nested.get("draft"))
        if body.get("canon") != canon:
            problems.append(f"канон черновика вложенной записи {canon!r} не "
                            f"равен канону квитанции {body.get('canon')!r}")
    if nested.get("state") != body.get("state"):
        problems.append(f"состояние вложенной записи {nested.get('state')!r} не "
                        f"равно состоянию квитанции {body.get('state')!r}")
    outer = body.get("decision")
    inner = nested.get("decision")
    outer = outer if isinstance(outer, dict) else {}
    inner = inner if isinstance(inner, dict) else {}
    for field, label in (("by", "автор"), ("words", "слова"), ("at", "время")):
        if _nullstr(inner.get(field)) != _nullstr(outer.get(field)):
            problems.append(f"{label} решения вложенной записи: {inner.get(field)!r}; "
                            f"в квитанции: {outer.get(field)!r} — значения не совпадают")
    outer_version = _decision_number(outer.get("version"))
    inner_version = _decision_number(inner.get("version"))
    if (outer_version is not None and inner_version is not None
            and outer_version != inner_version):
        problems.append(f"версия решения вложенной записи {inner_version!r} "
                        f"не равна версии решения квитанции {outer_version!r}")
    return problems


def receipt_problems(status: int, body, sent=None, content_type=None
                     ) -> list[str]:
    """Чем ответ службы НЕ является квитанцией (пусто — является).

    Закон один с клиентом CDPM (П0 §4.1, K07) — и буквально один, не
    «такой же»: тело судит общая функция читателя каталога
    `template_catalog.receipts.validate_receipt(body, sent=…)` (схема
    `receipt/1`, закрытый перечень состояний, идентичность заявки), а
    здесь добавляется только ТРАНСПОРТ, о котором тело знать не может:
    код ответа, точный тип содержимого и согласие признака повтора с
    кодом, а также связь необязательного request с внешней квитанцией.
    Пока перечней было два, они и разошлись: слова руководства обещали
    проверку схемой, а код её не делал.

    `sent` — отправленная заявка (запись очереди): по ней сверяются
    номер, раздел, запись и канон черновика. Любой иной 2xx, страница
    входа, пустое тело, чужой номер — исход «неизвестен»: файл очереди
    остаётся в очереди, повтор идёт ТЕМ ЖЕ номером, нового номера не
    порождается (номер содержательный).

    Перечень возвращается СЛОВАМИ, а не булевым: площадка обязана
    прочитать в файле-ответе, чего именно не хватило, — «не принята» без
    причины отправляет человека читать логи службы."""
    беды: list[str] = []
    if status not in (201, 200):
        беды.append(f"код ответа {status} (квитанция — 201 либо 200)")
    if content_type is None:
        беды.append("тип содержимого не назван, а квитанция — application/json")
    elif str(content_type).split(";")[0].strip().lower() != "application/json":
        беды.append(f"тип содержимого {content_type!r}, а не application/json")
    беды.extend(validate_receipt(body, sent=sent))
    if not isinstance(body, dict):
        return беды
    беды.extend(nested_request_problems(body))
    # Согласие кода и тела: 201 — «записано сейчас», 200 — «доставлено
    # ранее» (сторона COSMOS §5.4). 201 со словами «уже подана» и 200 без
    # них клиент истолковать не может: приняли его заявку сейчас или она
    # лежала там с прошлого раза — вопрос не праздный (K08).
    if status == 200 and body.get("note") != REPEAT_NOTE:
        беды.append(f"200 без слов «{REPEAT_NOTE}» — это не повторная выдача "
                    f"той же квитанции")
    if status == 201 and (body.get("note") == REPEAT_NOTE
                          or body.get("repeat") is True):
        беды.append("201 с признаком повторной выдачи — код и тело "
                    "противоречат друг другу")
    return беды


def _words_of(status: int, body: dict) -> tuple[str, list]:
    """Слова и проблемы ответа — из `detail` отказа либо из тела приёма.

    Слова приёма берутся у СЛУЖБЫ (`words` тела), а своя строка остаётся
    запасной: пересказ ответа своими словами однажды разошёлся бы с тем,
    что служба сказала на самом деле."""
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    if detail:
        return (f"{detail.get('code', status)}: "
                f"{detail.get('words', '')}".strip(),
                list(detail.get("problems") or ()))
    said = str(body.get("words") or "").strip()
    if said:
        return said, []
    note = body.get("note")
    return (f"принята: {body.get('state', '')}"
            + (f" ({note})" if note else ""), [])


def one(path: Path, *, api_url: str, token: str | None,
        post=None) -> dict:
    """Разобрать один файл очереди и отдать его службе.

    Продукт-податель берётся ИЗ ФАЙЛА (`submitted_by`), а не из ключа
    запуска: одна папка переноса может нести очереди обоих продуктов, и
    подписывать чужую заявку своим именем инструмент не вправе."""
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    base = {"request_id": None, "catalog_request_id": None, "accepted": False,
            "state": None, "problems": [], "words": "", "at": at,
            "by": IMPORTER, "file": path.name}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {**base, "words": f"файл очереди не читается: {e}"}
    if not isinstance(doc, dict):
        return {**base, "words": "файл очереди не заявка: ожидался объект"}
    base["request_id"] = doc.get("request_id")
    product = str(doc.get("submitted_by") or "")
    if not product:
        return {**base, "words": "в файле нет submitted_by — некому "
                                 "подписать заявку"}
    headers = {"Content-Type": "application/json", PRODUCT_HEADER: product}
    if token:
        headers[TOKEN_HEADER] = token
    send = post or http_post
    status, body, *rest = send(api_url.rstrip("/") + "/api/requests", headers,
                               json.dumps(doc, ensure_ascii=False)
                               .encode("utf-8"))
    words, problems = _words_of(status, body)
    # Доставка засчитывается ТОЛЬКО по квитанции — тот же закон, что у
    # клиента CDPM (П0 §4.1). Прежнее «любой 200/201 — принята» считало
    # приёмом страницу входа площадки и ответ с чужим номером.
    беды = receipt_problems(status, body, doc, rest[0] if rest else None)
    accepted = not беды
    # Слова «не квитанция» — на ЛЮБОЙ успешный код, а не только на
    # 200/201: 204 без тела и 202 «принято к обработке» — такой же исход
    # «неизвестен», и площадке нужно прочитать то же самое: заявка
    # осталась в очереди, повтор идёт тем же номером. У 4xx/5xx слова
    # берутся из отказа службы — они говорят больше.
    if not accepted and 200 <= int(status) < 300:
        words = (f"ответ службы не является квитанцией (HTTP {status}): "
                 + "; ".join(беды)
                 + " — заявка остаётся в очереди, повтор тем же номером")
    return {**base, "accepted": accepted,
            "catalog_request_id": body.get("request_id") if accepted else None,
            "state": body.get("state") if accepted else None,
            "receipt": dict(body) if accepted else None,
            "problems": problems, "words": words, "status": status}


def run(inbox, *, api_url: str = DEFAULT_API_URL, token: str | None = None,
        reply: bool = False, post=None) -> dict:
    """Пройти папку очереди. Файл с готовым ответом пропускается: повтор
    прогона — не повторная подача.

    `post` берётся ПРИ ВЫЗОВЕ, а не значением по умолчанию: значение по
    умолчанию замерло бы на функции, известной при объявлении модуля, и
    подмена `http_post` (в проверке, в обёртке) молча ничего бы не
    меняла."""
    folder = Path(inbox)
    if not folder.is_dir():
        raise FileNotFoundError(f"папки очереди нет: {folder}")
    rows, skipped = [], []
    for path in queue_files(folder):
        if reply_path(path).is_file():
            skipped.append(path.name)
            continue
        row = one(path, api_url=api_url, token=token, post=post)
        rows.append(row)
        if reply:
            reply_path(path).write_text(
                json.dumps(row, ensure_ascii=False, sort_keys=True, indent=1)
                + "\n", encoding="utf-8")
    return {"seen": len(rows) + len(skipped), "sent": len(rows),
            "accepted": sum(1 for r in rows if r["accepted"]),
            "refused": sum(1 for r in rows if not r["accepted"]),
            "skipped": skipped, "rows": rows}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m catalog_service import-requests",
        description="Приём очереди заявок продукта на рабочем месте "
                    "владельца: файлы папки уходят в службу машинным "
                    "каналом, ответы кладутся рядом.")
    parser.add_argument("--inbox", required=True,
                        help="папка очереди продукта (CDPM_CATALOG_OUTBOX, "
                             "CREW_CATALOG_OUTBOX)")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"адрес службы (умолчание {DEFAULT_API_URL})")
    parser.add_argument("--token", default=None,
                        help="токен машинного канала (CATALOG_MACHINE_TOKEN)")
    parser.add_argument("--reply", action="store_true",
                        help="положить рядом файлы-ответы <имя>.reply.json")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # печать кириллицы
    import os
    args = parse_args(argv)
    token = args.token or os.environ.get("CATALOG_MACHINE_TOKEN")
    try:
        got = run(args.inbox, api_url=args.api_url, token=token,
                  reply=args.reply)
    except FileNotFoundError as e:
        print(f"приём заявок не начат: {e}")
        return 2
    except (urllib.error.URLError, OSError) as e:
        print(f"приём заявок не начат: служба каталога не отвечает по "
              f"{args.api_url} ({e}) — поднимите её "
              f"(python -m catalog_service --actor owner) и повторите")
        return 2
    for row in got["rows"]:
        mark = "принята" if row["accepted"] else "отказ"
        print(f"{row['file']}: {mark} — {row['words']}"
              + (f" [{row['catalog_request_id']}]"
                 if row["catalog_request_id"] else ""))
        for problem in row["problems"]:
            if isinstance(problem, dict):
                print(f"    {problem.get('where')}: {problem.get('message')}")
    if got["skipped"]:
        print(f"пропущено с готовым ответом: {len(got['skipped'])}")
    print(f"файлов рассмотрено: {got['seen']}, отдано службе: {got['sent']}, "
          f"принято: {got['accepted']}, отказано: {got['refused']}")
    return 1 if got["refused"] else 0


if __name__ == "__main__":                           # pragma: no cover
    sys.exit(main())
