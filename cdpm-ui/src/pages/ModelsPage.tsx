import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Cpu, Link2, Settings2 } from 'lucide-react'
import notify from '../components/common/StyledToast'
import AgentConfigPopup from '../components/graph/AgentConfigPopup'
import { getEffectiveAgents, listRoles, listRuns,
         type EffectiveRole, type RoleInfo, type RunSummary }
  from '../api'

// «Модели узлов» — полная независимость выбора LLM (директива
// владельца 2026-09-02): все LLM-точки конвейера как роли, у каждой
// — своя модель; незаданная роль наследует родителя (показано явно).
// Таблица «узел → модель» — та самая, что владелец подтверждает до
// старта прогона (правило llm-approval-before-runs).
const SLOT_TITLES: Record<string, string> = {
  cloud: 'облако', local: 'локально', 'cloud-country': 'в стране',
}

function slotLine(r: EffectiveRole): string {
  const parts: string[] = []
  for (const [klass, c] of Object.entries(r.channels)) {
    if (!c) continue
    const model = c.model && c.model !== '(по умолчанию)'
      ? c.model : undefined
    const name = model || c.provider || 'по умолчанию'
    parts.push(`${SLOT_TITLES[klass] || klass}: ${name}`
      + (c.effort ? ` (усилие ${c.effort})` : ''))
  }
  return parts.join(' · ')
}

export default function ModelsPage() {
  const { runId: routeRun } = useParams()
  const navigate = useNavigate()
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [runId, setRunId] = useState<string | undefined>(routeRun)
  const [roles, setRoles] = useState<RoleInfo[]>([])
  const [effective, setEffective] = useState<EffectiveRole[]>([])
  const [editing, setEditing] = useState<RoleInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    (async () => {
      try {
        const [rs, rl] = await Promise.all([listRuns(), listRoles()])
        setRuns(rs)
        setRoles(rl)
        if (!routeRun && rs.length) setRunId(rs[rs.length - 1].run)
      } catch (e) {
        setError(String(e))
        notify.error(String(e))
      }
    })()
  }, [routeRun])

  const refresh = async (id: string | undefined) => {
    if (!id) return
    try {
      const got = await getEffectiveAgents(id)
      setEffective(got.roles)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }
  useEffect(() => { refresh(runId) }, [runId])

  const byRole = Object.fromEntries(effective.map(r => [r.role, r]))
  const labelOf = (role: string | null) =>
    roles.find(r => r.role === role)?.label || role || ''

  return (
    <div className="relative flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6">
          <h1 className="flex items-center gap-2 text-2xl font-bold
                         text-cf-text">
            <Cpu className="h-6 w-6 text-cf-primary" />
            Модели узлов
          </h1>
          <p className="mt-1 text-sm text-cf-text-muted">
            У каждого LLM-узла конвейера — своя модель. Роль без
            собственной настройки наследует родителя (показано
            стрелкой); родитель без настройки — умолчание канала.
            Эта таблица — раскладка, которую владелец подтверждает
            до старта прогона.
          </p>
        </div>

        <label className="mb-4 block text-xs text-cf-text-muted">
          Прогон
          <select className="input-field mt-1 max-w-md"
                  value={runId || ''}
                  onChange={e => {
                    setRunId(e.target.value)
                    navigate(`/models/${encodeURIComponent(
                      e.target.value)}`, { replace: true })
                  }}>
            {runs.map(r => (
              <option key={r.run} value={r.run}>
                {r.run}{r.title ? ` — ${r.title}` : ''}
                {r.customer ? ` (${r.customer})` : ''}
              </option>
            ))}
          </select>
        </label>

        {error && (
          <p className="mb-3 text-sm text-red-500">{error}</p>
        )}

        <div className="overflow-x-auto rounded-xl border border-cf-border
                        bg-cf-panel">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-cf-border text-left text-xs
                             uppercase text-cf-text-muted">
                <th className="px-3 py-2">Узел</th>
                <th className="px-3 py-2">Что делает</th>
                <th className="px-3 py-2">Конфигурация</th>
                <th className="px-3 py-2">Модели слотов</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {roles.map(role => {
                const eff = byRole[role.role]
                const inherited = eff?.inherited
                const own = eff?.effective === role.role
                return (
                  <tr key={role.role}
                      className="border-b border-cf-border/60
                                 align-top last:border-0">
                    <td className="px-3 py-2 font-medium text-cf-text">
                      {role.label}
                      <div className="text-[11px] font-normal
                                      text-cf-text-muted">
                        {role.role}
                        {role.parent && (
                          <span title="Родитель наследования">
                            {' '}· наследует {labelOf(role.parent)}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-cf-text-muted">
                      {role.purpose}
                    </td>
                    <td className="px-3 py-2">
                      {own && (
                        <span className="rounded bg-emerald-500/15 px-1.5
                                         py-0.5 text-xs text-emerald-600">
                          своя
                        </span>
                      )}
                      {inherited && (
                        <span className="inline-flex items-center gap-1
                                         rounded bg-amber-500/15 px-1.5
                                         py-0.5 text-xs text-amber-600"
                              title="Собственной настройки нет — действует
конфигурация родителя">
                          <Link2 className="h-3 w-3" />
                          от {labelOf(eff?.effective)}
                        </span>
                      )}
                      {eff && !own && !inherited && (
                        <span className="rounded bg-slate-500/15 px-1.5
                                         py-0.5 text-xs text-cf-text-muted"
                              title="Ни своей настройки, ни родительской —
умолчание канала площадки">
                          умолчание
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-cf-text">
                      {eff ? (slotLine(eff) || '—') : '…'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        className="inline-flex items-center gap-1 rounded
                                   border border-cf-border px-2 py-1
                                   text-xs text-cf-text hover:bg-cf-hover
                                   disabled:opacity-40"
                        disabled={!runId}
                        title="Задать модель этой роли для прогона"
                        onClick={() => setEditing(role)}
                      >
                        <Settings2 className="h-3.5 w-3.5" />
                        настроить
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {editing && runId && (
        <AgentConfigPopup
          runId={runId}
          role={editing.role}
          label={editing.label}
          x={24}
          y={24}
          onClose={() => { setEditing(null); refresh(runId) }}
        />
      )}
    </div>
  )
}
