import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowDown, ArrowUp, Plus, Save, Trash2, Undo2 }
  from 'lucide-react'
import Button from '../components/common/Button'
import StatusBadge from '../components/common/StatusBadge'
import notify from '../components/common/StyledToast'
import JsonField from '../components/catalog/JsonField'
import ProblemList from '../components/catalog/ProblemList'
import {
  CLOSED_DICTIONARY_KEYS, deleteDraft, getDictionary, putDictionaryDraft,
  rightHint, rightOf, ServiceError,
  type DictionaryDetail, type Problem,
} from '../api'
import { useSessionStore } from '../stores/sessionStore'
import { t, tf } from '../i18n'

/** A map dictionary "code -> word": every value is a non empty string
 *  (since 0.5.0 such is `violation_units`). Membership of the catalog
 *  is judged by the KEYS, the value is only the word shown next to the
 *  code. An object of another shape (`check_templates`, `world_forms`,
 *  `unbound_params`) gives an empty list and is shown as JSON alone. */
function wordPairs(value: unknown): [string, string][] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const entries = Object.entries(value as Record<string, unknown>)
  if (!entries.length) return []
  if (!entries.every(([, word]) => typeof word === 'string' && !!word)) {
    return []
  }
  return entries as [string, string][]
}

// Card of one dictionary key. A list is edited value by value, an
// object as JSON, the lexicon order by arrows, plain text as a field.
// Service lists of the reader are shown but not offered for editing:
// their values are code, and the service answers 422 closed-list.
//
// "Editing with history" (plan, section 4): a dictionary key has no
// history field in the schema, so the journal below the card is built
// from the service journal and the changes of the version journal.
export default function DictionaryPage() {
  const { key = '' } = useParams()
  const navigate = useNavigate()
  const session = useSessionStore(s => s.session)
  const reloadSession = useSessionStore(s => s.reload)

  const [got, setGot] = useState<DictionaryDetail | null>(null)
  const [value, setValue] = useState<unknown>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState<string | null>(null)
  const [problems, setProblems] = useState<Problem[]>([])
  const [fresh, setFresh] = useState('')

  const load = useCallback(() => {
    setBusy(true)
    getDictionary(key)
      .then(one => {
        setGot(one)
        setValue(one.draft ? one.draft.value : one.value)
        setNote(one.draft?.note ?? '')
        setFailed(null)
      })
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
      .finally(() => setBusy(false))
  }, [key])

  useEffect(() => { load() }, [load])

  const closed = CLOSED_DICTIONARY_KEYS.includes(key)
  const derived = !!got?.derived
  const draftRight = rightOf(session, 'draft-save')
  const discardRight = rightOf(session, 'draft-discard')
  const mayEdit = draftRight.allowed && !closed && !derived

  async function save() {
    try {
      const saved = await putDictionaryDraft(key, value, note.trim())
      setProblems(saved.problems ?? [])
      notify.success(tf('dicts.saved', { key }))
      await reloadSession()
      load()
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
      await deleteDraft('dictionaries', key)
      notify.success(t('card.discarded'))
      await reloadSession()
      load()
    } catch (err) {
      notify.error(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err))
    }
  }

  const list = Array.isArray(value) ? value.map(String) : null
  // The table of words follows the EDITED value, so a change of the
  // draft is seen at once in words, not only in the JSON.
  const words = wordPairs(value)
  // What is removed against the value of the CATALOG (not of the
  // draft): the chip of the major level is set by this difference.
  const original = Array.isArray(got?.value) ? got.value.map(String) : []
  const removed = list
    ? original.filter(one => !list.includes(one)) : []

  function setList(next: string[]) {
    setValue(next)
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b
                      border-cf-border bg-cf-panel px-4 py-3">
        <Button variant="ghost" size="sm"
                leadingIcon={<ArrowLeft className="h-4 w-4" />}
                onClick={() => navigate('/dictionaries')}>
          {t('dicts.title')}
        </Button>
        <h1 className="font-mono text-base font-semibold">{key}</h1>
        {got?.draft && (
          <StatusBadge kind="warn">{t('records.draftDict')}</StatusBadge>
        )}
        {closed && (
          <StatusBadge kind="neutral" title={t('dicts.closed')}>
            {t('dicts.closedShort')}
          </StatusBadge>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button variant="secondary" size="sm"
                  leadingIcon={<Undo2 className="h-4 w-4" />}
                  disabled={!discardRight.allowed || !got?.draft || closed
                    || derived}
                  title={rightHint(discardRight)}
                  onClick={discard}>
            {t('common.discardDraft')}
          </Button>
          <Button size="sm" leadingIcon={<Save className="h-4 w-4" />}
                  disabled={!mayEdit || !note.trim()}
                  title={closed ? t('dicts.closed')
                    : derived ? tf('dicts.derived', { keys: key })
                    : rightHint(draftRight)}
                  onClick={save}>
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
        {!busy && !got && !failed && (
          <p className="text-sm text-cf-text-muted">
            {tf('dicts.notFound', { key })}
          </p>
        )}

        {got && (
          <div className="space-y-4">
            {mayEdit && (
              <div className="card">
                <label className="field-label" htmlFor="dict-note">
                  {t('common.changeNote')}
                </label>
                <input id="dict-note" className="input-field" value={note}
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

            <div className="card space-y-3">
              {derived && (
                <p className="text-sm text-cf-text-muted">
                  {tf('dicts.derived', { keys: key })}
                </p>
              )}

              {got.kind === 'order' && list && (
                <div className="space-y-2">
                  <p className="text-xs text-cf-text-muted">
                    {t('dicts.orderHint')}
                  </p>
                  {list.map((one, index) => (
                    <div key={one}
                         className="flex items-center gap-2 rounded-lg
                                    border border-cf-border bg-cf-input
                                    px-2 py-1">
                      <span className="w-6 text-xs text-cf-text-muted">
                        {index + 1}
                      </span>
                      <span className="flex-1 font-mono text-xs">{one}</span>
                      <Button variant="ghost" size="sm"
                              aria-label={t('dicts.up')}
                              title={t('dicts.up')}
                              disabled={!mayEdit || index === 0}
                              onClick={() => {
                                const next = [...list]
                                const prev = next[index - 1]
                                next[index - 1] = next[index]
                                next[index] = prev
                                setList(next)
                              }}>
                        <ArrowUp className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="sm"
                              aria-label={t('dicts.down')}
                              title={t('dicts.down')}
                              disabled={!mayEdit || index === list.length - 1}
                              onClick={() => {
                                const next = [...list]
                                const after = next[index + 1]
                                next[index + 1] = next[index]
                                next[index] = after
                                setList(next)
                              }}>
                        <ArrowDown className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}

              {got.kind === 'list' && list && (
                <div className="space-y-2">
                  <p className="text-xs text-cf-text-muted">
                    {t('dicts.removeHint')}
                  </p>
                  {/* Removing a value is a MAJOR change (section 4.2):
                      the screen shows the removed values with a chip
                      instead of forgetting them silently. */}
                  {removed.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1">
                      <StatusBadge kind="warn"
                                   title={t('dicts.removedHint')}>
                        {t('dicts.removedLevel')}
                      </StatusBadge>
                      {removed.map(one => (
                        <span key={one}
                              className="status-badge bg-cf-warning/15
                                         text-cf-warning line-through">
                          {tf('dicts.removedValue', { value: one })}
                          {mayEdit && (
                            <button
                              aria-label={t('dicts.restoreValue')}
                              title={t('dicts.restoreValue')}
                              onClick={() => setList([...list, one])}
                              className="rounded p-0.5 no-underline
                                         hover:bg-cf-hover"
                            >
                              <Undo2 className="h-3 w-3" />
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-1">
                    {list.map(one => (
                      <span key={one}
                            className="status-badge bg-cf-primary/15
                                       text-cf-primary">
                        {one}
                        {mayEdit && (
                          <button
                            aria-label={t('dicts.removeValue')}
                            title={t('dicts.removeValue')}
                            onClick={() => setList(
                              list.filter(other => other !== one))}
                            className="rounded p-0.5 hover:bg-cf-hover"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </span>
                    ))}
                  </div>
                  {mayEdit && (
                    <div className="flex items-center gap-2">
                      <input
                        className="input-field max-w-xs"
                        placeholder={t('dicts.newValue')}
                        value={fresh}
                        onChange={e => setFresh(e.target.value)}
                      />
                      <Button
                        variant="secondary" size="sm"
                        leadingIcon={<Plus className="h-4 w-4" />}
                        disabled={!fresh.trim() || list.includes(fresh.trim())}
                        onClick={() => {
                          setList([...list, fresh.trim()])
                          setFresh('')
                        }}
                      >
                        {t('dicts.addValue')}
                      </Button>
                    </div>
                  )}
                </div>
              )}

              {got.kind === 'text' && (
                <div>
                  <label className="field-label" htmlFor="dict-text">
                    {t('dicts.textHint')}
                  </label>
                  <textarea
                    id="dict-text"
                    className="input-field"
                    rows={3}
                    disabled={!mayEdit}
                    value={typeof value === 'string' ? value : ''}
                    onChange={e => setValue(e.target.value)}
                  />
                </div>
              )}

              {got.kind === 'object' && words.length > 0 && (
                <div className="space-y-2">
                  <span className="field-label">{t('dicts.wordsTitle')}</span>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead>
                        <tr>
                          <th className="table-head">{t('dicts.colCode')}</th>
                          <th className="table-head">{t('dicts.colWord')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {words.map(([code, word]) => (
                          <tr key={code} className="table-row">
                            <td className="table-cell font-mono text-xs">
                              {code}
                            </td>
                            <td className="table-cell">{word}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-xs text-cf-text-muted">
                    {t('dicts.wordsHint')}
                  </p>
                </div>
              )}

              {got.kind === 'object' && (
                <JsonField
                  id="dict-object"
                  label={key}
                  value={value}
                  rows={14}
                  hint={t('dicts.objectHint')}
                  onChange={mayEdit ? setValue : undefined}
                />
              )}
            </div>

            <div className="card">
              <h3 className="mb-2 text-sm font-semibold">
                {t('dicts.usages')}
              </h3>
              {got.usages.length === 0
                ? <p className="text-sm text-cf-text-muted">
                    {t('dicts.usagesEmpty')}
                  </p>
                : <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <tbody>
                        {got.usages.map((one, index) => (
                          <tr key={`${one.section}-${one.id}-${index}`}
                              className="table-row">
                            <td className="table-cell font-mono text-xs">
                              <Link to={`/records/${one.section}/${one.id}`}
                                    className="text-cf-primary
                                               hover:underline">
                                {one.section}/{one.id}
                              </Link>
                            </td>
                            <td className="table-cell font-mono text-xs">
                              {one.where}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>}
            </div>

            <div className="card">
              <h3 className="mb-2 text-sm font-semibold">
                {t('dicts.journal')}
              </h3>
              {got.journal.length === 0
                ? <p className="text-sm text-cf-text-muted">
                    {t('dicts.journalEmpty')}
                  </p>
                : <ul className="space-y-1 text-xs text-cf-text-muted">
                    {got.journal.map((one, index) => (
                      <li key={index}>
                        <span className="font-mono">
                          {one.at ?? one.version ?? ''}
                        </span>
                        {' '}
                        {[one.actor ?? one.by, one.op ?? one.kind,
                          one.words ?? one.note]
                          .filter(Boolean).join(' · ')}
                      </li>
                    ))}
                  </ul>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
