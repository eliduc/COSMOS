import StatusBadge from '../common/StatusBadge'
import JsonField from './JsonField'
import type { BadgeKind, StandRow, StandTask } from '../../api'
import { dictKeys } from '../../stores/sessionStore'
import { t, tf } from '../../i18n'

// Stand tasks of a record and the results the products reported for
// them. The tasks are edited as JSON (the shape is the reader's schema:
// id, world, expected, note); the form of the world is checked here
// only against the world_forms dictionary - the meaning is judged by
// validate() of the service on save (A03, X05, X07).
//
// The results column is read only: rows come from stand_verified of the
// binding files, and the products own them.
const RESULT_KIND: Record<string, BadgeKind> = {
  passed: 'ok',
  failed: 'error',
  skipped: 'neutral',
}

export default function StandTasksTable({ tasks, onChange, dicts, results,
                                          summary }: {
  tasks: StandTask[]
  onChange?: (next: StandTask[]) => void
  dicts: Record<string, unknown>
  results?: StandRow[]
  summary?: { passed: number; failed: number; skipped: number }
}) {
  const forms = dictKeys(dicts, 'world_forms')
  const unknownForms = tasks
    .map(one => String((one.world ?? {}).form ?? ''))
    .filter(form => form && forms.length > 0 && !forms.includes(form))

  return (
    <div className="space-y-4">
      {tasks.length === 0 && (
        <p className="text-sm text-cf-text-muted">
          {t('card.standEmptyWords')}
        </p>
      )}
      {tasks.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="table-head">{t('card.standTask')}</th>
                <th className="table-head">{t('card.standWorld')}</th>
                <th className="table-head">{t('card.standExpected')}</th>
                <th className="table-head">{t('card.standNote')}</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map(one => (
                <tr key={one.id} className="table-row">
                  <td className="table-cell font-mono text-xs">{one.id}</td>
                  <td className="table-cell font-mono text-xs">
                    {String((one.world ?? {}).form ?? '')}
                  </td>
                  <td className="table-cell font-mono text-xs">
                    {Object.keys(one.expected ?? {}).join(', ')}
                  </td>
                  <td className="table-cell">{one.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {unknownForms.length > 0 && (
        <StatusBadge kind="warn">
          {unknownForms.join(', ')}
        </StatusBadge>
      )}

      {onChange && (
        <JsonField
          id="stand-tasks"
          label={t('card.groupStand')}
          value={tasks}
          rows={10}
          onChange={next => onChange(
            Array.isArray(next) ? next as StandTask[] : [])}
        />
      )}

      {summary && (
        <StatusBadge kind={summary.failed > 0 ? 'error'
          : summary.passed > 0 ? 'ok' : 'neutral'}>
          {tf('records.stand', { passed: summary.passed,
                                 failed: summary.failed,
                                 skipped: summary.skipped })}
        </StatusBadge>
      )}

      {results && results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="table-head">{t('executors.standTask')}</th>
                <th className="table-head">{t('executors.standKind')}</th>
                <th className="table-head">{t('executors.standResult')}</th>
                <th className="table-head">{t('executors.standAt')}</th>
                <th className="table-head">{t('executors.standCommit')}</th>
                <th className="table-head">{t('executors.standNote')}</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row, index) => (
                <tr key={`${row.task}-${row.executor_kind}-${index}`}
                    className="table-row">
                  <td className="table-cell font-mono text-xs">{row.task}</td>
                  <td className="table-cell font-mono text-xs">
                    {row.executor_kind}
                  </td>
                  <td className="table-cell">
                    <StatusBadge kind={RESULT_KIND[row.result] ?? 'neutral'}>
                      {row.result}
                    </StatusBadge>
                  </td>
                  <td className="table-cell whitespace-nowrap text-xs">
                    {row.at}
                  </td>
                  <td className="table-cell font-mono text-xs">
                    {(row.commit ?? '').slice(0, 7)}
                  </td>
                  <td className="table-cell text-xs">{row.note ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
