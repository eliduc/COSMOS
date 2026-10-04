import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { BookMarked, CheckCircle2, ExternalLink, XCircle } from 'lucide-react'
import clsx from 'clsx'

// Узел-спутник «Каталог шаблонов» на схеме прогона (слово владельца
// 17.09.2026): каталог — ОТДЕЛЬНАЯ служба, а не стадия конвейера,
// поэтому узел стоит вне рамок групп и связан с блоками пунктиром:
// словари и посадка — Обработчику, записи семей — Своду, заявки на
// пробелы (посадки C) — от Итога. Данные узла — та же проба каталога
// и тот же адрес службы, что у строки в меню (catalogStore); своей
// правды у узла нет. Клик по узлу открывает панель службы (RunPage),
// пока служба настроена; без настройки узел говорит об этом словами.
export interface CatalogNodeData extends Record<string, unknown> {
  version?: string | null
  fingerprint?: string | null
  // null — проба каталога не отвечает (сервер прежней версии).
  available: boolean | null
  words?: string | null
  href: string | null
  // Заявлено в каталог из посадок C — по состоянию прогона.
  submitted?: number
  gaps?: number
}

// Цвет каталога — свой, ни одной группы схемы не повторяет.
export const CATALOG_COLOR = '#c026d3'

function CatalogNode({ data }: { data: CatalogNodeData }) {
  const fp = (data.fingerprint || '').slice(0, 12)
  const stateWords = data.available === null
    ? 'проба каталога не отвечает'
    : data.available ? 'каталог доступен'
      : (data.words || 'каталог недоступен')
  const ok = data.available === true
  const tip = 'Каталог шаблонов — отдельная служба: словарь машинных '
    + 'параметров, записи семей, заявки на пробелы, версии. Панель '
    + 'читает его на старте, в паспорте прогона, при извлечении и '
    + 'посадке правил, в форме ВМП, в «Итоге» (заявки) и при выпуске '
    + 'пакета (отпечаток в манифесте).'
    + (data.href ? '\nКлик — открыть панель службы.'
      : '\nСлужба каталога не настроена (CREW_CATALOG_URL).')
  return (
    <div
      className="w-[300px] cursor-pointer rounded-2xl border-2
                 border-dashed bg-cf-panel p-3 shadow-md"
      style={{ borderColor: CATALOG_COLOR }}
      title={tip}
    >
      <div className="flex items-center gap-3">
        <div className="rounded-lg p-2"
             style={{ background: `${CATALOG_COLOR}22` }}>
          <BookMarked className="h-5 w-5" style={{ color: CATALOG_COLOR }} />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-bold leading-tight text-cf-text">
            Каталог шаблонов
          </h3>
          <p className="truncate text-xs font-medium text-cf-text-muted">
            отдельная служба · словари, семьи, заявки, версии
          </p>
        </div>
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-xs"
           title={`Состояние каталога: ${stateWords}`}>
        {ok
          ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
          : <XCircle className="h-3.5 w-3.5 text-red-500" />}
        <span className={clsx('truncate font-medium',
                              ok ? 'text-emerald-600' : 'text-red-500')}>
          {stateWords}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t pt-2"
           style={{ borderColor: `${CATALOG_COLOR}55` }}>
        <div className="flex items-center gap-1 rounded px-1.5 py-0.5"
             style={{ background: `${CATALOG_COLOR}14` }}
             title="Версия каталога, которым работает панель">
          <span className="text-xs font-semibold text-cf-text">
            {data.version || '—'}
          </span>
          <span className="text-[10px] text-cf-text-muted">версия</span>
        </div>
        <div className="flex items-center gap-1 rounded px-1.5 py-0.5"
             style={{ background: `${CATALOG_COLOR}14` }}
             title={data.fingerprint
               ? `отпечаток каталога: ${data.fingerprint}`
               : 'отпечатка нет'}>
          <span className="font-mono text-xs font-semibold text-cf-text">
            {fp || '—'}
          </span>
          <span className="text-[10px] text-cf-text-muted">отпечаток</span>
        </div>
        {typeof data.submitted === 'number' && (
          <div className="flex items-center gap-1 rounded px-1.5 py-0.5"
               style={{ background: `${CATALOG_COLOR}14` }}
               title="Заявлено в каталог из посадок C этого прогона">
            <span className="text-xs font-semibold text-cf-text">
              {/* «из N» — только пока строки C ещё есть: после выпуска
                  каталога они пересаживаются, и «1 из 0» читалось бы как
                  ошибка, хотя заявка по-прежнему одна. */}
              {data.submitted}{typeof data.gaps === 'number' && data.gaps > 0
                ? ` из ${data.gaps}` : ''}
            </span>
            <span className="text-[10px] text-cf-text-muted">заявлено</span>
          </div>
        )}
      </div>

      {data.href ? (
        <a href={data.href} target="_blank" rel="noopener noreferrer"
           onClick={e => e.stopPropagation()}
           className="mt-2 inline-flex items-center gap-1 text-xs
                      text-cf-primary hover:underline"
           title="Открыть панель службы каталога шаблонов">
          открыть службу <ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : (
        <p className="mt-2 text-[11px] text-cf-text-muted">
          служба каталога не настроена (CREW_CATALOG_URL)
        </p>
      )}

      {/* Три нижних коннектора — по одному на связь: Обработчик,
          Свод, Итог. Все — source: направление заявок (от Итога)
          рисуется стрелкой у начала ребра. */}
      <Handle type="source" position={Position.Bottom} id="w"
              style={{ left: '20%' }}
              className="!h-3 !w-3 !border-2 !border-gray-700 !bg-white" />
      <Handle type="source" position={Position.Bottom} id="a"
              style={{ left: '50%' }}
              className="!h-3 !w-3 !border-2 !border-gray-700 !bg-white" />
      <Handle type="source" position={Position.Bottom} id="o"
              style={{ left: '80%' }}
              className="!h-3 !w-3 !border-2 !border-gray-700 !bg-white" />
    </div>
  )
}

export default memo(CatalogNode)
