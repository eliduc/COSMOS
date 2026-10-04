import { Link } from 'react-router-dom'
import type { HistoryItem } from '../../api'
import { t } from '../../i18n'

// History of a record: read only. Lines are written by the assembly of
// a draft (change, creation, owner decision, three lines of a request),
// never by hand - that is why the card shows them and does not edit
// them. A line with a ref to a request links to the request card.
const REQUEST_REF = /^req-\d{4,}$/

export default function HistoryList({ history }: {
  history: HistoryItem[]
}) {
  if (!history.length) {
    return (
      <p className="text-sm text-cf-text-muted">{t('card.historyEmpty')}</p>
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="table-head">{t('card.historyDate')}</th>
            <th className="table-head">{t('card.historyKind')}</th>
            <th className="table-head">{t('card.historyBy')}</th>
            <th className="table-head">{t('card.historyNote')}</th>
            <th className="table-head">{t('card.historyRef')}</th>
          </tr>
        </thead>
        <tbody>
          {history.map((one, index) => (
            <tr key={`${one.date}-${index}`} className="table-row">
              <td className="table-cell whitespace-nowrap font-mono text-xs">
                {one.date}
              </td>
              <td className="table-cell whitespace-nowrap">{one.kind}</td>
              <td className="table-cell whitespace-nowrap">{one.by}</td>
              <td className="table-cell">{one.note}</td>
              <td className="table-cell whitespace-nowrap font-mono text-xs">
                {one.ref && REQUEST_REF.test(one.ref)
                  ? <Link to={`/requests/${one.ref}`}
                          className="text-cf-primary hover:underline">
                      {one.ref}
                    </Link>
                  : (one.ref ?? '')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
