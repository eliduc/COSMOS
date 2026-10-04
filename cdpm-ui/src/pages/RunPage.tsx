import { useCallback, useEffect, useMemo, useRef, useState,
         type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ReactFlow, Background, Controls, MarkerType, useEdgesState,
         useNodesState, type Node, type Edge,
         type NodeMouseHandler } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import notify from '../components/common/StyledToast'
import { PackageReadinessProblems } from '../components/PackageReadinessProblems'
import clsx from 'clsx'
import { Download, ExternalLink, Map as MapIcon, Pause, Pencil, Play,
         RotateCcw, Send, Square, Trash2, X }
  from 'lucide-react'
import StageNode, { type NodeAction, type StageKind,
                    type StageNodeData }
  from '../components/graph/StageNode'
import CatalogNode, { CATALOG_COLOR, type CatalogNodeData }
  from '../components/graph/CatalogNode'

// Обработчики стадий, доступные панели действий над блоками.
type StageHandlers = {
  launch: () => Promise<void>
  launchExchange: (continueFromStop?: boolean) => Promise<void>
  launchFullCycle: () => Promise<void>
  launchWhole: () => Promise<void>
  launchArbiter: () => Promise<void>
  launchDataRun: () => Promise<void>
  launchDataReview: () => Promise<void>
  sendNow: () => Promise<void>
  sendControl: (cmd: 'pause' | 'resume' | 'stop') => Promise<void>
  setConfirmStop: (v: boolean) => void
  // Загрузка книги данных с блока схемы (перетаскивание / выбор).
  uploadData: (list: FileList | null) => Promise<void>
}

// Каталог шаблонов паспорта: версия и отпечаток, а при настроенной
// службе (CREW_CATALOG_URL) — ссылка на карточку версии в её экране
// «Версии». Адреса в паспорте нет и не будет: он хранит только версию
// и отпечаток, ссылку строит интерфейс — переехавшая служба не должна
// делать прежние паспорта лживыми.
function CatalogVersionLink({ version, fingerprint }:
                            { version?: string | null
                              fingerprint?: string | null }) {
  const url = useCatalogStore(s => s.url)
  const load = useCatalogStore(s => s.load)
  useEffect(() => { load() }, [load])
  if (!version) return <>не объявлен</>
  const text = `${version} · ${(fingerprint || '').slice(0, 12)}…`
  const href = catalogLink(url, `/versions/${encodeURIComponent(version)}`)
  if (!href) {
    return <span title="служба каталога не настроена (CREW_CATALOG_URL)">
      {text}
    </span>
  }
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
       title="Открыть версию каталога в службе каталога шаблонов"
       className="text-cf-primary hover:underline">{text}</a>
  )
}

// Заголовок секции панели деталей (слово владельца 07.09: «всё очень
// скученно — нет промежутков между секциями»): секции разделяются
// линией и воздухом сверху, заголовок — капителью приглушённого цвета.
function fmtReplayVal(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
    return String(v)
  }
  const s = JSON.stringify(v)
  return s.length > 160 ? s.slice(0, 160) + '…' : s
}

function fmtDivergence(fd: Record<string, unknown>): string {
  const kind = String(fd.kind || '')
  if (kind === 'prompt') {
    return `запрос к модели вне журнала — стадия ${fd.stage || '—'}, роль `
      + `${fd.role || '—'}: ${fd.words || ''}`
  }
  if (kind === 'outcome') {
    return `${fd.name}: ${fmtReplayVal(fd.source)} → ${fmtReplayVal(fd.replay)}`
      + (fd.words ? ` (${fd.words})` : '')
  }
  if (kind === 'json') {
    return `${fd.path}: ${fd.where || ''} — ${fmtReplayVal(fd.source)} → `
      + `${fmtReplayVal(fd.replay)}${fd.note ? ` (${fd.note})` : ''}`
  }
  if (kind === 'text') {
    return `${fd.path}, строка ${fd.line ?? '—'}: `
      + `${fmtReplayVal(fd.source)} → ${fmtReplayVal(fd.replay)}`
  }
  if (kind === 'binary') {
    return `${fd.path}: двоичное расхождение со смещения ${fd.offset}`
  }
  if (kind === 'only-in-source' || kind === 'only-in-replay') {
    return `${fd.path}: ${kind === 'only-in-source'
      ? 'есть только у источника' : 'есть только у повтора'}`
  }
  return String(fd.words || fd.path || JSON.stringify(fd))
}

const SECTION_H3 = 'mt-4 border-t border-cf-border pt-3 text-xs '
  + 'font-semibold uppercase tracking-wide text-cf-text-muted'

// Причина остановки аудит-цикла словами (вопрос владельца на демо
// 06.09: «после запуска аудитора 2 обмена, а не 3» — лимит обменов
// потолок, цикл закрывается раньше по двум чистым аудитам подряд).
function verdictReason(v: string): string {
  return v === 'успех' ? 'два чистых аудита подряд'
    : v === 'лимит' ? 'исчерпан лимит обменов'
    : v === 'стагнация' ? 'замечания перестали убывать'
    : v === 'остановлен по Б/К' ? 'блокирующих и критических нет'
    // A07 (12.09.2026): чистые метрики при живой Б/К-находке реестра.
    : v === 'не закрыто' ? 'блокирующие находки реестра не закрыты'
    : v === 'не закрыто (по реестру)'
      ? 'старый цикл: записан «успех», но в реестре живут блокирующие'
    : v
}

// A07, миграция показа: вердикт, пересуженный реестром при чтении
// (сервер отдаёт verdict_by_registry; файлы истории не переписаны).
function shownVerdict(ex: ExchangeState): string {
  return ex.verdict_by_registry ?? ex.verdict?.verdict ?? ''
}
import AgentConfigPopup from '../components/graph/AgentConfigPopup'
import FileConfigPopup from '../components/graph/FileConfigPopup'
import ConfirmDialog from '../components/common/ConfirmDialog'
import MapView from '../components/MapView'
import EffectiveParameters from '../components/EffectiveParameters'
import { RunTreeCard } from '../components/RunTree'
import FilesNode, { POLICY_LABELS, type FilesNodeData }
  from '../components/graph/FilesNode'
import Button from '../components/common/Button'
import StatusBadge from '../components/common/StatusBadge'
import { arbiterAnketaUrl, arbiterStatus, arbiterSvodUrl,
         artifactUrl, catalogLink, dataStatus, deleteDataFile,
         getCatalogRequests, postCatalogRequests,
         type CatalogCandidateRow, type CatalogChannel,
         type CatalogRequestRow, type CatalogRequestsBody,
         type CatalogRequestsState,
         getReplayReport, startReplay, type ReplayReport,
         getRunRecord, recordDownloadUrl,
         modelSystemWords, MODELS_BOTH_SYSTEMS_TITLE,
         type RecordJournalEntry, type RunRecord,
         exchangeAnketaUrl, exchangeRoundUrl,
         exportUrl, getDelivery, resetRun, sendTaskPackage,
         startSmtVerify,
         exchangeStatus, exchangeSvodUrl, getAgents, getFilesConfig,
         humanState, humanVerdict, isStalledState,
         runControl, runStatus, setRunTitle, startArbiter,
         startDataReview, startFullCycle, validateCycleLimits,
         EXCHANGE_ROUNDS_MIN, EXCHANGE_ROUNDS_MAX,
         FULL_CYCLES_MIN, FULL_CYCLES_MAX,
         uploadAnswers, uploadThirdParty, startMode2,
         startDataRun,
         startExchange,
         startExtract, uploadData, listRoles, type AgentsConfig,
         type ArbiterState, type DataState, type DeliveryMark,
         type ExchangeState,
         type FileConfig, type RunStatus } from '../api'
import { useCatalogStore } from '../stores/catalogStore'
import { Trophy } from 'lucide-react'

// Рамка группы стадий (по образцу групп CODERS/TESTERS CodeForge):
// пунктирный контур с плашкой-заголовком, лежит под блоками.
// Кнопка контура (слово владельца 06.09: «карта/персонал — отдельной
// кнопкой в нижнем контуре, а не внутри блока»): действие группы
// живёт на её рамке, у правого верхнего угла, а не на блоке-члене.
export interface FrameAction {
  label: string
  onClick: () => void
  disabled?: boolean
  tip?: string
}

function FrameNode({ data }: {
  data: { label: string; w: number; h: number; color: string
          action?: FrameAction }
}) {
  return (
    <div
      className="cursor-move rounded-2xl border-2 border-dashed"
      style={{ width: data.w, height: data.h,
               borderColor: data.color }}
    >
      <span
        className="absolute -top-3 left-4 rounded bg-cf-panel px-2
                   py-0.5 text-xs font-bold tracking-wider"
        style={{ color: data.color }}
      >
        {data.label}
      </span>
      {data.action && (
        <button
          onClick={e => {
            e.stopPropagation()
            if (!data.action?.disabled) data.action?.onClick()
          }}
          disabled={data.action.disabled}
          aria-label={data.action.label}
          title={data.action.tip}
          className="nodrag nopan absolute -top-4 right-4 flex
                     items-center gap-1.5 rounded-lg bg-emerald-500
                     px-3 py-1.5 text-xs font-semibold text-white shadow
                     transition-colors hover:bg-emerald-400
                     disabled:cursor-not-allowed disabled:opacity-50"
        >
          <MapIcon className="h-4 w-4" />
          {data.action.label}
        </button>
      )}
    </div>
  )
}

// Страница прогона: диаграмма конвейера (по образцу графа CodeForge).
// Клик по блоку открывает панель деталей справа; арбитр — третья
// независимая модель, разрешающая тупики аудит-цикла (решение
// владельца 2026-08-18); сравнения с эталоном в продукте нет.

const NODE_TYPES = { stage: StageNode, frame: FrameNode,
                     files: FilesNode, catalog: CatalogNode }
// Узел-спутник «Каталог шаблонов» (слово владельца 17.09): отдельная
// служба, не стадия — стоит над лентой правил вне рамок групп, между
// формализацией и итогом, куда ведут его связи.
const POS_CATALOG = { x: 1300, y: -195 }
const ORDER: StageKind[] = ['freeze', 'worker', 'artifacts',
                            'auditor', 'arbiter', 'output',
                            'data_checks', 'data_report']
// Раскладка: лента правил (блок «Документы правил» над ней — её
// первый блок) и ЛЕНТА ДАННЫХ ниже (решение владельца 2026-08-18);
// аудитор поднят над циклом (как блоки улучшателей в эталоне).
const POS: Record<StageKind, { x: number; y: number }> = {
  documents: { x: 40, y: -170 }, freeze: { x: 70, y: 150 },
  worker: { x: 660, y: 150 }, artifacts: { x: 920, y: 150 },
  auditor: { x: 1250, y: 60 },
  arbiter: { x: 1580, y: 150 }, output: { x: 1910, y: 150 },
  'data-files': { x: 70, y: 560 },
  data_checks: { x: 920, y: 610 }, data_report: { x: 1250, y: 610 },
}
const FRAMES = [
  { id: 'f-prep', x: 40, y: 90, w: 280, h: 270,
    label: 'ПОДГОТОВКА', color: '#64748b' },
  { id: 'f-form', x: 630, y: 90, w: 540, h: 270,
    label: 'ФОРМАЛИЗАЦИЯ', color: '#6366f1' },
  { id: 'f-audit', x: 1220, y: 0, w: 280, h: 360,
    label: 'АУДИТ-ЦИКЛ', color: '#f59e0b' },
  { id: 'f-eval', x: 1550, y: 90, w: 280, h: 270,
    label: 'АРБИТРАЖ', color: '#10b981' },
  // «Итог» — отдельная группа (замечание владельца 2026-08-21):
  // блок приглушён, пока пары «свод + анкета» ещё нет.
  { id: 'f-final', x: 1880, y: 90, w: 280, h: 270,
    label: 'ИТОГ', color: '#0d9488' },
  { id: 'f-data', x: 40, y: 500, w: 1460, h: 330,
    label: 'ЛИНИЯ ДАННЫХ', color: '#0ea5e9' },
]
// Принадлежность блоков группам: блок перетаскивается отдельно,
// рамка группы тянет группу целиком, после переноса блока рамка
// пересчитывается по его новому положению (замечание владельца
// 2026-08-18).
const MEMBER_OF: Partial<Record<string, string>> = {
  freeze: 'f-prep', worker: 'f-form', artifacts: 'f-form',
  auditor: 'f-audit', arbiter: 'f-eval', output: 'f-final',
  'data-files': 'f-data', data_checks: 'f-data',
  data_report: 'f-data',
}
const FRAME_BY_ID = Object.fromEntries(FRAMES.map(f => [f.id, f]))
const FIT_PAD = 30
const FIT_TOP = 40

// Родители наследования ролей (с сервера, /api/agents/roles):
// роль без своей конфигурации работает конфигурацией родителя.
type RoleParents = Record<string, string | null>

function effectiveRole(agents: AgentsConfig, role: string,
                       parents: RoleParents): string | undefined {
  // Зеркало providers.effective_role: своя конфигурация, иначе
  // родитель по цепочке (data_compiler → applier и т.д.). Подпись
  // узла обязана называть модель, которая РЕАЛЬНО работает в узле,
  // а не соседней роли (живой урок 05.09: «Проверки данных»
  // показывали модель Обработчика, считала же роль data_compiler).
  const seen = new Set<string>()
  let cur: string | null | undefined = role
  while (cur && !seen.has(cur)) {
    if (agents[cur]?.channels) return cur
    seen.add(cur)
    cur = parents[cur]
  }
  return undefined
}

function roleModelLine(agents: AgentsConfig, role: string,
                       parents: RoleParents = {}):
    string | undefined {
  // Сводка трёх слотов модели эффективной роли: показываются
  // настроенные слоты.
  const eff = effectiveRole(agents, role, parents)
  const ch = eff ? agents[eff]?.channels : undefined
  if (!ch) return undefined
  const name = (c?: { provider?: string; model?: string }) => {
    if (!c) return undefined
    const model = c.model && c.model !== '(по умолчанию)'
      ? c.model : undefined
    return model || c.provider
  }
  const parts: string[] = []
  const cloud = name(ch.cloud)
  if (cloud) parts.push(`облако: ${cloud}`)
  const local = name(ch.local)
  if (local) parts.push(`локально: ${local}`)
  const country = name(ch['cloud-country'])
  if (country) parts.push(`в стране: ${country}`)
  return parts.join(' · ') || undefined
}

function buildNodes(st: RunStatus | null, extracting: boolean,
                    ex: ExchangeState | null, ab: ArbiterState | null,
                    agents: AgentsConfig,
                    filesCfg: Record<string, FileConfig>,
                    dl: DataState | null,
                    parents: RoleParents = {}): Node[] {
  const s = st?.state || ''
  const frozen = Boolean(st?.freeze_counters)
    && (st?.freeze_problems || []).length === 0
  const extracted = s === 'извлечено' || Boolean(st?.extract_summary)
  const failed = (s.startsWith('отказ') || s.startsWith('красный'))
    && !s.includes('аудит')
  const fullCycle = s === 'сквозной цикл идёт'
  const fcPhase = st?.full_cycle?.phase || ''
  const exRunning = s === 'аудит-цикл идёт'
    || (fullCycle && fcPhase === 'цикл')
  // Фазы обмена с сервера: «аудит» — читает аудитор, «ответы» —
  // отвечает и правит обработчик. Пульс переходит к тому, кто
  // работает (слово владельца 06.09: «фокус должен переходить к
  // обработчику, он должен мигать, потом опять аудитор»).
  const exPhase = exRunning ? (ex?.progress?.phase || '') : ''
  const exAnswering = exRunning && exPhase === 'ответы'
  const exRound = ex?.progress?.round
  const exProgress = ex?.progress
    ? `${ex.progress.done}/${ex.progress.total}` : ''
  // A07 (ревью, находка 3): схема стадий и бейдж читают тот же
  // пересуженный реестром вердикт, что и баннер, — не сырую запись.
  const exVerdict = ex?.verdict ? shownVerdict(ex) : undefined
  const lastRound = ex?.rounds?.length
    ? ex.rounds[ex.rounds.length - 1] : null
  const data: Partial<Record<StageKind, StageNodeData>> = {
    freeze: {
      kind: 'freeze',
      statusNode: (st?.freeze_problems || []).length ? 'error'
        : frozen ? 'done' : 'waiting',
      counters: st?.freeze_counters ? [
        { label: 'клауз', value: String(st.freeze_counters.clauses) },
        { label: 'оригиналов сверено',
          value: String(st.freeze_counters.originals) },
      ] : [],
    },
    worker: {
      kind: 'worker',
      statusNode: extracting || s === 'извлечение идёт' ? 'working'
        : s === 'на паузе: извлечение' ? 'paused'
        : exAnswering ? 'working'
        : extracted ? 'done'
        : failed ? 'error'
        : frozen ? 'idle' : 'waiting',
      statusText: failed ? st?.state
        : s === 'остановлено: извлечение' ? 'остановлено'
        : s === 'извлечение идёт' && st?.progress
          ? `${st.progress.stage}: чанк ${st.progress.done}`
            + `/${st.progress.total}`
        : exAnswering
          ? `обмен ${exRound} · ответы на замечания ${exProgress}`
        : undefined,
      llm: true,
      model: roleModelLine(agents, 'worker', parents)
        ?? (st?.llm_scope ? `допуск: ${st.llm_scope.effective}` : undefined),
      counters: st?.extract_summary ? [
        { label: 'правил', value: String(st.extract_summary.rules) },
        { label: 'вопросов',
          value: String(st.extract_summary.questions) },
        { label: 'отбраковано',
          value: String(st.extract_summary.rejected) },
      ] : s === 'извлечение идёт' && st?.progress ? [
        { label: 'чанков',
          value: `${st.progress.done}/${st.progress.total}` },
        { label: 'правил уже', value: String(st.progress.rules) },
      ] : [],
    },
    artifacts: {
      kind: 'artifacts',
      statusNode: s === 'применение ответов идёт' ? 'working'
        : s === 'на паузе: применение ответов' ? 'paused'
        : s === 'отказ применения ответов'
          || s === 'красный гейт ответов' ? 'error'
        : extracted ? 'done' : 'waiting',
      statusText: s === 'отказ применения ответов'
        ? (st?.error || '').slice(0, 60)
        : s === 'остановлено: применение ответов' ? 'остановлено'
        : undefined,
      counters: st?.answers_summary ? [
        { label: 'ревизия', value: String(st.answers_summary.revision) },
        { label: 'применено', value: String(st.answers_summary.applied) },
        ...(st.answers_summary.awaiting ? [{ label: 'ждёт обработчика',
            value: String(st.answers_summary.awaiting) }] : []),
        ...(st.answers_summary.unparsed ? [{ label: 'не разобрано',
            value: String(st.answers_summary.unparsed) }] : []),
        ...(st.answers_summary.conflicted ? [{ label: 'конфликт пересмотра',
            value: String(st.answers_summary.conflicted) }] : []),
      ] : [],
    },
    auditor: {
      kind: 'auditor',
      llm: true,
      model: roleModelLine(agents, 'auditor', parents),
      // Во время фазы «ответы» работает обработчик — аудитор ждёт его
      // правок (пульс на обработчике, см. exAnswering).
      statusNode: exRunning && !exAnswering ? 'working'
        : exAnswering ? 'idle'
        : s === 'на паузе: аудит-цикл' ? 'paused'
        : exVerdict === 'успех'
          || s.startsWith('сквозной цикл: чисто') ? 'done'
        // «лимит» и «стагнация» — законные исходы одиночного цикла,
        // не ошибка (замечание владельца 2026-08-21).
        : exVerdict ? 'idle'
        : s === 'отказ аудит-цикла' ? 'error'
        : extracted ? 'idle' : 'waiting',
      statusText: exAnswering
        ? `обмен ${exRound} · ждёт ответов обработчика`
        : exRunning && ex?.progress
          ? `обмен ${ex.progress.round} · аудит: `
            + `чанков ${ex.progress.done}/${ex.progress.total}`
        : exVerdict
          ? `вердикт: ${humanVerdict(exVerdict)}`
        : s === 'отказ аудит-цикла' ? st?.error?.slice(0, 60)
        : s === 'остановлено: аудит-цикл' ? 'остановлено'
        : undefined,
      counters: exRunning && ex?.progress ? [
        { label: `обмен ${ex.progress.round} · ${ex.progress.phase}`,
          value: `чанков ${ex.progress.done}/${ex.progress.total}` },
        ...(lastRound ? [{ label: '(Б, К, С) прошлого',
          value: (lastRound.weighty || []).join(', ') }] : []),
        // Тупики видны и ВО ВРЕМЯ работы аудитора (замечание
        // владельца 2026-08-21): счёт из реестра находок — споры,
        // созревшие к прошлому обмену.
        { label: 'тупики',
          value: String(ex?.registry?.['тупик'] ?? 0) },
      ] : lastRound ? [
        // Обменов — против лимита, и причина остановки словами
        // (вопрос владельца 06.09: «2 обмена, а не 3» — цикл
        // закрывается по двум чистым аудитам подряд, лимит — потолок).
        { label: 'обменов',
          value: String(ex!.rounds.length)
            + (ex?.config?.max_rounds
              ? ` из ${ex.config.max_rounds}` : '') },
        { label: '(Б, К, С)',
          value: (lastRound.weighty || []).join(', ') },
        { label: 'тупики',
          value: String(ex?.registry?.['тупик'] ?? 0) },
        ...(exVerdict ? [{ label: 'остановка',
          value: verdictReason(exVerdict) }] : []),
      ] : [],
    },
    arbiter: {
      kind: 'arbiter',
      llm: true,
      model: roleModelLine(agents, 'arbiter', parents),
      statusNode: s === 'арбитраж идёт'
        || (fullCycle && fcPhase.includes('арбитраж')) ? 'working'
        : s === 'на паузе: арбитраж' ? 'paused'
        : ab?.summary ? 'done'
        : s === 'отказ арбитража'
          || s === 'красный гейт арбитража' ? 'error'
        // Цикл завершился без тупиков — арбитру нечего судить, это
        // завершённое состояние, не ожидание (замечание владельца).
        : exVerdict && !(ab?.deadlocks || []).length ? 'done'
        : (ab?.deadlocks || []).length ? 'idle' : 'waiting',
      statusText: s === 'остановлено: арбитраж' ? 'остановлено'
        : !ab?.summary && (ab?.deadlocks || []).length
        ? `тупиков: ${ab!.deadlocks.length}`
        : exVerdict && !ab?.summary ? 'арбитраж не требуется'
        : undefined,
      counters: ab?.summary ? [
        { label: 'обработчик прав',
          value: String(ab.summary.by_verdict['обработчик прав']
                        ?? 0) },
        { label: 'аудитор прав',
          value: String(ab.summary.by_verdict['аудитор прав'] ?? 0) },
        { label: 'в анкету',
          value: String(ab.summary.by_verdict['в анкету'] ?? 0) },
        // A09: спор без решения (дубли/пропуски у арбитра) — не PASS.
        { label: 'не разрешено',
          value: String(ab.summary.unresolved ?? 0) },
      ] : [],
    },
    output: (() => {
      // Итог готов, когда цикл завершён и тупиков нет (либо тупики
      // уже разобраны арбитром): пара «свод + анкета» финальна.
      const ready = Boolean(ex?.verdict)
        && (!(ex!.verdict!.deadlocks || []).length
            || Boolean(ab?.summary))
      return {
        kind: 'output' as const,
        dimmed: !ready,
        statusNode: ready ? ('done' as const) : ('waiting' as const),
        statusText: !ready && exVerdict
          ? 'ждёт арбитража'
          : exVerdict
            ? `вердикт: ${humanVerdict(exVerdict)}` : undefined,
      }
    })(),
    data_checks: {
      kind: 'data_checks',
      llm: true,
      // Линию данных считает роль data_compiler (webui: _channel_for
      // «data_compiler»), наследующая applier — подпись по ней.
      model: dl?.class ? `класс канала: ${dl.class}`
        : roleModelLine(agents, 'data_compiler', parents),
      statusNode: dl?.state === 'проверки идут' ? 'working'
        : dl?.state === 'на паузе: проверки данных' ? 'paused'
        : dl?.summary ? 'done'
        : dl?.state === 'отказ проверок данных'
          || dl?.state === 'красный гейт отчёта данных' ? 'error'
        : (dl?.files || []).length && extracted ? 'idle' : 'waiting',
      statusText: dl?.state === 'отказ проверок данных'
        ? (dl?.error || '').slice(0, 60)
        : dl?.state === 'остановлено: проверки данных' ? 'остановлено'
        : undefined,
      counters: dl?.summary ? [
        { label: 'прогнано', value: String(dl.summary.checks_run) },
        { label: 'не прогнано',
          value: String(dl.summary.checks_skipped) },
        { label: 'находок', value: String(dl.summary.findings) },
      ] : [],
    },
    data_report: {
      kind: 'data_report',
      statusNode: dl?.state === 'сверка идёт' ? 'working'
        : dl?.state === 'на паузе: сверка спорного' ? 'paused'
        : dl?.state === 'отказ сверки спорного'
          || dl?.state === 'красный гейт сверки' ? 'error'
        : dl?.summary
          ? ((dl.gate_problems || []).length ? 'error' : 'done')
          : 'waiting',
      statusText: dl?.state === 'отказ сверки спорного'
        ? (dl?.error || '').slice(0, 60)
        : dl?.state === 'остановлено: сверка спорного' ? 'остановлено'
        : dl?.state === 'спорное сверено' ? 'спорное сверено'
        : undefined,
      llm: Boolean(dl?.summary),
      model: dl?.summary && dl?.class
        ? `сверка: класс ${dl.class}` : undefined,
      counters: dl?.summary ? [
        { label: 'записей', value: String(dl.summary.rows) },
        { label: 'находок', value: String(dl.summary.findings) },
        ...(dl.review_summary ? Object.entries(
          dl.review_summary.by_verdict || {}).map(([k, v]) => (
          { label: k, value: String(v) })) : []),
      ] : [],
    },
  }
  // Пакет трёх 2026-08-30, п.3: зелёный контур завершённых блоков.
  // Слово владельца 07.09.2026: зелёный контур — у КАЖДОГО блока в
  // состоянии «Готово». Прежнее правило (30.08) держало «Обработчик» и
  // «Свод + Анкета» без контура до ВМП (пока анкета не закрыта) — после
  // чистого аудит-цикла Аудитор и Арбитр зеленели, а блоки формализации
  // с бейджем «Готово» нет, и это читалось как незавершённость. Готовность
  // анкеты показывают полоса готовности и блок «Итог», не контур.
  for (const d of Object.values(data)) {
    if (!d || d.statusNode !== 'done') continue
    d.fullyDone = true
  }
  const frames: Node[] = FRAMES.map(f => ({
    id: f.id, type: 'frame', position: { x: f.x, y: f.y },
    data: { label: f.label, w: f.w, h: f.h, color: f.color },
    selectable: false, draggable: true, zIndex: -1,
  }))
  // Блоки-члены группы становятся подузлами рамки (координаты —
  // относительные): блок тянется отдельно, рамка тянет группу.
  const withParent = (n: Node): Node => {
    const pid = MEMBER_OF[n.id]
    if (!pid) return { ...n, draggable: true }
    const f = FRAME_BY_ID[pid]
    return { ...n, parentId: pid, draggable: true,
             position: { x: n.position.x - f.x,
                         y: n.position.y - f.y } }
  }
  // Блок «Документы правил» — первый блок ленты правил, id прежний
  // ('documents'): рёбра и панель деталей продолжают работать.
  const filesNode = withParent({
    id: 'documents', type: 'files', position: POS.documents,
    data: { title: 'Документы правил', files: st?.files || [],
            cfg: filesCfg } as FilesNodeData,
  })
  const dataFilesNode = withParent({
    id: 'data-files', type: 'files', position: POS['data-files'],
    data: { title: 'Операционные данные', files: dl?.files || [],
            cfg: filesCfg } as FilesNodeData,
  })
  const stages: Node[] = ORDER.map(kind => withParent({
    id: kind, type: 'stage', position: POS[kind],
    data: data[kind] as StageNodeData,
  }))
  return [...frames, filesNode, dataFilesNode, ...stages]
}

