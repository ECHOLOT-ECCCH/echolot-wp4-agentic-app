import { AlertTriangleIcon } from 'lucide-react'

import { PdfViewer } from '@/features/preview/PdfViewer'
import { fileDownloadUrl } from '@/lib/api'
import { usePages } from '@/lib/queries'
import type { DocumentDetail } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

/** A PDF document once its layout is extracted: page facts on top, the viewer below.
 *  Entry overlays and review come with M2.2/M2.3. */
export function PdfDocumentPanel({
  projectId,
  document,
}: {
  projectId: string
  document: DocumentDetail
}) {
  const pages = usePages(projectId, document.id)
  const target = usePreview((s) => s.target)
  const revision = usePreview((s) => s.revision)
  const scanned = document.stats.scanned_pages ?? 0
  const pageCount = document.stats.page_count ?? pages.data?.length ?? 0
  const focus = target.kind === 'file' && target.fileId === document.source_file_id ? target : null
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="truncate font-medium">{document.file_name}</span>
        <span className="text-muted-foreground">{pageCount.toLocaleString()} pages</span>
        {document.stats.lines !== undefined && (
          <span className="text-muted-foreground">
            {document.stats.lines.toLocaleString()} text lines
          </span>
        )}
        {scanned > 0 && (
          <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
            <AlertTriangleIcon className="size-3.5" />
            {scanned} page{scanned === 1 ? '' : 's'} without a text layer (scanned or empty)
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        <PdfViewer
          url={fileDownloadUrl(projectId, document.source_file_id, true)}
          page={focus?.page}
          bbox={focus?.bbox}
          revision={revision}
        />
      </div>
    </div>
  )
}
