// Клиент серверной части CDPM (FastAPI на том же хосте).
// Ошибки поднимаются исключением с текстом detail — вызывающий
// показывает их тостом, молча не глотает.

export const RECORD_WRITE_WARNING_EVENT = 'cdpm-record-write-warning'

async function fetchWithRecordWarning(input: RequestInfo | URL,
                                      init?: RequestInit): Promise<Response> {
  const response = await globalThis.fetch(input, init)
  // A successful action and a stale derived record are different outcomes.
  // Never turn this warning into an exception that encourages action replay.
  if (response.ok && response.headers.get('X-CDPM-Record-Warning') ===
      'record-refresh-busy' && typeof window !== 'undefined') {
    const runs = (response.headers.get('X-CDPM-Record-Runs') || '')
      .split(',').filter(run => /^run-\d{4}$/.test(run))
    window.dispatchEvent(new CustomEvent(RECORD_WRITE_WARNING_EVENT,
                                        { detail: { runs } }))
  }
  return response
}

export async function refreshRunRecordOnly(run: string): Promise<{ ok: boolean }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/record/refresh`, { method: 'POST' }))
}

export interface EffectiveParameter {
  name: string
  value: unknown
  quote: unknown
  from: 'template_fit' | 'params'
  unit: unknown
  relation: unknown
  condition: unknown
}

export interface ParameterSnapshot {
  run: string
  snapshot_at: string
  model_sha256: string
  model_revision: unknown
  landing_migrations?: {
    input_fingerprint: string; output_fingerprint: string;
    catalog: { version: string; fingerprint: string } | null;
    items: { index: number; rule: string | null;
             state: 'confirmed' | 'required' | 'unconfirmed'; words: string[];
             changes: { param: string; fields: string[];
                        source: { kind: 'code'; note: string } }[] }[]
  }[]
  family_review?: {
    judged: boolean
    words?: string
    catalog?: { version?: string; fingerprint?: string }
    families: { family: string; verdict: string; contract_state: string;
                transitions: string[]; missing: string[]; pending: string[];
                problems: string[] }[]
  }
  rules: { id: string; title: unknown; kind: unknown; zone: unknown;
           blockers?: string[];
           params: EffectiveParameter[] }[]
}

export async function getEffectiveParameters(run: string): Promise<ParameterSnapshot> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/effective-parameters`))
}

export interface RunSummary {
  title?: string | null
  run: string
  state: string
  customer?: string | null
  data_state?: string | null
  readiness?: { state: string; label: string; matrix?: ReadinessMatrix;
                vmp_state?: string; vmp_open_questions?: number
                vmp_questions_total?: number } | null
  files: string[]
  // Список прогонов (поручение владельца 06.09): дата создания для
  // сортировки и место в ручном порядке (order.json на площадке).
  created_at?: string | null
  position?: number
  // Паспорт прогона кратко (слово владельца 07.09: «прогон должен
  // хранить также модели и солверы»): модели ключевых ролей, решатели, код.
  passport_summary?: PassportSummary | null
  // Повтор прогона (проект повтора, Р2–Р3): потомок → источник,
  // источник → потомки.
  replay_of?: ReplayOf | null
  replays?: ReplayPointer[]
}

export interface ReplayOf {
  mode: 'replay-journal' | 'replay-fresh' | string
  source_run: string
  verdict?: string | null
}

export interface ReplayPointer {
  run: string
  mode: string
  verdict?: string | null
}

export interface ReplayAvailability {
  available: boolean
  reason_code?: string | null
  reason_words?: string | null
  // «Заново»: оценка для подтверждения — вызовов моделей и длительность
  // стадий у источника (стоимость — как у полного прогона).
  estimate?: { calls?: number | null; duration_s?: number | null
               stages?: string[]; reconstructed?: boolean } | null
}

export interface ReplayPassport {
  mode: string
  source_run: string
  source_passport_sha256?: string | null
  actions_upto?: number | null
  started_at?: string | null
  finished_at?: string | null
  verdict?: string | null
  report?: string | null
  reconstructed?: boolean
}

export interface ReplayReport {
  schema: string
  mode: string
  source_run: string
  run: string
  verdict: string
  environment: { differs: { what: string; source?: unknown
                            replay?: unknown }[] }
  actions: { replayed: number; stopped: boolean
             skipped: { n?: number; action?: string; code?: string }[] }
  calls: { source: number; served_from_journal: number; fresh: number
           unconsumed: number; errors_source?: number; errors_replay?: number }
  artifacts: { path: string; same: boolean; source_sha256?: string
               replay_sha256?: string; normalized?: boolean; code?: string }[]
  first_divergence: Record<string, unknown> | null
  outcomes: { name: string; source: unknown; replay: unknown; same: boolean
              source_file?: string }[]
  equivalence: { verdict?: string | null; problems?: string[]
                 problems_total?: number; baseline_problems?: number
                 error?: string } | null
  note?: string | null
  reconstructed?: boolean
  finished_at?: string
}

export interface PassportSummary {
  models: Record<string, string>
  solvers: string[]
  code?: string | null
  catalog?: string | null
}

// Паспорт прогона — рецепт воспроизведения (принцип владельца 07.09):
// окружение при рождении, входы по отпечаткам, модели по стадиям, журналы.
export interface RunPassport {
  created?: {
    code?: { commit?: string | null; short?: string | null }
    spec_version?: string | null
    python?: string
    packages?: Record<string, string | null>
    catalog?: { version?: string | null; fingerprint?: string | null }
  } | null
  created_at?: string | null
  inputs: {
    documents: { sid?: string; file?: string; sha256?: string
                 clauses?: number }[]
    data: { sid?: string; file?: string; sha256?: string; rows?: number }[]
    answers?: { revision?: string; file?: string | null
                sha256?: string | null } | null
  }
  stages: { role: string; role_title: string; klass?: string | null
            stage?: string | null; provider?: string | null
            model?: string | null; channel?: string | null
            effort?: string | null; temperature?: number | null
            config_source?: string | null; code?: string | null }[]
  journals: { extract_cache: number; exchange_rounds: number
              answers_revisions: number }
}

export interface FreezeCounters {
  files: number
  clauses: number
  anchors: number
  projections: number
  originals: number
}

export interface ExtractSummary {
  clauses_fed: number
  candidates: number
  rules: number
  questions: number
  questions_per_part: Record<string, number>
  rejected: number
  merged_questions: number
}

export interface CoverageReport {
  clauses: number
  covered_by_rows: number
  not_rule: number
  missing: { sid: string; anchor: string; text: string }[]
}

export interface PrincipleRow {
  title: string
  ok: boolean
  [key: string]: unknown
}

export interface Principles {
  P1: PrincipleRow & { open_contradictions: number
                       contradictions_total: number; param_conflicts: number }
  P2: PrincipleRow & { fit: Record<string, number>; b_pending: number
                       open_forks: number; forks_total: number }
  P3: PrincipleRow & { open_statement_questions: number
                       soft_without_weight: string[]; objectives: number
                       objective_zones: string[]
                       excluded_until_data: string[] }
  // A11: те же факты, что у замка выпуска пакета (единый расчёт
  // cdpm.coverage.bijection_facts); не судившееся успехом не считается.
  // A12: строка судит ФАКТЫ ПОКРЫТИЯ, а не смысловую полноту —
  // `meaning_completeness` несёт слова предела (что не доказано и чем
  // закрывается); признака «доказана» у смысловой полноты нет.
  P4: PrincipleRow & { rows_without_source: number
                       coverage: { clauses: number; covered_by_rows: number
                                   not_rule: number; missing: number } | null
                       basis?: string
                       meaning_completeness?: string
                       dangling_anchors?: number
                       anchors_judged?: boolean
                       content_judged?: boolean
                       covered_partially?: number
                       values_without_addressee?: number
                       normative_without_rows?: number
                       problems?: string[] }
}

