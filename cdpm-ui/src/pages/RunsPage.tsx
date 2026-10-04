import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import notify from '../components/common/StyledToast'
import ConfirmDialog from '../components/common/ConfirmDialog'
import { ArrowDown, ArrowDownAZ, ArrowUp, ArrowUpDown, CheckCircle,
         CheckSquare, ChevronRight, Clock, FileText, GitBranch, List,
         Loader2, Play, Plus, RefreshCw, Search, Snowflake, Square,
         Trash2, XCircle }
  from 'lucide-react'
import { archiveRuns, catalogLink, humanState, isStalledState, listRuns,
         putRunsOrder, type RunSummary } from '../api'
import { RunTreeBrowser } from '../components/RunTree'
import { useCatalogStore } from '../stores/catalogStore'

// Версия каталога шаблонов в карточке прогона. Карточка целиком —
// ссылка на прогон, поэтому вложенной ссылки здесь быть не может:
// открытие службы каталога делается щелчком с остановкой всплытия, и
// без настроенной службы остаётся простой подписью.
function CatalogChip({ version }: { version: string }) {
  const url = useCatalogStore(s => s.url)
  const load = useCatalogStore(s => s.load)
  // Настройка тянется один раз на всю панель (хранилище само себя
  // сторожит): страница не обязана знать, что её уже спросили меню.
  useEffect(() => { load() }, [load])
  const href = catalogLink(url, `/versions/${encodeURIComponent(version)}`)
  if (!href) {
    return <span className="text-gray-300"
                 title="служба каталога не настроена (CREW_CATALOG_URL)">
      {version}
    </span>
  }
  const open = (e: { preventDefault: () => void
                     stopPropagation: () => void }) => {
    e.preventDefault()
    e.stopPropagation()
    window.open(href, '_blank', 'noopener,noreferrer')
  }
  return (
    <span
      role="link"
      tabIndex={0}
      onClick={open}
      onKeyDown={e => { if (e.key === 'Enter') open(e) }}
      title="Открыть версию каталога в службе каталога шаблонов"
      className="cursor-pointer text-indigo-300 hover:underline"
    >{version}</span>
  )
}

// Список прогонов — порт SessionsPage CodeForge: крупный заголовок с
// подзаголовком, поиск, пилюли-фильтры состояний со счётчиками,
// ТЁМНЫЕ карточки списка (фирменная поверхность эталона — тёмная в
// обеих темах) с акцентной левой кромкой и шевроном.
//
// Поручение владельца 06.09.2026 (после демо): отметить прогоны и
// удалить; переместить прогон вверх/вниз по списку; упорядочение по
// дате создания, по имени, вверх/вниз. «Удалить» — перенос папки в
// архив площадки (обратимо), прогон с идущей стадией сервер не трогает.

type Bucket = 'все' | 'заморожено' | 'в работе' | 'готово' | 'отказ'
type SortKey = 'order' | 'created' | 'name'
type SortDir = 'asc' | 'desc'
const SORT_STORAGE_KEY = 'cdpm.runs.sort.v1'

const SORT_LABEL: Record<SortKey, string> = {
  order: 'ручной порядок',
  created: 'дата создания',
  name: 'имя',
}

// Карточка прогона учитывает ОБЕ линии: правил и данных (замечание
// владельца: «проверки данных идут», а список показывал «готово»).
function bucketOfRun(r: { state?: string
                          data_state?: string | null }): Bucket {
  const ds = r.data_state || ''
  if (ds.endsWith('идёт') || ds.startsWith('на паузе')) {
    return 'в работе'
  }
  if (ds.startsWith('отказ') || ds.startsWith('красный')) return 'отказ'
  return bucketOf(r.state)
}

function bucketOf(state: string | undefined): Bucket {
  const s = state || ''
  // Пауза (ПС-1) — прогон в работе; «остановлено» — прогон снова
  // ждёт запуска стадии, как замороженный.
  if (s.endsWith('идёт') || s.startsWith('на паузе')) return 'в работе'
  if (s.startsWith('отказ') || s.startsWith('красный')) return 'отказ'
  if (s === 'заморожено' || s === 'новый'
      || s.startsWith('остановлено')) return 'заморожено'
  return 'готово'
}

