import StatusBadge from '../common/StatusBadge'
import { stateBadgeKind } from '../../api'
import { t } from '../../i18n'

// Executor state of one cell. The words themselves are catalog data
// (the executor_states dictionary of the reader, STATE_* of api.ts); the
// colour map is the contract of the three panels. A cell with no rows
// at all is not "absent" - it is "nobody declared this kind", shown in
// blue with the words behind the tooltip.
export default function StateChip({ state, title }: {
  state: string | null | undefined
  title?: string
}) {
  if (!state) {
    return (
      <StatusBadge kind="info"
                   title={title ?? t('executors.cellUndeclaredTip')}>
        {t('executors.cellUndeclared')}
      </StatusBadge>
    )
  }
  return (
    <StatusBadge kind={stateBadgeKind(state)} title={title}>
      {state}
    </StatusBadge>
  )
}
