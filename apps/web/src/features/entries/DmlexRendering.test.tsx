import { render, screen } from '@testing-library/react'
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
        { text: 'a baby girl', exampleTranslations: [{ text: 'deklica', langCode: 'sl' }] },
      ],
    },
    { id: 'entry.id.5.sense.id.2', definitions: [{ text: 'the youngest of a group' }] },
  ],
  etymologies: [{ description: 'from Middle English' }],
}

describe('DmlexRendering', () => {
  it('reads like a dictionary, names the field of every value on hover, keeps ids to hover', () => {
    render(<DmlexRendering entry={entry} />)
    // no id is visible, both are on hover
    expect(screen.queryByText(/entry\.id\.5/)).not.toBeInTheDocument()
    expect(screen.getByText('baby')).toHaveAttribute('title', 'headword · id entry.id.5')
    expect(screen.getByText('1.')).toHaveAttribute('title', 'sense · id entry.id.5.sense.id.1')
    expect(screen.getByText('2.')).toHaveAttribute('title', 'sense · id entry.id.5.sense.id.2')
    // values with their DMLex names
    expect(screen.getByText('noun')).toHaveAttribute('title', 'partOfSpeech')
    expect(screen.getByText('fam.')).toHaveAttribute('title', 'label')
    expect(screen.getByText('/ˈbeɪbi/')).toHaveAttribute(
      'title',
      'pronunciation › transcription · scheme ipa',
    )
    expect(screen.getByText('a very young child')).toHaveAttribute(
      'title',
      'definition · definitionType gloss',
    )
    expect(screen.getByText(/^dojenček/)).toHaveAttribute(
      'title',
      'headwordTranslation · langCode sl',
    )
    expect(screen.getByText('a baby girl')).toHaveAttribute('title', 'example')
    expect(screen.getByText(/deklica/)).toHaveAttribute('title', 'exampleTranslation · langCode sl')
    expect(screen.getByText('babies').parentElement).toHaveAttribute(
      'title',
      'inflectedForm · tag pl',
    )
    expect(screen.getByTitle('homographNumber')).toHaveTextContent('1')
    // an extension module is not hidden
    expect(screen.getByTestId('dmlex-extras')).toHaveTextContent(
      'etymologies description from Middle English',
    )
  })
})
