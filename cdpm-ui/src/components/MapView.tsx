import { useEffect, useMemo, useRef, useState } from 'react'
import Modal from './common/Modal'

// Карта сети и персонал (ревизия 05.09.2026 — слово владельца с
// репетиции: «визуализация карты и персонала — отдельной визуальной
// кнопкой после блока загрузки соответствующей инфо; сейчас
// ненаглядно и некрасиво»). Окно во весь экран, три вкладки:
//   • «Схема сети» — общий движок NetMap (webapp/static/net-map.js;
//     панель отдаёт его тем же файлом на /net-map.js), рядом —
//     таблица станций (база, рейсов, сотрудников);
//   • «Карта мира» — тот же движок на контурах мира; доступна ТОЛЬКО
//     при полных координатах (geo.complete) и включается человеком —
//     умолчание всегда «схема» (правило продукта: вымышленные коды
//     обезличенных наборов случайно совпадают с настоящими);
//   • «Персонал» — разрез база × должность и полный список с
//     допусками по типам ВС.
// Источник — замороженная книга данных прогона (детерминированная
// проекция сервера /api/runs/{id}/map-projection), никакой модели.

interface MapNode {
  code: string; base?: boolean; flights?: number; employees?: number
}
interface MapArc { a: string; b: string; n: number; per_day?: number }
interface Employee {
  id: string; name: string; base: string; position?: string
  quals?: string[]
}
interface MapData {
  nodes: MapNode[]
  arcs: MapArc[]
  employees: Employee[]
  geo: { coords: Record<string, [number, number]>
         missing: string[]; complete: boolean
         // Источник координат и объяснение словами (05.09): у
         // синтетического класса справочник по коду не применяется.
         source?: string; note?: string }
  summary?: {
    flights?: number; stations?: number; bases?: number
    employees?: number; days?: number | null
    date_from?: string | null; date_to?: string | null
    fleet?: Record<string, number>
  }
  by_base?: Record<string, Record<string, number>>
}

declare global {
  interface Window {
    NetMap?: {
      draw: (cfg: unknown) => void
      worldUrl: string
    }
  }
}

type Tab = 'schema' | 'world' | 'crew'

const SVG_NS = 'http://www.w3.org/2000/svg'

function fmtDate(iso?: string | null): string {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  return d && m && y ? `${d}.${m}.${y}` : iso
}

function Chip({ label, value, title }: {
  label: string; value: string | number; title?: string
}) {
  return (
    <div className="rounded-lg border border-cf-border bg-cf-bg-muted
                    px-3 py-1.5" title={title}>
      <div className="text-[11px] uppercase tracking-wide
                      text-cf-text-muted">{label}</div>
      <div className="text-base font-semibold text-cf-text">{value}</div>
    </div>
  )
}

