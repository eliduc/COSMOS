import { create } from 'zustand'

// Состояние оболочки: свёрнутость сайдбара переживает перезагрузку,
// палитра команд открывается по Ctrl-K. Ключ хранилища свой
// (catalog_sidebar_collapsed).
interface UiState {
  sidebarCollapsed: boolean
  toggleSidebar: () => void
  paletteOpen: boolean
  setPaletteOpen: (open: boolean) => void
}

const STORAGE_KEY = 'catalog_sidebar_collapsed'

function getInitialCollapsed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export const useUiStore = create<UiState>((set, get) => ({
  sidebarCollapsed: getInitialCollapsed(),
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed
    try { localStorage.setItem(STORAGE_KEY, next ? '1' : '0') } catch { /* — */ }
    set({ sidebarCollapsed: next })
  },
  paletteOpen: false,
  setPaletteOpen: (open: boolean) => set({ paletteOpen: open }),
}))
