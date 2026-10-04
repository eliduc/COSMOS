import toast from 'react-hot-toast'
import type { Toast } from 'react-hot-toast'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X }
  from 'lucide-react'
import { type ReactNode } from 'react'

// Типизированные тосты (порт StyledToast CodeForge на токены cf):
// цветная плашка-иконка + акцентная полоса слева + закрытие.
type ToastType = 'error' | 'success' | 'warning' | 'info'

interface TypeStyle {
  icon: ReactNode
  border: string
  iconBg: string
  iconText: string
  accent: string
}

const STYLES: Record<ToastType, TypeStyle> = {
  error: {
    icon: <AlertCircle className="h-5 w-5" />,
    border: 'border-red-500/40',
    iconBg: 'bg-gradient-to-br from-red-500/25 to-red-600/15',
    iconText: 'text-red-500',
    accent: 'bg-gradient-to-b from-red-500 to-red-600',
  },
  success: {
    icon: <CheckCircle2 className="h-5 w-5" />,
    border: 'border-emerald-500/40',
    iconBg: 'bg-gradient-to-br from-emerald-500/25 to-emerald-600/15',
    iconText: 'text-emerald-500',
    accent: 'bg-gradient-to-b from-emerald-500 to-emerald-600',
  },
  warning: {
    icon: <AlertTriangle className="h-5 w-5" />,
    border: 'border-yellow-500/40',
    iconBg: 'bg-gradient-to-br from-yellow-500/25 to-yellow-600/15',
    iconText: 'text-yellow-500',
    accent: 'bg-gradient-to-b from-yellow-500 to-yellow-600',
  },
  info: {
    icon: <Info className="h-5 w-5" />,
    border: 'border-blue-500/40',
    iconBg: 'bg-gradient-to-br from-blue-500/25 to-blue-600/15',
    iconText: 'text-blue-500',
    accent: 'bg-gradient-to-b from-blue-500 to-blue-600',
  },
}

function show(type: ToastType, message: string) {
  const s = STYLES[type]
  return toast.custom((t: Toast) => (
    <div
      className={`pointer-events-auto relative flex w-96 items-start
        gap-3 overflow-hidden rounded-xl border ${s.border} bg-cf-panel
        p-3 pr-9 shadow-xl transition-all
        ${t.visible ? 'opacity-100' : 'opacity-0'}`}
    >
      <div className={`absolute inset-y-0 left-0 w-1 ${s.accent}`} />
      <div className={`shrink-0 rounded-lg p-1.5 ${s.iconBg}
                       ${s.iconText}`}>
        {s.icon}
      </div>
      <p className="min-w-0 flex-1 break-words pt-0.5 text-sm
                    text-cf-text">
        {message}
      </p>
      <button
        onClick={() => toast.dismiss(t.id)}
        aria-label="Закрыть уведомление"
        className="absolute right-2 top-2 rounded p-1 text-cf-text-muted
                   transition-colors hover:bg-cf-hover
                   hover:text-cf-text"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  ), { duration: type === 'error' ? 6000 : 4000 })
}

export const notify = {
  error: (m: string) => show('error', m),
  success: (m: string) => show('success', m),
  warning: (m: string) => show('warning', m),
  info: (m: string) => show('info', m),
}

export default notify