export interface RunStatus {
  run: string
  state: string
  title?: string | null
  // СМТ-1: проверка непротиворечивости свода.
  smt_state?: string
  smt_report?: { status: string; verdict?: string; core?: string[]
                 n_rules?: number; n_translated?: number
                 // A01: полнота перевода отдельно от вердикта.
                 n_untranslatable?: number; untranslated?: string[]
                 // A01-F2: записи ответа переводчика ВНЕ ФОРМЫ —
                 // позиция и вид, без выдуманного имени правила; слова
                 // о них уже в `status`, перечень — для диагностики.
                 malformed?: { field: string; index: number | null
                               kind: string; text: string }[] }
  files?: string[]
  customer?: string
  scope?: string
  /** Признак допуска моделей у комплекта (решение владельца 03.09):
   *  эффективный контур, его источник и чтение словами. */
  llm_scope?: {
    effective: 'any' | 'local-only'
    source: string
    reading: string
    data_class?: string | null
    llm_scope?: string | null
    basis?: string | null
    env_force_ignored?: boolean
  }
  freeze_counters?: FreezeCounters
  freeze_problems?: string[]
  extract_summary?: ExtractSummary
  extract_counters?: Record<string, number>
  extract_problems?: string[]
  answers_summary?: AnswersSummary | null
  answers_problems?: string[] | null
  readiness?: Readiness | null
  // Четыре принципа ВМП (владелец 2026-09-02): покрытие клауз
  // документов правил (замок биекции) и блок 4П по машинным фактам.
  coverage?: CoverageReport | null
  principles?: Principles | null
  third_party_file?: string | null
  third_party_claims?: number | null
  mode2_summary?: {
    claims: number
    checked: number
    by_verdict: Record<string, number>
    part4_added: number
    skipped: number
  } | null
  mode2_problems?: string[] | null
  // Прогресс идущей стадии (чанки) — директива владельца 2026-08-20.
  progress?: { stage: string; done: number; total: number
               rules: number } | null
  // СКВ-1: положение сквозного процесса.
  full_cycle?: { cycle: number; max_megacycles: number
                 phase: string } | null
  error?: string
  // Пробелы каталога шаблонов (шаг 6): побочная линия составления и
  // подачи заявок и напоминание счётчиками для блока «Итог».
  catalog_state?: string | null
  catalog_requests?: { candidates?: number; submitted?: number
                       held?: number; pending?: number
                       document_class?: string; error?: string } | null
  // Паспорт прогона — рецепт воспроизведения (07.09.2026).
  passport?: RunPassport | null
  // Повтор прогона (Р2–Р3): доступность кнопок, паспорт повтора у
  // потомка, ход водителя.
  replay?: {
    journal: ReplayAvailability
    fresh: ReplayAvailability
    of: ReplayPassport | null
    progress: { step: number; of: number; action?: string
                n?: number } | null
  } | null
}

/** Слова отказа из ответа сервера. Отдельной функцией, потому что
 *  честное «нет» (404 у записи, у узла дерева) — не исключение, а
 *  ответ со словами: вызывающий показывает их подписью, а не тостом
 *  ошибки. */
async function detailWords(resp: Response): Promise<string> {
  let detail = `${resp.status} ${resp.statusText}`
  try {
    const body = await resp.json()
    const d = body.detail
    // Отказ словами в ОБЪЕКТЕ `detail` — так отдаёт единый обработчик
    // «каталог шаблонов недоступен» (503, `detail.code ===
    // 'catalog-unavailable'`, слова причины в `detail.error`). Без
    // разбора объекта пользователь видел бы «[object Object]» вместо
    // причины — а причина эта одна на все ручки панели.
    if (d && typeof d === 'object' && d.error) detail = String(d.error)
    else if (d) detail = String(d)
    else if (body.error) detail = String(body.error)
  } catch { /* тело не JSON */ }
  return detail
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(await detailWords(resp))
  return resp.json() as Promise<T>
}

export async function listRuns(): Promise<RunSummary[]> {
  return unwrap(await fetchWithRecordWarning('/api/runs'))
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

// Ручной порядок списка прогонов (стрелки «вверх/вниз» — поручение
// владельца 06.09): хранится на площадке рядом с прогонами.
export async function getRunsOrder(): Promise<{ order: string[] }> {
  return unwrap(await fetchWithRecordWarning('/api/runs/order'))
}

export async function putRunsOrder(order: string[]):
    Promise<{ order: string[] }> {
  return unwrap(await fetchWithRecordWarning('/api/runs/order', {
    method: 'PUT', headers: JSON_HEADERS,
    body: JSON.stringify({ order }),
  }))
}

// «Удалить» отмеченные прогоны = перенос их папок в архив площадки
// (cdpm-runs-archive), обратимо; прогон с идущей стадией сервер не
// трогает и называет его в отказе.
export async function archiveRuns(runs: string[]):
    Promise<{ archived: string[]; archive_dir: string; left: number }> {
  return unwrap(await fetchWithRecordWarning('/api/runs/archive', {
    method: 'POST', headers: JSON_HEADERS,
    body: JSON.stringify({ runs }),
  }))
}

// cloudImages — явное разрешение облака для изображений (ПИ-1):
// локальной зрительной модели нет — без него сервер честно откажет.
/** Признак допуска моделей у комплекта (решение владельца 03.09):
 *  класс данных обязателен по смыслу — без него прогон идёт
 *  local-only; явный допуск и основание — для проприетарного класса
 *  с согласием авиакомпании. */
export interface KitScope {
  dataClass: '' | 'synthetic' | 'open' | 'proprietary'
  llmScope?: '' | 'any' | 'local-only'
  basis?: string
}

/** Объявление прогона формой «Новый прогон» (гейт шага 2 проекта
 *  «Запись о прогоне»): цель, кто запускает, откуда берутся ответы на
 *  анкеты, план этапов и этап остановки. Поле необязательное — прогон,
 *  созданный без него (тесты, прежние вызовы), получает объявление по
 *  умолчанию: цель «рабочий», актор «панель CDPM». */
export interface RunDeclaration {
  purpose?: string
  createdBy?: string
  answerPolicy?: string
  plannedStages?: string[]
  stopAfter?: string
}

export async function createRun(files: File[],
                                cloudImages = false,
                                customer = '',
                                kit: KitScope = { dataClass: '' },
                                declaration: RunDeclaration = {}):
    Promise<RunStatus> {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  const q = new URLSearchParams()
  if (cloudImages) q.set('cloud_images', 'true')
  if (customer.trim()) q.set('customer', customer.trim())
  if (kit.dataClass) q.set('data_class', kit.dataClass)
  if (kit.llmScope) q.set('llm_scope', kit.llmScope)
  if (kit.basis && kit.basis.trim()) q.set('llm_scope_basis', kit.basis.trim())
  // Объявление прогона: слова гейта. Пустые поля не шлются — сервер
  // подставит умолчания и рассудит среду сам.
  if (declaration.purpose) q.set('purpose', declaration.purpose)
  if (declaration.createdBy && declaration.createdBy.trim()) {
    q.set('created_by', declaration.createdBy.trim())
  }
  if (declaration.answerPolicy) q.set('answer_policy', declaration.answerPolicy)
  if (declaration.plannedStages && declaration.plannedStages.length) {
    q.set('planned_stages', declaration.plannedStages.join(','))
  }
  if (declaration.stopAfter) q.set('stop_after', declaration.stopAfter)
  const qs = q.toString()
  const url = qs ? `/api/runs?${qs}` : '/api/runs'
  return unwrap(await fetchWithRecordWarning(url, { method: 'POST', body: fd }))
}

// ЦФ-1: матрица корректности 3×2 (режимы × задачи).
export type ReadinessMatrix = Record<string, Record<string, {
  ready?: boolean; state?: string; note?: string
  reasons?: Record<string, number>; objectives?: string[]
}>>

// Состояние линии правил (СГ-1): а/б/в + причины по задачам.
export interface Readiness {
  state: 'а' | 'б' | 'в'
  label: string
  pairing_ready: boolean
  rostering_ready: boolean
  pairing_open: Record<string, number>
  rostering_open: Record<string, number>
  matrix?: ReadinessMatrix
  // Термин владельца (демо, 2026-08-29): ПВМП = модель до закрытия
  // вопросов анкеты; ВМП — открытых вопросов нет.
  vmp_state?: 'ПВМП' | 'ВМП'
  vmp_open_questions?: number
  // Все вопросы действующей анкеты (включая рождённые линией данных).
  vmp_questions_total?: number
}

export interface AnswersSummary {
  revision: number
  received: number
  applied: number
  awaiting: number
  unparsed: number
  conflicted?: number
  worker_applied?: number
  worker_rejected?: number
}

export async function runStatus(run: string): Promise<RunStatus> {
  return unwrap(await fetchWithRecordWarning(`/api/runs/${encodeURIComponent(run)}`))
}

// Приёмник ответов анкеты (интерактивный цикл, ИР-1): заполненная
// Excel-книга анкеты.
export async function uploadAnswers(run: string, file: File):
    Promise<{ run: string; state: string; revision: number
              received: number }> {
  const form = new FormData()
  form.append('file', file)
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/answers`,
    { method: 'POST', body: form }))
}

// Режим 2 (РМ2): файл формализации третьей стороны + запуск сверки.
export async function uploadThirdParty(run: string, file: File):
    Promise<{ run: string; file: string; claims: number }> {
  const form = new FormData()
  form.append('file', file)
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/third-party`,
    { method: 'POST', body: form }))
}

