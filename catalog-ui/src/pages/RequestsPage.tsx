import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Inbox, Plus } from 'lucide-react'
import Button from '../components/common/Button'
import StatusBadge from '../components/common/StatusBadge'
import {
  listRequests, requestBadgeKind, rightHint, rightOf, ServiceError,
  REQUEST_EXECUTOR_CLAIMED,
  type CatalogRequest,
} from '../api'
import { dictList, useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'
import type { MessageKey } from '../i18n'

// Screen "Requests": everything asked of the catalog - by an analyst,
// by the owner, and (step 6) by the products themselves through the
// machine channel. The decision belongs to the owner alone, and a
// request becomes a record only through the assembly of a draft.
//
// Step 6 adds three columns of the pipeline: the key of the change
// request (computed from the number by the law of the reader, so the
// same CR opens in any copy of COSMOS), the candidate file written by
// the branch of that CR, and the checklist that leads from an accepted
// request to a released version.
const FILTERS: { key: string; label: MessageKey; dict: string }[] = [
  { key: 'state', label: 'requests.filterState', dict: 'request_states' },
  { key: 'submitted_by', label: 'requests.filterSubmitter',
    dict: 'request_submitters' },
  { key: 'source_kind', label: 'requests.filterSource',
    dict: 'request_source_kinds' },
]

/** Steps from an accepted request to a released version (contract 8.4). */
const BEFORE_RELEASE: MessageKey[] = [
  'requests.beforeCr', 'requests.beforeMerge', 'requests.beforeAdopt',
  'requests.beforeAssemble', 'requests.beforeChain', 'requests.beforeSeed',
  'requests.beforeRelease',
]

export default function RequestsPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  useEffect(() => { loadDicts() }, [loadDicts])

  const [rows, setRows] = useState<CatalogRequest[]>([])
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)

  const filter = useMemo(() => ({
    state: params.get('state') ?? '',
    submitted_by: params.get('submitted_by') ?? '',
    source_kind: params.get('source_kind') ?? '',
    section: params.get('section') ?? '',
    record_id: params.get('record_id') ?? '',
    cr_key: params.get('cr_key') ?? '',
    ref: params.get('ref') ?? '',
    q: params.get('q') ?? '',
  }), [params])

  const reload = useCallback(() => {
    setBusy(true)
    listRequests(filter)
      .then(got => { setRows(got.items ?? []); setFailed(null) })
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

  const submitRight = rightOf(session, 'request-submit')
  const inWork = rows.filter(r => r.state === REQUEST_EXECUTOR_CLAIMED).length
  const withCandidate = rows.filter(r => !!r.candidate).length

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Inbox className="h-5 w-5 text-cf-primary" />
        <h1 className="text-base font-semibold">{t('requests.title')}</h1>
        {session?.machine_channel === false && (
          <span className="text-xs text-cf-text-muted"
                title={t('requests.channelOffHint')}>
            {t('requests.channelOff')}
          </span>
        )}
        <Button
          className="ml-auto" size="sm"
          leadingIcon={<Plus className="h-4 w-4" />}
          disabled={!submitRight.allowed}
          title={rightHint(submitRight)}
          onClick={() => navigate('/requests/new')}
        >
          {t('requests.new')}
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-2">
        {FILTERS.map(one => (
          <select
            key={one.key}
            className="input-field max-w-[12rem]"
            value={params.get(one.key) ?? ''}
            title={t(one.label)}
            onChange={e => setFilter(one.key, e.target.value)}
          >
            <option value="">
              {`${t(one.label)}: ${t('common.all')}`}
            </option>
            {dictList(dicts, one.dict).map(value => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        ))}
        <select
          className="input-field max-w-[10rem]"
          value={params.get('section') ?? ''}
          title={t('requests.filterSection')}
          onChange={e => setFilter('section', e.target.value)}
        >
          <option value="">
            {`${t('requests.filterSection')}: ${t('common.all')}`}
          </option>
          <option value="families">{t('records.families')}</option>
          <option value="criteria">{t('records.criteria')}</option>
        </select>
        <input
          className="input-field max-w-[12rem]"
          placeholder={t('requests.filterRecord')}
          value={params.get('record_id') ?? ''}
          onChange={e => setFilter('record_id', e.target.value)}
        />
        <input
          className="input-field max-w-[10rem] font-mono"
          placeholder={t('requests.filterCrKey')}
          value={params.get('cr_key') ?? ''}
          onChange={e => setFilter('cr_key', e.target.value.trim())}
        />
        <input
          className="input-field max-w-[10rem] font-mono"
          placeholder={t('requests.filterRef')}
          title={t('requests.filterRefHint')}
          value={params.get('ref') ?? ''}
          onChange={e => setFilter('ref', e.target.value.trim())}
        />
        <input
          className="input-field max-w-[16rem]"
          placeholder={t('requests.searchPlaceholder')}
          value={params.get('q') ?? ''}
          onChange={e => setFilter('q', e.target.value)}
        />
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
        {!busy && rows.length === 0 && !failed && (
          <p className="text-sm text-cf-text-muted">{t('common.empty')}</p>
        )}
        {rows.length > 0 && (
          <>
          <div className="overflow-x-auto rounded-xl border border-cf-border
                          bg-cf-panel">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="table-head">{t('requests.colId')}</th>
                  <th className="table-head">{t('requests.colState')}</th>
                  <th className="table-head">{t('requests.colRecord')}</th>
                  <th className="table-head">
                    {t('requests.colSubmittedBy')}
                  </th>
                  <th className="table-head">{t('requests.colSource')}</th>
                  <th className="table-head">
                    {t('requests.colSubmittedAt')}
                  </th>
                  <th className="table-head">{t('requests.colDecision')}</th>
                  <th className="table-head" title={t('requests.crStatusHint')}>
                    {t('requests.colCrKey')}
                  </th>
                  <th className="table-head">{t('requests.colCandidate')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(row => (
                  <tr key={row.request_id} className="table-row">
                    <td className="table-cell font-mono text-xs">
                      <Link to={`/requests/${row.request_id}`}
                            className="text-cf-primary hover:underline">
                        {row.request_id}
                      </Link>
                    </td>
                    <td className="table-cell">
                      <StatusBadge kind={requestBadgeKind(row.state)}>
                        {row.state}
                      </StatusBadge>
                    </td>
                    <td className="table-cell font-mono text-xs">
                      {row.record_exists === false
                        ? <span className="text-cf-text-muted">
                            {row.section}/{row.record_id}
                            {' '}({t('requests.newRecord')})
                          </span>
                        : <Link to={`/records/${row.section}/${row.record_id}`}
                                className="text-cf-primary hover:underline">
                            {row.section}/{row.record_id}
                          </Link>}
                    </td>
                    <td className="table-cell">{row.submitted_by}</td>
                    <td className="table-cell text-xs">
                      {[row.source?.kind, row.source?.document_class,
                        row.source?.run_class, row.source?.ref]
                        .filter(Boolean).join(' · ')}
                    </td>
                    <td className="table-cell whitespace-nowrap font-mono
                                   text-xs">
                      {row.submitted_at}
                    </td>
                    <td className="table-cell text-xs"
                        title={row.decision?.words ?? ''}>
                      {[row.decision?.by, row.decision?.at]
                        .filter(Boolean).join(' · ')}
                    </td>
                    <td className="table-cell font-mono text-xs">
                      {row.cr_key
                        ? row.cr_key
                        : <span className="text-cf-text-muted"
                                title={t('requests.crKeyExpectedHint')}>
                            {row.cr_key_expected ?? ''}
                          </span>}
                    </td>
                    <td className="table-cell text-xs">
                      {row.candidate
                        ? <StatusBadge kind="warn"
                                       title={row.candidate}>
                            {t('requests.candidateHere')}
                          </StatusBadge>
                        : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card mt-4">
            <h2 className="mb-2 text-sm font-semibold">
              {t('requests.beforeReleaseTitle')}
            </h2>
            <p className="mb-2 text-xs text-cf-text-muted">
              {tf('requests.beforeReleaseCounts',
                  { work: inWork, candidates: withCandidate })}
            </p>
            <ol className="list-decimal space-y-1 pl-5 text-xs
                           text-cf-text-muted">
              {BEFORE_RELEASE.map(key => (
                <li key={key}>{t(key)}</li>
              ))}
            </ol>
          </div>
          </>
        )}
      </div>
    </div>
  )
}
