import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EntryNavigator } from '@/features/entries/EntryNavigator'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const page = (items: { ordinal: number; headword: string }[], total: number) => ({
  items: items.map((i) => ({
    id: `e${i.ordinal}`,
    document_id: 'd1',
    snippet: `${i.headword} …`,
    ...i,
  })),
  total,
  offset: 0,
  limit: 50,
})

describe('EntryNavigator', () => {
  it('lists entries, pages and searches', async () => {
    const { calls } = mockApi([
      {
        path: /\/entries\?.*q=baby/,
        body: page([{ ordinal: 20, headword: 'baby' }], 1),
      },
      {
        path: /\/entries\?offset=50/,
        body: page([{ ordinal: 50, headword: 'baby-sit' }], 120),
      },
      {
        path: /\/entries\?offset=0/,
        body: page(
          [
            { ordinal: 0, headword: 'azyme' },
            { ordinal: 1, headword: 'b' },
          ],
          120,
        ),
      },
    ])
    const onSelect = vi.fn()
    renderWithProviders(
      <EntryNavigator projectId="p1" documentId="d1" selectedEntryId={null} onSelect={onSelect} />,
    )
    expect(await screen.findByText('azyme')).toBeInTheDocument()
    expect(screen.getByText('1–50 of 120')).toBeInTheDocument()

    fireEvent.click(screen.getByText('b'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'e1', headword: 'b' }))

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('baby-sit')).toBeInTheDocument()
    expect(screen.getByText('51–100 of 120')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Search entries'), { target: { value: 'baby' } })
    expect(await screen.findByText('baby', { exact: true })).toBeInTheDocument()
    await waitFor(() => expect(calls.some((c) => c.url.includes('q=baby'))).toBe(true))
  })
})
