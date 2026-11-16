import { useLayoutEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

interface Props {
  open: boolean
  children: ReactNode
}

const EXIT_MS = 220

// Slides over the cells to the right of the host cell's left edge, from right to left.
export function SlideAction({ open, children }: Props) {
  const clip = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(open)
  const [width, setWidth] = useState(0)

  if (open && !mounted) setMounted(true)

  useLayoutEffect(() => {
    if (!mounted) return
    const cell = clip.current?.parentElement
    const row = cell?.closest('tr')
    if (!cell || !row) return
    const measure = () => setWidth(row.getBoundingClientRect().right - cell.getBoundingClientRect().left)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(row)
    return () => observer.disconnect()
  }, [mounted])

  useLayoutEffect(() => {
    if (open || !mounted) return
    const timer = setTimeout(() => setMounted(false), EXIT_MS)
    return () => clearTimeout(timer)
  }, [open, mounted])

  if (!mounted) return null
  return (
    <div ref={clip} className="slide-clip" style={{ width: width || undefined }}>
      <div className={open ? 'slide-panel opening' : 'slide-panel closing'}>{children}</div>
    </div>
  )
}
