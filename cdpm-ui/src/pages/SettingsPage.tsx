import { useEffect, useState } from 'react'
import notify from '../components/common/StyledToast'
import { Cloud, HardDrive, Key, KeyRound, Palette, PlugZap, Save,
         Trash2 } from 'lucide-react'
import Button from '../components/common/Button'
import StatusBadge from '../components/common/StatusBadge'
import ThemeToggle from '../components/common/ThemeToggle'
import { checkProvider, getSettings, listProviders, putProviderKey,
         type ProviderInfo } from '../api'

// Настройки — хром SettingsPage CodeForge: узкий контейнер, крупный
// заголовок с подзаголовком, секции-панели с иконкой в заголовке,
// внутри — ряды провайдеров на подложке bg-cf-bg (как ряды ключей
// эталона). Ключи хранятся в каталоге данных на этой машине; наружу
// отдаются только маской.
export default function SettingsPage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [checks, setChecks] = useState<
    Record<string, { ok: boolean; detail: string
                     models?: string[] }>>({})

  const refresh = async () => {
    try {
      setProviders(await listProviders())
      setLoadError(null)
      await getSettings()
    } catch (e) {
      // Пустая секция без объяснения — обман; причина называется
      // прямо в секции, не только тостом (замечание владельца).
      setLoadError(String(e))
      notify.error(String(e))
    }
  }
  useEffect(() => { refresh() }, [])

  const save = async (name: string, value: string) => {
    setBusy(name)
    try {
      await putProviderKey(name, value)
      setDrafts(d => ({ ...d, [name]: '' }))
      notify.success(value ? 'Ключ сохранён' : 'Ключ удалён')
      refresh()
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  const check = async (name: string) => {
    setBusy(name)
    try {
      const got = await checkProvider(name)
      setChecks(c => ({ ...c, [name]: got }))
      if (got.ok) notify.success(`${name}: связь есть`)
      else notify.error(`${name}: ${got.detail}`)
    } catch (e) {
      notify.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl">
        <div className="mb-8">
          <h1 className="mb-2 text-3xl font-bold text-cf-text">
            Настройки
          </h1>
          <p className="text-cf-text-muted">
            Провайдеры языковых моделей, ключи доступа и внешний вид
          </p>
        </div>

        {/* Внешний вид — секция эталона */}
        <div className="mb-6 rounded-xl border border-cf-border
                        bg-cf-panel p-6">
          <h2 className="mb-4 flex items-center gap-2 text-lg
                         font-semibold text-cf-text">
            <Palette className="h-5 w-5 text-cf-primary" />
            Внешний вид
          </h2>
          <div className="flex items-center gap-4">
            <span className="text-sm text-cf-text-muted">Тема</span>
            <ThemeToggle />
          </div>
        </div>

        {/* Провайдеры моделей */}
        <div className="mb-6 rounded-xl border border-cf-border
                        bg-cf-panel p-6">
          <h2 className="mb-1 flex items-center gap-2 text-lg
                         font-semibold text-cf-text">
            <Key className="h-5 w-5 text-cf-primary" />
            Провайдеры моделей
          </h2>
          <p className="mb-4 text-sm text-cf-text-muted">
            Ключи хранятся в каталоге данных на этой машине, в
            репозиторий не попадают и наружу отдаются только маской.
            Облачные провайдеры недоступны наборам с признаком «только
            локальная модель».
          </p>

          {providers.length === 0 && (
            <div className="mb-3 rounded-lg border border-cf-warning/40
                            bg-cf-warning/10 p-4 text-sm text-cf-text">
              <p className="mb-2">
                Список провайдеров не загрузился
                {loadError ? ` — ${loadError}` : ' — сервер не '
                  + 'ответил (возможно, перезапускался)'}.
                Здесь должны быть пять карточек с полями ключей:
                облачная Claude, локальная модель, OpenAI, xAI Grok,
                Google Gemini.
              </p>
              <Button size="sm" onClick={refresh}>
                Загрузить снова
              </Button>
            </div>
          )}
          <div className="space-y-3">
            {providers.map(p => (
              <section key={p.name}
                       className="rounded-lg bg-cf-bg p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex h-10 w-10 items-center
                                  justify-center rounded-lg
                                  bg-cf-primary/10">
                    {p.cloud
                      ? <Cloud className="h-5 w-5 text-cf-primary" />
                      : <HardDrive className="h-5 w-5
                                              text-cf-primary" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold
                                       text-cf-text">
                        {p.title}
                      </span>
                      <StatusBadge kind={p.configured ? 'ok' : 'muted'}
                                   title={p.configured
                        ? 'Ключ задан — провайдер готов к работе'
                        : 'Без ключа провайдер недоступен блокам'}>
                        {p.configured ? 'настроен' : 'ключ не задан'}
                      </StatusBadge>
                      <StatusBadge kind="info"
                                   title="К какому каналу политики
относится провайдер: local — локальная модель, cloud — внешнее
облако, cloud-country — облако внутри страны">
                        канал: {p.channel}
                      </StatusBadge>
                      {p.cloud && (
                        <StatusBadge kind="warn"
                                     title="Облачный провайдер:
содержимое уходит внешней модели — недоступен наборам «только
локальная модель»">
                          облако
                        </StatusBadge>
                      )}
                      {p.key_masked && (
                        <span className="font-mono text-xs
                                         text-cf-text-muted"
                              title="Сохранённый ключ (показывается
только маской; наружу целиком не отдаётся)">
                          {p.key_masked}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-cf-text-muted">
                      Модели-подсказки: {p.models.join(', ')} ·
                      применяется у блоков:
                      {' '}{p.params_supported.join(', ')}
                    </p>
                  </div>
                </div>

                {p.name !== 'local' && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <input
                      type="password"
                      className="input-field w-full md:w-auto md:flex-1"
                      placeholder={p.key_hint || 'ключ доступа'}
                      value={drafts[p.name] || ''}
                      onChange={e => setDrafts(d => ({ ...d,
                        [p.name]: e.target.value }))}
                      aria-label={`Ключ доступа: ${p.title}`}
                    />
                    <Button
                      size="sm"
                      onClick={() => save(p.name, drafts[p.name] || '')}
                      disabled={busy === p.name
                        || !(drafts[p.name] || '').trim()}
                      leadingIcon={<Save className="h-4 w-4" />}
                      title="Сохранить ключ в каталоге данных этой
машины (в репозиторий не попадает)"
                    >
                      Сохранить
                    </Button>
                    <Button
                      size="sm" variant="secondary"
                      onClick={() => check(p.name)}
                      disabled={busy === p.name}
                      leadingIcon={<PlugZap className="h-4 w-4" />}
                      title="Живой пробный запрос провайдеру: проверяет
ключ и показывает модели, доступные именно вашему ключу"
                    >
                      Проверить связь
                    </Button>
                    {p.key_masked && (
                      <Button
                        size="sm" variant="danger"
                        onClick={() => save(p.name, '')}
                        disabled={busy === p.name}
                        leadingIcon={<Trash2 className="h-4 w-4" />}
                        title="Удалить сохранённый ключ с этой машины —
провайдер станет недоступен блокам"
                      >
                        Удалить ключ
                      </Button>
                    )}
                  </div>
                )}

                {checks[p.name] && (
                  <p className={`mt-2 text-xs ${checks[p.name].ok
                    ? 'text-cf-success' : 'text-cf-error'}`}>
                    {checks[p.name].ok
                      ? 'связь есть — ' + checks[p.name].detail
                      : checks[p.name].detail}
                  </p>
                )}
                {(checks[p.name]?.models || []).length > 0 && (
                  <p className="mt-1 text-xs text-cf-text-muted">
                    Модели, доступные вашему ключу:
                    {' '}{checks[p.name]!.models!.join(', ')} — имя
                    модели вводится в слоте блока (шестерёнка на
                    блоке диаграммы).
                  </p>
                )}
              </section>
            ))}
          </div>

          <p className="mt-4 flex items-start gap-2 text-xs
                        text-cf-text-muted">
            <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Разные провайдеры у обработчика, аудитора и арбитра делают
            проверку независимой: модель, проверяющая собственную
            работу, склонна с собой соглашаться. Выбор провайдера и
            модели для каждой роли — шестерёнка на блоке диаграммы
            прогона.
          </p>
        </div>
      </div>
    </div>
  )
}
