"""Реестр точек вызова ИИ — LLM call-site registry (2026-07-17).

Product artifact for customer audits (strict mode, §8-bis of the
data-formalization document): EVERY place COSMOS talks to a language model,
with the routing mechanism that decides local-vs-cloud and the class of
data the prompt carries. Admins see it at GET /api/llm-registry (вкладка
«ИИ-контур»); auditors get one authoritative answer instead of grepping.

The registry cannot silently rot: tests/test_llm_call_registry.py scans
webapp/*.py for the egress primitives (llm_backend.complete / claude_cli)
and FAILS when a module calls a model without being declared here — adding
a new call site forces the author to document its contour story.

Routing vocabulary (`routing`):
  llm_scope      — resolved per dataset via llm_backend.resolve_backend();
                   local-only datasets can never reach the cloud.
  contour-guard  — change_requests._agent_backend(): forced to the local
                   model when this checkout IS the prod/stage contour
                   (fail-closed on unresolvable contour dirs).
  external-only  — refuses to run at all on the internal contour; where it
                   runs, the backend is the cloud CLI on the isolated Test
                   checkout.
"""
from __future__ import annotations

ROUTINGS = ("llm_scope", "contour-guard", "external-only")

# Keep `module` equal to the source file name under webapp/ — the guard
# test matches on it. `data`/`purpose` are Russian: they are shown verbatim
# in the admin UI and quoted in audit replies.
CALL_SITES: list[dict] = [
    {
        "id": "package-runs.certifier",
        "module": "package_runs.py",
        "function": "cloud_certifier_channel",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "тексты правил модели пакета постановки ВМП и решение "
                "(каталог цепочек или ростер) — компиляция и прогон "
                "машинных проверок сертификата решения; допуск моделей "
                "берётся из признака kit.llm_scope пакета (нет признака "
                "— только локальная модель), при «any» — канал площадки "
                "(CLI подписки с явной моделью либо API) с моделью из "
                "настройки CREW_CERTIFIER_MODEL",
        "purpose": "сертификация решений пейринга и ростеринга по "
                   "пакету постановки на странице «Пакет постановки» "
                   "(переезд расчёта в COSMOS, 04.09.2026)",
    },
    {
        "id": "source-check.compile",
        "module": "source_check_compile.py",
        "function": "compile_checks",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "тексты клауз замороженных исходных документов "
                "прогона (целиком, с якорями) — независимая "
                "компиляция машинных проверок решения; ВМП и "
                "промпты формализации в запрос не попадают",
        "purpose": "шестая проверка: соответствие решения "
                   "первоисточнику (поручение владельца 2026-08-31); "
                   "канал читателя объявляется вызывающим явно "
                   "(cli — CLI подписки точной моделью, ключ API канал "
                   "не меняет; api — Messages API; local — локальная "
                   "модель; без канала — прежний путь complete по "
                   "контуру), наблюдённый канал пишется в протокол и "
                   "снимки (S06 единой версии, 06.09.2026)",
    },
    {
        "id": "import.mapping-plan",
        "module": "data_import.py",
        "function": "propose_mapping",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "образец файлов: настоящие заголовки + до 30 строк; "
                "значения маскируются для облака (и локально при "
                "CREW_IMPORT_MASK_SAMPLES=1)",
        "purpose": "план маппинга колонок в канонические поля",
    },
    {
        "id": "advisor.consult",
        "module": "solver_advisor.py",
        "function": "consult",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "обезличенный дескриптор задачи (размеры, признаки); "
                "без сырых строк и персональных данных",
        "purpose": "рекомендация выбора солвера перед запуском",
    },
    {
        "id": "advisor.build-skill",
        "module": "solver_advisor.py",
        "function": "build_solver_skill",
        "routing": "external-only",
        "backends": "cloud",
        "data": "только публичные бенчмарки и литература",
        "purpose": "сборка базы знаний по выбору солверов (Тест-контур)",
    },
    {
        "id": "help.ask-ai",
        "module": "help_center.py",
        "function": "ask",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "вопрос пользователя + публичный обзор приложения + "
                "метаданные наборов (без сырых данных); правило «хоть один "
                "local-only набор → локальная модель», отказ в сторону "
                "безопасности",
        "purpose": "Ask AI — вопрос-ответ раздела «Помощь»",
    },
    {
        "id": "cr.feedback-agent",
        "module": "change_requests.py",
        "function": "run_feedback_agent",
        "routing": "contour-guard",
        "backends": "local|cloud",
        "data": "тексты предложений пользователей (до 600 символов каждое) "
                "— на внутреннем контуре принудительно локальная модель",
        "purpose": "агент «Отзывы»: кластеризация предложений в CR",
    },
    {
        "id": "cr.regulatory-agent",
        "module": "change_requests.py",
        "function": "run_regulatory_agent",
        "routing": "contour-guard",
        "backends": "local|cloud",
        "data": "только код юрисдикции (страна инстанса); данных "
                "заказчика нет",
        "purpose": "агент «Регуляторика»: дайджест изменений норм",
    },
    {
        "id": "cr.uiux-agent",
        "module": "change_requests.py",
        "function": "run_uiux_agent",
        "routing": "contour-guard",
        "backends": "local|cloud",
        "data": "только публичный обзор приложения",
        "purpose": "агент «Идеи»: предложения по интерфейсу",
    },
    {
        "id": "cr.implementer",
        "module": "change_requests.py",
        "function": "implement_cr",
        "routing": "external-only",
        "backends": "cloud",
        "data": "CR + исходные тексты предложений Тест-контура; на "
                "внутреннем контуре запуск запрещён "
                "(_repo_is_internal_contour, громкий отказ)",
        "purpose": "Реализатор: пишет код CR в ветке изолированного Теста",
    },
    {
        "id": "rules.extractor",
        "module": "rule_extractor.py",
        "function": "extract",
        "routing": "llm_scope",
        "backends": "local|cloud",
        "data": "фрагмент нормативного документа: public (открытые нормы) "
                "может идти в облако, confidential (коллективный договор, "
                "внутренние положения) — только локальная модель "
                "(ScopeViolation при запросе облака)",
        "purpose": "извлечение кандидатов правил в Rule Pack (черновики, "
                   "утверждает человек)",
    },
    {
        "id": "audit.process",
        "module": "audit_inbox.py",
        "function": "process",
        "routing": "contour-guard",
        "backends": "local|cloud",
        "data": "текст отчёта стороннего аудитора (не содержимое "
                "инстансов)",
        "purpose": "разбор внешнего аудита в CR agent=audit",
    },
    {
        "id": "advisor.impact",
        "module": "solver_advisor.py",
        "function": "score_finding",
        "routing": "external-only",
        "backends": "cloud",
        "data": "только публичные данные: заголовок/аннотация/ссылка "
                "публикации (находка агента «Алгоритмы») + база знаний "
                "советника (skill.md, без данных инстансов)",
        "purpose": "оценка эффективности публикации для нашего "
                "пейринга/ростеринга (сортировка вкладки «Алгоритмы»)",
    },
    {
        "id": "evolution.research",
        "module": "evolution_runner.py",
        "function": "run_nightly / run_phase",
        "routing": "external-only",
        "backends": "cloud",
        "data": "только публичные данные (публикации, бенчмарки); гард "
                "_assert_external_contour",
        "purpose": "агент «Алгоритмы»: ночное исследование и фазы "
                "эволюции движка",
    },
]

# The one module allowed to contain the raw egress primitives without a
# CALL_SITES entry: it IS the plumbing everything above goes through.
PLUMBING_MODULES = ("llm_backend.py",)


def declared_modules() -> set[str]:
    return {c["module"] for c in CALL_SITES}


def as_payload() -> dict:
    """API shape for the admin UI."""
    return {"items": CALL_SITES, "routings": list(ROUTINGS)}
