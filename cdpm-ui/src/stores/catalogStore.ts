import { create } from 'zustand'
import { getCatalog, getSettings, type CatalogProbe } from '../api'

// Каталог шаблонов на всю панель, две правды в одном хранилище.
//
// 1. Адрес СЛУЖБЫ каталога — настройка CREW_CATALOG_URL с точки
//    /api/settings. Не настроена — null, и ссылок панель не строит:
//    мёртвая ссылка хуже честного «служба каталога не настроена».
// 2. Проба САМОГО каталога, которым панель работает (`GET /api/catalog`,
//    шаг 7): версия и отпечаток либо слова отказа. С шага 7 каталог
//    обязателен, и без него ручки отвечают 503 `catalog-unavailable` —
//    состояние каталога панель обязана показывать словами, а не
//    оставлять аналитика гадать по отказам.
//
// Обе точки читаются ОДИН раз на панель (а не каждой страницей): ни та,
// ни другая на ход прогона не влияют, и опроса им не нужно.
//
// Попутно из тех же настроек — номер версии продукта для подписи внизу
// меню (РВ-141): одна настройка площадки CREW_PRODUCT_VERSION, не
// задана — null и подписи нет.
interface CatalogState {
  url: string | null
  productVersion: string | null
  probe: CatalogProbe | null
  loaded: boolean
  load: () => void
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  url: null,
  productVersion: null,
  probe: null,
  loaded: false,
  load: () => {
    if (get().loaded) return
    set({ loaded: true })
    getSettings()
      .then(s => set({ url: s.catalog_url ?? null,
                       productVersion: s.product_version ?? null }))
      // Настройки не ответили — ссылок не будет: панель из-за
      // необязательной службы падать не должна.
      .catch(() => set({ url: null, productVersion: null }))
    getCatalog()
      .then(p => set({ probe: p }))
      // Проба не ответила (сервер прежней версии, точки ещё нет) —
      // строки состояния просто не будет; молчание тише вранья.
      .catch(() => set({ probe: null }))
  },
}))
