import { useEffect, useRef, useState } from 'react'
import { Cloud, Flag, HardDrive, Scale, ShieldCheck, Wrench, X }
  from 'lucide-react'
import Button from '../common/Button'
import notify from '../common/StyledToast'
import { getAgents, getFilesConfig, listProviders, providerModels,
         putAgents, type AgentConfig, type AgentsConfig,
         type ChannelClass, type ChannelConfig,
         type ProviderInfo } from '../../api'

// Плавающая панель конфигурации LLM-блока (порт AgentConfigPopup
// CodeForge): открывается шестерёнкой НА БЛОКЕ. Три слота модели —
// по классу входных данных (решение владельца 2026-08-18): блок
// обрабатывает каждый входной файл моделью слота его признака.
// Полная независимость ролей (2.09): попап открывается для ЛЮБОЙ
// роли перечня сервера; три исторические — со своими значками, прочие
// — общий значок и название с сервера (label).
const ROLE_META: Record<string, { Icon: typeof Wrench; label: string
                                  gradient: string }> = {
  worker: { Icon: Wrench, label: 'Обработчик',
    gradient: 'from-blue-600 to-indigo-700' },
  auditor: { Icon: ShieldCheck, label: 'Аудитор',
    gradient: 'from-amber-500 to-orange-600' },
  arbiter: { Icon: Scale, label: 'Арбитр',
    gradient: 'from-emerald-500 to-teal-600' },
}
const GENERIC_META = { Icon: Wrench, label: '',
                       gradient: 'from-slate-500 to-slate-700' }

const THINKING = ['(не задано)', 'low', 'medium', 'high', 'max']

const SLOTS: { klass: ChannelClass; label: string; Icon: typeof Cloud
               hint: string }[] = [
  { klass: 'cloud', label: 'Облачная модель', Icon: Cloud,
    hint: 'для входов с признаком «облачная модель»' },
  { klass: 'local', label: 'Локальная модель', Icon: HardDrive,
    hint: 'для входов «только локально» — данные не покидают машину' },
  { klass: 'cloud-country', label: 'Облачная модель внутри страны',
    Icon: Flag,
    hint: 'для входов «облако внутри страны»; пока провайдер не '
      + 'выбран, слот работает локальной моделью (строже, не мягче)' },
]

const POLICY_TITLES: Record<string, string> = {
  local: 'локальная модель',
  'cloud-country': 'облако внутри страны',
  cloud: 'облачная модель',
}

