import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockApi, renderWithProviders } from '@/test/utils'
import type { Project } from '@/lib/types'

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

import { LeftPane } from '@/features/project/LeftPane'

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

const project = (source_type: 'xml' | 'pdf'): Project => ({
  id: 'p1',
  owner_id: 'u1',
  name: 'Oxford',
  description: null,
  source_type,
  default_model: null,
  settings: {},
  created_at: '',
  updated_at: '',
  my_role: 'owner',
})

const file = {
  id: 'f1',
  project_id: 'p1',
  kind: 'source',
  name: 'oxford.xml',
  size: 1000,
  sha256: '',
  mime: 'application/xml',
  meta: {},
  created_by: null,
  created_at: '',
}
const document = {
  id: 'd1',
  project_id: 'p1',
  source_file_id: 'f1',
  type: 'xml',
  status: 'ready',
  file_name: 'oxford.xml',
  entry_count: 102,
  page_count: null,
  error: null,
  parsed_at: null,
  created_at: '',
  updated_at: '',
}

function routes(documents: unknown[]) {
  return mockApi([
    { path: '/api/projects/p1/files', body: [file] },
    {
      path: '/api/projects/p1/members',
      body: [{ user_id: 'u1', email: 'a@b.c', name: 'Ada', role: 'owner' }],
    },
    { path: '/api/projects/p1/documents', body: documents },
    { path: '/api/projects/p1/runs', body: [] },
    { path: '/api/projects/p1/steps', body: [] },
    { path: '/api/projects/p1/mappings', body: [] },
  ])
}

describe('LeftPane', () => {
  it('shows an XML project with the essentials and the rest behind Show more', async () => {
    routes([document])
    renderWithProviders(<LeftPane project={project('xml')} />)
    expect(await screen.findByText('102 entries')).toBeInTheDocument()
    expect(screen.getByText('Mapping')).toBeInTheDocument()
    const more = await screen.findByRole('button', { name: 'Show more' })
    expect(await screen.findByText('1 file · 0 runs · 0 steps · members')).toBeInTheDocument()
    expect(screen.queryByText('Files')).not.toBeInTheDocument()
    expect(screen.queryByText('Members')).not.toBeInTheDocument()

    fireEvent.click(more)
    expect(await screen.findByText('Files')).toBeInTheDocument()
    expect(screen.getByText('Members')).toBeInTheDocument()
    expect(screen.getByText('Ada')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
    expect(localStorage.getItem('elx.leftpane.more.p1')).toBe('1')
  })

  it('opens a fresh XML project with the upload area', async () => {
    routes([])
    renderWithProviders(<LeftPane project={project('xml')} />)
    expect(await screen.findByText('Files')).toBeInTheDocument()
    expect(screen.getByText(/Drop XML or PDF files here/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })

  it('leaves a PDF project as it was', async () => {
    routes([{ ...document, type: 'pdf', page_count: 4 }])
    renderWithProviders(<LeftPane project={project('pdf')} />)
    expect(await screen.findByText('Files')).toBeInTheDocument()
    expect(await screen.findByText('Members')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show more|Show less/ })).not.toBeInTheDocument()
  })
})
