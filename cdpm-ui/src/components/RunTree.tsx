import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { Crown, GitBranch, GitCompare, RefreshCw, RotateCcw }
  from 'lucide-react'
import notify from './common/StyledToast'
import Button from './common/Button'
import { compareRuns, getRunTree, getRunTreeView, markGolden, unmarkGolden,
         type RunTreeCompare, type RunTreeExpectations, type RunTreeIndex,
         type RunTreeNode, type RunTreeView } from '../api'

// ── Дерево прогонов (проект «Запись о прогоне», шаг 3) ──
// База прогонов имеет вид дерева, а не списка: корень — отпечатки
// документов правил, данных и ответов на анкеты; узел — прогон; ребро
// — «те же входы, другое объявление» либо «продолжение от артефакта
// родителя». Индекс собирает `vmp/run_tree.py` (`cosmos-run-tree/v1`),
// ручки одни у обеих систем; показ ничего не считает сам — он читает
// узлы, рёбра и готовые сравнения и переводит их словами.
//
// В файле два показа на одном наборе мелочей: `RunTreeCard` — секция
// страницы прогона (путь от корня, соседние ветви, сравнения, золотой)
// и `RunTreeBrowser` — вкладка «Дерево» списка прогонов (корни с
// ветвями). Мелочи общие, чтобы слово «продолжение» на двух страницах
// не расходилось.

/** Заголовок секции — как у прочих секций панели деталей страницы
 *  прогона (линия и воздух сверху, капитель приглушённого цвета). */
const SECTION_H3 = 'mt-4 border-t border-cf-border pt-3 text-xs '
  + 'font-semibold uppercase tracking-wide text-cf-text-muted'

/** Отпечаток коротко: сверяют его целиком (подсказкой), а читают
 *  началом. */
const SHORT = (v?: string | null, n = 12) =>
  v ? `${String(v).slice(0, n)}…` : '—'

/** Секунды словами: до минуты — секундами, дальше — минутами. */
function fmtSeconds(s?: number | null): string {
  if (s === null || s === undefined) return '—'
  if (s < 60) return `${Math.round(s * 10) / 10} с`
  const m = Math.floor(s / 60)
  const rest = Math.round(s - m * 60)
  return rest ? `${m} мин ${rest} с` : `${m} мин`
}

