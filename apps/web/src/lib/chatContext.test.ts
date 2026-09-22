import { beforeEach, describe, expect, it } from 'vitest'

import { useChatContext } from '@/lib/chatContext'

beforeEach(() => useChatContext.getState().clear())

describe('chat context chips', () => {
  it('labels regions and entries and dedupes by id', () => {
    const { add, addEntry } = useChatContext.getState()
    add({ document_id: 'd1', page: 3, bbox: [1, 2, 3, 4], text: 'abacus n. a frame', words: 0 })
    addEntry({
      entry_id: 'e1',
      ordinal: 7,
      headword: 'babble',
      origin: 'converted live with mapping v3',
      source_kind: 'xml',
      source_text: '<se>\n  <hw>babble</hw>\n</se>',
      output_xml: '<entry/>',
      issues: [],
    })
    addEntry({
      entry_id: 'e1',
      ordinal: 7,
      headword: 'babble',
      origin: 'converted live with mapping v3',
      source_kind: 'xml',
      source_text: '<se><hw>babble</hw></se>',
      output_xml: '<entry/>',
      issues: ['x'],
    })
    const chips = useChatContext.getState().chips
    expect(chips.map((c) => c.label)).toEqual(['page 3, 4 words', 'entry 8 babble'])
    expect(chips[1]).toMatchObject({
      kind: 'entry',
      text: '<se><hw>babble</hw></se>',
      issues: ['x'],
    })
  })

  it('take() hands the chips out once', () => {
    useChatContext.getState().addEntry({
      source_kind: 'pdf',
      page: 2,
      headword: 'bond',
      source_text: 'bond I sam.',
      output_xml: '',
      issues: [],
    })
    expect(useChatContext.getState().take()).toHaveLength(1)
    expect(useChatContext.getState().take()).toEqual([])
  })
})
