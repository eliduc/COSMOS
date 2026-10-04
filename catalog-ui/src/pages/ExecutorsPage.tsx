import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Cpu } from 'lucide-react'
import Button from '../components/common/Button'
import StatusBadge from '../components/common/StatusBadge'
import ExecutorCell, { ExecutorCellRows }
  from '../components/catalog/ExecutorCell'
import ProblemList from '../components/catalog/ProblemList'
import {
  getExecutors, ServiceError, STATE_ABSENT, STATE_CLAIMED, STATE_PRESENT,
  STATE_UNDECLARED_FILTER, TOTALS_UNDECLARED,
  type BadgeKind, type ExecutorProduct, type ExecutorsReport,
  type Problem, type StandRow,
} from '../api'
import { dictList, useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'
import type { MessageKey } from '../i18n'

// Screen "Executors by product": read only. Rows come from the binding
// files of the products (vmp/bindings.json, cdpm/bindings.json), and
// the products own them - the catalog only says which kinds a record
// declares. A missing file of a product is not a refusal: it is honest
// "not declared" with the words next to the product card.
const FRESHNESS_LABEL: Record<string, MessageKey> = {
  match: 'executors.freshMatch',
  'stale-draft': 'executors.freshStale',
  stop: 'executors.freshStop',
  missing: 'executors.freshMissing',
}

/** Colour of a stand result - the same map as in the record card. */
const STAND_RESULT_KIND: Record<string, BadgeKind> = {
  passed: 'ok',
  failed: 'error',
  skipped: 'neutral',
}

const FRESHNESS_KIND: Record<string, BadgeKind> = {
  match: 'ok',
  'stale-draft': 'warn',
  stop: 'error',
  missing: 'neutral',
}

function ProductCard({ product }: { product: ExecutorProduct }) {
  // Binding problems fold away: a product with a narrow zone can have
  // dozens of them, and the product card would stop being readable.
  const [open, setOpen] = useState(false)
  return (
    <div className="card min-w-[18rem] flex-1 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{product.product}</h3>
        <StatusBadge kind={FRESHNESS_KIND[product.freshness] ?? 'neutral'}
                     title={product.freshness_words}>
          {FRESHNESS_LABEL[product.freshness]
            ? t(FRESHNESS_LABEL[product.freshness]) : product.freshness}
        </StatusBadge>
        <StatusBadge kind={product.found ? 'ok' : 'neutral'}>
          {product.found ? t('executors.fileFound')
            : t('executors.fileMissing')}
        </StatusBadge>
      </div>
      <p className="font-mono text-xs text-cf-text-muted">
        {product.file_name}
      </p>
      <dl className="grid grid-cols-1 gap-x-3 text-xs sm:grid-cols-2">
        <div>
          <dt className="inline text-cf-text-muted">
            {t('executors.productVersion')}:{' '}
          </dt>
          <dd className="inline font-mono">
            {product.product_version ?? ''}
          </dd>
        </div>
        <div>
          <dt className="inline text-cf-text-muted">
            {t('executors.catalogVersion')}:{' '}
          </dt>
          <dd className="inline font-mono">
            {product.catalog_version ?? ''}
            {product.catalog_fingerprint
              ? ` · ${product.catalog_fingerprint.slice(0, 12)}` : ''}
          </dd>
        </div>
        <div>
          <dt className="inline text-cf-text-muted">
            {t('executors.generatedAt')}:{' '}
          </dt>
          <dd className="inline font-mono">{product.generated_at ?? ''}</dd>
        </div>
        <div>
          <dt className="inline text-cf-text-muted">
            {t('executors.generatedFrom')}:{' '}
          </dt>
          <dd className="inline font-mono">
            {(product.generated_from ?? '').slice(0, 7)}
          </dd>
        </div>
      </dl>
      {product.problems?.length > 0 && (
        <div className="pt-2">
          <button
            className="mb-1 text-xs font-semibold text-cf-error"
            onClick={() => setOpen(!open)}
          >
            {tf('executors.problemsCount',
                { n: product.problems.length })}
          </button>
          {open && <ProblemList problems={product.problems} compact />}
        </div>
      )}
    </div>
  )
}

// Problems that belong to no single file: completeness by kind (B04) is
// judged by the zones of all binding files at once, so its address is a
// record, not a product. They fold away like the product ones.
function CommonProblems({ problems }: { problems: Problem[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card space-y-1">
      <button className="text-xs font-semibold text-cf-error"
              title={t('executors.commonProblemsHint')}
              onClick={() => setOpen(!open)}>
        {tf('executors.commonProblemsCount', { n: problems.length })}
      </button>
      <p className="text-xs text-cf-text-muted">
        {t('executors.commonProblemsHint')}
      </p>
      {open && <ProblemList problems={problems} compact />}
    </div>
  )
}

export default function ExecutorsPage() {
  const [params, setParams] = useSearchParams()
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  useEffect(() => { loadDicts() }, [loadDicts])

  const [report, setReport] = useState<ExecutorsReport | null>(null)
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [standOpen, setStandOpen] = useState<string | null>(null)

  const filter = useMemo(() => ({
    product: params.get('product') ?? '',
    section: params.get('section') ?? '',
    id: params.get('id') ?? '',
    kind: params.get('kind') ?? '',
    state: params.get('state') ?? '',
  }), [params])

  const reload = useCallback(() => {
    setBusy(true)
    getExecutors(filter)
      .then(got => { setReport(got); setFailed(null) })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
      .finally(() => setBusy(false))
  }, [filter])

  useEffect(() => { reload() }, [reload])

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const kinds = report?.kinds ?? dictList(dicts, 'executor_kinds')
  const totals = report?.totals ?? {}

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Cpu className="h-5 w-5 text-cf-primary" />
        <h1 className="text-base font-semibold">{t('executors.title')}</h1>
        <span className="text-xs text-cf-text-muted">
          {t('executors.readOnly')}
        </span>
      </div>

      <div className="flex flex-wrap items-end gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-2">
        <select className="input-field max-w-[10rem]"
                value={filter.product}
                title={t('executors.filterProduct')}
                onChange={e => setFilter('product', e.target.value)}>
          <option value="">
            {`${t('executors.filterProduct')}: ${t('common.all')}`}
          </option>
          {dictList(dicts, 'products').map(one => (
            <option key={one} value={one}>{one}</option>
          ))}
        </select>
        <select className="input-field max-w-[10rem]"
                value={filter.section}
                title={t('executors.filterSection')}
                onChange={e => setFilter('section', e.target.value)}>
          <option value="">
            {`${t('executors.filterSection')}: ${t('common.all')}`}
          </option>
          <option value="families">{t('records.families')}</option>
          <option value="criteria">{t('records.criteria')}</option>
        </select>
        <select className="input-field max-w-[14rem]"
                value={filter.kind}
                title={t('executors.filterKind')}
                onChange={e => setFilter('kind', e.target.value)}>
          <option value="">
            {`${t('executors.filterKind')}: ${t('common.all')}`}
          </option>
          {kinds.map(one => (
            <option key={one} value={one}>{one}</option>
          ))}
        </select>
        <select className="input-field max-w-[12rem]"
                value={filter.state}
                title={t('executors.filterState')}
                onChange={e => setFilter('state', e.target.value)}>
          <option value="">
            {`${t('executors.filterState')}: ${t('common.all')}`}
          </option>
          {dictList(dicts, 'executor_states').map(one => (
            <option key={one} value={one}>{one}</option>
          ))}
          <option value={STATE_UNDECLARED_FILTER}>
            {t('records.filterStateUndeclared')}
          </option>
        </select>
        <input className="input-field max-w-[12rem]"
               placeholder={t('executors.colRecord')}
               value={filter.id}
               onChange={e => setFilter('id', e.target.value)} />
        <Button variant="ghost" size="sm"
                onClick={() => setParams(new URLSearchParams(),
                                         { replace: true })}>
          {t('common.reset')}
        </Button>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {failed && (
          <div className="card mb-4 border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}
        {busy && (
          <p className="text-sm text-cf-text-muted">{t('common.loading')}</p>
        )}

        {report && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-3">
              {report.products.map(one => (
                <ProductCard key={one.product} product={one} />
              ))}
            </div>

            {(report.problems?.length ?? 0) > 0 && (
              <CommonProblems problems={report.problems ?? []} />
            )}

            <p className="text-xs text-cf-text-muted">
              {tf('executors.totals', {
                cells: totals.cells ?? 0,
                present: totals[STATE_PRESENT] ?? 0,
                claimed: totals[STATE_CLAIMED] ?? 0,
                absent: totals[STATE_ABSENT] ?? 0,
                undeclared: totals[TOTALS_UNDECLARED] ?? 0,
              })}
            </p>

            <div className="overflow-x-auto rounded-xl border
                            border-cf-border bg-cf-panel">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    <th className="table-head">{t('executors.colRecord')}</th>
                    {kinds.map(kind => (
                      <th key={kind} className="table-head">{kind}</th>
                    ))}
                    <th className="table-head">{t('executors.colStand')}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map(row => {
                    const rowKey = `${row.section}/${row.id}`
                    const openKind = open?.startsWith(`${rowKey}|`)
                      ? open.slice(rowKey.length + 1) : null
                    return (
                      <Fragment key={rowKey}>
                        <tr className="table-row">
                          <td className="table-cell font-mono text-xs">
                            <Link to={`/records/${row.section}/${row.id}`}
                                  className="text-cf-primary
                                             hover:underline">
                              {row.id}
                            </Link>
                          </td>
                          {kinds.map(kind => (
                            <td key={kind} className="table-cell">
                              <ExecutorCell
                                cell={row.cells?.[kind]}
                                kind={kind}
                                active={openKind === kind}
                                onToggle={() => setOpen(
                                  openKind === kind
                                    ? null : `${rowKey}|${kind}`)}
                              />
                            </td>
                          ))}
                          <td className="table-cell text-xs">
                            <StandCounts
                              stand={row.stand}
                              open={standOpen === rowKey}
                              onToggle={() => setStandOpen(
                                standOpen === rowKey ? null : rowKey)}
                            />
                          </td>
                        </tr>
                        {openKind && (
                          <tr>
                            <td className="table-cell"
                                colSpan={kinds.length + 2}>
                              <ExecutorCellRows
                                cell={row.cells?.[openKind]} />
                            </td>
                          </tr>
                        )}
                        {standOpen === rowKey && (
                          <tr>
                            <td className="table-cell"
                                colSpan={kinds.length + 2}>
                              <StandRows stand={row.stand} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function StandCounts({ stand, open, onToggle }: {
  stand: Record<string, StandRow[]> | undefined
  open: boolean
  onToggle: () => void
}) {
  const rows = Object.values(stand ?? {}).flat()
  if (!rows.length) {
    return (
      <span className="text-cf-text-muted">{t('executors.standEmpty')}</span>
    )
  }
  const counts: Record<string, number> = {}
  for (const row of rows) counts[row.result] = (counts[row.result] ?? 0) + 1
  return (
    <button
      className="flex flex-wrap gap-1"
      title={open ? t('executors.standCollapse') : t('executors.standExpand')}
      onClick={onToggle}
    >
      {Object.entries(counts).map(([result, count]) => (
        <StatusBadge
          key={result}
          kind={result === 'failed' ? 'error'
            : result === 'passed' ? 'ok' : 'neutral'}
          title={result}
        >
          {tf('executors.standCounts', { result, n: count })}
        </StatusBadge>
      ))}
    </button>
  )
}

// The stand_verified rows of a product (section 4.4 of the project):
// task, kind, result in colour, when, the slice of code and the words.
// The counters above them are the door; the rows are the truth itself.
function StandRows({ stand }: {
  stand: Record<string, StandRow[]> | undefined
}) {
  const pairs = Object.entries(stand ?? {})
  if (!pairs.length) {
    return (
      <p className="text-xs text-cf-text-muted">{t('executors.standEmpty')}</p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="table-head">{t('executors.standProduct')}</th>
            <th className="table-head">{t('executors.standTask')}</th>
            <th className="table-head">{t('executors.standKind')}</th>
            <th className="table-head">{t('executors.standResult')}</th>
            <th className="table-head">{t('executors.standAt')}</th>
            <th className="table-head">{t('executors.standCommit')}</th>
            <th className="table-head">{t('executors.standNote')}</th>
          </tr>
        </thead>
        <tbody>
          {pairs.map(([product, rows]) => rows.map((row, index) => (
            <tr key={`${product}-${row.task}-${row.executor_kind}-${index}`}
                className="table-row">
              <td className="table-cell text-xs">{product}</td>
              <td className="table-cell font-mono text-xs">{row.task}</td>
              <td className="table-cell font-mono text-xs">
                {row.executor_kind}
              </td>
              <td className="table-cell">
                <StatusBadge kind={STAND_RESULT_KIND[row.result] ?? 'neutral'}>
                  {row.result}
                </StatusBadge>
              </td>
              <td className="table-cell whitespace-nowrap text-xs">
                {row.at}
              </td>
              <td className="table-cell font-mono text-xs">
                {(row.commit ?? '').slice(0, 7)}
              </td>
              <td className="table-cell text-xs">{row.note ?? ''}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  )
}