/** Момент времени коротко: день, месяц и часы с минутами. */
function fmtMoment(v?: string | null): string {
  if (!v) return '—'
  const d = new Date(String(v))
  if (Number.isNaN(d.getTime())) return String(v)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getDate())}.${two(d.getMonth() + 1)} `
    + `${two(d.getHours())}:${two(d.getMinutes())}`
}

/** Значение словами — как их печатает `vmp/run_tree.py`: пусто, «да» /
 *  «нет», число, перечень. Длинное значение обрезается: строка таблицы
 *  не должна разъезжаться из-за одного отпечатка. */
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return 'нет'
  if (typeof v === 'boolean') return v ? 'да' : 'нет'
  if (typeof v === 'number') return String(v)
  if (Array.isArray(v)) {
    return v.length ? `[${v.map(x => String(x)).join(', ')}]` : '[]'
  }
  if (typeof v === 'object') {
    const s = JSON.stringify(v)
    return s.length > 90 ? `${s.slice(0, 90)}…` : s
  }
  const s = String(v)
  return s.length > 90 ? `${s.slice(0, 90)}…` : s
}

/** Цвет состояния прогона — тона исходов карточки «Запись о прогоне»:
 *  завершён — зелёный, остановлен — жёлтый, сбой — красный, объявлен и
 *  идёт — приглушённый. */
function stateClass(state?: string | null): string {
  const s = String(state || '')
  if (s === 'завершён') return 'text-emerald-500'
  if (s.startsWith('остановлен')) return 'text-amber-500'
  if (s === 'сбой') return 'text-red-500'
  return 'text-cf-text-muted'
}

/** Вид ребра словами подсказки (`run_tree.EDGES`, 4 слова). */
const EDGE_TITLE: Record<string, string> = {
  'корень': 'первый прогон на этих входах',
  'соседняя ветвь': 'те же входы, другое объявление',
  'изменены ответы': 'тот же корень входов, другой корень с ответами '
    + 'на анкеты',
  'продолжение': 'частичный прогон от артефакта родителя (готовый ВМП, '
    + 'пакет постановки)',
}

/** Поля сравнения словами: путь «поле.поле» переводится по частям,
 *  чужое слово (роль модели, имя задачи решателя) остаётся как есть. */
const FIELD_WORDS: Record<string, string> = {
  purpose: 'цель', contour: 'площадка', catalog: 'каталог',
  catalog_extension: 'расширение каталога', bindings: 'привязки',
  code: 'код', libraries: 'библиотеки', models: 'модели',
  solvers: 'решатели', planned_stages: 'план этапов',
  stop_after: 'остановиться после',
  name: 'имя', version: 'версия', fingerprint: 'отпечаток',
  catalog_version: 'версия каталога',
  catalog_fingerprint: 'отпечаток каталога',
  cdpm_commit: 'коммит CDPM', cosmos_commit: 'коммит COSMOS',
  runner_fingerprint: 'отпечаток раннера',
  packages_sha256: 'отпечаток перечня пакетов',
  model: 'модель', provider: 'провайдер', channel: 'канал',
  effort: 'усилие', engine: 'движок', build_sha256: 'отпечаток сборки',
  spent_limit_s: 'лимит секунд',
  unimproved_spent_limit_s: 'лимит секунд без улучшения',
  moves: 'ходы', seed: 'зерно', step_limit: 'лимит шагов',
  unimproved_step_limit: 'лимит шагов без улучшения',
  deterministic_time: 'детерминированное время',
  accepted_uncovered: 'принятые непокрытые',
}
const fieldWords = (f: string) => String(f).split('.')
  .map(part => FIELD_WORDS[part] || part).join(' · ')

/** Хронометраж сравнения словами. */
const TIMING_WORDS: Record<string, string> = {
  machine_s: 'время машины', waiting_human_s: 'ожидание человека',
  total_s: 'всего',
}

/** Сдвиг числа словами: без сдвига — так и сказано. */
function deltaWords(d?: number | null): string {
  if (d === null || d === undefined) return ''
  if (d === 0) return ' (без сдвига)'
  return ` (${d > 0 ? '+' : ''}${d})`
}

/** Токены узла кратко: вход/выход суммой по моделям; канал без учёта
 *  честно говорит «не всё считается», а не подсовывает ноль. */
function tokensWords(n: RunTreeNode): string {
  const rows = Object.values(n.tokens || {})
  if (!rows.length) return '—'
  let input = 0
  let output = 0
  let counted = true
  for (const r of rows) {
    input += r.input_tokens || 0
    output += r.output_tokens || 0
    if (r.counted === false) counted = false
  }
  return `${input}/${output}${counted ? '' : ' · не всё считается'}`
}

/** Вердикты узла кратко: «задача: вердикт» через точку с запятой. */
function verdictsWords(n: RunTreeNode): string {
  const rows = Object.entries(n.verdicts || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!rows.length) return '—'
  return rows.map(([k, v]) => `${k}: ${fmtVal(v)}`).join('; ')
}

/** Имя папки прогона панели: страница прогона живёт по имени папки
 *  (`run-NNNN`), а номер записи у прогона, объявленного раннером
 *  наборов, свой (вида `20260910-132100-9c6f40ad`). Имя папки берётся
 *  из пути найденной записи; не нашлось — узел остаётся подписью без
 *  ссылки: ссылка в никуда хуже её отсутствия. */
const PANEL_RUN = /^run-\d+$/
function panelRunOf(n: RunTreeNode): string | null {
  const parts = String(n.path || '').split(/[\\/]/).filter(Boolean)
  const folder = parts.length >= 2 ? parts[parts.length - 2] : ''
  if (PANEL_RUN.test(folder)) return folder
  if (PANEL_RUN.test(String(n.run_id || ''))) return String(n.run_id)
  return null
}

/** Номер прогона ссылкой на его страницу — либо подписью, если прогон
 *  не панельный (частный прогон COSMOS, прогон с другой машины). */
function NodeLink({ node, className, children }: {
  node: RunTreeNode
  className?: string
  children?: ReactNode
}) {
  const to = panelRunOf(node)
  const text = children ?? (node.run_id || 'номер не назван')
  if (!to) {
    return (
      <span className={className}
            title={`Запись: ${node.path || 'путь не назван'} — страницы `
              + 'этого прогона на панели нет (прогон объявлен другой '
              + 'системой либо лежит вне каталога прогонов панели)'}>
        {text}
      </span>
    )
  }
  return (
    <Link to={`/runs/${to}`} className={clsx('hover:underline', className)}
          title={`Открыть прогон ${to}`}>
      {text}
    </Link>
  )
}

/** Признак золотого прогона значком. */
function GoldenMark({ node }: { node: RunTreeNode }) {
  if (!node.golden?.confirmed) return null
  return (
    <span className="inline-flex items-center gap-1 rounded bg-amber-500/15
                     px-1.5 py-0.5 text-[11px] font-semibold text-amber-500"
          title={`Золотой прогон: подтвердил ${
            node.golden.by || 'кто не назван'}`
            + (node.golden.at ? `, ${fmtMoment(node.golden.at)}` : '')
            + (node.golden.words ? ` — «${node.golden.words}»` : '')}>
      <Crown className="h-3 w-3" /> золотой
    </span>
  )
}

/** Ребро узла: вид и чем ветвь отличается от той, к кому ведёт. */
function EdgeChips({ node }: { node: RunTreeNode }) {
  const edge = node.edge
  if (!edge) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className="rounded bg-cf-border/60 px-1.5 py-0.5 text-[11px]
                       text-cf-text-muted"
            title={(EDGE_TITLE[edge.kind] || '')
              + (edge.words ? `\n${edge.words}` : '')}>
        {edge.kind}
        {edge.to ? ` → ${edge.to}` : ''}
      </span>
      {(edge.differs || []).map(d => (
        <span key={d}
              className="rounded bg-indigo-500/15 px-1.5 py-0.5 text-[11px]
                         text-indigo-400"
              title="Чем эта ветвь отличается от той, к кому ведёт ребро">
          {d}
        </span>
      ))}
    </span>
  )
}

/** Сравнение двух прогонов таблицами: объявление, вердикты, время,
 *  токены, счётчики. Слова сравнения — сервера: показ их не
 *  пересказывает, а даёт прочитать целиком. */
function CompareView({ cmp }: { cmp: RunTreeCompare }) {
  const changed = cmp.declaration?.changed || []
  const same = cmp.declaration?.same || []
  const verdicts = Object.entries(cmp.results?.verdicts || {})
  const timing = Object.entries(cmp.results?.timing || {})
  const tokens = Object.entries(cmp.results?.tokens || {})
  const counters = Object.entries(cmp.results?.counters || {})
    .filter(([, r]) => r.a !== r.b)
  return (
    <div className="space-y-1 text-xs">
      <p className="text-cf-text">
        <span className="font-mono">{cmp.a || '—'}</span>
        {' → '}
        <span className="font-mono">{cmp.b || '—'}</span>
        {' · '}
        <span className={cmp.same_root
          ? 'text-cf-text-muted' : 'text-amber-500'}
              title={cmp.same_root
                ? 'Входы те же: прогоны сравнимы между собой'
                : 'Прогоны стоят на разных входах — сравниваются '
                  + 'разные задачи'}>
          {cmp.same_root ? 'корень один' : 'КОРНИ РАЗНЫЕ'}
        </span>
      </p>
      <p className="text-cf-text-muted">
        объявление: изменено полей {changed.length}, совпало {same.length}
      </p>
      {changed.length === 0 ? (
        <p className="text-cf-text-muted">объявления совпадают полностью</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-[520px] border-collapse text-[11px]">
            <thead>
              <tr className="text-left text-cf-text-muted">
                <th className="py-1 pr-2 font-normal">поле объявления</th>
                <th className="py-1 pr-2 font-normal">было</th>
                <th className="py-1 font-normal">стало</th>
              </tr>
            </thead>
            <tbody>
              {changed.map(row => (
                <tr key={row.field}
                    className="border-t border-cf-border align-top">
                  <td className="py-1 pr-2 text-cf-text" title={row.field}>
                    {fieldWords(row.field)}
                  </td>
                  <td className="py-1 pr-2 text-cf-text-muted">
                    {fmtVal(row.a)}
                  </td>
                  <td className="py-1 text-cf-text">{fmtVal(row.b)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {verdicts.length > 0 && (
        <ul className="space-y-0.5">
          {verdicts.map(([task, row]) => (
            <li key={task} className="flex justify-between gap-2">
              <span className="text-cf-text-muted">вердикт {task}</span>
              <span className={clsx('text-right',
                                    row.same ? 'text-cf-text'
                                      : 'font-medium text-amber-500')}>
                {fmtVal(row.a)} → {fmtVal(row.b)}
                {row.same ? '' : ' — расхождение'}
              </span>
            </li>
          ))}
        </ul>
      )}
      <ul className="space-y-0.5">
        {timing
          .filter(([, row]) => row.a !== null || row.b !== null)
          .map(([field, row]) => (
            <li key={field} className="flex justify-between gap-2">
              <span className="text-cf-text-muted">
                {TIMING_WORDS[field] || field}
              </span>
              <span className="text-right text-cf-text">
                {fmtSeconds(row.a)} → {fmtSeconds(row.b)}
                {deltaWords(row.delta)}
              </span>
            </li>
          ))}
      </ul>
      {tokens.length > 0 && (
        <ul className="space-y-0.5">
          {tokens.map(([key, row]) => (
            <li key={key} className="flex justify-between gap-2"
                title="Расход моделей в токенах: вход и выход по каждой
роли и модели">
              <span className="text-cf-text-muted">токены {key}</span>
              <span className="text-right text-cf-text">
                вход {fmtVal(row.a?.input_tokens)} →{' '}
                {fmtVal(row.b?.input_tokens)}
                {deltaWords(row.delta?.input_tokens)}, выход{' '}
                {fmtVal(row.a?.output_tokens)} →{' '}
                {fmtVal(row.b?.output_tokens)}
                {deltaWords(row.delta?.output_tokens)}
                {row.only_in
                  ? ` — этой модели у прогона ${
                    row.only_in === 'a' ? 'б' : 'а'} нет`
                  : (row.counted ? '' : ' — не считается')}
              </span>
            </li>
          ))}
        </ul>
      )}
      {counters.length > 0 && (
        <div>
          <div className="text-cf-text-muted"
               title="Счётчики сравниваются справочно: число гуляет по
чтению модели — расхождение счётчика повод посмотреть, а не вердикт">
            счётчики (справочно)
          </div>
          <ul className="space-y-0.5">
            {counters.map(([name, row]) => (
              <li key={name} className="flex justify-between gap-2">
                <span className="text-cf-text-muted">{name}</span>
                <span className="text-right text-cf-text">
                  {fmtVal(row.a)} → {fmtVal(row.b)}{deltaWords(row.delta)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(cmp.words || []).length > 0 && (
        <details className="text-[11px] text-cf-text-muted">
          <summary className="cursor-pointer">
            сравнение словами целиком
          </summary>
          <pre className="mt-1 whitespace-pre-wrap font-mono">
            {(cmp.words || []).join('\n')}
          </pre>
        </details>
      )}
    </div>
  )
}

/** Сравнение с ожиданиями набора: их пишет хозяин записи при закрытии
 *  (`results.comparison.expectations`). Состав полей у хозяина свой,
 *  поэтому известные названы словами, а остальные показаны как есть. */
function ExpectationsView({ e }: { e: RunTreeExpectations }) {
  const words = Array.isArray(e.words)
    ? e.words.map(w => String(w))
    : (e.words ? [String(e.words)] : [])
  const rest = Object.entries(e)
    .filter(([k]) => k !== 'words' && k !== 'kind' && k !== 'ref'
      && k !== 'verdict')
  return (
    <div className="space-y-0.5 text-xs">
      <p className="text-cf-text">
        {e.kind ? `${e.kind}` : 'ожидания набора'}
        {e.ref ? ` · ${e.ref}` : ''}
        {e.verdict ? ` · вердикт: ${e.verdict}` : ''}
      </p>
      {words.map((w, i) => (
        <p key={i} className="text-cf-text-muted">{w}</p>
      ))}
      {rest.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-2">
          <span className="text-cf-text-muted">{k}</span>
          <span className="text-right text-cf-text">{fmtVal(v)}</span>
        </div>
      ))}
    </div>
  )
}

/** Блок «Золотой прогон»: признак, кто и какими словами подтвердил, и
 *  кнопки подтверждения и снятия. Кнопки показываются ТОЛЬКО у
 *  завершённого прогона площадки Prod (решение владельца 6.3: признак
 *  ставится словом владельца после приёмки на Prod), у остальных стоит
 *  подпись — почему кнопок нет. */
function GoldenBlock({ run, node, onDone }: {
  /** Имя прогона панели: ручка золотого зовётся по нему — оно же имя
   *  папки, в которой лежит запись. Номер записи (`node.run_id`) у
   *  прогона, объявленного раннером наборов, свой, и звать ручку по
   *  нему было бы звать не тот прогон. */
  run: string
  node: RunTreeNode
  onDone: () => void
}) {
  const [form, setForm] = useState<'' | 'mark' | 'unmark'>('')
  const [by, setBy] = useState('')
  const [words, setWords] = useState('')
  const [busy, setBusy] = useState(false)
  const golden = node.golden || { confirmed: false, by: null, at: null,
                                  words: null }
  // Замки признака: прогон завершён и площадка Prod. Приёмку судит
  // гейт трёх наборов, а показ судит то, что видит, — и кнопок у
  // прогона, которому образцом быть рано, не показывает вовсе.
  const allowed = node.state === 'завершён' && node.contour === 'prod'

  const close = () => { setForm(''); setBy(''); setWords('') }

  const doMark = async () => {
    setBusy(true)
    try {
      await markGolden(run, by.trim(), words.trim())
      notify.success(`Прогон ${node.run_id || run} подтверждён золотым`)
      close()
      onDone()
    } catch (e) {
      notify.error(`Золотым не назван: ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const doUnmark = async () => {
    setBusy(true)
    try {
      await unmarkGolden(run, by.trim())
      notify.success(`Признак золотого снят у прогона ${
        node.run_id || run}`)
      close()
      onDone()
    } catch (e) {
      notify.error(`Признак не снят: ${String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1 text-xs">
      <div className="text-cf-text-muted">золотой прогон</div>
      <p className={golden.confirmed
        ? 'font-medium text-amber-500' : 'text-cf-text-muted'}>
        {golden.confirmed
          ? `подтверждён — ${golden.by || 'кто не назван'}`
            + (golden.at ? `, ${fmtMoment(golden.at)}` : '')
          : 'не подтверждён'}
      </p>
      {golden.confirmed && golden.words && (
        <p className="text-cf-text">слова подтверждения: «{golden.words}»</p>
      )}
      {!allowed && (
        <p className="text-cf-text-muted"
           title="Признак прогона-образца ставится только словом владельца
после приёмки на Prod; незавершённый прогон образцом не бывает">
          Золотой подтверждается после приёмки на Prod
          {` (сейчас: состояние «${node.state || 'не названо'}», площадка «${
            node.contour || 'не названа'}»)`}
        </p>
      )}
      {allowed && form === '' && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={busy}
                  title="Подтвердить прогон образцом: с ним по умолчанию
сравниваются новые ветви того же корня. Подтверждает человек —
подпись и слова обязательны"
                  onClick={() => setForm('mark')}>
            <Crown className="h-3.5 w-3.5" /> Подтвердить золотым
          </Button>
          {golden.confirmed && (
            <Button size="sm" variant="secondary" disabled={busy}
                    title="Снять признак: подтверждение отозвано (нашёлся
прогон лучше либо признак поставлен по ошибке)"
                    onClick={() => setForm('unmark')}>
              Снять признак
            </Button>
          )}
        </div>
      )}
      {allowed && form !== '' && (
        <div className="space-y-1 rounded-lg border border-cf-border p-2">
          <label className="block text-cf-text-muted">
            {form === 'mark' ? 'кто подтверждает' : 'кто снимает'}
            <input className="input-field mt-1" value={by}
                   onChange={e => setBy(e.target.value)}
                   placeholder="имя или почта — подтверждает человек" />
          </label>
          {form === 'mark' && (
            <label className="block text-cf-text-muted">
              слова подтверждения
              <input className="input-field mt-1" value={words}
                     onChange={e => setWords(e.target.value)}
                     placeholder="чем этот прогон образец" />
            </label>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={busy || !by.trim()
                      || (form === 'mark' && !words.trim())}
                    title={form === 'mark'
                      ? 'Поставить признак золотого: подпись без слов не '
                        + 'подпись'
                      : 'Снять признак золотого'}
                    onClick={() => {
                      if (form === 'mark') doMark()
                      else doUnmark()
                    }}>
              {form === 'mark' ? 'Подтвердить' : 'Снять'}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy}
                    onClick={close}>
              Отмена
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

/** Отказы обхода списком: читатель обязан видеть не только найденное,
 *  но и НЕ прочитанное. */
function RefusalsList({ index }: { index: RunTreeIndex }) {
  const rows = index.refusals || []
  if (!rows.length) return null
  return (
    <div className="text-xs">
      <div className="text-cf-text-muted"
           title="Что найдено обходом, но не прочитано: не JSON, не по
словарю записи, папка не читается, номер прогона встретился дважды">
        отказы обхода ({rows.length})
      </div>
      <ul className="space-y-0.5">
        {rows.map((r, i) => (
          <li key={`${r.path}/${i}`}>
            <span className="font-mono text-cf-text-muted"
                  title={r.path || ''}>
              {r.path || 'путь не назван'}
            </span>
            {(r.words || []).map((w, j) => (
              <span key={j} className="block text-cf-error">! {w}</span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Строка итога индекса: когда собран и что в нём. */
function IndexLine({ index }: { index: RunTreeIndex }) {
  const runs = (index.roots || []).reduce(
    (n, r) => n + (r.runs || []).length, 0) + (index.orphans || []).length
  const golden = (index.roots || []).reduce(
    (n, r) => n + (r.runs || []).filter(x => x.golden?.confirmed).length, 0)
  return (
    <p className="text-[11px] text-cf-text-muted"
       title="Индекс — снимок, а не хранилище: записи живут в своих
папках, индекс перестраивается по событию и кнопкой">
      дерево собрано {fmtMoment(index.built_at)} · корней{' '}
      {(index.roots || []).length} · прогонов {runs} · золотых {golden}
      {(index.orphans || []).length
        ? ` · без корня ${(index.orphans || []).length}` : ''}
      {(index.refusals || []).length
        ? ` · отказов ${(index.refusals || []).length}` : ''}
    </p>
  )
}

// ── Секция страницы прогона ──

/** «Дерево прогонов» на странице прогона: путь от корня, соседние
 *  ветви, сравнение с золотым (с предыдущим — по выбору), сравнение с
 *  соседом и признак золотого. Секция только читает дерево; меняет она
 *  одно — признак золотого, и только рукой человека. */
export function RunTreeCard({ runId, runState }: {
  runId: string
  /** Смена линии прогона — повод перечитать дерево (узел растёт). */
  runState?: string | null
}) {
  const [index, setIndex] = useState<RunTreeIndex | null>(null)
  const [view, setView] = useState<RunTreeView | null>(null)
  const [absent, setAbsent] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState<'золотой' | 'предыдущий'>('золотой')
  const [peer, setPeer] = useState('')
  const [peerCompare, setPeerCompare] = useState<RunTreeCompare | null>(null)
  const [peerBusy, setPeerBusy] = useState(false)

  const load = useCallback(async (rebuild = false) => {
    setBusy(true)
    try {
      // Сперва индекс (при перестройке — обход диска), затем взгляд по
      // прогону: иначе взгляд читал бы индекс, пока его переписывают.
      setIndex(await getRunTree(rebuild))
      const got = await getRunTreeView(runId)
      if ('absent' in got) {
        setView(null)
        setAbsent(got.words)
      } else {
        setView(got)
        setAbsent('')
      }
      setError('')
    } catch (e) {
      setIndex(null)
      setView(null)
      setAbsent('')
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [runId])
  useEffect(() => { load() }, [load, runState])
  // Сравнение с соседом относится к показанному прогону: сменился
  // прогон — прежний выбор и его таблица уходят.
  useEffect(() => { setPeer(''); setPeerCompare(null) }, [runId])

  const node = view?.node
  const cmp = mode === 'золотой'
    ? view?.compare_golden : view?.compare_previous
  const siblings = view?.siblings || []

  const comparePeer = async () => {
    if (!peer || !node?.run_id) return
    setPeerBusy(true)
    try {
      setPeerCompare(await compareRuns(node.run_id, peer))
    } catch (e) {
      setPeerCompare(null)
      notify.error(`Сравнение не собрано: ${String(e)}`)
    } finally {
      setPeerBusy(false)
    }
  }

  return (
    <div className="space-y-1.5">
      <h3 className={SECTION_H3}
          title="Дерево прогонов (cosmos-run-tree/v1): корень — отпечатки
документов правил, данных и ответов на анкеты; узел — прогон; ребро —
«те же входы, другое объявление» либо «продолжение от артефакта
родителя». Прогоны одного корня сравнимы между собой, новые ветви
сравниваются с золотым прогоном по умолчанию">
        <GitBranch className="mr-1 inline h-3.5 w-3.5" />
        Дерево прогонов
      </h3>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={busy}
                title="Перечитать дерево из индекса: обход диска не
делается"
                onClick={() => { load(false) }}>
          <RotateCcw className="h-3.5 w-3.5" /> Обновить
        </Button>
        <Button size="sm" variant="secondary" disabled={busy}
                title="Перестроить индекс обходом каталога прогонов:
запись нового прогона попадёт в дерево сразу, не дожидаясь события"
                onClick={() => { load(true) }}>
          <RefreshCw className="h-3.5 w-3.5" /> Обновить дерево
        </Button>
      </div>

      {error && (
        <p className="text-xs text-cf-error">
          Дерево не построено: {error}
        </p>
      )}
      {index && <IndexLine index={index} />}
      {!error && !view && (
        <p className="text-xs text-cf-text-muted">
          {busy ? 'Дерево читается…'
            : absent
              ? `Прогона в дереве нет: ${absent}`
              : 'Прогона в дереве нет: у него ещё нет записи о прогоне '
                + 'либо индекс не перестроен — нажмите «Обновить дерево».'}
        </p>
      )}

      {node && (
        <>
          {/* Путь от корня: входы → родители → прогон. */}
          <div className="text-xs">
            <div className="text-cf-text-muted"
                 title="Корень — отпечаток документов правил и данных;
корень с ответами — он же с ревизией ответов на анкеты. Смена одного
ответа даёт ветвь «изменены ответы», а не новое дерево">
              путь от корня
            </div>
            <p className="text-cf-text">
              авиакомпания {view?.path?.root?.airline || 'не названа'} ·
              корень{' '}
              <span className="font-mono"
                    title={view?.path?.root?.root_sha256 || ''}>
                {SHORT(view?.path?.root?.root_sha256)}
              </span>
              {(view?.path?.root?.root_answers_sha256 || []).length > 1
                ? ` · ветвей по ответам ${
                  (view?.path?.root?.root_answers_sha256 || []).length}`
                : ''}
            </p>
            <ul className="space-y-0.5">
              {(view?.path?.root?.documents?.rules || []).map((d, i) => (
                <li key={`r-${d.name}-${i}`}
                    className="flex justify-between gap-2 font-mono">
                  <span className="truncate" title={d.name || ''}>
                    правила: {d.name || '—'}
                  </span>
                  <span className="text-cf-text-muted"
                        title={d.sha256 || ''}>{SHORT(d.sha256)}</span>
                </li>
              ))}
              {(view?.path?.root?.documents?.data || []).map((d, i) => (
                <li key={`d-${d.name}-${i}`}
                    className="flex justify-between gap-2 font-mono">
                  <span className="truncate" title={d.name || ''}>
                    данные: {d.name || '—'}
                  </span>
                  <span className="text-cf-text-muted"
                        title={d.sha256 || ''}>{SHORT(d.sha256)}</span>
                </li>
              ))}
              <li className="flex justify-between gap-2 font-mono">
                <span>ответы на анкеты</span>
                <span className="text-cf-text-muted"
                      title={node.root_answers_sha256 || ''}>
                  {node.root_answers_sha256
                    ? SHORT(node.root_answers_sha256)
                    : 'ревизий ещё нет'}
                </span>
              </li>
            </ul>
            <ol className="mt-1 space-y-0.5">
              {(view?.path?.runs || []).map((n, i) => (
                <li key={`${n.run_id}-${i}`}
                    className="flex flex-wrap items-center gap-1.5">
                  <span className="text-cf-text-muted">
                    {i === 0 ? '' : '→'}
                  </span>
                  <NodeLink node={n}
                            className={clsx('font-mono',
                                            n.run_id === node.run_id
                                              ? 'font-semibold text-cf-text'
                                              : 'text-cf-primary')} />
                  <span className={stateClass(n.state)}>
                    {n.state || 'состояние не названо'}
                  </span>
                  <EdgeChips node={n} />
                  <GoldenMark node={n} />
                </li>
              ))}
            </ol>
          </div>

          {/* Соседние ветви того же корня. */}
          <div className="text-xs">
            <div className="text-cf-text-muted"
                 title="Прогоны того же корня: те же входы, другое
объявление. Колонка «чем отличается» — крупные части объявления:
модели, решатели, параметры решателей, каталог, привязки, код,
библиотеки, цель, площадка, план">
              соседние ветви ({siblings.length})
            </div>
            {siblings.length === 0 ? (
              <p className="text-cf-text-muted">
                прогон на этих входах один: сравнивать не с кем
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-[720px] border-collapse text-[11px]">
                  <thead>
                    <tr className="text-left text-cf-text-muted">
                      <th className="py-1 pr-2 font-normal">номер записи</th>
                      <th className="py-1 pr-2 font-normal">кто объявил</th>
                      <th className="py-1 pr-2 font-normal">цель</th>
                      <th className="py-1 pr-2 font-normal">
                        чем отличается
                      </th>
                      <th className="py-1 pr-2 font-normal">вердикты</th>
                      <th className="py-1 pr-2 font-normal">время</th>
                      <th className="py-1 pr-2 font-normal">токены</th>
                      <th className="py-1 font-normal">золотой</th>
                    </tr>
                  </thead>
                  <tbody>
                    {siblings.map((n, i) => (
                      <tr key={`${n.run_id}-${i}`}
                          className="border-t border-cf-border align-top">
                        <td className="py-1 pr-2">
                          <NodeLink node={n}
                                    className="font-mono text-cf-primary" />
                          <span className="block text-cf-text-muted"
                                title={n.created_at || ''}>
                            {fmtMoment(n.created_at)} ·{' '}
                            <span className={stateClass(n.state)}>
                              {n.state || 'состояние не названо'}
                            </span>
                          </span>
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {n.created_by || '—'}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {n.purpose || '—'}
                          {n.contour ? ` · ${n.contour}` : ''}
                        </td>
                        <td className="py-1 pr-2">
                          <EdgeChips node={n} />
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {verdictsWords(n)}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {fmtSeconds(n.timing?.total_s)}
                        </td>
                        <td className="py-1 pr-2 text-cf-text-muted">
                          {tokensWords(n)}
                        </td>
                        <td className="py-1">
                          {n.golden?.confirmed
                            ? <GoldenMark node={n} />
                            : <span className="text-cf-text-muted">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Сравнение: с золотым по умолчанию, с предыдущим — по выбору. */}
          <div className="text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-cf-text-muted">сравнение</span>
              {(['золотой', 'предыдущий'] as const).map(m => (
                <button key={m} type="button"
                        onClick={() => setMode(m)}
                        aria-pressed={mode === m}
                        title={m === 'золотой'
                          ? 'С золотым прогоном того же корня — так '
                            + 'сравниваются новые ветви по умолчанию'
                          : 'С предыдущим прогоном того же корня — '
                            + 'сдвиги чисел от прогона к прогону'}
                        className={clsx(
                          'rounded-full border px-2 py-0.5 text-[11px]',
                          mode === m
                            ? 'border-cf-primary text-cf-primary'
                            : 'border-cf-border text-cf-text-muted')}>
                  {m === 'золотой' ? 'с золотым' : 'с предыдущим'}
                </button>
              ))}
            </div>
            {cmp ? <CompareView cmp={cmp} /> : (
              <p className="text-cf-text-muted">
                {mode === 'золотой'
                  ? 'Золотого прогона у этого корня нет (либо золотой — '
                    + 'это он сам): сравнивать не с чем.'
                  : 'Прогон первый на этом корне: предыдущего нет.'}
              </p>
            )}
          </div>

          {/* Сравнение с соседом по выбору. */}
          {siblings.length > 0 && (
            <div className="text-xs">
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-cf-text-muted">
                  сосед для сравнения
                  <select className="input-field mt-1" value={peer}
                          onChange={e => setPeer(e.target.value)}>
                    <option value="">— выберите прогон —</option>
                    {siblings.map((n, i) => (
                      <option key={`${n.run_id}-${i}`}
                              value={n.run_id || ''}>
                        {n.run_id} · {fmtMoment(n.created_at)} ·{' '}
                        {n.state || 'состояние не названо'}
                        {(n.edge?.differs || []).length
                          ? ` · ${(n.edge?.differs || []).join(', ')}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <Button size="sm" variant="secondary"
                        disabled={peerBusy || !peer}
                        title="Сравнить показанный прогон с выбранным
соседом: что менялось в объявлении и что изменилось в итогах"
                        onClick={() => { comparePeer() }}>
                  <GitCompare className="h-3.5 w-3.5" /> Сравнить с соседом
                </Button>
              </div>
              {peerCompare && <CompareView cmp={peerCompare} />}
            </div>
          )}

          {/* Сравнение с ожиданиями набора — из записи, без сборки. */}
          {view?.expectations && (
            <div className="text-xs">
              <div className="text-cf-text-muted"
                   title="Ожидания набора из его манифеста: их пишет
хозяин записи при закрытии прогона — судимые и несудимые ключи и
расхождения">
                сравнение с ожиданиями набора
              </div>
              <ExpectationsView e={view.expectations} />
            </div>
          )}

          <GoldenBlock run={runId} node={node}
                       onDone={() => { load(true) }} />
        </>
      )}

      {index && <RefusalsList index={index} />}
    </div>
  )
}

