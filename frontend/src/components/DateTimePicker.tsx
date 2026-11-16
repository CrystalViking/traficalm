import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useSettings } from '../settings'
import { formatEuropeanDateTime, toLocalInputValue } from '../utils/format'

type Props = {
  id: string
  /** Local date-time as "YYYY-MM-DDTHH:mm". */
  value: string
  onChange: (value: string) => void
}

const ITEM_HEIGHT = 44
const DAYS_AHEAD = 30
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const MINUTES = Array.from({ length: 60 }, (_, m) => m)
const pad = (n: number) => String(n).padStart(2, '0')
const dayKey = (date: Date) => toLocalInputValue(date).slice(0, 10)

function toDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? new Date() : date
}

function Wheel({ label, values, value, onChange }: { label: string, values: number[], value: number, onChange: (value: number) => void }) {
  const list = useRef<HTMLDivElement>(null)
  const settle = useRef<number | undefined>(undefined)

  useLayoutEffect(() => {
    const element = list.current
    const target = values.indexOf(value) * ITEM_HEIGHT
    if (element && Math.abs(element.scrollTop - target) > 1) element.scrollTop = target
  }, [value, values])
  useEffect(() => () => window.clearTimeout(settle.current), [])

  const select = (next: number) => {
    const element = list.current
    const top = values.indexOf(next) * ITEM_HEIGHT
    if (element?.scrollTo) element.scrollTo({ top, behavior: 'smooth' })
    onChange(next)
  }
  const onScroll = () => {
    window.clearTimeout(settle.current)
    settle.current = window.setTimeout(() => {
      const element = list.current
      if (!element) return
      const index = Math.min(values.length - 1, Math.max(0, Math.round(element.scrollTop / ITEM_HEIGHT)))
      if (values[index] !== value) onChange(values[index])
    }, 90)
  }
  const onKeyDown = (event: ReactKeyboardEvent) => {
    const index = values.indexOf(value)
    if (event.key === 'ArrowDown' && index < values.length - 1) select(values[index + 1])
    else if (event.key === 'ArrowUp' && index > 0) select(values[index - 1])
    else return
    event.preventDefault()
  }

  return (
    <div className="wheel">
      <div className="wheel-highlight" aria-hidden="true" />
      <div ref={list} className="wheel-list" role="listbox" aria-label={label} tabIndex={0}
        aria-activedescendant={`${label}-${value}`} onScroll={onScroll} onKeyDown={onKeyDown}>
        {values.map(item => (
          <div key={item} id={`${label}-${item}`} role="option" aria-selected={item === value}
            className={`wheel-item${item === value ? ' selected' : ''}`} onClick={() => select(item)}>
            {pad(item)}
          </div>
        ))}
      </div>
    </div>
  )
}

/** Mobile-style bottom sheet with a date strip and 24-hour time wheels, independent of the browser locale. */
export function DateTimePicker({ id, value, onChange }: Props) {
  const { t, language } = useSettings()
  const locale = language === 'pl' ? 'pl-PL' : 'en-GB'
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const [today, setToday] = useState(() => new Date())
  const trigger = useRef<HTMLButtonElement>(null)
  const sheet = useRef<HTMLDivElement>(null)
  const dateStrip = useRef<HTMLDivElement>(null)

  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }
  const show = () => {
    setDraft(value)
    setToday(new Date())
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    sheet.current?.focus()
    dateStrip.current?.querySelector('.date-chip.selected')?.scrollIntoView?.({ inline: 'center', block: 'nearest' })
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const draftDate = toDate(draft)
  const setDraftParts = (date: Date, hour = draftDate.getHours(), minute = draftDate.getMinutes()) =>
    setDraft(toLocalInputValue(new Date(date.getFullYear(), date.getMonth(), date.getDate(), hour, minute)))
  const dayLabel = (date: Date) => {
    const offset = Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
      - new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()) / 86_400_000)
    if (offset === 0) return t('Today')
    if (offset === 1) return t('Tomorrow')
    return date.toLocaleDateString(locale, { weekday: 'short' })
  }

  const days = Array.from({ length: DAYS_AHEAD }, (_, i) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + i))
  if (!days.some(day => dayKey(day) === dayKey(draftDate))) {
    days.unshift(new Date(draftDate.getFullYear(), draftDate.getMonth(), draftDate.getDate()))
  }
  const quickPicks = [
    { label: t('Now'), minutes: 0 },
    { label: t('In {minutes} min', { minutes: 15 }), minutes: 15 },
    { label: t('In {minutes} min', { minutes: 30 }), minutes: 30 },
    { label: t('In 1 h'), minutes: 60 },
  ]
  const current = toDate(value)
  const [datePart, timePart] = formatEuropeanDateTime(value).split(' ')

  return (
    <>
      <button ref={trigger} id={id} type="button" className="leave-at-trigger" aria-haspopup="dialog" aria-expanded={open} onClick={show}>
        <span className="leave-at-trigger-date">{dayLabel(current)} · {datePart}</span>
        <span className="leave-at-trigger-time">{timePart}</span>
      </button>
      {open && createPortal(
        <div className="sheet-backdrop" onClick={event => { if (event.target === event.currentTarget) close() }}>
          <div ref={sheet} className="sheet" role="dialog" aria-modal="true" aria-label={t('Leave at')} tabIndex={-1}>
            <div className="sheet-handle" aria-hidden="true" />
            <div className="sheet-header">
              <h2>{t('Leave at')}</h2>
              <p className="sheet-summary">
                {draftDate.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long' })}
                <strong>{pad(draftDate.getHours())}:{pad(draftDate.getMinutes())}</strong>
              </p>
            </div>
            <div className="quick-picks">
              {quickPicks.map(pick => (
                <button key={pick.minutes} type="button" className="quick-pick"
                  onClick={() => setDraft(toLocalInputValue(new Date(Date.now() + pick.minutes * 60_000)))}>
                  {pick.label}
                </button>
              ))}
            </div>
            <div ref={dateStrip} className="date-strip" role="listbox" aria-label={t('Date')}>
              {days.map(day => {
                const selected = dayKey(day) === dayKey(draftDate)
                return (
                  <button key={dayKey(day)} type="button" role="option" aria-selected={selected}
                    aria-label={day.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                    className={`date-chip${selected ? ' selected' : ''}`} onClick={() => setDraftParts(day)}>
                    <span className="date-chip-weekday">{dayLabel(day)}</span>
                    <span className="date-chip-day">{day.getDate()}</span>
                    <span className="date-chip-month">{day.toLocaleDateString(locale, { month: 'short' })}</span>
                  </button>
                )
              })}
            </div>
            <div className="wheels">
              <Wheel label={t('Hour')} values={HOURS} value={draftDate.getHours()}
                onChange={hour => setDraftParts(draftDate, hour)} />
              <span className="wheel-colon" aria-hidden="true">:</span>
              <Wheel label={t('Minute')} values={MINUTES} value={draftDate.getMinutes()}
                onChange={minute => setDraftParts(draftDate, undefined, minute)} />
            </div>
            <div className="sheet-actions">
              <button type="button" className="sheet-cancel" onClick={close}>{t('Cancel')}</button>
              <button type="button" className="sheet-done" onClick={() => { onChange(draft); close() }}>{t('Done')}</button>
            </div>
          </div>
        </div>,
        document.querySelector('.app') ?? document.body,
      )}
    </>
  )
}
