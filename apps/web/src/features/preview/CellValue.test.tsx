import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CellValue } from '@/features/preview/CellValue'
import { cellTitle, decisionOptions, isCandidateList } from '@/lib/cells'

const matches = [
  {
    url: 'http://en.wikipedia.org/wiki/Brian_O%27Doherty',
    wikidata_item_id: 'Q3777954',
    title: "Brian O'Doherty",
    score: 1,
  },
  {
    url: 'http://en.wikipedia.org/wiki/Doherty',
    wikidata_item_id: null,
    title: 'Doherty',
    score: 0.4321,
  },
  { url: 'http://en.wikipedia.org/wiki/A', wikidata_item_id: 'Q1', title: 'A', score: 0.2 },
  { url: 'http://en.wikipedia.org/wiki/B', wikidata_item_id: 'Q2', title: 'B', score: 0.1 },
]

describe('CellValue', () => {
  it('renders reconciliation candidates as a ranked list of links', () => {
    render(<CellValue value={matches} />)
    expect(screen.getByRole('link', { name: 'Q3777954' })).toHaveAttribute(
      'href',
      'https://www.wikidata.org/wiki/Q3777954',
    )
    expect(screen.getByRole('link', { name: "Brian O'Doherty" })).toHaveAttribute(
      'href',
      'http://en.wikipedia.org/wiki/Brian_O%27Doherty',
    )
    expect(screen.getByText('no QID')).toBeInTheDocument()
    expect(screen.getByText('0.43')).toHaveAttribute(
      'title',
      expect.stringContaining('not a certainty'),
    )
    expect(screen.getByText('+1 more')).toBeInTheDocument()
    expect(cellTitle(matches).split('\n')[3]).toBe('4. Q2 B (score 0.10)')
  })

  it('links QIDs and URLs and keeps clicks on links from selecting the row', () => {
    const onRow = vi.fn()
    render(
      <table>
        <tbody>
          <tr onClick={onRow}>
            <td>
              <CellValue value="Q937" />
            </td>
            <td>
              <CellValue value="https://ehkultura.wikibase.cloud/entity/Q1" />
            </td>
          </tr>
        </tbody>
      </table>,
    )
    fireEvent.click(screen.getByRole('link', { name: 'Q937' }))
    expect(onRow).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: /ehkultura/ })).toBeInTheDocument()
  })

  it('leaves other values as text', () => {
    const { container } = render(
      <>
        <CellValue value="Quentin" />
        <CellValue value={0.123456} />
        <CellValue value={{ a: 1 }} />
        <CellValue value={null} />
        <CellValue value={[]} />
      </>,
    )
    expect(container.textContent).toBe('Quentin0.123{"a":1}none')
    expect(isCandidateList([])).toBe(false)
    expect(isCandidateList([{ a: 1 }])).toBe(false)
  })
  it("shows llm_based's choice as selected, not as a score", () => {
    const chosen = [
      {
        url: 'http://www.wikidata.org/entity/Q937',
        wikidata_item_id: 'Q937',
        title: 'Albert Einstein',
        score: 1,
        algorithm: 'llm_based',
      },
    ]
    render(<CellValue value={chosen} />)
    expect(screen.getByText('selected')).toHaveAttribute(
      'title',
      expect.stringContaining('no confidence'),
    )
    expect(screen.queryByText('1.00')).not.toBeInTheDocument()
    expect(screen.queryByText('llm_based')).not.toBeInTheDocument() // one algorithm: no label
    expect(cellTitle(chosen)).toBe('1. Q937 Albert Einstein (selected by llm_based)')
  })

  it('names the algorithm when one list mixes them', () => {
    const mixed = [
      { url: 'u', wikidata_item_id: 'Q1', title: 'A', score: 0.97, algorithm: 'rule_based' },
      { url: 'v', wikidata_item_id: 'Q2', title: 'B', score: 0.8, algorithm: 'wikifier' },
    ]
    render(<CellValue value={mixed} />)
    expect(screen.getByText('rule_based')).toBeInTheDocument()
    expect(screen.getByText('wikifier')).toBeInTheDocument()
    expect(cellTitle(mixed).split('\n')[0]).toBe('1. Q1 A (rule_based score 0.97)')
  })

  it('offers each QID once across the algorithms, with who proposed it', () => {
    const wikifier = [
      {
        url: 'http://en.wikipedia.org/wiki/A',
        wikidata_item_id: 'Q1',
        title: 'A',
        score: 0.98,
        algorithm: 'wikifier',
      },
      {
        url: 'http://en.wikipedia.org/wiki/N',
        wikidata_item_id: null,
        title: 'N',
        score: 0.5,
        algorithm: 'wikifier',
      },
    ]
    const llm = [
      {
        url: 'http://www.wikidata.org/entity/Q1',
        wikidata_item_id: 'Q1',
        title: 'A',
        score: 1,
        algorithm: 'llm_based',
      },
    ]
    const rules = [
      {
        url: 'http://vocab.getty.edu/ulan/500000000',
        wikidata_item_id: null,
        title: 'U',
        score: 0.95,
        algorithm: 'rule_based',
      },
      {
        url: 'http://www.wikidata.org/entity/Q2',
        wikidata_item_id: 'Q2',
        title: 'B',
        score: 0.95,
        algorithm: 'rule_based',
      },
    ]
    expect(decisionOptions([wikifier, llm, [], undefined, rules])).toEqual([
      { qid: 'Q1', title: 'A', notes: ['wikifier 0.98', 'llm_based selected'] },
      { qid: 'Q2', title: 'B', notes: ['rule_based 0.95'] },
    ])
    expect(decisionOptions([[{ wikidata_item_id: 'Q3', title: 'C', score: 0.5 }]])).toEqual([
      { qid: 'Q3', title: 'C', notes: ['0.50'] },
    ])
  })
})
