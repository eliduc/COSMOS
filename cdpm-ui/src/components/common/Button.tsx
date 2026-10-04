import { type ButtonHTMLAttributes, type ReactNode } from 'react'
import clsx from 'clsx'

// Примитив кнопки (по образцу CodeForge Button): варианты и размеры
// через служебные классы index.css; иконочная кнопка обязана получать
// aria-label от вызывающего.
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  leadingIcon?: ReactNode
  children?: ReactNode
}

const VARIANT_CLASS: Record<Variant, string> = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  ghost: 'btn-ghost',
  danger: 'btn-danger',
}

export default function Button({
  variant = 'primary', size = 'md', leadingIcon, children, className,
  type = 'button', ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(
        VARIANT_CLASS[variant],
        size === 'sm' && 'px-2.5 py-1.5 text-xs',
        className,
      )}
      {...rest}
    >
      {leadingIcon}
      {children}
    </button>
  )
}
