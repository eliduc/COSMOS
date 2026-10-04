"""Усечения поиска как наблюдаемый факт, а не как догадка по длине списка.

Класс дефекта (внешние аудиты R6→R9, ось «доказанная нижняя граница»):
признак усечения выводился ИЗ ДЛИНЫ уже усечённого списка
(`if len(opts) >= k: ...`). Такая проверка не отличает «ровно k вариантов
и существует» от «k+1 существовал и был выброшен», а в шести местах из
восьми её просто забывали написать. Каждый раунд аудита закрывал один
забытый вызов и находил соседний.

Здесь класс закрывается сменой формы. Правила две:

1. **Усечение записывается в момент НАБЛЮДЕНИЯ отброшенного варианта.**
   Отбор берёт `k` элементов, но подсматривает `(k+1)`-й; факт усечения
   равен «(k+1)-й существовал», а не «набрано k». Ложные срабатывания
   (предел равен числу вариантов) исчезают вместе с ложными пропусками.

2. **Приёмник передаётся явно и обязателен.** Вызывающий не может
   «забыть» записать усечение, потому что не он его записывает: отбор
   принимает приёмник и пишет туда сам. Забыть можно только не вызвать
   отбор — а это видно переписью по исходным текстам.

Приёмники ИМЕНОВАНЫ и РАЗДЕЛЬНЫ. Усечение пула кандидатов не обязано
подавлять границу (пул — эвристический разогрев, граница доказывается
последним проходом ценообразования), а усечение самого прохода — обязано.
Слить их в один приёмник значило бы никогда не публиковать границу;
потерять различие — публиковать её незаслуженно.

Образец дисциплины, к которому это приводит построитель, уже работает в
соседней ветке: `_gerad_colgen` объявляет усечённый проход неполным через
исключение, и достичь предела, не пометив проход, там невозможно.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

# Приёмник, подавляющий публикацию границы.
SCOPE_PRICING = "pricing"
# Пространство смен: усечение до ценообразования, тоже подавляет.
SCOPE_DUTY = "duty"
# Разогрев пула кандидатов: НЕ подавляет (граница доказывается проходом).
SCOPE_POOL = "pool"
# Зонд существования (есть ли хоть один вариант): усечение бессмысленно.
SCOPE_PROBE = "probe"

_SUPPRESSING = frozenset({SCOPE_PRICING, SCOPE_DUTY})


class Truncations:
    """Именованный приёмник усечений одной области.

    `record` вызывается ТОЛЬКО когда отброшенный вариант действительно
    наблюдался. Поле `observed_at_least` хранит минимально доказанное
    число вариантов (`limit + 1`) — этого достаточно, чтобы отличить
    «усечено» от «в точности исчерпано», и не требует досчитывать
    остаток, который и был отброшен ради скорости.
    """

    __slots__ = ("scope", "_hits")

    def __init__(self, scope: str) -> None:
        self.scope = scope
        # name -> (limit, observed_at_least): предел, при котором усечение
        # наблюдалось, обязан ехать рядом с наблюдением (аудит R10, тип
        # PricingResult требует {scope, name, observed, limit}).
        self._hits: dict[str, tuple[int, int]] = {}

    def record(self, name: str, limit: int, observed_at_least: int) -> None:
        prev = self._hits.get(name)
        if prev is None:
            self._hits[name] = (limit, observed_at_least)
        else:
            self._hits[name] = (min(prev[0], limit),
                                max(prev[1], observed_at_least))

    @property
    def suppresses_bound(self) -> bool:
        """Подавляет ли эта область публикацию нижней границы."""
        return bool(self._hits) and self.scope in _SUPPRESSING

    def names(self) -> list[str]:
        return sorted(self._hits)

    def as_rows(self) -> list[dict]:
        """Машинно-проверяемое перечисление: что усечено и чем доказано."""
        return [{"name": n, "limit": self._hits[n][0],
                 "observed_at_least": self._hits[n][1],
                 "scope": self.scope} for n in sorted(self._hits)]

    def __bool__(self) -> bool:
        return bool(self._hits)

    def __repr__(self) -> str:            # pragma: no cover - диагностика
        return f"Truncations({self.scope!r}, {self._hits!r})"


def take(source: Iterable, k: int, *, sink: Truncations | None,
         name: str) -> list:
    """Взять не более `k` элементов, ПОДСМОТРЕВ `(k+1)`-й.

    Порядок и отбор сохраняются в точности: возвращаются те же первые `k`
    элементов, что и при срезе. Отличие только в том, что источник
    опрашивается на один элемент дальше — и, если этот элемент есть,
    усечение записывается как НАБЛЮДЁННЫЙ факт.

    `sink=None` допустим лишь там, где усечение не имеет смысла (зонд
    существования). Он обязан быть указан явно: умолчания нет намеренно,
    иначе «забыл приёмник» снова стало бы бесшумным.
    """
    if k < 0:
        raise ValueError(f"предел не может быть отрицательным: {k}")
    it: Iterator = iter(source)
    out: list = []
    for item in it:
        if len(out) == k:
            # (k+1)-й вариант существует — усечение доказано наблюдением
            if sink is not None:
                sink.record(name, k, k + 1)
            return out
        out.append(item)
    return out


@dataclass(frozen=True)
class PricingResult:
    """Единый результат прохода ценообразования (аудит R10, пакет A).

    Тройка `(columns, exhausted, caps)` объявлена запрещённой формой:
    булево «исчерпано» без структурированного перечня усечений позволяло
    объявить полноту, не предъявив доказательства, — три мутации аудита
    R10 получали «pricing исчерпан, caps пусты» при существующей
    улучшающей колонке. Здесь полнота ВЫЧИСЛЯЕТСЯ из перечня:
    `certifies_bound()` истинно только когда нет ни одного усечения в
    подавляющей области и проход не был оборван по времени. Поля
    «exhausted» у типа НЕТ намеренно — записать полноту отдельно от
    доказательства невозможно по построению.

    `problem_fingerprint` привязывает результат к постановке: граница,
    предъявленная без отпечатка задачи, неотличима от границы чужого
    прогона (требование аудита о сверке fingerprint у потребителей).
    """

    columns: tuple
    sinks: tuple                      # tuple[Truncations, ...]
    problem_fingerprint: str
    duals_fingerprint: str | None = None
    deadline_hit: bool = False

    def certifies_bound(self) -> bool:
        """Проход имеет право сертифицировать нижнюю границу."""
        return (not self.deadline_hit
                and not any(s.suppresses_bound for s in self.sinks))

    def truncation_rows(self) -> list[dict]:
        """Машинно-проверяемый перечень усечений всех областей."""
        rows = [r for s in self.sinks for r in s.as_rows()]
        if self.deadline_hit:
            rows.append({"name": "deadline", "limit": -1,
                         "observed_at_least": -1, "scope": SCOPE_PRICING})
        return rows

    def truncation_names(self) -> list[str]:
        return sorted({r["name"] for r in self.truncation_rows()})


def merge_names(*sinks: Truncations) -> list[str]:
    """Сводный перечень имён усечений по нескольким областям."""
    seen: set[str] = set()
    for s in sinks:
        seen.update(s.names())
    return sorted(seen)


def suppressed_by(*sinks: Truncations) -> list[str]:
    """Имена областей, подавляющих границу (для честного сообщения)."""
    return sorted({s.scope for s in sinks if s.suppresses_bound})


def floor_bound(value: float) -> int:
    """Округление нижней границы ВНИЗ.

    Граница снизу, округлённая к ближайшему, перестаёт быть границей: она
    способна оказаться выше истинного оптимума на половину единицы и тем
    самым «доказать» недостижимое. Отдельная функция нужна, чтобы это
    решение было названо и проверялось тестом, а не жило в выражении.
    """
    import math
    return int(math.floor(value))