const BUCKET_META: Record<Bucket, {
  icon: ReactNode; pillActive: string
}> = {
  'все': { icon: null,
    pillActive: 'bg-indigo-100 text-indigo-700 border-indigo-300'
      + ' dark:bg-indigo-500/30 dark:text-indigo-200'
      + ' dark:border-indigo-500/50' },
  'заморожено': { icon: <Snowflake className="h-4 w-4 text-cyan-400" />,
    pillActive: 'bg-cyan-100 text-cyan-700 border-cyan-300'
      + ' dark:bg-cyan-500/20 dark:text-cyan-300'
      + ' dark:border-cyan-500/50' },
  'в работе': { icon: <Play className="h-4 w-4 text-blue-400" />,
    pillActive: 'bg-blue-100 text-blue-700 border-blue-300'
      + ' dark:bg-blue-500/30 dark:text-blue-300'
      + ' dark:border-blue-500/50' },
  'готово': { icon: <CheckCircle className="h-4 w-4 text-green-400" />,
    pillActive: 'bg-green-100 text-green-700 border-green-300'
      + ' dark:bg-green-500/20 dark:text-green-300'
      + ' dark:border-green-500/50' },
  'отказ': { icon: <XCircle className="h-4 w-4 text-red-400" />,
    pillActive: 'bg-red-100 text-red-700 border-red-300'
      + ' dark:bg-red-500/30 dark:text-red-300'
      + ' dark:border-red-500/50' },
}

// Пилюля состояния НА карточке: тона эталона для тёмной поверхности.
function statusPill(state: string | undefined): {
  cls: string; icon: ReactNode
} {
  // «Остановился, но не чисто» (стагнация, лимит) — предупреждение,
  // не зелёное «готово» (замечание владельца 2026-08-21).
  if (isStalledState(state || '')) {
    return { cls: 'bg-amber-500/20 text-amber-300',
      icon: <Clock className="h-4 w-4" /> }
  }
  switch (bucketOf(state)) {
    case 'в работе':
      return { cls: 'bg-blue-500/30 text-blue-300 font-semibold',
        icon: <Play className="h-4 w-4 animate-pulse" /> }
    case 'отказ':
      return { cls: 'bg-red-500/30 text-red-300 font-semibold',
        icon: <XCircle className="h-4 w-4" /> }
    case 'заморожено':
      return { cls: 'bg-cyan-500/20 text-cyan-300',
        icon: <Snowflake className="h-4 w-4" /> }
    case 'готово':
      return { cls: 'bg-green-500/20 text-green-300',
        icon: <CheckCircle className="h-4 w-4" /> }
    default:
      return { cls: 'bg-gray-500/20 text-gray-300',
        icon: <Clock className="h-4 w-4" /> }
  }
}

function runBusy(r: RunSummary): boolean {
  return bucketOfRun(r) === 'в работе'
}

function nameOf(r: RunSummary): string {
  return (r.title || r.run).toLowerCase()
}

function createdOf(r: RunSummary): number {
  const t = r.created_at ? Date.parse(r.created_at) : NaN
  return Number.isNaN(t) ? 0 : t
}

