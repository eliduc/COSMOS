import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams }
  from 'react-router-dom'
import {
  ArrowLeft, Copy, Download, PackageCheck, Tags, Trash2, Upload,
} from 'lucide-react'
import Button from '../components/common/Button'
import ConfirmDialog from '../components/common/ConfirmDialog'
import Modal from '../components/common/Modal'
import StatusBadge from '../components/common/StatusBadge'
import notify from '../components/common/StyledToast'
import ChangesTable from '../components/catalog/ChangesTable'
import {
  assemble, compareVersions, deleteAllDrafts, EXPORT_URL,
  getJournal, getReleaseChecks, getVersion, getVersions, importCatalog,
  listDrafts, release, rightHint, rightOf, ServiceError, validateCatalog,
  type Change, type DraftRow, type JournalRow, type ReleaseChecks,
  type ValidateReport, type VersionDetail, type VersionsReport,
} from '../api'
import { useSessionStore } from '../stores/sessionStore'
import { levelWords, t, tf } from '../i18n'

// Screen "Versions and releases": the drafts of the service become a
// catalog by one button ("Assemble the draft"), and a release is made
// by the owner alone. Nothing here happens quietly: the assembly answers
// with the chain of commands that has to follow it, and the release
// answers with the checklist of what was written.
export default function VersionsPage({ compare }: { compare?: boolean }) {
  const { version } = useParams()
  if (compare) return <CompareView />
  if (version) return <OneVersion version={version} />
  return <VersionsMain />
}