export async function startMode2(run: string):
    Promise<{ run: string; state: string; class: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/mode2`,
    { method: 'POST' }))
}

export async function startExtract(run: string, customer: string):
    Promise<RunStatus> {
  const q = new URLSearchParams({ customer })
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/extract?${q}`,
    { method: 'POST' }))
}

/** Адрес артефакта прогона (открывается в новой вкладке). */
// Человеческие формулировки машинных вердиктов цикла (замечание
// владельца 2026-08-21: «стагнация» зелёным читалась как успех).
// Машинные значения в данных не меняются — только показ.
export function humanVerdict(v: string): string {
  return v === 'стагнация' ? 'замечания не убывают'
    : v === 'лимит' ? 'исчерпан лимит обменов'
    : v === 'успех' ? 'чисто'
    // A07 (12.09.2026): метрики чисты, но в реестре живёт
    // блокирующая/критическая находка — не успех.
    : v === 'не закрыто' ? 'не закрыто: живые блокирующие находки реестра'
    : v
}

export function humanState(s: string): string {
  return s
    .replace('аудит-цикл: стагнация',
             'аудит-цикл остановлен: замечания не убывают')
    .replace('аудит-цикл: лимит',
             'аудит-цикл: исчерпан лимит обменов')
}

// Исходы «остановился, но не чисто» — предупреждение, не успех и не
// ошибка: им жёлтый тон.
export function isStalledState(s: string): boolean {
  return s.includes('стагнация')
    || s.startsWith('аудит-цикл: лимит')
    || s.startsWith('аудит-цикл: не закрыто')   // A07: не зелёное «готово»
    || s.startsWith('сквозной цикл: лимит')
    || s.startsWith('аудит остановлен')
    // A09: гейт зелёный (форма отчёта цела), но споры остались без
    // решения — это не «готово», а «вернитесь к арбитражу».
    || s.startsWith('арбитраж готов: не разрешено')
    // §12.8: спор передан владельцу вопросом анкеты и ждёт его слова —
    // находка живая, «успех» цикла невозможен.
    || s.startsWith('арбитраж готов: ждут ответа владельца')
}

export function artifactUrl(run: string, alias: string): string {
  return `/api/runs/${encodeURIComponent(run)}/file/`
    + encodeURIComponent(alias)
}

// ── Аудит-цикл ──

export interface ExchangeRound {
  round: number
  weighty?: number[]
  E?: number
  remarks?: number
  answers?: number
}

export interface ExchangeVerdict {
  verdict: string
  rounds: number
  history: { round: number; weighty: number[]; E: number }[]
  deadlocks: { id: string; severity: string; key: unknown[] }[]
  final_version: string
}

export interface ExchangeState {
  run: string
  state?: string
  progress?: { round: number; phase: string; done: number
               total: number } | null
  config?: { auditor: string; worker: string; max_rounds: number }
  error?: string
  gate_problems?: string[]
  rounds: ExchangeRound[]
  registry: Record<string, number>
  verdict: ExchangeVerdict | null
  /** A07: сводка живых блокирующих/критических находок реестра. */
  registry_verdict?: { count: number
                       blockers: { id: string; severity: string
                                   state: string
                                   opened_round: number }[] } | null
  /** A07, миграция показа: записанный вердикт, пересуженный реестром
   *  при чтении («не закрыто (по реестру)» вместо старого «успех»). */
  verdict_by_registry?: string | null
}

export async function exchangeStatus(run: string):
    Promise<ExchangeState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/exchange`))
}

// K02/T02: fields and browser request paths share bounds.
// Python parity: tests/test_cdpm_k02_ui_limits.py::test_browser_bounds_equal_each_server_door.
export const EXCHANGE_ROUNDS_MIN = 1
export const EXCHANGE_ROUNDS_MAX = 20
export const FULL_CYCLES_MIN = 1
export const FULL_CYCLES_MAX = 10

export function validateCycleLimits(maxRounds: number, maxMegacycles?: number): void {
  if (!Number.isInteger(maxRounds)
      || maxRounds < EXCHANGE_ROUNDS_MIN || maxRounds > EXCHANGE_ROUNDS_MAX) {
    throw new Error(`лимит обменов — целое число от ${EXCHANGE_ROUNDS_MIN} до ${EXCHANGE_ROUNDS_MAX}`)
  }
  if (maxMegacycles !== undefined && (!Number.isInteger(maxMegacycles)
      || maxMegacycles < FULL_CYCLES_MIN || maxMegacycles > FULL_CYCLES_MAX)) {
    throw new Error(`лимит кругов — целое число от ${FULL_CYCLES_MIN} до ${FULL_CYCLES_MAX}`)
  }
}

export async function startExchange(
  run: string, auditor: string, worker: string,
  maxRounds: number,
  continueFromStop = false): Promise<{ run: string; state: string }> {
  validateCycleLimits(maxRounds)
  const q = new URLSearchParams({ auditor, worker,
                                  max_rounds: String(maxRounds) })
  // ОСТ-1: подтверждённое продолжение после «аудит остановлен: Б 0,
  // К 0, С N» — остановка снова сработает только на новом обмене.
  if (continueFromStop) q.set('continue_from_stop', 'true')
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/exchange?${q}`,
    { method: 'POST' }))
}

