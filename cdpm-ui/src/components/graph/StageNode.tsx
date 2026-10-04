import { memo, useEffect, useRef, useState } from 'react'
import { Handle, NodeToolbar, Position } from '@xyflow/react'

// Панель действий над блоком: задержка скрытия после ухода курсора и
// видимый отступ до блока (внутри обёртки панели, не зазор портала).
const TOOLBAR_HIDE_DELAY_MS = 250
const TOOLBAR_GAP_PX = 6
import {
  BarChart3, CheckCircle2, ClipboardCheck, Clock, FileInput,
  FileOutput, Loader2, Pause, Play, Scale, Send, Settings2,
  ShieldCheck, SkipForward, Snowflake, Square, Wrench, XCircle,
  BookOpenCheck,
} from 'lucide-react'
import clsx from 'clsx'

// Блок стадии конвейера CDPM — визуальный язык AgentNode CodeForge:
// градиентная заливка ВСЕГО блока, рамка цвета роли, свечение и пульс
// активного состояния, иконка в полупрозрачной плашке, модель на
// блоке, счётчики поверх градиента. Текст на градиенте — белый (это
// фиксированная поверхность роли, не тематическая).
export type StageKind = 'documents' | 'freeze' | 'worker' | 'artifacts'
  | 'auditor' | 'arbiter' | 'output'
  | 'data-files' | 'data_checks' | 'data_report'
export type StageStatus = 'idle' | 'waiting' | 'working' | 'done'
  | 'error' | 'paused'

export interface StageNodeData extends Record<string, unknown> {
  kind: StageKind
  statusNode: StageStatus
  statusText?: string
  model?: string
  llm?: boolean
  counters?: { label: string; value: string }[]
  // Блок приглушён, пока его содержимое не готово («Итог» до конца
  // цикла — замечание владельца 2026-08-21); яркость возвращается
  // вместе с готовностью.
  dimmed?: boolean
  // Пакет трёх 2026-08-30, п.3: работа блока в прогоне ЗАКОНЧЕНА —
  // зелёный контур. С 07.09.2026 (слово владельца) совпадает с
  // statusNode='done' у всех блоков: готовность анкеты контуром не
  // показывается.
  fullyDone?: boolean
  // Настройка модели живёт ВНУТРИ блока (как у блоков CodeForge):
  // шестерёнка на блоке открывает плавающую панель конфигурации.
  onConfigClick?: (e: React.MouseEvent) => void
  // Панель действий над правым верхним краем блока (слово владельца
  // 06.09: «иконки Play для одного блока и перемотка для сквозного
  // цикла над границей, как в лучших практиках» — NodeToolbar React
  // Flow, как в n8n и Airflow): те же обработчики, что в панели
  // деталей, панель деталей остаётся для параметров и ссылок.
  actions?: NodeAction[]
  // Панель видна всегда (ближайший доступный шаг), не только при
  // наведении или выделении.
  toolbarAlways?: boolean
}

export type NodeActionId = 'play' | 'through' | 'pause' | 'resume'
  | 'stop' | 'send'

export interface NodeAction {
  id: NodeActionId
  // Подпись (подсказка при наведении и имя для экранного диктора).
  label: string
  onClick: () => void
  disabled?: boolean
  // Почему недоступна — словами, в подсказке.
  reason?: string
}

const ACTION_ICON: Record<NodeActionId, typeof Play> = {
  play: Play, through: SkipForward, pause: Pause, resume: Play,
  stop: Square, send: Send,
}

