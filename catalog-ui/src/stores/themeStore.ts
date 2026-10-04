import { create } from 'zustand'

// Тема трёх значений (по образцу панели CDPM): хранится выбор
// пользователя ('light' | 'dark' | 'system'), к <html> всегда
// применяется действующая тема ('light' | 'dark'). Применение — до
// рендера React, чтобы не было вспышки чужой темы. Ключ хранилища
// свой (catalog_theme): служба каталога — отдельный модуль на своём
// порту, чужой выбор темы она не переписывает.
export type Theme = 'light' | 'dark' | 'system'
export type EffectiveTheme = 'light' | 'dark'

interface ThemeState {
  theme: Theme
  effectiveTheme: EffectiveTheme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const STORAGE_KEY = 'catalog_theme'

function getSystemEffective(): EffectiveTheme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light'
  } catch {
    return 'dark'
  }
}

function resolveEffective(theme: Theme): EffectiveTheme {
  return theme === 'system' ? getSystemEffective() : theme
}

function applyEffective(effective: EffectiveTheme) {
  const root = document.documentElement
  if (effective === 'dark') root.classList.add('dark')
  else root.classList.remove('dark')
}

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') {
      return stored
    }
  } catch { /* нет localStorage — не страшно */ }
  return 'system'
}

const initialTheme = getInitialTheme()
const initialEffective = resolveEffective(initialTheme)
applyEffective(initialEffective)

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: initialTheme,
  effectiveTheme: initialEffective,

  setTheme: (theme: Theme) => {
    const effective = resolveEffective(theme)
    applyEffective(effective)
    try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* — */ }
    set({ theme, effectiveTheme: effective })
  },

  toggleTheme: () => {
    const state = get()
    const current: EffectiveTheme =
      state.theme === 'system' ? state.effectiveTheme : state.theme
    const next: Theme = current === 'dark' ? 'light' : 'dark'
    const effective = resolveEffective(next)
    applyEffective(effective)
    try { localStorage.setItem(STORAGE_KEY, next) } catch { /* — */ }
    set({ theme: next, effectiveTheme: effective })
  },
}))

// Пока выбор пользователя — 'system', действующая тема следует за
// системной настройкой.
try {
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', (e: MediaQueryListEvent) => {
    const { theme } = useThemeStore.getState()
    if (theme !== 'system') return
    const effective: EffectiveTheme = e.matches ? 'dark' : 'light'
    applyEffective(effective)
    useThemeStore.setState({ effectiveTheme: effective })
  })
} catch { /* среда без matchMedia (тесты) */ }
