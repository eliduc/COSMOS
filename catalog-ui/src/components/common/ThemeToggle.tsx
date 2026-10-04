import { Moon, Sun, Monitor } from 'lucide-react'
import clsx from 'clsx'
import { useThemeStore, type Theme } from '../../stores/themeStore'
import { t } from '../../i18n'
import type { MessageKey } from '../../i18n'

// Three-way theme switch (light / dark / system): segments with icons;
// the current choice is highlighted, every segment carries aria-label
// and aria-pressed.
const OPTIONS: { value: Theme; label: MessageKey; Icon: typeof Sun }[] = [
  { value: 'light', label: 'theme.light', Icon: Sun },
  { value: 'dark', label: 'theme.dark', Icon: Moon },
  { value: 'system', label: 'theme.system', Icon: Monitor },
]

export default function ThemeToggle({ compact }: { compact?: boolean }) {
  const theme = useThemeStore(s => s.theme)
  const setTheme = useThemeStore(s => s.setTheme)
  return (
    <div className={clsx('flex items-center gap-1 rounded-lg border',
                         'border-cf-border bg-cf-input p-1',
                         compact && 'flex-col')}>
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          aria-label={t(label)}
          aria-pressed={theme === value}
          title={t(label)}
          onClick={() => setTheme(value)}
          className={clsx(
            'rounded-md p-1.5 transition-colors',
            theme === value
              ? 'bg-cf-panel text-cf-primary shadow-sm'
              : 'text-cf-text-muted hover:text-cf-text',
          )}
        >
          <Icon className="h-4 w-4" />
        </button>
      ))}
    </div>
  )
}
