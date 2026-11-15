import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeItinerary } from '../test/fixtures'
import { CrowdBadge } from './CrowdBadge'
import { ItineraryDetails } from './ItineraryDetails'
import { RouteOptions } from './RouteOptions'
import type { StopCall } from '../types'

describe('CrowdBadge', () => {
  it('shows a clamped percentage', () => {
    render(<CrowdBadge value={123.4} />)
    expect(screen.getByLabelText('Crowd index 100% (very high crowd)')).toHaveTextContent('100%')
  })
})

describe('RouteOptions', () => {
  it('lists options sorted from the least crowded with their crowd index', async () => {
    const onSelect = vi.fn()
    render(
      <RouteOptions
        itineraries={[
          makeItinerary('busy', 80),
          makeItinerary('calm', 12, { crowd_avoiding: true }),
          makeItinerary('mid', 40, { transfers: 1, type: 'transfer' }),
        ]}
        selectedId="calm"
        onSelect={onSelect}
      />,
    )
    const items = screen.getAllByRole('listitem')
    expect(items.map((li) => within(li).getByLabelText(/Crowd index/).textContent)).toEqual(['👥 12%', '👥 40%', '👥 80%'])
    expect(within(items[0]).getByText(/Avoids crowded stops/)).toBeInTheDocument()
    expect(within(items[1]).getByText('1 change')).toBeInTheDocument()
    expect(within(items[0]).getByText('🚋 8')).toBeInTheDocument()

    await userEvent.click(within(items[2]).getByRole('button'))
    expect(onSelect).toHaveBeenCalledWith('busy')
  })

  it('shows a message when there are no options', () => {
    render(<RouteOptions itineraries={[]} selectedId={null} onSelect={() => {}} />)
    expect(screen.getByText(/No connections found/)).toBeInTheDocument()
  })
})

describe('ItineraryDetails', () => {
  it('suggests alterations only above tolerance and allows selecting any stop', async () => {
    const onAvoidStop = vi.fn()
    const itinerary = makeItinerary('x', 30)
    function EditableDetails({ tolerance }: { tolerance: number }) {
      const [stop, setStop] = useState<StopCall | null>(null)
      return <ItineraryDetails itinerary={itinerary} realtime={{}} crowdTolerance={tolerance}
        onAvoidStop={onAvoidStop} selectedStop={stop} onSelectStop={setStop} />
    }
    const { rerender } = render(<EditableDetails tolerance={35} />)
    expect(screen.queryByRole('button', { name: 'Find alternatives avoiding Stop 3' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Keep Stop 3' }))
    expect(screen.queryByRole('button', { name: 'Yes, avoid Stop 3' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Stop 3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Yes, avoid Stop 3' }))
    expect(onAvoidStop).toHaveBeenCalledTimes(1)
    expect(onAvoidStop).toHaveBeenCalledWith(itinerary.legs[1].stops[1])
    rerender(<EditableDetails tolerance={45} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Find alternatives avoiding Stop 1' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Stop 1' }))
    expect(screen.getByRole('button', { name: 'Find alternatives avoiding Stop 1' })).toBeEnabled()
  })

  it('lists stops with planned and real-time times', () => {
    render(
      <ItineraryDetails
        itinerary={makeItinerary('x', 30)}
        realtime={{
          'T8-1:20261116': {
            trip_id: 'T8-1',
            has_realtime: true,
            vehicle: null,
            stops: [
              {
                stop_id: 'S3',
                stop_sequence: 3,
                delay_seconds: 300,
                realtime_arrival: '2026-11-16T07:39:00+01:00',
                realtime_departure: '2026-11-16T07:39:00+01:00',
              },
            ],
          },
        }}
      />,
    )
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent('Stop 1')
    expect(rows[1]).toHaveTextContent('07:26')
    expect(rows[2]).toHaveTextContent('07:34')
    expect(rows[2]).toHaveTextContent('07:39 (+5 min)')
    expect(screen.getByText('● live')).toBeInTheDocument()
    expect(screen.getByText('Morning rush hour')).toBeInTheDocument()
  })
})
