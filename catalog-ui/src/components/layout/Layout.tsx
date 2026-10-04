import { Component, useEffect, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import toast, { Toaster, useToasterStore } from 'react-hot-toast'
import {
  AlertTriangle, BookMarked, ChevronsLeft, ChevronsRight, Copy, Cpu,
  Inbox, Layers, Library, RotateCcw, Tags,
} from 'lucide-react'
import clsx from 'clsx'
import Button from '../common/Button'
import CommandPalette from '../common/CommandPalette'
import StatusBadge from '../common/StatusBadge'
import ThemeToggle from '../common/ThemeToggle'
import TooltipLayer from '../common/TooltipLayer'
import RoleBadge from '../catalog/RoleBadge'
import notify from '../common/StyledToast'
import { draftCount } from '../../api'
import { useSessionStore } from '../../stores/sessionStore'
import { useUiStore } from '../../stores/uiStore'
import { t, tf } from '../../i18n'
import type { MessageKey } from '../../i18n'

// Shell of the catalog service (same shape as the CDPM panel):
// h-screen flex - the page itself does not scroll, inner panels do; the
// sidebar collapses; main sits inside an ErrorBoundary; toasts top
// right; Ctrl-K palette. Above every screen there is one status line
// from GET /api/session: catalog version and fingerprint, draft or
// release, workplace or view mode, counters of drafts and open
// requests.

const NAV: { to: string; label: MessageKey; tip: MessageKey
             Icon: typeof Layers }[] = [
  { to: '/records', label: 'nav.records', tip: 'nav.recordsTip',
    Icon: Layers },
  { to: '/dictionaries', label: 'nav.dictionaries',
    tip: 'nav.dictionariesTip', Icon: BookMarked },
  { to: '/requests', label: 'nav.requests', tip: 'nav.requestsTip',
    Icon: Inbox },
  { to: '/executors', label: 'nav.executors', tip: 'nav.executorsTip',
    Icon: Cpu },
  { to: '/versions', label: 'nav.versions', tip: 'nav.versionsTip',
    Icon: Tags },
]

function StatusLine() {
  const session = useSessionStore(s => s.session)
  const error = useSessionStore(s => s.sessionError)
  const load = useSessionStore(s => s.load)
  useEffect(() => { load() }, [load])

  if (error) {
    return (
      <div className="border-b border-cf-border bg-cf-panel px-4 py-2">
        <StatusBadge kind="error">
          {tf('common.serviceDown', { words: error })}
        </StatusBadge>
      </div>
    )
  }
  if (!session) {
    return (
      <div className="border-b border-cf-border bg-cf-panel px-4 py-2
                      text-xs text-cf-text-muted">
        {t('common.loading')}
      </div>
    )
  }
  const cat = session.catalog
  const drafts = draftCount(session)
  return (
    <div className="flex flex-wrap items-center gap-2 border-b
                    border-cf-border bg-cf-panel px-4 py-2 text-xs">
      <span className="font-mono text-cf-text">
        {tf('session.catalog', { version: cat.version,
                                 fingerprint: cat.fingerprint.slice(0, 12) })}
      </span>
      <button
        aria-label={t('session.copyFingerprint')}
        title={t('session.copyFingerprint')}
        onClick={() => {
          navigator.clipboard?.writeText(cat.fingerprint)
          notify.info(t('common.copied'))
        }}
        className="rounded p-1 text-cf-text-muted transition-colors
                   hover:bg-cf-hover hover:text-cf-text"
      >
        <Copy className="h-3 w-3" />
      </button>
      <StatusBadge kind={cat.is_draft ? 'warn' : 'ok'}>
        {cat.is_draft ? t('session.draft') : t('session.release')}
      </StatusBadge>
      <RoleBadge session={session} />
      <span className="text-cf-text-muted">
        {tf('session.drafts', { n: drafts })}
      </span>
      <span className="text-cf-text-muted">
        {tf('session.requestsOpen', { n: session.requests_open })}
      </span>
      <span className="ml-auto font-mono text-cf-text-muted">
        {cat.dir_name}
      </span>
    </div>
  )
}

// "Dismiss all" once three or more toasts are visible.
function DismissAllToasts() {
  const { toasts } = useToasterStore()
  const visible = toasts.filter(one => one.visible)
  if (visible.length < 3) return null
  return (
    <button
      onClick={() => toast.dismiss()}
      className="fixed right-4 top-2 z-[60] rounded-full border
                 border-cf-border bg-cf-panel px-3 py-1 text-xs
                 font-medium text-cf-text-muted shadow-md
                 transition-colors hover:text-cf-text"
    >
      {t('nav.dismissAll')}
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
    const details = `${err.toString()}\n\n${err.stack ?? ''}`
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <AlertTriangle className="mx-auto mb-4 h-12 w-12
                                    text-cf-warning" />
          <h2 className="mb-2 text-lg font-semibold text-cf-text">
            {t('nav.errorTitle')}
          </h2>
          <p className="mb-4 text-sm text-cf-text-muted">
            {t('nav.errorWords')}
          </p>
          <div className="flex flex-wrap items-center justify-center
                          gap-2">
            <Button
              variant="primary"
              leadingIcon={<RotateCcw className="h-4 w-4" />}
              onClick={() => this.setState({ error: null })}
            >
              {t('common.retry')}
            </Button>
            <Button
              variant="secondary"
              leadingIcon={<Copy className="h-4 w-4" />}
              onClick={() => navigator.clipboard?.writeText(details)}
            >
              {t('nav.copyDetails')}
            </Button>
          </div>
        </div>
      </div>
    )
  }
}

// Product version in small type at the very bottom of the sidebar (RV-141,
// owner's word 04.10): one site setting for all components, read from
// GET /api/session; no literal in code. Not set - no label; collapsed
// sidebar - the number only.
function ProductVersion({ collapsed }: { collapsed: boolean }) {
  const version = useSessionStore(s => s.session?.product_version)
  if (!version) return null
  const words = tf('nav.productVersion', { version })
  return (
    <p className="text-center text-[10px] leading-tight text-cf-text-muted"
       title={words}>
      {collapsed ? version : words}
    </p>
  )
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
          <Library className="h-6 w-6 shrink-0 text-cf-primary" />
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">
                {t('nav.title')}
              </p>
              <p className="truncate text-xs text-cf-text-muted">
                {t('nav.subtitle')}
              </p>
            </div>
          )}
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
            title={collapsed ? t('nav.expand') : t('nav.collapse')}
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
          {NAV.map(({ to, label, tip, Icon }) => (
            <NavLink
              key={to}
              to={to}
              aria-label={t(label)}
              title={t(tip)}
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
              {!collapsed && t(label)}
            </NavLink>
          ))}
        </nav>
        <div className={clsx('space-y-2 border-t border-cf-border p-2',
                             collapsed && 'flex flex-col items-center')}>
          {!collapsed && <ThemeToggle />}
          <button
            onClick={toggleSidebar}
            aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
            title={collapsed ? t('nav.expand') : t('nav.collapse')}
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

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <StatusLine />
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
