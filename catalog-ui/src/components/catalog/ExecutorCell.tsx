import clsx from 'clsx'
import StateChip from './StateChip'
import StatusBadge from '../common/StatusBadge'
import { stateBadgeKind, type ExecutorCellData, type ExecutorRow }
  from '../../api'
import { t, tf } from '../../i18n'

// One cell of the matrix "record x kind x product". The chip shows the
// BEST state of the cell (present > claimed > absent, STATE_* of api.ts):
// one product may legally have several rows of the same kind, and the
// cell must not look empty because the last row happened to be absent.
//
// A cell without rows at all is "nobody declared this kind" (blue), not
// "absent" (grey) - the difference matters when a product simply has
// the kind outside its zone.
const FIELD_ORDER = [
  'constraint', 'label', 'param', 'threshold', 'keyed', 'unit', 'relation',
  'accepts', 'form', 'constraint_id_rule', 'params', 'param_set', 'metric',
  'tasks', 'approximation', 'refusal', 'template',
]

function cellTip(cell: ExecutorCellData | undefined, kind: string): string {
  const rows = Object.values(cell?.by ?? {})
  const best = rows.find(row => row.state === cell?.best) ?? rows[0]
  if (!best) return t('executors.cellUndeclaredTip')
  return tf('executors.cellTipRow', {
    kind,
    symbol: best.symbol ?? '',
    file: best.file ?? '',
  })
}

export default function ExecutorCell({ cell, kind, active, onToggle }: {
  cell: ExecutorCellData | undefined
  kind: string
  active: boolean
  onToggle: () => void
}) {
  return (
    <button
      onClick={onToggle}
      aria-label={active ? t('executors.collapse') : t('executors.expand')}
      title={cellTip(cell, kind)}
      className={clsx('rounded-lg p-1 transition-colors',
                      active ? 'bg-cf-hover' : 'hover:bg-cf-hover')}
    >
      <StateChip state={cell?.best ?? null} />
    </button>
  )
}

/** Rows of one cell by product: language, file, symbol, kind fields, note. */
export function ExecutorCellRows({ cell }: {
  cell: ExecutorCellData | undefined
}) {
  const rows = Object.entries(cell?.by ?? {})
  if (!rows.length) {
    return (
      <p className="text-sm text-cf-text-muted">
        {t('executors.cellUndeclaredTip')}
      </p>
    )
  }
  return (
    <div className="space-y-2">
      {rows.map(([owner, row]) => (
        <div key={owner} className="rounded-lg border border-cf-border
                                    bg-cf-input p-2">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-cf-text">{owner}</span>
            <StatusBadge kind={stateBadgeKind(row.state)}>
              {row.state}
            </StatusBadge>
            {row.cr_key && (
              <span className="font-mono text-xs text-cf-text-muted">
                {t('executors.rowCrKey')}: {row.cr_key}
              </span>
            )}
          </div>
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs
                         sm:grid-cols-2">
            {row.lang && (
              <div>
                <dt className="inline text-cf-text-muted">
                  {t('executors.rowLang')}:{' '}
                </dt>
                <dd className="inline font-mono">{row.lang}</dd>
              </div>
            )}
            {row.file && (
              <div className="sm:col-span-2">
                <dt className="inline text-cf-text-muted">
                  {t('executors.rowFile')}:{' '}
                </dt>
                <dd className="inline break-all font-mono">{row.file}</dd>
              </div>
            )}
            {row.symbol && (
              <div className="sm:col-span-2">
                <dt className="inline text-cf-text-muted">
                  {t('executors.rowSymbol')}:{' '}
                </dt>
                <dd className="inline break-all font-mono">{row.symbol}</dd>
              </div>
            )}
            {FIELD_ORDER.filter(field => fieldOf(row, field) !== null)
              .map(field => (
                <div key={field}>
                  <dt className="inline text-cf-text-muted">{field}: </dt>
                  <dd className="inline break-all font-mono">
                    {fieldOf(row, field)}
                  </dd>
                </div>
              ))}
            {row.note && (
              <div className="sm:col-span-2">
                <dt className="inline text-cf-text-muted">
                  {t('executors.rowNote')}:{' '}
                </dt>
                <dd className="inline">{row.note}</dd>
              </div>
            )}
          </dl>
        </div>
      ))}
    </div>
  )
}

function fieldOf(row: ExecutorRow, field: string): string | null {
  const direct = row[field]
  const nested = (row.fields ?? {})[field]
  const value = direct === undefined ? nested : direct
  if (value === undefined || value === null) return null
  if (Array.isArray(value)) {
    return value.length ? value.map(String).join(', ') : null
  }
  return String(value)
}
