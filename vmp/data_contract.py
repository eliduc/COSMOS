"""Public workbook sheet names shared by the package reader and G2.

These names are fixed product vocabulary, never inferred from a customer
document, a model proposal, or a run. Explicit source addresses still need
anonymisation even when their sheet name is public vocabulary.
"""

SHEET_FLIGHTS = "Рейсы"
SHEET_CREW = "Экипажи"
SHEET_QUALS = "Допуски"
SHEET_VACATIONS = "Отпуска"
SHEET_DUTIES = "Смены"

STANDARD_DATA_SHEETS = frozenset((
    SHEET_FLIGHTS, SHEET_CREW, SHEET_QUALS, SHEET_VACATIONS, SHEET_DUTIES,
))
