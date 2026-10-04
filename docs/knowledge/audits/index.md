---
type: Knowledge Section
title: "Аудиты и укрепление"
description: Раунды внешнего аудита R1–R10, пакеты исправлений и реестр инвариантов.
tags: [audits, hardening, invariants]
timestamp: 2026-08-07T12:00:00
---

# Аудиты и укрепление

Главный реестр гарантий: [INVARIANTS.md](../../../final-report/INVARIANTS.md)
(охраняется судьёй `tests/test_invariant_registry.py`: точный перечень,
ворота-функции, AST-цензусы, мета-мутации).

## Узлы

- [Пакет A R10 — достоверность числа](r10-paket-a.md)
- [Пакет закрытия P0-02/P0-04 — манифест + пакет C](r10-paket-bc.md)

## Документы раундов (final-report/)

- Аудит R10: `AUDIT-legion-Test-20260731-R10-RU.md` (вердикт NO-GO,
  дифференциальный оракул как требование).
- Ответы: `AUDIT-R7..R9-OTVET-*-RU.md`,
  `OTVET-R10-PAKET-A-20260807-RU.md`,
  `OTVET-R10-PAKET-BC-20260807-RU.md`.

Память процесса: узлы `audit-*` в каталоге памяти ассистента (уроки
раундов, что опровергалось и почему).