const STAGE_CONFIG: Record<StageKind, {
  Icon: typeof FileInput; gradient: string; border: string
  glow: string; label: string; tip: string
}> = {
  documents: { Icon: FileInput, label: 'Документы',
    gradient: 'from-slate-600 to-slate-700',
    border: 'border-slate-500', glow: 'shadow-slate-500/20',
    tip: 'Исходные документы правил авиакомпании. Шестерёнка у файла '
       + '— политика обработки (куда можно отправлять содержимое) и '
       + 'примечание. Клик — детали и режим 2' },
  freeze: { Icon: Snowflake, label: 'Заморозка',
    gradient: 'from-cyan-600 to-sky-700',
    border: 'border-cyan-400', glow: 'shadow-cyan-500/30',
    tip: 'Фиксация исходников: SHA-256 каждого файла, разбор на '
       + 'клаузы с якорями, сверка с оригиналом байт-в-байт. Вся '
       + 'дальнейшая работа — только по замороженной копии' },
  worker: { Icon: Wrench, label: 'Обработчик',
    gradient: 'from-blue-600 to-indigo-700',
    border: 'border-blue-400', glow: 'shadow-blue-500/30',
    tip: 'Извлечение правил: модель читает клаузы чанками и '
       + 'предлагает строки свода, код проверяет каждую контрактом. '
       + 'Клик — запуск и параметры' },
  artifacts: { Icon: BookOpenCheck, label: 'Свод + Анкета',
    gradient: 'from-purple-600 to-violet-700',
    border: 'border-purple-400', glow: 'shadow-purple-500/30',
    tip: 'Результаты: формализованный свод, анкета вопросов '
       + 'авиакомпании, машинная модель, отбраковка. Здесь же — '
       + 'загрузка ответов анкеты (свод получает новую ревизию)' },
  auditor: { Icon: ShieldCheck, label: 'Аудитор',
    gradient: 'from-amber-500 to-orange-600',
    border: 'border-amber-400', glow: 'shadow-amber-500/30',
    tip: 'Аудит-цикл: вторая модель перечитывает все клаузы против '
       + 'свода и пишет замечания, обработчик правит или отклоняет; '
       + 'до чистоты, стагнации или лимита. Клик — запуск и обмены' },
  arbiter: { Icon: Scale, label: 'Арбитр',
    gradient: 'from-emerald-500 to-teal-600',
    border: 'border-emerald-400', glow: 'shadow-emerald-500/30',
    tip: 'Третья независимая модель решает тупики цикла: обработчик '
       + 'прав / аудитор прав (исполняется обязательно) / в анкету — '
       + 'спорное чтение решает авиакомпания' },
  output: { Icon: FileOutput, label: 'Итог',
    gradient: 'from-green-600 to-emerald-700',
    border: 'border-green-400', glow: 'shadow-green-500/30',
    tip: 'Итог аудит-цикла: вердикт и финальная версия свода со '
       + 'всеми принятыми исправлениями' },
  'data-files': { Icon: FileInput, label: 'Операционные данные',
    gradient: 'from-slate-600 to-slate-700',
    border: 'border-slate-500', glow: 'shadow-slate-500/20',
    tip: 'Таблицы данных (.xlsx/.csv): ростеры, допуски, журналы. '
       + 'Клик — загрузка и заморозка; политика файла — шестерёнка' },
  data_checks: { Icon: ClipboardCheck, label: 'Проверки данных',
    gradient: 'from-sky-600 to-cyan-700',
    border: 'border-sky-400', glow: 'shadow-sky-500/30',
    tip: 'Строки свода компилируются в машинные проверки и '
       + 'прогоняются по замороженным данным детерминированно; '
       + 'неподъёмная строка — честный статус с причиной' },
  data_report: { Icon: BarChart3, label: 'Отчёт 3.3',
    gradient: 'from-lime-600 to-green-700',
    border: 'border-lime-400', glow: 'shadow-lime-500/30',
    tip: 'Отчёт о качестве операционных данных: находки по '
       + 'серьёзности и классам; спорное сверяется моделью и '
       + 'раскладывается на нарушение / исключение / к владельцу' },
}

// Подсказки счётчиков-чипов (директива владельца 2026-08-20:
// подсказки на всех элементах; сокращения расшифровываются).
const COUNTER_TIPS: Record<string, string> = {
  'клауз': 'Абзацев-клауз в замороженных документах',
  'оригиналов сверено': 'Файлов, чья замороженная копия сверена с '
    + 'оригиналом',
  'правил': 'Строк формализованного свода',
  'вопросов': 'Открытых вопросов анкеты',
  'отбраковано': 'Кандидатов, отклонённых контрактом (с причинами)',
  'ревизия': 'Номер ревизии свода — растёт с каждым применением '
    + 'ответов авиакомпании',
  'применено': 'Ответов, применённых к своду',
  'ждёт обработчика': 'Ответов, требующих точечной правки '
    + 'обработчиком',
  'не разобрано': 'Ответов, которые не удалось сопоставить развилке — '
    + 'нужна ручная формулировка',
  'обменов': 'Раундов аудит-цикла',
  '(Б, К, С)': 'Весомые замечания последнего обмена: Блокирующие, '
    + 'Критические, Серьёзные',
  'обработчик прав': 'Тупиков, закрытых арбитром в пользу обработчика',
  'аудитор прав': 'Тупиков, где арбитр велел исполнить замечание',
  'в анкету': 'Тупиков, переданных на решение авиакомпании',
  'не разрешено': 'Споров, по которым арбитр после повторного запроса '
    + 'не дал одного решения (дубль, противоречие или молчание): '
    + 'решением это не считается, находка остаётся живой — запустите '
    + 'арбитраж ещё раз',
  'прогнано': 'Машинных проверок, исполненных по данным',
  'не прогнано': 'Проверок, не исполненных — с честной причиной',
  'находок': 'Расхождений данных с правилами свода',
  'записей': 'Строк данных, рассмотренных проверками',
  'Листов': 'Листов в замороженных таблицах',
  'Строк': 'Строк в замороженных таблицах',
}

