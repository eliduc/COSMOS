"""Отпечаток задачи пейринга — закрепление и правила его изменения.

Отпечаток (`_gerad_colgen.space_fingerprint`) — это идентичность задачи:
те же легы под другими правилами суть другая задача. От него зависит,
годится ли продвинутый каталог, а через хеш правил в манифесте — считаются
ли актуальными все опубликованные ростеры.

До этого файла отпечаток не был закреплён НИЧЕМ. Существующий тест правил
проверял лишь, что хеш ДВИГАЕТСЯ при смене min_sit_min; что он при этом
остаётся РАВЕН исторически продвинутым значениям — не проверял никто.
Поэтому случайное включение нового ключа в отпечаток прошло бы все тесты
и обнаружилось бы только на боевом стенде, обесценив каталоги.

Здесь закрепляются:
  * абсолютные отпечатки всех семи эталонных наборов (из продвинутых
    каталогов на диске — они и есть исторический факт);
  * состав разрешающего списка: добавление ключа в идентичность задачи
    обязано быть осознанным действием, а не побочным эффектом.
"""
import json

import pytest

import _gerad_colgen as gc
import rules_config
from webapp import registry

# Продвинутые каталоги живут в каталоге данных (вынос результатов
# из-под контроля версий): реестр отвечает за путь, тест не строит его
# сам — прежний REPO_ROOT/final-report/best после выноса всегда пуст, и
# все семь закреплений молча пропускались.
BEST_DIR = registry.BEST_DIR

# Исторические отпечатки продвинутых каталогов. Значения НЕ выведены из
# кода — они прочитаны с диска и вписаны сюда, чтобы тест ловил именно
# расхождение кода с историей, а не сверял код сам с собой.
PINNED = {
    "inst1": "be9146648bbdfb638f3ad4f1f69634b4bfd4da8f0adbd11d3f952eb7e9e6e4ac",
    "inst2": "3f785424d7166a03e156e7b72bb9e580e1d8a67f1d8f0a0a596e62b2aa06f97e",
    "inst3": "19ebccef1e77d44f94e3ee52caeb22b9fe050d8216c8d1d0c27d108ceb5a6cc6",
    "inst4": "98c8eb670c9869c33ad72c73e71a460a6494692c214315edac9de95ccf8c9d6b",
    "inst5": "c966ff3e6c6dfbd16464c2bd2734df11accf3be49eaf8893a199bb91597df3ac",
    "inst6": "6cf06b25ac07cd4d74109eb48d27a850f57bc029722bba6c9520e39df6ad7c2e",
    "inst7": "520ec52c3a556b5012ddc3842fb8c6196a3b80a2ad2818945cc7834382dd62c4",
}


@pytest.mark.parametrize("label", sorted(PINNED))
def test_promoted_catalog_fingerprint_is_unchanged(label):
    """Отпечаток продвинутого каталога на диске равен закреплённому.

    Расхождение означает, что каталог был перезаписан прогоном под
    другими правилами — и сравнивать его с прежними результатами больше
    нельзя."""
    path = BEST_DIR / f"meta_{label}.json"
    if not path.exists():
        pytest.skip(f"нет продвинутого каталога {path.name}")
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta.get("fingerprint") == PINNED[label], (
        f"{label}: отпечаток продвинутого каталога разошёлся с историей")


def test_space_keys_are_an_explicit_allow_list():
    """Состав идентичности задачи задан явно и совпадает с ожидаемым.

    Раньше фильтр был ЗАПРЕЩАЮЩИЙ («всё, кроме comp_*»), и новый ключ
    правил попадал в отпечаток молча. Тест фиксирует ровно шесть ключей:
    седьмой обязан появиться здесь вместе с решением, что он меняет
    идентичность задачи и обесценивает каталоги."""
    assert gc.TIER1_SPACE_KEYS == frozenset({
        "min_sit_min", "max_landings_per_duty", "max_duty_span_min",
        "min_rest_min", "max_monthly_block_min", "max_pairing_span_min"})


def test_composition_rules_stay_out_of_the_problem_identity():
    """Правила состава экипажа — про рассадку этапа 2, а не про то, какие
    пейринги существуют. Их попадание в отпечаток обесценило бы каталоги
    при простом включении правила."""
    assert not (gc.TIER1_SPACE_KEYS & rules_config.COMPOSITION_KEYS)


def test_every_space_key_exists_in_the_vocabulary():
    """Опечатка в разрешающем списке молча выкинула бы правило из
    идентичности задачи — самый тихий способ её сломать."""
    unknown = gc.TIER1_SPACE_KEYS - set(rules_config.DEFAULTS)
    assert not unknown, f"в разрешающем списке ключи вне словаря: {unknown}"


def test_allow_list_selects_exactly_what_the_old_deny_list_did():
    """Замена запрещающего фильтра разрешающим обязана быть
    ХЕШ-НЕЙТРАЛЬНОЙ на текущем словаре — иначе она сама обесценила бы
    все каталоги ровно тем способом, от которого защищает."""
    eff = rules_config.effective()
    deny = {k: v for k, v in eff.items() if not k.startswith("comp_")}
    allow = {k: v for k, v in eff.items() if k in gc.TIER1_SPACE_KEYS}
    assert deny == allow


def test_a_new_non_space_rule_key_does_not_move_the_fingerprint(monkeypatch):
    """Ключевое свойство: правило, НЕ входящее в идентичность задачи, не
    двигает отпечаток. Это и есть условие, при котором ярус 2 можно будет
    добавить, не обесценив ни одного продвинутого каталога."""
    class _Leg:
        fn, dep_min, arr_min, dep, arr, ac = "F1", 0, 120, "AAA", "BBB", "A320"

    dump = {"bases": ["B1"], "pricer": "duty", "max_legs": 5,
            "ip_model": "cover"}
    before = gc.space_fingerprint([_Leg()], dump)

    base = dict(rules_config.effective())
    base["tier2_example_rule"] = 42           # ключ вне разрешающего списка
    monkeypatch.setattr(rules_config, "effective", lambda: dict(base))
    after = gc.space_fingerprint([_Leg()], dump)

    assert before == after, "новый ключ правил просочился в идентичность задачи"
