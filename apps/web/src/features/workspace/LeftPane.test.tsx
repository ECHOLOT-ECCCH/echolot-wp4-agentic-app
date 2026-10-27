import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, renderWithProviders } from '@/test/utils'
import type { Workspace } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

vi.mock('@tanstack/react-router', async () => {
  const actual =
    await vi.importActual<typeof import('@tanstack/react-router')>('@tanstack/react-router')
  return {
    ...actual,
    Link: ({ children, ...rest }: { children: React.ReactNode; to?: string }) => (
      <a href={rest.to ?? '#'}>{children}</a>
    ),
  }
})

import { LeftPane } from '@/features/workspace/LeftPane'

afterEach(() => vi.unstubAllGlobals())

const workspace: Workspace = {
  id: 'w1',
  name: 'Basque people',
  description: 'ehkultura persons',
  created_at: '',
  updated_at: '',
}

const file = (over: Record<string, unknown>) => ({
  id: 'f1',
  workspace_id: 'w1',
  kind: 'input',
  name: 'people.csv',
  size: 2048,
  sha256: null,
  mime: 'text/csv',
  meta: { format: 'csv', record_count: 1234, columns: ['uri', 'name'] },
  created_at: '',
  ...over,
})

describe('LeftPane', () => {
  it('lists files with their record counts and opens them', async () => {
    mockApi([
      {
        path: '/api/workspaces/w1/files',
        body: [file({}), file({ id: 'f2', name: 'notes.txt', size: 10, meta: {} })],
      },
    ])
    renderWithProviders(<LeftPane workspace={workspace} />)
    expect(await screen.findByText('people.csv')).toBeInTheDocument()
    expect(screen.getByText('1,234 records')).toBeInTheDocument()
    expect(screen.getByText('10 B')).toBeInTheDocument()
    expect(screen.getByText(/Drop CSV, TSV, JSON or JSONL/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('people.csv'))
    expect(usePreview.getState().target).toEqual({ kind: 'file', fileId: 'f1' })
    fireEvent.click(screen.getByText('Task notes'))
    expect(usePreview.getState().target).toEqual({ kind: 'task' })
  })

  it('says when there are no files yet', async () => {
    mockApi([{ path: '/api/workspaces/w1/files', body: [] }])
    renderWithProviders(<LeftPane workspace={workspace} />)
    expect(await screen.findByText('No files yet.')).toBeInTheDocument()
  })
})
