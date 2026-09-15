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

import { ProjectsPage } from '@/features/project/ProjectsPage'

afterEach(() => vi.unstubAllGlobals())

describe('ProjectsPage', () => {
  it('lists the projects of the signed-in user', async () => {
    mockApi([
      { path: '/api/me', body: { id: 'u1', email: 'dev@example.com', name: 'Dev', role: 'user' } },
      {
        path: '/api/projects',
        body: [
          {
            id: 'p1',
            owner_id: 'u1',
            name: 'Slovar',
            description: 'A test dictionary',
            source_type: 'xml',
            default_model: null,
            settings: {},
            created_at: '2026-09-15T10:00:00Z',
            updated_at: '2026-09-15T10:00:00Z',
            my_role: 'owner',
          },
        ],
      },
    ])
    renderWithProviders(<ProjectsPage />)
    expect(await screen.findByText('Slovar')).toBeInTheDocument()
    expect(screen.getByText('A test dictionary')).toBeInTheDocument()
    expect(screen.getByText('XML')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /new project/i })).toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    mockApi([
      { path: '/api/me', body: { id: 'u1', email: 'dev@example.com', name: 'Dev', role: 'user' } },
      { path: '/api/projects', body: [] },
    ])
    renderWithProviders(<ProjectsPage />)
    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })
})
