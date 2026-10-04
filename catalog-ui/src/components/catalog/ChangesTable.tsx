import { Link } from 'react-router-dom'
import StatusBadge from '../common/StatusBadge'
import type { BadgeKind, Change } from '../../api'
import { levelWords, t } from '../../i18n'

// "What changed": rows of the reader's diff. The kind of a change comes
// from the closed list of the version journal (added / changed /
// removed) and is not translated here - it is catalog data. The level
// (major / minor / patch) is the diff's hint about the next version and
// is shown only where the service sends it.
const LEVEL_KIND: Record<string, BadgeKind> = {
  major: 'error',
  minor: 'warn',
  patch: 'neutral',
  same: 'neutral',
}

export default function ChangesTable({ changes }: { changes: Change[] }) {
  if (!changes.length) {
    return <p className="text-sm text-cf-text-muted">{t('changes.empty')}</p>
  }
  const withLevel = changes.some(one => !!one.level)
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="table-head">{t('changes.colSection')}</th>
            <th className="table-head">{t('changes.colId')}</th>
            <th className="table-head">{t('changes.colKind')}</th>
            {withLevel && (
              <th className="table-head">{t('changes.colLevel')}</th>
            )}
            <th className="table-head">{t('changes.colNote')}</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((one, index) => (
            <tr key={`${one.section}-${one.id}-${index}`}
                className="table-row">
              <td className="table-cell font-mono text-xs">
                {one.section}
              </td>
              <td className="table-cell font-mono text-xs">
                {one.section === 'dictionaries'
                  ? <Link to={`/dictionaries/${one.id}`}
                          className="text-cf-primary hover:underline">
                      {one.id}
                    </Link>
                  : <Link to={`/records/${one.section}/${one.id}`}
                          className="text-cf-primary hover:underline">
                      {one.id}
                    </Link>}
              </td>
              <td className="table-cell whitespace-nowrap">{one.kind}</td>
              {withLevel && (
                <td className="table-cell whitespace-nowrap">
                  {one.level
                    ? <StatusBadge kind={LEVEL_KIND[one.level] ?? 'neutral'}>
                        {levelWords(one.level)}
                      </StatusBadge>
                    : null}
                </td>
              )}
              <td className="table-cell">{one.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
