import { Eye, UserCog } from 'lucide-react'
import StatusBadge from '../common/StatusBadge'
import { isOwner, rightOf, type Session } from '../../api'
import { t, tf } from '../../i18n'

// Role of the workplace: owner, analyst or the read-only mode of a
// site. The tooltip carries the refusal words of the SERVICE itself
// (session.rights), so a disabled button is never a silent one and the
// screen keeps no second copy of the rules.
export default function RoleBadge({ session }: { session: Session | null }) {
  if (!session) return null
  if (session.mode !== 'edit' || !session.actor) {
    return (
      <StatusBadge kind="neutral"
                   title={rightOf(session, 'draft-save').words ?? undefined}>
        <Eye className="h-3 w-3" />
        {t('session.modeView')}
      </StatusBadge>
    )
  }
  return (
    <StatusBadge
      kind={isOwner(session) ? 'info' : 'warn'}
      title={rightOf(session, 'release').words ?? undefined}
    >
      <UserCog className="h-3 w-3" />
      {tf('session.workplace', { actor: session.actor })}
    </StatusBadge>
  )
}
