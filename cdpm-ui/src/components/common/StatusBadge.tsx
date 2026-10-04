import clsx from 'clsx'

// Бейдж состояния прогона/находки на токенах темы.
const KIND_CLASS: Record<string, string> = {
  ok: 'bg-cf-success/15 text-cf-success',
  warn: 'bg-cf-warning/15 text-cf-warning',
  error: 'bg-cf-error/15 text-cf-error',
  info: 'bg-cf-primary/15 text-cf-primary',
  muted: 'bg-cf-hover text-cf-text-muted',
}

export default function StatusBadge({ kind = 'muted', children,
                                      title }: {
  kind?: keyof typeof KIND_CLASS
  children: React.ReactNode
  title?: string
}) {
  return (
    <span className={clsx('status-badge', KIND_CLASS[kind])}
          title={title}>
      {children}
    </span>
  )
}
