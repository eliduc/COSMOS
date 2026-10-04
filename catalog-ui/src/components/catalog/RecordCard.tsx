import { Link } from 'react-router-dom'
import clsx from 'clsx'
import { X } from 'lucide-react'
import JsonField from './JsonField'
import ParamsTable from './ParamsTable'
import StandTasksTable from './StandTasksTable'
import HistoryList from './HistoryList'
import StateChip from './StateChip'
import {
  recArr, recObj, recStr,
  type CatalogRecord, type ExecutorRow, type HistoryItem,
  type RecordParam, type StandRow, type StandTask,
} from '../../api'
import { dictKeys, dictList, dictWords } from '../../stores/sessionStore'
import { t } from '../../i18n'

// Card of one catalog record: every field of the reader's schema in
// groups with anchors (#params, #executors, #stand, #history), so a
// link from a product lands on the field it is about.
//
// Closed dictionaries of the catalog become selects and chips - a value
// outside the dictionary is a refusal of the service, and the card does
// not invite it. Structural fields without a widget of their own
// (param_sets, conditions, rule_form, semantics of a criterion,
// instance_form, stand tasks) are edited as JSON: the shape is judged
// by the reader on save, not guessed here.
//
// Read only mode (onChange omitted) is used by the request card and by
// a card opened at a released version.

interface RecordCardProps {
  section: string
  record: CatalogRecord
  onChange?: (next: CatalogRecord) => void
  canSetStatus?: boolean
  /** Why the status cannot be edited - the words of the SERVICE
   *  (rights['draft-status'].words), not a copy of the refusal here. */
  statusHint?: string
  dicts: Record<string, unknown>
  executors?: Record<string, Record<string, ExecutorRow>> | null
  stand?: { passed: number; failed: number; skipped: number
            rows: StandRow[] } | null
  complementOptions?: string[]
  hideRecordOnly?: boolean
}

function Group({ id, title, children, hint }: {
  id?: string
  title: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <section id={id} className="field-group">
      <h3 className="mb-3 text-sm font-semibold text-cf-text">{title}</h3>
      {hint && (
        <p className="mb-3 text-xs text-cf-text-muted">{hint}</p>
      )}
      <div className="space-y-3">{children}</div>
    </section>
  )
}

function TextField({ label, value, onChange, mono, id }: {
  label: string
  value: string
  onChange?: (next: string) => void
  mono?: boolean
  id?: string
}) {
  return (
    <div>
      <label className="field-label" htmlFor={id}>{label}</label>
      <input
        id={id}
        className={clsx('input-field', mono && 'font-mono')}
        value={value}
        disabled={!onChange}
        onChange={e => onChange?.(e.target.value)}
      />
    </div>
  )
}

function AreaField({ label, value, onChange, rows = 3, id }: {
  label: string
  value: string
  onChange?: (next: string) => void
  rows?: number
  id?: string
}) {
  return (
    <div>
      <label className="field-label" htmlFor={id}>{label}</label>
      <textarea
        id={id}
        className="input-field"
        rows={rows}
        value={value}
        disabled={!onChange}
        onChange={e => onChange?.(e.target.value)}
      />
    </div>
  )
}

/** Label of a dictionary value: the code, and next to it the word of
 *  the catalog when the dictionary is a map "code -> word" (since 0.5.0
 *  such is `violation_units`). The word is neither translated nor
 *  invented here - it comes from the service. */
function optionLabel(code: string, words?: Record<string, string>): string {
  const word = words?.[code]
  return word ? `${code} — ${word}` : code
}

