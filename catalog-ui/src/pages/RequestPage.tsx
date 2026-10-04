import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams }
  from 'react-router-dom'
import { ArrowLeft, Check, KeyRound, PackagePlus, X } from 'lucide-react'
import Button from '../components/common/Button'
import ConfirmDialog from '../components/common/ConfirmDialog'
import Modal from '../components/common/Modal'
import StatusBadge from '../components/common/StatusBadge'
import notify from '../components/common/StyledToast'
import RecordCard from '../components/catalog/RecordCard'
import ProblemList from '../components/catalog/ProblemList'
import {
  adoptRequest, blankRecord, createRequest, decideRequest, dryRunRequest,
  getRequest, putCrKey, requestBadgeKind, rightHint, rightOf, ServiceError,
  REQUEST_ACCEPTED, REQUEST_EXECUTOR_CLAIMED, REQUEST_REJECTED,
  REQUEST_SUBMITTED, SOURCE_MANUAL,
  type CatalogRecord, type RequestDetail, type RequestPreview,
  type Section,
} from '../api'
import { dictList, useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'

// Card of a request and the form for a new one.
//
// The decision belongs to the owner and is not recorded without words.
// "Move to records" writes ONLY a draft of the record: the present
// state of a request and the record itself appear together, in one write
// of the assembly - otherwise the catalog would carry a request pointing
// at a record that does not exist (rule R02 of the reader).
//
// Step 6. The number of a request is not a counter: the service computes
// it from the content by the law of the reader, so the same gap seen by
// CDPM and by COSMOS on different machines gets ONE number. The form
// therefore asks the service before submitting (dry run): the number,
// the key of the change request, whether the record already exists (only
// a request for an executor kind is lawful then) and the refusal words -
// all of it without writing a byte.
const CR_KEY = /^cr[0-9a-f]{8}$/
const ID_PATTERN = /^[a-z][a-z0-9_]{2,40}$/
/** Material key of a request brought by a link of a product: 64 hex. */
const KEY_PATTERN = /^[0-9a-f]{64}$/

export default function RequestPage({ create }: { create?: boolean }) {
  return create ? <NewRequest /> : <ExistingRequest />
}

function NewRequest() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  const reloadSession = useSessionStore(s => s.reload)
  useEffect(() => { loadDicts() }, [loadDicts])

  const classes = useMemo(() => dictList(dicts, 'classes'), [dicts])
  const kinds = useMemo(() => dictList(dicts, 'executor_kinds'), [dicts])
  const sourceKinds = useMemo(
    () => dictList(dicts, 'request_source_kinds'), [dicts])
  // Document classes are ONE list: the dictionary of the catalog when it
  // exists, otherwise the list of the reader. The service hands it over
  // in the session - the screen keeps no copy of the seven values, for a
  // copy would drift away from the reader and from the form of CDPM.
  const documentClasses = useMemo(
    () => session?.document_classes ?? [], [session])

  const [section, setSection] = useState<Section>(
    params.get('section') === 'criteria' ? 'criteria' : 'families')
  const [recordId, setRecordId] = useState(params.get('record_id') ?? '')
  // The material key is brought by a link of a product (the press of
  // COSMOS, the panel of CDPM): with it the service numbers the request
  // by the same law as the product, so the numbers meet without any
  // exchange between the machines.
  const materialKey = (params.get('key') ?? '').trim()
  const [preview, setPreview] = useState<RequestPreview | null>(null)
  const [previewFailed, setPreviewFailed] = useState<string | null>(null)
  const [sourceKind, setSourceKind] = useState(
    params.get('source') ?? SOURCE_MANUAL)
  const [documentClass, setDocumentClass] = useState(
    params.get('document_class') ?? '')
  const [runClass, setRunClass] = useState(params.get('run_class') ?? '')
  const [ref, setRef] = useState(params.get('ref') ?? '')
  const [wants, setWants] = useState<'record' | 'executors'>('record')
  const [draft, setDraft] = useState<CatalogRecord>(
    () => blankRecord('families', '', '',
                      { classes: [], statuses: [], forRequest: true }))
  const [wantedKinds, setWantedKinds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  // The identity of the record follows the fields of the form: the
  // draft of a request carries the same id and section as the request.
  useEffect(() => {
    setDraft(prev => ({ ...prev, id: recordId }))
  }, [recordId])

  // A change of the section changes the whole shape of the draft
  // (a family or a criterion), so the draft is rebuilt from scratch.
  useEffect(() => {
    setDraft(prev => blankRecord(section, String(prev.id ?? ''), '',
                                 { classes, statuses: [],
                                   forRequest: true }))
  }, [section, classes])

  // The body of the submission and the body of the dry run are ONE
  // computation: what is asked of the service is exactly what will be
  // submitted afterwards.
  const body = useMemo(() => ({
    section,
    record_id: recordId,
    source: {
      kind: sourceKind,
      document_class: documentClass.trim() || null,
      run_class: runClass.trim() || null,
      ref: ref.trim() || null,
    },
    draft: wants === 'record' ? draft : null,
    executor_kinds_requested: wants === 'executors' ? wantedKinds : null,
    key: KEY_PATTERN.test(materialKey) ? materialKey : null,
  }), [section, recordId, sourceKind, documentClass, runClass, ref, wants,
       draft, wantedKinds, materialKey])

  async function look() {
    try {
      const got = await dryRunRequest(body)
      setPreview(got)
      setPreviewFailed(null)
      // For an existing record only a request for an executor kind is
      // lawful (R02): the form switches itself instead of letting the
      // analyst promise the impossible.
      if (got.record_exists && wants === 'record') setWants('executors')
    } catch (err) {
      setPreview(null)
      setPreviewFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err))
    }
  }

  async function submit() {
    setBusy(true)
    try {
      const got = await createRequest(body)
      notify.success(tf('requests.submitted', { id: got.request_id }))
      await reloadSession()
      navigate(`/requests/${got.request_id}`)
    } catch (err) {
      if (err instanceof ServiceError) {
        notify.error(tf('common.refusal',
          { code: err.code, words: err.words }))
        const existing = err.extra?.existing
        if (typeof existing === 'string') {
          navigate(`/requests/${existing}`)
        }
      } else {
        notify.error(String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  const submitRight = rightOf(session, 'request-submit')
  const ready = ID_PATTERN.test(recordId) && !!sourceKind
    && (wants === 'record' || wantedKinds.length > 0)

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate('/requests')}>
          {t('requests.title')}
        </Button>
        <h1 className="text-base font-semibold">{t('requests.newTitle')}</h1>
        <Button
          className="ml-auto" variant="secondary" size="sm"
          disabled={!ready || busy}
          title={t('requests.dryRunHint')}
          onClick={look}
        >
          {t('requests.dryRun')}
        </Button>
        <Button
          size="sm"
          disabled={!submitRight.allowed || !ready || busy}
          title={rightHint(submitRight)}
          onClick={submit}
        >
          {t('requests.submit')}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-4">
          <div className="card grid gap-3 sm:grid-cols-2">
            <div>
              <label className="field-label" htmlFor="req-section">
                {t('requests.section')}
              </label>
              <select id="req-section" className="input-field"
                      value={section}
                      onChange={e => setSection(e.target.value as Section)}>
                <option value="families">{t('records.families')}</option>
                <option value="criteria">{t('records.criteria')}</option>
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="req-record">
                {t('requests.recordId')}
              </label>
              <input id="req-record" className="input-field font-mono"
                     value={recordId}
                     onChange={e => setRecordId(e.target.value.trim())} />
            </div>
            <div>
              <label className="field-label" htmlFor="req-source">
                {t('requests.sourceKind')}
              </label>
              <select id="req-source" className="input-field"
                      value={sourceKind}
                      onChange={e => setSourceKind(e.target.value)}>
                {sourceKinds.map(one => (
                  <option key={one} value={one}>{one}</option>
                ))}
                {sourceKind && !sourceKinds.includes(sourceKind) && (
                  <option value={sourceKind}>{sourceKind}</option>
                )}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="req-doc">
                {t('requests.documentClass')}
              </label>
              <select id="req-doc" className="input-field"
                      value={documentClass}
                      onChange={e => setDocumentClass(e.target.value)}>
                <option value="">{t('common.notSet')}</option>
                {documentClasses.map(one => (
                  <option key={one} value={one}>{one}</option>
                ))}
                {documentClass && !documentClasses.includes(documentClass) && (
                  <option value={documentClass}>{documentClass}</option>
                )}
              </select>
            </div>
            <div>
              <label className="field-label" htmlFor="req-run">
                {t('requests.runClass')}
              </label>
              <input id="req-run" className="input-field" value={runClass}
                     onChange={e => setRunClass(e.target.value)} />
            </div>
            <div>
              <label className="field-label" htmlFor="req-ref">
                {t('requests.ref')}
              </label>
              <input id="req-ref" className="input-field" value={ref}
                     onChange={e => setRef(e.target.value)} />
              <p className="mt-1 text-xs text-cf-text-muted">
                {t('requests.refHint')}
              </p>
            </div>
          </div>

          <div className="card space-y-3">
            <div>
              <label className="field-label" htmlFor="req-wants">
                {t('requests.kindOfRequest')}
              </label>
              <select id="req-wants" className="input-field" value={wants}
                      onChange={e => setWants(
                        e.target.value === 'executors'
                          ? 'executors' : 'record')}>
                <option value="record">{t('requests.kindRecord')}</option>
                <option value="executors">
                  {t('requests.kindExecutors')}
                </option>
              </select>
            </div>
            {wants === 'executors' && (
              <div>
                <span className="field-label">
                  {t('requests.kindsRequested')}
                </span>
              <div className="flex flex-wrap gap-1">
                {kinds.map(kind => (
                  <button
                    key={kind}
                    onClick={() => setWantedKinds(
                      wantedKinds.includes(kind)
                        ? wantedKinds.filter(one => one !== kind)
                        : [...wantedKinds, kind])}
                    className={wantedKinds.includes(kind)
                      ? 'status-badge bg-cf-primary/15 text-cf-primary'
                      : 'status-badge bg-cf-hover text-cf-text-muted'}
                  >
                    {kind}
                  </button>
                ))}
              </div>
              </div>
            )}
          </div>

          {previewFailed && (
            <div className="card border-cf-error">
              <p className="text-sm text-cf-error">{previewFailed}</p>
            </div>
          )}
          {preview && (
            <div className="card space-y-2 text-sm">
              <p>
                <span className="text-cf-text-muted">
                  {t('requests.previewNumber')}:{' '}
                </span>
                <span className="font-mono">{preview.request_id}</span>
                {' · '}
                <span className="text-cf-text-muted">
                  {t('requests.colCrKey')}:{' '}
                </span>
                <span className="font-mono">{preview.cr_key_expected}</span>
              </p>
              {preview.record_exists && (
                <p className="text-cf-warning">
                  {t('requests.previewRecordExists')}
                </p>
              )}
              {preview.known && (
                <p className="text-cf-warning">
                  {tf('requests.previewKnown',
                      { id: preview.known.request_id,
                        state: preview.known.state })}
                </p>
              )}
              {preview.open_request && !preview.known && (
                <p className="text-cf-warning">
                  {tf('requests.previewOpen',
                      { id: preview.open_request.request_id,
                        state: preview.open_request.state })}
                </p>
              )}
              {preview.problems.length > 0
                ? <ProblemList problems={preview.problems} />
                : <p className="text-cf-success">
                    {t('requests.previewClean')}
                  </p>}
            </div>
          )}
          {wants === 'record' && (
            <RecordCard
              section={section}
              record={draft}
              dicts={dicts}
              hideRecordOnly
              onChange={setDraft}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function ExistingRequest() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const reloadSession = useSessionStore(s => s.reload)
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  useEffect(() => { loadDicts() }, [loadDicts])

  const [got, setGot] = useState<RequestDetail | null>(null)
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [decision, setDecision] = useState<string | null>(null)
  const [adoptOpen, setAdoptOpen] = useState(false)
  const [adoptWords, setAdoptWords] = useState<string | null>(null)
  const [crOpen, setCrOpen] = useState(false)
  const [crKey, setCrKey] = useState('')
  // The switch "take the record and the stand tasks from the candidate".
  // The schema forbids stand_tasks in the draft of a request, and there
  // is no other way to bring the tasks into a new record - only the file
  // written by the pipeline.
  const [fromCandidate, setFromCandidate] = useState(true)

  const load = useCallback(() => {
    setBusy(true)
    getRequest(id)
      .then(one => {
        setGot(one)
        setFailed(null)
        // The key of the change request is not typed in "as it turned
        // out": it is computed by the law of the reader from the number
        // of the request, and the service accepts no other.
        setCrKey(one.cr_key ?? one.cr_key_expected ?? '')
      })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
      .finally(() => setBusy(false))
  }, [id])

  useEffect(() => { load() }, [load])

  function refuse(err: unknown) {
    notify.error(err instanceof ServiceError
      ? tf('common.refusal', { code: err.code, words: err.words })
      : String(err))
  }

  async function decide(state: string, words: string) {
    try {
      const next = await decideRequest(id, state, words)
      notify.success(tf('requests.decided',
        { id, state: next.state ?? state }))
      if (state === REQUEST_ACCEPTED) {
        notify.info(t('requests.acceptedWords'))
      }
      await reloadSession()
      load()
    } catch (err) {
      refuse(err)
    }
  }

  async function adopt(confirm: string | null) {
    try {
      const next = await adoptRequest(id, confirm,
                                      fromCandidate && !!got?.candidate)
      notify.success(next.next || t('requests.adoptDone'))
      await reloadSession()
      load()
    } catch (err) {
      if (err instanceof ServiceError && err.code === 'executor-not-present') {
        setAdoptWords(err.words)
        setAdoptOpen(true)
        return
      }
      refuse(err)
    }
  }

  async function claim() {
    try {
      await putCrKey(id, crKey.trim())
      setCrOpen(false)
      notify.success(t('requests.acceptedWords'))
      load()
    } catch (err) {
      refuse(err)
    }
  }

  // Rights come from the service: every button has its own operation
  // of the contract, and the tooltip carries its refusal words.
  const decideRight = rightOf(session, 'request-decide')
  const crKeyRight = rightOf(session, 'request-cr-key')
  const adoptRight = rightOf(session, 'request-adopt')
  const state = got?.state ?? ''
  const decidable = state === REQUEST_SUBMITTED
  // Decision 0.15: the state machine has no dead ends. The owner may
  // reject an accepted request and revise a rejected one with words: the
  // number of a gap comes from its content, and no other number for that
  // same gap will ever exist.
  const rejectable = decidable || state === REQUEST_ACCEPTED
    || state === REQUEST_EXECUTOR_CLAIMED
  const revisable = state === REQUEST_REJECTED
  const adoptable = state === REQUEST_ACCEPTED
    || state === REQUEST_EXECUTOR_CLAIMED

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate('/requests')}>
          {t('requests.title')}
        </Button>
        <h1 className="font-mono text-base font-semibold">{id}</h1>
        {got && (
          <StatusBadge kind={requestBadgeKind(got.state)}>
            {got.state}
          </StatusBadge>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button
            variant="secondary" size="sm"
            leadingIcon={<X className="h-4 w-4" />}
            disabled={!decideRight.allowed || !rejectable}
            title={rightHint(decideRight) ?? t('requests.rejectHint')}
            onClick={() => setDecision(REQUEST_REJECTED)}
          >
            {t('requests.reject')}
          </Button>
          <Button
            size="sm"
            leadingIcon={<Check className="h-4 w-4" />}
            disabled={!decideRight.allowed || !(decidable || revisable)}
            title={rightHint(decideRight)
              ?? (revisable ? t('requests.reviseHint') : undefined)}
            onClick={() => setDecision(REQUEST_ACCEPTED)}
          >
            {revisable ? t('requests.revise') : t('requests.accept')}
          </Button>
          <Button
            variant="secondary" size="sm"
            leadingIcon={<KeyRound className="h-4 w-4" />}
            disabled={!crKeyRight.allowed || state !== REQUEST_ACCEPTED}
            title={rightHint(crKeyRight)}
            onClick={() => setCrOpen(true)}
          >
            {t('requests.crKey')}
          </Button>
          <Button
            size="sm"
            leadingIcon={<PackagePlus className="h-4 w-4" />}
            disabled={!adoptRight.allowed || !adoptable}
            title={rightHint(adoptRight)}
            onClick={() => adopt(null)}
          >
            {t('requests.adopt')}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {failed && (
          <div className="card mb-4 border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}
        {busy && (
          <p className="text-sm text-cf-text-muted">{t('common.loading')}</p>
        )}
        {!busy && !got && !failed && (
          <p className="text-sm text-cf-text-muted">
            {tf('requests.notFound', { id })}
          </p>
        )}

        {got && (
          <div className="space-y-4">
            <div className="card grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <span className="text-cf-text-muted">
                  {t('requests.colRecord')}:{' '}
                </span>
                {got.record_exists === false
                  ? <span className="font-mono">
                      {got.section}/{got.record_id}
                      {' '}({t('requests.newRecord')})
                    </span>
                  : <Link to={`/records/${got.section}/${got.record_id}`}
                          className="font-mono text-cf-primary
                                     hover:underline">
                      {got.section}/{got.record_id}
                    </Link>}
              </div>
              <div>
                <span className="text-cf-text-muted">
                  {t('requests.colSubmittedBy')}:{' '}
                </span>
                {got.submitted_by}
                {' · '}
                <span className="font-mono text-xs">{got.submitted_at}</span>
              </div>
              <div className="sm:col-span-2">
                <span className="text-cf-text-muted">
                  {t('requests.colSource')}:{' '}
                </span>
                {[got.source?.kind, got.source?.document_class,
                  got.source?.run_class, got.source?.ref]
                  .filter(Boolean).join(' · ')}
              </div>
              {got.decision?.by && (
                <div className="sm:col-span-2">
                  <span className="text-cf-text-muted">
                    {t('requests.colDecision')}:{' '}
                  </span>
                  {got.decision.by} · {got.decision.at} — {got.decision.words}
                </div>
              )}
              <div title={t('requests.crStatusHint')}>
                <span className="text-cf-text-muted">
                  {t('requests.colCrKey')}:{' '}
                </span>
                <span className="font-mono">
                  {got.cr_key ?? got.cr_key_expected ?? ''}
                </span>
                {!got.cr_key && got.cr_key_expected && (
                  <span className="ml-2 text-xs text-cf-text-muted">
                    {t('requests.crKeyExpectedHint')}
                  </span>
                )}
              </div>
              {got.has_draft && (
                <div className="sm:col-span-2">
                  <StatusBadge kind="warn">
                    {t('requests.hasDraft')}
                  </StatusBadge>
                </div>
              )}
            </div>

            {got.executor_kinds_requested?.length ? (
              <div className="card">
                <p className="text-sm">
                  {tf('requests.forKinds',
                      { kinds: got.executor_kinds_requested.join(', ') })}
                </p>
              </div>
            ) : null}

            {got.candidate && (
              <div className="card space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge kind="warn">
                    {t('requests.candidateHere')}
                  </StatusBadge>
                  <span className="font-mono text-xs">{got.candidate}</span>
                </div>
                <p className="text-xs text-cf-text-muted">
                  {tf('requests.candidateGates', {
                    branch: got.candidate_evidence?.branch ?? '—',
                    passed: got.candidate_evidence?.gates?.stand?.passed ?? 0,
                    failed: got.candidate_evidence?.gates?.stand?.failed ?? 0,
                    skipped: got.candidate_evidence?.gates?.stand?.skipped ?? 0,
                    tests: got.candidate_evidence?.gates?.pytest ?? '—',
                  })}
                </p>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={fromCandidate}
                         onChange={e => setFromCandidate(e.target.checked)} />
                  {t('requests.useCandidate')}
                </label>
                <p className="text-xs text-cf-text-muted">
                  {t('requests.useCandidateHint')}
                </p>
              </div>
            )}

            {got.draft_problems?.length > 0 && (
              <div className="card border-cf-error">
                <h3 className="mb-2 text-sm font-semibold">
                  {t('requests.draftProblems')}
                </h3>
                <ProblemList problems={got.draft_problems} />
              </div>
            )}

            {got.draft && (
              <>
                <h2 className="text-sm font-semibold">
                  {t('requests.draftRecord')}
                </h2>
                <RecordCard
                  section={got.section}
                  record={got.draft}
                  dicts={dicts}
                  hideRecordOnly
                />
              </>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!decision}
        title={tf('requests.decisionTitle', { id })}
        message={t('requests.decisionWordsHint')}
        confirmLabel={decision === REQUEST_ACCEPTED
          ? t('requests.accept') : t('requests.reject')}
        danger={decision === REQUEST_REJECTED}
        wordsLabel={t('requests.decisionWords')}
        wordsHint={t('requests.decisionWordsHint')}
        wordsRequired
        onCancel={() => setDecision(null)}
        onConfirm={words => {
          const which = decision
          setDecision(null)
          if (which) decide(which, words)
        }}
      />

      <ConfirmDialog
        open={adoptOpen}
        title={tf('requests.adoptTitle', { id })}
        message={adoptWords ?? t('requests.adoptHint')}
        confirmLabel={t('requests.adopt')}
        wordsLabel={t('requests.adoptWords')}
        wordsHint={t('requests.adoptHint')}
        wordsRequired
        onCancel={() => setAdoptOpen(false)}
        onConfirm={words => {
          setAdoptOpen(false)
          adopt(words)
        }}
      />

      <Modal open={crOpen} onClose={() => setCrOpen(false)}
             title={t('requests.crKeyTitle')}>
        <div className="space-y-3">
          <div>
            <label className="field-label" htmlFor="cr-key">
              {t('requests.crKeyField')}
            </label>
            <input id="cr-key" className="input-field font-mono"
                   value={crKey}
                   onChange={e => setCrKey(e.target.value.trim())} />
            <p className="mt-1 text-xs text-cf-text-muted">
              {t('requests.crKeyHint')}
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setCrOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={!CR_KEY.test(crKey)} onClick={claim}>
              {t('common.confirm')}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
