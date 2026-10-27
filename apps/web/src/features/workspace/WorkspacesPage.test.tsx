import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, renderWithProviders } from '@/test/utils'

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

import { WorkspacesPage } from '@/features/workspace/WorkspacesPage'

afterEach(() => vi.unstubAllGlobals())

describe('WorkspacesPage', () => {
  it('lists the workspaces', async () => {
    mockApi([
      {
        path: '/api/workspaces',
        body: [
          {
            id: 'w1',
            name: 'Basque people',
            description: 'ehkultura persons',
            created_at: '2026-10-27T10:00:00Z',
            updated_at: '2026-10-27T10:00:00Z',
          },
        ],
      },
    ])
    renderWithProviders(<WorkspacesPage />)
    expect(await screen.findByText('Basque people')).toBeInTheDocument()
    expect(screen.getByText('ehkultura persons')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /new workspace/i })).toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    mockApi([{ path: '/api/workspaces', body: [] }])
    renderWithProviders(<WorkspacesPage />)
    expect(await screen.findByText('No workspaces yet')).toBeInTheDocument()
  })
})
