import { screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EntryView } from '@/features/entries/EntryView'
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
      severity: 'error',
      source: 'schema',
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

describe('EntryView', () => {
  it('shows source and rendered DMLex side by side with issues', async () => {
    mockApi([
      { path: /\/entries\/e1\/converted\?run_id=r1/, body: [converted] },
      { path: /\/entries\/e1$/, body: entry },
    ])
    renderWithProviders(<EntryView projectId="p1" entryId="e1" runId="r1" />)
    expect(await screen.findByText('entry 5')).toBeInTheDocument()
    expect(await screen.findByText('· mapping v2')).toBeInTheDocument()
    expect(screen.getByText('dojenček')).toBeInTheDocument()
    expect(screen.getByText('a baby girl')).toBeInTheDocument()
    expect(screen.getByText('duplicated indicator')).toBeInTheDocument()
    expect(screen.getByText('noun')).toBeInTheDocument()
  })

  it('explains when an entry has not been converted', async () => {
    mockApi([
      { path: /\/entries\/e1\/converted$/, body: [] },
      { path: /\/entries\/e1$/, body: entry },
    ])
    renderWithProviders(<EntryView projectId="p1" entryId="e1" runId={null} />)
    expect(await screen.findByText(/Not converted yet/)).toBeInTheDocument()
  })
})
