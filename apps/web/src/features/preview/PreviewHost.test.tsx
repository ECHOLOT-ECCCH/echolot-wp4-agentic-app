import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { PreviewHost } from '@/features/preview/PreviewHost'
import { usePreview } from '@/lib/uiActions'
import { mockApi, renderWithProviders } from '@/test/utils'

const file = {
  id: 'f1',
  workspace_id: 'w1',
  kind: 'input',
  name: 'people.csv',
  size: 4096,
  sha256: null,
  mime: 'text/csv',
  meta: { format: 'csv', record_count: 120, columns: ['uri', 'name'] },
  created_at: '',
}

function page(offset: number) {
  const rows = Array.from({ length: Math.min(50, 120 - offset) }, (_, i) => offset + i)
  return {
    total: 120,
    offset,
    columns: ['uri', 'name'],
    records: rows.map((row) => ({ row, record: { uri: `u${row}`, name: `Name ${row}` } })),
  }
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  mockApi([
    { path: '/api/workspaces/w1/files/f1/records?offset=0', body: page(0) },
    { path: '/api/workspaces/w1/files/f1/records?offset=50', body: page(50) },
    { path: '/api/workspaces/w1/files/f1/records?offset=100', body: page(100) },
    { path: '/api/workspaces/w1/files', body: [file] },
  ])
})
afterEach(() => {
  vi.unstubAllGlobals()
  usePreview.getState().clear()
})

describe('PreviewHost records view', () => {
  it('pages through records and selects a row', async () => {
    act(() => usePreview.getState().openFile('f1'))
    renderWithProviders(<PreviewHost workspaceId="w1" />)
    expect(await screen.findByText('Name 0')).toBeInTheDocument()
    expect(screen.getByText('rows 0–49 of 120')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Name 50')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Name 57'))
    expect(usePreview.getState().target).toEqual({ kind: 'file', fileId: 'f1', row: 57 })
    expect(screen.getByText('Name 57').closest('tr')).toHaveAttribute('aria-selected', 'true')
  })

  it('jumps to the page of a row the agent opened', async () => {
    act(() => usePreview.getState().apply({ type: 'open_file', file_id: 'f1', row: 103 }))
    renderWithProviders(<PreviewHost workspaceId="w1" />)
    expect(await screen.findByText('Name 103')).toBeInTheDocument()
    expect(screen.getByText('rows 100–119 of 120')).toBeInTheDocument()
  })
})
