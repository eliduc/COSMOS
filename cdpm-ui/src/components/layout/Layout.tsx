import { Component, useEffect, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import toast, { Toaster, useToasterStore } from 'react-hot-toast'
import {
  AlertTriangle, BookMarked, ChevronsLeft, ChevronsRight, Copy, Cpu,
  ExternalLink, ListChecks, RefreshCw, RotateCcw, Scale, Settings, Upload,
  Workflow,
} from 'lucide-react'
import clsx from 'clsx'
import Button from '../common/Button'
import CommandPalette from '../common/CommandPalette'
import ThemeToggle from '../common/ThemeToggle'
import TooltipLayer from '../common/TooltipLayer'
import { catalogLink } from '../../api'
import { useCatalogStore } from '../../stores/catalogStore'
import { useUiStore } from '../../stores/uiStore'

// Оболочка (по образцу CodeForge Layout): h-screen flex — страница не
// скроллится, скроллятся внутренние панели; сворачиваемый сайдбар;
// main внутри ErrorBoundary; тосты справа сверху; палитра Ctrl-K.

const NAV = [
  { to: '/runs', label: 'Прогоны', Icon: ListChecks,
    tip: 'Все прогоны формализации: состояния обеих линий (правил и '
       + 'данных), поиск и фильтры' },
  { to: '/runs/new', label: 'Новый прогон', Icon: Upload,
    tip: 'Загрузить документы правил авиакомпании и заморозить их с '
       + 'манифестом SHA-256' },
  { to: '/exchange', label: 'Аудит-цикл', Icon: RefreshCw,
    tip: 'Про аудит-цикл: вторая модель перечитывает документы против '
       + 'свода; запуск — на странице прогона (блок «Аудитор»)' },
  { to: '/arbiter', label: 'Арбитр', Icon: Scale,
    tip: 'Про арбитраж: третья модель решает тупики цикла; запуск — '
       + 'на странице прогона (блок «Арбитр»)' },
  { to: '/models', label: 'Модели узлов', Icon: Cpu,
    tip: 'Раскладка моделей по всем LLM-узлам конвейера: каждая роль '
       + '— своя модель; незаданная наследует родителя' },
  { to: '/settings', label: 'Настройки', Icon: Settings,
    tip: 'Ключи провайдеров моделей, проверка связи и внешний вид' },
]

// Служба каталога шаблонов — отдельный модуль на своём порту, поэтому
// пункт меню внешний. Адреса в коде нет: он приходит настройкой
// CREW_CATALOG_URL с точки /api/settings. Не настроена — пункт виден,
// но неактивен и объясняет словами, чего не хватает: мёртвая ссылка
// хуже честного «не настроено».
const CATALOG_LABEL = 'Каталог шаблонов'
const CATALOG_TIP = 'Записи, заявки, исполнители и версии каталога '
  + 'шаблонов — отдельная служба на своём порту'
const CATALOG_OFF = 'служба каталога не настроена (CREW_CATALOG_URL)'

// Состояние САМОГО каталога, которым работает панель (не службы): с
// шага 7 каталог обязателен, и без него ручки, которым нужны словари,
// отвечают 503 «каталог шаблонов недоступен: …». Строка называет
// версию и отпечаток заранее — чтобы аналитик знал, чем считает панель,
// и видел беду контура до первого отказа, а не после него.
function CatalogStateLine() {
  const probe = useCatalogStore(s => s.probe)
  // Пробы нет (точка не ответила) — строки нет: молчание тише вранья.
  if (!probe) return null
  const shape = 'px-2.5 pb-1 text-[11px] leading-tight break-words'
  if (!probe.available) {
    const words = probe.error || probe.words || 'причина не названа'
    return (
      <p className={clsx(shape, 'text-cf-error')}
         title={probe.words || `каталог шаблонов недоступен: ${words}`}>
        каталог шаблонов недоступен: {words}
      </p>
    )
  }
  const fingerprint = probe.fingerprint || ''
  return (
    <p className={clsx(shape, 'text-cf-text-muted')}
       title={'Каталог шаблонов, которым работает панель'
         + (probe.path ? `: ${probe.path}` : '')
         + (fingerprint ? `\nотпечаток ${fingerprint}` : '')
         + (probe.warnings ? `\nпредупреждений: ${probe.warnings}` : '')}>
      каталог шаблонов: {probe.version || '—'}
      {fingerprint ? ` · ${fingerprint.slice(0, 12)}` : ''}
    </p>
  )
}

function CatalogNavItem({ collapsed }: { collapsed: boolean }) {
  const url = useCatalogStore(s => s.url)
  const load = useCatalogStore(s => s.load)
  useEffect(() => { load() }, [load])
  const href = catalogLink(url, '/')
  const shape = 'flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm'
  const item = !href ? (
    <span
      aria-disabled="true"
      aria-label={CATALOG_LABEL}
      title={CATALOG_OFF}
      className={clsx(shape, 'cursor-not-allowed text-cf-text-muted',
                      'opacity-50')}
    >
      <BookMarked className="h-4 w-4 shrink-0" />
      {!collapsed && CATALOG_LABEL}
    </span>
  ) : (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={CATALOG_LABEL}
      title={CATALOG_TIP}
      className={clsx(shape, 'text-cf-text-muted transition-colors',
                      'hover:bg-cf-hover hover:text-cf-text')}
    >
      <BookMarked className="h-4 w-4 shrink-0" />
      {!collapsed && (
        <span className="flex flex-1 items-center justify-between gap-2">
          {CATALOG_LABEL}
          <ExternalLink className="h-3 w-3 shrink-0" />
        </span>
      )}
    </a>
  )
  // Задвинутое меню строки не показывает: в 12 знаков отпечаток не
  // влезет, а обрезок хуже отсутствия.
  return (
    <>
      {item}
      {!collapsed && <CatalogStateLine />}
    </>
  )
}

// Номер версии продукта мелким шрифтом в самом низу меню (РВ-141, слово
// владельца 04.10): одна настройка площадки на все компоненты, литерала
// в коде нет. Не задана — подписи нет; задвинутое меню — лишь номер.
function ProductVersion({ collapsed }: { collapsed: boolean }) {
  const version = useCatalogStore(s => s.productVersion)
  const load = useCatalogStore(s => s.load)
  useEffect(() => { load() }, [load])
  if (!version) return null
  return (
    <p className="text-center text-[10px] leading-tight text-cf-text-muted"
       title={`версия ${version}`}>
      {collapsed ? version : `версия ${version}`}
    </p>
  )
}

// «Скрыть все» при трёх и более видимых уведомлениях (приём эталона).
function DismissAllToasts() {
  const { toasts } = useToasterStore()
  const visible = toasts.filter(t => t.visible)
  if (visible.length < 3) return null
  return (
    <button
      onClick={() => toast.dismiss()}
      className="fixed right-4 top-2 z-[60] rounded-full border
                 border-cf-border bg-cf-panel px-3 py-1 text-xs
                 font-medium text-cf-text-muted shadow-md
                 transition-colors hover:text-cf-text"
    >
      Скрыть все уведомления
    </button>
  )
}

interface ErrorBoundaryState { error: Error | null }

class ErrorBoundary extends Component<{ children: ReactNode },
                                      ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    const err = this.state.error
    if (!err) return this.props.children
    const details = `${err.toString()}\n\n${err.stack ?? '(без стека)'}`
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <AlertTriangle className="mx-auto mb-4 h-12 w-12
                                    text-cf-warning" />
          <h2 className="mb-2 text-lg font-semibold text-cf-text">
            Что-то пошло не так
          </h2>
          <p className="mb-4 text-sm text-cf-text-muted">
            Интерфейс столкнулся с ошибкой. Попробуйте ещё раз или
            скопируйте детали для отчёта.
          </p>
          <div className="flex flex-wrap items-center justify-center
                          gap-2">
            <Button
              variant="primary"
              leadingIcon={<RotateCcw className="h-4 w-4" />}
              onClick={() => this.setState({ error: null })}
            >
              Повторить
            </Button>
            <Button
              variant="secondary"
              leadingIcon={<Copy className="h-4 w-4" />}
              onClick={() => navigator.clipboard?.writeText(details)}
            >
              Скопировать детали
            </Button>
          </div>
        </div>
      </div>
    )
  }
}

