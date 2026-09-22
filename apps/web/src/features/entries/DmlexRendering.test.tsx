import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DmlexRendering } from '@/features/entries/DmlexRendering'

const entry = {
  id: 'entry.id.5',
  headword: 'baby',
  homographNumber: 1,
  partsOfSpeech: [{ tag: 'noun' }],
  labels: [{ tag: 'fam.' }],
  pronunciations: [{ transcriptions: [{ text: 'ˈbeɪbi', scheme: 'ipa' }] }],
  inflectedForms: [{ text: 'babies', tag: 'pl' }],
  senses: [
    {
      id: 'entry.id.5.sense.id.1',
      indicator: 'o otroku',
      definitions: [{ text: 'a very young child', definitionType: 'gloss' }],
      headwordTranslations: [
        { text: 'dojenček', langCode: 'sl' },
        { text: 'dojenčica', langCode: 'sl' },
      ],
      examples: [
        {
          text: 'a baby girl',
          exampleTranslations: [{ text: 'deklica', langCode: 'sl' }],
        },
      ],
    },
    { id: 'entry.id.5.sense.id.2', definitions: [{ text: 'the youngest of a group' }] },
  ],
  etymologies: [{ description: 'from Middle English' }],
}

describe('DmlexRendering', () => {
  it('names every field the way DMLex does and shows every id', () => {
    render(<DmlexRendering entry={entry} />)
    const names = screen.getAllByTestId('dmlex-field').map((e) => e.textContent)
    expect(names).toEqual([
      'entry',
      'partOfSpeech',
      'label',
      'transcription',
      'inflectedForm',
      'indicator',
      'definition',
      'headwordTranslation 1',
      'headwordTranslation 2',
      'exampleTranslation',
      'definition',
      'description',
    ])
    expect(screen.getAllByTestId('dmlex-id').map((e) => e.textContent)).toEqual([
      'entry.id.5',
      'entry.id.5.sense.id.1',
      'entry.id.5.sense.id.2',
    ])
    expect(screen.getByText('baby')).toBeInTheDocument()
    expect(screen.getByTitle('homographNumber')).toHaveTextContent('1')
    expect(screen.getByText('noun')).toBeInTheDocument()
    expect(screen.getByText('dojenček')).toBeInTheDocument()
    expect(screen.getByTitle('scheme')).toHaveTextContent('ipa')
    expect(screen.getAllByTitle('langCode')).toHaveLength(3)

    // senses, examples and pronunciations are blocks with their name and number
    const senses = screen.getAllByTestId('dmlex-sense')
    expect(senses).toHaveLength(2)
    expect(within(senses[0]!).getByText('sense 1')).toBeInTheDocument()
    expect(within(senses[0]!).getByText('a baby girl')).toBeInTheDocument()
    expect(screen.getByTestId('dmlex-example')).toHaveTextContent(
      /exampleTranslation\s*deklica\s*sl/,
    )
    expect(screen.getByTestId('dmlex-pronunciation')).toHaveTextContent(
      /transcription\s*ˈbeɪbi\s*ipa/,
    )
    // an extension module is shown too
    expect(screen.getByTestId('dmlex-etymology')).toHaveTextContent('from Middle English')
  })

  it('copes with an entry that has only a headword', () => {
    render(<DmlexRendering entry={{ headword: 'x' }} />)
    expect(screen.getByText('No fields besides the headword.')).toBeInTheDocument()
  })
})
