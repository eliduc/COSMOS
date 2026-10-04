import { Fragment, type ReactNode } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { X } from 'lucide-react'
import { t } from '../../i18n'

// Modal window (same shape as the CDPM panel): backdrop plus a panel on
// theme tokens; Dialog.Title is required for accessibility.
interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  wide?: boolean
  full?: boolean
}

export default function Modal({ open, onClose, title, children,
                                wide, full }: ModalProps) {
  return (
    <Transition show={open} as={Fragment}>
      <Dialog onClose={onClose} className="relative z-50">
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-200" enterFrom="opacity-0"
          enterTo="opacity-100" leave="ease-in duration-150"
          leaveFrom="opacity-100" leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        </Transition.Child>
        <div className="fixed inset-0 flex items-center justify-center
                        p-4">
          <Transition.Child
            as={Fragment}
            enter="ease-out duration-200"
            enterFrom="opacity-0 scale-95"
            enterTo="opacity-100 scale-100"
            leave="ease-in duration-150"
            leaveFrom="opacity-100 scale-100"
            leaveTo="opacity-0 scale-95"
          >
            <Dialog.Panel
              className={`w-full ${full ? 'max-w-[94vw] max-h-[94vh]'
                  + ' overflow-y-auto'
                : wide ? 'max-w-3xl max-h-[88vh] overflow-y-auto'
                       : 'max-w-md'}
                rounded-xl border border-cf-border bg-cf-panel p-5
                shadow-xl`}
            >
              <div className="mb-3 flex items-center justify-between">
                <Dialog.Title className="text-base font-semibold
                                         text-cf-text">
                  {title}
                </Dialog.Title>
                <button
                  onClick={onClose}
                  aria-label={t('common.closeWindow')}
                  className="rounded-lg p-1 text-cf-text-muted
                             transition-colors hover:bg-cf-hover
                             hover:text-cf-text"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              {children}
            </Dialog.Panel>
          </Transition.Child>
        </div>
      </Dialog>
    </Transition>
  )
}
