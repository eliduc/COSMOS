import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams }
  from 'react-router-dom'
import { ArrowLeft, Diff, Inbox, Save, Trash2, Undo2 } from 'lucide-react'
import Button from '../components/common/Button'
import ConfirmDialog from '../components/common/ConfirmDialog'
import Modal from '../components/common/Modal'
import StatusBadge from '../components/common/StatusBadge'
import notify from '../components/common/StyledToast'
import RecordCard from '../components/catalog/RecordCard'
import ChangesTable from '../components/catalog/ChangesTable'
import ProblemList from '../components/catalog/ProblemList'
import {
  blankRecord, deleteDraft, getRecord, listRecords, putRecordDraft, recStr,
  rightHint, rightOf, ServiceError, STATUS_PRESENT, STATUS_REMOVED,
  type CatalogRecord, type Change, type Problem, type RecordDetail,
} from '../api'
import { dictList, useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'

// Card of one record: the draft of the service is edited here and saved
// by an explicit button. Saving writes a draft file OUTSIDE the catalog
// package - the catalog and its fingerprint do not move until the
// "Assemble the draft" button on the versions screen.

export default function RecordPage() {
  const { section = 'families', id = '' } = useParams()
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const reloadSession = useSessionStore(s => s.reload)
  const dicts = useSessionStore(s => s.dicts)
  const loadDicts = useSessionStore(s => s.loadDicts)
  useEffect(() => { loadDicts() }, [loadDicts])

  const isNew = params.get('new') === '1'
  const version = params.get('version') ?? undefined
  // A link from a product carries the fingerprint it is pinned to: the
  // service answers with honest words when a different version is shown.
  const pinned = params.get('fingerprint') ?? undefined

  const [detail, setDetail] = useState<RecordDetail | null>(null)
  const [draft, setDraft] = useState<CatalogRecord | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [problems, setProblems] = useState<Problem[]>([])
  const [changes, setChanges] = useState<Change[]>([])
  const [changesOpen, setChangesOpen] = useState(false)
  const [removeOpen, setRemoveOpen] = useState(false)
  const [families, setFamilies] = useState<string[]>([])

  const classes = useMemo(() => dictList(dicts, 'classes'), [dicts])
  const statuses = useMemo(() => dictList(dicts, 'record_statuses'), [dicts])

  const load = useCallback(() => {
    if (isNew) {
      setDetail(null)
      setDraft(blankRecord(section, id, params.get('title') ?? '',
                           { classes, statuses }))
      setBusy(false)
      return
    }
    setBusy(true)
    getRecord(section, id, version, pinned)
      .then(got => {
        setDetail(got)
        setDraft((got.draft?.record ?? got.record) as CatalogRecord)
        setNote(got.draft?.note ?? '')
        setProblems(got.problems ?? [])
        setChanges(got.changes ?? [])
        setFailed(null)
      })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
      .finally(() => setBusy(false))
  }, [section, id, version, pinned, isNew, params, classes, statuses])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    listRecords({ section: 'families', status: STATUS_PRESENT })
      .then(got => setFamilies((got.items ?? [])
        .map(row => row.id).filter(one => one !== id)))
      .catch(() => setFamilies([]))
  }, [id])

  // Anchors of the address (#params, #executors, #stand, #history):
  // inside a hash router the browser does not jump on its own.
  useEffect(() => {
    if (!location.hash || busy) return
    const target = document.getElementById(location.hash.slice(1))
    if (target) target.scrollIntoView({ block: 'start' })
  }, [location.hash, busy])

  async function save(recordToSave: CatalogRecord, words: string) {
    try {
      const got = await putRecordDraft(section, id, recordToSave, words)
      setProblems(got.problems ?? [])
      setChanges(got.changes ?? [])
      notify.success(tf('card.saved', { n: (got.changes ?? []).length }))
      if (got.derived?.length) {
        notify.info(tf('card.derived', { list: got.derived.join(', ') }))
      }
      await reloadSession()
      if (isNew) navigate(`/records/${section}/${id}`)
      else load()
    } catch (err) {
      if (err instanceof ServiceError) {
        setProblems(err.problems ?? [])
        notify.error(tf('common.refusal',
          { code: err.code, words: err.words }))
      } else {
        notify.error(String(err))
      }
    }
  }

  async function discard() {
    try {
      await deleteDraft(section, id)
      notify.success(t('card.discarded'))
      await reloadSession()
      if (isNew) navigate('/records')
      else load()
    } catch (err) {
      notify.error(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err))
    }
  }

  // Rights come from the service (session.rights): a button is
  // disabled by `allowed` and the tooltip carries its own words. A card
  // opened at a released version (`?version=`) is read only anyway.
  const draftRight = rightOf(session, 'draft-save')
  const statusRight = rightOf(session, 'draft-status')
  const discardRight = rightOf(session, 'draft-discard')
  const submitRight = rightOf(session, 'request-submit')
  const mayEdit = draftRight.allowed && !version
  const shown = detail?.shown
  const draftFile = detail?.draft

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate(`/records?section=${section}`)}>
          {t('records.title')}
        </Button>
        <h1 className="font-mono text-base font-semibold">{id}</h1>
        {draftFile && (
          <StatusBadge kind="warn"
                       title={tf('card.draftFrom', {
                         date: draftFile.saved_at, by: draftFile.by,
                         note: draftFile.note })}>
            {t('records.colDraft')}
          </StatusBadge>
        )}
        {detail?.request && (
          <Link to={`/requests/${detail.request.request_id}`}
                className="text-xs text-cf-primary hover:underline">
            {tf('card.requestOpen',
                { id: detail.request.request_id })}
          </Link>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm"
                  leadingIcon={<Diff className="h-4 w-4" />}
                  onClick={() => setChangesOpen(true)}>
            {t('common.changes')}
          </Button>
          <Button
            variant="ghost" size="sm"
            leadingIcon={<Inbox className="h-4 w-4" />}
            onClick={() => navigate(
              `/requests/new?section=${section}&record_id=${id}`)}
            disabled={!submitRight.allowed}
            title={rightHint(submitRight)}
          >
            {t('requests.new')}
          </Button>
          <Button
            variant="danger" size="sm"
            leadingIcon={<Trash2 className="h-4 w-4" />}
            disabled={!statusRight.allowed || !mayEdit || isNew
              || recStr(draft ?? {}, 'status') === STATUS_REMOVED}
            title={rightHint(statusRight)
              ?? (version ? t('card.readOnlyRecord') : undefined)}
            onClick={() => setRemoveOpen(true)}
          >
            {t('card.remove')}
          </Button>
          <Button
            variant="secondary" size="sm"
            leadingIcon={<Undo2 className="h-4 w-4" />}
            disabled={!discardRight.allowed || !mayEdit || !draftFile}
            title={rightHint(discardRight)
              ?? (version ? t('card.readOnlyRecord') : undefined)}
            onClick={discard}
          >
            {t('common.discardDraft')}
          </Button>
          <Button
            size="sm"
            leadingIcon={<Save className="h-4 w-4" />}
            disabled={!mayEdit || !draft || !note.trim()}
            title={rightHint(draftRight)
              ?? (version ? t('card.readOnlyRecord') : undefined)}
            onClick={() => draft && save(draft, note.trim())}
          >
            {t('common.saveDraft')}
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

        {!busy && draft && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 text-xs
                            text-cf-text-muted">
              {shown && (
                <span className="font-mono">
                  {tf('card.shown', {
                    version: shown.version,
                    fingerprint: shown.fingerprint.slice(0, 12) })}
                </span>
              )}
              {detail?.pinned_words && (
                <StatusBadge kind="warn">{detail.pinned_words}</StatusBadge>
              )}
              {/* Section 6.1: next to the line of divergence - a link
                  to the card of that version, but only when it opens
                  (the service says so with pinned_release_ok). */}
              {detail?.pinned_words && detail?.pinned_release_ok && version && (
                <Link to={`/versions/${version}`}
                      className="text-cf-primary hover:underline">
                  {tf('card.pinnedOpen', { version })}
                </Link>
              )}
              {detail?.in_catalog === false && (
                <StatusBadge kind="info">{t('card.newRecord')}</StatusBadge>
              )}
              {version && (
                <StatusBadge kind="neutral">
                  {t('card.readOnlyRecord')}
                </StatusBadge>
              )}
              <span>{t('card.assembleHint')}</span>
            </div>

            {mayEdit && (
              <div className="card">
                <label className="field-label" htmlFor="draft-note">
                  {t('common.changeNote')}
                </label>
                <input id="draft-note" className="input-field" value={note}
                       onChange={e => setNote(e.target.value)} />
                <p className="mt-1 text-xs text-cf-text-muted">
                  {t('common.changeNoteHint')}
                </p>
              </div>
            )}

            {problems.length > 0 && (
              <div className="card border-cf-error">
                <ProblemList problems={problems} />
              </div>
            )}

            <RecordCard
              section={section}
              record={draft}
              dicts={dicts}
              executors={detail?.executors ?? null}
              stand={detail?.stand ?? null}
              complementOptions={families}
              canSetStatus={statusRight.allowed && mayEdit}
              statusHint={rightHint(statusRight)}
              onChange={mayEdit ? setDraft : undefined}
            />
          </div>
        )}

        {!busy && !draft && !failed && (
          <p className="text-sm text-cf-text-muted">
            {tf('card.notFound', { section, id })}
          </p>
        )}
      </div>

      <Modal open={changesOpen} onClose={() => setChangesOpen(false)}
             title={t('common.changes')} wide>
        <ChangesTable changes={changes} />
      </Modal>

      <ConfirmDialog
        open={removeOpen}
        title={t('card.removeTitle')}
        message={t('card.removeWords')}
        confirmLabel={t('card.remove')}
        danger
        wordsLabel={t('common.changeNote')}
        wordsHint={t('common.changeNoteHint')}
        wordsRequired
        onCancel={() => setRemoveOpen(false)}
        onConfirm={words => {
          setRemoveOpen(false)
          if (draft) {
            const next = { ...draft, status: STATUS_REMOVED }
            setDraft(next)
            setNote(words)
            save(next, words)
          }
        }}
      />
    </div>
  )
}