function SelectField({ label, value, options, onChange, allowEmpty, title,
                       disabled, id, words }: {
  label: string
  value: string
  options: string[]
  onChange?: (next: string) => void
  allowEmpty?: boolean
  title?: string
  disabled?: boolean
  id?: string
  /** Map "code -> word" of a map dictionary; without it only the
   *  code is shown. */
  words?: Record<string, string>
}) {
  return (
    <div>
      <label className="field-label" htmlFor={id}>{label}</label>
      <select
        id={id}
        className="input-field"
        title={title}
        value={value}
        disabled={disabled || !onChange}
        onChange={e => onChange?.(e.target.value)}
      >
        {allowEmpty && <option value="">{t('common.notSet')}</option>}
        {options.map(one => (
          <option key={one} value={one}>{optionLabel(one, words)}</option>
        ))}
        {value && !options.includes(value) && (
          <option value={value}>{optionLabel(value, words)}</option>
        )}
      </select>
    </div>
  )
}

function Chips({ label, values, options, onChange, hint }: {
  label: string
  values: string[]
  options: string[]
  onChange?: (next: string[]) => void
  hint?: string
}) {
  const rest = options.filter(one => !values.includes(one))
  return (
    <div>
      <span className="field-label">{label}</span>
      <div className="flex flex-wrap items-center gap-1">
        {values.length === 0 && (
          <span className="text-xs text-cf-text-muted">{t('common.no')}</span>
        )}
        {values.map(one => (
          <span key={one}
                className="status-badge bg-cf-primary/15 text-cf-primary">
            {one}
            {onChange && (
              <button
                aria-label={t('common.remove')}
                title={t('common.remove')}
                onClick={() => onChange(values.filter(v => v !== one))}
                className="rounded p-0.5 hover:bg-cf-hover"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        ))}
        {onChange && rest.length > 0 && (
          <select
            className="input-field max-w-[16rem]"
            value=""
            onChange={e => {
              if (e.target.value) onChange([...values, e.target.value])
            }}
          >
            <option value="">{t('common.add')}</option>
            {rest.map(one => (
              <option key={one} value={one}>{one}</option>
            ))}
          </select>
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-cf-text-muted">{hint}</p>}
    </div>
  )
}

function ExecutorStates({ section, id, executors }: {
  section: string
  id: string
  executors: Record<string, Record<string, ExecutorRow>>
}) {
  const kinds = Object.keys(executors).sort()
  if (!kinds.length) return null
  return (
    <div className="space-y-2">
      <span className="field-label">{t('card.executorsLive')}</span>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <tbody>
            {kinds.map(kind => (
              <tr key={kind} className="table-row">
                <td className="table-cell font-mono text-xs">{kind}</td>
                <td className="table-cell">
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(executors[kind]).map(([owner, row]) => (
                      <span key={owner}
                            className="flex items-center gap-1 text-xs">
                        <span className="text-cf-text-muted">{owner}</span>
                        <StateChip
                          state={row.state}
                          title={[row.symbol, row.file, row.note]
                            .filter(Boolean).join(' · ')}
                        />
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Link
        to={`/executors?section=${section}&id=${id}`}
        className="text-xs text-cf-primary hover:underline"
      >
        {t('card.executorsLink')}
      </Link>
    </div>
  )
}

export default function RecordCard({
  section, record, onChange, canSetStatus, statusHint, dicts, executors,
  stand, complementOptions, hideRecordOnly,
}: RecordCardProps) {
  const criteria = section === 'criteria'
  const semantics = recObj(record, 'semantics') ?? {}

  function set(key: string, value: unknown) {
    onChange?.({ ...record, [key]: value })
  }

  function setSemantics(key: string, value: unknown) {
    onChange?.({ ...record, semantics: { ...semantics, [key]: value } })
  }

  const origin = recObj(record, 'origin') ?? {}
  function setOrigin(key: string, value: unknown) {
    onChange?.({ ...record, origin: { ...origin, [key]: value } })
  }

  const status = recStr(record, 'status')
  const subjectWords = recArr<string>(record, 'subject_words')

  // Reading of history (catalog 0.5.0): the field is either `null`
  // (the family does not read history before the horizon) or the whole
  // object `{need, kind}`. The kind cleared turns the field into
  // `null`, not into a half empty object: that shape the service would
  // refuse by rule X15.
  const readsHistory = recObj(record, 'reads_history')
  const readsKind = recStr(readsHistory, 'kind')
  const readsNeed = recStr(readsHistory, 'need')

  return (
    <div className="space-y-4">
      <Group id="header" title={t('card.groupHeader')}>
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField id="f-id" label={t('card.fieldId')}
                     value={recStr(record, 'id')} mono
                     onChange={onChange && (v => set('id', v))} />
          <SelectField id="f-class" label={t('card.fieldClass')}
                       value={recStr(record, 'class')}
                       options={dictList(dicts, 'classes')}
                       onChange={onChange && (v => set('class', v))} />
        </div>
        <TextField id="f-title" label={t('card.fieldTitle')}
                   value={recStr(record, 'title')}
                   onChange={onChange && (v => set('title', v))} />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {!criteria && (
            <SelectField id="f-kind" label={t('card.fieldKind')}
                         value={recStr(record, 'kind')}
                         options={dictList(dicts, 'kinds')}
                         onChange={onChange && (v => set('kind', v))} />
          )}
          <SelectField id="f-zone" label={t('card.fieldZone')}
                       value={recStr(record, 'zone')}
                       options={dictList(dicts, 'zones')}
                       onChange={onChange && (v => set('zone', v))} />
          {!criteria && (
            <SelectField id="f-nature" label={t('card.fieldNature')}
                         value={recStr(record, 'nature')}
                         options={dictList(dicts, 'family_natures')}
                         onChange={onChange && (v => set('nature', v))} />
          )}
          {!criteria && (
            <SelectField
              id="f-nature-by" label={t('card.fieldNatureConfirmedBy')}
              value={recStr(record, 'nature_confirmed_by')}
              options={dictList(dicts, 'nature_witnesses')}
              title={t('card.natureConfirmedByHint')}
              onChange={onChange
                && (v => set('nature_confirmed_by', v))} />
          )}
          {!criteria && (
            <SelectField
              id="f-violation" label={t('card.fieldViolationUnit')}
              value={recStr(record, 'violation_unit')} allowEmpty
              options={dictKeys(dicts, 'violation_units')}
              words={dictWords(dicts, 'violation_units')}
              onChange={onChange
                && (v => set('violation_unit', v || null))} />
          )}
          {!criteria && (
            <SelectField
              id="f-violation-size" label={t('card.fieldViolationSize')}
              value={recStr(record, 'violation_size')} allowEmpty
              options={dictList(dicts, 'violation_sizes')}
              title={t('card.violationSizeHint')}
              onChange={onChange
                && (v => set('violation_size', v || null))} />
          )}
          {!hideRecordOnly && (
            <SelectField
              id="f-status" label={t('card.fieldStatus')}
              value={status}
              options={dictList(dicts, 'record_statuses')}
              title={canSetStatus ? undefined : statusHint}
              disabled={!canSetStatus}
              onChange={onChange && (v => set('status', v))} />
          )}
        </div>
      </Group>

      {!criteria && (
        <Group id="reads-history" title={t('card.groupReadsHistory')}
               hint={t('card.readsHistoryHint')}>
          <div className="grid gap-3 sm:grid-cols-2">
            <SelectField
              id="f-reads-history-kind"
              label={t('card.fieldReadsHistoryKind')}
              value={readsKind} allowEmpty
              options={dictList(dicts, 'history_read_kinds')}
              onChange={onChange && (v => set(
                'reads_history',
                v ? { need: readsNeed, kind: v } : null))} />
          </div>
          {readsKind && (
            <AreaField id="f-reads-history-need"
                       label={t('card.fieldReadsHistoryNeed')}
                       value={readsNeed} rows={2}
                       onChange={onChange && (v => set(
                         'reads_history',
                         { need: v, kind: readsKind }))} />
          )}
        </Group>
      )}

      {!criteria && (
        <Group id="subject" title={t('card.groupSubject')}>
          <AreaField id="f-subject" label={t('card.fieldSubject')}
                     value={recStr(record, 'subject')} rows={2}
                     onChange={onChange && (v => set('subject', v))} />
          <div>
            <label className="field-label" htmlFor="f-subject-words">
              {t('card.fieldSubjectWords')}
            </label>
            <textarea
              id="f-subject-words"
              className="input-field"
              rows={3}
              value={subjectWords.join('\n')}
              disabled={!onChange}
              onChange={e => {
                const words = e.target.value.split('\n')
                  .map(one => one.trim()).filter(Boolean)
                set('subject_words', words.length ? words : null)
              }}
            />
            <p className="mt-1 text-xs text-cf-text-muted">
              {t('card.subjectWordsHint')}
            </p>
          </div>
          <Chips
            label={t('card.fieldComplements')}
            values={recArr<string>(record, 'complements')}
            options={complementOptions ?? []}
            hint={t('card.complementsHint')}
            onChange={onChange && (next => set('complements', next))}
          />
        </Group>
      )}

      {criteria && (
        <Group id="subject" title={t('card.groupSubject')}>
          <AreaField id="f-needs" label={t('card.fieldNeeds')}
                     value={recStr(record, 'needs')} rows={2}
                     onChange={onChange
                       && (v => set('needs', v || null))} />
          <AreaField id="f-refusal" label={t('card.fieldRefusal')}
                     value={recStr(record, 'refusal')} rows={2}
                     onChange={onChange
                       && (v => set('refusal', v || null))} />
        </Group>
      )}

      {!criteria && (
        <Group id="params" title={t('card.groupParams')}>
          <ParamsTable
            params={recArr<RecordParam>(record, 'params')}
            dicts={dicts}
            onChange={onChange && (next => set('params', next))}
          />
        </Group>
      )}

      {!criteria && (
        <Group id="param-sets" title={t('card.groupParamSets')}>
          <JsonField id="f-param-sets" label={t('card.groupParamSets')}
                     value={recArr(record, 'param_sets')} rows={5}
                     onChange={onChange && (v => set('param_sets', v))} />
        </Group>
      )}

      {!criteria && (
        <Group id="conditions" title={t('card.groupConditions')}>
          <JsonField id="f-conditions" label={t('card.groupConditions')}
                     value={recArr(record, 'conditions')} rows={5}
                     onChange={onChange && (v => set('conditions', v))} />
        </Group>
      )}

      {!criteria && (
        <Group id="rule-form" title={t('card.groupRuleForm')}>
          <JsonField id="f-rule-form" label={t('card.groupRuleForm')}
                     value={record.rule_form ?? null} rows={6}
                     onChange={onChange && (v => set('rule_form', v))} />
        </Group>
      )}

      <Group id="semantics" title={t('card.groupSemantics')}>
        <AreaField id="f-sem-text" label={t('card.fieldText')}
                   value={typeof semantics.text === 'string'
                     ? semantics.text : ''}
                   rows={3}
                   onChange={onChange
                     && (v => setSemantics('text', v || null))} />
        {!criteria && (
          <>
            <AreaField id="f-sem-scope" label={t('card.fieldScope')}
                       value={typeof semantics.scope === 'string'
                         ? semantics.scope : ''}
                       rows={2}
                       onChange={onChange
                         && (v => setSemantics('scope', v || null))} />
            <AreaField id="f-sem-unit" label={t('card.fieldUnit')}
                       value={typeof semantics.unit === 'string'
                         ? semantics.unit : ''}
                       rows={2}
                       onChange={onChange
                         && (v => setSemantics('unit', v || null))} />
            <AreaField id="f-sem-measure" label={t('card.fieldMeasure')}
                       value={typeof semantics.measure === 'string'
                         ? semantics.measure : ''}
                       rows={2}
                       onChange={onChange
                         && (v => setSemantics('measure', v || null))} />
          </>
        )}
        {criteria && (
          <>
            <AreaField id="f-sem-formula" label={t('card.fieldFormula')}
                       value={typeof semantics.formula === 'string'
                         ? semantics.formula : ''}
                       rows={2}
                       onChange={onChange
                         && (v => setSemantics('formula', v || null))} />
            <JsonField id="f-sem-rates" label={t('card.fieldRatesForm')}
                       value={semantics.rates_form ?? null} rows={5}
                       onChange={onChange
                         && (v => setSemantics('rates_form', v))} />
          </>
        )}
      </Group>

      {criteria && (
        <Group id="instance-form" title={t('card.groupInstanceForm')}>
          <JsonField id="f-instance-form" label={t('card.groupInstanceForm')}
                     value={record.instance_form ?? null} rows={6}
                     onChange={onChange
                       && (v => set('instance_form', v))} />
        </Group>
      )}

      <Group id="executors" title={t('card.groupExecutors')}>
        <Chips
          label={criteria ? t('card.computableBy') : t('card.executorKinds')}
          values={recArr<string>(record,
            criteria ? 'computable_by' : 'executor_kinds')}
          options={dictList(dicts, 'executor_kinds')}
          onChange={onChange && (next => set(
            criteria ? 'computable_by' : 'executor_kinds', next))}
        />
        {executors && (
          <ExecutorStates section={section} id={recStr(record, 'id')}
                          executors={executors} />
        )}
      </Group>

      {!criteria && (
        <Group id="templates" title={t('card.groupTemplates')}>
          <Chips
            label={t('card.groupTemplates')}
            values={recArr<string>(record, 'check_templates')}
            options={dictKeys(dicts, 'check_templates')}
            hint={t('card.templatesHint')}
            onChange={onChange && (next => set('check_templates', next))}
          />
        </Group>
      )}

      {!hideRecordOnly && (
        <Group id="stand" title={t('card.groupStand')}>
          <StandTasksTable
            tasks={recArr<StandTask>(record, 'stand_tasks')}
            dicts={dicts}
            results={stand?.rows}
            summary={stand ? { passed: stand.passed, failed: stand.failed,
                               skipped: stand.skipped } : undefined}
            onChange={onChange && (next => set('stand_tasks', next))}
          />
        </Group>
      )}

      <Group id="acceptance" title={t('card.groupAcceptance')}
             hint={t('card.acceptanceHint')}>
        <AreaField id="f-acceptance" label={t('card.fieldAcceptance')}
                   value={recStr(record, 'acceptance')} rows={2}
                   onChange={onChange
                     && (v => set('acceptance', v || null))} />
      </Group>

      <Group id="origin" title={t('card.groupOrigin')}>
        <div className="grid gap-3 sm:grid-cols-3">
          <SelectField id="f-origin-kind" label={t('card.fieldOriginKind')}
                       value={typeof origin.kind === 'string'
                         ? origin.kind : ''}
                       options={dictList(dicts, 'origin_kinds')}
                       onChange={onChange
                         && (v => setOrigin('kind', v))} />
          <div>
            <label className="field-label" htmlFor="f-origin-date">
              {t('card.fieldOriginDate')}
            </label>
            <input
              id="f-origin-date"
              type="date"
              className="input-field"
              value={typeof origin.date === 'string' ? origin.date : ''}
              disabled={!onChange}
              onChange={e => setOrigin('date', e.target.value)}
            />
          </div>
          <SelectField
            id="f-origin-by" label={t('card.fieldOriginConfirmedBy')}
            value={typeof origin.confirmed_by === 'string'
              ? origin.confirmed_by : ''}
            options={dictList(dicts, 'actors')}
            onChange={onChange && (v => setOrigin('confirmed_by', v))} />
        </div>
        <AreaField id="f-origin-note" label={t('card.fieldOriginNote')}
                   value={typeof origin.note === 'string' ? origin.note : ''}
                   rows={2}
                   onChange={onChange && (v => setOrigin('note', v))} />
      </Group>

      <Group id="note" title={t('card.groupNote')}>
        <AreaField id="f-note" label={t('common.note')}
                   value={recStr(record, 'note')} rows={2}
                   onChange={onChange && (v => set('note', v || null))} />
      </Group>

      {!hideRecordOnly && (
        <Group id="history" title={t('card.groupHistory')}>
          <HistoryList history={recArr<HistoryItem>(record, 'history')} />
        </Group>
      )}
    </div>
  )
}
