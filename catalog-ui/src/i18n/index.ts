import { ru } from './ru'

// Словарь подписей интерфейса. Правило: в файлах экранов (*.tsx) нет
// ни одной русской строки — только ключи t('…') и tf('…', {…}); гейт
// T13 (tests/catalog_service/test_ui_i18n.py) это проверяет. Пять
// языков — по отдельному слову владельца: тогда рядом появляются
// en/uz/kk/ar.ts и паритетный тест ключей и подстановок.
export type MessageKey = keyof typeof ru

/** Подпись по ключу. Ключа нет — возвращается сам ключ: экран не
 *  падает и потеря видна словами. */
export function t(key: MessageKey): string {
  return ru[key] ?? (key as string)
}

/** Подпись с подстановками вида {имя}. Пропущенная подстановка
 *  остаётся в тексте как {имя} — молчаливой пустоты не бывает. */
export function tf(key: MessageKey,
                   params: Record<string, string | number | null | undefined>):
    string {
  return t(key).replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name]
    return value === undefined || value === null ? whole : String(value)
  })
}

/** Уровень правки диффа (major | minor | patch | same) словами. */
const LEVEL_LABEL: Record<string, MessageKey> = {
  major: 'versions.levelMajor',
  minor: 'versions.levelMinor',
  patch: 'versions.levelPatch',
  same: 'versions.levelSame',
}

export function levelWords(level: string | null | undefined): string {
  const key = LEVEL_LABEL[level ?? '']
  return key ? t(key) : (level ?? '')
}

export { ru }