// СКВ-1: сквозной процесс цикл→арбитраж→новый цикл до Б=0 и К=0
// или лимита кругов.
export async function startFullCycle(
  run: string, auditor: string, worker: string,
  maxRounds: number, maxMegacycles: number):
    Promise<{ run: string; state: string }> {
  validateCycleLimits(maxRounds, maxMegacycles)
  const q = new URLSearchParams({ auditor, worker,
                                  max_rounds: String(maxRounds),
                                  max_megacycles:
                                    String(maxMegacycles) })
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/full-cycle?${q}`,
    { method: 'POST' }))
}

export function exchangeRoundUrl(run: string, round: number,
                                 alias: string): string {
  return `/api/runs/${encodeURIComponent(run)}/exchange/rounds/`
    + `${round}/${encodeURIComponent(alias)}`
}

export function exchangeSvodUrl(run: string, version: number): string {
  return `/api/runs/${encodeURIComponent(run)}/exchange/svod/${version}`
}

// Выход каждого обмена — пара «свод + анкета» (замечание владельца
// 2026-08-21); после арбитража — своя пара.
export function exchangeAnketaUrl(run: string,
                                  version: number): string {
  return `/api/runs/${encodeURIComponent(run)}`
    + `/exchange/anketa/${version}`
}

export function arbiterSvodUrl(run: string): string {
  return `/api/runs/${encodeURIComponent(run)}/exchange/arbiter-svod`
}

export function arbiterAnketaUrl(run: string): string {
  return `/api/runs/${encodeURIComponent(run)}/exchange/arbiter-anketa`
}

// Сброс прогона к исходному состоянию: файлы, заморозка и настройки
// моделей остаются, результаты стадий удаляются.
export async function resetRun(run: string): Promise<RunStatus> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/reset`, { method: 'POST' }))
}

// СМТ-1: машинная проверка непротиворечивости свода (Z3).
export async function startSmtVerify(
  run: string): Promise<{ run: string; smt_state: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/smt-verify`,
    { method: 'POST' }))
}

// ЭКС-1: скачивание итоговой модели в формате решателя.
export function exportUrl(run: string, fmt: string): string {
  return `/api/runs/${encodeURIComponent(run)}/export/`
    + encodeURIComponent(fmt)
}

// Канал ВМП+МГ: доставка пакета постановки приёмнику COSMOS и
// квитанция последней доставки (кнопка «Передать в COSMOS» в узле
// «Итог» — владелец на репетиции 05.09: «где онлайн-передача, я её
// не вижу»). Отметка та же, что пишет сервер в channel/delivery.json.
export interface DeliveryMark {
  run?: string
  sent: boolean
  configured?: boolean
  sha?: string
  attempts?: number
  detail?: string
  verdict?: string
  mg?: Record<string, { state?: string; launch_allowed?: boolean }>
  mismatch?: string[]
  already_accepted?: boolean
  at?: string
  // Прогон COSMOS, в который лёг пакет (07.09.2026); при repeat —
  // держатель уже принятого пакета; null — хранилище не заведено.
  cosmos_run?: string | null
  repeat?: boolean
}

export async function sendTaskPackage(run: string): Promise<DeliveryMark> {
  return unwrap(await fetchWithRecordWarning(exportUrl(run, 'task-package-send')))
}

// Повтор прогона (проект повтора, Р2–Р3): рождение потомка и водитель в
// фоне; 202 с номером потомка; 409 — причина словами и reason_code.
export async function startReplay(run: string, mode: 'journal' | 'fresh'):
    Promise<{ run: string; source_run: string; mode: string; state: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/replay`, {
      method: 'POST', headers: JSON_HEADERS,
      body: JSON.stringify({ mode }),
    }))
}

export async function getReplayReport(run: string):
    Promise<{ run: string; source_run: string; mode: string
              report: ReplayReport }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/replay-report`))
}

export async function getDelivery(run: string): Promise<DeliveryMark> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/channel`))
}

// ── Пробелы каталога шаблонов: заявки из посадок категории C ──
// (шаг 6 каталога, часть CDPM). Панель заявки не решает и в каталог
// не пишет: решения — на экране службы каталога, здесь только подача,
// отзыв, подтверждение удержанного черновика и доставка.

/** Строка свода с посадкой C: кандидат заявки либо причина отказа
 *  словами (служебная C, спор посадки, открытый вопрос анкеты). */
export interface CatalogCandidateRow {
  rule_id: string
  title: string
  statement: string
  kind?: string | null
  zone?: string | null
  gap: string
  params?: { name?: string; unit?: string | null }[]
  sources?: { sid?: string; anchor?: string }[]
  key: string
  request_id: string
  candidate: boolean
  /** Служебная C — след брака посадки: заявить можно только кнопкой
   *  строки с явным подтверждением. */
  service_c: boolean
  reason?: string | null
}

/** Заявка прогона от индекса `catalog-requests/index.json`: состояние
 *  составления (`composed`) и живое состояние словами (`state`). */
export interface CatalogRequestRow {
  rule_id: string
  request_id: string
  /** Id семьи-черновика заявки (`draft.id`, он же `record_id` записи):
   *  по нему заявка ищется в каталоге. Файла заявки нет — null. */
  record_id?: string | null
  key?: string | null
  composed?: string | null
  words?: string | null
  document_class?: string | null
  state: string
  /** K08 (`request-state/2`): независимые поля состояния ОБЩЕЙ заявки —
   *  решение службы видно одинаково из любого прогона, происхождение —
   *  подпись, не замена решения. */
  schema_version?: string
  canonical_request_id?: string | null
  /** Слово состояния службы (подана | принята | исполнитель заявлен |
   *  отклонена | есть); неизвестно — null. Происхождением не
   *  подменяется. */
  remote_state?: string | null
  /** Решение владельца: слова, версия (до каталога 0.6.0 — момент
   *  решения) и момент; решения нет — три null. */
  decision?: { words?: string | null; version?: string | number | null
               at?: string | null } | null
  /** Решение показано как ПОСЛЕДНЕЕ ИЗВЕСТНОЕ, а не подтверждённое
   *  прикладной квитанцией службы. */
  stale?: boolean
  /** ПРИЧИНА устаревания словами (закрытый перечень `STALE_REASONS`
   *  стороны прогона): квитанции у прогона нет | ответ службы старше
   *  решения | старшинство не доказано | копия каталога отстала |
   *  состояние из отказа службы. */
  stale_reason?: string | null
  /** Последнее известное решение ЗАПИСИ каталога, названное отдельно
   *  там, где показано не оно (копия каталога отстала от ответа
   *  службы): решение принадлежит состоянию и чужому не приписывается. */
  last_known?: { state?: string | null
                 decision?: { words?: string | null
                              version?: string | number | null
                              at?: string | null } | null } | null
  /** в очереди | доставлена | неизвестно | отказ | отозвана; null —
   *  черновик не подан (удержан или не составлен). */
  transport_state?: string | null
  /** Прогон-источник записи и подпись происхождения: этот прогон |
   *  другой прогон (тот же черновик) | другой прогон (иной черновик). */
  source_run?: string | null
  origin?: string | null
  submitted_at?: string | null
  held: boolean
  withdrawn: boolean
  /** Транспорт «доставлена»: прикладная квитанция службы ЭТОМУ прогону;
   *  своего ответа службы у прогона нет вовсе — запись каталога
   *  (доставил другой прогон). Отказ службы (в том числе
   *  `409 request-closed`) и ответ без прикладной квитанции записью
   *  каталога не перекрываются. */
  delivered: boolean
  /** Файл квитанции прогона (`receipt-file/2`, K07): исход заявки
   *  (`доставлена | неизвестен | отказ`) и вид случая по таблице §4.1
   *  (`таймаут`, `5xx`, `409 request-exists`, `409 request-closed`,
   *  `401/403`, `422/400`, `отказ службы`, `неизвестен`); `attempts` —
   *  ЧИСЛО попыток (в файле они журналом, здесь — счёт, как прежде);
   *  `last_checked_at` — момент последнего стука в службу. */
  delivery?: { at?: string | null; http?: number | null
               refusal?: string | null; words?: string | null
               attempts?: number | null; problems?: string[]
               outcome?: string | null; outcome_kind?: string | null
               last_checked_at?: string | null } | null
}

