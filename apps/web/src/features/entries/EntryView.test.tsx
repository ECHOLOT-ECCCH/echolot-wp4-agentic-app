import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CodeOutputs, EntryView } from '@/features/entries/EntryView'
import { mockApi, renderWithProviders } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const entry = {
  id: 'e1',
  document_id: 'd1',
  ordinal: 4,
  headword: 'baby',
  snippet: 'baby …',
  source_xml: '<se><hw>baby</hw></se>',
  source_ref: { start: 0, end: 10 },
}

const converted = {
  id: 'c1',
  run_id: 'r1',
  entry_id: 'e1',
  position: 0,
  ordinal: 4,
  headword: 'baby',
  issue_count: 1,
  issues: [
    {
      severity: 'error' as const,
      source: 'schema' as const,
      code: 'schema',
      path: 'sense[1]/indicator',
      message: 'duplicated indicator',
    },
  ],
  dmlex: {
    id: 'entry.id.5',
    headword: 'baby',
    partsOfSpeech: [{ tag: 'noun' }],
    senses: [
      {
        id: 'entry.id.5.sense.id.1',
        indicator: 'o otroku',
        headwordTranslations: [{ text: 'dojenček', langCode: 'sl' }],
        examples: [
          { text: 'a baby girl', exampleTranslations: [{ text: 'deklica', langCode: 'sl' }] },
        ],
      },
    ],
  },
  xml: '<entry id="entry.id.5">…</entry>',
  mapping_version: 2,
  run_created_at: '2026-09-16T00:00:00Z',
}

const mappings = [
  {
    id: 'm3',
    version: 3,
    note: '',
    source: 'agent',
    created_by: null,
    created_at: '',
    issue_count: 0,
  },
]

const preview = {
  entry_id: 'e1',
  mapping_version: 3,
  outputs: [
    {
      position: 0,
      headword: 'baby',
      dmlex: converted.dmlex,
      xml: converted.xml,
      issues: [],
      issue_count: 0,
    },
  ],
  skipped: false,
  resource_issues: [],
  plan_warnings: [],
  steps: ['Upper-case headwords'],
  warnings: ['custom steps not applied: the runner is down'],
  checked_alone: true,
}

describe('EntryView', () => {
  it('shows a run output next to the source, with a switch to the live view', async () => {
    mockApi([
      { path: /\/entries\/e1\/converted\?run_id=r1/, body: [converted] },
      { path: /\/entries\/e1$/, body: entry },
      { path: /\/mappings$/, body: mappings },
    ])
    renderWithProviders(<EntryView projectId="p1" entryId="e1" runId="r1" />)
    expect(await screen.findByText('entry 5')).toBeInTheDocument()
    expect(await screen.findByTestId('output-origin')).toHaveTextContent('From run · mapping v2')
    expect(screen.getByText('dojenček')).toBeInTheDocument()
    expect(screen.getByText('a baby girl')).toBeInTheDocument()
    expect(screen.getByText('duplicated indicator')).toBeInTheDocument()
    expect(screen.getByText('noun')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Show live (v3)' })).toBeInTheDocument()
  })

  it('converts the entry on the spot with the latest mapping when no run is given', async () => {
    const { calls } = mockApi([
      { path: /\/entries\/e1\/preview\?mapping_version=3/, body: preview },
      { path: /\/entries\/e1$/, body: entry },
      { path: /\/mappings$/, body: mappings },
    ])
    renderWithProviders(<EntryView projectId="p1" entryId="e1" runId={null} />)
    expect(await screen.findByTestId('output-origin')).toHaveTextContent(
      'Live · mapping v3 · 1 step',
    )
    expect(screen.getByText('dojenček')).toBeInTheDocument()
    expect(screen.getByTestId('live-notes')).toHaveTextContent('steps: Upper-case headwords')
    expect(screen.getByText(/the runner is down/)).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('/converted'))).toBe(false)
  })

  it('explains when the project has no mapping yet', async () => {
    mockApi([
      { path: /\/entries\/e1$/, body: entry },
      { path: /\/mappings$/, body: [] },
    ])
    renderWithProviders(<EntryView projectId="p1" entryId="e1" runId={null} />)
    expect(await screen.findByText(/No mapping yet/)).toBeInTheDocument()
  })
})

describe('CodeOutputs', () => {
  it('puts every output entry into one editor that fills the column, issues labelled', () => {
    const second = {
      ...converted,
      xml: '<entry id="entry.id.6">…</entry>',
      dmlex: { ...converted.dmlex, id: 'entry.id.6' },
      issues: [{ ...converted.issues[0]!, message: 'second output issue' }],
    }
    const { container } = renderWithProviders(
      <CodeOutputs outputs={[converted, second]} view="xml" />,
    )
    const editors = container.querySelectorAll('.cm-content')
    expect(editors).toHaveLength(1)
    expect(editors[0]?.textContent).toContain('entry.id.5')
    expect(editors[0]?.textContent).toContain('entry.id.6')
    expect(screen.getByText(/output 1: sense\[1\]\/indicator/)).toBeInTheDocument()
    expect(screen.getByText('second output issue')).toBeInTheDocument()
    expect(container.querySelector('[data-testid="code-outputs"]')?.className).toContain('flex-1')
    expect(container.querySelector('.h-\\[28rem\\]')).toBeNull()
  })
})