export default function MapView({ open, runId, onClose }: {
  open: boolean; runId: string; onClose: () => void
}) {
  const [data, setData] = useState<MapData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('schema')
  const [filter, setFilter] = useState('')
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    if (!open) return
    setError(null)
    setData(null)
    setTab('schema')
    setFilter('')
    fetch(`/api/runs/${encodeURIComponent(runId)}/map-projection`)
      .then(async r => {
        if (!r.ok) {
          const d = await r.json().catch(() => ({}))
          throw new Error(d.detail || `HTTP ${r.status}`)
        }
        return r.json()
      })
      .then(setData)
      .catch(e => setError(String(e.message || e)))
  }, [open, runId])

  useEffect(() => {
    if (!open || !data || tab === 'crew' || !svgRef.current
        || !window.NetMap) return
    window.NetMap.worldUrl = '/geo/world-110m.json'
    // Мировая подложка грузится лениво и зовёт redraw позже: к тому
    // времени вкладка могла смениться или окно закрыться — отменённый
    // эффект не рисует (ревью 05.09: рисовал «мир» поверх схемы).
    let cancelled = false
    const draw = () => {
      if (cancelled || !svgRef.current) return
      window.NetMap!.draw({
        svg: svgRef.current,
        data: { nodes: data.nodes, arcs: data.arcs, geo: data.geo },
        mode: tab,
        seed: runId,
        redraw: draw,
        // Подсказки движка — стандартным <title> SVG: HTML движка
        // разбирается DOM-ом (теги долой, сущности раскрыты), текст
        // читают браузер и экранный диктор.
        hover: (el: Element, htmlFn: unknown) => {
          const html = typeof htmlFn === 'function'
            ? String((htmlFn as () => unknown)()) : String(htmlFn ?? '')
          Array.from(el.querySelectorAll(':scope > title'))
            .forEach(t => t.remove())
          const tmp = document.createElement('div')
          tmp.innerHTML = html
          const t = document.createElementNS(SVG_NS, 'title')
          t.textContent = (tmp.textContent || '')
            .replace(/\s+/g, ' ').trim()
          el.appendChild(t)
        },
        labels: {
          arc: 'рейсы', dhArc: 'перегоны', uncArc: 'непокрытые',
          base: 'база',
        },
      })
    }
    draw()
    return () => { cancelled = true }
  }, [open, data, tab, runId])

  const s = data?.summary
  const stations = useMemo(() => {
    if (!data) return []
    return [...data.nodes].sort((a, b) =>
      Number(Boolean(b.base)) - Number(Boolean(a.base))
      || (b.flights || 0) - (a.flights || 0)
      || a.code.localeCompare(b.code))
  }, [data])
  const positions = useMemo(() => {
    const set = new Set<string>()
    Object.values(data?.by_base || {}).forEach(row =>
      Object.keys(row).forEach(p => set.add(p)))
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'ru'))
  }, [data])
  const crew = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const all = data?.employees || []
    if (!q) return all
    return all.filter(e => [e.id, e.name, e.base, e.position || '',
                            (e.quals || []).join(' ')]
      .join(' ').toLowerCase().includes(q))
  }, [data, filter])

  const tabBtn = (id: Tab, label: string, disabled = false,
                  title?: string) => (
    <button
      key={id}
      className={'rounded-lg px-3 py-1.5 text-sm font-semibold '
        + 'transition-colors '
        + (tab === id
          ? 'bg-cf-primary text-white'
          : 'bg-cf-bg-muted text-cf-text hover:bg-cf-hover')
        + (disabled ? ' cursor-not-allowed opacity-40' : '')}
      disabled={disabled}
      title={title}
      onClick={() => setTab(id)}>
      {label}
    </button>
  )

  return (
    <Modal open={open} onClose={onClose}
           title="Карта сети и персонал" full>
      {error && (
        <p className="text-sm text-amber-600">{error}</p>
      )}
      {!error && !data && (
        <p className="text-sm text-cf-text-muted">Загрузка…</p>
      )}
      {data && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Chip label="Станций"
                  value={s?.stations ?? data.nodes.length} />
            <Chip label="Баз приписки"
                  value={s?.bases ?? data.nodes.filter(n => n.base).length}
                  title="Станции, где приписан хотя бы один сотрудник" />
            <Chip label="Рейсов"
                  value={s?.flights ?? data.arcs.reduce((a, x) => a + x.n, 0)}
                  title="Строк листа рейсов со станциями вылета и прилёта" />
            <Chip label="Горизонт"
                  value={s?.days
                    ? `${s.days} сут.`
                    : '—'}
                  title={s?.date_from
                    ? `${fmtDate(s.date_from)} — ${fmtDate(s.date_to)}`
                    : 'В листе рейсов нет колонки даты'} />
            <Chip label="Сотрудников"
                  value={s?.employees ?? data.employees.length} />
            {s?.fleet && Object.keys(s.fleet).length > 0 && (
              <Chip label="Флот"
                    value={Object.entries(s.fleet)
                      .map(([t, n]) => `${t} ${n}`).join(' · ')}
                    title="Рейсов по типу воздушного судна" />
            )}
            {s?.date_from && (
              <span className="text-xs text-cf-text-muted">
                {fmtDate(s.date_from)} — {fmtDate(s.date_to)}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {tabBtn('schema', 'Схема сети')}
            {tabBtn('world', 'Карта мира', !data.geo.complete,
              data.geo.complete
                ? 'Контуры мира по координатам ('
                  + (data.geo.source || 'справочник') + ') — '
                  + 'включение всегда за человеком'
                : 'Карта мира недоступна: нет координат у станций '
                  + data.geo.missing.join(', ')
                  + (data.geo.note ? '. ' + data.geo.note : ''))}
            {tabBtn('crew', `Персонал (${data.employees.length})`)}
            <span className="ml-auto text-xs text-cf-text-muted">
              по замороженной книге данных, без модели
            </span>
          </div>

          {tab !== 'crew' && (
            <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(260px,1fr)]">
              <div>
                {/* Высоту задаёт движок (атрибут height, viewBox по
                    ширине элемента) — CSS-высота дала бы пустые поля
                    сверху и снизу (ревью 05.09). */}
                <svg ref={svgRef}
                     className="w-full rounded-xl border
                                border-cf-border bg-white" />
                <p className="mt-1 text-xs text-cf-text-muted">
                  Кружок — станция, выделенный — база приписки;
                  толщина дуги — по числу рейсов между станциями за
                  горизонт (в обе стороны). Наведите на станцию или
                  дугу — подсказка с числами.
                </p>
              </div>
              <div className="overflow-hidden rounded-xl border
                              border-cf-border">
                <table className="w-full text-sm">
                  <thead className="bg-cf-bg-muted text-xs uppercase
                                    text-cf-text-muted">
                    <tr>
                      <th className="px-2 py-1.5 text-left">Станция</th>
                      <th className="px-2 py-1.5 text-right"
                          title="Вылетов и прилётов за горизонт">
                        Рейсов
                      </th>
                      <th className="px-2 py-1.5 text-right"
                          title="Сотрудников с этой базой приписки">
                        Персонал
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {stations.map(n => (
                      <tr key={n.code}
                          className="border-t border-cf-border">
                        <td className="px-2 py-1.5 font-mono
                                       text-cf-text">
                          {n.code}
                          {n.base && (
                            <span className="ml-1.5 rounded-full
                                             bg-cf-primary/15 px-1.5
                                             py-0.5 text-[10px]
                                             font-semibold
                                             text-cf-primary">
                              база
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-right
                                       text-cf-text">
                          {n.flights ?? '—'}
                        </td>
                        <td className="px-2 py-1.5 text-right
                                       text-cf-text">
                          {n.employees ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.arcs.length > 0 && (
                  <div className="border-t border-cf-border px-2 py-1.5
                                  text-xs text-cf-text-muted">
                    Направлений: {data.arcs.length}
                    {data.arcs[0]?.per_day != null && (
                      <> · самое частое:{' '}
                        {(() => {
                          const top = [...data.arcs]
                            .sort((a, b) => b.n - a.n)[0]
                          return `${top.a}–${top.b} ${top.n} `
                            + `(${top.per_day} в сутки)`
                        })()}
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === 'crew' && (
            <div className="space-y-3">
              {data.by_base && Object.keys(data.by_base).length > 0 && (
                <div className="overflow-x-auto rounded-xl border
                                border-cf-border">
                  <table className="w-full text-sm">
                    <thead className="bg-cf-bg-muted text-xs uppercase
                                      text-cf-text-muted">
                      <tr>
                        <th className="px-2 py-1.5 text-left">База</th>
                        {positions.map(p => (
                          <th key={p} className="px-2 py-1.5 text-right">
                            {p}
                          </th>
                        ))}
                        <th className="px-2 py-1.5 text-right">Всего</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(data.by_base).map(([b, row]) => (
                        <tr key={b} className="border-t border-cf-border">
                          <td className="px-2 py-1.5 font-mono
                                         font-semibold text-cf-text">
                            {b}
                          </td>
                          {positions.map(p => (
                            <td key={p} className="px-2 py-1.5 text-right
                                                   text-cf-text">
                              {row[p] ?? ''}
                            </td>
                          ))}
                          <td className="px-2 py-1.5 text-right
                                         font-semibold text-cf-text">
                            {Object.values(row).reduce((a, x) => a + x, 0)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="flex items-center gap-2">
                <input
                  className="input-field max-w-xs"
                  placeholder="Фильтр: табельный, фамилия, база, должность…"
                  value={filter}
                  onChange={e => setFilter(e.target.value)}
                />
                <span className="text-xs text-cf-text-muted">
                  показано {crew.length} из {data.employees.length}
                </span>
              </div>
              <div className="max-h-[52vh] overflow-y-auto rounded-xl
                              border border-cf-border">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-cf-bg-muted text-xs
                                    uppercase text-cf-text-muted">
                    <tr>
                      <th className="px-2 py-1.5 text-left">Табельный</th>
                      <th className="px-2 py-1.5 text-left">Фамилия И.О.</th>
                      <th className="px-2 py-1.5 text-left">База</th>
                      <th className="px-2 py-1.5 text-left">Должность</th>
                      <th className="px-2 py-1.5 text-left"
                          title="Типы воздушных судов из листа допусков">
                        Допуски
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {crew.map(e => (
                      <tr key={e.id + e.name}
                          className="border-t border-cf-border">
                        <td className="px-2 py-1.5 font-mono text-cf-text">
                          {e.id}
                        </td>
                        <td className="px-2 py-1.5 text-cf-text">{e.name}</td>
                        <td className="px-2 py-1.5 font-mono text-cf-text">
                          {e.base || '—'}
                        </td>
                        <td className="px-2 py-1.5 text-cf-text">
                          {e.position || '—'}
                        </td>
                        <td className="px-2 py-1.5 text-cf-text">
                          {(e.quals || []).join(', ') || '—'}
                        </td>
                      </tr>
                    ))}
                    {crew.length === 0 && (
                      <tr>
                        <td colSpan={5}
                            className="px-2 py-3 text-center text-xs
                                       text-cf-text-muted">
                          {data.employees.length
                            ? 'по фильтру никого'
                            : 'в книге нет листа экипажей'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