// Подписи-пилюли на рёбрах — как «Code»/«Audit» на графе эталона.
const _EDGE_LABEL = {
  labelStyle: { fontSize: 11, fontWeight: 600 },
  labelBgPadding: [6, 3] as [number, number],
  labelBgBorderRadius: 8,
}
const _CATALOG_EDGE = { stroke: CATALOG_COLOR, strokeDasharray: '6 4' }
const _CATALOG_ARROW = { type: MarkerType.ArrowClosed,
                         color: CATALOG_COLOR, width: 16, height: 16 }
const _CATALOG_LABEL = { fontSize: 11, fontWeight: 600,
                         fill: CATALOG_COLOR }
const EDGES: Edge[] = [
  { id: 'e1', source: 'documents', target: 'freeze',
    label: 'документы', ..._EDGE_LABEL },
  { id: 'e2', source: 'freeze', target: 'worker', label: 'клаузы',
    ..._EDGE_LABEL },
  { id: 'e3', source: 'worker', target: 'artifacts', label: 'свод',
    ..._EDGE_LABEL },
  { id: 'e4', source: 'artifacts', target: 'auditor', label: 'аудит',
    ..._EDGE_LABEL },
  { id: 'e5', source: 'auditor', target: 'arbiter', label: 'тупики',
    ..._EDGE_LABEL },
  { id: 'e6', source: 'arbiter', target: 'output', label: 'решения',
    ..._EDGE_LABEL },
  // Возврат цикла — управляемым ортогональным маршрутом через нижнюю
  // и верхнюю ручки: свободная кривая справа налево неслась через всю
  // диаграмму поверх других связей (замечание владельца 2026-08-18:
  // «бегущие пунктиры не совпадают с линиями блоков»).
  { id: 'cycle', source: 'auditor', sourceHandle: 'bottom',
    target: 'worker', targetHandle: 'top', type: 'smoothstep',
    animated: true, label: 'обмены', ..._EDGE_LABEL },
  // Лента данных: данные + свод → проверки → отчёт 3.3.
  { id: 'd1', source: 'data-files', target: 'data_checks',
    label: 'данные', ..._EDGE_LABEL },
  { id: 'd2', source: 'artifacts', sourceHandle: 'bottom',
    target: 'data_checks', targetHandle: 'top',
    label: 'против свода', ..._EDGE_LABEL },
  { id: 'd3', source: 'data_checks', target: 'data_report',
    label: 'итог', ..._EDGE_LABEL },
  // Каталог шаблонов — отдельная служба (слово владельца 17.09):
  // пунктир своего цвета, стрелка показывает, кто кого читает —
  // словари и посадка идут Обработчику, записи семей — Своду, заявки
  // на пробелы (посадки C) идут ОТ Итога в каталог.
  { id: 'c1', source: 'catalog', sourceHandle: 'w',
    target: 'worker', targetHandle: 'top', type: 'smoothstep', label: 'словари · посадка',
    style: _CATALOG_EDGE, markerEnd: _CATALOG_ARROW,
    ..._EDGE_LABEL, labelStyle: _CATALOG_LABEL },
  { id: 'c2', source: 'catalog', sourceHandle: 'a',
    target: 'artifacts', targetHandle: 'top', type: 'smoothstep', label: 'семьи',
    style: _CATALOG_EDGE, markerEnd: _CATALOG_ARROW,
    ..._EDGE_LABEL, labelStyle: _CATALOG_LABEL },
  { id: 'c3', source: 'catalog', sourceHandle: 'o',
    target: 'output', targetHandle: 'top', type: 'smoothstep', label: 'заявки',
    style: _CATALOG_EDGE, markerStart: _CATALOG_ARROW,
    ..._EDGE_LABEL, labelStyle: _CATALOG_LABEL },
]

// Заголовок текущего состояния процесса над диаграммой (замечание
// владельца 2026-08-21 вместо легенды цветов и хинта «клик по блоку»):
// активные и финальные состояния показываются как есть, состояния
// готовности — как «Готово к следующему шагу».
function processHeadline(state: string | undefined,
                         customer: string): {
  text: string; tone: 'ok' | 'busy' | 'error' | 'wait'
} {
  const s = state || ''
  if (!s) return { text: 'Загрузка…', tone: 'wait' }
  if (s.includes('идёт') || s.startsWith('на паузе')) {
    return { text: s, tone: 'busy' }
  }
  if (s.startsWith('отказ') || s.startsWith('красный')) {
    return { text: s, tone: 'error' }
  }
  if (isStalledState(s)) {
    // «Остановился, но не чисто» — предупреждение, не успех: дальше
    // арбитраж, сквозной цикл или ответы авиакомпании.
    return { text: humanState(s), tone: 'wait' }
  }
  if (s === 'заморожено') {
    return customer.trim()
      ? { text: 'Готово к формализации (извлечению правил)',
          tone: 'ok' }
      : { text: 'Ожидание ввода параметров — задайте название '
              + 'авиакомпании', tone: 'wait' }
  }
  if (s === 'извлечено') {
    return { text: 'Готово к аудит-циклу', tone: 'ok' }
  }
  return { text: humanState(s), tone: 'ok' }
}

// Подсказки строк метрик и артефактов: полные расшифровки терминов
// (директива владельца 2026-08-20 — подсказки на всех элементах).
const METRIC_TIPS: Record<string, string> = {
  'Правил': 'Строк формализованного свода, извлечённых из документов',
  'Вопросов': 'Всех вопросов действующей анкеты — мест, где документы '
            + 'и данные не дают ответа (включая вопросы линии данных, '
            + 'например об истории сотрудников); в скобках — ещё '
            + 'открытых',
  'Обменов': 'Раундов аудит-цикла «обработчик — аудитор»',
  '(Б, К, С)': 'Весомые замечания последнего обмена: Блокирующие, '
             + 'Критические, Серьёзные',
  'Тупиков рассмотрено': 'Споров ЭТОГО цикла, побывавших у арбитра: '
                       + 'и решённых, и оставшихся без однозначного '
                       + 'решения',
  'Не разрешено арбитром': 'Споров, по которым арбитр после '
                         + 'повторного запроса не дал одного решения '
                         + '(дубль, противоречие или молчание). '
                         + 'Решением это не считается: находка живая, '
                         + '«успех» цикла невозможен — запустите '
                         + 'арбитраж ещё раз',
  'Ждут ответа владельца': 'Споров, переданных вам вопросом анкеты '
                         + '(замок кода запретил заплату): пока вы не '
                         + 'ответили, спор арбитру заново не '
                         + 'предъявляется — решение остаётся за вами',
  'Не исполнено (вопрос владельцу)': 'Решений «аудитор прав», '
                                   + 'которые нельзя исполнить '
                                   + 'заплатой: замок кода её '
                                   + 'запрещает. Спор передан вам '
                                   + 'вопросом анкеты — менять ли '
                                   + 'требование кода',
  'Тупиков': 'Замечания, на которых аудитор настаивает, а обработчик '
           + 'дважды отклонил — ждут арбитра',
  'В анкету': 'Тупиков, переданных арбитром на решение авиакомпании '
            + 'вопросом анкеты',
}

// Подсказки строк блока подробностей арбитража (та же директива
// владельца 2026-08-20: подсказки на всех элементах).
const ARBITER_ROW_TIPS: Record<string, string> = {
  'Тупиков рассмотрено': METRIC_TIPS['Тупиков рассмотрено'],
  'Обработчик прав': 'Тупиков, где арбитр признал правоту '
                   + 'обработчика: строка свода остаётся как есть',
  'Аудитор прав': 'Тупиков, где арбитр велел исполнить замечание '
                + 'аудитора заплатой обработчика',
  'В анкету (решает авиакомпания)': 'Тупиков, где клауза честно '
                                  + 'допускает оба чтения: развилку '
                                  + 'решает авиакомпания вопросом '
                                  + 'анкеты',
  'Не разрешено арбитром': METRIC_TIPS['Не разрешено арбитром'],
  'Ждут ответа владельца': METRIC_TIPS['Ждут ответа владельца'],
  'Не исполнено (вопрос владельцу)':
    METRIC_TIPS['Не исполнено (вопрос владельцу)'],
  'Лишних упоминаний в ответе арбитра': 'Идентификаторов, названных '
    + 'арбитром, но спорами ЭТОГО цикла не являющихся (и записей без '
    + 'формы). В счёт «рассмотрено» они не входят, но и не теряются: '
    + 'гейт арбитража по ним красный — перезапустите арбитраж',
  'Исполнено обработчиком': 'Решений «аудитор прав», по которым '
                          + 'заплата обработчика принята и оставила '
                          + 'след в своде',
  'Вопросов добавлено': 'Вопросов части 2, поставленных арбитражем '
                      + 'авиакомпании или владельцу',
}
const ARTIFACT_TIPS: Record<string, string> = {
  'свод': 'Формализованный свод правил — главный результат обработки '
        + 'документов',
  'анкета': 'Вопросы авиакомпании: развилки, неясности и противоречия '
          + 'документов',
  'анкета-xlsx': 'Excel-книга анкеты для заполнения авиакомпанией: '
               + 'колонка «ОТВЕТ АВИАКОМПАНИИ»; заполненную книгу '
               + 'загружают ниже кнопкой «Загрузить ответы». Всегда '
               + 'текущая ревизия — переиздаётся каждой стадией',
  'модель': 'Машинная модель свода (JSON) — источник всех проекций',
  'отбраковка': 'Кандидаты в правила, отклонённые контрактом с '
              + 'причинами',
  'сводка-ответов': 'Итог применения ответов авиакомпании: что '
                  + 'применено, что ждёт обработчика, что не разобрано',
  'отчёт-данных': 'Отчёт о качестве операционных данных (линия 3.3)',
  'отчёт-данных-модель': 'Машинная модель отчёта о данных (JSON)',
  'сводка-сверки': 'Итог сверки спорных находок моделью',
  'отчёт-сверки-формализации': 'Режим 2: расхождения чужой '
                             + 'формализации с источниками',
  'замечания': 'Пакет замечаний аудитора этого обмена',
  'ответы': 'Ответы обработчика на замечания этого обмена',
}

