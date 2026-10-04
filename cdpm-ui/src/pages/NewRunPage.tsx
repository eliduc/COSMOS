import { useEffect, useRef, useState,
         type DragEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import notify from '../components/common/StyledToast'
import { FileText, Loader2, Snowflake, Upload, X } from 'lucide-react'
import clsx from 'clsx'
import { createRun, getDeclarationPreview, modelSystemWords,
         MODELS_BOTH_SYSTEMS_TITLE,
         type DeclarationPreview, type KitScope } from '../api'

const ACCEPT = '.doc,.docx,.md,.xlsx,.pdf,.json,.jpg,.jpeg,.png'

// Изображения (ПИ-1): распознаются зрительной моделью при заморозке.
const isImage = (name: string) => /\.(jpe?g|png)$/i.test(name)

// ── Объявление прогона (гейт шага 2 проекта «Запись о прогоне») ──
// Прогон не создаётся без объявления: цель, кто запускает, откуда
// берутся ответы на анкеты, до какого этапа идём. Объявимую часть
// среды (площадка, каталог, привязки, модели, код, библиотеки) панель
// показывает ДО кнопки «Заморозить» — вместе со словами гейта, если
// объявить нечего.

/** Этапы плана словами: ключ словаря записи → подпись формы. */
const STAGE_WORDS: Record<string, string> = {
  create: 'создание и заморозка',
  data: 'данные',
  extract: 'извлечение',
  data_run: 'линия данных',
  cycle: 'сквозной цикл',
  answers: 'ответы',
  package: 'пакет постановки',
}
const stageWords = (s: string) => STAGE_WORDS[s] || s

/** Площадка словами: имя контура записи → как её называют в работе. */
const CONTOUR_WORDS: Record<string, string> = {
  local: 'местная машина (local)',
  test: 'Test — площадка проверки',
  stage: 'Stage — предпродовая площадка',
  prod: 'Prod — рабочая площадка',
}

/** Короткий отпечаток: первые 12 знаков SHA-256 — полный в подсказке. */
const shortSha = (v?: string | null) =>
  v ? `${String(v).slice(0, 12)}…` : '—'

/** Строка объявления: подпись слева, значение справа. */
function DeclRow({ label, value, title }: {
  label: string; value: ReactNode; title?: string
}) {
  return (
    <div className="flex justify-between gap-3" title={title}>
      <dt className="shrink-0 text-gray-400">{label}</dt>
      <dd className="text-right text-white">{value}</dd>
    </div>
  )
}

// Новый прогон — хром NewSessionPage CodeForge: узкий контейнер,
// крупный заголовок с подзаголовком, тёмные панели-секции, первичная
// индиго-кнопка.
export default function NewRunPage() {
  const [files, setFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [customer, setCustomer] = useState('')
  const [kit, setKit] = useState<KitScope>({ dataClass: '', llmScope: '', basis: '' })
  // Облако для изображений решает ДОПУСК комплекта (05.09): классы
  // «синтетический» и «открытый» либо явный допуск any разрешают его
  // сами, остальное — только локальные модели. Галочка ПОКАЗЫВАЕТ
  // решение, а не спрашивает его (вопрос владельца на репетиции 05.09:
  // «зачем галочка, если класс уже разрешает любые модели»).
  const cloudByScope = kit.dataClass === 'synthetic'
    || kit.dataClass === 'open' || kit.llmScope === 'any'
  const inputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()

  // Объявление прогона: поля формы и предполётная сверка среды.
  // Умолчания приходят с сервера (цель «рабочий», кто запускает — почта
  // из заголовка Access либо «панель CDPM»); пока сверка не пришла,
  // поля пусты и в объявление не шлются — сервер подставит своё.
  const [purpose, setPurpose] = useState('')
  const [createdBy, setCreatedBy] = useState('')
  // Имя, введённое человеком, повторная сверка не затирает.
  const createdByTouched = useRef(false)
  const [answerPolicy, setAnswerPolicy] = useState('')
  const [stopAfter, setStopAfter] = useState('')
  const [preview, setPreview] = useState<DeclarationPreview | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)

  // Сверка читается при открытии формы и следом за сменой класса
  // данных и допуска: они решают, законна ли раскладка моделей.
  // Основание допуска — текст, поэтому перечитывается с задержкой,
  // а не на каждую букву.
  const { dataClass: kitClass, llmScope: kitScope, basis: kitBasis } = kit
  useEffect(() => {
    let alive = true
    const timer = window.setTimeout(() => {
      setPreviewBusy(true)
      getDeclarationPreview({ dataClass: kitClass, llmScope: kitScope,
                              basis: kitBasis })
        .then(got => {
          if (!alive) return
          setPreview(got)
          setPreviewError('')
          // Умолчания сервера ставятся один раз: выбор человека
          // повторная сверка не затирает.
          setPurpose(p => p || got.defaults?.purpose || '')
          setAnswerPolicy(p => p || got.defaults?.answer_policy || '')
          setStopAfter(p => p || got.defaults?.stop_after || '')
          if (!createdByTouched.current) setCreatedBy(got.defaults?.created_by || '')
        })
        .catch(e => {
          if (!alive) return
          // Сервер прежней версии без сверки — блок молчит словами
          // ошибки, кнопка остаётся доступной: гейт сервера всё равно
          // рассудит объявление при создании.
          setPreview(null)
          setPreviewError(String(e))
        })
        .finally(() => { if (alive) setPreviewBusy(false) })
    }, 300)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [kitClass, kitScope, kitBasis])

  const stages = preview?.choices?.stages
    || preview?.defaults?.planned_stages || []
  // План этапов = путь до этапа остановки включительно: «остановиться
  // после извлечения» означает, что дальше по плану не идём.
  const plannedStages = (() => {
    const all = preview?.defaults?.planned_stages || []
    const i = stopAfter ? all.indexOf(stopAfter) : -1
    return i >= 0 ? all.slice(0, i + 1) : all
  })()
  const gateRefused = Boolean(preview && !preview.ok)
  // Объявимая часть среды: у отказавшего гейта отдельных полей может и
  // не быть — блок читает их бережно и говорит «не объявлен» словами.
  const env = preview?.declaration
  const envModels = env?.models || []

  const addFiles = (incoming: FileList | File[]) => {
    const fresh = Array.from(incoming)
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name))
      return [...prev, ...fresh.filter(f => !names.has(f.name))]
    })
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    addFiles(e.dataTransfer.files)
  }

  const freeze = async () => {
    if (!files.length) {
      notify.error('Добавьте хотя бы один документ')
      return
    }
    setBusy(true)
    try {
      // Флаг облака для изображений не шлётся: сервер судит по допуску
      // комплекта (класс данных / явный допуск), галочка — витрина.
      // Объявление прогона — рядом: гейт судит его до рождения папки.
      const got = await createRun(files, false, customer, kit, {
        purpose, createdBy, answerPolicy,
        plannedStages, stopAfter,
      })
      if (got.state === 'заморожено') {
        notify.success(
          `${got.run}: заморожено — клауз `
          + `${got.freeze_counters?.clauses ?? '?'}`)
        navigate(`/runs/${got.run}`)
      } else {
        // Отказ заморозки — форма остаётся на месте с документами и
        // причиной словами (04.09 на Проде отказ уходил на страницу
        // прогона без файлов и читался как молчание).
        notify.error(`${got.run}: ${got.state} — `
          + (got.error || (got.freeze_problems || []).join('; ')
             || 'причина не названа'))
      }
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8">
          <h1 className="mb-2 text-3xl font-bold text-cf-text">
            Новый прогон
          </h1>
          <p className="text-cf-text-muted">
            Загрузите документы правил авиакомпании и объявите прогон —
            модуль заморозит документы с манифестом SHA-256 и поведёт
            по конвейеру
          </p>
        </div>

        <div className="space-y-4">
          <div
            role="button"
            tabIndex={0}
            aria-label="Добавить документы"
            onClick={() => inputRef.current?.click()}
            onKeyDown={e => {
              if (e.key === 'Enter') inputRef.current?.click()
            }}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={clsx(
              'flex cursor-pointer flex-col items-center justify-center',
              'gap-2 rounded-xl border-2 border-dashed bg-gray-800 p-10',
              'text-center transition-colors',
              dragOver
                ? 'border-indigo-500 bg-indigo-600/10'
                : 'border-gray-600 hover:border-indigo-500/70',
            )}
          >
            <Upload className="h-8 w-8 text-gray-400" />
            <p className="text-sm text-white">
              Перетащите документы правил сюда или нажмите, чтобы выбрать
            </p>
            <p className="text-xs text-gray-400">
              Форматы первой очереди: doc, docx, md, xlsx, pdf, json,
              jpg, jpeg, png
            </p>
            <input ref={inputRef} type="file" multiple accept={ACCEPT}
                   className="hidden"
                   onChange={e => {
                     if (e.target.files) addFiles(e.target.files)
                     e.target.value = ''
                   }} />
          </div>

          {files.length > 0 && (
            <div className="space-y-2 rounded-xl border border-gray-700
                            bg-gray-800 p-4">
              {files.map(f => (
                <div key={f.name}
                     className="flex items-center justify-between gap-2
                                rounded-lg border border-gray-700
                                bg-gray-900/40 px-3 py-2">
                  <span className="flex min-w-0 items-center gap-2
                                   font-mono text-xs text-white"
                        title={f.name}>
                    <FileText className="h-4 w-4 shrink-0
                                         text-gray-400" />
                    <span className="truncate">{f.name}</span>
                  </span>
                  <button
                    aria-label={`Убрать файл ${f.name}`}
                    title="Убрать файл из списка (до заморозки)"
                    className="rounded p-1 text-gray-400
                               transition-colors hover:bg-gray-700
                               hover:text-red-400"
                    onClick={() => setFiles(prev =>
                      prev.filter(x => x.name !== f.name))}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {files.some(f => isImage(f.name)) && (
            <label
              className="flex items-start gap-2 rounded-xl
                         border border-gray-700 bg-gray-800 p-4 text-sm
                         text-white"
              title="Изображения распознаются зрительной моделью при
заморозке: локальной, если она настроена, иначе облачной — облако
разрешает допуск комплекта (класс данных или явный допуск), а не
галочка"
            >
              <input
                type="checkbox"
                className="mt-0.5 accent-indigo-600"
                checked={cloudByScope}
                disabled
                readOnly
              />
              <span>
                {cloudByScope
                  ? 'Облако для изображений разрешено классом данных'
                  : 'Облако для изображений запрещено допуском '
                    + '«только локальные модели»'}
                <span className="block text-xs text-gray-400">
                  {cloudByScope
                    ? 'Без локальной зрительной модели изображения '
                      + 'распознает облачная модель роли «Зрение». '
                      + 'Решает допуск комплекта, отдельного разрешения '
                      + 'не нужно.'
                    : 'Изображения распознает только локальная '
                      + 'зрительная модель; если она не настроена — '
                      + 'честный отказ заморозки. Облако откроет класс '
                      + '«синтетический»/«открытый» или допуск any '
                      + 'с основанием.'}
                </span>
              </span>
            </label>
          )}

          <label className="block text-sm text-cf-text-muted"
                 title="Название пойдёт в шапки свода и анкеты; без
него извлечение не запустится. Задаётся здесь, на первом экране —
менять потом можно в панели обработчика">
            Название авиакомпании
            <input
              className="input-field mt-1"
              value={customer}
              placeholder="например: «Полярис» (PLR)"
              onChange={e => setCustomer(e.target.value)}
            />
          </label>

          <label className="block text-sm text-cf-text-muted"
                 title="Признак допуска моделей хранится у комплекта:
синтетический и открытый классы разрешают любые модели, проприетарный
(данные авиакомпании) — только локальные, если авиакомпания не дала
согласие. Без класса прогон идёт только на локальных моделях.">
            Класс данных комплекта
            <select className="input-field mt-1" value={kit.dataClass}
                    onChange={e => setKit({ ...kit,
                      dataClass: e.target.value as KitScope['dataClass'] })}>
              <option value="">не указан — только локальные модели</option>
              <option value="synthetic">синтетический — любые модели</option>
              <option value="open">открытый — любые модели</option>
              <option value="proprietary">проприетарный (данные авиакомпании) — только локальные</option>
            </select>
          </label>
          {kit.dataClass === 'proprietary' && (
            <label className="block text-sm text-cf-text-muted"
                   title="Согласие авиакомпании на облачные модели: допуск
any требует основания — документ и дата">
              Согласие авиакомпании на облако (основание)
              <select className="input-field mt-1" value={kit.llmScope ?? ''}
                      onChange={e => setKit({ ...kit,
                        llmScope: e.target.value as KitScope['llmScope'] })}>
                <option value="">нет — только локальные модели</option>
                <option value="any">есть — любые модели (укажите основание)</option>
              </select>
              {kit.llmScope === 'any' && (
                <input className="input-field mt-1" value={kit.basis ?? ''}
                       placeholder="документ, дата (например: письмо от 01.09.2026, п. 3)"
                       onChange={e => setKit({ ...kit, basis: e.target.value })} />
              )}
            </label>
          )}

          {/* ── Объявление прогона (гейт шага 2) ──
              Никакой прогон не начинается без объявления параметров
              (слово владельца 10.09.2026): цель, кто запускает, откуда
              ответы на анкеты, до какого этапа идём — и объявимая часть
              среды, видная ДО кнопки «Заморозить». */}
          <div className="space-y-3 rounded-xl border border-gray-700
                          bg-gray-800 p-4">
            <div>
              <h2 className="text-sm font-semibold text-white">
                Объявление прогона
              </h2>
              <p className="text-xs text-gray-400">
                Параметры прогона объявляются до старта: они лягут в
                голову записи о прогоне и больше не изменятся —
                по ним прогон воспроизводится.
              </p>
            </div>

            <label className="block text-sm text-cf-text-muted"
                   title="Зачем этот прогон: приёмка (проверка перед
раскатом), демонстрация, регресс (повтор набора), исследование или
рабочий прогон. Цель попадает в голову записи и в дерево прогонов">
              Цель прогона
              <select className="input-field mt-1" value={purpose}
                      onChange={e => setPurpose(e.target.value)}>
                {(preview?.choices?.purposes || [purpose || 'рабочий'])
                  .map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
              </select>
            </label>

            <label className="block text-sm text-cf-text-muted"
                   title="Кто объявил прогон: имя или почта. Предзаполнено
почтой входа (заголовок Cloudflare Access), если вход по личности есть,
иначе «панель CDPM». Это же имя стоит актором у записей журнала панели">
              Кто запускает
              <input
                className="input-field mt-1"
                value={createdBy}
                placeholder="имя или почта"
                onChange={e => {
                  createdByTouched.current = true
                  setCreatedBy(e.target.value)
                }}
              />
            </label>

            <label className="block text-sm text-cf-text-muted"
                   title="Откуда берутся ответы на анкеты: «человек» —
ответы вносит аналитик, «книга» — ответы подаются готовой книгой
(так работает раннер наборов). Признак хранится в голове записи">
              Ответы на анкеты
              <select className="input-field mt-1" value={answerPolicy}
                      onChange={e => setAnswerPolicy(e.target.value)}>
                {(preview?.choices?.answer_policies
                  || [answerPolicy || 'человек']).map(p => (
                    <option key={p} value={p}>{p}</option>
                  ))}
              </select>
            </label>

            <label className="block text-sm text-cf-text-muted"
                   title="До какого этапа идёт прогон: план этапов
обрывается на выбранном. Умолчание — «пакет постановки», полный путь
панели">
              Остановиться после
              <select className="input-field mt-1" value={stopAfter}
                      onChange={e => setStopAfter(e.target.value)}>
                {(stages.length ? stages : [stopAfter || 'package'])
                  .map(s => (
                    <option key={s} value={s}>{stageWords(s)}</option>
                  ))}
              </select>
              {plannedStages.length > 0 && (
                <span className="mt-1 block text-xs text-gray-400">
                  План этапов:{' '}
                  {plannedStages.map(stageWords).join(' → ')}
                </span>
              )}
            </label>

            {/* Объявимая часть среды: её панель знает сама. */}
            <div className="space-y-1 border-t border-gray-700 pt-3
                            text-xs">
              {previewBusy && !preview && (
                <p className="text-gray-400">Сверка среды…</p>
              )}
              {previewError && (
                <p className="text-amber-400">
                  Объявимую часть среды показать не удалось:{' '}
                  {previewError}. Гейт всё равно рассудит объявление
                  при создании прогона.
                </p>
              )}
              {preview && (
                <dl className="space-y-0.5">
                  <DeclRow
                    label="площадка"
                    title="Контур, на котором идёт прогон: местная
машина, Test, Stage или Prod"
                    value={CONTOUR_WORDS[env?.contour?.name || '']
                      || env?.contour?.name || 'не объявлена'} />
                  <DeclRow
                    label="каталог шаблонов"
                    title={`отпечаток каталога: ${
                      env?.catalog?.fingerprint || 'нет'}`}
                    value={env?.catalog?.version
                      ? `${env.catalog.version} · `
                        + shortSha(env.catalog.fingerprint)
                      : 'не объявлен'} />
                  <DeclRow
                    label="привязки COSMOS"
                    title={`файл привязок: ${
                      env?.bindings?.path || 'не найден'}`}
                    value={env?.bindings?.catalog_version
                      ? `каталог ${env.bindings.catalog_version}`
                      : 'не объявлены'} />
                  <DeclRow
                    label="код"
                    title="Коммит кода панели: рецепт повторения"
                    value={(env?.code?.cdpm_commit || '')
                      .slice(0, 12) || 'не объявлен'} />
                  <DeclRow
                    label="библиотеки"
                    title={`отпечаток перечня библиотек: ${
                      env?.libraries?.packages_sha256 || 'нет'}`}
                    value={env?.libraries?.python
                      ? `python ${env.libraries.python} · `
                        + shortSha(env.libraries.packages_sha256)
                      : 'не объявлены'} />
                </dl>
              )}
              {preview && (
                <div className="pt-1" title={MODELS_BOTH_SYSTEMS_TITLE}>
                  <div className="text-gray-400">модели по ролям</div>
                  {envModels.length === 0 ? (
                    <p className="text-gray-400">раскладка не объявлена</p>
                  ) : (
                    <ul className="space-y-0.5">
                      {envModels.map(m => (
                        // Ключ — с системой: роль «сертификатор» у
                        // COSMOS и роль панели могут совпасть именем.
                        <li key={`${m.system || 'cdpm'}/${m.role}/`
                                 + `${m.model}/${m.channel}`}
                            className="flex justify-between gap-3">
                          <span className="flex items-baseline gap-1.5">
                            {/* Система строки — серым бейджем перед
                                ролью: чья это модель, видно сразу. */}
                            <span className="rounded bg-cf-hover px-1
                                             py-px text-[10px]
                                             text-gray-400">
                              {modelSystemWords(m.system)}
                            </span>
                            <span className="text-white">{m.role}</span>
                          </span>
                          <span className="text-right text-gray-400">
                            {m.provider ? `${m.provider} / ` : ''}
                            {m.model || '—'}
                            {m.channel ? ` · ${m.channel}` : ''}
                            {m.effort ? ` · усилие ${m.effort}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {env?.models_note && (
                    // Неполнота раскладки — предупреждающим тоном: её
                    // панель запишет в голову теми же словами, и после
                    // старта голова неизменна.
                    <div className="pt-1">
                      <p className="text-amber-400">{env.models_note}</p>
                      {preview.cosmos?.asked === false
                        && preview.cosmos.detail && (
                        <p className="text-[11px] text-gray-400">
                          почему: {preview.cosmos.detail}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
              {gateRefused && (
                <div className="mt-2 space-y-1 rounded-lg border
                                border-red-500/60 bg-red-500/10 p-2">
                  <p className="font-semibold text-red-400">
                    Прогон не будет создан — среду объявить нечем:
                  </p>
                  {(preview?.words || []).map(w => (
                    <p key={w} className="text-red-400">{w}</p>
                  ))}
                  <p className="text-gray-400">
                    Отказ гейта — до рождения папки прогона: номер не
                    расходуется, следов не остаётся.
                  </p>
                </div>
              )}
            </div>
          </div>

          <button
            onClick={freeze}
            // Название обязательно (наблюдение COSMOS на демо 06.09:
            // прогон без имени создавался и вставал с полосой «задайте
            // название» — извлечение не шло). Кнопка блокируется с
            // причиной в подсказке, как у панели действий блока.
            // Гейт «Новый прогон» (шаг 2): среду объявить нечем —
            // кнопка недоступна, причины словами выше. Отказ судится
            // ДО рождения папки, номер прогона не расходуется.
            disabled={busy || !files.length || !customer.trim()
              || gateRefused}
            title={!files.length
              ? 'Сначала добавьте документы правил'
              : !customer.trim()
                ? 'Введите название авиакомпании — без него извлечение '
                  + 'правил не запустится'
                : gateRefused
                  ? 'Гейт «Новый прогон» отказывает: '
                    + (preview?.words || []).join('; ')
                  : 'Создать прогон и заморозить документы: SHA-256 '
                    + 'каждого оригинала, разбор на клаузы с якорями, '
                    + 'проверка гейтом. Документы остаются на этой машине'}
            className="flex items-center gap-2 rounded-lg bg-indigo-600
                       px-4 py-2 font-medium text-white transition-colors
                       hover:bg-indigo-700 disabled:cursor-not-allowed
                       disabled:opacity-50"
          >
            {busy ? <Loader2 className="h-5 w-5 animate-spin" />
              : <Snowflake className="h-5 w-5" />}
            {busy ? 'Заморозка…' : 'Заморозить'}
          </button>
          <p className="text-xs text-cf-text-muted">
            {!customer.trim() && files.length > 0
              ? 'Кнопка станет доступна после ввода названия '
                + 'авиакомпании. '
              : ''}
            Заморозка считает SHA-256 каждого оригинала, режет документы
            на клаузы с якорями и проверяет комплект гейтом. Документы
            остаются на этой машине.
          </p>
        </div>
      </div>
    </div>
  )
}
