import { Fragment, useEffect, useState } from 'react'
import { Dialog, Combobox, Transition } from '@headlessui/react'
import { useNavigate } from 'react-router-dom'
import { Search, Layers, BookMarked, Inbox, Cpu, Tags, FileText }
  from 'lucide-react'
import { useUiStore } from '../../stores/uiStore'
import { listRecords, type RecordRow } from '../../api'
import { t } from '../../i18n'
import type { MessageKey } from '../../i18n'

// Ctrl/Cmd-K command palette: jumps between the five screens plus a
// search over catalog records by id and title (GET /api/records?q=).
interface Command {
  id: string
  label: string
  to: string
  Icon: typeof Search
}

const SECTIONS: { id: string; label: MessageKey; to: string
                  Icon: typeof Search }[] = [
  { id: 'records', label: 'nav.records', to: '/records', Icon: Layers },
  { id: 'dictionaries', label: 'nav.dictionaries', to: '/dictionaries',
    Icon: BookMarked },
  { id: 'requests', label: 'nav.requests', to: '/requests', Icon: Inbox },
  { id: 'executors', label: 'nav.executors', to: '/executors', Icon: Cpu },
  { id: 'versions', label: 'nav.versions', to: '/versions', Icon: Tags },
]

export default function CommandPalette() {
  const open = useUiStore(s => s.paletteOpen)
  const setOpen = useUiStore(s => s.setPaletteOpen)
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<RecordRow[]>([])
  const navigate = useNavigate()

  // Record search goes to the service: the palette does not keep its
  // own copy of the catalog.
  useEffect(() => {
    if (!open) return
    let alive = true
    const timer = window.setTimeout(() => {
      listRecords(query ? { q: query } : {})
        .then(got => { if (alive) setRows(got.items ?? []) })
        .catch(() => { if (alive) setRows([]) })
    }, 180)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [open, query])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen(!useUiStore.getState().paletteOpen)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setOpen])

  const commands: Command[] = SECTIONS
    .map(s => ({ id: s.id, label: t(s.label), to: s.to, Icon: s.Icon }))
    .filter(c => !query
      || c.label.toLowerCase().includes(query.toLowerCase()))
  const recordCommands: Command[] = rows.slice(0, 6).map(row => ({
    id: `${row.section}/${row.id}`,
    label: `${row.id} — ${row.title}`,
    to: `/records/${row.section}/${row.id}`,
    Icon: FileText,
  }))

  return (
    <Transition show={open} as={Fragment} afterLeave={() => setQuery('')}>
      <Dialog onClose={() => setOpen(false)} className="relative z-50">
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-150" enterFrom="opacity-0"
          enterTo="opacity-100" leave="ease-in duration-100"
          leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        </Transition.Child>
        <div className="fixed inset-0 flex items-start justify-center
                        p-4 pt-[15vh]">
          <Dialog.Panel className="w-full max-w-lg overflow-hidden
                                   rounded-xl border border-cf-border
                                   bg-cf-panel shadow-2xl">
            <Dialog.Title className="sr-only">
              {t('nav.paletteTitle')}
            </Dialog.Title>
            <Combobox onChange={(cmd: Command | null) => {
              if (cmd) {
                navigate(cmd.to)
                setOpen(false)
              }
            }}>
              <div className="flex items-center gap-2 border-b
                              border-cf-border px-3">
                <Search className="h-4 w-4 text-cf-text-muted" />
                <Combobox.Input
                  className="w-full bg-transparent py-3 text-sm
                             text-cf-text placeholder:text-cf-text-muted
                             focus:outline-none"
                  placeholder={t('nav.palettePlaceholder')}
                  onChange={e => setQuery(e.target.value)}
                  autoFocus
                />
                <kbd className="rounded border border-cf-border px-1.5
                                py-0.5 text-xs text-cf-text-muted">
                  Esc
                </kbd>
              </div>
              <Combobox.Options static className="max-h-72 overflow-y-auto
                                                  p-2">
                {commands.length + recordCommands.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm
                                text-cf-text-muted">
                    {t('common.empty')}
                  </p>
                )}
                {recordCommands.length > 0 && (
                  <p className="px-3 pb-1 pt-2 text-[10px] font-bold
                                uppercase tracking-wider
                                text-cf-text-muted">
                    {t('nav.paletteRecords')}
                  </p>
                )}
                {recordCommands.map(cmd => (
                  <Combobox.Option key={cmd.id} value={cmd}>
                    {({ active }) => (
                      <div className={`flex cursor-pointer items-center
                        gap-3 truncate rounded-lg px-3 py-2 text-sm
                        ${active ? 'bg-cf-hover text-cf-text'
                                 : 'text-cf-text-muted'}`}>
                        <cmd.Icon className="h-4 w-4 shrink-0" />
                        <span className="truncate">{cmd.label}</span>
                      </div>
                    )}
                  </Combobox.Option>
                ))}
                {commands.length > 0 && (
                  <p className="px-3 pb-1 pt-2 text-[10px] font-bold
                                uppercase tracking-wider
                                text-cf-text-muted">
                    {t('nav.paletteSections')}
                  </p>
                )}
                {commands.map(cmd => (
                  <Combobox.Option key={cmd.id} value={cmd}>
                    {({ active }) => (
                      <div className={`flex cursor-pointer items-center
                        gap-3 rounded-lg px-3 py-2 text-sm
                        ${active ? 'bg-cf-hover text-cf-text'
                                 : 'text-cf-text-muted'}`}>
                        <cmd.Icon className="h-4 w-4" />
                        {cmd.label}
                      </div>
                    )}
                  </Combobox.Option>
                ))}
              </Combobox.Options>
            </Combobox>
          </Dialog.Panel>
        </div>
      </Dialog>
    </Transition>
  )
}