export default function Layout({ children }: { children: ReactNode }) {
  const collapsed = useUiStore(s => s.sidebarCollapsed)
  const toggleSidebar = useUiStore(s => s.toggleSidebar)
  return (
    <div className="flex h-screen bg-cf-bg text-cf-text">
      <aside
        className={clsx(
          'flex shrink-0 flex-col border-r border-cf-border bg-cf-panel',
          'transition-[width] duration-300',
          collapsed ? 'w-12' : 'w-64',
        )}
      >
        <div className={clsx(
          'border-b border-cf-border p-3',
          collapsed ? 'flex flex-col items-center gap-2'
            : 'flex items-center gap-2',
        )}>
          <Workflow className="h-6 w-6 shrink-0 text-cf-primary" />
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">CDPM</p>
              <p className="truncate text-xs text-cf-text-muted">
                обработка данных авиакомпании
              </p>
            </div>
          )}
          {/* Задвинуть/выдвинуть меню — заметный шеврон в шапке
              (замечание владельца: нижняя кнопка не находилась). */}
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Выдвинуть меню' : 'Задвинуть меню'}
            title={collapsed ? 'Выдвинуть меню' : 'Задвинуть меню'}
            className="shrink-0 rounded-lg p-1.5 text-cf-text-muted
                       transition-colors hover:bg-cf-hover
                       hover:text-cf-text"
          >
            {collapsed
              ? <ChevronsRight className="h-4 w-4" />
              : <ChevronsLeft className="h-4 w-4" />}
          </button>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto p-2">
          {NAV.map(({ to, label, Icon, tip }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/runs'}
              aria-label={label}
              title={tip}
              className={({ isActive }) => clsx(
                'flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm',
                'transition-colors',
                isActive
                  ? 'bg-cf-primary/10 font-medium text-cf-primary'
                  : 'text-cf-text-muted hover:bg-cf-hover'
                    + ' hover:text-cf-text',
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && label}
            </NavLink>
          ))}
          <CatalogNavItem collapsed={collapsed} />
        </nav>
        <div className={clsx('space-y-2 border-t border-cf-border p-2',
                             collapsed && 'flex flex-col items-center')}>
          {!collapsed && <ThemeToggle />}
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
            title={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
            className="flex w-full items-center justify-center rounded-lg
                       p-2 text-cf-text-muted transition-colors
                       hover:bg-cf-hover hover:text-cf-text"
          >
            {collapsed
              ? <ChevronsRight className="h-4 w-4" />
              : <ChevronsLeft className="h-4 w-4" />}
          </button>
          <ProductVersion collapsed={collapsed} />
        </div>
      </aside>

      <main className="flex flex-1 flex-col overflow-hidden">
        <ErrorBoundary>{children}</ErrorBoundary>
      </main>

      <CommandPalette />
      <TooltipLayer />
      <DismissAllToasts />
      <Toaster
        position="top-right"
        containerStyle={{ top: 16, right: 16 }}
        toastOptions={{
          className: 'border border-cf-border bg-cf-panel text-cf-text'
            + ' shadow-md',
          style: { background: 'var(--cf-panel)',
                   color: 'var(--cf-text)' },
        }}
      />
    </div>
  )
}
