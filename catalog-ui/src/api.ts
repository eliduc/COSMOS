// Клиент службы каталога шаблонов (FastAPI на том же хосте, порт 8105).
// Формы ответов и коды отказов — по контракту службы (проект шага 5 §3
// и catalog_service/API-CONTRACT.md). Отказ поднимается исключением
// ServiceError с машинным кодом и словами: экран показывает слова
// службы, а не свою догадку, и ничего не глотает молча.

// ── Слова каталога, по которым интерфейс принимает решения ─────────
// Это ДАННЫЕ службы (словари каталога), а не подписи экрана: роли,
// состояния исполнителей, статусы записей и состояния заявок приходят
// русскими словами. Держим их здесь одним местом — в файлах экранов
// русских строк нет вовсе (гейт T13).
export const ACTOR_OWNER = 'владелец'
export const ACTOR_ANALYST = 'аналитик'

export const STATE_PRESENT = 'есть'
export const STATE_CLAIMED = 'заявлен'
export const STATE_ABSENT = 'нет'

export const STATUS_PRESENT = 'есть'
export const STATUS_REMOVED = 'снята'

export const REQUEST_SUBMITTED = 'подана'
export const REQUEST_ACCEPTED = 'принята'
export const REQUEST_EXECUTOR_CLAIMED = 'исполнитель заявлен'
export const REQUEST_PRESENT = 'есть'
export const REQUEST_REJECTED = 'отклонена'

export const SOURCE_MANUAL = 'вручную'

/** Свидетель природы семьи (`nature_confirmed_by`, каталог 0.5.0) для
 *  записи, пришедшей конвейером пополнения: правило X18 не даёт такой
 *  записи опереться на перечни сертификатора — её свидетель всегда
 *  вердикт судьи заявки. Слово словаря `nature_witnesses`. */
export const WITNESS_CHANGE_REQUEST = 'change_request'

/** Значение фильтра «клетка без строк» у ручек записей и исполнителей
 *  (контракт §3: state=есть|заявлен|нет|не-объявлено). */
export const STATE_UNDECLARED_FILTER = 'не-объявлено'

/** Ключ итогов «не объявлено» в сводке ручки исполнителей (§3:
 *  totals = {cells, есть, заявлен, нет, не объявлено}). */
export const TOTALS_UNDECLARED = 'не объявлено'

/** Незакрытые заявки: по ним ещё будет решение или сборка. */
export const REQUEST_OPEN_STATES: string[] = [
  REQUEST_SUBMITTED, REQUEST_ACCEPTED, REQUEST_EXECUTOR_CLAIMED,
]

/** Карта цветов состояний — одна на три панели (решение §0 проекта):
 *  есть — зелёный, заявлен — жёлтый, нет — серый, «вид никем не
 *  объявлен» — синий с подсказкой, ошибки привязок — красный. */
export type BadgeKind = 'ok' | 'warn' | 'error' | 'info' | 'neutral'

export function stateBadgeKind(state: string | null | undefined): BadgeKind {
  if (state === STATE_PRESENT) return 'ok'
  if (state === STATE_CLAIMED) return 'warn'
  if (state === STATE_ABSENT) return 'neutral'
  return 'info'   // клетки без строк: вид никем не объявлен
}

export function statusBadgeKind(status: string | null | undefined): BadgeKind {
  return status === STATUS_REMOVED ? 'neutral' : 'ok'
}

export function requestBadgeKind(state: string): BadgeKind {
  if (state === REQUEST_PRESENT) return 'ok'
  if (state === REQUEST_REJECTED) return 'neutral'
  if (state === REQUEST_SUBMITTED) return 'info'
  return 'warn'   // принята, исполнитель заявлен
}

// ── Отказ службы ──────────────────────────────────────────────────
export interface Problem {
  where: string
  code: string
  message: string
  severity: string
}

export class ServiceError extends Error {
  status: number
  code: string
  words: string
  problems: Problem[]
  extra: Record<string, unknown>

