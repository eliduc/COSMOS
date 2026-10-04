import { create } from 'zustand'
import { getDictionary, getSession, type Session } from '../api'

// Сеанс службы (роль, режим, версия каталога, счётчики черновиков и
// заявок) и кэш словарей каталога — одно место на весь интерфейс.
//
// Зачем кэш словарей: закрытые перечни каталога (классы, виды, зоны,
// единицы, отношения, шаблоны проверок, виды исполнителей, состояния
// заявок) наполняют выпадающие списки карточки и фильтры всех пяти
// экранов. Ручка GET /api/dictionaries отдаёт только опись ключей,
// значение даёт GET /api/dictionaries/{key} — поэтому нужные ключи
// читаются один раз при первом обращении и живут до перезагрузки
// страницы; сборка черновика сбрасывает кэш вместе с сеансом.

/** Ключи словарей, из которых интерфейс строит списки выбора. */
export const DICT_KEYS = [
  'classes', 'kinds', 'zones', 'family_natures', 'violation_units',
  'violation_sizes', 'history_read_kinds', 'nature_witnesses',
  'param_units', 'param_relations', 'qualifier_kinds', 'record_statuses',
  'executor_kinds', 'executor_states', 'check_templates', 'origin_kinds',
  'actors', 'request_states', 'request_submitters', 'request_source_kinds',
  'unbound_params', 'world_forms', 'rates_policies', 'stand_task_kinds',
  'products',
] as const

interface SessionState {
  session: Session | null
  sessionError: string | null
  loaded: boolean
  load: () => Promise<void>
  reload: () => Promise<void>

  dicts: Record<string, unknown>
  dictsLoaded: boolean
  loadDicts: () => Promise<void>
}

async function readSession(): Promise<Partial<SessionState>> {
  try {
    return { session: await getSession(), sessionError: null, loaded: true }
  } catch (err) {
    return { session: null, loaded: true,
             sessionError: err instanceof Error ? err.message : String(err) }
  }
}

export const useSessionStore = create<SessionState>((set, get) => ({
  session: null,
  sessionError: null,
  loaded: false,

  load: async () => {
    if (get().loaded) return
    set({ loaded: true })
    set(await readSession())
  },

  reload: async () => {
    set(await readSession())
  },

  dicts: {},
  dictsLoaded: false,

  loadDicts: async () => {
    if (get().dictsLoaded) return
    set({ dictsLoaded: true })
    const pairs = await Promise.all(DICT_KEYS.map(async key => {
      try {
        const got = await getDictionary(key)
        return [key, got.value] as const
      } catch {
        // Ключа нет или служба недоступна — список выбора остаётся
        // пустым; экран об этом скажет отказом службы, а не молчанием.
        return [key, null] as const
      }
    }))
    set({ dicts: Object.fromEntries(pairs) })
  },
}))

/** Значения перечня словаря (пустой список, пока словарь не прочитан). */
export function dictList(dicts: Record<string, unknown>,
                         key: string): string[] {
  const value = dicts[key]
  return Array.isArray(value) ? value.map(String) : []
}

/** Ключи объекта словаря (check_templates, unbound_params, world_forms). */
export function dictKeys(dicts: Record<string, unknown>,
                         key: string): string[] {
  const value = dicts[key]
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return Object.keys(value as Record<string, unknown>).sort()
  }
  return []
}

/** Значение подключа объекта словаря. */
export function dictEntry(dicts: Record<string, unknown>, key: string,
                          sub: string): Record<string, unknown> | null {
  const value = dicts[key]
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const entry = (value as Record<string, unknown>)[sub]
    if (entry && typeof entry === 'object') {
      return entry as Record<string, unknown>
    }
  }
  return null
}

/** Карта «код → слово» словаря-карты (с 0.5.0 таков `violation_units`).
 *  Членство судится по КЛЮЧАМ (`dictKeys`), а слово — только подпись
 *  рядом с кодом: второй карты слов у интерфейса нет, она одна — в
 *  каталоге. Значение не строка (объект, число) в карту не попадает:
 *  словарь другого вида показывается кодами, без выдуманных слов. */
export function dictWords(dicts: Record<string, unknown>,
                          key: string): Record<string, string> {
  const value = dicts[key]
  const out: Record<string, string> = {}
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [code, word] of Object.entries(
        value as Record<string, unknown>)) {
      if (typeof word === 'string' && word) out[code] = word
    }
  }
  return out
}