export default function AgentConfigPopup({ runId, role, x, y,
                                           onClose, label }: {
  runId: string
  role: string
  x: number
  y: number
  onClose: () => void
  label?: string
}) {
  const meta = ROLE_META[role]
    || { ...GENERIC_META, label: label || role }
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [cfg, setCfg] = useState<AgentConfig>({})
  const [agents, setAgents] = useState<AgentsConfig>({})
  const [busy, setBusy] = useState(false)
  // МП-1: живые модели ключа по провайдеру (свежие первыми) и
  // новейшая — зашитые подсказки устаревают.
  const [liveModels, setLiveModels] =
    useState<Record<string, string[]>>({})
  const [latest, setLatest] = useState<Record<string, string>>({})
  // Пакет трёх 2026-08-30, п.2: классы файлов прогона решают
  // доступность слотов (датасет-политика: файлы «локально» делают
  // облачные слоты недоступными — их выбор мёртв по построению).
  const [fileClasses, setFileClasses] =
    useState<Set<string> | null>(null)
  const popupRef = useRef<HTMLDivElement>(null)
  const repositioned = useRef(false)

  useEffect(() => {
    (async () => {
      try {
        const [prov, ag, fc] = await Promise.all([
          listProviders(), getAgents(runId),
          getFilesConfig(runId).catch(() => null)])
        setProviders(prov)
        setAgents(ag)
        setCfg(ag[role] || {})
        if (fc?.files) {
          setFileClasses(new Set(Object.values(fc.files)
            .map(f => f.processing || 'local')))
        }
        // Живой перечень — по настроенным облачным провайдерам,
        // параллельно; отказ одного не валит остальных.
        prov.filter(p => p.cloud && p.configured).forEach(async p => {
          try {
            const got = await providerModels(p.name)
            setLiveModels(m => ({ ...m, [p.name]: got.models }))
            if (got.latest) {
              setLatest(l => ({ ...l, [p.name]: got.latest! }))
            }
          } catch { /* живой перечень недоступен — подсказки */ }
        })
      } catch (e) {
        notify.error(String(e))
      }
    })()
  }, [runId, role])

  // Прижатие к краям холста (приём эталона): один раз после монтажа.
  useEffect(() => {
    if (repositioned.current) return
    const el = popupRef.current
    const parent = el?.offsetParent as HTMLElement | null
    if (!el || !parent) return
    repositioned.current = true
    const pr = parent.getBoundingClientRect()
    const er = el.getBoundingClientRect()
    let top = y
    let left = x
    if (er.bottom > pr.bottom - 8) {
      top = Math.max(8, pr.height - er.height - 8)
    }
    if (er.right > pr.right - 8) {
      left = Math.max(8, pr.width - er.width - 8)
    }
    el.style.top = `${top}px`
    el.style.left = `${left}px`
  }, [x, y])

  const chan = (klass: ChannelClass): ChannelConfig =>
    cfg.channels?.[klass] || {}

  const setChan = (klass: ChannelClass,
                   patch: Partial<ChannelConfig>) => {
    setCfg(prev => ({
      ...prev,
      channels: { ...prev.channels,
                  [klass]: { ...prev.channels?.[klass], ...patch } },
    }))
  }

  const cloudProviders = providers.filter(p => p.cloud)

  const save = async () => {
    setBusy(true)
    try {
      await putAgents(runId, { ...agents, [role]: cfg })
      notify.success(`Конфигурация блока «${meta.label}» сохранена`)
      onClose()
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      ref={popupRef}
      className="absolute z-50 flex max-h-[calc(100%-16px)] w-96
                 flex-col gap-2.5 overflow-y-auto rounded-xl border
                 border-cf-border bg-cf-panel p-3 shadow-2xl"
      style={{ left: x, top: y }}
      onClick={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`rounded-lg bg-gradient-to-br p-1.5
                           ${meta.gradient}`}>
            <meta.Icon className="h-4 w-4 text-white" />
          </div>
          <span className="text-sm font-semibold text-cf-text">
            {meta.label}
          </span>
        </div>
        <button onClick={onClose} aria-label="Закрыть настройку"
                title="Закрыть без сохранения"
                className="rounded p-1 text-cf-text-muted
                           transition-colors hover:bg-cf-hover
                           hover:text-cf-text">
          <X className="h-4 w-4" />
        </button>
      </div>
      <p className="text-[11px] text-cf-text-muted">
        Три слота модели — по признаку входных данных: блок
        обрабатывает каждый файл моделью слота его признака; стадии,
        смешивающие все файлы, идут слотом строжайшего признака.
      </p>

      {SLOTS.map(({ klass, label, Icon, hint }) => {
        const c = chan(klass)
        // П.2: слот недоступен, если файлов его класса в прогоне
        // нет — настройка была бы мёртвой (файлы «локально» никогда
        // не поедут облачным слотом по построению каналов).
        const unavailable = fileClasses !== null
          && !fileClasses.has(klass)
        if (unavailable) {
          return (
            <fieldset key={klass}
                      className="rounded-lg border border-cf-border
                                 bg-cf-bg p-2.5 opacity-50">
              <legend className="flex items-center gap-1.5 px-1
                                 text-xs font-semibold
                                 text-cf-text-muted">
                <Icon className="h-3.5 w-3.5" />
                {label}
              </legend>
              <p className="text-[11px] text-cf-text-muted">
                Недоступно: в прогоне нет файлов с признаком
                «{POLICY_TITLES[klass]}» — данные этим слотом не
                обрабатываются по построению.
              </p>
            </fieldset>
          )
        }
        // Пустой провайдер облачного слота = умолчание «Облако
        // (Claude)» — его живой перечень и «новейшая» тоже должны
        // работать (замечание владельца 2026-08-20: без этого у
        // Claude оставались зашитые псевдонимы без fable и версий).
        const effName = klass === 'local' ? 'local'
          : (c.provider || 'cloud')
        const providerInfo = providers.find(p => p.name === effName)
        return (
          <fieldset key={klass}
                    className="rounded-lg border border-cf-border
                               bg-cf-bg p-2.5">
            <legend className="flex items-center gap-1.5 px-1 text-xs
                               font-semibold text-cf-text">
              <Icon className="h-3.5 w-3.5 text-cf-primary" />
              {label}
            </legend>
            <p className="mb-2 text-[11px] text-cf-text-muted">
              {hint}
            </p>
            {klass !== 'local' && (
              <label className="block text-xs text-cf-text-muted"
                     title="Чей канал использует этот слот; ключи
провайдеров — страница «Настройки»">
                Провайдер
                <select className="input-field mt-1"
                        value={c.provider || ''}
                        onChange={e => setChan(klass, {
                          provider: e.target.value || undefined,
                          model: undefined })}>
                  <option value="">
                    {klass === 'cloud'
                      ? 'облачная Claude (по умолчанию)'
                      : '(не выбран — работает локальная модель)'}
                  </option>
                  {(klass === 'cloud-country'
                    // П.2: слоту «внутри страны» — только провайдеры
                    // своей юрисдикции; зарубежные облака недоступны.
                    ? cloudProviders.filter(p => p.in_country)
                    : cloudProviders).map(p => (
                    <option key={p.name} value={p.name}>
                      {p.title}{p.configured ? '' : ' (ключ не задан)'}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="mt-1.5 block text-xs text-cf-text-muted"
                   title="Точное имя модели провайдера; пусто — модель
по умолчанию. Список — живой перечень вашего ключа, свежие первыми">
              Модель
              {/* Живой перечень ключа (МП-1), свежие первыми; пока
                  не подтянулся — зашитые подсказки. Крестик очищает
                  поле: после точного совпадения выпадающий список
                  сворачивается, и выбрать другую модель без очистки
                  неудобно (замечание владельца 2026-08-20). */}
              <span className="relative mt-1 block">
                <input className="input-field w-full pr-7"
                       list={`models-${role}-${klass}`}
                       value={c.model || ''}
                       placeholder={providerInfo?.name
                         && latest[providerInfo.name]
                         ? `(по умолчанию; новейшая: `
                           + `${latest[providerInfo.name]})`
                         : '(по умолчанию)'}
                       onChange={e => setChan(klass, {
                         model: e.target.value || undefined })} />
                {c.model && (
                  <button
                    type="button"
                    aria-label="Очистить модель"
                    title="Очистить поле — снова покажется весь список
моделей"
                    className="absolute right-1.5 top-1/2 -translate-y-1/2
                               rounded p-0.5 text-cf-text-muted
                               hover:bg-cf-hover hover:text-cf-text"
                    onClick={() => setChan(klass,
                                           { model: undefined })}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </span>
              <datalist id={`models-${role}-${klass}`}>
                {(liveModels[providerInfo?.name || '']
                  || providerInfo?.models || []).map(mdl => (
                  <option key={mdl} value={mdl} />
                ))}
              </datalist>
              {providerInfo?.name && latest[providerInfo.name] && (
                <button
                  type="button"
                  className="mt-1 text-[11px] text-cf-primary
                             hover:underline"
                  title="Подставить новейшую полновесную модель ключа
(живое ранжирование по версии и свежести)"
                  onClick={() => setChan(klass, {
                    model: latest[providerInfo.name] })}
                >
                  новейшая: {latest[providerInfo.name]}
                </button>
              )}
            </label>
            {klass !== 'local' && (
              <div className="mt-1.5 grid grid-cols-3 gap-2">
                <label className="block text-xs text-cf-text-muted"
                       title="Сколько модель «думает» перед ответом:
low — быстрее и дешевле, max — тщательнее. OpenAI/Grok —
reasoning_effort, Gemini — thinkingBudget, Claude по ключу API —
extended thinking; канал по подписке (модель «(по умолчанию)») его
игнорирует">
                  Усилие
                  <select className="input-field mt-1"
                          value={c.effort || '(не задано)'}
                          onChange={e => setChan(klass, {
                            effort: e.target.value === '(не задано)'
                              ? undefined : e.target.value })}>
                    {THINKING.map(t => <option key={t}>{t}</option>)}
                  </select>
                </label>
                <label className="block text-xs text-cf-text-muted"
                       title="Случайность ответов модели: 0 —
максимально детерминированно (рекомендуется для формализации), 1 —
максимально разнообразно. Рассуждающие модели температуру отвергают —
канал автоматически повторит запрос без неё; у Claude с включённым
усилием температура не отправляется (API запрещает)">
                  Температура
                  <input type="number" step="0.1" min="0" max="1"
                         className="input-field mt-1"
                         value={c.temperature ?? ''}
                         placeholder="по умолч."
                         onChange={e => setChan(klass, {
                           temperature: e.target.value
                             ? Number(e.target.value) : undefined })} />
                </label>
                <label className="block text-xs text-cf-text-muted"
                       title="Потолок длины одного ответа модели;
пусто — умолчание канала">
                  Макс. токенов
                  <input type="number" className="input-field mt-1"
                         value={c.max_tokens ?? ''}
                         placeholder="по умолч."
                         onChange={e => setChan(klass, {
                           max_tokens: e.target.value
                             ? Number(e.target.value) : undefined })} />
                </label>
              </div>
            )}
          </fieldset>
        )
      })}

      <label className="block text-xs text-cf-text-muted"
             title="Дополнительный текст, добавляемый к штатному
заданию роли в каждом запросе этого блока">
        Своя инструкция блоку
        <textarea className="input-field mt-1" rows={1}
                  value={cfg.instruction || ''}
                  placeholder="необязательно"
                  onChange={e => setCfg({ ...cfg,
                    instruction: e.target.value || undefined })} />
      </label>
      <p className="text-[11px] text-cf-text-muted">
        Провайдер и модель применяются каналом всегда; температура,
        максимум токенов и усилие рассуждения — там, где модель их
        принимает (отвергнутый параметр канал снимает сам и повторяет
        запрос). Ключи провайдеров — страница «Настройки».
      </p>
      <Button size="sm" onClick={save} disabled={busy}
              title="Сохранить слоты этой роли для прогона —
сохранённая конфигурация главнее запасных селектов панели деталей">
        Сохранить конфигурацию
      </Button>
    </div>
  )
}