// Статусная строка — палитра AgentNode эталона: цвет говорит о
// состоянии, не о роли блока.
const STATUS_CONFIG: Record<StageStatus, {
  Indicator: typeof Clock; className: string; label: string
  spin?: boolean
}> = {
  idle: { Indicator: Clock, className: 'text-gray-300',
    label: 'Ожидание' },
  waiting: { Indicator: Clock, className: 'text-yellow-400',
    label: 'Ждёт предыдущей стадии' },
  working: { Indicator: Loader2, className: 'text-blue-300',
    label: 'В работе…', spin: true },
  done: { Indicator: CheckCircle2, className: 'text-green-400',
    label: 'Готово' },
  error: { Indicator: XCircle, className: 'text-red-400',
    label: 'Ошибка' },
  // Пауза — честное состояние ПС-1: без пульса «в работе», жёлтый
  // знак паузы; продолжение — с той же контрольной точки.
  paused: { Indicator: Pause, className: 'text-yellow-300',
    label: 'На паузе' },
}

function StageNode({ data, selected }: {
  data: StageNodeData; selected?: boolean
}) {
  const cfg = STAGE_CONFIG[data.kind]
  const st = STATUS_CONFIG[data.statusNode]
  const active = data.statusNode === 'working'
  // Наведение держит панель действий открытой и над самой панелью
  // (она рисуется порталом вне блока — иначе мигала бы на переходе).
  const [hover, setHover] = useState(false)
  // Дефект с демо 06.09 (владелец: «кнопки над блоками пропадают, когда
  // курсор ведёшь к ним»): между блоком и панелью был зазор, а уход
  // курсора с блока прятал панель мгновенно — курсор не успевал дойти.
  // Теперь скрытие отложено (таймер снимается при входе на панель), а
  // зазора нет: панель стоит вплотную, отступ — внутри её обёртки.
  const hideTimer = useRef<number | null>(null)
  const show = () => {
    if (hideTimer.current !== null) {
      window.clearTimeout(hideTimer.current)
      hideTimer.current = null
    }
    setHover(true)
  }
  const hideLater = () => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
    hideTimer.current = window.setTimeout(() => {
      hideTimer.current = null
      setHover(false)
    }, TOOLBAR_HIDE_DELAY_MS)
  }
  useEffect(() => () => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
  }, [])
  const actions = data.actions || []
  const showToolbar = actions.length > 0 || Boolean(data.onConfigClick)
  const toolbarVisible = showToolbar
    && (Boolean(selected) || hover || Boolean(data.toolbarAlways))
  return (
    <div
      title={cfg.tip}
      onMouseEnter={show}
      onMouseLeave={hideLater}
      className={clsx(
        'relative w-[220px] cursor-pointer rounded-xl border-2',
        // Без hover:scale — рост блока под курсором сдвигал геометрию
        // и тоже ронял наведение на пути к панели действий.
        'transition-shadow duration-300 ease-out',
        'hover:shadow-xl shadow-lg', cfg.glow,
        data.dimmed && 'opacity-40 saturate-50',
        active && 'cf-force-motion',
        active ? 'border-white/70'
          : data.statusNode === 'error' ? 'border-red-400'
          : data.fullyDone && data.statusNode === 'done'
            ? 'border-green-400'
          : cfg.border,
        selected && 'ring-2 ring-white ring-offset-2'
          + ' ring-offset-gray-900',
      )}
      style={active ? {
        animation: 'cf-border-pulse 1.5s ease-in-out infinite',
        boxShadow: '0 0 15px rgba(var(--cf-pulse),0.4), '
          + '0 0 30px rgba(var(--cf-pulse),0.2)',
      } : data.statusNode === 'error' ? {
        boxShadow: '0 0 15px rgba(248,113,113,0.4), '
          + '0 0 30px rgba(248,113,113,0.2)',
      } : data.fullyDone && data.statusNode === 'done' ? {
        // Завершённость отличима и от ролевых зелёных рамок
        // («Итог» зелёный по роли): свечение — только у done.
        boxShadow: '0 0 12px rgba(74,222,128,0.45), '
          + '0 0 24px rgba(74,222,128,0.2)',
      } : undefined}
    >
      {active && (
        <div
          className="cf-glow-overlay absolute -inset-1 rounded-xl"
          style={{ animation: 'cf-border-glow 1.5s ease-in-out infinite',
                   filter: 'blur(4px)' }}
        />
      )}
      <div className={clsx('absolute inset-0 rounded-xl opacity-90',
                           'bg-gradient-to-br', cfg.gradient)} />
      {active && (
        <div className={clsx('absolute inset-0 animate-pulse rounded-xl',
                             'opacity-50 bg-gradient-to-br',
                             cfg.gradient)} />
      )}

      {showToolbar && (
        <NodeToolbar isVisible={toolbarVisible} position={Position.Top}
                     align="end" offset={0}>
          {/* Обёртка с нижним отступом — «мост» между панелью и блоком:
              отступ принадлежит обёртке, поэтому курсор над ним не
              покидает панель (пустой зазор портала событий не ловил). */}
          <div
            style={{ paddingBottom: TOOLBAR_GAP_PX }}
            onMouseEnter={show}
            onMouseLeave={hideLater}
          >
          <div
            className="nodrag nopan flex items-center gap-1 rounded-lg
                       border border-gray-600 bg-gray-900/95 p-1
                       shadow-lg"
            role="toolbar"
            aria-label={`Действия блока «${cfg.label}»`}
          >
            {actions.map(a => {
              const Icon = ACTION_ICON[a.id]
              const primary = a.id === 'play' || a.id === 'send'
                || a.id === 'resume'
              return (
                <button
                  key={a.id}
                  type="button"
                  disabled={a.disabled}
                  aria-label={a.label}
                  title={a.disabled && a.reason
                    ? `${a.label} — ${a.reason}` : a.label}
                  onClick={e => {
                    e.stopPropagation()
                    if (!a.disabled) a.onClick()
                  }}
                  className={clsx(
                    'rounded-md p-1.5 text-white transition-colors',
                    'disabled:cursor-not-allowed disabled:opacity-40',
                    a.id === 'stop'
                      ? 'hover:bg-red-500/70'
                      : primary && !a.disabled
                        ? 'bg-indigo-500 hover:bg-indigo-400'
                        : 'hover:bg-white/20')}
                >
                  <Icon className="h-4 w-4" />
                </button>
              )
            })}
            {data.llm && data.onConfigClick && (
              <button
                type="button"
                onClick={e => {
                  e.stopPropagation()
                  data.onConfigClick?.(e)
                }}
                aria-label="Настроить модель блока"
                title="Настроить модель блока"
                className="rounded-md p-1.5 text-white transition-colors
                           hover:bg-white/20"
              >
                <Settings2 className="h-4 w-4" />
              </button>
            )}
          </div>
          </div>
        </NodeToolbar>
      )}
      <Handle type="target" position={Position.Left}
              className="!h-3 !w-3 !border-2 !border-gray-700
                         !bg-white" />
      <div className="relative flex min-h-[140px] flex-col p-4">
        <div className="mb-2 flex items-center gap-3">
          <div className={clsx('rounded-lg bg-white/20 p-2',
                               'backdrop-blur-sm',
                               active && 'animate-pulse')}>
            <cfg.Icon className="h-5 w-5 text-white" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-bold leading-tight text-white">
              {cfg.label}
            </h3>
            {data.model && (
              <p className="truncate text-xs font-medium text-white/80"
                 title={data.model}>
                {data.model}
              </p>
            )}
          </div>
          {/* Шестерёнка в шапке — только у блоков без панели действий
              (у остальных она переехала в панель над блоком). */}
          {data.llm && !showToolbar && (
            <button
              onClick={e => {
                e.stopPropagation()
                data.onConfigClick?.(e)
              }}
              aria-label="Настроить модель блока"
              title="Настроить модель блока"
              className="rounded-lg bg-white/10 p-1.5 transition-all
                         hover:bg-white/30"
            >
              <Settings2 className="h-4 w-4 text-white" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-1.5 text-xs"
             title={`Состояние стадии: ${data.statusText || st.label}`}>
          <st.Indicator className={clsx('h-3.5 w-3.5', st.className,
                                        st.spin && 'animate-spin')} />
          <span className={clsx('truncate font-medium', st.className)}>
            {data.statusText || st.label}
          </span>
        </div>

        {/* Метрики — чипы за разделителем, как у AgentNode эталона:
            каждая метрика в собственной полупрозрачной плашке. */}
        {(data.counters || []).length > 0 && (
          <div className="mt-auto flex flex-wrap items-center gap-1.5
                          border-t border-white/20 pt-2">
            {(data.counters || []).map(c => (
              <div key={c.label}
                   className="flex items-center gap-1 rounded
                              bg-white/5 px-1.5 py-0.5"
                   title={COUNTER_TIPS[c.label] || c.label}>
                <span className="text-xs font-semibold text-white/90">
                  {c.value}
                </span>
                <span className="text-[10px] text-white/60">
                  {c.label}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right}
              className="!h-3 !w-3 !border-2 !border-gray-700
                         !bg-white" />
      {/* Вертикальные коннекторы — для связи лент (свод → проверки
          данных). */}
      <Handle type="source" position={Position.Bottom} id="bottom"
              className="!h-3 !w-3 !border-2 !border-gray-700
                         !bg-white" />
      <Handle type="target" position={Position.Top} id="top"
              className="!h-3 !w-3 !border-2 !border-gray-700
                         !bg-white" />
    </div>
  )
}

export default memo(StageNode)