/** Канал подачи: очередь всегда, служба — когда настроена; без службы
 *  очередь и есть канал (её читает инструмент каталога). */
export interface CatalogChannel {
  service: boolean
  queue: string
  url?: string | null
  transitional: boolean
  /** Номера заявок прогона, которым доставка ещё нужна. */
  pending: string[]
  words: string
}

export interface CatalogRequestsState {
  run: string
  candidates: CatalogCandidateRow[]
  requests: CatalogRequestRow[]
  counts: { candidates: number; submitted: number; held: number
            pending: number }
  document_classes: string[]
  document_class: string
  channel: CatalogChannel
  catalog?: { version?: string | null; fingerprint?: string | null } | null
  auto: { enabled: boolean; skipped_reason?: string | null }
  last_action?: { trigger?: string | null; mode?: string | null
                  auto_setting?: string | null; at?: string | null
                  catalog_state?: string | null } | null
  /** Побочная линия прогона `catalog_state`. */
  line?: string | null
  /** Почему заявки не подаются вовсе (режим среза, каталог недоступен). */
  blocked?: string | null
}

export type CatalogRequestMode =
  'all' | 'one' | 'confirm' | 'withdraw' | 'resend' | 'deliver'

export interface CatalogRequestsBody {
  mode: CatalogRequestMode
  rule_ids?: string[]
  request_ids?: string[]
  document_class?: string
  confirm_service_c?: boolean
}

/** Ответ ручки: у фоновых режимов — занятая линия и строки действия, у
 *  доставки — счёт и квитанции по каждому номеру. */
export interface CatalogRequestsResult {
  run: string
  mode: string
  catalog_state?: string
  rows?: string[]
  delivered?: number
  refused?: number
  note?: string | null
  words?: string | null
  requests?: { request_id?: string; delivered?: boolean
               http?: number | null; refusal?: string | null
               words?: string | null; state?: string | null }[]
}

export async function getCatalogRequests(run: string):
    Promise<CatalogRequestsState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/catalog-requests`))
}

export async function postCatalogRequests(
    run: string, body: CatalogRequestsBody):
    Promise<CatalogRequestsResult> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/catalog-requests`,
    { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }))
}

// ── Политика обработки файлов данных ──

export type FilePolicy = 'local' | 'cloud-country' | 'cloud'

export interface FileConfig {
  processing: FilePolicy
  note: string
}

export interface FilesConfigState {
  run: string
  files: Record<string, FileConfig>
  policies: FilePolicy[]
}

export async function getFilesConfig(run: string):
    Promise<FilesConfigState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/files-config`))
}

export async function putFilesConfig(
    run: string, files: Record<string, FileConfig>):
    Promise<FilesConfigState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/files-config`,
    { method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }) }))
}

// ── Линия данных 3.3 ──

export interface DataState {
  run: string
  state?: string | null
  files: string[]
  class?: string | null
  freeze_counters?: { files: number; sheets: number; rows: number
                      originals: number } | null
  freeze_problems?: string[] | null
  summary?: {
    findings: number
    checks_run: number
    checks_skipped: number
    rows: number
    by_severity: Record<string, number>
    by_class: Record<string, number>
  } | null
  gate_problems?: string[] | null
  review_summary?: {
    candidates: number
    reviewed: number
    by_verdict: Record<string, number>
    skipped: number
  } | null
  review_problems?: string[] | null
  error?: string | null
}

export async function dataStatus(run: string): Promise<DataState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/data`))
}

// Сверка спорного (ДЛ-7): модель раскладывает находки отчёта 3.3 на
// три вердикта; решение по «неясно» — у владельца.
export async function startDataReview(run: string):
    Promise<{ run: string; state: string; class: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/data-review`,
    { method: 'POST' }))
}

export async function uploadData(run: string, files: File[]):
    Promise<{ run: string; state: string; problems: string[] }> {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/data`,
    { method: 'POST', body: form }))
}

// Удаление файла данных — каскадом по всем местам, где он живёт
// (data-src/, files-config.json, status.json, dataset + manifest);
// отчёт проверок и очищенные данные снимаются. 409 — пока идёт или
// стоит на паузе стадия линии данных.
export async function deleteDataFile(run: string, file: string):
    Promise<{ run: string; deleted: string; state: string | null
              files: string[]; problems: string[] }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/data/`
    + encodeURIComponent(file),
    { method: 'DELETE' }))
}

export async function startDataRun(run: string):
    Promise<{ run: string; state: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/data-run`,
    { method: 'POST' }))
}

// ── Арбитр тупиков аудит-цикла ──

export interface ArbiterSummary {
  deadlocks: number
  by_verdict: Record<string, number>
  enforced: number
  /** A3.5/A09: решения «аудитор прав», не исполненные кодом (вопрос владельцу). */
  unenforced?: number
  unenforced_deadlocks?: { deadlock: string; reason: string }[]
  /** A09 (контракт П0 §1.3/§12.2): споры без решения после повтора —
   *  дубли/пропуски/неизвестные идентификаторы; блокируют успех. */
  unresolved?: number
  unresolved_deadlocks?: { deadlock: string; reason: string }[]
  /** Лишние идентификаторы ответа арбитра и записи без формы: спорами
   *  цикла не являются и в `deadlocks` не считаются. */
  unresolved_foreign?: number
  /** §12.8: споры, переданные владельцу вопросом анкеты и ждущие его
   *  ответа — арбитру заново не предъявляются, но остаются живыми. */
  awaiting_owner?: number
  awaiting_owner_deadlocks?: {
    deadlock: string; question: string; reason: string }[]
  added_questions: number
  patch_rejections: number
}

export interface Deadlock {
  id: string
  state: string
  severity?: string
  defect_class?: string
  rule?: string | null
}

export interface ArbiterState {
  run: string
  state?: string
  deadlocks: Deadlock[]
  summary?: ArbiterSummary
  gate_problems?: string[]
  error?: string
  report?: {
    deadlocks: number
    verdicts: { deadlock: string; verdict: string | null;
                basis: string | null }[]
    by_verdict: Record<string, number>
    enforced: number
    added_questions: string[]
    patch_rejections: { answer: string; reasons: string[] }[]
    final_version: string
  } | null
}

export async function arbiterStatus(run: string):
    Promise<ArbiterState> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/arbiter`))
}

export async function startArbiter(run: string):
    Promise<{ run: string; state: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/arbiter`,
    { method: 'POST' }))
}

// Панель управления идущей стадией (ПС-1): пауза кооперативная —
// срабатывает на ближайшей контрольной точке между запросами к модели.
export async function runControl(
  run: string, command: 'pause' | 'resume' | 'stop',
): Promise<{ run: string; command: string }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/control`
    + `?command=${command}`,
    { method: 'POST' }))
}

// ── Провайдеры, настройки, конфигурация ролей ──

export interface ProviderInfo {
  name: string
  title: string
  configured: boolean
  channel: string
  key_masked: string | null
  key_hint: string | null
  cloud: boolean
  // Пакет трёх 2026-08-30, п.2: юрисдикция для слота «внутри страны».
  in_country?: boolean
  models: string[]
  params_supported: string[]
}

// Классы данных = слоты модели роли: блок обрабатывает каждый вход
// моделью слота, соответствующего признаку входа (политике файла).
export type ChannelClass = 'cloud' | 'local' | 'cloud-country'

export interface ChannelConfig {
  provider?: string
  model?: string
  temperature?: number
  max_tokens?: number
  // Усилие рассуждения слота: low | medium | high | max (МП-3).
  effort?: string
}

export interface AgentConfig {
  channels?: Partial<Record<ChannelClass, ChannelConfig>>
  thinking?: string
  instruction?: string
}

// Полная независимость выбора LLM (2.09): роли — все LLM-точки
// конвейера; перечень и родители наследования — с сервера.
export type AgentsConfig = Partial<Record<string, AgentConfig>>

