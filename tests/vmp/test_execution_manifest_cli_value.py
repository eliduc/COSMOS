# -*- coding: utf-8 -*-
"""Манифест исполнения: значение ключа запуска судится по ПОСЛЕДНЕМУ
вхождению — так же, как его читает построитель (argparse).

Живой случай 09.09.2026: пакет Н0-B нёс критерий (обнуляет веса штрафов —
«чистота цели», Г-П4 п. 7.1) и мягкое правило «штраф за ночёвку вне базы»
весом 10; строка запуска законно несла «--w-night 0 … --w-night 10»
(tests/vmp/test_pairing_input.py: «последний --w-night побеждает»), а
манифест читал первое вхождение и останавливал расчёт словами «две правды
о цене», хотя построителю уходило 10."""
from vmp import execution_manifest as em


def test_последнее_вхождение_ключа_как_у_построителя():
    args = ["--w-span-day", "100", "--w-night", "0", "--w-dh", "0",
            "--budget-s", "240", "--w-night", "10", "--no-presolve"]
    assert em._cli_value(args, "--w-night") == "10"
    assert em._cli_value(args, "--w-span-day") == "100"
    assert em._cli_value(args, "--w-dh") == "0"


def test_флаг_без_значения_и_отсутствие_ключа():
    args = ["--w-night", "10", "--no-presolve", "--w-night"]
    assert em._cli_value(args, "--no-presolve") is True
    assert em._cli_value(args, "--w-night") is True      # последний — флаг
    assert em._cli_value(args, "--w-chain") is None
    assert em._cli_value([], "--w-chain") is None
