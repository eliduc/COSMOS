import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import Button from '../common/Button'
import type { RecordParam } from '../../api'
import { dictEntry, dictKeys, dictList } from '../../stores/sessionStore'
import { t } from '../../i18n'

// Parameters of a family record: name, unit, relation, qualifier, note.
// Units, relations and qualifiers come from the closed dictionaries of
// the catalog, so they are selects, not free text.
//
// "Take a name from unbound_params" fills the row from the dictionary of
// names that belong to no family yet (unit, relation and the source
// note): the assembly then removes the taken name from unbound_params
// itself - rule D03 of the reader.
export default function ParamsTable({ params, onChange, dicts }: {
  params: RecordParam[]
  onChange?: (next: RecordParam[]) => void
  dicts: Record<string, unknown>
}) {
  const [pick, setPick] = useState('')
  const units = dictList(dicts, 'param_units')
  const relations = dictList(dicts, 'param_relations')
  const qualifiers = dictList(dicts, 'qualifier_kinds')
  const unbound = dictKeys(dicts, 'unbound_params')
    .filter(name => !params.some(one => one.name === name))

  function patch(index: number, field: keyof RecordParam,
                 value: string | null) {
    if (!onChange) return
    onChange(params.map((one, at) =>
      at === index ? { ...one, [field]: value } : one))
  }

  function add(name: string, unit: string, relation: string | null,
               note: string | null) {
    if (!onChange) return
    onChange([...params, { name, unit, relation, qualifier: null, note }])
  }

  function takeUnbound(name: string) {
    const entry = dictEntry(dicts, 'unbound_params', name)
    add(name,
        typeof entry?.unit === 'string' ? entry.unit : (units[0] ?? ''),
        typeof entry?.relation === 'string' ? entry.relation : null,
        typeof entry?.note === 'string' ? entry.note : null)
    setPick('')
  }

  return (
    <div className="space-y-3">
      {params.length === 0 && (
        <p className="text-sm text-cf-text-muted">{t('card.paramsEmpty')}</p>
      )}
      {params.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="table-head">{t('card.paramName')}</th>
                <th className="table-head">{t('card.paramUnit')}</th>
                <th className="table-head">{t('card.paramRelation')}</th>
                <th className="table-head">{t('card.paramQualifier')}</th>
                <th className="table-head">{t('card.paramNote')}</th>
                {onChange && <th className="table-head" />}
              </tr>
            </thead>
            <tbody>
              {params.map((one, index) => (
                <tr key={`${one.name}-${index}`} className="table-row">
                  <td className="table-cell">
                    <input
                      className="input-field font-mono text-xs"
                      value={one.name}
                      disabled={!onChange}
                      onChange={e => patch(index, 'name', e.target.value)}
                    />
                  </td>
                  <td className="table-cell">
                    <select
                      className="input-field"
                      value={one.unit}
                      disabled={!onChange}
                      onChange={e => patch(index, 'unit', e.target.value)}
                    >
                      {units.map(unit => (
                        <option key={unit} value={unit}>{unit}</option>
                      ))}
                      {!units.includes(one.unit) && (
                        <option value={one.unit}>{one.unit}</option>
                      )}
                    </select>
                  </td>
                  <td className="table-cell">
                    <select
                      className="input-field"
                      value={one.relation ?? ''}
                      disabled={!onChange}
                      onChange={e => patch(index, 'relation',
                                           e.target.value || null)}
                    >
                      <option value="">{t('common.notSet')}</option>
                      {relations.map(rel => (
                        <option key={rel} value={rel}>{rel}</option>
                      ))}
                    </select>
                  </td>
                  <td className="table-cell">
                    <select
                      className="input-field"
                      value={one.qualifier ?? ''}
                      disabled={!onChange}
                      onChange={e => patch(index, 'qualifier',
                                           e.target.value || null)}
                    >
                      <option value="">{t('common.notSet')}</option>
                      {qualifiers.map(kind => (
                        <option key={kind} value={kind}>{kind}</option>
                      ))}
                    </select>
                  </td>
                  <td className="table-cell">
                    <textarea
                      className="input-field min-h-[38px] text-xs"
                      rows={2}
                      value={one.note ?? ''}
                      disabled={!onChange}
                      onChange={e => patch(index, 'note',
                                           e.target.value || null)}
                    />
                  </td>
                  {onChange && (
                    <td className="table-cell">
                      <Button
                        variant="ghost" size="sm"
                        aria-label={t('card.paramRemove')}
                        title={t('card.paramRemove')}
                        onClick={() => onChange(
                          params.filter((__, at) => at !== index))}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {onChange && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary" size="sm"
            leadingIcon={<Plus className="h-4 w-4" />}
            onClick={() => add('', units[0] ?? '', null, null)}
          >
            {t('card.paramAdd')}
          </Button>
          {unbound.length > 0 && (
            <>
              <select
                className="input-field max-w-xs"
                value={pick}
                title={t('card.unboundHint')}
                onChange={e => {
                  if (e.target.value) takeUnbound(e.target.value)
                }}
              >
                <option value="">{t('card.unbound')}</option>
                {unbound.map(name => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
              <span className="text-xs text-cf-text-muted">
                {t('card.unboundHint')}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  )
}
