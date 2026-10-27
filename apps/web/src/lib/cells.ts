import { cellText } from '@/lib/format'

export interface Candidate {
  wikidata_item_id?: string | null
  title?: string | null
  url?: string | null
  score?: number | null
}

export const SCORE_NOTE = 'ranks the candidates; it is not a certainty'

export function isCandidateList(value: unknown): value is Candidate[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (item) =>
        typeof item === 'object' &&
        item !== null &&
        ('wikidata_item_id' in item || ('title' in item && 'url' in item)),
    )
  )
}

export function wikidataUrl(qid: string): string {
  return `https://www.wikidata.org/wiki/${qid}`
}

export function formatScore(score: number): string {
  return score >= 0 && score <= 1 ? score.toFixed(2) : String(score)
}

/** A cell's full text for its tooltip. */
export function cellTitle(value: unknown): string {
  if (isCandidateList(value)) {
    return value
      .map(
        (c, i) =>
          `${i + 1}. ${c.wikidata_item_id ?? 'no QID'} ${c.title ?? ''}` +
          (typeof c.score === 'number' ? ` (score ${formatScore(c.score)})` : ''),
      )
      .join('\n')
  }
  const text = cellText(value)
  return text.length > 2000 ? `${text.slice(0, 2000)}…` : text
}
