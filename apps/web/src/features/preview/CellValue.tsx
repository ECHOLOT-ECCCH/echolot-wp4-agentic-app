import {
  type Candidate,
  candidateScore,
  isCandidateList,
  SCORE_NOTE,
  SELECTED_NOTE,
  wikidataUrl,
} from '@/lib/cells'
import { cellText } from '@/lib/format'

const URL_RE = /^https?:\/\/\S+$/
const QID_RE = /^Q[1-9]\d*$/
const MAX_CANDIDATES = 3

function External({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-sky-700 underline decoration-sky-700/30 underline-offset-2 hover:decoration-sky-700 dark:text-sky-300 dark:decoration-sky-300/30"
      onClick={(e) => e.stopPropagation()} // a link in a row must not select the row
    >
      {children}
    </a>
  )
}

/** Reconciliation candidates in one line: QID (Wikidata), title (Wikipedia, Wikidata or ULAN),
 * score; the algorithm too when one list holds several algorithms' candidates. */
function CandidateList({ items }: { items: Candidate[] }) {
  const shown = items.slice(0, MAX_CANDIDATES)
  const rest = items.length - shown.length
  const mixed = new Set(items.map((c) => c.algorithm ?? '')).size > 1
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="candidates">
      {shown.map((c, i) => (
        <span key={i} className="bg-muted inline-flex items-center gap-1 rounded px-1.5 py-0.5">
          <span className="text-muted-foreground">{i + 1}.</span>
          {c.wikidata_item_id ? (
            <External href={wikidataUrl(c.wikidata_item_id)}>{c.wikidata_item_id}</External>
          ) : (
            <span className="text-muted-foreground">no QID</span>
          )}
          {c.title &&
            (c.url ? <External href={c.url}>{c.title}</External> : <span>{c.title}</span>)}
          {mixed && c.algorithm && <span className="text-muted-foreground">{c.algorithm}</span>}
          <Score candidate={c} />
        </span>
      ))}
      {rest > 0 && <span className="text-muted-foreground">+{rest} more</span>}
    </span>
  )
}

function Score({ candidate }: { candidate: Candidate }) {
  const score = candidateScore(candidate)
  if (score === null) return null
  const note = score === 'selected' ? SELECTED_NOTE : `score: ${SCORE_NOTE}`
  return (
    <span className="text-muted-foreground tabular-nums" title={note}>
      {score}
    </span>
  )
}

/** One table cell: links for URLs and QIDs, candidate lists, compact JSON for the rest. */
export function CellValue({ value }: { value: unknown }) {
  if (isCandidateList(value)) return <CandidateList items={value} />
  if (Array.isArray(value) && value.length === 0) {
    return <span className="text-muted-foreground">none</span>
  }
  if (typeof value === 'string') {
    if (QID_RE.test(value)) return <External href={wikidataUrl(value)}>{value}</External>
    if (URL_RE.test(value)) return <External href={value}>{value}</External>
  }
  if (typeof value === 'number' && !Number.isInteger(value)) {
    return <span className="tabular-nums">{value.toFixed(3).replace(/0+$/, '')}</span>
  }
  return <>{cellText(value)}</>
}
