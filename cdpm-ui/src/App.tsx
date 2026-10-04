import { useEffect, useRef, useState } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import RunsPage from './pages/RunsPage'
import NewRunPage from './pages/NewRunPage'
import RunPage from './pages/RunPage'
import SettingsPage from './pages/SettingsPage'
import ModelsPage from './pages/ModelsPage'
import { RECORD_WRITE_WARNING_EVENT, refreshRunRecordOnly } from './api'

// Заглушки остаются у разделов, которые наполняются пакетами
// УИ-3…УИ-5 (аудит-цикл, арбитраж, настройки провайдеров).
function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="card max-w-md text-center">
        <h1 className="mb-2 text-lg font-semibold text-cf-text">
          {title}
        </h1>
        <p className="text-sm text-cf-text-muted">
          Раздел в разработке: наполняется следующими пакетами работ.
          Пока пользуйтесь командной строкой (руководство, часть 10).
        </p>
      </div>
    </div>
  )
}

export default function App() {
  const [recordWarnings, setRecordWarnings] = useState<string[] | null>(null)
  const [recordBusy, setRecordBusy] = useState(false)
  const [recordError, setRecordError] = useState('')
  const recordWarningGeneration = useRef(0)
  useEffect(() => {
    const warn = (event: Event) => {
      recordWarningGeneration.current += 1
      const runs = (event as CustomEvent<{ runs: string[] }>).detail?.runs || []
      setRecordWarnings(previous => [...new Set([...(previous || []), ...runs])])
    }
    window.addEventListener(RECORD_WRITE_WARNING_EVENT, warn)
    return () => window.removeEventListener(RECORD_WRITE_WARNING_EVENT, warn)
  }, [])
  const refreshRecords = async () => {
    const runs = recordWarnings || []
    const generation = recordWarningGeneration.current
    setRecordBusy(true)
    setRecordError('')
    try {
      for (const run of runs) await refreshRunRecordOnly(run)
      // Do not dismiss a newer concurrent warning, even for the same run.
      if (recordWarningGeneration.current === generation) setRecordWarnings(null)
    } catch (e) {
      setRecordError(String(e))
    } finally {
      setRecordBusy(false)
    }
  }
  return (
    <Layout>
      {recordWarnings !== null && (
        <section role="status" aria-live="polite"
                 className="m-3 rounded border border-yellow-500/50 bg-cf-panel p-3 text-sm text-cf-text">
          <p>Действие выполнено, но сводная запись прогона занята и не обновилась
            {recordWarnings.length ? ` (${recordWarnings.join(', ')})` : ''}.
            Не повторяйте само действие — обновите только запись.</p>
          {recordError && <p className="mt-2 text-cf-error">{recordError}</p>}
          <div className="mt-2 flex gap-3">
            {recordWarnings.length > 0 && (
              <button type="button" disabled={recordBusy} onClick={refreshRecords}
                      className="rounded border border-cf-border px-3 py-1 disabled:opacity-50">
                {recordBusy ? 'Обновление записи…' : 'Обновить только запись'}
              </button>
            )}
            <button type="button" disabled={recordBusy}
                    onClick={() => { setRecordWarnings(null); setRecordError('') }}>
              Скрыть предупреждение
            </button>
          </div>
        </section>
      )}
      <Routes>
        <Route path="/" element={<Navigate to="/runs" replace />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/new" element={<NewRunPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/exchange"
               element={<Placeholder title="Аудит-цикл" />} />
        <Route path="/arbiter"
               element={<Placeholder title="Арбитр тупиков" />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/models/:runId" element={<ModelsPage />} />
        <Route path="*" element={<Navigate to="/runs" replace />} />
      </Routes>
    </Layout>
  )
}
