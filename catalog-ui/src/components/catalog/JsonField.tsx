import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import { t, tf } from '../../i18n'

// Structural field of a record (param_sets, conditions, rule_form,
// semantics, stand_tasks, acceptance of a criterion): a textarea with
// JSON parsing. A broken text is NOT sent to the parent - the field
// says what the parser choked on and keeps the previous value, so a
// half-typed brace never becomes a draft.
//
// The judge of meaning is the service: validate() of the reader answers
// with problem addresses. This field only guards the form of the text.
function pretty(value: unknown): string {
  return JSON.stringify(value ?? null, null, 1)
}

export default function JsonField({ label, value, onChange, disabled,
                                    hint, rows = 8, id }: {
  label: string
  value: unknown
  onChange?: (next: unknown) => void
  disabled?: boolean
  hint?: string
  rows?: number
  id?: string
}) {
  const [text, setText] = useState(() => pretty(value))
  const [error, setError] = useState<string | null>(null)
  const emitted = useRef<string>(pretty(value))

  // The parent echoes what we emitted - only a foreign change (another
  // record opened, a draft discarded) resets the text of the field.
  useEffect(() => {
    const next = pretty(value)
    if (next !== emitted.current) {
      emitted.current = next
      setText(next)
      setError(null)
    }
  }, [value])

  function edit(next: string) {
    setText(next)
    if (!onChange) return
    try {
      const parsed = JSON.parse(next)
      setError(null)
      emitted.current = pretty(parsed)
      onChange(parsed)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div>
      <label className="field-label" htmlFor={id}>{label}</label>
      <textarea
        id={id}
        className={clsx('input-field font-mono text-xs',
                        error && 'border-cf-error focus:border-cf-error')}
        rows={rows}
        spellCheck={false}
        disabled={disabled || !onChange}
        value={text}
        onChange={e => edit(e.target.value)}
      />
      {error
        ? <p className="mt-1 text-xs text-cf-error">
            {tf('card.jsonInvalid', { words: error })}
          </p>
        : hint
          ? <p className="mt-1 text-xs text-cf-text-muted">{hint}</p>
          : <p className="mt-1 text-xs text-cf-text-muted">
              {t('card.jsonHint')}
            </p>}
    </div>
  )
}