export interface RoleInfo {
  role: string
  label: string
  purpose: string
  parent: string | null
}

export async function listRoles(): Promise<RoleInfo[]> {
  const got = await unwrap<{ roles: RoleInfo[] }>(
    await fetchWithRecordWarning('/api/agents/roles'))
  return got.roles
}

export interface EffectiveRole {
  role: string
  label: string
  effective: string | null
  inherited: boolean
  channels: Partial<Record<ChannelClass,
    { provider?: string | null; model?: string | null;
      effort?: string | null }>>
}

export async function getEffectiveAgents(run: string):
    Promise<{ run: string; roles: EffectiveRole[] }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/agents/effective`))
}

export async function listProviders(): Promise<ProviderInfo[]> {
  const got = await unwrap<{ providers: ProviderInfo[] }>(
    await fetchWithRecordWarning('/api/providers'))
  return got.providers
}

export interface SettingsState {
  keys_masked: Record<string, string | null>
  claude_api_key_masked: string | null
  /** Базовый адрес службы каталога шаблонов (CREW_CATALOG_URL);
   *  не настроена — null, и ссылок на каталог интерфейс не строит. */
  catalog_url?: string | null
  /** Отметка каталога, которым сейчас работает панель. */
  catalog?: { version: string; fingerprint: string } | null
  /** Номер версии продукта (CREW_PRODUCT_VERSION площадки, РВ-141);
   *  не задан — null, и подписи внизу меню нет. */
  product_version?: string | null
}

/** Адрес экрана службы каталога: `<база>/ui/#/…` (HashRouter службы).
 *  Без настройки — null: мёртвых ссылок интерфейс не показывает. */
export function catalogLink(base: string | null | undefined,
                            hash: string): string | null {
  if (!base) return null
  return `${base.replace(/\/+$/, '')}/ui/#${hash}`
}

export async function getSettings(): Promise<SettingsState> {
  return unwrap(await fetchWithRecordWarning('/api/settings'))
}

/** Проба каталога шаблонов, которым работает панель (`GET /api/catalog`,
 *  шаг 7): 200 всегда — и когда каталога нет. Секретов не несёт: признак,
 *  версия, отпечаток, путь, число предупреждений и слова. */
export interface CatalogProbe {
  ok: boolean
  available: boolean
  version?: string | null
  fingerprint?: string | null
  path?: string | null
  warnings?: number
  /** Машинный код отказа — `catalog-unavailable`; доступен — null. */
  code?: string | null
  /** Слова причины отказа (без кода и без секретов). */
  error?: string | null
  /** Одна строка правды: «каталог шаблонов … » либо «… недоступен: …». */
  words?: string | null
}

export async function getCatalog(): Promise<CatalogProbe> {
  return unwrap(await fetchWithRecordWarning('/api/catalog'))
}

/** Сохранение (или удаление пустым значением) ключа провайдера. */
export async function putProviderKey(provider: string, apiKey: string):
    Promise<SettingsState> {
  return unwrap(await fetchWithRecordWarning('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, api_key: apiKey }),
  }))
}

export async function checkProvider(provider: string):
    Promise<{ ok: boolean; detail: string; models?: string[] }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/providers/${encodeURIComponent(provider)}/check`,
    { method: 'POST' }))
}

// УИ-13: человеческое название прогона; пустая строка убирает его.
export async function setRunTitle(run: string, title: string):
    Promise<{ run: string; title: string | null }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/title`,
    { method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }) }))
}

// МП-1: живой перечень моделей ключа, свежие первыми + новейшая.
export async function providerModels(provider: string):
    Promise<{ models: string[]; latest: string | null }> {
  return unwrap(await fetchWithRecordWarning(
    `/api/providers/${encodeURIComponent(provider)}/models`))
}

export async function getAgents(run: string): Promise<AgentsConfig> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/agents`))
}

export async function putAgents(run: string, agents: AgentsConfig):
    Promise<AgentsConfig> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/agents`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(agents),
    }))
}

// ── Запись о прогоне (проект «Запись о прогоне», шаг 2) ──
// Одна сущность на обе системы: голова (объявление до старта), журнал
// (этапы обеих систем) и итоги. Словарь полей общий —
// `cosmos-run-record/v1`; панель — ХРАНИТЕЛЬ файла записи, хозяин —
// тот, кто объявил прогон.

/** Модель раскладки: роль, модель, провайдер, вид канала, усилие и
 *  СИСТЕМА строки (договор с COSMOS 11.09.2026): `cdpm` — роль панели,
 *  `cosmos` — роль COSMOS (сертификатор, читатель ИДАК), `runner` —
 *  роль раннера наборов. Поля нет вовсе — это модель CDPM: так
 *  читаются все записи, объявленные до 11.09.2026. */
export interface RecordModel {
  role: string
  model?: string | null
  provider?: string | null
  channel?: string | null
  effort?: string | null
  system?: 'cdpm' | 'runner' | 'cosmos' | null
}

/** Система строки моделей словами — короткая метка для формы. Поля нет
 *  (записи до 11.09.2026) — «панель CDPM». */
export const MODEL_SYSTEM_WORDS: Record<string, string> = {
  cdpm: 'панель CDPM',
  cosmos: 'COSMOS',
  runner: 'раннер наборов',
}

export function modelSystemWords(system?: string | null): string {
  return MODEL_SYSTEM_WORDS[system || 'cdpm'] || String(system)
}

/** Подсказка списка моделей: одна на обе формы (объявление прогона и
 *  карточка записи) — слова о ней в руководстве 10. */
export const MODELS_BOTH_SYSTEMS_TITLE =
  'Модели обеих систем: панель CDPM и COSMOS (сертификатор, читатель '
  + 'ИДАК). Заметка означает, что COSMOS при объявлении не был опрошен '
  + 'или не назвал моделей — голова записи неполна, и это записано честно'

export interface RecordCatalog {
  name?: string | null
  version?: string | null
  fingerprint?: string | null
  path?: string | null
}

export interface RecordBindings {
  catalog_version?: string | null
  catalog_fingerprint?: string | null
  path?: string | null
}

export interface RecordCode {
  cdpm_commit?: string | null
  cosmos_commit?: string | null
  runner_fingerprint?: string | null
}

export interface RecordLibraries {
  python?: string | null
  packages_sha256?: string | null
  packages?: { name: string; version: string }[]
}

export interface RecordContour {
  name: string
  services?: Record<string, string>
}

/** Объявимая часть среды — то, что панель знает ДО заморозки:
 *  площадка, каталог, привязки, модели по ролям, код, библиотеки.
 *  `models_note` — слова о неполноте раскладки (COSMOS не опрошен либо
 *  моделей не назвал); ключ ставится ТОЛЬКО со словами. */
export interface DeclarationEnvironment {
  contour?: RecordContour | null
  catalog?: RecordCatalog | null
  bindings?: RecordBindings | null
  models?: RecordModel[]
  models_note?: string | null
  code?: RecordCode | null
  libraries?: RecordLibraries | null
}

export interface DeclarationDefaults {
  purpose: string
  created_by: string
  answer_policy: string
  planned_stages: string[]
  stop_after: string
}

export interface DeclarationChoices {
  purposes: string[]
  answer_policies: string[]
  stages: string[]
}

/** Предполётная сверка гейта «Новый прогон»: `ok: false` — прогон не
 *  родится, причины словами в `words` (каталог неизвестен, привязок
 *  нет, раскладка моделей запрещена допуском и прочее). */
export interface DeclarationPreview {
  ok: boolean
  words: string[]
  declaration: DeclarationEnvironment
  /** Спрошен ли COSMOS при объявлении и что помешало — сведения формы,
   *  а не гейт: недоступность COSMOS прогона не отменяет. */
  cosmos?: { asked: boolean; detail: string }
  defaults: DeclarationDefaults
  choices: DeclarationChoices
}

