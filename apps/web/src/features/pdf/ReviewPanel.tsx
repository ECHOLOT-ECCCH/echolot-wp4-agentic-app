import {
  AlertTriangleIcon,
  CheckCheckIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ScissorsIcon,
  XIcon,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import {
  useAcceptRest,
  useAnnotations,
  useBulkDecide,
  useDecideAnnotation,
  useReview,
  useSegmentDocument,
} from '@/lib/queries'
import type { AnnotationRecord, AnnotationStatus, ReviewPageFlag, ReviewSummary } from '@/lib/types'

export type ReviewFilter = 'proposed' | 'flagged' | 'accepted' | 'rejected' | 'all'

const FILTERS: { value: ReviewFilter; label: string }[] = [
  { value: 'proposed', label: 'To review' },
  { value: 'flagged', label: 'Flagged' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'all', label: 'All' },
]
const PAGE = 200

const humanize = (reason: string) => reason.replaceAll('_', ' ')

/** Why a page is in the sample, in words: "few entries (3, median 50)". */
function pageReasonText(flag: ReviewPageFlag, summary: ReviewSummary): string {
  const median = summary.median_entries_per_page.toLocaleString()
  return flag.reasons
    .map((reason) => {
      switch (reason) {
        case 'few_entries':
          return `few entries (${flag.entries}, median ${median})`
        case 'many_entries':
          return `many entries (${flag.entries}, median ${median})`
        case 'no_entries':
          return `no entries on ${flag.lines} lines`
        case 'flagged_spans':
          return 'flagged spans'
        default:
          return humanize(reason)
      }
    })
    .join(' · ')
}

/** The tooltip of a flagged row: the reasons, and the structurer's own words when it doubted
 *  the span. */
function flagTitle(annotation: AnnotationRecord): string | undefined {
  const flags = annotation.payload.flags ?? []
  if (flags.length === 0) return undefined
  const reason = annotation.payload.structurer?.reason
  return `Flagged: ${flags.map(humanize).join(', ')}${reason ? ` — ${reason}` : ''}`
}

export interface ReviewPanelProps {
  projectId: string
  documentId: string
  canEdit: boolean
  selectedId: string | null
  onSelect: (annotation: AnnotationRecord | null) => void
  onGoToPage: (page: number) => void
  currentPage: number
  hasSegmentation: boolean
}

/**
 * The list of entry spans with the review keyboard: j/k move, a accepts, r rejects, u undoes
 * (back to proposed). The selected span is highlighted on the page and scrolled into view.
 * Above the list, the review sample (first and last pages, one page per letter, every page
 * with an oddity) can be walked page by page, and the proposals nobody looked at can be
 * accepted in one go, flagged ones excluded.
 */
export function ReviewPanel({
  projectId,
  documentId,
  canEdit,
  selectedId,
  onSelect,
  onGoToPage,
  currentPage,
  hasSegmentation,
}: ReviewPanelProps) {
  const [filter, setFilter] = useState<ReviewFilter>('proposed')
  const [offset, setOffset] = useState(0)
  const [skipFlagged, setSkipFlagged] = useState(true)
  const [confirmAccept, setConfirmAccept] = useState(false)
  const status =
    filter === 'all' || filter === 'flagged'
      ? undefined
      : filter === 'accepted'
        ? 'accepted,edited'
        : filter
  const list = useAnnotations(projectId, documentId, {
    status,
    flagged: filter === 'flagged' ? true : undefined,
    offset,
    limit: PAGE,
  })
  const review = useReview(projectId, documentId, hasSegmentation)
  const decide = useDecideAnnotation(projectId, documentId)
  const bulk = useBulkDecide(projectId, documentId)
  const acceptRest = useAcceptRest(projectId, documentId)
  const segment = useSegmentDocument(projectId, documentId)
  const rootRef = useRef<HTMLDivElement>(null)
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const total = list.data?.total ?? 0

  const summary = review.data?.summary ?? null
  const samplePages = useMemo(() => summary?.sample_pages ?? [], [summary])
  const sampleIndex = samplePages.indexOf(currentPage)
  const pageFlag = summary?.flagged_pages.find((flag) => flag.page === currentPage)
  const remaining = review.data?.remaining_proposed ?? 0
  const remainingFlagged = review.data?.remaining_flagged_proposed ?? 0
  const toAccept = skipFlagged ? remaining - remainingFlagged : remaining

  const selectedIndex = items.findIndex((a) => a.id === selectedId)

  const move = (delta: number) => {
    if (items.length === 0) return
    const next = Math.min(
      items.length - 1,
      Math.max(0, (selectedIndex < 0 ? -1 : selectedIndex) + delta),
    )
    onSelect(items[next] ?? null)
  }
  const setStatus = (annotation: AnnotationRecord | undefined, next: AnnotationStatus) => {
    if (!annotation || !canEdit) return
    decide.mutate(
      { id: annotation.id, status: next },
      { onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message) },
    )
    // in the "to review" list the item disappears: keep the cursor on the following span
    if (filter === 'proposed') {
      const following = items[selectedIndex + 1] ?? items[selectedIndex - 1] ?? null
      onSelect(following)
    }
  }
  // the next or previous sample page; from a page outside the sample, the nearest one ahead
  const goSample = (delta: number) => {
    if (samplePages.length === 0) return
    let index: number
    if (sampleIndex >= 0) index = sampleIndex + delta
    else {
      const ahead = samplePages.findIndex((p) => p > currentPage)
      index = ahead < 0 ? samplePages.length - 1 : delta > 0 ? ahead : ahead - 1
    }
    const target = samplePages[Math.min(samplePages.length - 1, Math.max(0, index))]
    if (target !== undefined) onGoToPage(target)
  }

  // review keys work anywhere in the workspace except while typing (chat composer, inputs)
  const handlers = useRef({ move, setStatus, items, selectedIndex })
  useEffect(() => {
    handlers.current = { move, setStatus, items, selectedIndex }
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const tag = target?.tagName
      if (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        target?.isContentEditable ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      )
        return
      const {
        move: go,
        setStatus: decideCurrent,
        items: rows,
        selectedIndex: index,
      } = handlers.current
      const current = rows[index]
      switch (event.key) {
        case 'j':
          event.preventDefault()
          go(1)
          break
        case 'k':
          event.preventDefault()
          go(-1)
          break
        case 'a':
          event.preventDefault()
          decideCurrent(current, 'accepted')
          break
        case 'r':
          event.preventDefault()
          decideCurrent(current, 'rejected')
          break
        case 'u':
          event.preventDefault()
          decideCurrent(current, 'proposed')
          break
        default:
          break
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // keep the selected row visible
  useEffect(() => {
    if (!selectedId) return
    const row = rootRef.current?.querySelector<HTMLElement>(`[data-annotation-row="${selectedId}"]`)
    row?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  const acceptPage = () =>
    bulk.mutate(
      { status: 'accepted', page_no: currentPage, only_status: 'proposed' },
      {
        onSuccess: (r) => toast.success(`${r.updated} span(s) on page ${currentPage} accepted`),
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message),
      },
    )
  const acceptRemaining = () =>
    acceptRest.mutate(
      { skip_flagged: skipFlagged },
      {
        onSuccess: (r) => {
          setConfirmAccept(false)
          toast.success(`${r.updated} proposals accepted`)
        },
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message),
      },
    )
  const runSegmentation = () =>
    segment.mutate(
      {},
      {
        onSuccess: (r) =>
          toast.success(
            r.queued ? 'Segmentation queued' : `${r.stats?.spans ?? 0} entry spans proposed`,
          ),
        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message),
      },
    )

  return (
    <div ref={rootRef} className="flex h-full flex-col" aria-label="Entry review">
      <div className="flex shrink-0 items-center gap-1 border-b px-2 py-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => {
              setFilter(f.value)
              setOffset(0)
            }}
            className={`rounded px-1.5 py-0.5 text-xs ${
              filter === f.value
                ? 'bg-accent text-accent-foreground'
                : 'text-muted-foreground hover:bg-accent/60'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {summary && (
        <div
          className="flex shrink-0 flex-wrap items-center gap-1 border-b px-2 py-1 text-xs"
          data-testid="review-sample"
        >
          <span className="font-medium">Sample</span>
          <span className="text-muted-foreground">
            {sampleIndex >= 0
              ? `page ${currentPage} (${sampleIndex + 1} of ${samplePages.length})`
              : `${samplePages.length} of ${summary.text_pages.toLocaleString()} pages`}
          </span>
          <Button
            variant="ghost"
            size="xs"
            aria-label="Previous sample page"
            title="Previous sample page"
            disabled={samplePages.length === 0}
            onClick={() => goSample(-1)}
          >
            <ChevronLeftIcon />
          </Button>
          <Button
            variant="ghost"
            size="xs"
            aria-label="Next sample page"
            title="Next sample page"
            disabled={samplePages.length === 0}
            onClick={() => goSample(1)}
          >
            <ChevronRightIcon />
          </Button>
          <span
            className="text-muted-foreground ml-auto"
            title={
              Object.entries(summary.reasons)
                .map(([reason, count]) => `${humanize(reason)}: ${count}`)
                .join('\n') || 'No oddities found'
            }
          >
            {(review.data?.flagged_total ?? summary.flagged_spans).toLocaleString()} flagged
          </span>
          {pageFlag && (
            <span className="flex basis-full items-center gap-1 text-amber-700 dark:text-amber-400">
              <AlertTriangleIcon className="size-3.5 shrink-0" />
              <span className="truncate">{pageReasonText(pageFlag, summary)}</span>
            </span>
          )}
        </div>
      )}
      <div className="flex shrink-0 items-center gap-1 border-b px-2 py-1.5 text-xs">
        <span className="text-muted-foreground">{total.toLocaleString()} spans</span>
        <span className="ml-auto" />
        {canEdit && hasSegmentation && (
          <Button variant="outline" size="xs" onClick={acceptPage} disabled={bulk.isPending}>
            <CheckIcon /> Accept page {currentPage}
          </Button>
        )}
        {canEdit && (
          <Button
            variant={hasSegmentation ? 'ghost' : 'default'}
            size="xs"
            onClick={runSegmentation}
            disabled={segment.isPending}
            title="Propose entry boxes with the rule segmenter (default parameters)"
          >
            <ScissorsIcon /> {hasSegmentation ? 'Re-segment' : 'Segment'}
          </Button>
        )}
      </div>
      {canEdit && hasSegmentation && remaining > 0 && (
        <div
          className="flex shrink-0 flex-wrap items-center gap-1 border-b px-2 py-1 text-xs"
          data-testid="accept-rest"
        >
          {confirmAccept ? (
            <>
              <span>
                Accept {toAccept.toLocaleString()} proposal{toAccept === 1 ? '' : 's'}
                {skipFlagged && remainingFlagged > 0
                  ? ` (${remainingFlagged.toLocaleString()} flagged stay)`
                  : ''}
                ?
              </span>
              <Button size="xs" onClick={acceptRemaining} disabled={acceptRest.isPending}>
                Yes, accept
              </Button>
              <Button variant="ghost" size="xs" onClick={() => setConfirmAccept(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                size="xs"
                disabled={toAccept <= 0}
                onClick={() => setConfirmAccept(true)}
                title="Accept every proposal nobody has decided on yet"
              >
                <CheckCheckIcon /> Accept the remaining {toAccept.toLocaleString()}
              </Button>
              <label className="text-muted-foreground flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={skipFlagged}
                  onChange={(e) => setSkipFlagged(e.target.checked)}
                />
                except flagged
              </label>
            </>
          )}
        </div>
      )}
      <ul className="min-h-0 flex-1 overflow-auto">
        {items.map((annotation) => {
          const selected = annotation.id === selectedId
          const headword = annotation.payload.headword_guess
          const flags = annotation.payload.flags ?? []
          return (
            <li
              key={annotation.id}
              data-annotation-row={annotation.id}
              data-flags={flags.length > 0 ? flags.join(' ') : undefined}
              className={`flex items-center gap-1 border-b pr-1 text-sm ${
                selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(annotation)}
                className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1 text-left"
                title={flagTitle(annotation)}
              >
                <span className="text-muted-foreground w-8 shrink-0 text-xs">
                  p{annotation.page_no}
                </span>
                <span
                  className={`truncate ${annotation.payload.continuation ? 'italic' : 'font-medium'}`}
                >
                  {headword ??
                    (annotation.payload.continuation ? '(continuation)' : annotation.label)}
                </span>
                {flags.length > 0 && (
                  <AlertTriangleIcon
                    aria-hidden
                    className="size-3.5 shrink-0 text-amber-700 dark:text-amber-400"
                  />
                )}
                <StatusBadge status={annotation.status} source={annotation.source} />
              </button>
              {canEdit && (
                <>
                  <button
                    type="button"
                    aria-label="Accept"
                    className="rounded p-0.5 text-green-700 hover:bg-green-100 dark:hover:bg-green-900/40"
                    onClick={() => {
                      onSelect(annotation)
                      setStatus(annotation, 'accepted')
                    }}
                  >
                    <CheckIcon className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label="Reject"
                    className="rounded p-0.5 text-red-700 hover:bg-red-100 dark:hover:bg-red-900/40"
                    onClick={() => {
                      onSelect(annotation)
                      setStatus(annotation, 'rejected')
                    }}
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </>
              )}
            </li>
          )
        })}
        {list.data && items.length === 0 && (
          <li className="text-muted-foreground p-3 text-sm">
            {!hasSegmentation
              ? 'No entry spans yet. Ask the agent to segment the pages, or use Segment.'
              : filter === 'flagged'
                ? 'No flagged spans: nothing looked odd against the rest of the document.'
                : 'Nothing here.'}
          </li>
        )}
      </ul>
      <div className="flex shrink-0 items-center gap-2 border-t px-2 py-1 text-xs">
        <span className="text-muted-foreground">j/k move · a accept · r reject · u undo</span>
        <span className="ml-auto" />
        {total > PAGE && (
          <>
            <Button
              variant="ghost"
              size="xs"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="xs"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

function StatusBadge({ status, source }: { status: AnnotationStatus; source: string }) {
  const tone =
    source === 'user'
      ? 'bg-teal-100 text-teal-900 dark:bg-teal-900/40 dark:text-teal-200'
      : status === 'accepted'
        ? 'bg-green-100 text-green-900 dark:bg-green-900/40 dark:text-green-200'
        : status === 'rejected'
          ? 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-200'
          : status === 'edited'
            ? 'bg-violet-100 text-violet-900 dark:bg-violet-900/40 dark:text-violet-200'
            : 'bg-blue-100 text-blue-900 dark:bg-blue-900/40 dark:text-blue-200'
  return (
    <span className={`ml-auto shrink-0 rounded px-1 text-[10px] ${tone}`} data-status={status}>
      {source === 'user' ? 'yours' : status}
    </span>
  )
}
