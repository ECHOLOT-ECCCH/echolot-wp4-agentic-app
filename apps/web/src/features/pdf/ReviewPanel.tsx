import { CheckIcon, ScissorsIcon, XIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import {
  useAnnotations,
  useBulkDecide,
  useDecideAnnotation,
  useSegmentDocument,
} from '@/lib/queries'
import type { AnnotationRecord, AnnotationStatus } from '@/lib/types'

export type ReviewFilter = 'proposed' | 'accepted' | 'rejected' | 'all'

const FILTERS: { value: ReviewFilter; label: string }[] = [
  { value: 'proposed', label: 'To review' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'all', label: 'All' },
]
const PAGE = 200

export interface ReviewPanelProps {
  projectId: string
  documentId: string
  canEdit: boolean
  selectedId: string | null
  onSelect: (annotation: AnnotationRecord | null) => void
  currentPage: number
  hasSegmentation: boolean
}

/**
 * The list of entry spans with the review keyboard: j/k move, a accepts, r rejects, u undoes
 * (back to proposed). The selected span is highlighted on the page and scrolled into view.
 */
export function ReviewPanel({
  projectId,
  documentId,
  canEdit,
  selectedId,
  onSelect,
  currentPage,
  hasSegmentation,
}: ReviewPanelProps) {
  const [filter, setFilter] = useState<ReviewFilter>('proposed')
  const [offset, setOffset] = useState(0)
  const status = filter === 'all' ? undefined : filter === 'accepted' ? 'accepted,edited' : filter
  const list = useAnnotations(projectId, documentId, { status, offset, limit: PAGE })
  const decide = useDecideAnnotation(projectId, documentId)
  const bulk = useBulkDecide(projectId, documentId)
  const segment = useSegmentDocument(projectId, documentId)
  const rootRef = useRef<HTMLDivElement>(null)
  const items = useMemo(() => list.data?.items ?? [], [list.data])
  const total = list.data?.total ?? 0

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
      <ul className="min-h-0 flex-1 overflow-auto">
        {items.map((annotation) => {
          const selected = annotation.id === selectedId
          const headword = annotation.payload.headword_guess
          return (
            <li
              key={annotation.id}
              data-annotation-row={annotation.id}
              className={`flex items-center gap-1 border-b pr-1 text-sm ${
                selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(annotation)}
                className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1 text-left"
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
            {hasSegmentation
              ? 'Nothing here.'
              : 'No entry spans yet. Ask the agent to segment the pages, or use Segment.'}
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