export async function getDeclarationPreview(
    kit: KitScope = { dataClass: '' }): Promise<DeclarationPreview> {
  const q = new URLSearchParams()
  if (kit.dataClass) q.set('data_class', kit.dataClass)
  if (kit.llmScope) q.set('llm_scope', kit.llmScope)
  if (kit.basis && kit.basis.trim()) q.set('llm_scope_basis', kit.basis.trim())
  const qs = q.toString()
  return unwrap(await fetchWithRecordWarning(
    qs ? `/api/declaration-preview?${qs}` : '/api/declaration-preview'))
}

export interface RecordArtifact {
  kind?: string | null
  path?: string | null
  sha256?: string | null
}

/** Расход одной модели на этапе — в токенах (решение владельца 10.09:
 *  цены в запись не пишутся). `counted: false` — канал расход не
 *  сообщает, причина словами в `note`. */
export interface RecordResourceModel {
  role?: string | null
  model?: string | null
  provider?: string | null
  calls?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  counted?: boolean
  note?: string | null
}

export interface RecordJournalEntry {
  stage: string
  substage?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_machine_s?: number | null
  duration_waiting_human_s?: number | null
  outcome?: string | null
  reason_code?: string | null
  words?: string | null
  actor?: string | null
  artifacts?: RecordArtifact[]
  counters?: Record<string, number | null>
  resources?: {
    models?: RecordResourceModel[]
    solver_seconds?: number | null
  }
}

export interface RecordDeclaration {
  run_id: string
  created_at?: string | null
  created_by?: string | null
  purpose?: string | null
  contour?: RecordContour | null
  airline?: { name?: string | null; data_class?: string | null
              llm_scope?: string | null } | null
  rules_documents?: { name?: string | null; kind?: string | null
                      sha256?: string | null; size?: number | null }[]
  data_files?: { name?: string | null; sheets?: number | null
                 rows?: number | null; sha256?: string | null }[]
  answers?: { revision?: string | number | null; sha256?: string | null
              source?: string | null; at?: string | null }[]
  catalog?: RecordCatalog | null
  catalog_extension?: unknown
  bindings?: RecordBindings | null
  models?: RecordModel[]
  /** Неполнота раскладки в голове словами (ключа нет — раскладка
   *  полна): «COSMOS не опрошен при объявлении» либо «COSMOS не назвал
   *  моделей при объявлении». */
  models_note?: string | null
  solvers?: { name?: string | null; version?: string | null }[]
  code?: RecordCode | null
  libraries?: RecordLibraries | null
  planned_stages?: string[]
  stop_after?: string | null
  expectations?: { kind?: string | null; ref?: string | null } | null
  lineage?: {
    root_sha256?: string | null
    root_answers_sha256?: string | null
    parent_run?: string | null
    branch_of?: string | null
    package_sha256?: string | null
    source_run?: string | null
  } | null
  answer_policy?: string | null
}

export interface RecordResults {
  verdicts?: Record<string, unknown>
  deliveries?: RecordArtifact[]
  comparison?: Record<string, unknown>
  golden?: { confirmed?: boolean; by?: string | null; at?: string | null
             words?: string | null }
  timing?: { machine_s?: number | null; waiting_human_s?: number | null
             total_s?: number | null }
  state?: string | null
}

export interface RunRecord {
  schema: string
  declaration: RecordDeclaration
  declaration_sha256?: string | null
  journal: RecordJournalEntry[]
  results: RecordResults
}

/** Единая запись о прогоне: голова хозяина, журнал обеих систем
 *  (свои записи панели плюс присланные append-ом), итоги. Ответ 404 —
 *  записи о прогоне нет: это не ошибка, а честное «нет», поэтому
 *  возвращается null, а не исключение. */
