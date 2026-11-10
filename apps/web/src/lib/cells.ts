import { cellText } from '@/lib/format'

export interface Candidate {
  wikidata_item_id?: string | null
  title?: string | null
  url?: string | null
  score?: number | null
  /** The ECHOLOT algorithm that proposed it, when the script tags its matches. */
  algorithm?: string | null
}

export const SCORE_NOTE =
  "ranks one algorithm's candidates; it is not a certainty, and not comparable between algorithms"
export const SELECTED_NOTE = 'llm_based selected this candidate; it gives no confidence'

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

/** The score as shown: "selected" for llm_based (its score is always 1 and says nothing), two
 * decimals otherwise, null when there is none. */
export function candidateScore(c: Candidate): string | null {
  if (c.algorithm === 'llm_based') return 'selected'
  return typeof c.score === 'number' ? formatScore(c.score) : null
}

export interface DecisionOption {
  qid: string
  title: string
  /** Who proposed it and how strongly: "0.90", or "wikifier 0.98", "llm_based selected" */
  notes: string[]
}

/** The candidates a reviewer can choose for a row: each QID once, across the row's candidate
 * lists (one per algorithm when a script compares them), in the order they first appear. */
export function decisionOptions(lists: unknown[]): DecisionOption[] {
  const byQid = new Map<string, DecisionOption>()
  for (const list of lists) {
    if (!isCandidateList(list)) continue
    for (const c of list) {
      if (!c.wikidata_item_id) continue
      let option = byQid.get(c.wikidata_item_id)
      if (!option) {
        option = { qid: c.wikidata_item_id, title: c.title ?? '', notes: [] }
        byQid.set(c.wikidata_item_id, option)
      }
      const score = candidateScore(c)
      const note = [c.algorithm, score].filter(Boolean).join(' ')
      if (note) option.notes.push(note)
    }
  }
  return [...byQid.values()]
}

/** A cell's full text for its tooltip. */
export function cellTitle(value: unknown): string {
  if (isCandidateList(value)) {
    return value
      .map((c, i) => `${i + 1}. ${c.wikidata_item_id ?? 'no QID'} ${c.title ?? ''}` + scoreNote(c))
      .join('\n')
  }
  const text = cellText(value)
  return text.length > 2000 ? `${text.slice(0, 2000)}…` : text
}

function scoreNote(c: Candidate): string {
  const score = candidateScore(c)
  if (score === 'selected') return ' (selected by llm_based)'
  const by = c.algorithm ? `${c.algorithm} ` : ''
  return score ? ` (${by}score ${score})` : c.algorithm ? ` (${c.algorithm})` : ''
}
