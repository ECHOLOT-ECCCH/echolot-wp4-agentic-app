import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CellValue } from '@/features/preview/CellValue'
import { cellTitle, isCandidateList } from '@/lib/cells'

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
})