  constructor(status: number, code: string, words: string,
              problems: Problem[], extra: Record<string, unknown>) {
    super(words)
    this.name = 'ServiceError'
    this.status = status
    this.code = code
    this.words = words
    this.problems = problems
    this.extra = extra
  }
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let code = String(resp.status)
    let words = `${resp.status} ${resp.statusText}`
    let problems: Problem[] = []
    let extra: Record<string, unknown> = {}
    try {
      const body = await resp.json()
      const detail = body?.detail ?? body
      if (detail && typeof detail === 'object') {
        code = String(detail.code ?? code)
        words = String(detail.words ?? words)
        problems = Array.isArray(detail.problems) ? detail.problems : []
        extra = (detail.extra ?? {}) as Record<string, unknown>
      } else if (typeof detail === 'string') {
        words = detail
      }
    } catch { /* тело не JSON — остаются код и статус */ }
    throw new ServiceError(resp.status, code, words, problems, extra)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function query(params: object): string {
  const q = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    q.set(key, String(value))
  }
  const text = q.toString()
  return text ? `?${text}` : ''
}

async function getJson<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(path))
}

/** Список в конверте {items: […]} либо голым массивом: две ручки
 *  контракта (заявки, журнал) описаны словами «список», и экран
 *  принимает обе формы, а не падает на второй. */
function asItems<T>(got: unknown): { items: T[]; words?: string } {
  if (Array.isArray(got)) return { items: got as T[] }
  const box = (got ?? {}) as { items?: T[]; words?: string }
  return { items: Array.isArray(box.items) ? box.items : [],
           words: box.words }
}

async function sendJson<T>(path: string, method: string,
                           body: unknown): Promise<T> {
  return unwrap<T>(await fetch(path, {
    method, headers: JSON_HEADERS, body: JSON.stringify(body),
  }))
}

// ── Сеанс, шапка, проверка ────────────────────────────────────────

/** Десять операций записи службы (контракт §2.2 и §4: карта `rights`).
 *  Имена — машинные ключи службы, а не подписи экрана. */
export type RightKey =
  | 'draft-save' | 'draft-status' | 'draft-discard' | 'request-submit'
  | 'assemble' | 'request-decide' | 'request-cr-key' | 'request-adopt'
  | 'release' | 'import'

/** Право на одну операцию: можно ли и, если нельзя, ПОЧЕМУ — словами
 *  самой службы. Второго вычисления прав у экрана нет: он гасит кнопку
 *  по `allowed` и показывает `words` подсказкой. */
export interface Right {
  allowed: boolean
  words: string | null
}

export interface Session {
  mode: 'view' | 'edit'
  actor: string | null
  read_only: boolean
  catalog: {
    version: string
    fingerprint: string
    is_draft: boolean
    dir_name: string
  }
  drafts: { families: number; criteria: number; dictionaries: number }
  requests_open: number
  /** Шаг 6: заявки «исполнитель заявлен» — по ним идёт CR продукта. */
  requests_in_work?: number
  /** Шаг 6: включён ли машинный канал заявок (CATALOG_MACHINE_TOKEN). */
  machine_channel?: boolean
  /** Шаг 6: классы документа — словарь каталога либо перечень читателя.
   *  Своей копии семи значений экран не держит: копия разошлась бы. */
  document_classes?: string[]
  rights: Record<string, Right>
  /** РВ-141: номер версии продукта (CREW_PRODUCT_VERSION площадки);
   *  не задан — null, и подписи внизу меню нет. */
  product_version?: string | null
}

/** Право на операцию из сеанса. Сеанс не прочитан — права нет: кнопка
 *  неактивна, а причину скажет строка состояния экрана. */
export function rightOf(session: Session | null, op: RightKey): Right {
  const got = session?.rights?.[op]
  return got && typeof got.allowed === 'boolean'
    ? got : { allowed: false, words: null }
}

/** Подсказка неактивной кнопки: слова службы либо ничего. */
export function rightHint(right: Right): string | undefined {
  return right.allowed ? undefined : (right.words ?? undefined)
}

