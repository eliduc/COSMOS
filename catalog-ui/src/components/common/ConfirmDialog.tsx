import { useEffect, useState } from 'react'
import Modal from './Modal'
import Button from './Button'
import { t } from '../../i18n'

// Confirmation of an irreversible action: explicit buttons, danger
// variant for destructive ones. Some decisions of the catalog owner are
// not recorded without words (a request decision, an adopt override),
// so the dialog can carry a mandatory words field: an empty one keeps
// the confirm button disabled instead of sending a silent decision.
interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  wordsLabel?: string
  wordsHint?: string
  wordsRequired?: boolean
  busy?: boolean
  onConfirm: (words: string) => void
  onCancel: () => void
}

export default function ConfirmDialog({
  open, title, message, confirmLabel, danger, wordsLabel, wordsHint,
  wordsRequired, busy, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const [words, setWords] = useState('')
  useEffect(() => { if (open) setWords('') }, [open])
  const blocked = !!busy || (!!wordsRequired && !words.trim())
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <p className="mb-4 whitespace-pre-line text-sm text-cf-text-muted">
        {message}
      </p>
      {wordsLabel && (
        <div className="mb-4">
          <label className="field-label" htmlFor="confirm-words">
            {wordsLabel}
          </label>
          <textarea
            id="confirm-words"
            className="input-field min-h-[72px]"
            value={words}
            onChange={e => setWords(e.target.value)}
          />
          {wordsHint && (
            <p className="mt-1 text-xs text-cf-text-muted">{wordsHint}</p>
          )}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          {t('common.cancel')}
        </Button>
        <Button variant={danger ? 'danger' : 'primary'} disabled={blocked}
                onClick={() => onConfirm(words.trim())}>
          {confirmLabel ?? t('common.confirm')}
        </Button>
      </div>
    </Modal>
  )
}
