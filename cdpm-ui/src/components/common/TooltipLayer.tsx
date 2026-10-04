import { useEffect, useRef, useState } from 'react'

// Стилизованные подсказки CodeForge (УИ-12, решение владельца
// 2026-08-20) — один глобальный слой вместо системных подсказок
// браузера. Источник текста — обычный атрибут title любого элемента:
// при первом наведении текст переносится в data-tip (чтобы системная
// подсказка не дублировала нашу), слой показывает его в панели темы
// быстрее и читабельнее системной. Компоненты продолжают писать
// title — специальной разметки не требуется.
const DELAY_MS = 180
const WIDTH = 340

interface Tip {
  text: string
  x: number       // центр якоря по горизонтали (вьюпорт)
  top: number     // нижняя кромка якоря
  bottom: number  // верхняя кромка якоря (для показа сверху)
  flipUp: boolean
}

export default function TooltipLayer() {
  const [tip, setTip] = useState<Tip | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const anchor = useRef<Element | null>(null)
  // Для какого элемента панель СЕЙЧАС видна: без этого повторное
  // наведение на тот же элемент после скрытия не показывало панель
  // (якорь совпадал, таймер не взводился — поймано при проверке).
  const shownFor = useRef<Element | null>(null)

  useEffect(() => {
    const hide = () => {
      window.clearTimeout(timer.current)
      anchor.current = null
      shownFor.current = null
      setTip(null)
    }
    const onOver = (e: MouseEvent) => {
      const hit = (e.target as Element | null)
        ?.closest?.('[title], [data-tip]') as HTMLElement | null
      if (!hit) return
      if (hit.title) {
        hit.dataset.tip = hit.title
        hit.removeAttribute('title')
      }
      const text = (hit.dataset.tip || '').trim()
      if (!text) return
      // Панель уже видна для этого элемента — не перевзводить
      // (mouseover пузырится от каждого дочернего элемента).
      if (anchor.current === hit && shownFor.current === hit) return
      const fresh = anchor.current !== hit
      anchor.current = hit
      if (fresh) {
        shownFor.current = null
        setTip(null)        // смена якоря — прежняя панель скрывается
      }
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        if (anchor.current !== hit) return
        const r = hit.getBoundingClientRect()
        shownFor.current = hit
        setTip({
          text,
          x: r.left + r.width / 2,
          top: r.bottom + 8,
          bottom: r.top - 8,
          flipUp: r.bottom > window.innerHeight - 140,
        })
      }, DELAY_MS)
    }
    const onOut = (e: MouseEvent) => {
      const el = anchor.current
      if (!el) return
      const to = e.relatedTarget as Node | null
      if (to && el.contains(to)) return
      if (el !== (e.target as Element | null)
            ?.closest?.('[data-tip]')) return
      hide()
    }
    document.addEventListener('mouseover', onOver, true)
    document.addEventListener('mouseout', onOut, true)
    document.addEventListener('mousedown', hide, true)
    window.addEventListener('scroll', hide, true)
    return () => {
      document.removeEventListener('mouseover', onOver, true)
      document.removeEventListener('mouseout', onOut, true)
      document.removeEventListener('mousedown', hide, true)
      window.removeEventListener('scroll', hide, true)
    }
  }, [])

  if (!tip) return null
  const left = Math.min(Math.max(8, tip.x - WIDTH / 2),
                        Math.max(8, window.innerWidth - WIDTH - 8))
  return (
    <div
      className="cf-tooltip"
      role="tooltip"
      style={{
        left,
        maxWidth: WIDTH,
        top: tip.flipUp ? tip.bottom : tip.top,
        transform: tip.flipUp ? 'translateY(-100%)' : undefined,
      }}
    >
      {tip.text}
    </div>
  )
}