export async function getRunRecord(run: string):
    Promise<RunRecord | null> {
  const resp = await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/record`)
  if (resp.status === 404) return null
  return unwrap<RunRecord>(resp)
}

/** Та же запись файлом `run-record.json` — для кнопки «Скачать
 *  запись» (обычная ссылка, без выборки). */
export function recordDownloadUrl(run: string): string {
  return `/api/runs/${encodeURIComponent(run)}/record?download=1`
}

// ── Дерево прогонов (проект «Запись о прогоне», шаг 3) ──
// База прогонов имеет вид ДЕРЕВА, а не списка: корень — отпечатки
// документов правил, данных и ответов на анкеты; узел — прогон; ребро
// — «те же входы, другое объявление» либо «продолжение от артефакта
// родителя». Индекс `cosmos-run-tree/v1` собирается из записей о
// прогонах и ничего к ним не добавляет: узел — плоский взгляд на
// объявление и итоги записи. Форма — схема `vmp/run_tree.schema.json`,
// сборщик — модуль `vmp/run_tree.py`; ручки одни у обеих систем
// (раздел 12 проекта `final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md`).

/** Вид ребра дерева (`run_tree.EDGES`, 4 слова). */
export type RunTreeEdgeKind =
  'корень' | 'соседняя ветвь' | 'изменены ответы' | 'продолжение'

/** Чем ветвь отличается от той, к кому ведёт ребро, крупными частями
 *  объявления (`run_tree.DIFFERS`, 10 слов). */
export type RunTreeDiffers =
  'модели' | 'решатели' | 'параметры решателей' | 'каталог' | 'привязки'
  | 'код' | 'библиотеки' | 'цель' | 'площадка' | 'план'

export interface RunTreeEdge {
  kind: RunTreeEdgeKind
  /** К какому прогону ведёт ребро; у корня — null. */
  to: string | null
  differs: RunTreeDiffers[]
  words: string | null
}

export interface RunTreeDocument {
  name: string | null
  sha256: string | null
}

/** Входы корня по ПЕРВОЙ записи: имена — человеку для узнавания,
 *  отпечатки — для сверки. */
export interface RunTreeDocuments {
  rules: RunTreeDocument[]
  data: RunTreeDocument[]
}

export interface RunTreeCatalog {
  name: string | null
  version: string | null
  fingerprint: string | null
}

export interface RunTreeBindingsRow {
  catalog_version: string | null
  catalog_fingerprint: string | null
}

export interface RunTreeCodeRow {
  cdpm_commit: string | null
  cosmos_commit: string | null
  runner_fingerprint: string | null
}

export interface RunTreeLibrariesRow {
  python: string | null
  packages_sha256: string | null
}

export interface RunTreeModelRow {
  model: string | null
  provider: string | null
  channel: string | null
  effort: string | null
}

/** Движок задачи и его параметры: имена — из словаря записи
 *  (`declaration.solvers[]` без поля «задача»: задача стала ключом). */
export interface RunTreeSolverRow {
  engine: string | null
  version: string | null
  build_sha256: string | null
  spent_limit_s: number | null
  unimproved_spent_limit_s: number | null
  moves: string | null
  seed: number | null
  step_limit: number | null
  unimproved_step_limit: number | null
  deterministic_time: number | null
  accepted_uncovered: string[] | null
}

/** Токены одной модели в одной роли за прогон. `counted: false` —
 *  канал расход не сообщает: честное «не считается», а не ноль. */
export interface RunTreeTokensRow {
  input_tokens: number | null
  output_tokens: number | null
  calls: number | null
  counted: boolean | null
}

export interface RunTreeTiming {
  machine_s: number | null
  waiting_human_s: number | null
  total_s: number | null
}

/** Признак прогона-образца: ставится ТОЛЬКО подтверждением владельца
 *  после приёмки на Prod (решение владельца 6.3 от 10.09.2026). */
export interface RunTreeGolden {
  confirmed: boolean
  by: string | null
  at: string | null
  words: string | null
}

/** Узел дерева — прогон: плоский взгляд на объявление и итоги записи.
 *  Журнала этапов и адресов служб площадки в узле нет: журнал читают в
 *  самой записи, а индекс носят между машинами. */
export interface RunTreeNode {
  run_id: string | null
  /** Где лежит запись: путь файла `run-record.json`, найденного
   *  обходом. */
  path: string | null
  created_at: string | null
  created_by: string | null
  purpose: string | null
  /** Имя площадки: local / test / stage / prod. */
  contour: string | null
  state: string | null
  declaration_sha256: string | null
  root_sha256: string | null
  root_answers_sha256: string | null
  airline: string | null
  catalog: RunTreeCatalog
  catalog_extension: RunTreeCatalog | null
  bindings: RunTreeBindingsRow
  code: RunTreeCodeRow
  libraries: RunTreeLibrariesRow
  models: Record<string, RunTreeModelRow>
  solvers: Record<string, RunTreeSolverRow>
  planned_stages: string[]
  stop_after: string | null
  parent_run: string | null
  branch_of: string | null
  source_run: string | null
  package_sha256: string | null
  edge: RunTreeEdge
  verdicts: Record<string, unknown>
  timing: RunTreeTiming
  tokens: Record<string, RunTreeTokensRow>
  counters: Record<string, number | null>
  golden: RunTreeGolden
}

/** Корень дерева: отпечатки входов и все прогоны на них. Больше одного
 *  корня с ответами — на дереве есть ветвь «изменены ответы». */
export interface RunTreeRoot {
  root_sha256: string | null
  root_answers_sha256: string[]
  airline: string | null
  documents: RunTreeDocuments
  runs: RunTreeNode[]
}

/** Отказ обхода: что найдено, но не прочитано. Читатель обязан видеть
 *  не только найденное, но и НЕ прочитанное. */
export interface RunTreeRefusal {
  path: string | null
  words: string[]
}

export interface RunTreeIndex {
  schema: string
  built_at: string | null
  roots: RunTreeRoot[]
  /** Прогоны без корня: входы отпечатками не названы и родителя в
   *  перечне нет. Молчать о них нельзя — они есть. */
  orphans: RunTreeNode[]
  refusals: RunTreeRefusal[]
}

export interface RunTreeCompareRow {
  field: string
  a: unknown
  b: unknown
}

export interface RunTreeCompareVerdict {
  a: unknown
  b: unknown
  same: boolean
}

export interface RunTreeCompareNumber {
  a: number | null
  b: number | null
  delta: number | null
}

export interface RunTreeCompareTokens {
  a: RunTreeTokensRow
  b: RunTreeTokensRow
  delta: { input_tokens: number | null; output_tokens: number | null
           calls: number | null }
  counted: boolean
  /** Роль шла разной моделью: у второго прогона такой строки нет —
   *  сравнивать её расход не с чем. */
  only_in: 'a' | 'b' | null
}

export interface RunTreeCompareCounter {
  a: number | null
  b: number | null
  delta: number | null
  /** Всегда «справочно»: число гуляет по чтению модели. */
  note: string
}

/** Сравнение двух прогонов: что менялось в объявлении и что изменилось
 *  в итогах. `words` — то же сравнение словами, как его печатает
 *  `vmp/run_tree.py`: показ ничего не пересказывает от себя. */
export interface RunTreeCompare {
  /** Вид сравнения: «пара», «золотой», «предыдущий». */
  kind: string
  a: string | null
  b: string | null
  same_root: boolean
  declaration: { changed: RunTreeCompareRow[]; same: string[] }
  results: {
    verdicts: Record<string, RunTreeCompareVerdict>
    timing: Record<string, RunTreeCompareNumber>
    tokens: Record<string, RunTreeCompareTokens>
    counters: Record<string, RunTreeCompareCounter>
  }
  words: string[]
}

/** Путь от корня к прогону: входы → родители → прогон. У прогона без
 *  корня входы пусты — путь всё равно есть. */
export interface RunTreePath {
  root: {
    root_sha256: string | null
    root_answers_sha256: string[] | null
    airline: string | null
    documents: RunTreeDocuments | null
  }
  runs: RunTreeNode[]
}

/** Сравнение с ожиданиями набора (`results.comparison.expectations`
 *  записи): его пишет хозяин записи при закрытии — раннер наборов из
 *  манифеста набора. Состав полей у хозяина свой, поэтому здесь названы
 *  только те, что показ понимает словами, а остальное показывается как
 *  есть. */
export interface RunTreeExpectations {
  kind?: string | null
  ref?: string | null
  verdict?: string | null
  words?: string[] | string | null
  judged?: Record<string, unknown> | null
  not_judged?: Record<string, unknown> | null
  differences?: unknown
  [key: string]: unknown
}

/** Взгляд дерева на один прогон: узел, путь от корня, соседние ветви,
 *  золотой прогон корня и готовые сравнения. */
export interface RunTreeView {
  node: RunTreeNode
  path: RunTreePath
  siblings: RunTreeNode[]
  golden: RunTreeNode | null
  compare_golden: RunTreeCompare | null
  compare_previous: RunTreeCompare | null
  expectations: RunTreeExpectations | null
}

/** Узла в дереве нет (404): не ошибка, а ответ со словами — прогон
 *  объявлен до введения записи, запись не найдена обходом либо индекс
 *  ещё не перестроен. */
export interface RunTreeAbsent {
  absent: true
  words: string
}

/** Индекс дерева целиком. `rebuild` — перестроить обходом каталога
 *  прогонов перед ответом (обычное чтение идёт из файла
 *  `run-tree.json`, диск на каждый запрос не обходится). */
export async function getRunTree(rebuild = false): Promise<RunTreeIndex> {
  return unwrap(await fetchWithRecordWarning(
    rebuild ? '/api/run-tree?rebuild=1' : '/api/run-tree'))
}

/** Кнопка «Обновить дерево»: та же ручка с перестройкой. Отдельным
 *  именем — чтобы у показа не было соблазна перестраивать индекс на
 *  каждое открытие карточки. */
export async function rebuildRunTree(): Promise<RunTreeIndex> {
  return getRunTree(true)
}

/** Взгляд дерева на прогон. 404 — узла нет: возвращаются слова
 *  сервера, а не исключение (см. `RunTreeAbsent`). */
export async function getRunTreeView(run: string):
    Promise<RunTreeView | RunTreeAbsent> {
  const resp = await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/tree`)
  if (resp.status === 404) {
    return { absent: true, words: await detailWords(resp) }
  }
  return unwrap<RunTreeView>(resp)
}

/** Сравнение двух прогонов по номерам записей. */
export async function compareRuns(a: string, b: string):
    Promise<RunTreeCompare> {
  const q = new URLSearchParams({ a, b })
  return unwrap(await fetchWithRecordWarning(`/api/run-tree/compare?${q.toString()}`))
}

/** Назвать прогон золотым. Подтверждает ЧЕЛОВЕК: «кто» и слова
 *  обязательны, иначе сервер отвечает 400 словами; прогон не завершён
 *  либо площадка не `prod` — 409 словами (решение владельца 6.3:
 *  золотой — только после приёмки на Prod). */
export async function markGolden(run: string, by: string, words: string):
    Promise<RunRecord> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/golden`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ by, words }),
    }))
}

/** Снять признак золотого: подтверждение отозвано (нашёлся прогон
 *  лучше либо признак поставлен по ошибке). Снимает тоже человек. */
export async function unmarkGolden(run: string, by: string):
    Promise<RunRecord> {
  return unwrap(await fetchWithRecordWarning(
    `/api/runs/${encodeURIComponent(run)}/golden/unmark`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ by }),
    }))
}
