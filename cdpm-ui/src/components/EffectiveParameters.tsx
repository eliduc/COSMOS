import { useEffect, useState } from 'react'
import { getEffectiveParameters, type ParameterSnapshot } from '../api'
import { codePoints, visuallyConfusable } from '../unicodeConfusables'

function words(value: unknown): string {
  if (value === null || value === undefined) return 'не задано'
  if (typeof value === 'string' && value.trim() === '') return JSON.stringify(value)
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function EffectiveParameterSnapshot({ snapshot }:
                                          { snapshot: ParameterSnapshot }) {
  return (
    <div className="space-y-3 text-xs">
      <p className="text-cf-text-muted">
        Снимок текущей модели: {snapshot.snapshot_at}. Ревизия: {words(snapshot.model_revision)}.
        Это не подтверждение передачи параметров в расчёт или допуска к использованию.
        Применимость и фактическое исполнение проверяются отдельно.</p>
      <p className="break-all text-cf-text-muted">SHA-256 модели: {snapshot.model_sha256}</p>
      {!!snapshot.landing_migrations?.length && (
        <section aria-label="История миграции параметров" className="space-y-2">
          <h4 className="font-semibold">История миграции параметров</h4>
          <p>Это история преобразований, а не текущая готовность или разрешение исполнения.
            Загрузка может дополнить модель в памяти; исходный файл при чтении не перезаписывается.
            SHA-256 снимка относится к прочитанному файлу.</p>
          {snapshot.landing_migrations.map((event, index) => (
            <details key={index} className="rounded border border-cf-border p-2">
              <summary className="cursor-pointer">Запись миграции {index + 1} · {event.items.length} правил</summary>
              <p className="break-all">Вход: {event.input_fingerprint}</p>
              <p className="break-all">Результат: {event.output_fingerprint}</p>
              <p className="break-all">Каталог: {event.catalog
                ? `${event.catalog.version} · ${event.catalog.fingerprint}` : 'недоступен'}</p>
              {event.items.map(item => (
                <div key={item.index} className="mt-2 space-y-1">
                  <p className="font-medium">{item.rule || `Правило ${item.index + 1}`}: {{
                    confirmed: 'форма landing/2 подтверждена',
                    required: 'перепосадка требуется',
                    unconfirmed: 'перепосадка не подтверждена',
                  }[item.state]}</p>
                  {item.words.map((text, i) => <p key={i} className="whitespace-pre-wrap break-words">{text}</p>)}
                  {item.changes.map((change, i) => <p key={i} className="whitespace-pre-wrap break-words">
                    {change.param}: {change.fields.join(', ')}. Источник изменения — код: {change.source.note}
                  </p>)}
                </div>
              ))}
            </details>
          ))}
        </section>
      )}
      <section aria-label="Полнота семей и изменения требований" className="space-y-2">
        <h4 className="font-semibold">Полнота семей и изменения требований</h4>
        {!snapshot.family_review || !snapshot.family_review.judged
          ? <p>Полнота семей не проверена: {snapshot.family_review?.words || 'нет результата пересчёта'}.</p>
          : <>
            <p className="break-all text-cf-text-muted">
              Каталог проверки: {snapshot.family_review.catalog?.version}
              {' · '}{snapshot.family_review.catalog?.fingerprint}.
              Вердикт о наличии значений не означает допуска к исполнению.</p>
            {!snapshot.family_review.families.length && <p>Посадок на семьи нет.</p>}
            {snapshot.family_review.families.map(family => (
              <div key={family.family} className="space-y-1 rounded border border-cf-border p-2">
                <p className="font-medium">{family.family}: {family.verdict}</p>
                <p>Контракт: {family.contract_state}</p>
                {family.transitions.map((text, i) => <p key={`t-${i}`} className="whitespace-pre-wrap break-words">{text}</p>)}
                {!!family.missing.length && <p>Не заданы: {family.missing.join(', ')}</p>}
                {!!family.pending.length && <p>Ожидают пересмотра: {family.pending.join(', ')}</p>}
                {family.problems.map((text, i) => <p key={`p-${i}`} className="whitespace-pre-wrap break-words">{text}</p>)}
              </div>
            ))}
          </>}
      </section>
      {!snapshot.rules.length && <p>В модели нет правил.</p>}
      <div className="max-h-96 space-y-2 overflow-y-auto">
        {snapshot.rules.map((rule, index) => (
          <details key={`${rule.id}-${index}`} className="rounded border border-cf-border p-2">
            <summary className="cursor-pointer break-words font-medium">
              {rule.id || 'Без ID'}{rule.title ? ` — ${words(rule.title)}` : ''}
              {` · ${words(rule.kind)} / ${words(rule.zone)} · параметров: ${rule.params.length}`}
            </summary>
            {!!rule.blockers?.length && <p className="mt-2 font-medium">
              У правила не сняты блокировки: {rule.blockers.join(', ')}.
              Показанные ниже значения не означают готовность правила к исполнению.
            </p>}
            {!rule.params.length && <p className="mt-2">Параметров нет.</p>}
            {rule.params.map(param => (
              <div key={param.name} className="mt-3 space-y-1 border-t border-cf-border pt-2">
                <p className="font-medium">{param.name}</p>
                <p>Значение параметра:</p>
                <pre className="whitespace-pre-wrap break-words font-mono">{words(param.value)}</pre>
                <p className="text-cf-text-muted">Источник значения: {param.from === 'template_fit'
                  ? 'посадка на шаблон (template_fit)' : 'параметры правила (params)'}</p>
                {param.quote !== null && param.quote !== undefined && param.quote !== ''
                  && param.quote !== param.value && (
                    <p className="whitespace-pre-wrap break-words">Цитата в модели: «{words(param.quote)}»</p>
                  )}
                {typeof param.value === 'string' && typeof param.quote === 'string'
                  && param.quote !== '' && visuallyConfusable(param.value, param.quote) && (
                    <div className="space-y-1">
                      <p className="font-medium">Строки отличаются посимвольно и могут выглядеть похоже.
                        Обозначения не заменены; сходство не подтверждает соответствие.</p>
                      <details>
                        <summary className="cursor-pointer">Показать кодовые точки Unicode</summary>
                        <p>Значение:</p>
                        <pre className="whitespace-pre-wrap break-words font-mono">{codePoints(param.value)}</pre>
                        <p>Цитата:</p>
                        <pre className="whitespace-pre-wrap break-words font-mono">{codePoints(param.quote)}</pre>
                      </details>
                    </div>
                  )}
                {([['Единица', param.unit], ['Отношение', param.relation],
                   ['Условие', param.condition]] as const).map(([label, value]) => (
                  value !== null && value !== undefined && value !== ''
                    ? <p key={label} className="whitespace-pre-wrap break-words">{label}: {words(value)}</p>
                    : null
                ))}
              </div>
            ))}
          </details>
        ))}
      </div>
      <p className="text-cf-text-muted">Источник обозначает поле модели, а не автора ответа.
        Различие цитаты и значения может быть нормализацией единиц, а не ошибкой.</p>
    </div>
  )
}

export default function EffectiveParameters({ runId }: { runId: string }) {
  const [snapshot, setSnapshot] = useState<ParameterSnapshot | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    setSnapshot(null)
    setError('')
    setLoading(true)
    getEffectiveParameters(runId).then(value => {
      if (active) setSnapshot(value)
    }).catch(reason => {
      if (active) setError(String(reason))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [runId, refresh])
  const current = snapshot?.run === runId ? snapshot : null
  return (
    <section aria-label="Параметры текущей модели"
             className="space-y-3 rounded border border-cf-border p-3 text-cf-text">
      <h3 className="text-sm font-semibold">Параметры текущей модели</h3>
      <p className="text-xs text-cf-text-muted">Для проверки человеком. После изменения модели
        обновите снимок; это чтение, не запуск расчёта.</p>
      <button type="button" disabled={loading} onClick={() => setRefresh(n => n + 1)}
              className="rounded border border-cf-border px-2 py-1 text-xs disabled:opacity-50">
        {loading ? 'Чтение модели…' : 'Обновить снимок параметров'}
      </button>
      {error && <p role="alert" className="text-xs text-cf-error">{error}</p>}
      {current && <EffectiveParameterSnapshot snapshot={current} />}
    </section>
  )
}
