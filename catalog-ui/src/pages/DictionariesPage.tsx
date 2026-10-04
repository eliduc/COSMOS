import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { BookMarked } from 'lucide-react'
import StatusBadge from '../components/common/StatusBadge'
import {
  CLOSED_DICTIONARY_KEYS, listDictionaries, ServiceError,
  type DictionariesList,
} from '../api'
import { t, tf } from '../i18n'
import type { MessageKey } from '../i18n'

// Screen "Dictionaries": the inventory of catalog dictionary keys. The
// number of keys is taken from the service, never written as a literal
// - the catalog is allowed to grow a key without the screen lying.
const KIND_LABEL: Record<string, MessageKey> = {
  list: 'dicts.kindList',
  object: 'dicts.kindObject',
  text: 'dicts.kindText',
  order: 'dicts.kindOrder',
}

export default function DictionariesPage() {
  const [got, setGot] = useState<DictionariesList | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    listDictionaries()
      .then(setGot)
      .catch(err => setFailed(err instanceof ServiceError
        ? tf('common.refusal', { code: err.code, words: err.words })
        : String(err)))
  }, [])

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-cf-border
                      bg-cf-panel px-4 py-3">
        <BookMarked className="h-5 w-5 text-cf-primary" />
        <h1 className="text-base font-semibold">{t('dicts.title')}</h1>
        {got && (
          <span className="ml-auto text-xs text-cf-text-muted">
            {tf('dicts.count', { n: got.keys.length })}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {failed && (
          <div className="card mb-4 border-cf-error">
            <p className="text-sm text-cf-error">{failed}</p>
          </div>
        )}
        {!got && !failed && (
          <p className="text-sm text-cf-text-muted">{t('common.loading')}</p>
        )}
        {got && got.derived.length > 0 && (
          <p className="mb-3 text-xs text-cf-text-muted">
            {tf('dicts.derived', { keys: got.derived.join(', ') })}
          </p>
        )}
        {got && (
          <div className="overflow-x-auto rounded-xl border border-cf-border
                          bg-cf-panel">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="table-head">{t('dicts.colKey')}</th>
                  <th className="table-head">{t('dicts.colKind')}</th>
                  <th className="table-head">{t('dicts.colSize')}</th>
                  <th className="table-head">{t('dicts.colUsages')}</th>
                  <th className="table-head">{t('dicts.colDraft')}</th>
                </tr>
              </thead>
              <tbody>
                {got.keys.map(row => (
                  <tr key={row.key} className="table-row">
                    <td className="table-cell font-mono text-xs">
                      <Link to={`/dictionaries/${row.key}`}
                            className="text-cf-primary hover:underline">
                        {row.key}
                      </Link>
                      {CLOSED_DICTIONARY_KEYS.includes(row.key) && (
                        <StatusBadge kind="neutral" className="ml-2"
                                     title={t('dicts.closed')}>
                          {t('dicts.closedShort')}
                        </StatusBadge>
                      )}
                    </td>
                    <td className="table-cell">
                      {KIND_LABEL[row.kind]
                        ? t(KIND_LABEL[row.kind]) : row.kind}
                    </td>
                    <td className="table-cell">{row.size}</td>
                    <td className="table-cell">{row.usages}</td>
                    <td className="table-cell">
                      {row.draft && (
                        <StatusBadge kind="warn">
                          {t('records.draftDict')}
                        </StatusBadge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
