import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangleIcon,
  MergeIcon,
  MessageSquareQuoteIcon,
  MousePointer2Icon,
  ScissorsLineDashedIcon,
  SquareDashedIcon,
  Trash2Icon,
} from 'lucide-react'
import { useCallback, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { type EditTool, OverlayLayer } from '@/features/pdf/OverlayLayer'
import { ReviewPanel } from '@/features/pdf/ReviewPanel'
import { selectionOnPage, textInRegion } from '@/features/pdf/selection'
import { PdfViewer } from '@/features/preview/PdfViewer'
import { ApiError, fileDownloadUrl } from '@/lib/api'
import { useChatContext } from '@/lib/chatContext'
import {
  pageLayoutOptions,
  useAnnotations,
  useEditAnnotations,
  usePages,
  useProject,
} from '@/lib/queries'
import type { AnnotationRecord, DocumentDetail } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

const LABELS = ['entry', 'heading', 'note', 'other']

/** A PDF document once its layout is extracted: page facts on top, the viewer with entry-span
 *  overlays, and the review list on the right. */
export function PdfDocumentPanel({
  projectId,
  document,
  page,
}: {
  projectId: string
  document: DocumentDetail
  page?: number
}) {
  const pages = usePages(projectId, document.id)
  const project = useProject(projectId)
  const target = usePreview((s) => s.target)
  const revision = usePreview((s) => s.revision)
  const canEdit = project.data?.my_role !== 'viewer'
  const scanned = document.stats.scanned_pages ?? 0
  const pageCount = document.stats.page_count ?? pages.data?.length ?? 0
  const focus = target.kind === 'file' && target.fileId === document.source_file_id ? target : null
  const segmentation = document.stats.segmentation

  const [selected, setSelected] = useState<AnnotationRecord | null>(null)
  const [currentPage, setCurrentPage] = useState(page ?? focus?.page ?? 1)
  const [showRejected, setShowRejected] = useState(false)
  const [editing, setEditing] = useState(false)
  const [tool, setTool] = useState<EditTool>('select')
  const [contextMode, setContextMode] = useState(false)
  const [textSelection, setTextSelection] = useState<{
    pageNumber: number
    box: [number, number, number, number]
    text: string
    at: { x: number; y: number }
  } | null>(null)
  const edits = useEditAnnotations(projectId, document.id)
  const queryClient = useQueryClient()
  const addChip = useChatContext((s) => s.add)
  const scaleRef = useRef(1)
  const panelRef = useRef<HTMLDivElement>(null)
  const fail = (error: unknown) =>
    toast.error(error instanceof ApiError ? error.detail : String(error))
  const onScaleChange = useCallback((scale: number) => {
    scaleRef.current = scale
  }, [])

  const sendRegion = async (pageNumber: number, box: [number, number, number, number]) => {
    try {
      const layout = await queryClient.fetchQuery(
        pageLayoutOptions(projectId, document.id, pageNumber),
      )
      const { text, words } = textInRegion(layout, box)
      if (!text) {
        toast.message('No text in that region')
        return
      }
      addChip({
        document_id: document.id,
        file_id: document.source_file_id,
        page: pageNumber,
        bbox: box,
        text,
        words,
      })
      toast.success(`Selection added to the chat (page ${pageNumber}, ${words} words)`)
    } catch (error) {
      fail(error)
    }
  }

  // text selected in the page's text layer offers an "Add to chat" button
  const onMouseUp = () => {
    const found = selectionOnPage(window.getSelection(), scaleRef.current)
    if (!found) {
      setTextSelection(null)
      return
    }
    const range = window.getSelection()?.getRangeAt(0)
    const rect = range?.getBoundingClientRect()
    const panel = panelRef.current?.getBoundingClientRect()
    if (!rect || !panel) return
    setTextSelection({
      ...found,
      at: { x: rect.left - panel.left + rect.width / 2, y: rect.bottom - panel.top + 6 },
    })
  }
  // a selection in the list scrolls the viewer; a newer ui-action (page + revision) wins again
  const externalKey = `${page ?? focus?.page ?? ''}:${revision}`
  const [focusRequest, setFocusRequest] = useState<{
    page: number
    bbox?: [number, number, number, number]
    revision: number
    externalKey: string
  } | null>(null)
  const active = focusRequest && focusRequest.externalKey === externalKey ? focusRequest : null

  // overlays for the pages around the reader (one query per page, cached)
  const last = Math.max(1, pageCount)
  const before = Math.max(1, currentPage - 1)
  const after = Math.min(last, currentPage + 1)
  const queryBefore = useAnnotations(projectId, document.id, { page_no: before, limit: 1000 })
  const queryCurrent = useAnnotations(projectId, document.id, {
    page_no: Math.min(last, currentPage),
    limit: 1000,
  })
  const queryAfter = useAnnotations(projectId, document.id, { page_no: after, limit: 1000 })
  const loaded: { page: number; items: AnnotationRecord[] }[] = [
    { page: before, items: queryBefore.data?.items ?? [] },
    { page: Math.min(last, currentPage), items: queryCurrent.data?.items ?? [] },
    { page: after, items: queryAfter.data?.items ?? [] },
  ]
  const annotationsFor = (pageNo: number): AnnotationRecord[] =>
    loaded.find((entry) => entry.page === pageNo)?.items ?? []
  // the current selection, refreshed from the loaded page data after edits
  const live = selected
    ? (annotationsFor(selected.page_no).find((a) => a.id === selected.id) ?? selected)
    : null
  const nextOnPage = live
    ? (annotationsFor(live.page_no)
        .filter((a) => a.status !== 'rejected')
        .sort((a, b) => a.position - b.position || a.bbox[1] - b.bbox[1])
        .find(
          (a) => a.position > live.position || (a.position === live.position && a.id > live.id),
        ) ?? null)
    : null

  const select = useCallback(
    (annotation: AnnotationRecord | null) => {
      setSelected(annotation)
      if (annotation) {
        setCurrentPage(annotation.page_no)
        setFocusRequest((prev) => ({
          page: annotation.page_no,
          bbox: annotation.bbox,
          revision: 1000 + (prev?.revision ?? 0) + 1,
          externalKey,
        }))
      }
    },
    [externalKey],
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="truncate font-medium">{document.file_name}</span>
        <span className="text-muted-foreground">{pageCount.toLocaleString()} pages</span>
        {segmentation && (
          <span className="text-muted-foreground">
            {segmentation.stats.spans.toLocaleString()} entry spans ({segmentation.stats.mode})
          </span>
        )}
        {scanned > 0 && (
          <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
            <AlertTriangleIcon className="size-3.5" />
            {scanned} page{scanned === 1 ? '' : 's'} without a text layer (scanned or empty)
          </span>
        )}
        <label className="text-muted-foreground ml-auto flex items-center gap-1">
          <input
            type="checkbox"
            checked={showRejected}
            onChange={(e) => setShowRejected(e.target.checked)}
          />
          show rejected
        </label>
        <Button
          variant={contextMode ? 'default' : 'outline'}
          size="xs"
          onClick={() => {
            setContextMode((v) => !v)
            setEditing(false)
          }}
          aria-pressed={contextMode}
          title="Drag a region on the page to send its text with your next message"
        >
          <MessageSquareQuoteIcon /> Select for chat
        </Button>
        {canEdit && (
          <Button
            variant={editing ? 'default' : 'outline'}
            size="xs"
            onClick={() => {
              setEditing((v) => !v)
              setContextMode(false)
              setTool('select')
            }}
            aria-pressed={editing}
          >
            {editing ? 'Done editing' : 'Edit boxes'}
          </Button>
        )}
      </div>
      {editing && (
        <div className="flex shrink-0 flex-wrap items-center gap-1 border-b px-3 py-1 text-xs">
          <Button
            variant={tool === 'select' ? 'secondary' : 'ghost'}
            size="xs"
            onClick={() => setTool('select')}
            title="Select, move and resize boxes"
          >
            <MousePointer2Icon /> Select
          </Button>
          <Button
            variant={tool === 'draw' ? 'secondary' : 'ghost'}
            size="xs"
            onClick={() => setTool('draw')}
            title="Drag on the page to draw a new entry box"
          >
            <SquareDashedIcon /> Draw
          </Button>
          <Button
            variant={tool === 'split' ? 'secondary' : 'ghost'}
            size="xs"
            onClick={() => setTool('split')}
            title="Click inside a box to split it at that line"
          >
            <ScissorsLineDashedIcon /> Split
          </Button>
          <span className="bg-border mx-1 h-4 w-px" />
          {live ? (
            <>
              <span className="text-muted-foreground">
                {live.payload.headword_guess ?? live.label} (p{live.page_no})
              </span>
              <select
                aria-label="Label"
                className="bg-background rounded border px-1 py-0.5"
                value={live.label}
                onChange={(e) =>
                  edits.update.mutate({ id: live.id, label: e.target.value }, { onError: fail })
                }
              >
                {[...new Set([...LABELS, live.label])].map((label) => (
                  <option key={label} value={label}>
                    {label}
                  </option>
                ))}
              </select>
              <Button
                variant="ghost"
                size="xs"
                disabled={!nextOnPage || edits.merge.isPending}
                title="Merge with the next span on this page"
                onClick={() =>
                  nextOnPage &&
                  edits.merge.mutate(
                    { id: live.id, withId: nextOnPage.id },
                    {
                      onSuccess: (merged) => setSelected(merged),
                      onError: fail,
                    },
                  )
                }
              >
                <MergeIcon /> Merge with next
              </Button>
              <Button
                variant="ghost"
                size="xs"
                className="text-destructive"
                disabled={edits.remove.isPending}
                onClick={() =>
                  edits.remove.mutate(live.id, {
                    onSuccess: () => setSelected(null),
                    onError: fail,
                  })
                }
              >
                <Trash2Icon /> Delete box
              </Button>
            </>
          ) : (
            <span className="text-muted-foreground">
              {tool === 'draw'
                ? 'Drag on the page to draw a box.'
                : tool === 'split'
                  ? 'Click inside a box to split it there.'
                  : 'Click a box to select it; drag it or its handles.'}
            </span>
          )}
        </div>
      )}
      <div
        ref={panelRef}
        className="relative flex min-h-0 flex-1"
        onMouseUp={onMouseUp}
        role="presentation"
      >
        {textSelection && (
          <button
            type="button"
            className="bg-primary text-primary-foreground absolute z-10 -translate-x-1/2 rounded-md px-2 py-1 text-xs shadow"
            style={{ left: textSelection.at.x, top: textSelection.at.y }}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              addChip({
                document_id: document.id,
                file_id: document.source_file_id,
                page: textSelection.pageNumber,
                bbox: textSelection.box,
                text: textSelection.text,
                words: 0,
              })
              toast.success(`Selection added to the chat (page ${textSelection.pageNumber})`)
              window.getSelection()?.removeAllRanges()
              setTextSelection(null)
            }}
          >
            Add to chat
          </button>
        )}
        <div className="min-w-0 flex-1">
          <PdfViewer
            url={fileDownloadUrl(projectId, document.source_file_id, true)}
            page={active?.page ?? page ?? focus?.page}
            bbox={active ? active.bbox : focus?.bbox}
            revision={active?.revision ?? revision}
            highlightBbox={!active}
            onCurrentPageChange={setCurrentPage}
            onScaleChange={onScaleChange}
            renderOverlay={({ pageNumber, scale, viewport, pageHeight }) => (
              <OverlayLayer
                annotations={annotationsFor(pageNumber)}
                pageHeight={pageHeight}
                scale={scale}
                viewport={viewport}
                selectedId={selected?.id ?? null}
                showRejected={showRejected}
                onSelect={(id) => {
                  const found = annotationsFor(pageNumber).find((a) => a.id === id) ?? null
                  setSelected(found)
                }}
                editable={(editing && canEdit) || contextMode}
                tool={contextMode ? 'context' : tool}
                edits={{
                  onRegion: (bbox) => void sendRegion(pageNumber, bbox),
                  onCreate: (bbox) =>
                    edits.create.mutate(
                      { page_no: pageNumber, bbox },
                      {
                        onSuccess: (created) => {
                          setSelected(created)
                          setTool('select')
                        },
                        onError: fail,
                      },
                    ),
                  onChangeBox: (id, bbox) => edits.update.mutate({ id, bbox }, { onError: fail }),
                  onSplit: (id, y) =>
                    edits.split.mutate(
                      { id, y },
                      {
                        onSuccess: (halves) => {
                          setSelected(halves[0] ?? null)
                          setTool('select')
                        },
                        onError: fail,
                      },
                    ),
                }}
              />
            )}
          />
        </div>
        <div className="w-72 shrink-0 border-l">
          <ReviewPanel
            projectId={projectId}
            documentId={document.id}
            canEdit={canEdit}
            selectedId={selected?.id ?? null}
            onSelect={select}
            currentPage={currentPage}
            hasSegmentation={Boolean(segmentation)}
          />
        </div>
      </div>
    </div>
  )
}