// Плавающая панель метрик прогона (порт MetricsPanel CodeForge).
function MetricsPanel({ status, exchange, arbiter }: {
  status: RunStatus | null
  exchange: ExchangeState | null
  arbiter: ArbiterState | null
}) {
  const rows: [string, string][] = []
  if (status?.extract_summary) {
    rows.push(['Правил', String(status.extract_summary.rules)])
    // Все вопросы действующей анкеты (в том числе рождённые линией
    // данных — вопрос истории), а не только извлечённые: замечание
    // владельца на репетиции 05.09 («Вопросов 2», а в анкете три).
    const total = status.readiness?.vmp_questions_total
    const open = status.readiness?.vmp_open_questions
    rows.push(['Вопросов', total != null
      ? `${total}${open != null ? ` (открытых ${open})` : ''}`
      : String(status.extract_summary.questions)])
  }
  const lastRound = exchange?.rounds?.length
    ? exchange.rounds[exchange.rounds.length - 1] : null
  if (lastRound) {
    rows.push(['Обменов', String(exchange!.rounds.length)])
    rows.push(['(Б, К, С)', (lastRound.weighty || []).join(', ')])
  }
  if (arbiter?.summary) {
    // A09: summary.deadlocks — рассмотренные споры (решения + не
    // разрешённые); не разрешённые и не исполненные — отдельно.
    rows.push(['Тупиков рассмотрено',
               String(arbiter.summary.deadlocks)])
    if (arbiter.summary.unresolved) {
      rows.push(['Не разрешено арбитром',
                 String(arbiter.summary.unresolved)])
    }
    if (arbiter.summary.awaiting_owner) {
      rows.push(['Ждут ответа владельца',
                 String(arbiter.summary.awaiting_owner)])
    }
    if (arbiter.summary.unenforced) {
      rows.push(['Не исполнено (вопрос владельцу)',
                 String(arbiter.summary.unenforced)])
    }
    rows.push(['В анкету', String(arbiter.summary.added_questions)])
  } else if ((arbiter?.deadlocks || []).length) {
    rows.push(['Тупиков', String(arbiter!.deadlocks.length)])
  }
  if (!rows.length) return null
  return (
    <div className="absolute right-4 top-4 z-10 w-52 rounded-xl border
                    border-cf-border bg-cf-panel/95 p-3 shadow-md
                    backdrop-blur">
      <p className="mb-2 text-xs font-bold uppercase tracking-wider
                    text-cf-text-muted">
        Метрики прогона
      </p>
      <dl className="space-y-1 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between"
               title={METRIC_TIPS[k]}>
            <dt className="text-cf-text-muted">{k}</dt>
            <dd className="font-semibold text-cf-text">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

// Занятость линии прогона — тем же законом, что судит сервер
// (`_line_busy`): «… идёт/идут» и «на паузе …».
function lineIsBusy(state?: string | null): boolean {
  const s = String(state || '')
  return s.endsWith('идёт') || s.endsWith('идут') || s.startsWith('на паузе')
}

// Источник последнего составления словами (журнал действий, не индекс).
function catalogTriggerWords(trigger?: string | null): string {
  return trigger === 'auto' ? 'автоматически по завершении стадии'
    : trigger === 'replay' ? 'повтор прогона'
      : trigger === 'button' ? 'кнопкой аналитика'
        : String(trigger || '—')
}

// Раздел «Пробелы каталога шаблонов (посадки C)» боковой панели блока
// «Итог» (шаг 6 каталога, часть CDPM). Перечень — от индекса прогона
// (заявка живёт дольше строки C) плюс текущие кандидаты; состояние
// каждой заявки читается из каталога, очереди и квитанции доставки.
// Решения по заявкам принимаются на экране службы каталога, не здесь.
function CatalogRequestsSection({ runId, line, runState, busy, onStarted }: {
  runId: string
  /** Побочная линия `catalog_state` из статуса прогона (свежая от опроса). */
  line?: string | null
  /** Основная линия: её смена — повод перечитать перечень (автоподача). */
  runState?: string | null
  /** Основная линия занята: подача требует свободных линий. */
  busy: boolean
  /** Взвести опрос статуса страницы: фоновое составление идёт нитью. */
  onStarted: () => void
}) {
  const [data, setData] = useState<CatalogRequestsState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [docClass, setDocClass] = useState('')
  // Служебная C заявляется только с подтверждением «это пробел
  // каталога, не брак посадки» — строка ждёт ответа в диалоге.
  const [confirmRow, setConfirmRow] = useState<CatalogCandidateRow | null>(null)
  const catalogUrl = useCatalogStore(s => s.url)
  const loadCatalogUrl = useCatalogStore(s => s.load)
  useEffect(() => { loadCatalogUrl() }, [loadCatalogUrl])

  const load = useCallback(async () => {
    try {
      setData(await getCatalogRequests(runId))
      setError(null)
    } catch (e) {
      // Сервер прежней версии без точки заявок — раздела просто нет.
      setData(null)
      setError(String(e))
    }
  }, [runId])
  // Перечень читается при открытии панели и следом за сменой линий —
  // своего опроса раздел не заводит (не чаще опроса страницы).
  useEffect(() => { load() }, [load, line, runState])

  const busyLine = lineIsBusy(line ?? data?.line)
  const disabled = acting || busy || busyLine
  const disabledTip = busyLine ? 'заявки в каталог: идут'
    : busy ? 'идёт другая стадия прогона' : undefined
  const docClasses = data?.document_classes || []
  const effClass = docClass || data?.document_class || ''

  const act = useCallback(async (body: CatalogRequestsBody, words: string) => {
    setActing(true)
    try {
      const got = await postCatalogRequests(runId, body)
      if (body.mode === 'deliver') {
        notify.success(`Доставка в службу каталога: доставлено `
          + `${got.delivered ?? 0}, отказано ${got.refused ?? 0}`
          + (got.note ? ` — ${got.note}` : ''))
      } else {
        notify.success(words)
      }
      onStarted()
      await load()
    } catch (e) {
      // Отказы ручки (409 занято/уже принята, 422 форма, 503 служба) —
      // словами сервера, без пересказа.
      notify.error(String(e))
    } finally {
      setActing(false)
    }
  }, [runId, load, onStarted])

  const claim = (row: CatalogCandidateRow) => act(
    { mode: 'one', rule_ids: [row.rule_id],
      document_class: effClass || undefined,
      confirm_service_c: !row.candidate },
    `Заявка по строке ${row.rule_id}: составление пошло`)

  // Заявленные строки: у кандидата с тем же ключом кнопки «Заявить»
  // нет — кроме строки с несоставленным черновиком (её кнопка
  // составляет заново, как и «Заявить в каталог шаблонов»).
  const claimed = new Map((data?.requests || []).map(r => [r.rule_id, r]))
  const again = (row?: CatalogRequestRow, key?: string) =>
    !row || row.key !== key || row.composed === 'не составлена'
  const rows = data?.candidates || []
  const todo = rows.filter(r => r.candidate && again(claimed.get(r.rule_id),
                                                     r.key))

  if (!data && !error) return null
  if (error && /(^|\s)404(\s|$)/.test(error)) return null

  return (
    <div className="space-y-1.5">
      <h3 className={SECTION_H3}
          title="Строки свода с посадкой C: ни одна семья каталога
шаблонов не покрывает их механику. CDPM составляет обезличенный
черновик записи семьи и подаёт заявку; решения по заявкам — на экране
службы каталога, не здесь.">
        Пробелы каталога шаблонов (посадки C)
      </h3>
      {error ? (
        <p className="text-xs text-cf-error">{error}</p>
      ) : (
        <>
          <p className="text-xs text-cf-text-muted">
            кандидатов {data!.counts.candidates}
            {' · '}заявлено {data!.counts.submitted}
            {' · '}ждут подтверждения {data!.counts.held}
            {data!.counts.pending
              ? ` · без заявки ${data!.counts.pending}` : ''}
          </p>
          {data!.blocked && (
            <p className="text-xs text-cf-warning">{data!.blocked}</p>
          )}
          <p className="text-[11px] text-cf-text-muted">
            автоподача: {data!.auto.enabled ? 'включена' : 'выключена'}
            {data!.auto.skipped_reason
              ? ` — ${data!.auto.skipped_reason}` : ''}
          </p>
          {data!.last_action && (
            <p className="text-[11px] text-cf-text-muted">
              последнее составление:{' '}
              {catalogTriggerWords(data!.last_action.trigger)}
              {data!.last_action.at ? `, ${data!.last_action.at}` : ''}
              {data!.last_action.catalog_state
                ? ` — ${data!.last_action.catalog_state}` : ''}
            </p>
          )}
          {busyLine && (
            <p className="text-[11px] text-cf-warning">
              {line ?? data!.line}
            </p>
          )}
          <p className="text-[11px] text-cf-text-muted"
             title={`очередь заявок: ${data!.channel.queue}`}>
            канал: {data!.channel.words}
          </p>
          <label className="block text-xs text-cf-text-muted">
            Класс документа (для будущих подач)
            <select className="input-field mt-1" value={effClass}
                    disabled={disabled}
                    onChange={e => setDocClass(e.target.value)}>
              {docClasses.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button size="sm" disabled={disabled || !todo.length}
                    title={disabledTip
                      || (todo.length
                        ? 'Составить обезличенные черновики по всем '
                          + 'кандидатам и подать заявки'
                        : 'незаявленных кандидатов нет')}
                    onClick={() => act(
                      { mode: 'all', document_class: effClass || undefined },
                      'Заявки в каталог: составление пошло')}>
              Заявить в каталог шаблонов
            </Button>
            <Button size="sm" variant="secondary"
                    disabled={disabled || !data!.channel.service
                      || !data!.channel.pending.length}
                    title={!data!.channel.service ? data!.channel.words
                      : disabledTip
                        || (data!.channel.pending.length
                          ? `Доставить в службу каталога: `
                            + `${data!.channel.pending.length}`
                          : 'доставлять нечего — очередь прогона пуста')}
                    onClick={() => act({ mode: 'deliver' },
                                       'Доставка в службу каталога')}>
              Доставить в службу
            </Button>
          </div>
          {!rows.length && !data!.requests.length && (
            <p className="text-xs text-cf-text-muted">
              Посадок категории C в своде нет — заявлять нечего.
            </p>
          )}
          <ul className="space-y-2 pt-1">
            {rows.map(row => {
              const req = claimed.get(row.rule_id)
              const reworded = Boolean(req && req.key !== row.key)
              const canClaim = again(req, row.key)
              return (
                <li key={row.rule_id}
                    className="rounded border border-cf-border p-2 text-xs">
                  <p className="font-medium text-cf-text">
                    {row.rule_id}
                    {row.title ? ` · ${row.title}` : ''}
                  </p>
                  <p className="text-cf-text-muted" title={row.statement}>
                    пробел: {row.gap || '—'}
                  </p>
                  {!row.candidate && (
                    <p className="text-cf-text-muted">
                      не кандидат: {row.reason}
                    </p>
                  )}
                  {req ? (
                    <CatalogRequestLines row={req} url={catalogUrl}
                                         reworded={reworded} />
                  ) : (
                    <p className="text-cf-text-muted">не заявлена</p>
                  )}
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {canClaim && (
                      <Button size="sm" variant="secondary"
                              disabled={disabled}
                              title={disabledTip
                                || (row.candidate
                                  ? 'Составить черновик и подать заявку '
                                    + 'по этой строке'
                                  : 'Кандидатом код строку не счёл: '
                                    + 'заявить можно только с подтверждением')}
                              onClick={() => (row.candidate
                                ? claim(row) : setConfirmRow(row))}>
                        Заявить
                      </Button>
                    )}
                    {req && (
                      <CatalogRowActions row={req} disabled={disabled}
                                         disabledTip={disabledTip}
                                         channel={data!.channel}
                                         docClass={effClass} act={act} />
                    )}
                  </div>
                </li>
              )
            })}
            {/* Заявки без строки-кандидата: посадка стала A/B или
                формулировка изменилась — отозвать можно и теперь. */}
            {(data!.requests || [])
              .filter(r => !rows.some(c => c.rule_id === r.rule_id))
              .map(req => (
                <li key={req.request_id}
                    className="rounded border border-cf-border p-2 text-xs">
                  <p className="font-medium text-cf-text">{req.rule_id}</p>
                  <CatalogRequestLines row={req} url={catalogUrl}
                                       reworded={false} />
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <CatalogRowActions row={req} disabled={disabled}
                                       disabledTip={disabledTip}
                                       channel={data!.channel}
                                       docClass={effClass} act={act} />
                  </div>
                </li>
              ))}
          </ul>
        </>
      )}
      <ConfirmDialog
        open={Boolean(confirmRow)}
        title={confirmRow?.service_c
          ? 'Заявить служебную посадку C?'
          : 'Заявить строку, которую код кандидатом не счёл?'}
        message={`Строка ${confirmRow?.rule_id || ''}: ${
          confirmRow?.reason || ''}. Подтвердите, что это пробел каталога, `
          + 'а не брак посадки — иначе заявка засорит каталог шаблонов.'}
        confirmLabel="Это пробел каталога"
        onCancel={() => setConfirmRow(null)}
        onConfirm={() => {
          const row = confirmRow
          setConfirmRow(null)
          if (row) claim(row)
        }} />
    </div>
  )
}

// Строки состояния одной заявки: номер, состояние словами, семья-
// черновик, класс документа, дата подачи, состояние службы с решением
// владельца (K08: происхождение и транспорт — отдельными словами,
// решение они не подменяют), слова доставки и ссылка в службу каталога.
function CatalogRequestLines({ row, url, reworded }: {
  row: CatalogRequestRow
  url: string | null
  /** Формулировка строки свода изменилась после подачи. */
  reworded: boolean
}) {
  // Ссылка ведёт по КАНОНИЧЕСКОМУ номеру — к заявке, которая у службы
  // действительно есть (K08-F3): служба отказом называет уже открытую
  // по этой записи заявку, и решение общей заявки лежит у неё. Прежде
  // ссылка вела по собственному номеру прогона и открывала заявку,
  // которой в каталоге нет.
  const canonical = row.canonical_request_id || row.request_id
  const href = catalogLink(url, `/requests/${encodeURIComponent(canonical)}`)
  const otherNumber = canonical !== row.request_id
  const otherRun = Boolean(row.origin && row.origin !== 'этот прогон')
  // Последнее известное решение ЗАПИСИ каталога: состояние называется
  // только там, где отличается от показанного (иначе слово повторилось
  // бы дважды), а момент берётся у ПОКАЗАННОГО решения — его нет
  // (ответ службы назвал состояние, но не решение) — у последнего
  // известного.
  const lastKnown = row.last_known || null
  const lastKnownWords = lastKnown
    ? (lastKnown.state && lastKnown.state !== row.remote_state
        ? `${lastKnown.state} — ` : '')
      + (lastKnown.decision?.words || lastKnown.decision?.at || 'без слов')
    : ''
  const staleAt = row.decision?.at || lastKnown?.decision?.at || null
  return (
    <>
      <p className="text-cf-text">
        <span className="font-mono">{row.request_id}</span>
        {/* Номера разошлись — конфликт канона: названы ОБА, и принятым
            свой черновик не объявляется. */}
        {otherNumber && (
          <>
            {' · общая заявка '}
            <span className="font-mono">{canonical}</span>
          </>
        )}
        {' · '}{row.state}
      </p>
      <p className="text-[11px] text-cf-text-muted">
        составление: {row.composed || '—'}
        {/* Семья-черновик (`draft.id`, он же `record_id` записи): по
            этому имени заявка ищется в каталоге шаблонов. */}
        {row.record_id ? ' · семья-черновик: ' : ''}
        {row.record_id && (
          <span className="font-mono">{row.record_id}</span>
        )}
        {row.document_class ? ` · класс: ${row.document_class}` : ''}
        {row.submitted_at ? ` · подана ${row.submitted_at}` : ''}
      </p>
      {/* K08 (`request-state/2`): состояние службы и решение владельца
          — отдельно от подписи происхождения и транспорта; подпись
          решение не подменяет. */}
      {(row.remote_state || row.transport_state || otherRun) && (
        <p className="text-[11px] text-cf-text-muted"
           title={'Состояние заявки у службы каталога, решение владельца '
             + 'с моментом, происхождение записи и транспорт — '
             + 'независимые поля'}>
          служба: {row.remote_state || '—'}
          {row.decision?.words ? ` · решение: ${row.decision.words}` : ''}
          {row.decision?.at ? ` (${row.decision.at})` : ''}
          {/* Версия решения до каталога 0.6.0 — сам момент: своё слово
              она получает только там, где отличается от момента. */}
          {row.decision?.version != null
            && String(row.decision.version) !== String(row.decision.at || '')
            ? ` · версия решения ${row.decision.version}` : ''}
          {row.origin ? ` · происхождение: ${row.origin}` : ''}
          {otherRun && row.source_run ? ` — ${row.source_run}` : ''}
          {row.transport_state ? ` · транспорт: ${row.transport_state}` : ''}
        </p>
      )}
      {row.stale && (
        <p className="text-[11px] text-cf-warning"
           title={'Показано последнее известное решение, а не '
             + 'подтверждённое прикладной квитанцией службы; причина — '
             + 'рядом. Решение принадлежит состоянию: ответ службы '
             + 'назвал иное состояние, а решения не назвал — решение '
             + 'записи стоит отдельной строкой, не подменяя новое'}>
          последнее известное решение
          {' — '}{row.stale_reason || 'причина не названа'}
          {lastKnown ? ` · по записи каталога: ${lastKnownWords}` : ''}
          {staleAt ? ` · ${staleAt}` : ''}
        </p>
      )}
      {row.words && (
        <p className="text-[11px] text-cf-warning">{row.words}</p>
      )}
      {reworded && (
        <p className="text-[11px] text-cf-warning">
          формулировка строки изменилась — прежняя заявка остаётся поданной
        </p>
      )}
      {row.delivery && (
        <p className="text-[11px] text-cf-text-muted">
          доставка: {row.delivered ? 'исполнена' : 'не исполнена'}
          {row.delivery.http ? ` · код ${row.delivery.http}` : ''}
          {row.delivery.attempts ? ` · попыток ${row.delivery.attempts}` : ''}
          {row.delivery.words ? ` · ${row.delivery.words}` : ''}
          {row.delivery.refusal ? ` · ${row.delivery.refusal}` : ''}
        </p>
      )}
      {href && (
        <a className="inline-flex items-center gap-1 text-cf-primary
                      hover:underline"
           href={href} target="_blank" rel="noopener noreferrer"
           title={otherNumber
             ? `Открыть ОБЩУЮ заявку ${canonical} на экране службы `
               + 'каталога шаблонов: по этой записи она уже открыта, а '
               + `черновик ${row.request_id} служба не приняла`
             : 'Открыть заявку на экране службы каталога шаблонов'}>
          {otherNumber ? 'открыть общую заявку в каталоге'
            : 'открыть в каталоге'} <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </>
  )
}

// Кнопки строки по состоянию заявки: подтверждение удержанного
// черновика, отзыв до принятия, повторная подача и доставка.
function CatalogRowActions({ row, disabled, disabledTip, channel, docClass,
                             act }: {
  row: CatalogRequestRow
  disabled: boolean
  disabledTip?: string
  channel: CatalogChannel
  docClass: string
  act: (body: CatalogRequestsBody, words: string) => Promise<void>
}) {
  const submitted = row.composed === 'подана'
    || row.composed === 'подтверждена и подана'
  const withdrawn = row.composed === 'отозвана аналитиком'
  const failedDelivery = Boolean(row.delivery) && !row.delivered
  const cls = docClass || undefined
  return (
    <>
      {(row.held || row.composed === 'удержана') && (
        <Button size="sm" disabled={disabled}
                title={disabledTip || 'Подать удержанный черновик как есть: '
                  + 'модель не зовётся'}
                onClick={() => act(
                  { mode: 'confirm', request_ids: [row.request_id],
                    document_class: cls },
                  `Черновик ${row.request_id} подтверждён и подан`)}>
          Подтвердить и подать
        </Button>
      )}
      {submitted && !withdrawn && (
        <Button size="sm" variant="secondary" disabled={disabled}
                title={disabledTip || 'Отозвать заявку, пока каталог её не '
                  + 'принял: файл уходит из очереди'}
                onClick={() => act(
                  { mode: 'withdraw', request_ids: [row.request_id],
                    document_class: cls },
                  `Заявка ${row.request_id} отозвана`)}>
          Отозвать
        </Button>
      )}
      {(withdrawn || failedDelivery) && (
        <Button size="sm" variant="secondary" disabled={disabled}
                title={disabledTip || 'Вернуть заявку в очередь тем же '
                  + 'номером: модель не зовётся'}
                onClick={() => act(
                  { mode: 'resend', request_ids: [row.request_id],
                    document_class: cls },
                  `Заявка ${row.request_id} подана снова`)}>
          Подать снова
        </Button>
      )}
      {submitted && !row.delivered && (
        <Button size="sm" variant="secondary"
                disabled={disabled || !channel.service}
                title={!channel.service ? channel.words
                  : disabledTip || 'Доставить заявку в службу каталога '
                    + '(повтор идемпотентен по номеру)'}
                onClick={() => act(
                  { mode: 'deliver', request_ids: [row.request_id] },
                  `Доставка заявки ${row.request_id}`)}>
          Доставить в службу
        </Button>
      )}
    </>
  )
}

// ── Карточка «Запись о прогоне» (проект «Запись о прогоне», шаг 2) ──
// Запись — одна сущность на обе системы: голова (объявление до старта,
// после старта неизменна), журнал этапов обеих систем и итоги. Панель —
// хранитель файла; хозяин — тот, кто объявил прогон. Карточка только
// читает: ничего не меняет и модели не зовёт.

/** Этапы панели словами; чужие этапы (COSMOS) — как названы. */
const RECORD_STAGE_WORDS: Record<string, string> = {
  freeze: 'заморозка',
  data_freeze: 'заморозка данных',
  data: 'линия данных',
  extract: 'извлечение',
  exchange: 'аудит-цикл',
  answers: 'ответы',
  catalog_requests: 'заявки в каталог',
  files_config: 'политика файлов',
  agents: 'раскладка ролей',
  replay: 'повтор',
}
const recordStageWords = (s: string) => RECORD_STAGE_WORDS[s] || s

/** Этапы ПЛАНА прогона (гейт «Новый прогон») — свой словарь: план
 *  объявляется до старта и называет намерение, а не действие журнала. */
const PLAN_STAGE_WORDS: Record<string, string> = {
  create: 'создание и заморозка',
  data: 'данные',
  extract: 'извлечение',
  data_run: 'линия данных',
  cycle: 'сквозной цикл',
  answers: 'ответы',
  package: 'пакет постановки',
}
const planStageWords = (s: string) => PLAN_STAGE_WORDS[s] || s

/** Номер попытки COSMOS: дата, «T», время и хвост отпечатка. */
const COSMOS_ATTEMPT = /^\d{4}-?\d{2}-?\d{2}[T_-]\d/
/** Этапы COSMOS по названию: расчёты и сверка с исходными документами. */
const COSMOS_STAGE = /пейринг|ростер|sourcecheck|сверка/i

/** Из какой системы запись журнала — словами, для столбца таблицы. */
function recordSystemWords(e: RecordJournalEntry): string {
  if (COSMOS_ATTEMPT.test(String(e.substage || ''))) return 'COSMOS'
  if (COSMOS_STAGE.test(String(e.stage || ''))) return 'COSMOS'
  return 'панель CDPM'
}

/** Цвет исхода: успех — зелёный, предупреждение — жёлтый, отказ по
 *  правилу и сбой — красный, не начат — серый. */
function outcomeClass(outcome?: string | null): string {
  const o = String(outcome || '')
  if (o === 'успех') return 'text-emerald-500'
  if (o === 'предупреждение' || o === 'остановлен человеком') {
    return 'text-amber-500'
  }
  if (o === 'отказ по правилу' || o === 'сбой') return 'text-red-500'
  return 'text-cf-text-muted'
}

/** Секунды словами: до минуты — секундами, дальше — минутами. */
function fmtSeconds(s?: number | null): string {
  if (s === null || s === undefined) return '—'
  if (s < 60) return `${Math.round(s * 10) / 10} с`
  const m = Math.floor(s / 60)
  const rest = Math.round(s - m * 60)
  return rest ? `${m} мин ${rest} с` : `${m} мин`
}

/** Момент времени коротко: день, месяц и часы с минутами. */
function fmtMoment(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(String(v))
  if (Number.isNaN(d.getTime())) return String(v)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getDate())}.${two(d.getMonth() + 1)} `
    + `${two(d.getHours())}:${two(d.getMinutes())}`
}

/** Токены этапа по моделям кратко: «модель: вход/выход»; канал без
 *  учёта расхода — «не считается» (честно, а не ноль). */
function fmtTokens(e: RecordJournalEntry): string {
  const models = e.resources?.models || []
  if (!models.length) return '—'
  return models.map(m => {
    const name = m.model || m.provider || m.role || 'модель'
    if (m.counted === false) return `${name}: не считается`
    const i = m.input_tokens ?? 0
    const o = m.output_tokens ?? 0
    return `${name}: ${i}/${o}`
  }).join('; ')
}

const RECORD_SHORT = (v?: string | null) =>
  v ? `${String(v).slice(0, 12)}…` : '—'

/** Строка головы записи: подпись слева, значение справа. */
function RecordRow({ label, value, title }: {
  label: string; value: ReactNode; title?: string
}) {
  return (
    <div className="flex justify-between gap-2" title={title}>
      <dt className="shrink-0 text-cf-text-muted">{label}</dt>
      <dd className="text-right text-cf-text">{value}</dd>
    </div>
  )
}

function RunRecordCard({ runId, runState }: {
  runId: string
  /** Смена линии прогона — повод перечитать запись (журнал растёт). */
  runState?: string | null
}) {
  const [record, setRecord] = useState<RunRecord | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      // null — записи нет (прогон объявлен до введения записи):
      // честное «нет», а не ошибка.
      setRecord(await getRunRecord(runId))
      setError('')
    } catch (e) {
      // Прочие отказы — словами сервера, без пересказа.
      setRecord(null)
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [runId])
  // Запись читается при открытии панели и следом за сменой линии —
  // своего опроса карточка не заводит.
  useEffect(() => { load() }, [load, runState])

  const d = record?.declaration
  const res = record?.results
  const journal = record?.journal || []

  return (
    <div className="space-y-1.5">
      <h3 className={SECTION_H3}
          title="Единая запись прогона обеих систем (словарь
cosmos-run-record/v1): голова — объявление до старта (после старта
неизменно, отпечаток declaration_sha256), журнал — этапы панели и
COSMOS с хронометражем, исходом и токенами по моделям, итоги —
состояние, вердикты, доставки и признак золотого прогона">
        Запись о прогоне
      </h3>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={busy}
                title="Перечитать запись: журнал растёт по ходу прогона,
записи COSMOS приходят дописыванием"
                onClick={() => { load() }}>
          <RotateCcw className="h-3.5 w-3.5" /> Обновить
        </Button>
        <a
          href={recordDownloadUrl(runId)}
          target="_blank" rel="noreferrer"
          title="Скачать запись файлом run-record.json — рецепт
повторения прогона: голова, журнал и итоги"
          className={clsx('btn-secondary px-2.5 py-1.5 text-xs',
                          !record && 'pointer-events-none opacity-50')}
        >
          <Download className="h-3.5 w-3.5" /> Скачать запись
        </a>
      </div>

      {!record && (
        <p className="text-xs text-cf-text-muted">
          {busy ? 'Запись читается…'
            : error
              ? `Запись прочитать не удалось: ${error}`
              : 'Записи о прогоне нет: прогон объявлен до введения '
                + 'записи либо папка прогона её не содержит.'}
        </p>
      )}

      {record && d && (
        <>
          <dl className="space-y-0.5 text-xs">
            <RecordRow label="номер записи"
                       title={`отпечаток объявления: ${
                         record.declaration_sha256 || 'нет'}`}
                       value={<span className="font-mono">
                         {d.run_id}</span>} />
            <RecordRow label="кто объявил"
                       title="Актор объявления: он же стоит актором у
записей журнала панели"
                       value={d.created_by || '—'} />
            <RecordRow label="цель" value={d.purpose || '—'} />
            <RecordRow label="площадка"
                       value={d.contour?.name || '—'} />
            <RecordRow label="авиакомпания"
                       title="Класс данных решает допуск моделей:
проприетарный — только локальные, если согласия нет"
                       value={`${d.airline?.name || '—'}`
                         + (d.airline?.data_class
                           ? ` · ${d.airline.data_class}` : '')
                         + (d.airline?.llm_scope
                           ? ` · допуск ${d.airline.llm_scope}` : '')} />
            <RecordRow label="план этапов"
                       title="Объявленный путь прогона и этап, после
которого прогон останавливается"
                       value={(d.planned_stages || []).length
                         ? (d.planned_stages || [])
                           .map(planStageWords).join(' → ')
                         : 'не объявлен'} />
            <RecordRow label="остановиться после"
                       value={d.stop_after
                         ? planStageWords(d.stop_after) : '—'} />
            <RecordRow label="ответы на анкеты"
                       value={d.answer_policy || '—'} />
            <RecordRow label="каталог шаблонов"
                       title={`отпечаток каталога: ${
                         d.catalog?.fingerprint || 'нет'}`}
                       value={d.catalog?.version
                         ? `${d.catalog.version} · `
                           + RECORD_SHORT(d.catalog.fingerprint)
                         : 'не объявлен'} />
            <RecordRow label="привязки COSMOS"
                       title={`файл привязок: ${
                         d.bindings?.path || 'не найден'}`}
                       value={d.bindings?.catalog_version
                         ? `каталог ${d.bindings.catalog_version} · `
                           + RECORD_SHORT(d.bindings.catalog_fingerprint)
                         : 'не объявлены'} />
            <RecordRow label="код"
                       title="Коммиты кода обеих систем при объявлении"
                       value={<span className="font-mono">
                         {(d.code?.cdpm_commit || '').slice(0, 12) || '—'}
                       </span>} />
            <RecordRow label="корень дерева"
                       title="Корень дерева прогонов: отпечаток правил и
данных (root_sha256) и отпечаток с ответами на анкеты
(root_answers_sha256) — прогоны с одним корнем сравнимы между собой"
                       value={<span className="font-mono">
                         {RECORD_SHORT(d.lineage?.root_sha256)}
                         {' / '}
                         {RECORD_SHORT(d.lineage?.root_answers_sha256)}
                       </span>} />
          </dl>

          <div className="text-xs" title={MODELS_BOTH_SYSTEMS_TITLE}>
            <div className="text-cf-text-muted">модели по ролям</div>
            {(d.models || []).length === 0 ? (
              <p className="text-cf-text-muted">раскладка не объявлена</p>
            ) : (
              <ul className="space-y-0.5">
                {(d.models || []).map(m => (
                  // Ключ — с системой: роль «сертификатор» у COSMOS и
                  // роль панели могут совпасть именем.
                  <li key={`${m.system || 'cdpm'}/${m.role}/`
                           + `${m.model}/${m.channel}`}
                      className="flex justify-between gap-2">
                    <span className="flex items-baseline gap-1.5">
                      {/* Система строки — серым бейджем перед ролью:
                          чья это модель, видно сразу. */}
                      <span className="rounded bg-cf-hover px-1 py-px
                                       text-[10px] text-cf-text-muted">
                        {modelSystemWords(m.system)}
                      </span>
                      <span className="text-cf-text">{m.role}</span>
                    </span>
                    <span className="text-right text-cf-text-muted">
                      {m.provider ? `${m.provider} / ` : ''}
                      {m.model || '—'}
                      {m.channel ? ` · ${m.channel}` : ''}
                      {m.effort ? ` · усилие ${m.effort}` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {d.models_note && (
              // Неполнота раскладки — теми же словами, что в голове на
              // диске: причины в голове нет (после старта она
              // неизменна), поэтому её здесь и не показываем.
              <p className="pt-1 text-amber-500">{d.models_note}</p>
            )}
          </div>

          <div className="text-xs">
            <div className="text-cf-text-muted">
              журнал прогона ({journal.length})
            </div>
            {journal.length === 0 ? (
              <p className="text-cf-text-muted">
                этапов ещё не было: прогон объявлен, но не шёл
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-[640px] border-collapse text-[11px]">
                  <thead>
                    <tr className="text-left text-cf-text-muted">
                      <th className="py-1 pr-2 font-normal">этап</th>
                      <th className="py-1 pr-2 font-normal">подэтап</th>
                      <th className="py-1 pr-2 font-normal">начало</th>
                      <th className="py-1 pr-2 font-normal"
                          title="Машинное время этапа и время ожидания
человека (у ответов на анкеты — от закрытия предыдущего действия)">
                        машина / ожидание
                      </th>
                      <th className="py-1 pr-2 font-normal">исход</th>
                      <th className="py-1 pr-2 font-normal">слова</th>
                      <th className="py-1 pr-2 font-normal">актор</th>
                      <th className="py-1 pr-2 font-normal">система</th>
                      <th className="py-1 font-normal"
                          title="Расход моделей в токенах: вход/выход.
Канал, который расход не сообщает, честно говорит «не считается»">
                        токены
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {journal.map((e, i) => (
                      <tr key={`${e.stage}/${e.substage}/${i}`}
                          className="border-t border-cf-border align-top">
                        <td className="py-1 pr-2 text-cf-text">
                          {recordStageWords(e.stage)}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {e.substage || '—'}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted"
                            title={e.started_at || ''}>
                          {fmtMoment(e.started_at)}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {fmtSeconds(e.duration_machine_s)}
                          {' / '}
                          {fmtSeconds(e.duration_waiting_human_s)}
                        </td>
                        <td className={clsx('py-1 pr-2 font-medium',
                                            outcomeClass(e.outcome))}
                            title={e.reason_code
                              ? `код причины: ${e.reason_code}` : undefined}>
                          {e.outcome || 'не начат'}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted"
                            title={e.words || ''}>
                          {e.words || '—'}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {e.actor || '—'}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {recordSystemWords(e)}
                        </td>
                        <td className="py-1 text-cf-text-muted">
                          {fmtTokens(e)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="text-xs">
            <div className="text-cf-text-muted">итоги</div>
            <dl className="space-y-0.5">
              <RecordRow label="состояние" value={res?.state || '—'} />
              <RecordRow
                label="хронометраж"
                title="Машинное время, ожидание человека и всего"
                value={`${fmtSeconds(res?.timing?.machine_s)} / `
                  + `${fmtSeconds(res?.timing?.waiting_human_s)} / `
                  + `${fmtSeconds(res?.timing?.total_s)}`} />
              <RecordRow
                label="золотой прогон"
                title="Признак прогона-образца ставится только словом
владельца после приёмки на Prod"
                value={res?.golden?.confirmed
                  ? `подтверждён — ${res.golden.by || 'кем не названо'}`
                    + (res.golden.at ? `, ${fmtMoment(res.golden.at)}` : '')
                  : 'не подтверждён'} />
            </dl>
            {Object.entries(res?.verdicts || {})
              .filter(([, v]) => v !== null && v !== undefined && v !== '')
              .length > 0 && (
              <div className="mt-1">
                <div className="text-cf-text-muted">вердикты</div>
                <dl className="space-y-0.5">
                  {Object.entries(res?.verdicts || {})
                    .filter(([, v]) => v !== null && v !== undefined
                      && v !== '')
                    .map(([k, v]) => (
                      <RecordRow key={k} label={k}
                                 value={typeof v === 'object'
                                   ? JSON.stringify(v).slice(0, 80)
                                   : String(v)} />
                    ))}
                </dl>
              </div>
            )}
            {(res?.deliveries || []).length > 0 && (
              <div className="mt-1">
                <div className="text-cf-text-muted">доставки</div>
                <ul className="space-y-0.5">
                  {(res?.deliveries || []).map((a, i) => (
                    <li key={`${a.path}/${i}`}
                        className="flex justify-between gap-2 font-mono">
                      <span className="truncate" title={a.path || ''}>
                        {a.kind || a.path || '—'}
                      </span>
                      <span className="text-cf-text-muted"
                            title={a.sha256 || ''}>
                        {RECORD_SHORT(a.sha256)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default function RunPage() {
  const { runId = '' } = useParams()
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [exchange, setExchange] = useState<ExchangeState | null>(null)
  const [arbiter, setArbiter] = useState<ArbiterState | null>(null)
  const [dl, setDl] = useState<DataState | null>(null)
  // Файл данных, ожидающий подтверждения удаления: имя держится
  // отдельно от флага открытия, чтобы текст диалога не пустел на
  // уходящей анимации.
  const [dataDeleteFile, setDataDeleteFile] = useState('')
  // Карта постановки (пакет трёх 2026-08-30, п.1): активна при
  // наличии книги данных прогона.
  const [showMap, setShowMap] = useState(false)
  const [dataDeleteOpen, setDataDeleteOpen] = useState(false)
  const askDataDelete = useCallback((file: string) => {
    setDataDeleteFile(file)
    setDataDeleteOpen(true)
  }, [])
  const [selected, setSelected] = useState<StageKind | null>(null)
  const [customer, setCustomer] = useState('')
  const [extracting, setExtracting] = useState(false)
  // Опрос после запуска стадии (гонка на Проде 04.09): первый опрос
  // сразу после POST может застать ПРЕЖНЕЕ состояние — поток сервера
  // ещё не сменил статус, — и страница переставала опрашивать до
  // перезагрузки. После любого запуска опрос держится 30 с независимо
  // от состояния; дальше его ведёт само состояние «… идёт».
  const [pollArmed, setPollArmed] = useState(false)
  const armTimer = useRef<number | null>(null)
  const armPolling = useCallback(() => {
    setPollArmed(true)
    if (armTimer.current) window.clearTimeout(armTimer.current)
    armTimer.current = window.setTimeout(() => setPollArmed(false), 30000)
  }, [])
  const [auditorChannel, setAuditorChannel] = useState('cloud')
  const [workerChannel, setWorkerChannel] = useState('cloud')
  const [maxRounds, setMaxRounds] = useState(5)
  const [agents, setAgents] = useState<AgentsConfig>({})
  const [roleParents, setRoleParents] = useState<RoleParents>({})
  // Квитанция канала ВМП+МГ (кнопка «Передать в COSMOS» в узле «Итог»).
  const [delivery, setDelivery] = useState<DeliveryMark | null>(null)
  const [sending, setSending] = useState(false)
  // Повтор прогона (проект повтора, Р2–Р3): отчёт потомка — по факту
  // вердикта в паспорте; старт повтора уводит на страницу потомка.
  const navigate = useNavigate()
  const [replayReport, setReplayReport] = useState<ReplayReport | null>(null)
  const [replayStarting, setReplayStarting] = useState(false)
  useEffect(() => {
    const verdict = status?.replay?.of?.verdict
    if (!verdict) { setReplayReport(null); return }
    let alive = true
    getReplayReport(runId)
      .then(r => { if (alive) setReplayReport(r.report) })
      .catch(() => { if (alive) setReplayReport(null) })
    return () => { alive = false }
  }, [runId, status?.replay?.of?.verdict])
  const launchReplay = async (mode: 'journal' | 'fresh') => {
    const avail = mode === 'journal' ? status?.replay?.journal
      : status?.replay?.fresh
    if (!avail?.available) return
    if (mode === 'fresh') {
      const est = avail.estimate
      const parts: string[] = []
      if (est?.calls != null) parts.push(`вызовов моделей у источника: ${est.calls}`)
      else parts.push('вызовов моделей у источника: неизвестно (прогон до журнала)')
      if (est?.duration_s != null) {
        parts.push(`длительность стадий источника: ${Math.round(est.duration_s / 60)} мин`)
      }
      if (est?.stages?.length) parts.push(`стадии: ${est.stages.join(', ')}`)
      if (est?.reconstructed) parts.push('цепочка действий восстановлена по артефактам')
      if (!window.confirm(
        'Повтор заново: те же документы и раскладка, модели будут вызваны '
        + 'ещё раз — стоимость как у полного прогона.\n' + parts.join('; ')
        + '.\nЗапустить?')) return
    }
    setReplayStarting(true)
    try {
      const got = await startReplay(runId, mode)
      notify.success(`Повтор запущен: потомок ${got.run}`)
      navigate(`/runs/${got.run}`)
    } catch (e) {
      notify.error(String(e))
    } finally {
      setReplayStarting(false)
    }
  }
  useEffect(() => {
    // Родители наследования ролей — один раз; сервер прежней версии
    // без точки — подписи узлов по своим конфигурациям ролей.
    listRoles()
      .then(rs => setRoleParents(
        Object.fromEntries(rs.map(r => [r.role, r.parent]))))
      .catch(() => { /* сервер без точки перечня ролей */ })
  }, [])
  const [filesCfg, setFilesCfg] =
    useState<Record<string, FileConfig>>({})
  const [configPopup, setConfigPopup] = useState<{
    role: 'worker' | 'auditor' | 'arbiter'; x: number; y: number
  } | null>(null)
  const [filePopup, setFilePopup] = useState<{
    file: string; x: number; y: number
  } | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)

  const pollFails = useRef(0)
  const refresh = useCallback(async () => {
    try {
      const st = await runStatus(runId)
      setStatus(st)
      // Название авиакомпании задаётся на первом экране и хранится в
      // статусе — поле обработчика лишь показывает/правит его.
      if (st.customer) {
        setCustomer(prev => prev.trim() ? prev : st.customer!)
      }
      // «заморожено» сразу после запуска — извлечение ещё не стартовало
      // потоком сервера, флаг не сбрасываем (опрос держит armPolling).
      if (st.state !== 'извлечение идёт' && st.state !== 'заморожено') {
        setExtracting(false)
      }
      setExchange(await exchangeStatus(runId))
      // Точки арбитра и конфигурации могут отсутствовать у сервера
      // прежней версии — страница не сыпет тостами при опросе.
      try {
        setArbiter(await arbiterStatus(runId))
      } catch { /* сервер без точки арбитра */ }
      try {
        setDelivery(await getDelivery(runId))
      } catch { /* сервер без точки квитанции канала */ }
      try {
        setAgents(await getAgents(runId))
      } catch { /* сервер без точки конфигурации */ }
      try {
        setFilesCfg((await getFilesConfig(runId)).files)
      } catch { /* сервер без точки политики файлов */ }
      try {
        setDl(await dataStatus(runId))
      } catch { /* сервер без линии данных */ }
      pollFails.current = 0
    } catch (e) {
      // Разовый сорванный опрос (моргнувшее расширение, обрыв сети)
      // не заслуживает красного тоста — поллинг сам повторится через
      // 1,5 с (живой прогон нано 31.08); кричим только о ПОЛОСЕ.
      pollFails.current += 1
      if (pollFails.current === 4) notify.error(String(e))
    }
  }, [runId])

  useEffect(() => { refresh() }, [refresh])
  const busyState = extracting
    || status?.state === 'извлечение идёт'
    || status?.state === 'аудит-цикл идёт'
    || status?.state === 'арбитраж идёт'
    || status?.state === 'сквозной цикл идёт'
    || status?.state === 'применение ответов идёт'
    || status?.state === 'сверка формализации идёт'
    || dl?.state === 'проверки идут'
    || dl?.state === 'сверка идёт'
  // Пауза (ПС-1) — тоже «живое» состояние: страница продолжает опрос,
  // панель управления остаётся в шапке до продолжения или остановки.
  const paused = (status?.state || '').startsWith('на паузе')
    || (dl?.state || '').startsWith('на паузе')
  // Линия данных занята: идёт или стоит на паузе её стадия — состав
  // данных менять нельзя (сервер отвечает 409).
  const dataLineBusy = dl?.state === 'проверки идут'
    || dl?.state === 'сверка идёт'
    || (dl?.state || '').startsWith('на паузе')
  // Побочная линия заявок в каталог шаблонов: составление идёт нитью
  // сервера — страница опрашивает статус тем же чередом, что у прочих
  // линий, иначе «заявки в каталог идут» висело бы до перезагрузки.
  const catalogLineBusy = lineIsBusy(status?.catalog_state)
  useEffect(() => {
    if (!busyState && !paused && !pollArmed && !catalogLineBusy) return
    const t = setInterval(refresh, 1500)
    return () => clearInterval(t)
  }, [busyState, paused, pollArmed, catalogLineBusy, refresh])

  const openConfig = useCallback(
    (role: 'worker' | 'auditor' | 'arbiter', e: React.MouseEvent) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      setConfigPopup({
        role,
        x: rect ? e.clientX - rect.left + 12 : 40,
        y: rect ? e.clientY - rect.top + 8 : 40,
      })
    }, [])

  const openFileConfig = useCallback(
    (file: string, e: React.MouseEvent) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      setFilePopup({
        file,
        x: rect ? e.clientX - rect.left + 12 : 40,
        y: rect ? e.clientY - rect.top + 8 : 40,
      })
    }, [])

  // «Передать в COSMOS» (владелец на репетиции 05.09: «где
  // онлайн-передача, я её не вижу»): та же ручка канала
  // task-package-send — сборка, собственный гейт, доставка приёмнику;
  // квитанция остаётся в панели (и переживает перезагрузку страницы —
  // читается из channel/delivery.json).
  const sendNow = useCallback(async () => {
    setSending(true)
    try {
      const mark = await sendTaskPackage(runId)
      setDelivery(mark)
      if (mark.sent) {
        notify.success(`Пакет передан в COSMOS: ${mark.verdict || 'OK'}`)
      } else {
        notify.error(`Пакет не передан: ${mark.detail || 'канал недоступен'}`)
      }
    } catch (e) {
      notify.error(`Передача не удалась: ${(e as Error).message || e}`)
    } finally {
      setSending(false)
    }
  }, [runId])

  // Обработчики стадий объявлены ниже по тексту компонента; панель
  // действий над блоками зовёт их через ссылку, заполняемую каждым
  // рендером перед отрисовкой (клик всегда позже рендера).
  const handlersRef = useRef<Partial<StageHandlers>>({})

  // Узел «Каталог шаблонов»: та же проба и тот же адрес службы, что у
  // строки в меню (catalogStore), — своей правды у узла нет.
  const catalogProbe = useCatalogStore(s => s.probe)
  const catalogServiceUrl = useCatalogStore(s => s.url)
  const catalogHref = catalogLink(catalogServiceUrl, '/')

  const computedNodes = useMemo(() => {
    const base = buildNodes(status, extracting, exchange, arbiter,
                            agents, filesCfg, dl, roleParents)
    const cr = status?.catalog_requests
    const catalogNode: Node = {
      id: 'catalog', type: 'catalog', position: POS_CATALOG,
      draggable: true,
      data: {
        version: catalogProbe?.version ?? null,
        fingerprint: catalogProbe?.fingerprint ?? null,
        available: catalogProbe ? catalogProbe.available : null,
        words: catalogProbe?.words ?? catalogProbe?.error ?? null,
        href: catalogHref,
        ...(cr && !cr.error && typeof cr.submitted === 'number'
          ? { submitted: cr.submitted,
              gaps: status?.principles?.P2?.fit?.C }
          : {}),
      } as CatalogNodeData,
    }
    const s = status?.state || ''
    const extracted = Boolean(status?.extract_summary)
    const h = handlersRef
    const busyReason = 'идёт стадия — дождитесь или остановите её'
    // Действия блока над его правым верхним краем (слово владельца
    // 06.09): те же обработчики и те же условия, что у кнопок в
    // панели деталей; недоступность — с причиной в подсказке.
    const stageActions = (id: string, statusNode: string):
        NodeAction[] => {
      if (statusNode === 'working' || statusNode === 'paused') {
        return [
          statusNode === 'paused'
            ? { id: 'resume', label: 'Продолжить с контрольной точки',
                onClick: () => { void h.current.sendControl?.('resume') } }
            : { id: 'pause',
                label: 'Пауза на ближайшей контрольной точке',
                onClick: () => { void h.current.sendControl?.('pause') } },
          { id: 'stop', label: 'Остановить стадию (подтверждение в шапке)',
            onClick: () => {
              h.current.setConfirmStop?.(true)
              notify.success('Подтвердите остановку в шапке страницы')
            } },
        ]
      }
      switch (id) {
        case 'freeze':
          return [{ id: 'through',
            label: 'Прогон целиком: извлечение и сквозной цикл',
            onClick: () => { void h.current.launchWhole?.() },
            disabled: busyState || paused
              || !(s === 'заморожено' || extracted),
            reason: busyState || paused ? busyReason
              : 'документы ещё не заморожены' }]
        case 'worker':
          return [
            { id: 'play', label: 'Извлечь правила',
              onClick: () => { void h.current.launch?.() },
              disabled: busyState || paused,
              reason: busyReason },
            { id: 'through',
              label: 'Отсюда до конца: извлечение и сквозной цикл',
              onClick: () => { void h.current.launchWhole?.() },
              disabled: busyState || paused, reason: busyReason },
          ]
        case 'auditor':
          return [
            { id: 'play', label: 'Запустить один аудит-цикл',
              onClick: () => { void h.current.launchExchange?.() },
              disabled: busyState || paused || !extracted,
              reason: !extracted ? 'сначала извлечение правил'
                                 : busyReason },
            { id: 'through',
              label: 'Сквозной цикл до чистоты по Б и К',
              onClick: () => { void h.current.launchFullCycle?.() },
              disabled: busyState || paused || !extracted,
              reason: !extracted ? 'сначала извлечение правил'
                                 : busyReason },
          ]
        case 'arbiter':
          return [{ id: 'play', label: 'Арбитраж тупиков цикла',
            onClick: () => { void h.current.launchArbiter?.() },
            disabled: busyState || paused
              || !(arbiter?.deadlocks || []).length,
            reason: (arbiter?.deadlocks || []).length
              ? busyReason : 'тупиков для арбитража нет' }]
        case 'output':
          return [{ id: 'send', label: 'Передать в COSMOS',
            onClick: () => { void h.current.sendNow?.() },
            disabled: sending || busyState || paused || !extracted
              || !status?.readiness || status.readiness.state === 'а',
            reason: sending ? 'передаётся'
              : busyState || paused ? busyReason
              : !extracted ? 'сначала извлечение правил'
              : 'данных пока недостаточно — ответьте на вопросы анкеты' }]
        case 'data_checks':
          return [{ id: 'play', label: 'Прогнать проверки данных',
            onClick: () => { void h.current.launchDataRun?.() },
            disabled: busyState || paused || !(dl?.files || []).length
              || !extracted,
            reason: !(dl?.files || []).length
              ? 'сначала загрузите книгу данных'
              : !extracted ? 'сначала извлечение правил' : busyReason }]
        case 'data_report':
          return [{ id: 'play', label: 'Сверка спорных находок',
            onClick: () => { void h.current.launchDataReview?.() },
            disabled: busyState || paused || !dl?.summary?.findings,
            reason: !dl?.summary ? 'сначала проверки данных'
              : !dl.summary.findings ? 'спорных находок нет'
              : busyReason }]
        default:
          return []
      }
    }
    const withActions = (n: Node) => {
      const d = n.data as StageNodeData
      if (n.type !== 'stage' || !d?.kind) return n
      const actions = stageActions(n.id, String(d.statusNode))
      // Панель видна всегда у ближайшего доступного шага и у идущей
      // стадии (пауза и стоп под рукой), у остальных — при наведении.
      const live = d.statusNode === 'working' || d.statusNode === 'paused'
      const ready = (d.statusNode === 'idle' || d.statusNode === 'waiting')
        && actions.some(a => !a.disabled
                             && (a.id === 'play' || a.id === 'send'))
      return { ...n, data: { ...d, actions, toolbarAlways: live || ready } }
    }
    return [catalogNode, ...base.map(n =>
      n.id === 'worker' || n.id === 'auditor' || n.id === 'arbiter'
        ? withActions({ ...n, data: { ...n.data,
            onConfigClick: (e: React.MouseEvent) =>
              openConfig(n.id as 'worker' | 'auditor' | 'arbiter',
                         e) } })
        : n.id === 'documents'
          ? { ...n, data: { ...n.data, onFileGear: openFileConfig } }
        : n.id === 'data-files'
          ? { ...n, data: { ...n.data, onFileGear: openFileConfig,
              // Пока идёт или стоит на паузе стадия линии данных,
              // сервер ответит 409 — кнопка не предлагается.
              onFileDelete: dataLineBusy ? undefined : askDataDelete,
              // Загрузка книги прямо на блок — перетаскиванием или
              // выбором файла, как у документов правил на форме
              // нового прогона (слово владельца 06.09: «должно быть
              // единообразие»); панель деталей остаётся вторым входом.
              onUpload: dataLineBusy ? undefined
                : (list: FileList) => { void h.current.uploadData?.(list) },
              accept: '.xlsx,.csv,.json',
              uploadHint: 'Перетащите таблицы xlsx / csv / json сюда '
                + 'или нажмите, чтобы выбрать — заморозка снимет SHA-256',
              emptyHint: dataLineBusy
                ? 'файлов пока нет — загрузка недоступна, пока идёт '
                  + 'стадия линии данных'
                : undefined } }
        : n.id === 'f-data'
          // Карта сети и персонал — кнопка КОНТУРА «Линия данных»
          // (слово владельца 06.09), доступна после заморозки книги.
          ? { ...n, data: { ...n.data, action: {
              label: 'Карта сети и персонал',
              onClick: () => setShowMap(true),
              disabled: !dl?.files?.length,
              tip: dl?.files?.length
                ? 'Схема сети станций и рейсов, персонал по базам и '
                  + 'должностям, карта мира при полных координатах — '
                  + 'по замороженной книге, без модели'
                : 'Карта появится после заморозки книги операционных '
                  + 'данных' } as FrameAction } }
          : withActions(n))]
  }, [status, extracting, exchange, arbiter, agents, filesCfg, dl,
      roleParents, openConfig, openFileConfig, dataLineBusy,
      askDataDelete, busyState, paused, sending,
      catalogProbe, catalogHref])

  // Управляемый граф обязан применять изменения (в т.ч. ПРОМЕРЫ
  // узлов) — без этого рёбра не строятся вовсе: позиции концов
  // неизвестны. Обновления данных вливаются с сохранением промеров И
  // позиций (блоки и группы перетаскиваются пользователем); геометрия
  // рамок тоже клиентская — пересчитывается после переноса блока.
  const [nodes, setNodes, onNodesChange] = useNodesState(computedNodes)
  const [edges, , onEdgesChange] = useEdgesState(EDGES)
  useEffect(() => {
    setNodes(prev => computedNodes.map(n => {
      const old = prev.find(p => p.id === n.id)
      if (!old) return n
      if (n.type === 'frame') {
        // Геометрия рамки — клиентская (пересчитывается после переноса
        // блока), а кнопка контура — из свежих данных: иначе после
        // первой отрисовки она застывала бы недоступной.
        return { ...old, ...n, position: old.position,
                 data: { ...old.data,
                         action: (n.data as { action?: FrameAction })
                           .action },
                 measured: old.measured }
      }
      return { ...old, ...n, position: old.position,
               measured: old.measured }
    }))
  }, [computedNodes, setNodes])

  // Перенос блока из группы: рамка группы перерисовывается по новому
  // положению блоков (замечание владельца 2026-08-18).
  const onNodeDragStop = useCallback(
    (_e: unknown, node: Node) => {
      const pid = node.parentId
      if (!pid) return
      setNodes(nds => {
        const frame = nds.find(n => n.id === pid)
        if (!frame) return nds
        const kids = nds.filter(n => n.parentId === pid)
        const pos = (n: Node) =>
          n.id === node.id ? node.position : n.position
        const minX = Math.min(...kids.map(k => pos(k).x))
        const minY = Math.min(...kids.map(k => pos(k).y))
        const maxX = Math.max(...kids.map(
          k => pos(k).x + (k.measured?.width ?? 220)))
        const maxY = Math.max(...kids.map(
          k => pos(k).y + (k.measured?.height ?? 140)))
        const dx = minX - FIT_PAD
        const dy = minY - FIT_TOP
        const w = maxX - minX + FIT_PAD * 2
        const h = maxY - minY + FIT_TOP + FIT_PAD
        return nds.map(n => {
          if (n.id === pid) {
            return { ...n,
                     position: { x: n.position.x + dx,
                                 y: n.position.y + dy },
                     data: { ...n.data, w, h } }
          }
          if (n.parentId === pid) {
            return { ...n, position: { x: pos(n).x - dx,
                                       y: pos(n).y - dy } }
          }
          return n
        })
      })
    }, [setNodes])

  const onNodeClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (node.type === 'catalog') {
        // Каталог — отдельная служба: клик ведёт в её панель, а не в
        // панель деталей стадии; без настройки — честные слова.
        const href = (node.data as CatalogNodeData).href
        if (href) window.open(href, '_blank', 'noopener')
        else notify.error('служба каталога не настроена (CREW_CATALOG_URL)')
        return
      }
      if (node.type === 'stage' || node.type === 'files') {
        setSelected(node.id as StageKind)
      }
    }, [])

  // Панель деталей закрывается клавишей Escape (замечание владельца:
  // пустая панель закрывала полдиаграммы — теперь она выдвижная).
  useEffect(() => {
    if (!selected) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  // УИ-13: редактируемое название прогона (решение владельца
  // 2026-08-20). Пустое название = снова «Прогон run-NNNN».
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const saveTitle = async () => {
    setEditingTitle(false)
    try {
      await setRunTitle(runId, titleDraft.trim())
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  // Панель Пауза/Продолжить/Остановить (ПС-1). Остановка — только с
  // подтверждением: инлайн-вопрос вместо системного окна.
  const [confirmStop, setConfirmStop] = useState(false)
  useEffect(() => {
    if (!busyState && !paused) setConfirmStop(false)
  }, [busyState, paused])
  const sendControl = async (cmd: 'pause' | 'resume' | 'stop') => {
    try {
      await runControl(runId, cmd)
      notify.success(cmd === 'pause'
        ? 'Пауза запрошена — сработает на ближайшей контрольной точке'
        : cmd === 'resume' ? 'Обработка продолжается'
        : 'Остановка запрошена')
      setConfirmStop(false)
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const launch = async () => {
    if (!customer.trim()) {
      notify.error('Укажите название авиакомпании')
      return
    }
    try {
      setExtracting(true)
      await startExtract(runId, customer)
      armPolling()
      notify.success('Извлечение запущено')
    } catch (e) {
      setExtracting(false)
      notify.error(String(e))
    }
  }

  const launchExchange = async (continueFromStop = false) => {
    try {
      await startExchange(runId, auditorChannel, workerChannel,
                          maxRounds, continueFromStop)
      armPolling()
      notify.success(continueFromStop
        ? 'Аудит продолжен' : 'Аудит-цикл запущен')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const [maxMegacycles, setMaxMegacycles] = useState(3)
  const launchFullCycle = async () => {
    try {
      await startFullCycle(runId, auditorChannel, workerChannel,
                           maxRounds, maxMegacycles)
      armPolling()
      notify.success('Сквозной цикл запущен')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  // «Прогон целиком» (замечание владельца 2026-08-21: кнопки не было
  // видно): с текущего места — извлечение (если его ещё не было) и
  // затем сквозной цикл с лимитами из панели аудит-цикла.
  const pipelineRef = useRef(false)
  const launchWhole = async () => {
    // Refuse before extraction too, not only when the deferred cycle starts.
    try {
      validateCycleLimits(maxRounds, maxMegacycles)
    } catch (e) {
      notify.error(String(e))
      return
    }
    const s = status?.state || ''
    if (s === 'заморожено') {
      if (!customer.trim()) {
        notify.error('Укажите название авиакомпании — поле на первом '
          + 'экране или в панели обработчика')
        return
      }
      pipelineRef.current = true
      await launch()
      notify.success('Прогон целиком: после извлечения сам начнётся '
        + 'сквозной цикл')
    } else {
      await launchFullCycle()
    }
  }
  useEffect(() => {
    if (pipelineRef.current && status?.state === 'извлечено') {
      pipelineRef.current = false
      launchFullCycle()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.state])

  const launchArbiter = async () => {
    try {
      await startArbiter(runId)
      armPolling()
      notify.success('Арбитраж запущен')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const thirdPartyInputRef = useRef<HTMLInputElement>(null)
  const onThirdPartyFile = async (list: FileList | null) => {
    const file = list?.[0]
    if (!file) return
    try {
      const got = await uploadThirdParty(runId, file)
      notify.success(`Файл принят: ${got.file}, утверждений `
        + `${got.claims}`)
      refresh()
    } catch (e) {
      notify.error(String(e))
    } finally {
      if (thirdPartyInputRef.current)
        thirdPartyInputRef.current.value = ''
    }
  }
  const launchMode2 = async () => {
    try {
      await startMode2(runId)
      armPolling()
      notify.success('Сверка формализации запущена')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const answersInputRef = useRef<HTMLInputElement>(null)
  const onAnswersFile = async (list: FileList | null) => {
    const file = list?.[0]
    if (!file) return
    try {
      const got = await uploadAnswers(runId, file)
      notify.success(`Ответы приняты: ${got.received}, ревизия `
        + `${got.revision} — применяются`)
      refresh()
    } catch (e) {
      notify.error(String(e))
    } finally {
      if (answersInputRef.current) answersInputRef.current.value = ''
    }
  }

  const dataInputRef = useRef<HTMLInputElement>(null)
  const onDataFiles = async (list: FileList | null) => {
    if (!list?.length) return
    try {
      const got = await uploadData(runId, Array.from(list))
      if (got.problems?.length) notify.error(got.problems.join('; '))
      else notify.success(`Данные заморожены: ${got.state}`)
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const confirmDataDelete = async () => {
    const file = dataDeleteFile
    setDataDeleteOpen(false)
    if (!file) return
    try {
      const got = await deleteDataFile(runId, file)
      if (got.problems?.length) notify.error(got.problems.join('; '))
      else notify.success(
        got.files.length
          ? `Файл ${file} удалён; данные пере-заморожены: `
            + `файлов ${got.files.length}`
          : `Файл ${file} удалён; данных в прогоне больше нет`)
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const launchDataRun = async () => {
    try {
      await startDataRun(runId)
      armPolling()
      notify.success('Проверки данных запущены')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  const launchDataReview = async () => {
    try {
      await startDataReview(runId)
      armPolling()
      notify.success('Сверка спорного запущена')
      refresh()
    } catch (e) {
      notify.error(String(e))
    }
  }

  // Обработчики для панели действий над блоками — свежие на каждом
  // рендере (см. handlersRef выше).
  handlersRef.current = {
    launch, launchExchange, launchFullCycle, launchWhole, launchArbiter,
    launchDataRun, launchDataReview, sendNow, sendControl, setConfirmStop,
    uploadData: onDataFiles,
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      <div className="flex flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b
                           border-cf-border bg-cf-panel px-4 py-3">
          {editingTitle ? (
            <input
              autoFocus
              className="input-field w-72 text-base font-semibold"
              value={titleDraft}
              placeholder={`Прогон ${runId}`}
              title="Введите название; Enter — сохранить, Escape —
отменить; пустое поле возвращает номер прогона"
              onChange={e => setTitleDraft(e.target.value)}
              onBlur={saveTitle}
              onKeyDown={e => {
                if (e.key === 'Enter') saveTitle()
                if (e.key === 'Escape') setEditingTitle(false)
              }}
            />
          ) : (
            <h1 className="flex items-center gap-1.5 text-base
                           font-semibold text-cf-text"
                title={status?.title
                  ? `${status.title} (${runId})` : undefined}>
              {status?.title || `Прогон ${runId}`}
              {status?.title && (
                <span className="text-xs font-normal
                                 text-cf-text-muted">
                  {runId}
                </span>
              )}
              <button
                aria-label="Переименовать прогон"
                title="Переименовать прогон (название хранится в
прогоне; пустое название возвращает номер)"
                className="rounded p-1 text-cf-text-muted
                           transition-colors hover:bg-cf-hover
                           hover:text-cf-text"
                onClick={() => {
                  setTitleDraft(status?.title || '')
                  setEditingTitle(true)
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
            </h1>
          )}
          {/* Бейджи состояния и готовности из шапки убраны
              (замечание владельца 2026-08-21): состояние показывает
              заголовок над диаграммой, готовность данных — список
              прогонов и панели блоков. Здесь остаются только
              кнопки действий. */}
          {!busyState && !paused && (
            <Button variant="ghost" size="sm"
                    title="Сбросить прогон к исходному состоянию:
исходные файлы, заморозка, модели блоков, политика файлов и название
остаются; результаты извлечения, циклов, арбитража и линии данных
удаляются"
                    onClick={async () => {
                      if (!window.confirm(
                        'Сбросить прогон? Результаты всех стадий '
                        + 'будут удалены; файлы и настройки моделей '
                        + 'останутся.')) return
                      try {
                        await resetRun(runId)
                        notify.success('Прогон сброшен к исходному '
                          + 'состоянию')
                        setSelected(null)
                        refresh()
                      } catch (e) {
                        notify.error(String(e))
                      }
                    }}>
              <RotateCcw className="h-4 w-4" /> Сброс
            </Button>
          )}
          {!busyState && !paused && status?.replay && (
            <>
              {/* Слова причины у ДОСТУПНОЙ кнопки — предупреждение, не
                  запрет: так приходит `dictionary-order` (журнал записан
                  прежним порядком словаря каталога). Оно и в подсказке,
                  и строкой под кнопкой — иначе владелец узнаёт о
                  расхождении только из отчёта потомка. */}
              <div className="flex flex-col items-start">
                <Button variant="ghost" size="sm"
                        disabled={replayStarting || !status.replay.journal.available}
                        title={status.replay.journal.available
                          ? 'Повтор из журнала: новый прогон-потомок с теми же '
                            + 'документами и раскладкой; ответы моделей берутся '
                            + 'из журнала этого прогона (модели не вызываются, '
                            + 'сети не нужно); ожидание — артефакты байт в байт, '
                            + 'расхождение называется словами'
                            + (status.replay.journal.reason_words
                               ? '; ' + status.replay.journal.reason_words : '')
                          : 'Повтор из журнала недоступен: '
                            + (status.replay.journal.reason_words
                               || status.replay.journal.reason_code || '')}
                        onClick={() => launchReplay('journal')}>
                  <RotateCcw className="h-4 w-4" /> Повтор из журнала
                </Button>
                {status.replay.journal.available
                  && status.replay.journal.reason_words && (
                  <p className="max-w-xs text-[11px] leading-tight
                                text-cf-warning"
                     title={status.replay.journal.reason_words}>
                    {status.replay.journal.reason_words}
                  </p>
                )}
              </div>
              <Button variant="ghost" size="sm"
                      disabled={replayStarting || !status.replay.fresh.available}
                      title={status.replay.fresh.available
                        ? 'Повтор заново: те же документы и раскладка, модели '
                          + 'вызываются ещё раз (стоимость — как у полного '
                          + 'прогона, будет подтверждение с оценкой); сравнение '
                          + 'по итогам, не по байтам'
                          + (status.replay.fresh.reason_words
                             ? '; ' + status.replay.fresh.reason_words : '')
                        : 'Повтор заново недоступен: '
                          + (status.replay.fresh.reason_words
                             || status.replay.fresh.reason_code || '')}
                      onClick={() => launchReplay('fresh')}>
                <Play className="h-4 w-4" /> Повтор заново
              </Button>
            </>
          )}
          {(status?.state || '').startsWith('аудит остановлен') && (
            <Button size="sm" className="ml-auto"
                    title="Блокирующих и критических нет, остались
только серьёзные — цикл ждёт вашего решения. «Продолжить» сделает
следующие обмены; не продолжать — переходите к арбитражу, ответам
авиакомпании или сквозному циклу"
                    onClick={() => launchExchange(true)}>
              <Play className="h-4 w-4" /> Продолжить аудит
            </Button>
          )}
          {!busyState && !paused
            && ['заморожено', 'извлечено'].includes(status?.state || '')
            && (
            <Button size="sm" className="ml-auto"
                    title="Запустить конвейер с текущего места одной
кнопкой: извлечение (если его ещё не было) и сквозной цикл
«обработчик — аудитор — арбитр» до чистоты по блокирующим и
критическим либо до лимита кругов. Лимиты — в панели блока
аудит-цикла"
                    onClick={launchWhole}>
              <Play className="h-4 w-4" /> Прогон целиком
            </Button>
          )}
          {(busyState || paused) && (
            <div className="ml-auto flex items-center gap-2">
              {paused ? (
                <Button variant="secondary" size="sm"
                        title="Снять паузу — стадия продолжит с той
контрольной точки, где остановилась"
                        onClick={() => sendControl('resume')}>
                  <Play className="h-4 w-4" /> Продолжить
                </Button>
              ) : (
                <Button variant="secondary" size="sm"
                        title="Приостановить на ближайшей контрольной
точке (между чанками или раундами); идущий запрос к модели дорабатывает
до точки"
                        onClick={() => sendControl('pause')}>
                  <Pause className="h-4 w-4" /> Пауза
                </Button>
              )}
              {confirmStop ? (
                <span className="flex items-center gap-2 rounded-lg
                                 border border-red-400/50
                                 bg-red-500/10 px-2 py-1 text-sm
                                 text-cf-text">
                  Точно остановить?
                  <Button variant="danger" size="sm"
                          title="Остановить стадию на контрольной точке.
Сделанная работа сохраняется на диске; повторный запуск продолжит с
неё"
                          onClick={() => sendControl('stop')}>
                    Да, остановить
                  </Button>
                  <Button variant="ghost" size="sm"
                          title="Не останавливать — стадия продолжает
работу"
                          onClick={() => setConfirmStop(false)}>
                    Отмена
                  </Button>
                </span>
              ) : (
                <Button variant="danger" size="sm"
                        title="Остановить идущую стадию (с
подтверждением). Это не отказ: результат до точки останова сохраняется"
                        onClick={() => setConfirmStop(true)}>
                  <Square className="h-4 w-4" /> Остановить
                </Button>
              )}
            </div>
          )}
        </header>
        {status?.replay?.of && (
          <div className={clsx(
            'flex flex-wrap items-center gap-3 border-b px-4 py-2.5',
            status.replay.of.verdict === 'совпадает'
              || status.replay.of.verdict === 'итоги совпадают'
              ? 'border-emerald-500/30 bg-emerald-500/10'
              : status.replay.of.verdict
                ? 'border-cf-warning/30 bg-cf-warning/10'
                : 'border-cf-border bg-cf-surface')}
               title="Этот прогон — повтор другого прогона (проект повтора):
«из журнала» — ответы моделей взяты из журнала источника, ожидание
«совпадает» байт в байт; «заново» — модели вызваны ещё раз, сравнение по
итогам. Отчёт — в панели заморозки, секция «Отчёт повтора»">
            <RotateCcw className={clsx('h-5 w-5',
              status.replay.of.verdict === 'совпадает'
                || status.replay.of.verdict === 'итоги совпадают'
                ? 'text-emerald-500'
                : status.replay.of.verdict ? 'text-cf-warning'
                : 'text-cf-text-muted')} />
            <p className="text-sm font-semibold text-cf-text">
              {status.replay.of.mode === 'replay-fresh'
                ? 'Повтор заново' : 'Повтор из журнала'} прогона{' '}
              <a className="text-cf-primary hover:underline"
                 href={`#/runs/${status.replay.of.source_run}`}>
                {status.replay.of.source_run}
              </a>
              {status.replay.of.reconstructed
                ? ' (цепочка действий восстановлена по артефактам)' : ''}
              {' — '}
              {status.replay.of.verdict
                ? status.replay.of.verdict
                : status.replay.progress
                  ? `идёт: действие ${status.replay.progress.step} из `
                    + `${status.replay.progress.of}`
                    + (status.replay.progress.action
                       ? ` (${status.replay.progress.action})` : '')
                  : 'идёт'}
              {status.replay.of.verdict && (
                <a className="ml-2 font-normal text-cf-primary hover:underline"
                   href="#replay-report"
                   onClick={e => {
                     e.preventDefault()
                     document.getElementById('replay-report')
                       ?.scrollIntoView({ behavior: 'smooth' })
                   }}
                   title="Секция «Отчёт повтора» в панели заморозки">
                  отчёт
                </a>
              )}
            </p>
          </div>
        )}
        {exchange?.verdict && (
          <div className={clsx(
            'flex items-center gap-3 border-b px-4 py-2.5',
            shownVerdict(exchange) === 'успех'
              ? 'border-emerald-500/30 bg-emerald-500/10'
              : 'border-cf-warning/30 bg-cf-warning/10')}>
            <Trophy className={clsx('h-5 w-5',
              shownVerdict(exchange) === 'успех'
                ? 'text-emerald-500' : 'text-cf-warning')} />
            <p className="text-sm font-semibold text-cf-text">
              Аудит-цикл завершён — вердикт:
              {' '}{humanVerdict(shownVerdict(exchange))}
              {' '}({verdictReason(shownVerdict(exchange))})
              {shownVerdict(exchange) !== exchange.verdict.verdict && (
                <span className="font-normal text-cf-muted"
                      title="Вердикт старого цикла пересужен реестром при
чтении: файл истории не переписан, но «успех» при живой
блокирующей/критической находке не показывается (A07)">
                  {' '}— записано «{humanVerdict(exchange.verdict.verdict)}»
                </span>
              )}
              {shownVerdict(exchange).startsWith('не закрыто') && (
                <span className="font-normal text-cf-muted"
                      title="Живые блокирующие/критические находки реестра
сейчас; 0 — все разрешены арбитром или закрыты вставшей правкой,
запись цикла при этом не переписывается (A07)">
                  {' '}— живых блокирующих сейчас:
                  {' '}{exchange.registry_verdict?.count ?? 0}
                  {(exchange.registry_verdict?.count ?? 0) === 0
                    ? ' (разрешены после записи вердикта)' : ''}
                </span>
              )}, обменов
              {' '}{exchange.verdict.rounds}
              {exchange.config?.max_rounds
                ? ` из ${exchange.config.max_rounds}` : ''}.
              <a className="ml-2 font-normal text-cf-primary
                            hover:underline"
                 href={exchangeSvodUrl(runId, exchange.verdict.rounds)}
                 target="_blank" rel="noreferrer"
                 title="Открыть свод после последнего обмена цикла —
все принятые исправления уже внесены">
                Финальная версия свода
              </a>
            </p>
          </div>
        )}
        <div className="relative flex-1" ref={canvasRef}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={NODE_TYPES}
            onNodeClick={onNodeClick}
            onNodeDragStop={onNodeDragStop}
            onPaneClick={() => { setConfigPopup(null)
                                 setFilePopup(null)
                                 setSelected(null) }}
            fitView
            proOptions={{ hideAttribution: true }}
            nodesConnectable={false}
          >
            <Background gap={18} size={1.5}
                        color="var(--cf-rf-dots)" />
            <Controls showInteractive={false} />
          </ReactFlow>
          {configPopup && (
            <AgentConfigPopup
              runId={runId}
              role={configPopup.role}
              x={configPopup.x}
              y={configPopup.y}
              onClose={() => { setConfigPopup(null); refresh() }}
            />
          )}
          {filePopup && (
            <FileConfigPopup
              runId={runId}
              file={filePopup.file}
              all={filesCfg}
              x={filePopup.x}
              y={filePopup.y}
              onClose={saved => {
                setFilePopup(null)
                if (saved) refresh()
              }}
            />
          )}
          <ConfirmDialog
            open={dataDeleteOpen}
            title="Удалить файл данных?"
            message={`${dataDeleteFile}: файл, его политика обработки, `
              + 'запись в статусе прогона и источник в замороженном '
              + 'наборе будут удалены разом; оставшиеся файлы '
              + 'пере-заморожены. Отчёт проверок данных и очищенные '
              + 'данные по прежнему составу снимаются — после удаления '
              + 'проверки нужно прогнать заново.'}
            confirmLabel="Удалить"
            danger
            onConfirm={confirmDataDelete}
            onCancel={() => setDataDeleteOpen(false)}
          />
          <MapView open={showMap} runId={runId}
                   onClose={() => setShowMap(false)} />
          <MetricsPanel status={status} exchange={exchange}
                        arbiter={arbiter} />
          {(() => {
            const h = processHeadline(status?.state, customer)
            return (
              <div
                title="Текущее состояние процесса; страница обновляется
сама"
                className={clsx(
                  'absolute left-1/2 top-3 z-10 -translate-x-1/2',
                  'rounded-xl border px-4 py-1.5 text-sm font-semibold',
                  'shadow-md backdrop-blur bg-cf-panel/95',
                  h.tone === 'error' && 'border-red-400 text-red-600',
                  h.tone === 'busy'
                    && 'border-cf-primary text-cf-primary',
                  h.tone === 'wait'
                    && 'border-amber-400 text-amber-600',
                  h.tone === 'ok'
                    && 'border-emerald-400 text-emerald-600',
                )}
              >
                {h.text}
              </div>
            )
          })()}
        </div>
      </div>

      {/* Выдвижная панель деталей: занимает место только когда выбран
          блок; закрывается крестиком, Escape и кликом по пустому полю
          диаграммы. */}
      {selected && (
      <aside className="w-96 shrink-0 overflow-y-auto border-l
                        border-cf-border bg-cf-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider
                           text-cf-text-muted">
            Детали стадии
          </span>
          <button
            onClick={() => setSelected(null)}
            aria-label="Закрыть панель деталей"
            title="Закрыть (Escape)"
            className="rounded-lg p-1.5 text-cf-text-muted
                       transition-colors hover:bg-cf-hover
                       hover:text-cf-text"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {selected === 'documents' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Файлы данных
            </h2>
            <p className="text-xs text-cf-text-muted">
              Политика задаёт, куда могут уходить клаузы файла при
              обработке; изменить её и примечание — шестерёнка у файла
              на блоке. Стадии, смешивающие содержимое всех файлов
              (противоречия, аудит-цикл, арбитраж), идут по строжайшей
              политике прогона.
            </p>
            {(status?.files || []).map(f => {
              const c = filesCfg[f] || { processing: 'local', note: '' }
              return (
                <div key={f} className="card space-y-1 !p-3">
                  <p className="truncate font-mono text-xs
                                text-cf-text" title={f}>
                    {f}
                  </p>
                  <StatusBadge kind={c.processing === 'cloud'
                    ? 'warn' : 'info'}>
                    {POLICY_LABELS[c.processing] || c.processing}
                  </StatusBadge>
                  {c.note && (
                    <p className="text-xs italic text-cf-text-muted">
                      {c.note}
                    </p>
                  )}
                </div>
              )
            })}
            <div className="border-t border-cf-border pt-3">
              <h3 className="mb-1 text-sm font-semibold text-cf-text">
                Режим 2 — формализация третьей стороны
              </h3>
              <p className="mb-2 text-xs text-cf-text-muted">
                Загрузите чужой формализованный файл правил — сверка
                против выверенного свода добавит в анкету часть 4
                (несоответствия источникам) и аудиторский отчёт.
              </p>
              {status?.third_party_file && (
                <p className="mb-2 font-mono text-xs text-cf-text">
                  {status.third_party_file}
                  {' '}({status.third_party_claims} утверждений)
                </p>
              )}
              {status?.mode2_summary && (
                <dl className="mb-2 space-y-1 text-sm">
                  {Object.entries({
                    'Сверено': status.mode2_summary.checked,
                    ...status.mode2_summary.by_verdict,
                    'Вопросов части 4':
                      status.mode2_summary.part4_added,
                    'Не сверено': status.mode2_summary.skipped,
                  }).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-cf-text-muted">{k}</dt>
                      <dd className="font-medium text-cf-text">
                        {v as number}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {(status?.mode2_problems || []).length > 0 && (
                <p className="mb-2 text-xs text-cf-error">
                  {(status!.mode2_problems || []).join('; ')}
                </p>
              )}
              <input ref={thirdPartyInputRef} type="file"
                     accept=".md,.docx,.doc,.xlsx,.json"
                     className="hidden"
                     onChange={e => onThirdPartyFile(e.target.files)} />
              <div className="flex gap-2">
                <Button size="sm" variant="secondary"
                        disabled={busyState}
                        title="Выбрать файл чужой формализации
(markdown, Word, Excel или JSON) — например, свод правил, написанный
интегратором"
                        onClick={() =>
                          thirdPartyInputRef.current?.click()}>
                  Загрузить файл
                </Button>
                <Button size="sm"
                        disabled={busyState || !status?.third_party_file
                          || !status?.extract_summary}
                        title="Сверить каждое утверждение чужого файла
с выверенным сводом и источниками: вердикты «соответствует»,
«расходится», «в источниках отсутствует», «неясно»; расхождения
попадают в анкету частью 4"
                        onClick={launchMode2}>
                  <Play className="h-4 w-4" /> Сверить формализацию
                </Button>
              </div>
              {status?.mode2_summary && (
                <a className="mt-2 flex items-center justify-between
                              rounded-lg border border-cf-border px-3
                              py-2 text-sm text-cf-text
                              transition-colors hover:bg-cf-hover"
                   href={artifactUrl(runId,
                                     'отчёт-сверки-формализации')}
                   target="_blank" rel="noreferrer">
                  отчёт-сверки-формализации
                  <ExternalLink className="h-3.5 w-3.5
                                           text-cf-text-muted" />
                </a>
              )}
            </div>
          </div>
        )}
        {selected === 'freeze' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Заморозка источников
            </h2>
            {status?.freeze_counters && (
              <dl className="space-y-1 text-sm">
                {Object.entries({
                  'Файлов': status.freeze_counters.files,
                  'Клауз': status.freeze_counters.clauses,
                  'Якорей': status.freeze_counters.anchors,
                  'Оригиналов сверено':
                    status.freeze_counters.originals,
                }).map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <dt className="text-cf-text-muted">{k}</dt>
                    <dd className="font-medium text-cf-text">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {(status?.freeze_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            {/* Паспорт прогона — рецепт воспроизведения (принцип
                владельца 07.09: «информация о прогоне должна давать
                возможность его однозначно воспроизвести»). */}
            {status?.passport && (
              <div className="space-y-1.5">
                <h3 className={SECTION_H3}
                    title="Чем сделан прогон: код и окружение панели при
рождении, входы по отпечаткам SHA-256, модели по стадиям (из журнала
каналов), журналы для повтора «из журнала». Повтор «заново» — те же
входы и раскладка, новые вызовы моделей">
                  Паспорт прогона
                </h3>
                <dl className="space-y-0.5 text-xs">
                  <div className="flex justify-between gap-2">
                    <dt className="text-cf-text-muted">код панели</dt>
                    <dd className="font-mono text-cf-text">
                      {status.passport.created?.code?.short || '—'}
                      {status.passport.created?.spec_version
                        ? ` · спецификация ${status.passport.created.spec_version}`
                        : ''}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-cf-text-muted">решатели</dt>
                    <dd className="text-right text-cf-text">
                      {Object.entries(status.passport.created?.packages || {})
                        .filter(([, v]) => v)
                        .map(([k, v]) => `${k} ${v}`).join(' · ') || '—'}
                      {status.passport.created?.python
                        ? ` · python ${status.passport.created.python}` : ''}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-cf-text-muted">каталог шаблонов</dt>
                    <dd className="text-right text-cf-text">
                      <CatalogVersionLink
                        version={status.passport.created?.catalog?.version}
                        fingerprint={
                          status.passport.created?.catalog?.fingerprint} />
                    </dd>
                  </div>
                </dl>
                <div className="text-xs">
                  <div className="text-cf-text-muted">входы (SHA-256)</div>
                  <ul className="space-y-0.5">
                    {status.passport.inputs.documents.map(d => (
                      <li key={`d-${d.sid}`} className="flex justify-between
                                                        gap-2 font-mono">
                        <span className="truncate" title={d.file}>{d.file}</span>
                        <span className="text-cf-text-muted"
                              title={d.sha256}>{(d.sha256 || '').slice(0, 12)}…</span>
                      </li>
                    ))}
                    {status.passport.inputs.data.map(d => (
                      <li key={`x-${d.sid}`} className="flex justify-between
                                                        gap-2 font-mono">
                        <span className="truncate" title={d.file}>{d.file}</span>
                        <span className="text-cf-text-muted"
                              title={d.sha256}>{(d.sha256 || '').slice(0, 12)}…</span>
                      </li>
                    ))}
                    {status.passport.inputs.answers && (
                      <li className="flex justify-between gap-2 font-mono">
                        <span className="truncate">
                          ответы {status.passport.inputs.answers.revision}
                        </span>
                        <span className="text-cf-text-muted"
                              title={status.passport.inputs.answers.sha256 || ''}>
                          {(status.passport.inputs.answers.sha256 || '').slice(0, 12)}…
                        </span>
                      </li>
                    )}
                  </ul>
                </div>
                <div className="text-xs">
                  <div className="text-cf-text-muted">модели по стадиям</div>
                  {status.passport.stages.length === 0 ? (
                    <p className="text-cf-text-muted">стадии ещё не шли</p>
                  ) : (
                    <ul className="space-y-0.5">
                      {status.passport.stages.map(s => (
                        <li key={`${s.role}/${s.klass}`}
                            className="flex justify-between gap-2"
                            title={`роль ${s.role}, класс ${s.klass || '—'}, `
                              + `источник ${s.config_source || '—'}`
                              + (s.stage ? `, стадия «${s.stage}»` : '')
                              + (s.code ? `, код ${s.code}` : '')}>
                          <span className="text-cf-text">{s.role_title}</span>
                          <span className="text-right text-cf-text-muted">
                            {s.provider ? `${s.provider} / ` : ''}{s.model || '—'}
                            {s.channel ? ` · ${s.channel}` : ''}
                            {s.effort ? ` · усилие ${s.effort}` : ''}
                            {s.temperature !== undefined && s.temperature !== null
                              ? ` · t ${s.temperature}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <p className="text-[11px] text-cf-text-muted"
                   title="Повтор «из журнала»: сохранённые ответы моделей —
кэш извлечения по чанкам, замечания и ответы каждого обмена, ревизии
ответов анкеты; повтор «заново» — новые вызовы с теми же входами и
раскладкой, сравнение по итогам">
                  журналы для повтора: кэш извлечения{' '}
                  {status.passport.journals.extract_cache}, обменов{' '}
                  {status.passport.journals.exchange_rounds}, ревизий ответов{' '}
                  {status.passport.journals.answers_revisions}
                </p>
              </div>
            )}
            {/* Запись о прогоне — рядом с паспортом: паспорт говорит,
                ЧЕМ сделан прогон, запись — что было объявлено, что
                происходило (обе системы) и чем кончилось. */}
            <RunRecordCard runId={runId} runState={status?.state} />
            {/* Дерево прогонов — рядом с записью: запись говорит, ЧТО
                было в этом прогоне, дерево — где он стоит среди
                прогонов на тех же входах и чем от них отличается
                (проект «Запись о прогоне», шаг 3). */}
            <RunTreeCard runId={runId} runState={status?.state} />
            {/* Отчёт повтора (проект повтора, Р2–Р3): у потомка после
                завершения водителя. */}
            {status?.replay?.of && replayReport && (
              <div id="replay-report" className="space-y-1.5">
                <h3 className={SECTION_H3}
                    title="Итог повтора: различия окружения (код панели,
спецификация, Python, пакеты, каталог, модели ролей), счёт действий и
вызовов, первое расхождение словами, расходящиеся артефакты, у «заново» —
таблица итогов и вердикт эквивалентности двух формализаций">
                  Отчёт повтора
                </h3>
                <p className="text-xs text-cf-text">
                  Вердикт: <span className={
                    replayReport.verdict === 'совпадает'
                      || replayReport.verdict === 'итоги совпадают'
                      ? 'font-semibold text-emerald-500'
                      : 'font-semibold text-cf-warning'}>
                    {replayReport.verdict}</span>
                  {' · '}действий {replayReport.actions.replayed}
                  {replayReport.actions.skipped.length
                    ? `, пропущено ${replayReport.actions.skipped.length}` : ''}
                  {replayReport.actions.stopped ? ', остановлено' : ''}
                  {' · '}вызовов: у источника {replayReport.calls.source},
                  из журнала {replayReport.calls.served_from_journal},
                  живых {replayReport.calls.fresh}
                  {replayReport.calls.unconsumed
                    ? `, не израсходовано ${replayReport.calls.unconsumed}` : ''}
                </p>
                {replayReport.note && (
                  <p className="text-[11px] text-cf-text-muted">{replayReport.note}</p>
                )}
                {replayReport.environment.differs.length > 0 && (
                  <div className="text-xs">
                    <div className="text-cf-text-muted">различия окружения</div>
                    <ul className="space-y-0.5">
                      {replayReport.environment.differs.map((d, i) => (
                        <li key={i} className="flex justify-between gap-2">
                          <span className="text-cf-text">{d.what}</span>
                          <span className="text-right text-cf-text-muted">
                            {fmtReplayVal(d.source)} → {fmtReplayVal(d.replay)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {replayReport.first_divergence && (
                  <div className="text-xs">
                    <div className="text-cf-text-muted">первое расхождение</div>
                    <p className="text-cf-text">
                      {fmtDivergence(replayReport.first_divergence)}
                    </p>
                  </div>
                )}
                {replayReport.artifacts.length > 0 && (
                  <div className="text-xs">
                    <div className="text-cf-text-muted">
                      артефакты: совпало{' '}
                      {replayReport.artifacts.filter(a => a.same).length} из{' '}
                      {replayReport.artifacts.length}
                    </div>
                    {replayReport.artifacts.some(a => !a.same) && (
                      <ul className="space-y-0.5 font-mono">
                        {replayReport.artifacts.filter(a => !a.same).slice(0, 20)
                          .map(a => (
                            <li key={a.path} className="text-cf-warning">
                              {a.path}{a.code ? ` (${a.code})` : ''}
                            </li>
                          ))}
                      </ul>
                    )}
                  </div>
                )}
                {replayReport.outcomes.length > 0 && (
                  <div className="text-xs">
                    <div className="text-cf-text-muted">итоги</div>
                    <ul className="space-y-0.5">
                      {replayReport.outcomes.map(o => (
                        <li key={o.name} className="flex justify-between gap-2"
                            title={o.source_file || ''}>
                          <span className={o.same ? 'text-cf-text' : 'text-cf-warning'}>
                            {o.name}
                          </span>
                          <span className="text-right text-cf-text-muted">
                            {o.same ? fmtReplayVal(o.replay)
                              : `${fmtReplayVal(o.source)} → ${fmtReplayVal(o.replay)}`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {replayReport.equivalence && (
                  <div className="text-xs">
                    <div className="text-cf-text-muted">эквивалентность ВМП</div>
                    <p className={replayReport.equivalence.verdict === 'EQUIVALENT'
                      ? 'text-emerald-500' : 'text-cf-warning'}>
                      {replayReport.equivalence.verdict || replayReport.equivalence.error}
                      {replayReport.equivalence.problems_total
                        ? ` — расхождений ${replayReport.equivalence.problems_total}` : ''}
                    </p>
                    {(replayReport.equivalence.problems || []).slice(0, 5).map((p, i) => (
                      <p key={i} className="text-cf-text-muted">{p}</p>
                    ))}
                  </div>
                )}
              </div>
            )}
            <a className="inline-flex items-center gap-1 text-sm
                          text-cf-primary hover:underline"
               href={artifactUrl(runId, 'манифест')} target="_blank"
               rel="noreferrer"
               title="Манифест заморозки: SHA-256 каждого исходного
файла и счётчики клауз — доказательство, что обработка шла по
неизменённым документам">
              Манифест <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        )}
        {selected === 'worker' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Обработчик — извлечение правил
            </h2>
            <label className="block text-xs text-cf-text-muted"
                   title="Имя заказчика — попадает в шапки свода,
анкеты и отчётов; на обработку не влияет">
              Название авиакомпании
              <input className="input-field mt-1" value={customer}
                     onChange={e => setCustomer(e.target.value)}
                     placeholder="для шапки свода и анкеты" />
            </label>
            <div className="block text-xs text-cf-text-muted"
                 title="Признак допуска моделей хранится у комплекта документов
(класс данных и явный признак с основанием); площадка его не переопределяет.
Нет признака — только локальная модель.">
              Допуск моделей комплекта
              <div className="input-field mt-1 bg-cf-bg-muted">
                {status?.llm_scope
                  ? `${status.llm_scope.effective} — ${status.llm_scope.reading}`
                  : 'признак не прочитан'}
              </div>
            </div>
            <Button
              onClick={launch}
              disabled={extracting
                || status?.state === 'извлечение идёт'}
              leadingIcon={<Play className="h-4 w-4" />}
              title="Запустить извлечение: модель читает замороженные
клаузы чанками и предлагает правила, код проверяет каждый кандидат
контрактом; итог — свод, анкета и отбраковка"
            >
              {status?.state === 'извлечение идёт'
                ? 'Извлечение идёт…' : 'Извлечь'}
            </Button>
            {status?.error && (
              <p className="text-xs text-cf-error">{status.error}</p>
            )}
            {(status?.extract_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            <p className="text-xs text-cf-text-muted">
              Модель и параметры — шестерёнка на самом блоке.
            </p>
          </div>
        )}
        {selected === 'artifacts' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Артефакты прогона
            </h2>
            {/* Форма ВМП (решение владельца 2026-09-02): каноническая
                форма выдачи и приёмки — общий раздел, части «Пейринг» и
                «Ростеринг» с разделами а/б/в; строится по требованию из
                текущего свода, внизу — проверки формы приёмки. */}
            <a className="flex items-center justify-between rounded-lg
                          border border-cf-border px-3 py-2 text-sm
                          text-cf-text transition-colors hover:bg-cf-hover"
               href={`/api/runs/${encodeURIComponent(runId)}/form.md`}
               target="_blank" rel="noreferrer"
               title="Форма ВМП — общий раздел, часть 1 «Пейринг» и часть 2
«Ростеринг», в каждой: а) жёсткие ограничения, б) мягкие с
коэффициентами штрафов и единицей нарушения, в) критерии оптимизации.
Форма выдачи и приёмки ВМП; внизу — проверки формы приёмки.">
              форма ВМП
              <ExternalLink className="h-3.5 w-3.5 text-cf-text-muted" />
            </a>
            {['свод', 'анкета', 'модель', 'отбраковка',
              ...(status?.answers_summary ? ['сводка-ответов'] : []),
             ].map(alias => (
              <a key={alias}
                 className="flex items-center justify-between rounded-lg
                            border border-cf-border px-3 py-2 text-sm
                            text-cf-text transition-colors
                            hover:bg-cf-hover"
                 href={artifactUrl(runId, alias)} target="_blank"
                 rel="noreferrer" title={ARTIFACT_TIPS[alias]}>
                {alias}
                <ExternalLink className="h-3.5 w-3.5
                                         text-cf-text-muted" />
              </a>
            ))}
            {/* Путь человека к ответам (живой вопрос 04.09): «анкета»
                выше — html-представление для чтения; книга для
                заполнения — отдельная ссылка на скачивание. */}
            <a className="flex items-center justify-between rounded-lg
                          border border-cf-border px-3 py-2 text-sm
                          text-cf-text transition-colors hover:bg-cf-hover"
               href={artifactUrl(runId, 'анкета-xlsx')}
               download={`anketa-${runId}.xlsx`}
               title={ARTIFACT_TIPS['анкета-xlsx']}>
              анкета — Excel-книга для заполнения
              <Download className="h-3.5 w-3.5 text-cf-text-muted" />
            </a>
            <div className="border-t border-cf-border pt-3">
              <h3 className="mb-1 text-sm font-semibold text-cf-text">
                Ответы авиакомпании
              </h3>
              <p className="mb-2 text-xs text-cf-text-muted">
                Скачайте книгу ссылкой «анкета — Excel-книга для
                заполнения» выше, заполните колонку «ОТВЕТ
                АВИАКОМПАНИИ» и загрузите её здесь: однозначные ответы
                развилок применяются к своду машинно, свод и анкета
                получают новую ревизию.
              </p>
              {status?.answers_summary && (
                <dl className="mb-2 space-y-1 text-sm">
                  {Object.entries({
                    'Ревизия свода': status.answers_summary.revision,
                    'Получено ответов': status.answers_summary.received,
                    'Применено': status.answers_summary.applied,
                    'Внесено обработчиком':
                      status.answers_summary.worker_applied ?? 0,
                    'Отброшено патчей':
                      status.answers_summary.worker_rejected ?? 0,
                    'Не разобрано': status.answers_summary.unparsed,
                    ...(status.answers_summary.conflicted ? {
                      'Конфликтов пересмотра': status.answers_summary.conflicted,
                    } : {}),
                  }).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-cf-text-muted">{k}</dt>
                      <dd className="font-medium text-cf-text">
                        {v as number}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {(status?.answers_problems || []).length > 0 && (
                <p className="mb-2 text-xs text-cf-error">
                  {(status!.answers_problems || []).join('; ')}
                </p>
              )}
              <input ref={answersInputRef} type="file" accept=".xlsx"
                     className="hidden"
                     onChange={e => onAnswersFile(e.target.files)} />
              <Button size="sm" disabled={busyState
                        || !status?.extract_summary}
                      title="Выбрать заполненную книгу анкеты (.xlsx).
Однозначные ответы применяет код, сложные — обработчик точечными
правками под аудитом; свод и анкета получают новую ревизию"
                      onClick={() => answersInputRef.current?.click()}>
                <Play className="h-4 w-4" /> Загрузить ответы
              </Button>
            </div>
          </div>
        )}
        {selected === 'auditor' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Аудит-цикл «обработчик — аудитор»
            </h2>
            {/* Замечание владельца 2026-08-21: селекты показывали
                запасной канал «облако (Claude)» даже при настроенных
                ролях — путаница. Настроенная роль показывается
                фактом, селект остаётся только роли без настройки. */}
            {(agents.auditor?.channels?.cloud?.provider
              || agents.worker?.channels?.cloud?.provider) && (
              <p className="rounded-lg bg-cf-hover px-3 py-2 text-xs
                            text-cf-text"
                 title="Сохранённая конфигурация ролей (шестерёнка на
блоке) главнее запасных селектов — цикл пойдёт именно этими
моделями">
                Настроено: аудитор — {
                  agents.auditor?.channels?.cloud?.provider
                    ? `${agents.auditor.channels.cloud.provider} / ${
                        agents.auditor.channels.cloud.model
                        || '(по умолчанию)'}`
                    : 'запасной канал'}; обработчик — {
                  agents.worker?.channels?.cloud?.provider
                    ? `${agents.worker.channels.cloud.provider} / ${
                        agents.worker.channels.cloud.model
                        || '(по умолчанию)'}`
                    : 'запасной канал'}
              </p>
            )}
            <div className="grid grid-cols-2 gap-2">
              <label className={clsx('text-xs text-cf-text-muted',
                       // Запасной селект при настроенной роли не
                       // действует — и не показывается: серая подпись
                       // «облако (Claude)» при аудиторе openai читалась
                       // как ошибка (замечание владельца 06.09).
                       agents.auditor?.channels?.cloud?.provider
                         ? 'hidden' : 'block')}
                     title="Какой моделью аудитор перечитывает
документы против свода. Роли цикла независимы — аудитора полезно
держать на другой модели, чем обработчика. При сохранённой
конфигурации роли этот запасной селект не действует">
                Канал аудитора
                <select className="input-field mt-1"
                        disabled={Boolean(
                          agents.auditor?.channels?.cloud?.provider)}
                        value={auditorChannel}
                        onChange={e =>
                          setAuditorChannel(e.target.value)}>
                  <option value="cloud">облако (по раскладке площадки)</option>
                  <option value="local">локальная модель</option>
                  <option value="auto">по умолчанию контура</option>
                </select>
              </label>
              <label className={clsx('text-xs text-cf-text-muted',
                       agents.worker?.channels?.cloud?.provider
                         ? 'hidden' : 'block')}
                     title="Какой моделью обработчик отвечает на
замечания и правит свод. При сохранённой конфигурации роли этот
запасной селект не действует">
                Канал обработчика
                <select className="input-field mt-1"
                        disabled={Boolean(
                          agents.worker?.channels?.cloud?.provider)}
                        value={workerChannel}
                        onChange={e =>
                          setWorkerChannel(e.target.value)}>
                  <option value="cloud">облако (по раскладке площадки)</option>
                  <option value="local">локальная модель</option>
                  <option value="auto">по умолчанию контура</option>
                </select>
              </label>
            </div>
            <label className="block text-xs text-cf-text-muted"
                   title="Максимум обменов «аудит → ответ». Цикл
останавливается раньше: успех — два чистых аудита подряд, стагнация —
замечания перестали убывать">
              Лимит обменов
              <input type="number" min={EXCHANGE_ROUNDS_MIN} max={EXCHANGE_ROUNDS_MAX} step={1}
                     className="input-field mt-1" value={maxRounds}
                     onChange={e =>
                       setMaxRounds(Number(e.target.value))} />
            </label>
            <Button
              onClick={() => launchExchange()}
              disabled={busyState || !status?.extract_summary}
              leadingIcon={<Play className="h-4 w-4" />}
              title="Запустить ОДИН аудит-цикл: аудитор перечитывает
все клаузы против свода и пишет замечания, обработчик правит или
мотивированно отклоняет; повторяется до чистоты, стагнации или
лимита. Прерванный цикл продолжает с диска без потери работы"
            >
              {status?.state === 'аудит-цикл идёт'
                ? 'Цикл идёт…' : 'Запустить цикл'}
            </Button>
            <div className="rounded-lg border border-cf-border bg-cf-bg
                            p-2.5">
              <label className="block text-xs text-cf-text-muted"
                     title="Максимум кругов сквозного процесса: круг =
аудит-цикл + при тупиках арбитраж + новая ревизия">
                Повторений сквозного цикла
                <input type="number" min={FULL_CYCLES_MIN} max={FULL_CYCLES_MAX} step={1}
                       className="input-field mt-1"
                       value={maxMegacycles}
                       onChange={e =>
                         setMaxMegacycles(Number(e.target.value))} />
              </label>
              <Button
                className="mt-2"
                onClick={launchFullCycle}
                disabled={busyState || !status?.extract_summary}
                leadingIcon={<Play className="h-4 w-4" />}
                title="Сквозной процесс одной кнопкой (СКВ-1):
аудит-цикл → при тупиках арбитраж с исполнением → новая ревизия →
снова цикл… До отсутствия блокирующих и критических замечаний
(Б=0 и К=0) либо до исчерпания повторений. Если на диске лежит
завершённый цикл с неразобранными тупиками — начнёт с их арбитража.
Пауза и стоп действуют на всю цепочку"
              >
                {status?.state === 'сквозной цикл идёт'
                  ? `Сквозной цикл идёт (круг `
                    + `${status?.full_cycle?.cycle ?? '…'}, `
                    + `${status?.full_cycle?.phase ?? ''})…`
                  : 'Сквозной цикл'}
              </Button>
            </div>
            {!status?.extract_summary && (
              <p className="text-xs text-cf-text-muted">
                Цикл аудирует готовый свод — сначала извлечение.
              </p>
            )}
            <p className="text-xs text-cf-text-muted">
              Модель и параметры каждой роли — шестерёнка на блоке;
              сохранённая конфигурация старше запасных селектов выше.
            </p>

            {(exchange?.rounds || []).length > 0 && (
              <div className="space-y-2">
                <h3 className={SECTION_H3}>
                  Обмены
                </h3>
                {exchange!.rounds.map(r => (
                  <div key={r.round} className="card space-y-1 !p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium
                                       text-cf-text">
                        Обмен {r.round}
                      </span>
                      <span className={clsx('status-badge',
                        (r.weighty || []).every(v => v === 0)
                          ? 'bg-cf-success/15 text-cf-success'
                          : 'severity-crit')}
                            title="Весомые замечания обмена:
Блокирующие, Критические, Серьёзные. Цикл сходится, когда все три —
нули два обмена подряд">
                        Б {r.weighty?.[0] ?? '?'} ·
                        К {r.weighty?.[1] ?? '?'} ·
                        С {r.weighty?.[2] ?? '?'}
                      </span>
                    </div>
                    <p className="text-xs text-cf-text-muted">
                      замечаний {r.remarks ?? 0}, ответов
                      {' '}{r.answers ?? 0}, редакционных {r.E ?? 0}
                    </p>
                    <div className="flex gap-3 text-xs">
                      {['замечания', 'ответы'].map(alias => (
                        <a key={alias}
                           className="text-cf-primary hover:underline"
                           href={exchangeRoundUrl(runId, r.round,
                                                  alias)}
                           target="_blank" rel="noreferrer"
                           title={ARTIFACT_TIPS[alias]}>
                          {alias}
                        </a>
                      ))}
                      <a className="text-cf-primary hover:underline"
                         href={exchangeSvodUrl(runId, r.round)}
                         target="_blank" rel="noreferrer"
                         title="Версия свода после этого обмена — с
принятыми в нём исправлениями">
                        свод v{r.round}
                      </a>
                      <a className="text-cf-primary hover:underline"
                         href={exchangeAnketaUrl(runId, r.round)}
                         target="_blank" rel="noreferrer"
                         title="Анкета после этого обмена — пара к
своду той же версии: правки меняют и вопросы">
                        анкета v{r.round}
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {Object.keys(exchange?.registry || {}).length > 0 && (
              <div>
                <h3 className={SECTION_H3}>
                  Реестр находок
                </h3>
                <dl className="space-y-0.5 text-sm">
                  {Object.entries(exchange!.registry).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-cf-text-muted">{k}</dt>
                      <dd className="font-medium text-cf-text">{v}</dd>
                    </div>
                  ))}
                  {!('тупик' in (exchange?.registry || {})) && (
                    <div className="flex justify-between">
                      <dt className="text-cf-text-muted">тупики</dt>
                      <dd className="font-medium text-cf-text">0</dd>
                    </div>
                  )}
                </dl>
                {exchange?.verdict
                  && !(exchange.verdict.deadlocks || []).length && (
                  <p className="mt-1.5 rounded-lg border
                                border-emerald-400/40
                                bg-emerald-500/10 px-3 py-1.5 text-xs
                                text-emerald-600"
                     title="Тупик — спор, где обработчик дважды
отклонил замечание аудитора; таких споров в цикле не было, судить
арбитру нечего">
                    Арбитраж не требуется — тупиков нет
                  </p>
                )}
              </div>
            )}

            {(exchange?.verdict?.deadlocks || []).length > 0 && (
              <div className="rounded-lg border border-cf-warning/40
                              bg-cf-warning/10 p-3">
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-warning">
                  Тупики — на ваше решение
                </h3>
                {exchange!.verdict!.deadlocks.map(d => (
                  <p key={d.id} className="text-xs text-cf-text">
                    {d.id} · {d.severity} ·
                    {' '}{String(d.key?.[1] ?? d.key?.[0] ?? '')}
                  </p>
                ))}
              </div>
            )}
            {(exchange?.gate_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            {status?.state === 'отказ аудит-цикла' && status.error && (
              <p className="text-xs text-cf-error">{status.error}</p>
            )}
          </div>
        )}
        {selected === 'data-files' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Операционные данные
            </h2>
            <p className="text-xs text-cf-text-muted">
              Таблицы xlsx / csv / json: ростеры, допуски, журналы.
              Заморозка считает SHA-256 каждого файла; политика
              обработки и примечание — шестерёнка у файла на блоке
              (умолчание — «только локально»).
            </p>
            <input ref={dataInputRef} type="file" multiple
                   accept=".xlsx,.csv,.json" className="hidden"
                   onChange={e => { onDataFiles(e.target.files)
                                    e.target.value = '' }} />
            <Button
              onClick={() => dataInputRef.current?.click()}
              leadingIcon={<Play className="h-4 w-4" />}
              title="Выбрать таблицы (.xlsx / .csv / .json). Заморозка
снимает SHA-256 и фиксирует листы и строки — проверки идут строго по
замороженной копии"
            >
              Загрузить и заморозить данные
            </Button>
            <Button
              variant="secondary"
              onClick={() => setShowMap(true)}
              disabled={!dl?.files?.length}
              leadingIcon={<MapIcon className="h-4 w-4" />}
              title={dl?.files?.length
                ? 'Схема сети станций и рейсов, персонал по базам и '
                  + 'должностям, карта мира при полных координатах — '
                  + 'по замороженной книге, без модели'
                : 'Карта появится после заморозки книги операционных '
                  + 'данных'}
            >
              Карта сети и персонал
            </Button>
            {(dl?.files || []).length > 0 && (
              <ul className="space-y-1 text-sm"
                  aria-label="Файлы операционных данных">
                {(dl?.files || []).map(f => (
                  <li key={f}
                      className="flex items-center gap-2 rounded-lg
                                 bg-cf-hover px-2 py-1">
                    <span className="min-w-0 flex-1 truncate font-mono
                                     text-xs text-cf-text" title={f}>
                      {f}
                    </span>
                    <button
                      onClick={() => askDataDelete(f)}
                      disabled={dataLineBusy}
                      aria-label={`Удалить файл данных: ${f}`}
                      title={dataLineBusy
                        ? 'Идёт стадия линии данных — остановите её '
                          + 'перед удалением файла'
                        : 'Удалить файл данных: файл, его политика, '
                          + 'запись в статусе и источник в '
                          + 'замороженном наборе уходят разом; отчёт '
                          + 'проверок по прежнему составу снимается'}
                      className="rounded-lg p-1 text-cf-text-muted
                                 transition-colors hover:bg-cf-error/20
                                 hover:text-cf-error
                                 disabled:cursor-not-allowed
                                 disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {dl?.freeze_counters && (
              <dl className="space-y-1 text-sm">
                {Object.entries({
                  'Файлов': dl.freeze_counters.files,
                  'Листов': dl.freeze_counters.sheets,
                  'Строк': dl.freeze_counters.rows,
                  'Оригиналов сверено': dl.freeze_counters.originals,
                }).map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <dt className="text-cf-text-muted">{k}</dt>
                    <dd className="font-medium text-cf-text">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {(dl?.freeze_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            <a className="inline-flex items-center gap-1 text-sm
                          text-cf-primary hover:underline"
               href={artifactUrl(runId, 'манифест-данных')}
               target="_blank" rel="noreferrer"
               title="Манифест заморозки данных: SHA-256 каждой
таблицы, листы и счётчики строк">
              Манифест данных <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        )}
        {selected === 'data_checks' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Проверки данных — против свода правил
            </h2>
            <p className="text-xs text-cf-text-muted">
              Модель-компилятор превращает строки свода в машинные
              проверки по закрытому перечню шаблонов; код валидирует и
              исполняет их детерминированно. Неподъёмная или
              блокированная строка — честный статус с причиной. Канал
              компилятора — слот обработчика по строжайшему классу
              политики файлов прогона.
            </p>
            <Button
              onClick={launchDataRun}
              disabled={busyState || !(dl?.files || []).length
                || !status?.extract_summary}
              leadingIcon={<Play className="h-4 w-4" />}
              title="Скомпилировать строки свода в машинные проверки и
прогнать их по замороженным данным; итог — отчёт о качестве данных с
находками"
            >
              {dl?.state === 'проверки идут'
                ? 'Проверки идут…' : 'Прогнать проверки'}
            </Button>
            {!status?.extract_summary && (
              <p className="text-xs text-cf-text-muted">
                Сначала извлечение правил: проверки генерируются из
                строк свода.
              </p>
            )}
            {!(dl?.files || []).length && (
              <p className="text-xs text-cf-text-muted">
                Сначала загрузка данных — блок «Операционные данные».
              </p>
            )}
            {dl?.summary && (
              <dl className="space-y-1 text-sm">
                {Object.entries({
                  'Проверок прогнано': dl.summary.checks_run,
                  'Не прогнано (с причинами)':
                    dl.summary.checks_skipped,
                  'Записей рассмотрено': dl.summary.rows,
                  'Находок': dl.summary.findings,
                }).map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <dt className="text-cf-text-muted">{k}</dt>
                    <dd className="font-medium text-cf-text">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            <a className="inline-flex items-center gap-1 text-sm
                          text-cf-primary hover:underline"
               href={artifactUrl(runId, 'проверки-данных')}
               target="_blank" rel="noreferrer"
               title="Что именно проверялось: скомпилированная из
каждой строки свода машинная проверка, её шаблон и параметры; для
непрогнанных — честная причина">
              Спецификации проверок
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
            {(dl?.gate_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            {dl?.state === 'отказ проверок данных' && dl.error && (
              <p className="text-xs text-cf-error">{dl.error}</p>
            )}
          </div>
        )}
        {selected === 'data_report' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Отчёт о качестве операционных данных (3.3)
            </h2>
            {dl?.summary ? (
              <>
                <dl className="space-y-1 text-sm">
                  {Object.entries({
                    ...Object.fromEntries(
                      Object.entries(dl.summary.by_severity || {})
                        .map(([k, v]) => [`Серьёзность: ${k}`, v])),
                    ...Object.fromEntries(
                      Object.entries(dl.summary.by_class || {})
                        .map(([k, v]) => [`Класс: ${k}`, v])),
                  }).map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-cf-text-muted">{k}</dt>
                      <dd className="font-medium text-cf-text">
                        {v as number}
                      </dd>
                    </div>
                  ))}
                </dl>
                {['отчёт-данных', 'отчёт-данных-модель',
                  ...(dl?.review_summary ? ['сводка-сверки'] : []),
                 ].map(alias => (
                  <a key={alias}
                     className="flex items-center justify-between
                                rounded-lg border border-cf-border px-3
                                py-2 text-sm text-cf-text
                                transition-colors hover:bg-cf-hover"
                     href={artifactUrl(runId, alias)} target="_blank"
                     rel="noreferrer" title={ARTIFACT_TIPS[alias]}>
                    {alias}
                    <ExternalLink className="h-3.5 w-3.5
                                             text-cf-text-muted" />
                  </a>
                ))}
                <div className="border-t border-cf-border pt-3
                                space-y-1">
                  <h3 className={SECTION_H3}>
                    Очищенные данные (нижний контур)
                  </h3>
                  {[['xlsx', 'Очищенные данные (XLSX)'],
                    ['json', 'Очищенные данные (JSON)']].map(
                    ([f, label]) => (
                    <a key={f}
                       className="flex items-center gap-1 text-sm
                                  text-cf-primary hover:underline"
                       href={`/api/runs/${runId}/export-clean-data`
                         + `?fmt=${f}`}
                       title="Книга данных минус строки с адресными
находками блокирующей и критической серьёзности; отчёт очистки —
листом «Очистка» (что удалено и что оставлено). Очистка обоснована
только находками проверок — ничего не правится по догадке">
                      {label}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ))}
                </div>
                <div className="border-t border-cf-border pt-3">
                  <h3 className="mb-1 text-sm font-semibold
                                 text-cf-text">
                    Сверка спорного
                  </h3>
                  <p className="mb-2 text-xs text-cf-text-muted">
                    Модель раскладывает находки на «похоже на настоящее
                    нарушение», «законное исключение» и «неясно — к
                    владельцу»; решение по «неясно» — за вами.
                  </p>
                  {dl?.review_summary && (
                    <dl className="mb-2 space-y-1 text-sm">
                      {Object.entries({
                        'Кандидатов прохода':
                          dl.review_summary.candidates,
                        'Сверено проходом': dl.review_summary.reviewed,
                        'Не сверено': dl.review_summary.skipped,
                        ...dl.review_summary.by_verdict,
                      }).map(([k, v]) => (
                        <div key={k} className="flex justify-between">
                          <dt className="text-cf-text-muted">{k}</dt>
                          <dd className="font-medium text-cf-text">
                            {v as number}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  {(dl?.review_problems || []).length > 0 && (
                    <p className="mb-2 text-xs text-cf-error">
                      {(dl!.review_problems || []).join('; ')}
                    </p>
                  )}
                  <Button size="sm" onClick={launchDataReview}
                          disabled={busyState}
                          title="Модель перечитывает каждую спорную
находку с доказательствами из данных и раскладывает: похоже на
настоящее нарушение / законное исключение / неясно — к владельцу">
                    <Play className="h-4 w-4" />
                    {dl?.review_summary ? 'Сверить заново'
                      : 'Сверить спорное'}
                  </Button>
                </div>
              </>
            ) : (
              <p className="text-sm text-cf-text-muted">
                Отчёт появится после прогона проверок данных.
              </p>
            )}
          </div>
        )}
        {selected === 'output' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Итог цикла
              {status?.readiness?.vmp_state && (
                <span
                  className={'ml-2 rounded px-1.5 py-0.5 text-[11px] '
                    + 'font-semibold '
                    + (status.readiness.vmp_state === 'ВМП'
                       ? 'bg-emerald-100 text-emerald-700'
                       : 'bg-amber-100 text-amber-700')}
                  title={status.readiness.vmp_state === 'ВМП'
                    ? 'ВМП — верифицированная модель правил: открытых '
                      + 'вопросов анкеты нет'
                    : 'ПВМП — предварительный ВМП: модель до закрытия '
                      + 'вопросов анкеты (открытых: '
                      + String(status.readiness.vmp_open_questions
                               ?? '?') + ')'}>
                  {status.readiness.vmp_state}
                </span>
              )}
            </h2>
            <EffectiveParameters runId={runId} />
            {/* Четыре принципа ВМП (владелец 2026-09-02): светофор по
                машинным фактам модели — противоречия, определённость
                (посадки), полнота постановки, биекция (покрытие клауз
                документов правил: замок «молчание не вердикт»).
                A12-заявления (12.09.2026): блок называет ПЕРЕЧИСЛЕННЫЕ
                машинные факты, а не смысловую полноту. Слова предела
                панель НЕ ХРАНИТ: они приходят одним полем сервера
                `principles.P4.meaning_completeness` (носитель один —
                cdpm.coverage.MEANING_GAP) и печатаются ровно один раз
                подписью под блоком; поля нет — панель о полноте
                молчит, а не сочиняет своих слов. */}
            {status?.principles && (
              <div>
                <h3 className={SECTION_H3}
                    title="Четыре принципа эффективного ВМП: каждая строка
— машинные факты свода, не мнение модели. Зелёный — перечисленные в
строке факты сошлись на текущем своде; жёлтый — есть открытое.
Зелёный блок смысловой полноты ВМП не доказывает.">
                  Четыре принципа ВМП
                </h3>
                <ul className="space-y-0.5 text-xs">
                  {(['P1', 'P2', 'P3', 'P4'] as const).map(key => {
                    const p = status.principles![key]
                    let detail = ''
                    // Основание строки словами (A11): на чём стоит
                    // вердикт индикатора — подсказкой при наведении.
                    let basis: string | undefined
                    if (key === 'P1') {
                      const r = status.principles!.P1
                      detail = `открытых противоречий ${r.open_contradictions}`
                        + ` из ${r.contradictions_total}; конфликтов`
                        + ` параметров ${r.param_conflicts}`
                    } else if (key === 'P2') {
                      const r = status.principles!.P2
                      detail = `посадки A ${r.fit.A} · B ${r.fit.B}`
                        + ` · C ${r.fit.C} · D ${r.fit.D}`
                        + (r.fit.none ? ` · без посадки ${r.fit.none}` : '')
                        + `; B без подтверждения ${r.b_pending};`
                        + ` открытых развилок ${r.open_forks}`
                      // Пробелы каталога шаблонов: сколько посадок C
                      // уже заявлено (K может быть больше C — строки
                      // уходят из C после подачи).
                      const cr = status.catalog_requests
                      if (cr && !cr.error && (cr.submitted || r.fit.C)) {
                        detail += ` · заявлено в каталог ${cr.submitted ?? 0}`
                          + ` из ${r.fit.C}`
                      }
                    } else if (key === 'P3') {
                      const r = status.principles!.P3
                      detail = `целевых ${r.objectives}`
                        + (r.objective_zones.length
                           ? ` (${r.objective_zones.join(', ')})` : '')
                        + `; мягких без веса ${r.soft_without_weight.length}`
                        + `; открытых вопросов постановки`
                        + ` ${r.open_statement_questions}`
                        + (r.excluded_until_data.length
                           ? `; исключено до данных`
                             + ` ${r.excluded_until_data.length}` : '')
                    } else {
                      const r = status.principles!.P4
                      // A12: основание строки приходит с сервера и
                      // уже кончается словами предела — вторым
                      // разом их приклеивать нельзя: предел виден
                      // ровно один раз, подписью под блоком.
                      basis = r.basis
                      detail = r.coverage
                        ? `клауз ${r.coverage.clauses}: покрыто строками`
                          + ` ${r.coverage.covered_by_rows}, «не правило»`
                          + ` ${r.coverage.not_rule}, без вердикта`
                          + ` ${r.coverage.missing}`
                        : 'покрытие клауз: нет комплекта заморозки'
                      detail += `; строк без источника ${r.rows_without_source}`
                      // A11: те же факты, что у замка выпуска пакета —
                      // содержание (частично покрытые, величины без
                      // адресата, нормативные без строк) и висячие
                      // якоря; не судившееся названо, не скрыто.
                      if (r.content_judged) {
                        detail += `; частично покрытых ${r.covered_partially ?? 0}`
                          + `, величин без адресата ${r.values_without_addressee ?? 0}`
                          + `, нормативных без строк ${r.normative_without_rows ?? 0}`
                      } else if (r.coverage) {
                        detail += '; содержание не судилось'
                      }
                      if (r.anchors_judged) {
                        detail += `; висячих якорей ${r.dangling_anchors ?? 0}`
                      } else if (r.coverage) {
                        detail += '; якоря не судились'
                      }
                    }
                    return (
                      <li key={key} className="flex items-baseline gap-2"
                          title={basis}>
                        <span className={clsx('inline-block h-2 w-2 rounded-full',
                          p.ok ? 'bg-emerald-500' : 'bg-amber-500')} />
                        <span className="font-medium text-cf-text">
                          {p.title}
                        </span>
                        <span className="text-cf-text-muted">{detail}</span>
                      </li>
                    )
                  })}
                </ul>
                <PackageReadinessProblems problems={status.principles!.P4.problems} />
                {/* Предел заявления (A12-заявления, 12.09.2026):
                    подпись под блоком — чтобы её видели БЕЗ наведения.
                    Слова приходят с сервера; своих слов о полноте у
                    панели нет. */}
                {status.principles!.P4.meaning_completeness && (
                  <p className="mt-1 text-[11px] text-cf-text-muted"
                     title="Блок судит машинные факты покрытия (клаузы с
вердиктом, якоря, строки свода, величины, содержание). Смысловая
полнота ВМП этими фактами не доказывается: покрытая клауза не равна
всем обязательствам внутри неё.">
                    Предел заявления:{' '}
                    {status.principles!.P4.meaning_completeness}
                  </p>
                )}
                {status.coverage && status.coverage.missing.length > 0 && (
                  <p className="mt-1 text-[11px] text-amber-700"
                     title="Каждая клауза документов правил обязана быть
покрыта строкой свода либо признана аудитором «не правило» с
причиной. Клауза без вердикта не даёт циклу успеха и
останавливает сборку пакета.">
                    Клаузы без вердикта покрытия:{' '}
                    {status.coverage.missing.slice(0, 6).map(x =>
                      `${x.sid}/${x.anchor}`).join(', ')}
                    {status.coverage.missing.length > 6
                      ? ` … всего ${status.coverage.missing.length}` : ''}
                  </p>
                )}
              </div>
            )}
            {/* Пробелы каталога шаблонов (шаг 6 каталога, часть CDPM):
                строки с посадкой C, заявки прогона и их состояния. */}
            <CatalogRequestsSection
              runId={runId}
              line={status?.catalog_state}
              runState={status?.state}
              busy={busyState || paused}
              onStarted={() => { armPolling(); refresh() }} />
            {status?.readiness?.matrix && (
              <div>
                <h3 className={SECTION_H3}
                    title="ЦФ-1: корректность свода по режимам задачи.
optimization — есть целевая, критерий = целевая ± штрафы мягких;
feasibility-soft — минимум штрафов мягких; feasibility-hard — любое
допустимое решение. Ячейки вложены сверху вниз">
                  Матрица корректности
                </h3>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-cf-text-muted">
                      <th className="text-left font-normal">режим</th>
                      <th className="font-normal">пейринг</th>
                      <th className="font-normal">ростеринг</th>
                    </tr>
                  </thead>
                  <tbody>
                    {['optimization', 'feasibility-soft',
                      'feasibility-hard'].map(mode => (
                      <tr key={mode}>
                        <td className="text-cf-text-muted">{mode}</td>
                        {['pairing', 'rostering'].map(task => {
                          const cell = status!.readiness!
                            .matrix![task]?.[mode]
                          const label = cell?.ready ? '✓'
                            : cell?.state === 'отказ подтверждён'
                              ? 'отказ' : '—'
                          return (
                            <td key={task}
                                title={cell?.note
                                  || Object.entries(cell?.reasons
                                       || {}).map(([k, v]) =>
                                       `${k}: ${v}`).join('; ')
                                  || 'готово'}
                                className={clsx('text-center',
                                  cell?.ready
                                    ? 'text-emerald-600'
                                    : cell?.state
                                        === 'отказ подтверждён'
                                      ? 'text-cf-text-muted'
                                      : 'text-amber-600')}>
                              {label}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {/* Карта постановки (пакет трёх 2026-08-30, п.1):
                видна независимо от вердикта аудит-цикла — источник
                у неё книга данных, не итог цикла. */}
            <button
              className={'flex items-center gap-1 text-sm '
                + (dl?.files?.length
                  ? 'text-cf-primary hover:underline'
                  : 'cursor-not-allowed text-cf-text-muted')}
              disabled={!dl?.files?.length}
              title={dl?.files?.length
                ? 'Сеть станций и персонал из книги данных прогона '
                  + '(детерминированная проекция; карта мира — '
                  + 'только при полных координатах, включает человек)'
                : 'Карта появится после загрузки книги операционных '
                  + 'данных'}
              onClick={() => setShowMap(true)}>
              Карта сети и персонал
            </button>
            {exchange?.verdict ? (
              <>
                <StatusBadge kind={shownVerdict(exchange) === 'успех'
                  ? 'ok' : 'warn'}>
                  {shownVerdict(exchange)} за
                  {' '}{exchange.verdict.rounds} обменов
                </StatusBadge>
                <a className="flex items-center gap-1 text-sm
                              text-cf-primary hover:underline"
                   href={exchangeSvodUrl(runId,
                                         exchange.verdict.rounds)}
                   target="_blank" rel="noreferrer">
                  Финальная версия свода
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
                <a className="flex items-center gap-1 text-sm
                              text-cf-primary hover:underline"
                   href={exchangeAnketaUrl(runId,
                                           exchange.verdict.rounds)}
                   target="_blank" rel="noreferrer"
                   title="Итог — всегда пара: свод и анкета одной
версии">
                  Финальная версия анкеты
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
                {arbiter?.summary && (
                  <>
                    <a className="flex items-center gap-1 text-sm
                                  text-cf-primary hover:underline"
                       href={arbiterSvodUrl(runId)}
                       target="_blank" rel="noreferrer">
                      Свод после арбитража
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                    <a className="flex items-center gap-1 text-sm
                                  text-cf-primary hover:underline"
                       href={arbiterAnketaUrl(runId)}
                       target="_blank" rel="noreferrer">
                      Анкета после арбитража
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </>
                )}
                <div className="space-y-1.5">
                  <h3 className={SECTION_H3}>
                    Скачать для решателя
                  </h3>
                  <a className="flex items-center gap-1 text-sm
                                text-cf-primary hover:underline"
                     href={exportUrl(runId, 'task-package')}
                     download={`${runId}-task-package.zip`}
                     title={'Самодостаточный zip: правила, целевые, '
                       + 'веса, разрешённые режимы и очищенные '
                       + 'данные с контрольными суммами — единица '
                       + 'обмена CDPM → COSMOS (спецификация v1.0). '
                       + 'Доступен, когда матрица корректности '
                       + 'разрешает хотя бы один режим'}>
                    Пакет постановки COSMOS (zip)
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                  <div className="my-3 space-y-1.5">
                    <Button
                      size="sm"
                      leadingIcon={<Send className="h-3.5 w-3.5" />}
                      onClick={sendNow}
                      disabled={sending
                                || status?.readiness?.state === 'а'}
                      title={'Онлайн-передача: собрать пакет, прогнать '
                        + 'собственный гейт и доставить приёмнику COSMOS '
                        + 'по каналу контура (та же ручка '
                        + 'task-package-send). Доставка ЗАМЕНЯЕТ текущий '
                        + 'пакет на стороне COSMOS'}>
                      {sending ? 'Передаётся…' : 'Передать в COSMOS'}
                    </Button>
                    {delivery?.sent ? (
                      <p className="text-xs text-emerald-600"
                         title={delivery.detail || ''}>
                        Передано {delivery.at || ''}: sha{' '}
                        {(delivery.sha || '').slice(0, 12)}…, вердикт{' '}
                        {delivery.verdict || 'OK'}
                        {delivery.attempts && delivery.attempts > 1
                          ? `, попыток ${delivery.attempts}` : ''}
                        {delivery.already_accepted
                          ? ' (пакет уже был принят)' : ''}
                        {delivery.cosmos_run
                          ? (delivery.repeat
                              ? `; пакет уже лежит в прогоне COSMOS ${delivery.cosmos_run}`
                              : `; прогон COSMOS ${delivery.cosmos_run}`)
                          : ''}
                        {delivery.mg
                          ? ' — ' + Object.entries(delivery.mg)
                              .map(([k, v]) => `${k === 'pairing'
                                ? 'пейринг' : k === 'rostering'
                                ? 'ростеринг' : k}: ${v?.state || '?'}`)
                              .join(', ')
                          : ''}
                        {delivery.mismatch?.length
                          ? `; расхождения: ${delivery.mismatch.join('; ')}`
                          : ''}
                        {delivery.detail ? ` — ${delivery.detail}` : ''}
                      </p>
                    ) : delivery && delivery.configured === false ? (
                      <p className="text-xs text-amber-600">
                        Канал выключен на этой площадке — пакет
                        передаётся файлом (zip выше).
                      </p>
                    ) : delivery && !delivery.sent && delivery.attempts ? (
                      <p className="text-xs text-red-600">
                        Не передано: {delivery.detail || 'приёмник отказал'}
                      </p>
                    ) : null}
                  </div>
                  {status?.readiness?.state === 'а' ? (
                    <p className="text-xs text-amber-600"
                       title="Экспорт откроется, когда в зонах задачи
не останется блокированных строк свода и открытых вопросов анкеты">
                      Экспорт закрыт: данных пока недостаточно ни для
                      пейринга, ни для ростеринга — ответьте на
                      вопросы анкеты.
                    </p>
                  ) : (
                    <>
                      {[['objective', 'Критерий оптимизации (JSON)',
                         'Целевые функции пейринга и ростеринга: '
                         + 'мягкие правила как компоненты с весами '
                         + '(вес — только названный в формальной '
                         + 'записи) и сырым ответом авиакомпании '
                         + 'на вопрос целевой функции'],
                        ['rave', 'Rave (Jeppesen)',
                         'Скелет правил на языке Rave: правило на '
                         + 'каждую строку свода, формальная запись и '
                         + 'трассировка в remark; выражения — к '
                         + 'доводке инженером Carmen'],
                        ['minizinc', 'MiniZinc (CP-решатели)',
                         'Компилируемый скелет модели: ограничение '
                         + 'на каждое жёсткое правило (тело — '
                         + 'заглушка к доводке), мягкие — слагаемые '
                         + 'целевой; данные подключаются отдельным '
                         + '.dzn, любой CP-решатель получает FlatZinc '
                         + 'штатной компиляцией'],
                        ['csv', 'CSV (интеграторы)',
                         'Таблица правил: идентификатор, зона, тип, '
                         + 'формулировка, источники, блокировки'],
                        ['json', 'JSON (модель CDPM)',
                         'Полная машинная модель — свод и анкета '
                         + 'одним документом (контракт П-1)'],
                      ].map(([fmt, label, tip]) => (
                        <a key={fmt}
                           className="flex items-center gap-1 text-sm
                                      text-cf-primary hover:underline"
                           href={exportUrl(runId, fmt)}
                           download
                           title={tip}>
                          {label}
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                      ))}
                      <button
                        type="button"
                        className="flex items-center gap-1 text-sm
                                   text-cf-primary hover:underline"
                        title="Спецификация COS-MERIDIAN v1.17 — обмен
операционными ДАННЫМИ (правила в ней — только ссылка presetId):
выгружается книга данных прогона как dataset для передачи в COS.
Книга должна быть полна по обязательным полям спецификации — иначе
честный отказ с перечнем недостающего"
                        onClick={() => {
                          const preset = window.prompt(
                            'presetId — идентификатор пресета правил '
                            + 'на стороне COS (обязателен по '
                            + 'спецификации):')
                          if (!preset) return
                          window.open(exportUrl(runId, 'meridian')
                            + `?preset_id=${
                              encodeURIComponent(preset)}`)
                        }}
                      >
                        Meridian (dataset COS)
                        <ExternalLink className="h-3.5 w-3.5" />
                      </button>
                    </>
                  )}
                </div>
                {/* Граница CDPM (решение владельца 04.09): работа
                    модуля заканчивается пакетом постановки. Расчёт
                    пейринга и ростеринга, сертификация решений, карта
                    ростера и экспорт MPS/LP — у системы-потребителя
                    пакета. */}
                <p className="mt-3 text-xs text-cf-text-muted"
                   title="CDPM не знает и не должен знать, что происходит
с пакетом дальше: расчёт, сертификация решений и их выгрузка —
у системы-потребителя пакета (в COSMOS — страница «Пакет постановки»:
блок «Расчёт по пакету» панели «Постановка (ВМП)»)">
                  Дальше — вне CDPM: скачайте пакет для решателя и
                  передайте его системе расчёта (в COSMOS — страница
                  «Пакет постановки»).
                </p>
                <div className="space-y-1.5">
                  <h3 className={SECTION_H3}>
                    Непротиворечивость (SMT)
                  </h3>
                  {status?.smt_state === 'SMT-проверка идёт' ? (
                    <p className="text-xs text-cf-primary">
                      Проверка идёт: модель переводит правила в
                      SMT, Z3 ищет конфликт…
                    </p>
                  ) : (
                    <Button size="sm" className="my-2"
                            title="Жёсткие правила свода переводятся
языковой моделью в SMT-LIB, решатель Z3 проверяет их совместность;
конфликт называется парами правил (unsat core). Перевод сохраняется
в отчёте и проверяем глазами"
                            onClick={async () => {
                              try {
                                await startSmtVerify(runId)
                                armPolling()
                                notify.success('SMT-проверка запущена')
                                refresh()
                              } catch (e) {
                                notify.error(String(e))
                              }
                            }}>
                      Проверить непротиворечивость
                    </Button>
                  )}
                  {status?.smt_report && (
                    <p className={clsx('text-xs',
                      status.smt_report.verdict === 'sat'
                        && (status.smt_report.n_translated ?? 0)
                          >= (status.smt_report.n_rules ?? 0)
                        ? 'text-emerald-600'
                        : status.smt_report.verdict === 'unsat'
                          ? 'text-red-600' : 'text-amber-600')}>
                      {status.smt_report.status}
                      {status.smt_report.n_translated !== undefined
                        && ` (переведено ${
                          status.smt_report.n_translated} из ${
                          status.smt_report.n_rules} правил${
                          (status.smt_report.untranslated || []).length > 0
                            ? ' — проверка неполна, не переведены: '
                              + status.smt_report.untranslated!.join(', ')
                            : ''})`}
                      {(status.smt_report.core || []).length > 0
                        && ` — конфликтуют: ${
                          status.smt_report.core!.join(', ')}`}
                    </p>
                  )}
                </div>
              </>
            ) : (
              <p className="text-sm text-cf-text-muted">
                Итог появится после аудит-цикла (без тупиков — сразу,
                с тупиками — после арбитража).
              </p>
            )}
          </div>
        )}
        {selected === 'arbiter' && (
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-cf-text">
              Арбитр — разрешение тупиков цикла
            </h2>
            <p className="text-xs text-cf-text-muted">
              Тупик — замечание, на котором аудитор настаивает, а
              обработчик дважды отклонил. Третья независимая модель
              решает каждый спор по клаузе-источнику: обработчик прав,
              аудитор прав (исполнение обязательно) или в анкету —
              спорное чтение решает авиакомпания.
            </p>
            <Button
              onClick={launchArbiter}
              disabled={busyState || !(arbiter?.deadlocks || []).length}
              leadingIcon={<Play className="h-4 w-4" />}
              title="Передать тупики цикла третьей независимой модели.
Вердикт «аудитор прав» исполняется обработчиком обязательно; «в
анкету» — спорное чтение уходит на решение авиакомпании; «обработчик
прав» закрывает находку навсегда"
            >
              {status?.state === 'арбитраж идёт'
                ? 'Арбитраж идёт…' : 'Разрешить тупики'}
            </Button>
            {!(arbiter?.deadlocks || []).length && (
              <p className="text-xs text-cf-text-muted">
                Тупиков нет — арбитражу нечего разбирать.
              </p>
            )}
            <p className="text-xs text-cf-text-muted">
              Модель арбитра — шестерёнка на блоке; независимость от
              обработчика и аудитора — на вашей совести (третий
              провайдер).
            </p>
            {(arbiter?.deadlocks || []).length > 0
              && !arbiter?.summary && (
              <div className="rounded-lg border border-cf-warning/40
                              bg-cf-warning/10 p-3">
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-warning">
                  Тупики цикла
                </h3>
                {arbiter!.deadlocks.map(d => (
                  <p key={d.id} className="text-xs text-cf-text">
                    {d.id} · {d.severity ?? ''} ·
                    {' '}{d.defect_class ?? ''}
                    {d.rule ? ` · ${d.rule}` : ''}
                  </p>
                ))}
              </div>
            )}
            {arbiter?.summary && (
              // A09: исходов у спора не три, а пять — три решения,
              // «не разрешено» (решения нет) и «не исполнено» (замок
              // кода запретил заплату, спор ушёл владельцу). Сумма
              // трёх решений меньше числа рассмотренных ровно на
              // число неразрешённых: спор не исчезает из счёта.
              <dl className="space-y-1 text-sm">
                {Object.entries({
                  'Тупиков рассмотрено': arbiter.summary.deadlocks,
                  'Обработчик прав':
                    arbiter.summary.by_verdict['обработчик прав'] ?? 0,
                  'Аудитор прав':
                    arbiter.summary.by_verdict['аудитор прав'] ?? 0,
                  'В анкету (решает авиакомпания)':
                    arbiter.summary.by_verdict['в анкету'] ?? 0,
                  'Не разрешено арбитром':
                    arbiter.summary.unresolved ?? 0,
                  'Ждут ответа владельца':
                    arbiter.summary.awaiting_owner ?? 0,
                  'Не исполнено (вопрос владельцу)':
                    arbiter.summary.unenforced ?? 0,
                  // Ревью A09 №2, находка 7: счётчик был в ответе, но
                  // нигде не показывался — лишний идентификатор ответа
                  // арбитра оператор видел только красным гейтом.
                  'Лишних упоминаний в ответе арбитра':
                    arbiter.summary.unresolved_foreign ?? 0,
                  'Исполнено обработчиком': arbiter.summary.enforced,
                  'Вопросов добавлено':
                    arbiter.summary.added_questions,
                }).map(([k, v]) => (
                  <div key={k} className="flex justify-between"
                       title={ARBITER_ROW_TIPS[k]}>
                    <dt className="text-cf-text-muted">{k}</dt>
                    <dd className="font-medium text-cf-text">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {/* Счётчик без причины — это «исполнений 0» и красный
                гейт без объяснения (урок A3.5): по каждому спору
                показывается «тупик → причина» словами. */}
            {(arbiter?.summary?.unresolved_deadlocks || []).length > 0 && (
              <div className="rounded-lg border border-cf-warning/40
                              bg-cf-warning/10 p-3"
                   title={ARBITER_ROW_TIPS['Не разрешено арбитром']}>
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-warning">
                  Не разрешено арбитром — спор остаётся открытым
                </h3>
                {arbiter!.summary!.unresolved_deadlocks!.map(u => (
                  <p key={u.deadlock}
                     className="text-xs text-cf-text">
                    {u.deadlock} · {u.reason}
                  </p>
                ))}
              </div>
            )}
            {(arbiter?.summary?.awaiting_owner_deadlocks
              || []).length > 0 && (
              <div className="rounded-lg border border-cf-warning/40
                              bg-cf-warning/10 p-3"
                   title={ARBITER_ROW_TIPS['Ждут ответа владельца']}>
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-warning">
                  Ждут вашего ответа — спор передан вам вопросом анкеты
                </h3>
                {arbiter!.summary!.awaiting_owner_deadlocks!.map(a => (
                  <p key={a.deadlock}
                     className="text-xs text-cf-text">
                    {a.deadlock} · {a.question} · {a.reason}
                  </p>
                ))}
              </div>
            )}
            {(arbiter?.summary?.unenforced_deadlocks || []).length > 0 && (
              <div className="rounded-lg border border-cf-warning/40
                              bg-cf-warning/10 p-3"
                   title={ARBITER_ROW_TIPS[
                     'Не исполнено (вопрос владельцу)']}>
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-warning">
                  Вердикт не исполнен — причина по каждому тупику
                </h3>
                {arbiter!.summary!.unenforced_deadlocks!.map(u => (
                  <p key={u.deadlock}
                     className="text-xs text-cf-text">
                    {u.deadlock} · {u.reason}
                  </p>
                ))}
              </div>
            )}
            {arbiter?.summary && (
              <div className="space-y-1">
                <a className="flex items-center gap-1 text-sm
                              text-cf-primary hover:underline"
                   href={arbiterSvodUrl(runId)}
                   target="_blank" rel="noreferrer"
                   title="Свод после исполнения вердиктов арбитра">
                  Свод после арбитража
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
                <a className="flex items-center gap-1 text-sm
                              text-cf-primary hover:underline"
                   href={arbiterAnketaUrl(runId)}
                   target="_blank" rel="noreferrer"
                   title="Анкета после арбитража: вердикты «в анкету»
добавляют вопросы-развилки">
                  Анкета после арбитража
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              </div>
            )}
            {(arbiter?.report?.verdicts || []).length > 0 && (
              <div className="space-y-2">
                <h3 className={SECTION_H3}>
                  Вердикты с основаниями
                </h3>
                {arbiter!.report!.verdicts.map(v => (
                  <div key={v.deadlock} className="card space-y-1 !p-3">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs
                                       text-cf-text-muted">
                        {v.deadlock}
                      </span>
                      <span className={clsx('status-badge',
                        v.verdict === 'в анкету'
                          ? 'bg-cf-warning/15 text-cf-warning'
                          : 'bg-cf-success/15 text-cf-success')}>
                        {v.verdict ?? '—'}
                      </span>
                    </div>
                    {v.basis && (
                      <p className="text-xs text-cf-text">{v.basis}</p>
                    )}
                  </div>
                ))}
              </div>
            )}
            {(arbiter?.report?.patch_rejections || []).length > 0 && (
              <div className="rounded-lg border border-cf-error/40
                              bg-cf-error/10 p-3">
                <h3 className="mb-1 text-xs font-semibold
                               text-cf-error">
                  Отбраковка исполнения
                </h3>
                {arbiter!.report!.patch_rejections.map(r => (
                  <p key={r.answer} className="text-xs text-cf-text">
                    {r.answer}: {r.reasons.join('; ')}
                  </p>
                ))}
              </div>
            )}
            {(arbiter?.gate_problems || []).map(p => (
              <p key={p} className="text-xs text-cf-error">{p}</p>
            ))}
            {status?.state === 'отказ арбитража' && status.error && (
              <p className="text-xs text-cf-error">{status.error}</p>
            )}
          </div>
        )}
      </aside>
      )}
    </div>
  )
}
