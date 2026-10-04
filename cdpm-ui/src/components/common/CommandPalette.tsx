import { Fragment, useEffect, useState } from 'react'
import { Dialog, Combobox, Transition } from '@headlessui/react'
import { useNavigate } from 'react-router-dom'
import { Search, ListChecks, Upload, Scale, RefreshCw,
         Settings, FileText, Cpu } from 'lucide-react'
import { useUiStore } from '../../stores/uiStore'
import { listRuns, type RunSummary } from '../../api'

// Палитра команд Ctrl/Cmd-K (по образцу CodeForge CommandPalette):
// Headless UI Combobox поверх Dialog; команды — переходы по разделам.
interface Command {
  id: string
  label: string
  to: string
  Icon: typeof Search
}

const COMMANDS: Command[] = [
  { id: 'runs', label: 'Прогоны', to: '/runs', Icon: ListChecks },
  { id: 'new', label: 'Новый прогон', to: '/runs/new', Icon: Upload },
  { id: 'exchange', label: 'Аудит-цикл', to: '/exchange',
    Icon: RefreshCw },
  { id: 'arbiter', label: 'Арбитр тупиков', to: '/arbiter',
    Icon: Scale },
  { id: 'models', label: 'Модели узлов', to: '/models', Icon: Cpu },
  { id: 'settings', label: 'Настройки', to: '/settings',
    Icon: Settings },
]

export default function CommandPalette() {
  const open = useUiStore(s => s.paletteOpen)
  const setOpen = useUiStore(s => s.setPaletteOpen)
  const [query, setQuery] = useState('')
  const [runs, setRuns] = useState<RunSummary[]>([])
  const navigate = useNavigate()

  // Поиск по прогонам — как поиск сессий в палитре эталона.
  useEffect(() => {
    if (!open) return
    listRuns().then(setRuns).catch(() => setRuns([]))
  }, [open])

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

  const filtered = query
    ? COMMANDS.filter(c =>
        c.label.toLowerCase().includes(query.toLowerCase()))
    : COMMANDS
  const q = query.toLowerCase()
  const runCommands: Command[] = runs
    .filter(r => !q || r.run.toLowerCase().includes(q)
      || r.files.some(f => f.toLowerCase().includes(q))
      || (r.state || '').toLowerCase().includes(q))
    .slice(0, 6)
    .map(r => ({
      id: r.run,
      label: `${r.run} — ${r.state} — ${r.files.join(', ')}`,
      to: `/runs/${r.run}`,
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
              Палитра команд
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
                  placeholder="Куда перейти…"
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
                {filtered.length + runCommands.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm
                                text-cf-text-muted">
                    Ничего не найдено
                  </p>
                )}
                {runCommands.length > 0 && (
                  <p className="px-3 pb-1 pt-2 text-[10px] font-bold
                                uppercase tracking-wider
                                text-cf-text-muted">
                    Прогоны
                  </p>
                )}
                {runCommands.map(cmd => (
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
                {filtered.length > 0 && (
                  <p className="px-3 pb-1 pt-2 text-[10px] font-bold
                                uppercase tracking-wider
                                text-cf-text-muted">
                    Разделы
                  </p>
                )}
                {filtered.map(cmd => (
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
