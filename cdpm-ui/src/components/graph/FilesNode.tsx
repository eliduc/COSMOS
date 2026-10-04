import { memo, useRef, useState } from 'react'
import { Handle, Position } from '@xyflow/react'
import { FileInput, FileText, Settings2, Trash2, UploadCloud }
  from 'lucide-react'
import clsx from 'clsx'
import type { FileConfig } from '../../api'

// Большой блок «Файлы данных» НАД конвейером — естественный первый
// блок (решение владельца 2026-08-18): все загруженные файлы на виду,
// у каждого шестерёнка политики обработки и примечание.
export interface FilesNodeData extends Record<string, unknown> {
  title?: string
  files: string[]
  cfg: Record<string, FileConfig>
  onFileGear?: (file: string, e: React.MouseEvent) => void
  // Удаление файла (только блок операционных данных): каскад на
  // сервере; кнопка не показывается, пока идёт стадия линии данных.
  onFileDelete?: (file: string) => void
  // Загрузка файлов ПРЯМО НА БЛОК — перетаскиванием или выбором
  // (слово владельца 06.09: «правила загружаются через блок с
  // drag-and-drop, а данные — только через табличку; должно быть
  // единообразие»). Без onUpload блок файлов не принимает
  // (документы правил после заморозки неизменны).
  onUpload?: (files: FileList) => void
  accept?: string
  uploadHint?: string
  // Подпись пустого блока без загрузки (например, пока идёт стадия).
  emptyHint?: string
}

export const POLICY_LABELS: Record<string, string> = {
  local: 'локальная модель',
  'cloud-country': 'облако внутри страны',
  cloud: 'облачная модель',
}

const POLICY_CHIP: Record<string, { label: string; cls: string
                                    tip: string }> = {
  local: { label: 'локально',
    cls: 'bg-cyan-500/20 text-cyan-300',
    tip: 'Политика «локальная модель»: содержимое файла не покидает '
       + 'эту машину' },
  'cloud-country': { label: 'облако в стране',
    cls: 'bg-amber-500/20 text-amber-300',
    tip: 'Политика «облако внутри страны»: допускается облачная '
       + 'модель с обработкой внутри страны заказчика' },
  cloud: { label: 'облако',
    cls: 'bg-indigo-500/25 text-indigo-300',
    tip: 'Политика «облачная модель»: содержимое можно отправлять '
       + 'внешней облачной модели' },
}