export function isOwner(session: Session | null): boolean {
  return !!session && session.mode === 'edit'
    && session.actor === ACTOR_OWNER
}

export function draftCount(session: Session | null): number {
  if (!session) return 0
  const d = session.drafts
  return (d?.families ?? 0) + (d?.criteria ?? 0) + (d?.dictionaries ?? 0)
}

export async function getSession(): Promise<Session> {
  return getJson('/api/session')
}

export interface CatalogHeader {
  form_version?: number
  name?: string
  version: string
  fingerprint: string
  legacy_registry_rev?: string | null
  released_at: string
  released_by: string
  note: string
  sections?: Record<string, string>
  gates?: { stand_tasks_required: boolean; subject_words_required: boolean }
}

export interface CatalogOverview extends CatalogHeader {
  is_draft: boolean
  counts: {
    families: number
    criteria: number
    dictionaries_keys: number
    requests_open: number
    versions: number
    drafts: number
  }
  problems: { errors: number; warnings: number }
  releases_ok: boolean
}

export async function getCatalog(): Promise<CatalogOverview> {
  return getJson('/api/catalog')
}

export interface ValidateReport {
  problems: Problem[]
  errors: number
  warnings: number
  releases: { version: string; ok: boolean; words: string }[]
}

export async function validateCatalog(): Promise<ValidateReport> {
  return getJson('/api/validate')
}

// ── Записи ────────────────────────────────────────────────────────
export type Section = 'families' | 'criteria'

export interface RecordRow {
  section: Section
  id: string
  title: string
  class: string
  kind: string | null
  zone: string | null
  nature: string | null
  violation_unit: string | null
  status: string
  params: number
  stand_tasks: number
  executor_summary: Record<string, number>
  draft: string | null
  request_id: string | null
}

export interface RecordsFilter {
  section?: Section
  status?: string
  kind?: string
  zone?: string
  class?: string
  state?: string
  q?: string
  drafts?: string
  requests?: string
}

export async function listRecords(filter: RecordsFilter):
    Promise<{ items: RecordRow[]; total: number }> {
  return getJson(`/api/records${query(filter)}`)
}

/** Запись каталога: форма — схема читателя ($defs/family, $defs/criterion). */
export type CatalogRecord = Record<string, unknown>

// Чтение полей записи: форма записи известна схеме читателя, но по
// проводу приходит свободный JSON — здесь одно место, где он
// разбирается на строки, списки и объекты, вместо приведения типов в
// каждом экране.
export function recStr(rec: CatalogRecord | null | undefined,
                       key: string): string {
  const value = rec?.[key]
  return typeof value === 'string' ? value : ''
}

export function recStrOrNull(rec: CatalogRecord | null | undefined,
                             key: string): string | null {
  const value = rec?.[key]
  return typeof value === 'string' ? value : null
}

export function recArr<T = unknown>(rec: CatalogRecord | null | undefined,
                                    key: string): T[] {
  const value = rec?.[key]
  return Array.isArray(value) ? value as T[] : []
}

