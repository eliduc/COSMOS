import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ClipboardCheck, Layers, Plus, PackageCheck } from 'lucide-react'
import Button from '../components/common/Button'
import Modal from '../components/common/Modal'
import StatusBadge from '../components/common/StatusBadge'
import notify from '../components/common/StyledToast'
import ProblemList from '../components/catalog/ProblemList'
import {
  listRecords, rightHint, rightOf, stateBadgeKind, statusBadgeKind,
  validateCatalog, ServiceError, STATE_UNDECLARED_FILTER, STATUS_PRESENT,
  type RecordRow, type Section,
} from '../api'
import { dictList, dictWords, useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'
import type { MessageKey } from '../i18n'

// Screen "Families and criteria": the list with filters built from the
// closed dictionaries of the catalog, and the door to the card of every
// record. Filters live in the address (HashRouter), so a link from a
// product opens the same view.
const ID_PATTERN = /^[a-z][a-z0-9_]{2,40}$/

const FILTER_KEYS = ['kind', 'zone', 'class', 'state', 'status'] as const

const FILTER_DICT: Record<string, string> = {
  kind: 'kinds',
  zone: 'zones',
  class: 'classes',
  state: 'executor_states',
  status: 'record_statuses',
}

const FILTER_LABEL: Record<string, MessageKey> = {
  kind: 'records.filterKind',
  zone: 'records.filterZone',
  class: 'records.filterClass',
  state: 'records.filterState',
  status: 'records.filterStatus',
}

/** Legal values of a filter. The executor state has one more than the
 *  dictionary `executor_states` holds: "undeclared" is a CELL without
 *  rows, not a state of the catalog (contract, sections 4 and 6.1).
 *  Without it the screen would drop its own menu item as unknown. */
function knownValues(dicts: Record<string, unknown>, key: string): string[] {
  const known = dictList(dicts, FILTER_DICT[key])
  if (key !== 'state' || !known.length) return known
  return [...known, STATE_UNDECLARED_FILTER]
}

export default function RecordsPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  useEffect(() => { loadDicts() }, [loadDicts])
  const unitWords = useMemo(
    () => dictWords(dicts, 'violation_units'), [dicts])

  const [rows, setRows] = useState<RecordRow[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [problemsOpen, setProblemsOpen] = useState(false)
  const [problems, setProblems] = useState<
    Awaited<ReturnType<typeof validateCatalog>> | null>(null)
  const [newOpen, setNewOpen] = useState(false)
  const [newSection, setNewSection] = useState<Section>('families')
  const [newId, setNewId] = useState('')
  const [newTitle, setNewTitle] = useState('')

  const section = (params.get('section') === 'criteria'
    ? 'criteria' : 'families') as Section
  const status = params.get('status') ?? STATUS_PRESENT
  const query = params.get('q') ?? ''

  const filter = useMemo(() => ({
    section,
    status,
    kind: params.get('kind') ?? '',
    zone: params.get('zone') ?? '',
    class: params.get('class') ?? '',
    state: params.get('state') ?? '',
    q: query,
    drafts: params.get('drafts') ?? '',
    requests: params.get('requests') ?? '',
  }), [section, status, query, params])

  // A filter value outside its dictionary is dropped with words: a link
  // from a product must not leave the screen showing an empty list for
  // an unknown reason.
  useEffect(() => {
    for (const key of FILTER_KEYS) {
      const value = params.get(key)
      if (!value || value === 'all') continue
      const known = knownValues(dicts, key)
      if (!known.length || known.includes(value)) continue
      notify.warning(tf('records.filterDropped',
        { value, dict: FILTER_DICT[key] }))
      const next = new URLSearchParams(params)
      next.delete(key)
      setParams(next, { replace: true })
      break
    }
  }, [params, dicts, setParams])

  const reload = useCallback(() => {
    setBusy(true)
    listRecords(filter)
      .then(got => {
        setRows(got.items ?? [])
        setTotal(got.total ?? (got.items ?? []).length)
        setFailed(null)
      })
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

  function filterWords(): string {
    const parts: string[] = [`${t('records.title')}: ${section}`]
    for (const key of FILTER_KEYS) {
      const value = key === 'status' ? status : params.get(key)
      if (value) parts.push(`${t(FILTER_LABEL[key])} — ${value}`)
    }
    if (params.get('drafts')) parts.push(t('records.filterDrafts'))
    if (params.get('requests')) parts.push(t('records.filterRequests'))
    return parts.join(', ')
  }

  async function checkCatalog() {
    try {
      const got = await validateCatalog()
      setProblems(got)
      setProblemsOpen(true)
      if (got.errors === 0) {
        notify.success(tf('records.validateOk', { warnings: got.warnings }))
      } else {
        notify.error(tf('records.validateBad',
          { errors: got.errors, warnings: got.warnings }))
      }
    } catch (err) {
      notify.error(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err))
    }
  }

  // Rights come from the service: a button is disabled by
  // rights[operation].allowed and the tooltip carries its words.
  const assembleRight = rightOf(session, 'assemble')
  const draftRight = rightOf(session, 'draft-save')
  const idOk = ID_PATTERN.test(newId)

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Layers className="h-5 w-5 text-cf-primary" />
        <h1 className="text-base font-semibold">{t('records.title')}</h1>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button
            variant="secondary" size="sm"
            leadingIcon={<ClipboardCheck className="h-4 w-4" />}
            onClick={checkCatalog}
          >
            {t('records.validate')}
          </Button>
          <Button
            variant="secondary" size="sm"
            leadingIcon={<PackageCheck className="h-4 w-4" />}
            onClick={() => navigate('/versions?assemble=1')}
            disabled={!assembleRight.allowed}
            title={rightHint(assembleRight)}
          >
            {t('records.assemble')}
          </Button>
          <Button
            size="sm"
            leadingIcon={<Plus className="h-4 w-4" />}
            onClick={() => setNewOpen(true)}
            disabled={!draftRight.allowed}
            title={rightHint(draftRight)}
          >
            {t('records.new')}
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-2">
        <div className="flex overflow-hidden rounded-lg border
                        border-cf-border">
          {(['families', 'criteria'] as Section[]).map(one => (
            <button
              key={one}
              onClick={() => setFilter('section', one)}
              className={one === section
                ? 'bg-cf-primary/10 px-3 py-1.5 text-xs font-medium'
                  + ' text-cf-primary'
                : 'px-3 py-1.5 text-xs text-cf-text-muted'
                  + ' hover:bg-cf-hover'}
            >
              {one === 'families'
                ? t('records.families') : t('records.criteria')}
            </button>
          ))}
        </div>
        <input
          className="input-field max-w-xs"
          placeholder={t('records.searchPlaceholder')}
          value={query}
          onChange={e => setFilter('q', e.target.value)}
        />
        {FILTER_KEYS.map(key => {
          const chosen = key === 'status' ? status : (params.get(key) ?? '')
          const known = dictList(dicts, FILTER_DICT[key])
          // The "undeclared" item is drawn separately below.
          return (
          <select
            key={key}
            className="input-field max-w-[10rem]"
            value={chosen}
            onChange={e => setFilter(key, e.target.value)}
            title={t(FILTER_LABEL[key])}
          >
            <option value={key === 'status' ? 'all' : ''}>
              {`${t(FILTER_LABEL[key])}: ${t('common.all')}`}
            </option>
            {known.map(value => (
              <option key={value} value={value}>{value}</option>
            ))}
            {/* Dictionaries not read yet: the chosen value is shown as
                it is, so the list never lies about the filter. */}
            {chosen && chosen !== 'all' && !known.includes(chosen)
              && !(key === 'state' && chosen === STATE_UNDECLARED_FILTER)
              && <option value={chosen}>{chosen}</option>}
            {key === 'state' && (
              <option value={STATE_UNDECLARED_FILTER}>
                {t('records.filterStateUndeclared')}
              </option>
            )}
          </select>
          )
        })}
        <label className="flex items-center gap-1 text-xs
                          text-cf-text-muted">
          <input
            type="checkbox"
            checked={!!params.get('drafts')}
            onChange={e => setFilter('drafts', e.target.checked ? '1' : '')}
          />
          {t('records.filterDrafts')}
        </label>
        <label className="flex items-center gap-1 text-xs
                          text-cf-text-muted">
          <input
            type="checkbox"
            checked={!!params.get('requests')}
            onChange={e => setFilter('requests',
                                     e.target.checked ? '1' : '')}
          />
          {t('records.filterRequests')}
        </label>
        <Button variant="ghost" size="sm"
                onClick={() => setParams(new URLSearchParams(
                  { section }), { replace: true })}>
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
        {!busy && rows.length === 0 && (
          <p className="text-sm text-cf-text-muted">
            {query
              ? tf('records.emptyWords',
                   { q: query, filters: filterWords() })
              : tf('records.emptyNoQuery', { filters: filterWords() })}
          </p>
        )}
        {rows.length > 0 && (
          <div className="overflow-x-auto rounded-xl border
                          border-cf-border bg-cf-panel">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="table-head">{t('records.colId')}</th>
                  <th className="table-head">{t('records.colTitle')}</th>
                  <th className="table-head">{t('records.colClass')}</th>
                  {section === 'families' && (
                    <th className="table-head">{t('records.colKind')}</th>
                  )}
                  <th className="table-head">{t('records.colZone')}</th>
                  {section === 'families' && (
                    <th className="table-head">{t('records.colNature')}</th>
                  )}
                  {section === 'families' && (
                    <th className="table-head">
                      {t('records.colViolationUnit')}
                    </th>
                  )}
                  <th className="table-head">{t('records.colParams')}</th>
                  <th className="table-head">{t('records.colStand')}</th>
                  <th className="table-head">{t('records.colExecutors')}</th>
                  <th className="table-head">{t('records.colDraft')}</th>
                  <th className="table-head">{t('records.colRequest')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={`${row.section}/${row.id}`} className="table-row">
                    <td className="table-cell font-mono text-xs">
                      <Link
                        to={`/records/${row.section}/${row.id}`}
                        className="text-cf-primary hover:underline"
                      >
                        {row.id}
                      </Link>
                    </td>
                    <td className="table-cell">
                      {row.title}
                      {row.status && row.status !== STATUS_PRESENT && (
                        <StatusBadge kind={statusBadgeKind(row.status)}
                                     className="ml-2">
                          {row.status}
                        </StatusBadge>
                      )}
                    </td>
                    <td className="table-cell">{row.class}</td>
                    {section === 'families' && (
                      <td className="table-cell">{row.kind ?? ''}</td>
                    )}
                    <td className="table-cell">{row.zone ?? ''}</td>
                    {section === 'families' && (
                      <td className="table-cell">{row.nature ?? ''}</td>
                    )}
                    {section === 'families' && (
                      // Unit of violation: the code, and next to it the
                      // word of the catalog (the dictionary
                      // `violation_units` is a map "code -> word" since
                      // 0.5.0). The screen keeps no second map of
                      // words: the dictionary not read yet - only the
                      // code is seen.
                      <td className="table-cell">
                        {row.violation_unit ?? ''}
                        {unitWords[row.violation_unit ?? ''] && (
                          <span className="ml-1 text-xs text-cf-text-muted">
                            {unitWords[row.violation_unit ?? '']}
                          </span>
                        )}
                      </td>
                    )}
                    <td className="table-cell">{row.params}</td>
                    <td className="table-cell whitespace-nowrap text-xs">
                      <StandCell row={row} />
                    </td>
                    <td className="table-cell">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(row.executor_summary ?? {})
                          .filter(([, count]) => count > 0)
                          .map(([state, count]) => (
                            <StatusBadge
                              key={state}
                              kind={stateBadgeKind(state)}
                              title={state}
                            >
                              {count}
                            </StatusBadge>
                          ))}
                      </div>
                    </td>
                    <td className="table-cell">
                      {row.draft && (
                        <StatusBadge kind="warn">{row.draft}</StatusBadge>
                      )}
                    </td>
                    <td className="table-cell font-mono text-xs">
                      {row.request_id && (
                        <Link to={`/requests/${row.request_id}`}
                              className="text-cf-primary hover:underline">
                          {row.request_id}
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {rows.length > 0 && (
          <p className="mt-2 text-xs text-cf-text-muted">
            {tf('common.shown', { shown: rows.length, total })}
          </p>
        )}
      </div>

      <Modal open={problemsOpen} onClose={() => setProblemsOpen(false)}
             title={t('records.validate')} wide>
        {problems && <ProblemList problems={problems.problems} />}
      </Modal>

      <Modal open={newOpen} onClose={() => setNewOpen(false)}
             title={t('records.newTitle')}>
        <div className="space-y-3">
          <div>
            <label className="field-label" htmlFor="new-section">
              {t('records.newSection')}
            </label>
            <select id="new-section" className="input-field"
                    value={newSection}
                    onChange={e => setNewSection(
                      e.target.value as Section)}>
              <option value="families">{t('records.families')}</option>
              <option value="criteria">{t('records.criteria')}</option>
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="new-id">
              {t('records.newId')}
            </label>
            <input id="new-id" className="input-field font-mono"
                   value={newId}
                   onChange={e => setNewId(e.target.value.trim())} />
            <p className="mt-1 text-xs text-cf-text-muted">
              {t('records.newIdHint')}
            </p>
          </div>
          <div>
            <label className="field-label" htmlFor="new-title">
              {t('records.newRecordTitle')}
            </label>
            <input id="new-title" className="input-field" value={newTitle}
                   onChange={e => setNewTitle(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setNewOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={!idOk || newTitle.trim().length < 3}
              onClick={() => {
                setNewOpen(false)
                navigate(`/records/${newSection}/${newId}?new=1`
                  + `&title=${encodeURIComponent(newTitle.trim())}`)
              }}
            >
              {t('records.newCreate')}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

function StandCell({ row }: { row: RecordRow }) {
  // A row of the list carries the NUMBER of stand tasks (contract,
  // section 4): the results come from the binding files of the products
  // and live in the record card and on the executors screen; the
  // tooltip says so plainly.
  if (!row.stand_tasks) {
    return <span className="text-cf-text-muted">{t('records.standEmpty')}</span>
  }
  return <span title={t('records.standTasksTip')}>{row.stand_tasks}</span>
}
