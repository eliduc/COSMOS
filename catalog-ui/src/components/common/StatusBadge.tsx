import clsx from 'clsx'
import type { ReactNode } from 'react'
import type { BadgeKind } from '../../api'

// State chip on theme tokens. The colour map is the contract shared by
// the three panels (project step 5, section 0): present - green,
// claimed - yellow, absent - grey, "nobody declared the kind" - blue,
// binding errors (B0x) - red.
const KIND_CLASS: Record<BadgeKind, string> = {
  ok: 'bg-cf-success/15 text-cf-success',
  warn: 'bg-cf-warning/15 text-cf-warning',
  error: 'bg-cf-error/15 text-cf-error',
  info: 'bg-cf-primary/15 text-cf-primary',
  neutral: 'bg-cf-hover text-cf-text-muted',
}

export default function StatusBadge({ kind = 'neutral', children, title,
                                      className }: {
  kind?: BadgeKind
  children: ReactNode
  title?: string
  className?: string
}) {
  return (
    <span className={clsx('status-badge', KIND_CLASS[kind], className)}
          title={title}>
      {children}
    </span>
  )
}
