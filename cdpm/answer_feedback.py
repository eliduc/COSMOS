"""Delivery of processing advice, separate from the airline's words."""

DATA_EXCLUSION_PENDING = (
    "Выбрано исключение записей из планирования, а не исключение правила. "
    "Автоматическое исключение записей этим обработчиком пока не поддержано. "
    "Подготовьте входные данные без выбранных для исключения записей и "
    "повторите проверку данных. До подтверждения правило и блокировка сохранены; "
    "менять норму или переводить правило в OUT нельзя."
)


def lines(q: dict, *, doc: dict | None = None) -> list[str]:
    answer = q.get("answer") or {}
    if not isinstance(answer, dict):
        return []
    result = []
    if answer.get("closed_by") == "code":
        reason = answer.get("close_reason") or answer.get("note")
        # A keyed answer already displays its note next to the key.
        if reason and (not answer.get("key") or reason != answer.get("note")):
            result.append("Причина снятия: " + str(reason))
    if answer.get("status") == "не разобран":
        result.append(f"Неприменённый ответ сохранён: «{answer.get('raw', '')}».")
        if answer.get("note"):
            result.append("Ответ не разобран: " + str(answer["note"]))
    if answer.get("status") == "ожидает обработчика" and answer.get("processing_note"):
        result.append("Ответ не применён: " + str(answer["processing_note"]))
    from cdpm import model as m
    if answer.get("status") == m.REVISION_CONFLICT_STATUS:
        conflicts = answer.get("conflicts")
        if isinstance(conflicts, list):
            result += ["Причина конфликта: " + str(reason) for reason in conflicts]
        if answer.get("processing_note"):
            result.append(str(answer["processing_note"]))
    if answer.get("status") == m.PARTIAL_ANSWER_STATUS:
        result += ["Ответ применён частично.",
                   f"Прежний ответ сохранён: «{answer.get('raw', '')}»."]
        if doc is not None:
            from cdpm import answers
            pending = [r["id"] for r in answers._affected_rules(doc, q)]
            pending_ids = set(pending)
            processed = [r["id"] for r in answers._affected_rules(
                doc, q, include_processed=True) if r["id"] not in pending_ids]
            result += ["Обработанные строки: " + (", ".join(processed) or "нет") + ".",
                       "Остались без результата обработчика: "
                       + (", ".join(pending) or "не определены — проверьте модель") + "."]
        result.append(
            "Повторно заполнять поле ответа не нужно. Обработчик должен продолжить "
            "обработку сохранённого ответа по оставшимся строкам. Если ответ нужно "
            "изменить или дополнить, откройте пересмотр этого вопроса.")
    unchanged = answer.get("worker_unchanged")
    if isinstance(unchanged, dict):
        for rid, basis in unchanged.items():
            result.append(f"Результат обработчика: {rid} — признано неизменным "
                          f"по ответу: «{basis}».")
    return result