function fmtCreated(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const d = new Date(t)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getDate())}.${two(d.getMonth() + 1)}.${d.getFullYear()}`
    + ` ${two(d.getHours())}:${two(d.getMinutes())}`
}

function loadSort(): { key: SortKey; dir: SortDir } {
  try {
    const raw = localStorage.getItem(SORT_STORAGE_KEY)
    if (raw) {
      const got = JSON.parse(raw) as { key?: string; dir?: string }
      const key = (['order', 'created', 'name'] as SortKey[])
        .find(k => k === got.key)
      const dir = got.dir === 'desc' ? 'desc' : 'asc'
      if (key) return { key, dir }
    }
  } catch { /* хранилище недоступно — умолчание */ }
  return { key: 'order', dir: 'asc' }
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null)
  // Взгляд на прогоны: список (плоский, с фильтрами и порядком) либо
  // дерево (корни входов и ветви на них — проект «Запись о прогоне»,
  // шаг 3). Поиск, пилюли состояний и ручной порядок относятся к
  // списку: у дерева свой порядок — по корням и времени объявления.
  const [view, setView] = useState<'список' | 'дерево'>('список')
  const [query, setQuery] = useState('')
  const [bucket, setBucket] = useState<Bucket>('все')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>(loadSort)
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const refresh = async () => {
    try {
      const got = await listRuns()
      setRuns(got)
      // Отметки на исчезнувших прогонах не держим.
      setSelected(prev => {
        const ids = new Set(got.map(r => r.run))
        const next = new Set([...prev].filter(id => ids.has(id)))
        return next.size === prev.size ? prev : next
      })
    } catch (e) {
      notify.error(String(e))
      setRuns([])
    }
  }
  useEffect(() => { refresh() }, [])
  // Список обновляется при возврате во вкладку и раз в 20 с, пока у
  // какого-то прогона идёт стадия: занятость и состояния не застывают
  // снимком момента открытия (ревью 06.09).
  useEffect(() => {
    const onFocus = () => { void refresh() }
    window.addEventListener('focus', onFocus)
    const anyBusy = (runs || []).some(runBusy)
    const timer = anyBusy
      ? window.setInterval(() => { void refresh() }, 20_000) : null
    return () => {
      window.removeEventListener('focus', onFocus)
      if (timer !== null) window.clearInterval(timer)
    }
  }, [runs])
  useEffect(() => {
    try {
      localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify(sort))
    } catch { /* хранилище недоступно — порядок не запоминается */ }
  }, [sort])

  // Полный список в действующем порядке (ручной порядок — с сервера,
  // поле position); фильтр и поиск применяются поверх него.
  const ordered = useMemo(() => {
    const all = [...(runs || [])]
    const dirMul = sort.dir === 'asc' ? 1 : -1
    all.sort((a, b) => {
      let c = 0
      if (sort.key === 'order') {
        c = (a.position ?? 0) - (b.position ?? 0)
      } else if (sort.key === 'created') {
        c = createdOf(a) - createdOf(b)
      } else {
        c = nameOf(a).localeCompare(nameOf(b), 'ru')
      }
      if (c === 0) c = a.run.localeCompare(b.run)
      return c * dirMul
    })
    return all
  }, [runs, sort])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return ordered.filter(r => {
      const okBucket = bucket === 'все' || bucketOfRun(r) === bucket
      const hay = (r.run + ' ' + (r.title || '') + ' '
        + (r.customer || '') + ' '
        + r.files.join(' ') + ' '
        + (r.state || '')).toLowerCase()
      return okBucket && (q === '' || hay.includes(q))
    })
  }, [ordered, query, bucket])

  // Стрелки «вверх/вниз» действуют в ручном порядке по полному списку
  // и только без фильтра и поиска: иначе сосед мог бы быть скрыт, и
  // нажатие меняло бы порядок «невидимо» (ревью 06.09).
  const unfiltered = bucket === 'все' && query.trim() === ''
  const canReorder = sort.key === 'order' && sort.dir === 'asc'
    && unfiltered
  const move = async (run: string, delta: -1 | 1) => {
    const ids = ordered.map(r => r.run)
    const i = ids.indexOf(run)
    const j = i + delta
    if (i < 0 || j < 0 || j >= ids.length) return
    const next = [...ids]
    next[i] = ids[j]
    next[j] = ids[i]
    setBusy(true)
    try {
      await putRunsOrder(next)
      await refresh()
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (run: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(run)) next.delete(run)
      else next.add(run)
      return next
    })
  }
  const selectShown = () => setSelected(new Set(shown.map(r => r.run)))
  const clearSelection = () => setSelected(new Set())
  const selectedBusy = useMemo(
    () => (runs || []).filter(r => selected.has(r.run) && runBusy(r))
      .map(r => r.run),
    [runs, selected])

  const doArchive = async () => {
    setConfirmOpen(false)
    const ids = [...selected]
    if (!ids.length) return
    setBusy(true)
    try {
      // Свежий снимок перед архивом: занятость с момента открытия
      // страницы могла измениться (ревью 06.09); сервер проверит ещё
      // раз под замком, здесь — понятные слова до запроса.
      const fresh = await listRuns()
      setRuns(fresh)
      const nowBusy = fresh.filter(r => ids.includes(r.run) && runBusy(r))
        .map(r => r.run)
      if (nowBusy.length) {
        notify.error('У прогонов идёт стадия — в архив не переносятся: '
          + nowBusy.join(', '))
        return
      }
      const got = await archiveRuns(ids)
      notify.success(
        `В архив перенесено: ${got.archived.length} `
        + `(${got.archived.join(', ')}); осталось прогонов: ${got.left}`)
      setSelected(new Set())
      setSelectMode(false)
      await refresh()
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  const cycleSortKey = () => {
    setSort(s => ({ ...s, key: s.key === 'order' ? 'created'
      : s.key === 'created' ? 'name' : 'order' }))
  }
  const flipDir = () => {
    setSort(s => ({ ...s, dir: s.dir === 'asc' ? 'desc' : 'asc' }))
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-6xl">
        {/* Шапка страницы — как у эталона */}
        <div className="mb-8 flex flex-wrap items-center justify-between
                        gap-3">
          <div>
            <h1 className="mb-2 text-3xl font-bold text-cf-text">
              Прогоны
            </h1>
            <p className="text-cf-text-muted">
              Формализация документов авиакомпании: заморозка →
              извлечение → аудит-цикл → арбитраж
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              className="flex items-center gap-2 rounded-lg px-3 py-2
                         text-sm text-cf-text-muted transition-colors
                         hover:bg-cf-border hover:text-cf-text
                         dark:hover:bg-gray-700 dark:hover:text-white"
              title="Обновить список"
            >
              <RefreshCw className="h-5 w-5" />
              Обновить
            </button>
            <Link
              to="/runs/new"
              className="flex items-center gap-2 rounded-lg bg-indigo-600
                         px-4 py-2 font-medium text-white
                         transition-colors hover:bg-indigo-700"
              title="Создать прогон: загрузить документы правил и
заморозить их"
            >
              <Plus className="h-5 w-5" />
              Новый прогон
            </Link>
          </div>
        </div>

        {/* Переключатель взгляда: список либо дерево прогонов. Пилюли
            того же вида, что и фильтры состояний, — вкладка не заводит
            своего языка (проект «Запись о прогоне», шаг 3). */}
        <div className="mb-4 flex flex-wrap items-center gap-2">
          {([
            ['список', 'Список', <List key="l" className="h-4 w-4" />,
             'Плоский список прогонов: поиск, фильтры состояний, ручной '
             + 'порядок'],
            ['дерево', 'Дерево', <GitBranch key="t" className="h-4 w-4" />,
             'Дерево прогонов: корень — отпечатки документов правил, '
             + 'данных и ответов на анкеты; под корнем — ветви с рёбрами '
             + 'и отличиями объявления'],
          ] as const).map(([key, label, icon, hint]) => (
            <button
              key={key}
              onClick={() => setView(key)}
              aria-pressed={view === key}
              title={hint}
              className={'inline-flex items-center gap-1.5 rounded-full'
                + ' border px-3 py-1 text-xs font-medium transition-colors '
                + (view === key
                  ? BUCKET_META['все'].pillActive
                  : 'border-cf-border bg-cf-panel text-cf-text-muted'
                    + ' hover:border-cf-text-muted dark:border-gray-600'
                    + ' dark:bg-gray-700/50 dark:text-gray-400'
                    + ' dark:hover:border-gray-500')}
            >
              {icon}
              {label}
            </button>
          ))}
        </div>

        {view === 'дерево' && <RunTreeBrowser />}

        {/* Поиск — как строка поиска эталона */}
        {view === 'список' && runs !== null && runs.length > 0 && (
          <div className="mb-4 relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4
                               -translate-y-1/2 text-cf-text-muted" />
            <input
              type="text"
              placeholder="Поиск по номеру прогона, файлам, состоянию…"
              value={query}
              onChange={e => setQuery(e.target.value)}
              aria-label="Поиск прогонов"
              className="w-full rounded-lg border border-cf-border
                         bg-cf-panel py-2 pl-10 pr-4 text-cf-text
                         placeholder-cf-text-muted focus:border-cf-primary
                         focus:outline-none dark:border-gray-700
                         dark:bg-gray-800 dark:text-white
                         dark:placeholder-gray-500"
            />
          </div>
        )}

        {/* Пилюли-фильтры состояний со счётчиками + порядок и выбор */}
        {view === 'список' && runs !== null && runs.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            {(Object.keys(BUCKET_META) as Bucket[]).map(b => {
              const active = bucket === b
              const count = b === 'все' ? (runs || []).length
                : (runs || []).filter(r => bucketOfRun(r) === b).length
              const cls = active
                ? BUCKET_META[b].pillActive
                : 'border-cf-border bg-cf-panel text-cf-text-muted'
                  + ' hover:border-cf-text-muted dark:border-gray-600'
                  + ' dark:bg-gray-700/50 dark:text-gray-400'
                  + ' dark:hover:border-gray-500'
              return (
                <button
                  key={b}
                  onClick={() => setBucket(b)}
                  aria-pressed={active}
                  title={b === 'все'
                    ? 'Показать все прогоны'
                    : `Показать только прогоны в состоянии «${b}» (по
обеим линиям: правил и данных)`}
                  className={'inline-flex items-center gap-1.5 rounded-full'
                    + ' border px-3 py-1 text-xs font-medium'
                    + ' transition-colors ' + cls}
                >
                  {BUCKET_META[b].icon}
                  {b.charAt(0).toUpperCase() + b.slice(1)}
                  <span className="ml-0.5">({count})</span>
                </button>
              )
            })}
            <span className="mx-1 h-5 w-px bg-cf-border dark:bg-gray-600" />
            <button
              onClick={cycleSortKey}
              title={'Упорядочение списка: ручной порядок → дата '
                + 'создания → имя. Стрелки «вверх/вниз» на карточках '
                + 'действуют в ручном порядке по возрастанию и только '
                + 'без фильтра и поиска'}
              aria-label={`Упорядочение: ${SORT_LABEL[sort.key]}`}
              className="inline-flex items-center gap-1.5 rounded-full
                         border border-cf-border bg-cf-panel px-3 py-1
                         text-xs font-medium text-cf-text-muted
                         transition-colors hover:border-cf-text-muted
                         dark:border-gray-600 dark:bg-gray-700/50
                         dark:text-gray-400 dark:hover:border-gray-500"
            >
              <ArrowDownAZ className="h-4 w-4" />
              {SORT_LABEL[sort.key]}
            </button>
            <button
              onClick={flipDir}
              title={sort.dir === 'asc'
                ? 'По возрастанию — нажмите, чтобы перевернуть'
                : 'По убыванию — нажмите, чтобы перевернуть'}
              aria-label={sort.dir === 'asc'
                ? 'Порядок: по возрастанию' : 'Порядок: по убыванию'}
              className="inline-flex items-center gap-1.5 rounded-full
                         border border-cf-border bg-cf-panel px-3 py-1
                         text-xs font-medium text-cf-text-muted
                         transition-colors hover:border-cf-text-muted
                         dark:border-gray-600 dark:bg-gray-700/50
                         dark:text-gray-400 dark:hover:border-gray-500"
            >
              <ArrowUpDown className="h-4 w-4" />
              {sort.dir === 'asc' ? 'вверх' : 'вниз'}
            </button>
            <span className="mx-1 h-5 w-px bg-cf-border dark:bg-gray-600" />
            <button
              onClick={() => {
                setSelectMode(m => !m)
                if (selectMode) clearSelection()
              }}
              aria-pressed={selectMode}
              title={selectMode
                ? 'Выйти из режима отметки'
                : 'Отметить прогоны, чтобы удалить их (в архив площадки)'}
              className={'inline-flex items-center gap-1.5 rounded-full'
                + ' border px-3 py-1 text-xs font-medium transition-colors '
                + (selectMode
                  ? BUCKET_META['все'].pillActive
                  : 'border-cf-border bg-cf-panel text-cf-text-muted'
                    + ' hover:border-cf-text-muted dark:border-gray-600'
                    + ' dark:bg-gray-700/50 dark:text-gray-400'
                    + ' dark:hover:border-gray-500')}
            >
              <CheckSquare className="h-4 w-4" />
              {selectMode ? 'Отметка: выкл.' : 'Отметить'}
            </button>
            {selectMode && (
              <>
                <button
                  onClick={selectShown}
                  className="rounded-full border border-cf-border bg-cf-panel
                             px-3 py-1 text-xs text-cf-text-muted
                             hover:border-cf-text-muted dark:border-gray-600
                             dark:bg-gray-700/50 dark:text-gray-400"
                  title="Отметить все показанные прогоны"
                >
                  Все показанные
                </button>
                <button
                  onClick={clearSelection}
                  className="rounded-full border border-cf-border bg-cf-panel
                             px-3 py-1 text-xs text-cf-text-muted
                             hover:border-cf-text-muted dark:border-gray-600
                             dark:bg-gray-700/50 dark:text-gray-400"
                  title="Снять все отметки"
                >
                  Снять
                </button>
                <button
                  onClick={() => setConfirmOpen(true)}
                  disabled={busy || selected.size === 0
                    || selectedBusy.length > 0}
                  title={selected.size === 0
                    ? 'Отметьте хотя бы один прогон'
                    : selectedBusy.length
                      ? 'Среди отмеченных есть прогоны с идущей стадией: '
                        + selectedBusy.join(', ')
                        + ' — дождитесь или остановите её'
                      : 'Перенести отмеченные прогоны в архив площадки '
                        + '(папка cdpm-runs-archive рядом с прогонами; '
                        + 'возврат — перенос папки обратно)'}
                  className="inline-flex items-center gap-1.5 rounded-full
                             bg-red-600 px-3 py-1 text-xs font-semibold
                             text-white transition-colors hover:bg-red-500
                             disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                  Удалить отмеченные ({selected.size})
                </button>
              </>
            )}
          </div>
        )}

        {/* Список — тёмные карточки эталона */}
        {view === 'дерево' ? null : runs === null ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-8 w-8 animate-spin text-indigo-500" />
          </div>
        ) : runs.length === 0 ? (
          <div className="rounded-xl border border-gray-700 bg-gray-800
                          p-12 text-center">
            <div className="mb-4 text-gray-400">
              Прогонов пока нет. Загрузите документы правил
              авиакомпании — модуль заморозит их и поведёт по конвейеру.
            </div>
            <Link
              to="/runs/new"
              className="inline-flex items-center gap-2 rounded-lg
                         bg-indigo-600 px-4 py-2 font-medium text-white
                         transition-colors hover:bg-indigo-700"
            >
              <Plus className="h-5 w-5" />
              Создать первый прогон
            </Link>
          </div>
        ) : shown.length === 0 ? (
          <div className="py-12 text-center text-cf-text-muted">
            <p className="text-lg">Под фильтр не попал ни один прогон</p>
            <p className="mt-2 text-sm">
              Уберите фильтр или измените запрос поиска.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {shown.map(r => {
              const pill = statusPill(r.state)
              const isSel = selected.has(r.run)
              const idx = ordered.findIndex(x => x.run === r.run)
              return (
                <div key={r.run} className="flex items-stretch gap-2">
                  {selectMode && (
                    <button
                      onClick={() => toggle(r.run)}
                      aria-pressed={isSel}
                      aria-label={isSel
                        ? `Снять отметку: ${r.run}`
                        : `Отметить: ${r.run}`}
                      title={runBusy(r)
                        ? 'У прогона идёт стадия — в архив он не уйдёт'
                        : (isSel ? 'Снять отметку' : 'Отметить')}
                      className={'flex w-10 shrink-0 items-center'
                        + ' justify-center rounded-xl border transition-colors '
                        + (isSel
                          ? 'border-indigo-400 bg-indigo-500/20 text-indigo-300'
                          : 'border-gray-700 bg-gray-800 text-gray-500'
                            + ' hover:text-gray-300')}
                    >
                      {isSel ? <CheckSquare className="h-5 w-5" />
                        : <Square className="h-5 w-5" />}
                    </button>
                  )}
                  <Link
                    to={`/runs/${r.run}`}
                    className={'group block min-w-0 flex-1 rounded-xl border'
                      + ' border-gray-700 border-l-4 bg-gray-800 p-4'
                      + ' transition-all duration-200 hover:border-gray-600'
                      + ' hover:border-l-cf-primary hover:bg-gray-700/40'
                      + ' hover:shadow-lg '
                      + (isSel ? 'border-l-indigo-400'
                               : 'border-l-transparent')}
                  >
                    <div className="flex items-center gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-3">
                          <h2 className="truncate text-lg font-semibold
                                         text-white transition-colors
                                         group-hover:text-indigo-400"
                              title={r.title
                                ? `${r.title} (${r.run})` : r.run}>
                            {r.title || r.run}
                            {r.title && (
                              <span className="ml-2 text-xs font-normal
                                               text-gray-400">
                                {r.run}
                              </span>
                            )}
                            {r.customer ? ` · ${r.customer}` : ''}
                          </h2>
                          <span className={'inline-flex items-center gap-1.5'
                            + ' rounded-full px-2 py-0.5 text-xs font-medium '
                            + pill.cls}
                                title="Состояние линии правил этого
прогона">
                            {pill.icon}
                            {r.state ? humanState(r.state) : '…'}
                          </span>
                          {r.readiness && (
                            <span className={
                              'inline-flex items-center gap-1 rounded '
                              + 'px-2 py-0.5 text-xs font-semibold '
                              + (r.readiness.state === 'в'
                                ? 'bg-green-500/20 text-green-300'
                                : 'bg-amber-500/20 text-amber-300')}
                                  title="Готовность линии правил: (а)
данных для пейринга и ростеринга недостаточно, (б) для пейринга
достаточно, для ростеринга нет, (в) достаточно для обоих">
                              {r.readiness.label}
                            </span>
                          )}
                          {r.data_state && (() => {
                            const dp = statusPill(r.data_state)
                            return (
                              <span className={'inline-flex items-center'
                                + ' gap-1.5 rounded-full px-2 py-0.5'
                                + ' text-xs font-medium ' + dp.cls}
                                    title="Состояние линии данных
(проверки операционных таблиц против свода)">
                                {dp.icon}
                                данные: {r.data_state}
                              </span>
                            )
                          })()}
                        </div>
                        <div className="flex flex-wrap items-center gap-x-4
                                        gap-y-1 text-sm text-gray-400">
                          <span className="rounded bg-gray-700/50 px-2
                                           py-0.5 font-mono">
                            файлов: {r.files.length}
                          </span>
                          {r.files.slice(0, 3).map(f => (
                            <span key={f}
                                  className="flex min-w-0 items-center
                                             gap-1.5"
                                  title={f}>
                              <FileText className="h-3.5 w-3.5 shrink-0" />
                              <span className="truncate">{f}</span>
                            </span>
                          ))}
                          {r.files.length > 3 && (
                            <span>и ещё {r.files.length - 3}</span>
                          )}
                          {r.created_at && (
                            <span className="flex items-center gap-1.5"
                                  title={`Создан ${r.created_at}`}>
                              <Clock className="h-3.5 w-3.5 shrink-0" />
                              {fmtCreated(r.created_at)}
                            </span>
                          )}
                        </div>
                        {/* Повтор (проект повтора, Р2–Р3): у потомка —
                            источник и вердикт, у источника — потомки. */}
                        {(r.replay_of || (r.replays && r.replays.length > 0)) && (
                          <div className="mt-1 flex flex-wrap items-center
                                          gap-x-3 gap-y-0.5 text-xs
                                          text-gray-400"
                               title="Повтор прогона: «из журнала» — те же
ответы моделей из журнала источника, ожидание «совпадает» байт в байт;
«заново» — модели вызваны ещё раз, сравнение по итогам">
                            {r.replay_of && (
                              <span>
                                {r.replay_of.mode === 'replay-fresh'
                                  ? 'повтор заново' : 'повтор из журнала'}
                                {' ← '}
                                <Link className="text-cf-primary hover:underline"
                                      to={`/runs/${r.replay_of.source_run}`}
                                      onClick={e => e.stopPropagation()}>
                                  {r.replay_of.source_run}
                                </Link>
                                {': '}
                                <span className={
                                  r.replay_of.verdict === 'совпадает'
                                    || r.replay_of.verdict === 'итоги совпадают'
                                    ? 'text-emerald-500'
                                    : r.replay_of.verdict ? 'text-amber-500'
                                    : 'text-gray-300'}>
                                  {r.replay_of.verdict || 'идёт'}
                                </span>
                              </span>
                            )}
                            {r.replays && r.replays.length > 0 && (
                              <span>повторы: {r.replays.map((p, i) => (
                                <span key={p.run}>
                                  {i > 0 ? ', ' : ''}
                                  <Link className="text-cf-primary hover:underline"
                                        to={`/runs/${p.run}`}
                                        onClick={e => e.stopPropagation()}>
                                    {p.run}
                                  </Link>
                                  {' '}{p.verdict || 'идёт'}
                                </span>
                              ))}</span>
                            )}
                          </div>
                        )}
                        {/* Паспорт кратко (слово владельца 07.09: «прогон
                            должен хранить также модели и солверы»): модели
                            ключевых ролей по стадиям, решатели, код. */}
                        {r.passport_summary && (
                          <div className="mt-1 flex flex-wrap items-center
                                          gap-x-3 gap-y-0.5 text-xs
                                          text-gray-400"
                               title="Чем сделан прогон: модели по ролям (по
журналу стадий), решатели проверок и коммит панели при рождении прогона.
Полный паспорт — панель «Заморозка» на странице прогона">
                            {Object.entries(r.passport_summary.models).map(
                              ([role, model]) => (
                                <span key={role}>
                                  {role === 'extractor' ? 'извлечение'
                                    : role === 'worker' ? 'обработчик'
                                    : role === 'auditor' ? 'аудитор'
                                    : role === 'arbiter' ? 'арбитр' : role}
                                  : <span className="text-gray-300">
                                    {model}</span>
                                </span>
                              ))}
                            {Object.keys(r.passport_summary.models).length
                              === 0 && (
                              <span title={r.state === 'заморожено'
                                ? 'Журнал каналов стадий пишется при первой стадии'
                                : 'Прогон сделан до паспорта: журнал каналов стадий не вёлся, модели не восстанавливаются'}>
                                {r.state === 'заморожено'
                                  ? 'модели: стадии ещё не шли'
                                  : 'модели: не записаны (прогон до паспорта)'}
                              </span>
                            )}
                            {r.passport_summary.solvers.length > 0 && (
                              <span>решатели: <span className="text-gray-300">
                                {r.passport_summary.solvers.join(' · ')}
                              </span></span>
                            )}
                            {r.passport_summary.code && (
                              <span>код: <span className="font-mono
                                               text-gray-300">
                                {r.passport_summary.code}</span></span>
                            )}
                            {r.passport_summary.catalog && (
                              <span>каталог: <CatalogChip
                                version={r.passport_summary.catalog} /></span>
                            )}
                          </div>
                        )}
                      </div>
                      <ChevronRight className="h-5 w-5 shrink-0
                                               text-gray-500 transition-colors
                                               group-hover:text-indigo-400" />
                    </div>
                  </Link>
                  {canReorder && (
                    <div className="flex shrink-0 flex-col justify-center
                                    gap-1">
                      <button
                        onClick={() => move(r.run, -1)}
                        disabled={busy || idx <= 0}
                        aria-label={`Переместить вверх: ${r.run}`}
                        title="Переместить прогон на одну позицию вверх
(ручной порядок хранится на площадке)"
                        className="rounded-lg border border-gray-700
                                   bg-gray-800 p-1.5 text-gray-400
                                   transition-colors hover:text-white
                                   disabled:cursor-not-allowed
                                   disabled:opacity-30"
                      >
                        <ArrowUp className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => move(r.run, 1)}
                        disabled={busy || idx < 0
                          || idx >= ordered.length - 1}
                        aria-label={`Переместить вниз: ${r.run}`}
                        title="Переместить прогон на одну позицию вниз
(ручной порядок хранится на площадке)"
                        className="rounded-lg border border-gray-700
                                   bg-gray-800 p-1.5 text-gray-400
                                   transition-colors hover:text-white
                                   disabled:cursor-not-allowed
                                   disabled:opacity-30"
                      >
                        <ArrowDown className="h-4 w-4" />
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
            <p className="pt-2 text-xs text-cf-text-muted">
              Удаление переносит папку прогона в архив площадки
              (каталог cdpm-runs-archive рядом с прогонами) — вернуть
              можно переносом папки обратно; номера удалённых прогонов
              заново не выдаются.
            </p>
          </div>
        )}
        <ConfirmDialog
          open={confirmOpen}
          title={`Удалить прогоны (${selected.size})?`}
          message={[...selected].sort().join(', ')
            + ' — папки прогонов будут перенесены в архив площадки '
            + '(cdpm-runs-archive). Пакеты, уже переданные в COSMOS, '
            + 'не затрагиваются. Вернуть прогон можно переносом папки '
            + 'обратно.'}
          confirmLabel="В архив"
          danger
          onConfirm={doArchive}
          onCancel={() => setConfirmOpen(false)}
        />
      </div>
    </div>
  )
}
