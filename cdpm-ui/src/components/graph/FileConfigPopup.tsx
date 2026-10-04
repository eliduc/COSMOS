import { useEffect, useRef, useState } from 'react'
import { FileText, X } from 'lucide-react'
import Button from '../common/Button'
import notify from '../common/StyledToast'
import { putFilesConfig, type FileConfig, type FilePolicy }
  from '../../api'

// Плавающая панель настройки файла данных (открывается шестерёнкой у
// файла на блоке «Файлы данных», по образцу AgentConfigPopup):
// политика обработки + примечание.
const POLICIES: { value: FilePolicy; label: string; hint: string }[] = [
  { value: 'local', label: 'Локальная модель',
    hint: 'содержимое файла не покидает эту машину (умолчание)' },
  { value: 'cloud-country', label: 'Облачная модель внутри страны',
    hint: 'собственный канал пока не подключён — до подключения файл '
      + 'обрабатывается локальной моделью (строже, не мягче)' },
  { value: 'cloud', label: 'Облачная модель',
    hint: 'клаузы файла можно отправлять облачной модели' },
]

export default function FileConfigPopup({ runId, file, all, x, y,
                                          onClose }: {
  runId: string
  file: string
  all: Record<string, FileConfig>
  x: number
  y: number
  onClose: (saved: boolean) => void
}) {
  const current = all[file] || { processing: 'local', note: '' }
  const [processing, setProcessing] =
    useState<FilePolicy>(current.processing)
  const [note, setNote] = useState(current.note)
  const [busy, setBusy] = useState(false)
  const popupRef = useRef<HTMLDivElement>(null)
  const repositioned = useRef(false)

  // Прижатие к краям холста — тот же приём, что у панели LLM-блока.
  useEffect(() => {
    if (repositioned.current) return
    const el = popupRef.current
    const parent = el?.offsetParent as HTMLElement | null
    if (!el || !parent) return
    repositioned.current = true
    const r = el.getBoundingClientRect()
    const p = parent.getBoundingClientRect()
    if (r.right > p.right - 8) {
      el.style.left = `${Math.max(8, p.right - 8 - r.width - p.left)}px`
    }
    if (r.bottom > p.bottom - 8) {
      el.style.top = `${Math.max(8, p.bottom - 8 - r.height - p.top)}px`
    }
  }, [])

  const save = async () => {
    setBusy(true)
    try {
      await putFilesConfig(runId, {
        ...all, [file]: { processing, note } })
      notify.success('Настройка файла сохранена')
      onClose(true)
    } catch (e) {
      notify.error(String(e))
      setBusy(false)
    }
  }

  return (
    <div
      ref={popupRef}
      className="absolute z-30 w-96 rounded-xl border border-cf-border
                 bg-cf-panel p-4 shadow-xl"
      style={{ left: x, top: y }}
      role="dialog"
      aria-label={`Обработка файла ${file}`}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-sm
                         font-semibold text-cf-text">
          <FileText className="h-4 w-4 shrink-0 text-cf-primary" />
          <span className="truncate" title={file}>{file}</span>
        </span>
        <button
          onClick={() => onClose(false)}
          aria-label="Закрыть без сохранения"
          title="Закрыть без сохранения"
          className="rounded-lg p-1.5 text-cf-text-muted
                     transition-colors hover:bg-cf-hover
                     hover:text-cf-text"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <fieldset className="mb-3">
        <legend className="mb-2 text-xs font-medium text-cf-text-muted">
          Как можно обрабатывать файл
        </legend>
        <div className="space-y-2">
          {POLICIES.map(p => (
            <label
              key={p.value}
              className="flex cursor-pointer items-start gap-3 rounded-lg
                         border border-transparent bg-cf-bg p-2.5
                         transition-colors hover:bg-cf-hover
                         has-[:checked]:border-cf-primary/40"
            >
              <input
                type="radio"
                name={`policy-${file}`}
                value={p.value}
                checked={processing === p.value}
                onChange={() => setProcessing(p.value)}
                className="mt-0.5 accent-cf-primary"
              />
              <span className="flex-1">
                <span className="block text-sm font-medium text-cf-text">
                  {p.label}
                </span>
                <span className="block text-xs text-cf-text-muted">
                  {p.hint}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="mb-3 block text-xs text-cf-text-muted"
             title="Свободный комментарий к файлу — виден на блоке;
здесь же фиксируются решения владельца по файлу (например, разрешение
на облако)">
        Примечание
        <textarea
          className="input-field mt-1 h-20 w-full resize-none"
          placeholder="любой комментарий к файлу"
          value={note}
          onChange={e => setNote(e.target.value)}
        />
      </label>

      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm"
                title="Закрыть без сохранения"
                onClick={() => onClose(false)} disabled={busy}>
          Отмена
        </Button>
        <Button size="sm" onClick={save} disabled={busy}
                title="Сохранить политику обработки и примечание файла.
Стадии, смешивающие все файлы, идут по строжайшей политике прогона">
          {busy ? 'Сохранение…' : 'Сохранить'}
        </Button>
      </div>
    </div>
  )
}