// ── Вкладка «Дерево» списка прогонов ──

/** Дерево целиком: корни со входами, под корнем — ветви с рёбрами и
 *  отличиями. Щелчок по узлу ведёт на страницу прогона. */
export function RunTreeBrowser() {
  const [index, setIndex] = useState<RunTreeIndex | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (rebuild = false) => {
    setBusy(true)
    try {
      setIndex(await getRunTree(rebuild))
      setError('')
    } catch (e) {
      setIndex(null)
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const roots = index?.roots || []

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => { load(true) }}
          disabled={busy}
          title="Перестроить индекс обходом каталога прогонов: записи
новых прогонов попадут в дерево сразу"
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm
                     text-cf-text-muted transition-colors hover:bg-cf-border
                     hover:text-cf-text disabled:opacity-50
                     dark:hover:bg-gray-700 dark:hover:text-white"
        >
          <RefreshCw className="h-4 w-4" />
          Обновить дерево
        </button>
        {index && <IndexLine index={index} />}
      </div>

      {error && (
        <p className="text-sm text-cf-error">Дерево не построено: {error}</p>
      )}
      {!error && index && roots.length === 0
        && (index.orphans || []).length === 0 && (
        <p className="text-sm text-cf-text-muted">
          Дерево пусто: записей о прогонах обход не нашёл. Записи
          появляются у прогонов, объявленных с шага 2 проекта, — нажмите
          «Обновить дерево» после первого такого прогона.
        </p>
      )}

      {roots.map((root, i) => (
        <div key={`${root.root_sha256}-${i}`}
             className="rounded-xl border border-gray-700 bg-gray-800 p-4">
          <div className="mb-1 flex flex-wrap items-center gap-3">
            <h2 className="text-base font-semibold text-white">
              {root.airline || 'авиакомпания не названа'}
            </h2>
            <span className="rounded bg-gray-700/50 px-2 py-0.5 font-mono
                             text-xs text-gray-300"
                  title={`Корень дерева: ${root.root_sha256 || 'нет'}`}>
              корень {SHORT(root.root_sha256)}
            </span>
            <span className="text-xs text-gray-400">
              ветвей {(root.runs || []).length}
            </span>
            {(root.root_answers_sha256 || []).length > 1 && (
              <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs
                               text-amber-400"
                    title="У прогонов этого корня встретилось несколько
ревизий ответов на анкеты: на дереве есть ветвь «изменены ответы»">
                ветвей по ответам {(root.root_answers_sha256 || []).length}
              </span>
            )}
          </div>
          <p className="mb-2 text-xs text-gray-400"
             title="Входы корня по первой записи: имена — человеку для
узнавания, отпечатки — для сверки">
            правила:{' '}
            {(root.documents?.rules || []).length
              ? (root.documents?.rules || []).slice(0, 3)
                .map(d => `${d.name || '—'} ${SHORT(d.sha256, 8)}`).join(', ')
                + ((root.documents?.rules || []).length > 3
                  ? ` и ещё ${(root.documents?.rules || []).length - 3}` : '')
              : 'документов нет'}
            {' · данные: '}
            {(root.documents?.data || []).length
              ? (root.documents?.data || []).slice(0, 3)
                .map(d => `${d.name || '—'} ${SHORT(d.sha256, 8)}`).join(', ')
                + ((root.documents?.data || []).length > 3
                  ? ` и ещё ${(root.documents?.data || []).length - 3}` : '')
              : 'нет'}
          </p>
          <ul className="space-y-1">
            {(root.runs || []).map((n, j) => (
              <li key={`${n.run_id}-${j}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1
                             border-t border-gray-700 pt-1 text-xs">
                <NodeLink node={n}
                          className="font-mono text-sm text-indigo-300" />
                <span className="text-gray-400" title={n.created_at || ''}>
                  {fmtMoment(n.created_at)}
                </span>
                <span className={stateClass(n.state)}>
                  {n.state || 'состояние не названо'}
                </span>
                <EdgeChips node={n} />
                <span className="text-gray-400">
                  {n.purpose || 'цель не названа'}
                  {n.contour ? ` · ${n.contour}` : ''}
                  {n.catalog?.version ? ` · каталог ${n.catalog.version}` : ''}
                </span>
                <GoldenMark node={n} />
              </li>
            ))}
          </ul>
        </div>
      ))}

      {(index?.orphans || []).length > 0 && (
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-4">
          <h2 className="mb-1 text-base font-semibold text-white"
              title="Прогоны, у которых объявление не назвало отпечатков
входов и родителя в перечне нет. Молчать о них нельзя — они есть">
            Без корня ({(index?.orphans || []).length})
          </h2>
          <ul className="space-y-1">
            {(index?.orphans || []).map((n, j) => (
              <li key={`${n.run_id}-${j}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1
                             border-t border-gray-700 pt-1 text-xs">
                <NodeLink node={n}
                          className="font-mono text-sm text-indigo-300" />
                <span className="text-gray-400">
                  {fmtMoment(n.created_at)}
                </span>
                <span className={stateClass(n.state)}>
                  {n.state || 'состояние не названо'}
                </span>
                <span className="text-gray-400">
                  {n.edge?.words || 'корень дерева не назван'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {index && (index.refusals || []).length > 0 && (
        <div className="rounded-xl border border-gray-700 bg-gray-800 p-4">
          <RefusalsList index={index} />
        </div>
      )}
    </div>
  )
}
