import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ServiceCallArgs, SubmitRunArgs } from '@/features/chat/runCards'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const file = {
  id: 'f1',
  workspace_id: 'w1',
  kind: 'input',
  name: 'people.csv',
  size: 10,
  sha256: null,
  mime: 'text/csv',
  meta: { format: 'csv', record_count: 1234, columns: ['name'] },
  created_at: '',
}
const script = (version: number, services: string[]) => ({
  id: `s${version}`,
  version,
  note: null,
  author: 'agent',
  services,
  sha256: '',
  created_at: '',
})

describe('SubmitRunArgs', () => {
  it('says what the user approves: purpose, records, script and services', async () => {
    mockApi([
      { path: '/api/workspaces/w1/files', body: [file] },
      { path: '/api/workspaces/w1/scripts', body: [script(3, ['echolot', 'wikidata'])] },
    ])
    renderWithProviders(
      <SubmitRunArgs
        workspaceId="w1"
        argsText={JSON.stringify({ file_id: 'f1', purpose: 'Try 20 people', limit: 20 })}
      />,
    )
    expect(screen.getByText('Try 20 people')).toBeInTheDocument()
    expect(
      await screen.findByText(/Runs script v3 on the first 20 records of people\.csv/),
    ).toBeInTheDocument()
    expect(screen.getByText('echolot')).toBeInTheDocument()
    expect(screen.getByText('wikidata')).toBeInTheDocument()
  })

  it('names a full run', async () => {
    mockApi([
      { path: '/api/workspaces/w1/files', body: [file] },
      { path: '/api/workspaces/w1/scripts', body: [script(1, [])] },
    ])
    renderWithProviders(
      <SubmitRunArgs
        workspaceId="w1"
        argsText={JSON.stringify({ file_id: 'f1', purpose: 'All' })}
      />,
    )
    expect(await screen.findByText(/on all 1,234 records of people\.csv/)).toBeInTheDocument()
    expect(await screen.findByText(/no services/)).toBeInTheDocument()
  })
})

describe('ServiceCallArgs', () => {
  it('shows the exact request the user approves', () => {
    renderWithProviders(
      <ServiceCallArgs
        argsText={JSON.stringify({
          service: 'echolot',
          path: '/wikidata/statements',
          purpose: 'See what Wikidata holds for Chillida',
          body: { ids: ['Q156736'], properties: ['P569'] },
        })}
      />,
    )
    expect(screen.getByText('See what Wikidata holds for Chillida')).toBeInTheDocument()
    expect(screen.getByText('echolot')).toBeInTheDocument()
    expect(screen.getByText('POST /wikidata/statements')).toBeInTheDocument()
    expect(screen.getByText(/"Q156736"/)).toBeInTheDocument()
  })

  it('shows a GET with its query and no body', () => {
    renderWithProviders(
      <ServiceCallArgs
        argsText={JSON.stringify({
          service: 'echolot',
          path: '/reconcile',
          method: 'GET',
          params: { algorithm: 'rule_based' },
          purpose: 'x',
        })}
      />,
    )
    expect(screen.getByText('GET /reconcile?algorithm=rule_based')).toBeInTheDocument()
    expect(screen.getByTestId('service-call-args').querySelector('pre')).toBeNull()
  })
})
