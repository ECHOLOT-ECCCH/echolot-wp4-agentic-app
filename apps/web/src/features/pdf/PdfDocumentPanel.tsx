import { AlertTriangleIcon } from 'lucide-react'
import { useCallback, useState } from 'react'

import { OverlayLayer } from '@/features/pdf/OverlayLayer'
import { ReviewPanel } from '@/features/pdf/ReviewPanel'
import { PdfViewer } from '@/features/preview/PdfViewer'
import { fileDownloadUrl } from '@/lib/api'
import { useAnnotations, usePages, useProject } from '@/lib/queries'
import type { AnnotationRecord, DocumentDetail } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

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
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <PdfViewer
            url={fileDownloadUrl(projectId, document.source_file_id, true)}
            page={active?.page ?? page ?? focus?.page}
            bbox={active ? active.bbox : focus?.bbox}
            revision={active?.revision ?? revision}
            highlightBbox={!active}
            onCurrentPageChange={setCurrentPage}
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
