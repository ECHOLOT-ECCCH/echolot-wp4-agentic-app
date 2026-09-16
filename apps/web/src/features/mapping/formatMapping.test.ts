import { describe, expect, it } from 'vitest'

import { formatMapping } from '@/features/mapping/formatMapping'

describe('formatMapping', () => {
  it('puts descriptor keys in reading order, recursively', () => {
    const stored = {
      children: [
        {
          textVals: [{ regex: 'x', attribute: 'a', outElement: 'headword', inSelector: 'w' }],
          outElement: 'entry',
          inSelector: 'e',
        },
      ],
      outElement: 'lexicographicResource',
      inSelector: '/d',
      extra: 1,
    }
    const text = formatMapping(stored)
    const keys = Object.keys(JSON.parse(text))
    expect(keys).toEqual(['inSelector', 'outElement', 'children', 'extra'])
    const entry = JSON.parse(text).children[0]
    expect(Object.keys(entry)).toEqual(['inSelector', 'outElement', 'textVals'])
    expect(Object.keys(entry.textVals[0])).toEqual([
      'outElement',
      'attribute',
      'inSelector',
      'regex',
    ])
    expect(text.startsWith('{\n  "inSelector": "/d"')).toBe(true)
  })
})