function OneVersion({ version }: { version: string }) {
  const navigate = useNavigate()
  const [got, setGot] = useState<VersionDetail | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    getVersion(version)
      .then(setGot)
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
  }, [version])

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-cf-border
                      bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate('/versions')}>
          {t('versions.title')}
        </Button>
        <h1 className="font-mono text-base font-semibold">
          {got?.is_current ? tf('versions.detailCurrent', { version })
            : tf('versions.detail', { version })}
        </h1>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {failed && (
          <div className="card border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}
        {!got && !failed && (
          <p className="text-sm text-cf-text-muted">
            {tf('versions.notFound', { version })}
          </p>
        )}
        {got && (
          <div className="space-y-4">
            {/* The header of a release comes UNDER the key `header`
                (contract, section 4); the top level carries only the
                number - so the fields are read from there. */}
            <div className="card space-y-1 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                {got.is_current && (
                  <StatusBadge kind="info">
                    {t('versions.currentChip')}
                  </StatusBadge>
                )}
                {got.header?.is_draft !== undefined && (
                  <StatusBadge kind={got.header.is_draft ? 'warn' : 'ok'}>
                    {got.header.is_draft ? t('session.draft')
                      : t('session.release')}
                  </StatusBadge>
                )}
                <StatusBadge kind={got.loadable ? 'ok' : 'error'}>
                  {got.loadable ? t('versions.loadable')
                    : t('versions.notLoadable')}
                </StatusBadge>
              </div>
              <p className="break-all font-mono text-xs">
                {got.header?.fingerprint ?? got.entry?.fingerprint ?? ''}
              </p>
              <p>
                {got.header?.released_at ?? got.entry?.released_at ?? ''}
                {' · '}
                {got.header?.released_by ?? got.entry?.released_by ?? ''}
              </p>
              <p className="text-cf-text-muted">{got.header?.note ?? ''}</p>
              {got.entry?.code && (
                <p className="font-mono text-xs text-cf-text-muted">
                  {t('versions.colCode')}: {got.entry.code.slice(0, 7)}
                </p>
              )}
              {!got.entry && (
                <p className="text-xs text-cf-text-muted">
                  {t('versions.noEntry')}
                </p>
              )}
            </div>
            <div className="card">
              <ChangesTable changes={got.changes ?? []} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function CompareView() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [report, setReport] = useState<VersionsReport | null>(null)
  const [changes, setChanges] = useState<Change[]>([])
  const [level, setLevel] = useState('')
  const [failed, setFailed] = useState<string | null>(null)

  const a = params.get('a') ?? 'current'
  const b = params.get('b') ?? 'drafts'

  useEffect(() => { getVersions().then(setReport).catch(() => setReport(null)) },
            [])

  useEffect(() => {
    compareVersions(a, b)
      .then(got => {
        setChanges(got.changes ?? [])
        setLevel(got.level ?? '')
        setFailed(null)
      })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
  }, [a, b])

  const options = ['current', 'drafts',
    ...(report?.versions ?? []).map(one => one.version)]

  function pick(side: string, value: string) {
    const next = new URLSearchParams(params)
    next.set(side, value)
    setParams(next, { replace: true })
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate('/versions')}>
          {t('versions.title')}
        </Button>
        <h1 className="text-base font-semibold">{t('versions.compare')}</h1>
      </div>
      <div className="flex flex-wrap items-end gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-2">
        {(['a', 'b'] as const).map(side => (
          <div key={side}>
            <label className="field-label" htmlFor={`cmp-${side}`}>
              {side === 'a' ? t('versions.compareA') : t('versions.compareB')}
            </label>
            <select id={`cmp-${side}`} className="input-field"
                    value={side === 'a' ? a : b}
                    onChange={e => pick(side, e.target.value)}>
              {options.map(one => (
                <option key={one} value={one}>
                  {one === 'current' ? t('versions.optionCurrent')
                    : one === 'drafts' ? t('versions.optionDrafts') : one}
                </option>
              ))}
            </select>
          </div>
        ))}
        <StatusBadge kind="info">
          {tf('versions.compareLevel', { level: levelWords(level) })}
        </StatusBadge>
      </div>
      <div className="flex-1 overflow-auto p-4">
        {failed && (
          <div className="card mb-4 border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}
        <div className="card">
          <ChangesTable changes={changes} />
        </div>
      </div>
    </div>
  )
}

/** Changes by level for the version hint: the service counts them
 *  itself (`suggested.by_level`), the screen only substitutes. */
function byLevel(counts: Record<string, number> | undefined):
    Record<string, number> {
  return { major: counts?.major ?? 0, minor: counts?.minor ?? 0,
           patch: counts?.patch ?? 0 }
}

function VersionsMain() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const reloadSession = useSessionStore(s => s.reload)

  const [report, setReport] = useState<VersionsReport | null>(null)
  const [validation, setValidation] = useState<ValidateReport | null>(null)
  const [validationFailed, setValidationFailed] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftRow[]>([])
  const [journal, setJournal] = useState<JournalRow[]>([])
  const [journalWords, setJournalWords] = useState<string | undefined>()
  const [journalOpen, setJournalOpen] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)

  const [assembleOpen, setAssembleOpen] = useState(
    params.get('assemble') === '1')
  const [assembleVersion, setAssembleVersion] = useState('')
  const [assembleNote, setAssembleNote] = useState('')
  const [assembleStop, setAssembleStop] = useState(false)
  const [assembleWarn, setAssembleWarn] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [releaseOpen, setReleaseOpen] = useState(false)
  const [releaseVersion, setReleaseVersion] = useState('')
  const [releaseNote, setReleaseNote] = useState('')
  const [releaseStop, setReleaseStop] = useState(false)
  const [releaseWarn, setReleaseWarn] = useState<string | null>(null)
  // The checklist of the release comes from the service itself (a dry
  // run that writes nothing): markers, a clean git tree and the watchman
  // of the products are its knowledge, not the screen's guess.
  const [releaseChecks, setReleaseChecks] = useState<ReleaseChecks | null>(
    null)
  const [checksFailed, setChecksFailed] = useState<string | null>(null)

  const [discardOpen, setDiscardOpen] = useState(false)
  const [changesOf, setChangesOf] = useState<Change[] | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importForce, setImportForce] = useState(false)

  const load = useCallback(() => {
    // The status line above the screen counts drafts and open requests:
    // on this screen they change, so the session is re-read with them.
    reloadSession()
    // A folder outside the journal is not a journal entry. Its diagnostic
    // already belongs to /api/validate; show that result without inventing
    // release metadata or modifying the versions/API contract.
    setValidation(null)
    setValidationFailed(null)
    validateCatalog()
      .then(got => { setValidation(got) })
      .catch(err => setValidationFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
    getVersions()
      .then(got => {
        setReport(got)
        setAssembleVersion(got.suggested?.version ?? '')
        setReleaseVersion((got.suggested?.version ?? '').split('-')[0])
        setFailed(null)
      })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
    listDrafts().then(got => {
      setDrafts(got.items ?? [])
      // The warning about the watchman comes BEFORE the button
      // (section 4.5): the service runs B01 dry for the suggested
      // number, the screen shows its words with the checkbox. The 412
      // refusal on the click stays the second line and says the same.
      setAssembleWarn(got.bindings_stop?.words ?? null)
    }).catch(() => setDrafts([]))
    getJournal(50).then(got => {
      setJournal(got.items ?? [])
      setJournalWords(got.words)
    }).catch(() => setJournal([]))
  }, [reloadSession])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!releaseOpen) return
    let alive = true
    const timer = setTimeout(() => {
      getReleaseChecks(releaseVersion.trim() || undefined)
        .then(got => {
          if (!alive) return
          setReleaseChecks(got)
          setChecksFailed(null)
        })
        .catch(err => {
          if (!alive) return
          setReleaseChecks(null)
          setChecksFailed(err instanceof ServiceError
            ? tf('common.refusal', { code: err.code, words: err.words })
            : String(err))
        })
    }, 250)
    return () => { alive = false; clearTimeout(timer) }
  }, [releaseOpen, releaseVersion])

  function refuse(err: unknown, onStop: (words: string) => void) {
    if (err instanceof ServiceError) {
      if (err.code === 'bindings-stop') onStop(err.words)
      notify.error(tf('common.refusal',
        { code: err.code, words: err.words }))
    } else {
      notify.error(String(err))
    }
  }

  async function runAssemble() {
    setBusy(true)
    try {
      const got = await assemble(assembleNote.trim(),
                                 assembleVersion.trim() || null,
                                 assembleStop)
      notify.success(tf('versions.assembleDone', {
        version: got.version,
        fingerprint: got.fingerprint.slice(0, 12),
        applied: got.applied }))
      if (got.next?.length) {
        notify.info(`${t('versions.next')}:\n${got.next.join('\n')}`)
      }
      if (got.derived?.length) {
        notify.info(tf('card.derived', { list: got.derived.join(', ') }))
      }
      setAssembleOpen(false)
      setAssembleStop(false)
      setAssembleWarn(null)
      const next = new URLSearchParams(params)
      next.delete('assemble')
      setParams(next, { replace: true })
      await reloadSession()
      load()
    } catch (err) {
      refuse(err, words => setAssembleWarn(words))
    } finally {
      setBusy(false)
    }
  }

  async function runRelease() {
    setBusy(true)
    try {
      const got = await release(releaseVersion.trim(), releaseNote.trim(),
                                releaseStop)
      notify.success(tf('versions.releaseDone', {
        version: got.version,
        fingerprint: got.fingerprint.slice(0, 12),
        dir: got.release_dir }))
      if (got.next?.length) {
        notify.info(`${t('versions.next')}:\n${got.next.join('\n')}`)
      }
      setReleaseOpen(false)
      setReleaseStop(false)
      setReleaseWarn(null)
      await reloadSession()
      load()
    } catch (err) {
      refuse(err, words => setReleaseWarn(words))
    } finally {
      setBusy(false)
    }
  }

  async function discardAll() {
    setDiscardOpen(false)
    try {
      const got = await deleteAllDrafts()
      notify.success(tf('versions.discardAllDone', { n: got.discarded }))
    } catch (err) {
      refuse(err, () => undefined)
    }
    await reloadSession()
    load()
  }

  async function runImport() {
    if (!importFile) return
    setBusy(true)
    try {
      const got = await importCatalog(importFile, importForce)
      notify.success(tf('versions.importDone', {
        version: got.version,
        fingerprint: got.fingerprint.slice(0, 12) }))
      for (const words of got.warnings ?? []) notify.warning(words)
      setImportOpen(false)
      setImportFile(null)
      setImportForce(false)
      await reloadSession()
      load()
    } catch (err) {
      refuse(err, () => undefined)
    } finally {
      setBusy(false)
    }
  }

  // Rights come from the service (GET /api/session, `rights`): the
  // screen disables a button by `allowed` and shows ITS words in the
  // tooltip instead of keeping a copy of them.
  const assembleRight = rightOf(session, 'assemble')
  const discardRight = rightOf(session, 'draft-discard')
  const releaseRight = rightOf(session, 'release')
  const importRight = rightOf(session, 'import')
  const current = report?.current
  const suggested = report?.suggested
  const base = useMemo(() => {
    const rows = report?.versions ?? []
    return rows.length ? rows[rows.length - 1].version : null
  }, [report])
  const releaseProblems = useMemo(() => {
    if (!report || !validation) return []
    const known = new Set(report.versions.map(row => row.version))
    return validation.releases.filter(row => !row.ok && !known.has(row.version))
  }, [report, validation])

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Tags className="h-5 w-5 text-cf-primary" />
        <h1 className="text-base font-semibold">{t('versions.title')}</h1>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <a className="btn-secondary px-2.5 py-1.5 text-xs"
             href={EXPORT_URL}>
            <Download className="h-4 w-4" />
            {t('versions.export')}
          </a>
          <Button variant="secondary" size="sm"
                  leadingIcon={<Upload className="h-4 w-4" />}
                  disabled={!importRight.allowed}
                  title={rightHint(importRight)}
                  onClick={() => setImportOpen(true)}>
            {t('versions.import')}
          </Button>
          <Button variant="secondary" size="sm"
                  onClick={() => navigate('/versions/compare?a=current'
                    + '&b=drafts')}>
            {t('versions.compare')}
          </Button>
          <Button size="sm"
                  leadingIcon={<PackageCheck className="h-4 w-4" />}
                  disabled={!assembleRight.allowed}
                  title={rightHint(assembleRight)}
                  onClick={() => setAssembleOpen(true)}>
            {t('versions.assemble')}
          </Button>
          <Button size="sm" variant="danger"
                  disabled={!releaseRight.allowed}
                  title={rightHint(releaseRight)}
                  onClick={() => setReleaseOpen(true)}>
            {t('versions.release')}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {failed && (
          <div className="card mb-4 border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}

        {current && (
          <div className="card mb-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold">
                {t('versions.current')}
              </h2>
              <StatusBadge kind={current.is_draft ? 'warn' : 'ok'}>
                {current.is_draft ? t('session.draft')
                  : t('session.release')}
              </StatusBadge>
              <span className="font-mono text-sm">{current.version}</span>
              <span className="break-all font-mono text-xs
                               text-cf-text-muted">
                {current.fingerprint}
              </span>
              <button
                aria-label={t('session.copyFingerprint')}
                title={t('session.copyFingerprint')}
                onClick={() => {
                  navigator.clipboard?.writeText(current.fingerprint)
                  notify.info(t('common.copied'))
                }}
                className="rounded p-1 text-cf-text-muted
                           transition-colors hover:bg-cf-hover"
              >
                <Copy className="h-3 w-3" />
              </button>
            </div>
            <p className="text-xs text-cf-text-muted">
              {current.released_at} · {current.released_by}
            </p>
            <p className="text-sm">{current.note}</p>
            {current.gates && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-cf-text-muted">
                  {t('versions.gates')}
                </span>
                <StatusBadge
                  kind={current.gates.stand_tasks_required ? 'ok' : 'neutral'}
                >
                  {t('versions.gateStand')}
                </StatusBadge>
                <StatusBadge
                  kind={current.gates.subject_words_required
                    ? 'ok' : 'neutral'}
                >
                  {t('versions.gateWords')}
                </StatusBadge>
              </div>
            )}
            {suggested && (
              <p className="text-xs text-cf-text-muted">
                {base
                  ? tf('versions.suggested', {
                      base, n: suggested.changes ?? 0,
                      version: suggested.version,
                      ...byLevel(suggested.by_level) })
                  : tf('versions.suggestedNoBase', {
                      version: suggested.version,
                      n: suggested.changes ?? 0,
                      ...byLevel(suggested.by_level) })}
              </p>
            )}
          </div>
        )}

        <div className="card mb-4 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">
              {tf('versions.draftsBlock', { n: drafts.length })}
            </h2>
            <Button className="ml-auto" variant="secondary" size="sm"
                    leadingIcon={<Trash2 className="h-4 w-4" />}
                    disabled={!discardRight.allowed || drafts.length === 0}
                    title={rightHint(discardRight)}
                    onClick={() => setDiscardOpen(true)}>
              {t('versions.discardAll')}
            </Button>
          </div>
          {drafts.length === 0
            ? <p className="text-sm text-cf-text-muted">
                {t('versions.draftsEmpty')}
              </p>
            : <ul className="space-y-1">
                {drafts.map(one => (
                  <li key={`${one.section}/${one.id ?? one.key}`}
                      className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-mono text-xs">
                      {tf('versions.draftsOf', {
                        section: one.section,
                        id: one.id ?? one.key ?? '',
                        kind: one.kind, by: one.by, at: one.saved_at })}
                    </span>
                    <span className="text-cf-text-muted">{one.note}</span>
                    <Button variant="ghost" size="sm"
                            onClick={() => setChangesOf(one.changes ?? [])}>
                      {t('common.changes')}
                    </Button>
                    {one.id && one.section !== 'dictionaries' && (
                      <Link to={`/records/${one.section}/${one.id}`}
                            className="text-xs text-cf-primary
                                       hover:underline">
                        {t('common.open')}
                      </Link>
                    )}
                    {one.section === 'dictionaries' && (
                      <Link to={`/dictionaries/${one.key ?? one.id}`}
                            className="text-xs text-cf-primary
                                       hover:underline">
                        {t('common.open')}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>}
        </div>

        {validationFailed && (
          <div role="alert" className="card mb-4 border-cf-error text-sm text-cf-error">
            {validationFailed}
          </div>
        )}
        {releaseProblems.length > 0 && (
          <ul role="alert" className="card mb-4 space-y-1 border-cf-error text-sm text-cf-error">
            {releaseProblems.map(row => (
              <li key={row.version}>
                <span className="font-mono">{row.version}</span>: {row.words}
              </li>
            ))}
          </ul>
        )}
        <div className="card mb-4">
          <h2 className="mb-2 text-sm font-semibold">
            {t('versions.journalOfVersions')}
          </h2>
          {(report?.versions ?? []).length === 0
            ? <p className="text-sm text-cf-text-muted">
                {t('versions.empty')}
              </p>
            : <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <th className="table-head">
                        {t('versions.colVersion')}
                      </th>
                      <th className="table-head">{t('versions.colDate')}</th>
                      <th className="table-head">{t('versions.colBy')}</th>
                      <th className="table-head">
                        {t('versions.colFingerprint')}
                      </th>
                      <th className="table-head">{t('versions.colCode')}</th>
                      <th className="table-head">
                        {t('versions.colChanges')}
                      </th>
                      <th className="table-head">{t('versions.colDir')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(report?.versions ?? []).map(row => (
                      <tr key={row.version} className="table-row">
                        <td className="table-cell font-mono text-xs">
                          <Link to={`/versions/${row.version}`}
                                className="text-cf-primary hover:underline">
                            {row.version}
                          </Link>
                        </td>
                        <td className="table-cell whitespace-nowrap">
                          {row.released_at}
                        </td>
                        <td className="table-cell">{row.released_by}</td>
                        <td className="table-cell font-mono text-xs">
                          {row.fingerprint.slice(0, 12)}
                        </td>
                        <td className="table-cell font-mono text-xs">
                          {(row.code ?? '').slice(0, 7)}
                        </td>
                        <td className="table-cell">
                          <Button variant="ghost" size="sm"
                                  onClick={() => setChangesOf(
                                    row.changes ?? [])}>
                            {(row.changes ?? []).length}
                          </Button>
                        </td>
                        <td className="table-cell">
                          <StatusBadge
                            kind={!row.release_dir_ok ? 'error'
                              : row.release_fingerprint_ok ? 'ok' : 'error'}
                          >
                            {!row.release_dir_ok
                              ? t('versions.dirMissing')
                              : row.release_fingerprint_ok
                                ? t('versions.dirOk')
                                : t('versions.dirMismatch')}
                          </StatusBadge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>}
        </div>

        <div className="card">
          <button
            className="flex w-full items-center justify-between text-sm
                       font-semibold"
            onClick={() => setJournalOpen(!journalOpen)}
          >
            {t('versions.journalTitle')}
            <span className="text-xs text-cf-text-muted">
              {journal.length}
            </span>
          </button>
          {journalOpen && (
            journal.length === 0
              ? <p className="mt-2 text-sm text-cf-text-muted">
                  {journalWords ?? t('versions.journalEmpty')}
                </p>
              : <div className="mt-2 overflow-x-auto">
                  <table className="w-full border-collapse">
                    <thead>
                      <tr>
                        <th className="table-head">
                          {t('versions.journalAt')}
                        </th>
                        <th className="table-head">
                          {t('versions.journalActor')}
                        </th>
                        <th className="table-head">
                          {t('versions.journalOp')}
                        </th>
                        <th className="table-head">
                          {t('versions.journalTarget')}
                        </th>
                        <th className="table-head">
                          {t('versions.journalOutcome')}
                        </th>
                        <th className="table-head">
                          {t('versions.journalWords')}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {journal.map((row, index) => (
                        <tr key={`${row.at}-${index}`} className="table-row">
                          <td className="table-cell whitespace-nowrap
                                         font-mono text-xs">
                            {row.at}
                          </td>
                          <td className="table-cell">{row.actor}</td>
                          <td className="table-cell font-mono text-xs">
                            {row.op}
                          </td>
                          <td className="table-cell font-mono text-xs">
                            {row.target}
                          </td>
                          <td className="table-cell">{row.outcome}</td>
                          <td className="table-cell text-xs">{row.words}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
          )}
        </div>
      </div>

      <Modal open={assembleOpen}
             onClose={() => setAssembleOpen(false)}
             title={t('versions.assembleTitle')} wide>
        <div className="space-y-3">
          <p className="text-xs text-cf-text-muted">
            {tf('versions.draftsBlock', { n: drafts.length })}
          </p>
          <div>
            <label className="field-label" htmlFor="asm-version">
              {t('versions.assembleVersion')}
            </label>
            <input id="asm-version" className="input-field font-mono"
                   value={assembleVersion}
                   onChange={e => setAssembleVersion(e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="asm-note">
              {t('versions.assembleNote')}
            </label>
            <textarea id="asm-note" className="input-field" rows={3}
                      value={assembleNote}
                      onChange={e => setAssembleNote(e.target.value)} />
          </div>
          {assembleWarn && (
            <div className="rounded-lg border border-cf-warning
                            bg-cf-warning/10 p-2">
              <p className="text-xs text-cf-text">{assembleWarn}</p>
              <p className="mt-1 text-xs text-cf-text-muted">
                {t('versions.assembleStopAhead')}
              </p>
              <label className="mt-2 flex items-center gap-2 text-xs">
                <input type="checkbox" checked={assembleStop}
                       onChange={e => setAssembleStop(e.target.checked)} />
                {t('versions.assembleStop')}
              </label>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary"
                    onClick={() => setAssembleOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button disabled={busy || !assembleNote.trim()
                      || drafts.length === 0}
                    onClick={runAssemble}>
              {t('versions.assemble')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={releaseOpen} onClose={() => setReleaseOpen(false)}
             title={t('versions.releaseTitle')} wide>
        <div className="space-y-3">
          <h3 className="text-sm font-semibold">
            {t('versions.releaseChecklist')}
          </h3>
          {checksFailed && (
            <p className="text-xs text-cf-error">{checksFailed}</p>
          )}
          {!releaseChecks && !checksFailed && (
            <p className="text-xs text-cf-text-muted">{t('common.loading')}</p>
          )}
          {releaseChecks && (
            <ul className="space-y-1 text-xs">
              {releaseChecks.checks.map(one => (
                <li key={one.key} className="flex flex-wrap items-baseline
                                             gap-2">
                  <StatusBadge kind={one.ok ? 'ok' : 'error'}
                               title={one.words}>
                    {one.n}. {one.title}
                  </StatusBadge>
                  <span className={one.ok ? 'text-cf-text-muted'
                    : 'text-cf-error'}>{one.words}</span>
                </li>
              ))}
              <li>
                <StatusBadge kind="info">
                  {t('versions.checkDiff')}: {releaseChecks.suggested.changes}
                </StatusBadge>
              </li>
            </ul>
          )}
          <p className="text-xs text-cf-text-muted">
            {t('versions.releaseServiceChecks')}
          </p>
          <div>
            <label className="field-label" htmlFor="rel-version">
              {t('versions.assembleVersion')}
            </label>
            <input id="rel-version" className="input-field font-mono"
                   value={releaseVersion}
                   onChange={e => setReleaseVersion(e.target.value)} />
          </div>
          <div>
            <label className="field-label" htmlFor="rel-note">
              {t('versions.assembleNote')}
            </label>
            <textarea id="rel-note" className="input-field" rows={3}
                      value={releaseNote}
                      onChange={e => setReleaseNote(e.target.value)} />
          </div>
          {releaseWarn && (
            <div className="rounded-lg border border-cf-warning
                            bg-cf-warning/10 p-2">
              <p className="text-xs text-cf-text">{releaseWarn}</p>
              <label className="mt-2 flex items-center gap-2 text-xs">
                <input type="checkbox" checked={releaseStop}
                       onChange={e => setReleaseStop(e.target.checked)} />
                {t('versions.assembleStop')}
              </label>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setReleaseOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger"
                    disabled={busy || !releaseNote.trim()
                      || !releaseVersion.trim()}
                    onClick={runRelease}>
              {t('versions.release')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={importOpen} onClose={() => setImportOpen(false)}
             title={t('versions.importTitle')}>
        <div className="space-y-3">
          <div
            className="rounded-xl border border-dashed border-cf-border
                       p-6 text-center text-sm text-cf-text-muted"
            onDragOver={e => { e.preventDefault(); e.stopPropagation() }}
            onDrop={e => {
              e.preventDefault()
              e.stopPropagation()
              const file = e.dataTransfer.files?.[0]
              if (file) setImportFile(file)
            }}
          >
            {importFile
              ? tf('versions.importPicked', { file: importFile.name })
              : t('versions.importDrop')}
          </div>
          <div>
            <label className="field-label" htmlFor="import-file">
              {t('versions.importPick')}
            </label>
            <input
              id="import-file"
              type="file"
              accept="application/json,.json"
              className="input-field"
              onChange={e => setImportFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <p className="text-xs text-cf-text-muted">
            {tf('versions.importConfirm', {
              from: current?.version ?? '',
              file: importFile?.name ?? '' })}
          </p>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={importForce}
                   onChange={e => setImportForce(e.target.checked)} />
            {t('versions.importForce')}
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setImportOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" disabled={!importFile || busy}
                    onClick={runImport}>
              {t('versions.import')}
            </Button>
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={discardOpen}
        title={t('versions.discardAllTitle')}
        message={t('versions.discardAllWords')}
        confirmLabel={t('versions.discardAll')}
        danger
        onCancel={() => setDiscardOpen(false)}
        onConfirm={discardAll}
      />

      <Modal open={changesOf !== null} onClose={() => setChangesOf(null)}
             title={t('common.changes')} wide>
        <ChangesTable changes={changesOf ?? []} />
      </Modal>
    </div>
  )
}