export function recObj(rec: CatalogRecord | null | undefined,
                       key: string): Record<string, unknown> | null {
  const value = rec?.[key]
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

/** Пустая запись по схеме читателя: все обязательные поля на месте,
 *  чтобы карточка не молчала о том, что заполнить. Черновик заявки
 *  ($defs/family_draft, $defs/criterion_draft) полей `status`,
 *  `stand_tasks` и `history` не несёт — их ставит сборка. */
export function blankRecord(section: string, id: string, title: string,
                            opts: { classes: string[]; statuses: string[]
                                    forRequest?: boolean }): CatalogRecord {
  const today = new Date().toISOString().slice(0, 10)
  const base: CatalogRecord = {
    id,
    class: opts.classes[0] ?? '',
    title,
    acceptance: null,
    origin: { kind: '', date: today, note: '', confirmed_by: '' },
    note: null,
  }
  if (!opts.forRequest) {
    base.stand_tasks = []
    base.status = opts.statuses.includes(STATUS_PRESENT)
      ? STATUS_PRESENT : (opts.statuses[0] ?? '')
  }
  if (section === 'criteria') {
    return {
      ...base,
      zone: '',
      needs: null,
      semantics: { formula: null, rates_form: null, text: null },
      instance_form: null,
      computable_by: [],
      refusal: null,
    }
  }
  return {
    ...base,
    subject: '',
    subject_words: null,
    complements: [],
    kind: '',
    zone: '',
    nature: '',
    // Свидетель природы (0.5.0): у записи ЗАЯВКИ он известен заранее —
    // правило X18 оставляет ей только вердикт судьи заявки; запись,
    // заводимую владельцем прямо в каталоге, свидетелем называет он сам
    // (пусто — служба откажет словами, как и у природы).
    nature_confirmed_by: opts.forRequest ? WITNESS_CHANGE_REQUEST : '',
    violation_unit: null,
    violation_size: null,
    reads_history: null,
    params: [],
    param_sets: [],
    conditions: [],
    rule_form: null,
    semantics: { scope: null, unit: null, measure: null, text: '' },
    executor_kinds: [],
    check_templates: [],
  }
}

/** Параметр записи ($defs/param). */
export interface RecordParam {
  name: string
  unit: string
  relation: string | null
  qualifier: string | null
  note: string | null
}

/** Строка истории записи ($defs/history_item). */
export interface HistoryItem {
  date: string
  kind: string
  by: string
  note: string
  ref: string | null
}

/** Стендовая задача записи ($defs/stand_task). */
export interface StandTask {
  id: string
  world: Record<string, unknown>
  expected: Record<string, unknown>
  note: string | null
}

export interface Change {
  section: string
  id: string
  kind: string
  note: string
  level?: string
}

export interface ExecutorRow {
  state: string
  lang?: string | null
  file?: string | null
  symbol?: string | null
  note?: string | null
  cr_key?: string | null
  fields?: Record<string, unknown>
  [key: string]: unknown
}

export interface StandRow {
  task: string
  executor_kind: string
  result: string
  at: string
  commit: string
  note: string | null
}

export interface DraftFile {
  form_version?: number
  section: string
  id?: string
  key?: string
  record?: CatalogRecord
  value?: unknown
  note: string
  by: string
  saved_at: string
  request_id?: string | null
}

export interface RecordDetail {
  record: CatalogRecord
  draft: DraftFile | null
  changes: Change[]
  /** Есть ли запись в каталоге: `false` — карточка открыта из черновика
   *  НОВОЙ записи (её в каталоге ещё нет, соберут сборкой). */
  in_catalog?: boolean
  executors: Record<string, Record<string, ExecutorRow>>
  stand: { passed: number; failed: number; skipped: number
           rows: StandRow[] } | null
  request: CatalogRequest | null
  problems: Problem[]
  shown: { version: string; fingerprint: string }
  pinned_words: string | null
  /** Откроется ли карточка версии, на которой закреплён продукт: только
   *  тогда рядом со строкой расхождения ставится ссылка на неё. */
  pinned_release_ok?: boolean
}

/** Карточка записи. `fingerprint` — отпечаток каталога, на котором
 *  закреплён продукт (ссылка из COSMOS или CDPM): по нему служба
 *  строит честную строку «показана другая версия». */
export async function getRecord(section: string, id: string,
                                version?: string,
                                fingerprint?: string): Promise<RecordDetail> {
  return getJson(`/api/records/${encodeURIComponent(section)}`
    + `/${encodeURIComponent(id)}${query({ version, fingerprint })}`)
}

// ── Черновики ─────────────────────────────────────────────────────
export interface DraftRow {
  section: string
  id?: string
  key?: string
  kind: string
  saved_at: string
  by: string
  note: string
  request_id?: string | null
  changes: Change[]
}

export interface VersionHint {
  level: string
  version: string
  changes?: number
  by_level?: Record<string, number>
  base?: string | null
}

export interface DraftsList {
  items: DraftRow[]
  suggested: VersionHint | null
  /** Сухой прогон B01 для подсказанного номера: предупреждение о стопе
   *  сторожа приходит ДО кнопки сборки, а не отказом после нажатия. */
  bindings_stop: { code: string; words: string
                   extra?: Record<string, unknown> } | null
}

export async function listDrafts(): Promise<DraftsList> {
  return getJson('/api/drafts')
}

export interface DraftSaved {
  saved: boolean
  changes: Change[]
  problems: Problem[]
  derived: string[]
}

export async function putRecordDraft(section: string, id: string,
                                     record: CatalogRecord, note: string):
    Promise<DraftSaved> {
  return sendJson(`/api/drafts/${encodeURIComponent(section)}`
    + `/${encodeURIComponent(id)}`, 'PUT', { record, note })
}

export async function putDictionaryDraft(key: string, value: unknown,
                                         note: string): Promise<DraftSaved> {
  return sendJson(`/api/drafts/dictionaries/${encodeURIComponent(key)}`,
    'PUT', { value, note })
}

export async function deleteDraft(section: string, id: string):
    Promise<{ discarded: boolean }> {
  return unwrap(await fetch(`/api/drafts/${encodeURIComponent(section)}`
    + `/${encodeURIComponent(id)}`, { method: 'DELETE' }))
}

/** «Отменить все» — одной ручкой службы: одна строка журнала действий
 *  вместо строки на каждый черновик. */
export async function deleteAllDrafts(): Promise<{ discarded: number }> {
  return unwrap(await fetch('/api/drafts', { method: 'DELETE' }))
}

// ── Словари ───────────────────────────────────────────────────────
export interface DictionaryRow {
  key: string
  kind: 'list' | 'object' | 'text' | 'order'
  size: number
  draft: boolean
  usages: number
}

export interface DictionariesList {
  keys: DictionaryRow[]
  derived: string[]
}

/** Служебные перечни читателя: их значения — код, а не данные
 *  каталога; служба отвечает на правку 422 closed-list, и экран
 *  такой правки не предлагает (контракт §3). */
export const CLOSED_DICTIONARY_KEYS: string[] = [
  'request_states', 'executor_states', 'record_statuses', 'products',
  'languages', 'stand_results',
]

export async function listDictionaries(): Promise<DictionariesList> {
  return getJson('/api/dictionaries')
}

export interface DictionaryDetail {
  key: string
  kind: 'list' | 'object' | 'text' | 'order'
  value: unknown
  draft: DraftFile | null
  usages: { section: string; id: string; where: string }[]
  journal: { at?: string; actor?: string; op?: string; words?: string
             by?: string; note?: string; version?: string
             kind?: string }[]
  closed?: boolean
  derived?: boolean
}

export async function getDictionary(key: string): Promise<DictionaryDetail> {
  return getJson(`/api/dictionaries/${encodeURIComponent(key)}`)
}

// ── Заявки ────────────────────────────────────────────────────────
export interface RequestSource {
  kind: string
  document_class: string | null
  run_class: string | null
  ref: string | null
}

export interface CatalogRequest {
  request_id: string
  state: string
  section: Section
  record_id: string
  submitted_at: string
  submitted_by: string
  source: RequestSource
  draft: CatalogRecord | null
  executor_kinds_requested: string[] | null
  decision: { by: string | null; at: string | null; words: string | null }
  cr_key: string | null
  record_exists?: boolean
  has_draft?: boolean
  /** Шаг 6: ключ CR по закону читателя — cr_key_of(request_id). */
  cr_key_expected?: string | null
  /** Шаг 6: имя файла-кандидата конвейера под папкой каталога. */
  candidate?: string | null
  candidate_evidence?: RequestEvidence | null
}

/** Свидетельство гейтов ветки CR из файла-кандидата (§8.4 контракта).
 *  Это отчёт конвейера, а не подпись: подписанное свидетельство —
 *  очередь укрепления. */
export interface RequestEvidence {
  cr_key?: string
  branch?: string
  commit?: string | null
  evidence_commit?: string | null
  at?: string
  isolation?: string | null
  gates?: {
    stand?: { passed?: number; failed?: number; skipped?: number }
    pytest?: string
    bindings_gate?: string
    bindings_build?: string
    catalog_validate_errors?: number
    [key: string]: unknown
  }
  executors?: { kind: string; state: string; file: string; symbol: string }[]
}

export interface RequestsFilter {
  state?: string
  submitted_by?: string
  source_kind?: string
  section?: string
  record_id?: string
  /** Шаг 6: ключ CR и префикс source.ref (имя прогона до «#»). */
  cr_key?: string
  ref?: string
  q?: string
}

export async function listRequests(filter: RequestsFilter):
    Promise<{ items: CatalogRequest[] }> {
  return asItems<CatalogRequest>(
    await getJson(`/api/requests${query(filter)}`))
}

export interface RequestDetail extends CatalogRequest {
  draft_problems: Problem[]
  record: CatalogRecord | null
}

export async function getRequest(id: string): Promise<RequestDetail> {
  return getJson(`/api/requests/${encodeURIComponent(id)}`)
}

export interface NewRequest {
  section: Section
  record_id: string
  source: RequestSource
  draft: CatalogRecord | null
  executor_kinds_requested: string[] | null
  /** Шаг 6: материал-ключ заявки (64 hex) из ссылки продукта — по нему
   *  служба нумерует заявку законом читателя. */
  key?: string | null
  submitted_at?: string | null
}

/** Предпросмотр подачи: номер по закону, ключ CR, известная заявка,
 *  существует ли запись и проблемы — БЕЗ записи и без журнала. */
export interface RequestPreview {
  request_id: string
  cr_key_expected: string
  known: CatalogRequest | null
  record_exists: boolean
  open_request: CatalogRequest | null
  problems: Problem[]
}

export async function dryRunRequest(body: NewRequest):
    Promise<RequestPreview> {
  return sendJson('/api/requests/dry-run', 'POST', body)
}

export async function createRequest(body: NewRequest):
    Promise<{ request_id: string; state: string }> {
  return sendJson('/api/requests', 'POST', body)
}

export async function decideRequest(id: string, decision: string,
                                    words: string): Promise<CatalogRequest> {
  return sendJson(`/api/requests/${encodeURIComponent(id)}/decide`, 'POST',
    { decision, words })
}

export async function putCrKey(id: string, crKey: string):
    Promise<CatalogRequest> {
  return sendJson(`/api/requests/${encodeURIComponent(id)}/cr_key`, 'PUT',
    { cr_key: crKey })
}

export async function adoptRequest(id: string, confirm: string | null,
                                   candidate = false):
    Promise<{ draft_written: boolean; next: string;
              candidate: string | null; executors_missing: string[] }> {
  return sendJson(`/api/requests/${encodeURIComponent(id)}/adopt`, 'POST',
    { confirm, candidate })
}

// ── Исполнители по продуктам ──────────────────────────────────────
export interface ExecutorProduct {
  product: string
  file_name: string
  found: boolean
  product_version: string | null
  catalog_version: string | null
  catalog_fingerprint: string | null
  generated_at: string | null
  generated_from: string | null
  freshness: 'match' | 'stale-draft' | 'stop' | 'missing'
  freshness_words: string
  problems: Problem[]
}

export interface ExecutorCellData {
  best: string | null
  by: Record<string, ExecutorRow>
}

export interface ExecutorsRow {
  section: string
  id: string
  title: string
  cells: Record<string, ExecutorCellData>
  stand: Record<string, StandRow[]>
}

export interface ExecutorsReport {
  kinds: string[]
  products: ExecutorProduct[]
  rows: ExecutorsRow[]
  totals: Record<string, number>
  // Общие замечания: полнота по видам (B04) судится зонами всех файлов
  // привязок сразу, поэтому её адрес — запись, а не файл продукта.
  problems?: Problem[]
}

export interface ExecutorsFilter {
  product?: string
  section?: string
  id?: string
  kind?: string
  state?: string
}

export async function getExecutors(filter: ExecutorsFilter):
    Promise<ExecutorsReport> {
  return getJson(`/api/executors${query(filter)}`)
}

// ── Версии, сборка, выпуск ────────────────────────────────────────
export interface VersionRow {
  version: string
  fingerprint: string
  released_at: string
  released_by: string
  code: string
  changes: Change[]
  release_dir_ok: boolean
  release_fingerprint_ok: boolean
}

export interface VersionsReport {
  current: CatalogHeader & { is_draft: boolean }
  versions: VersionRow[]
  suggested: VersionHint | null
  drafts: number
}

export async function getVersions(): Promise<VersionsReport> {
  return getJson('/api/versions')
}

/** Карточка версии (контракт §4, `GET /api/versions/{v}`). Шапка выпуска
 *  лежит ПОД ключом `header` — на верхнем уровне только номер: экран
 *  берёт отпечаток, дату, автора и заметку оттуда, а не с верхнего
 *  уровня, где их нет. `entry` — запись журнала версий (у текущего
 *  черновика её ещё нет). */
export interface VersionDetail {
  version: string
  entry: VersionRow | null
  header: (CatalogHeader & { is_draft?: boolean }) | null
  changes: Change[]
  loadable: boolean
  is_current: boolean
}

export async function getVersion(version: string): Promise<VersionDetail> {
  return getJson(`/api/versions/${encodeURIComponent(version)}`)
}

export interface CompareReport {
  a: { version: string; fingerprint: string }
  b: { version: string; fingerprint: string }
  changes: Change[]
  level: string
}

export async function compareVersions(a: string, b: string):
    Promise<CompareReport> {
  return getJson(`/api/versions/compare${query({ a, b })}`)
}

export interface AssembleResult {
  version: string
  fingerprint: string
  applied: number
  derived: string[]
  problems: Problem[]
  next: string[]
}

export async function assemble(note: string, version: string | null,
                               confirmStop: boolean): Promise<AssembleResult> {
  return sendJson('/api/versions/assemble', 'POST',
    { note, version, confirm_stop: confirmStop })
}

export interface ReleaseResult {
  version: string
  fingerprint: string
  released_at: string
  code: string
  changes: number
  release_dir: string
  next: string[]
}

export async function release(version: string, note: string,
                              confirmStop: boolean): Promise<ReleaseResult> {
  return sendJson('/api/versions/release', 'POST',
    { version, note, confirm_stop: confirmStop })
}

/** Строка чек-листа выпуска: слова — от службы (маркеры, чистоту дерева
 *  git и остановку сторожа знает только она), а не от экрана. */
export interface ReleaseCheck {
  n: number
  key: string
  title: string
  ok: boolean
  code: string | null
  words: string
  extra?: Record<string, unknown>
}

export interface ReleaseChecks {
  version: string
  suggested: VersionHint & { changes: number }
  ok: boolean
  checks: ReleaseCheck[]
  confirm_stop_needed: boolean
}

/** Сухой прогон выпуска: восемь предпроверок словами, без записи. */
export async function getReleaseChecks(version?: string):
    Promise<ReleaseChecks> {
  return getJson(`/api/versions/release/checks${query({ version })}`)
}

/** Выгрузка одним файлом — обычная ссылка: имя файла службa задаёт
 *  заголовком Content-Disposition. */
export const EXPORT_URL = '/api/export'

export interface ImportResult {
  replaced: boolean
  version: string
  fingerprint: string
  warnings: string[]
}

export async function importCatalog(file: File, force: boolean):
    Promise<ImportResult> {
  const form = new FormData()
  form.append('file', file)
  return unwrap(await fetch(`/api/import${force ? '?force=1' : ''}`,
    { method: 'POST', body: form }))
}

// ── Журнал действий службы ────────────────────────────────────────
export interface JournalRow {
  at: string
  actor: string
  op: string
  target: string
  outcome: string
  words: string
}

export async function getJournal(limit = 100):
    Promise<{ items: JournalRow[]; words?: string }> {
  return asItems<JournalRow>(await getJson(`/api/journal${query({ limit })}`))
}