function FilesNode({ data, selected }: {
  data: FilesNodeData; selected?: boolean
}) {
  const canUpload = Boolean(data.onUpload)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  // Перетаскивание файлов из проводника: события HTML5 ловятся на
  // самом блоке (не на холсте), чтобы зона совпадала с блоком.
  // preventDefault — всегда: файл, уроненный на блок без зоны загрузки,
  // иначе открылся бы браузером вместо панели (ревью 06.09; страховка
  // сверх общего запрета в main.tsx).
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (!canUpload) return
    e.stopPropagation()
    if (!dragOver) setDragOver(true)
  }
  const onDragLeave = (e: React.DragEvent) => {
    if (!canUpload) return
    e.stopPropagation()
    setDragOver(false)
  }
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    if (!canUpload) return
    e.stopPropagation()
    setDragOver(false)
    if (e.dataTransfer.files?.length) data.onUpload?.(e.dataTransfer.files)
  }
  const pick = () => inputRef.current?.click()
  return (
    <div
      onDragOver={onDragOver}
      onDragEnter={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={clsx(
        'w-[700px] cursor-pointer rounded-xl border-2 border-slate-500',
        'bg-gradient-to-br from-slate-700 to-slate-800 shadow-lg',
        'shadow-slate-500/20 transition-all duration-300',
        'hover:shadow-xl',
        dragOver && 'border-emerald-400 shadow-emerald-500/40',
        selected && 'ring-2 ring-white ring-offset-2'
          + ' ring-offset-gray-900',
      )}
    >
      <div className="flex items-center gap-3 p-4 pb-2">
        <div className="rounded-lg bg-white/20 p-2 backdrop-blur-sm">
          <FileInput className="h-5 w-5 text-white" />
        </div>
        <h3 className="text-sm font-bold text-white">
          {data.title || 'Файлы данных'}
        </h3>
        <span className="min-w-0 flex-1 truncate text-xs text-white/70">
          файлов: {data.files.length} · политика обработки и
          примечание — шестерёнка у файла
        </span>
      </div>
      <div className="space-y-1.5 px-4 pb-4">
        {data.files.map(f => {
          const c = data.cfg[f] || { processing: 'local', note: '' }
          const chip = POLICY_CHIP[c.processing] || POLICY_CHIP.local
          return (
            <div key={f} className="rounded-lg bg-white/10 px-3 py-2">
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4 shrink-0 text-white/70" />
                <span className="min-w-0 flex-1 truncate font-mono
                                 text-xs text-white" title={f}>
                  {f}
                </span>
                <span className={clsx(
                  'rounded-full px-2 py-0.5 text-[11px] font-medium',
                  chip.cls)} title={chip.tip}>
                  {chip.label}
                </span>
                <button
                  onClick={e => {
                    e.stopPropagation()
                    data.onFileGear?.(f, e)
                  }}
                  aria-label={`Настроить обработку: ${f}`}
                  title="Политика обработки и примечание"
                  className="rounded-lg bg-white/10 p-1.5 transition-all
                             hover:bg-white/30"
                >
                  <Settings2 className="h-4 w-4 text-white" />
                </button>
                {data.onFileDelete && (
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      data.onFileDelete?.(f)
                    }}
                    aria-label={`Удалить файл данных: ${f}`}
                    title="Удалить файл данных: файл, его политика,
запись в статусе и источник в замороженном наборе уходят разом;
отчёт проверок по прежнему составу снимается"
                    className="rounded-lg bg-white/10 p-1.5 transition-all
                               hover:bg-red-500/60"
                  >
                    <Trash2 className="h-4 w-4 text-white" />
                  </button>
                )}
              </div>
              {c.note && (
                <p className="mt-1 truncate pl-6 text-[11px] italic
                              text-white/70" title={c.note}>
                  {c.note}
                </p>
              )}
            </div>
          )
        })}
        {canUpload && (
          <div
            role="button"
            tabIndex={0}
            onClick={e => { e.stopPropagation(); pick() }}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                // Не отдавать клавишу обёртке узла React Flow — иначе
                // Enter/пробел ещё и переключали бы выделение блока.
                e.stopPropagation()
                pick()
              }
            }}
            aria-label={data.files.length
              ? 'Добавить файлы данных' : 'Загрузить файлы данных'}
            title={data.uploadHint}
            className={clsx(
              'nodrag nopan mt-1 flex cursor-pointer items-center',
              'justify-center gap-2 rounded-lg border-2 border-dashed',
              'px-3 py-2 text-xs transition-colors',
              dragOver
                ? 'border-emerald-400 bg-emerald-500/20 text-white'
                : 'border-white/30 text-white/70 hover:border-white/60'
                  + ' hover:text-white')}
          >
            <UploadCloud className="h-4 w-4 shrink-0" />
            {data.files.length
              ? 'Добавить файлы: перетащите сюда или нажмите'
              : (data.uploadHint
                 || 'Перетащите файлы сюда или нажмите, чтобы выбрать')}
            <input ref={inputRef} type="file" multiple
                   accept={data.accept} className="hidden"
                   onClick={e => e.stopPropagation()}
                   onChange={e => {
                     if (e.target.files?.length) {
                       data.onUpload?.(e.target.files)
                     }
                     e.target.value = ''
                   }} />
          </div>
        )}
        {!canUpload && data.files.length === 0 && (
          <p className="text-xs text-white/70">
            {data.emptyHint || 'файлов пока нет'}
          </p>
        )}
      </div>
      <Handle type="source" position={Position.Right}
              className="!h-3 !w-3 !border-2 !border-gray-700
                         !bg-white" />
    </div>
  )
}

export default memo(FilesNode)
