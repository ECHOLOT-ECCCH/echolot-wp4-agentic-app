import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ScriptView } from '@/features/scripts/ScriptView'
import { usePreview } from '@/lib/uiActions'
import { mockApi, renderWithProviders } from '@/test/utils'

vi.mock('@/features/preview/CodeEditor', () => ({
  CodeEditor: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <textarea aria-label="Script code" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}))

const summary = (version: number, author: 'agent' | 'user') => ({
  id: `s${version}`,
  version,
  note: version === 2 ? 'match on name' : null,
  author,
  services: ['echolot'],
  sha256: 'x',
  created_at: '2026-10-27T10:00:00Z',
})
const record = (version: number, author: 'agent' | 'user', code: string) => ({
  ...summary(version, author),
  workspace_id: 'w1',
  code,
})

beforeEach(() => act(() => usePreview.getState().open({ kind: 'script' })))
afterEach(() => vi.unstubAllGlobals())

describe('ScriptView', () => {
  it('shows the latest version and saves an edit as a new version', async () => {
    const { calls } = mockApi([
      {
        path: '/api/workspaces/w1/scripts/2',
        body: record(2, 'agent', 'def process(r, ctx):\n  return {}\n'),
      },
      { path: '/api/workspaces/w1/scripts/3', body: record(3, 'user', 'edited') },
      {
        method: 'POST',
        path: '/api/workspaces/w1/scripts',
        status: 201,
        body: record(3, 'user', 'edited'),
      },
      { path: '/api/workspaces/w1/scripts', body: [summary(2, 'agent'), summary(1, 'agent')] },
    ])
    renderWithProviders(<ScriptView workspaceId="w1" />)
    const editor = await screen.findByLabelText('Script code')
    expect(editor).toHaveValue('def process(r, ctx):\n  return {}\n')
    expect(screen.getByText(/match on name/)).toBeInTheDocument()
    expect(screen.getByText('echolot')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save as new version/i })).toBeDisabled()

    fireEvent.change(editor, { target: { value: 'edited' } })
    fireEvent.change(screen.getByLabelText('Version note'), { target: { value: 'tweak' } })
    fireEvent.click(screen.getByRole('button', { name: /save as new version/i }))
    await waitFor(() =>
      expect(usePreview.getState().target).toEqual({ kind: 'script', version: 3 }),
    )
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/scripts'))).toBe(true)
  })

  it('shows why the server refused a script', async () => {
    mockApi([
      { path: '/api/workspaces/w1/scripts/1', body: record(1, 'agent', 'ok') },
      {
        method: 'POST',
        path: '/api/workspaces/w1/scripts',
        status: 422,
        body: { detail: 'process takes (record, ctx); it is defined with (record)' },
      },
      { path: '/api/workspaces/w1/scripts', body: [summary(1, 'agent')] },
    ])
    renderWithProviders(<ScriptView workspaceId="w1" />)
    fireEvent.change(await screen.findByLabelText('Script code'), { target: { value: 'bad' } })
    fireEvent.click(screen.getByRole('button', { name: /save as new version/i }))
    expect(await screen.findByText(/it is defined with \(record\)/)).toBeInTheDocument()
  })
})
