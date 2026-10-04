import { Link } from 'react-router-dom'
import StatusBadge from '../common/StatusBadge'
import type { Problem } from '../../api'
import { t, tf } from '../../i18n'

// Refusals and warnings of the reader, shown the way the service sends
// them: address, machine code, words. The record part of an address
// (families/<id>, criteria/<id>) becomes a link to the card, because a
// cross rule (X01, D03) reports the address of ANOTHER record and the
// author has to get there in one click.
const ADDRESS = /^(families|criteria)\/([a-z][a-z0-9_]{2,40})/

function Address({ where }: { where: string }) {
  const hit = ADDRESS.exec(where)
  if (!hit) return <span className="font-mono text-xs">{where}</span>
  const rest = where.slice(hit[0].length)
  return (
    <span className="font-mono text-xs">
      <Link to={`/records/${hit[1]}/${hit[2]}`}
            className="text-cf-primary hover:underline">
        {hit[0]}
      </Link>
      {rest}
    </span>
  )
}

export default function ProblemList({ problems, compact }: {
  problems: Problem[]
  compact?: boolean
}) {
  if (!problems.length) {
    return (
      <p className="text-sm text-cf-text-muted">{t('problems.empty')}</p>
    )
  }
  const errors = problems.filter(p => p.severity !== 'warning').length
  return (
    <div className="space-y-2">
      {!compact && (
        <p className="text-xs text-cf-text-muted">
          {tf('problems.counts', { errors,
                                   warnings: problems.length - errors })}
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="table-head">{t('problems.colWhere')}</th>
              <th className="table-head">{t('problems.colCode')}</th>
              <th className="table-head">{t('problems.colWords')}</th>
            </tr>
          </thead>
          <tbody>
            {problems.map((problem, index) => (
              <tr key={`${problem.where}-${problem.code}-${index}`}
                  className="table-row">
                <td className="table-cell">
                  <Address where={problem.where} />
                </td>
                <td className="table-cell whitespace-nowrap">
                  <StatusBadge
                    kind={problem.severity === 'warning' ? 'warn' : 'error'}
                    title={problem.severity === 'warning'
                      ? t('problems.severityWarning')
                      : t('problems.severityError')}
                  >
                    {problem.code}
                  </StatusBadge>
                </td>
                <td className="table-cell">{problem.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
