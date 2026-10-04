import { Moon, Sun, Monitor } from 'lucide-react'
import clsx from 'clsx'
import { useThemeStore, type Theme } from '../../stores/themeStore'

// Переключатель темы трёх значений (light / dark / system) — сегменты
// с иконками; текущий выбор подсвечен, у каждого сегмента aria-label и
// aria-pressed.
const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Светлая тема', Icon: Sun },
  { value: 'dark', label: 'Тёмная тема', Icon: Moon },
  { value: 'system', label: 'Как в системе', Icon: Monitor },
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
          aria-label={label}
          aria-pressed={theme === value}
          title={label}
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
