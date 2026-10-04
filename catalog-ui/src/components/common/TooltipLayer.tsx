import { useEffect, useRef, useState } from 'react'

// Styled tooltips (same layer as the CDPM panel): one global layer
// instead of the browser's native tooltip. The text source is the plain
// title attribute of any element: on the first hover the text moves to
// data-tip (so the native tooltip does not duplicate ours) and the layer
// shows it on theme surfaces. Components keep writing title - no special
// markup is required.
const DELAY_MS = 180
const WIDTH = 340

interface Tip {
  text: string
  x: number       // anchor centre, horizontally (viewport)
  top: number     // bottom edge of the anchor
  bottom: number  // top edge of the anchor (when flipped up)
  flipUp: boolean
}

export default function TooltipLayer() {
  const [tip, setTip] = useState<Tip | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const anchor = useRef<Element | null>(null)
  // Which element the panel is visible for right now: without it a
  // repeated hover over the same element did not show the panel again.
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
      if (anchor.current === hit && shownFor.current === hit) return
      const fresh = anchor.current !== hit
      anchor.current = hit
      if (fresh) {
        shownFor.current = null
        setTip(null)
      }
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        if (anchor.current !== hit) return
        const rect = hit.getBoundingClientRect()
        shownFor.current = hit
        setTip({
          text,
          x: rect.left + rect.width / 2,
          top: rect.bottom + 8,
          bottom: rect.top - 8,
          flipUp: rect.bottom > window.innerHeight - 140,
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
