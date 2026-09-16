import { ArrowLeftIcon } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConvertedOutput, type OutputView } from '@/features/entries/EntryView'
import { useAnnotation, useConvertedEntryById, usePages } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

const DPI = 110
const PAD = 8 // points of context around the span

/** A structured PDF entry: the page crop of its span next to the DMLex the model produced. */
export function SpanView({
  projectId,
  runId,
  convertedId,
  documentId,
}: {
  projectId: string
  runId: string
  convertedId: string
  documentId: string | null
}) {
  const converted = useConvertedEntryById(projectId, runId, convertedId)
  const annotation = useAnnotation(projectId, converted.data?.annotation_id ?? null)
  const pages = usePages(projectId, documentId)
  const openRun = usePreview((s) => s.openRun)
  const openDocument = usePreview((s) => s.openDocument)
  const [view, setView] = useState<OutputView>('rendered')

  if (converted.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading…</p>
  if (converted.isError)
    return <p className="text-destructive p-3 text-sm">{converted.error.message}</p>
  const output = converted.data
  const span = annotation.data
  const page = span ? pages.data?.find((p) => p.page_no === span.page_no) : undefined
  const scale = DPI / 72
  const crop = span
    ? {
        left: Math.max(0, span.bbox[0] - PAD) * scale,
        top: Math.max(0, span.bbox[1] - PAD) * scale,
        width: (span.bbox[2] - span.bbox[0] + 2 * PAD) * scale,
        height: (span.bbox[3] - span.bbox[1] + 2 * PAD) * scale,
      }
    : null

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <Button variant="ghost" size="xs" onClick={() => openRun(runId)}>
          <ArrowLeftIcon /> Run
        </Button>
        <span className="font-medium">{output.headword ?? '(no headword)'}</span>
        {span && (
          <button
            type="button"
            className="text-muted-foreground underline-offset-2 hover:underline"
            onClick={() => documentId && openDocument(documentId, span.page_no)}
          >
            page {span.page_no}
          </button>
        )}
        {output.confidence !== null && output.confidence !== undefined && (
          <span
            className={
              output.confidence < 0.6
                ? 'text-amber-700 dark:text-amber-400'
                : 'text-muted-foreground'
            }
          >
            confidence {Math.round(output.confidence * 100)}%
          </span>
        )}
        <Tabs value={view} onValueChange={(v) => setView(v as OutputView)} className="ml-auto">
          <TabsList className="h-7">
            <TabsTrigger value="rendered" className="text-xs">
              Rendered
            </TabsTrigger>
            <TabsTrigger value="xml" className="text-xs">
              DMLex XML
            </TabsTrigger>
            <TabsTrigger value="json" className="text-xs">
              JSON
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="@container min-h-0 flex-1">
        <div className="grid h-full grid-cols-1 grid-rows-[2fr_3fr] @3xl:grid-cols-2 @3xl:grid-rows-1">
          <div className="flex min-h-0 flex-col border-b @3xl:border-r @3xl:border-b-0">
            <p className="text-muted-foreground bg-muted/40 border-b px-3 py-1 text-[11px] uppercase">
              Page {span?.page_no ?? '…'}
            </p>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              {span && crop && documentId && page ? (
                <div
                  className="overflow-hidden rounded border bg-white shadow-sm"
                  style={{ width: Math.min(crop.width, 1400), height: crop.height }}
                  data-testid="span-crop"
                >
                  <img
                    alt={`Page ${span.page_no} region`}
                    src={`/api/projects/${projectId}/documents/${documentId}/pages/${span.page_no}/image?dpi=${DPI}`}
                    style={{
                      maxWidth: 'none',
                      width: page.width * scale,
                      height: page.height * scale,
                      transform: `translate(${-crop.left}px, ${-crop.top}px)`,
                    }}
                  />
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">The span of this entry is gone.</p>
              )}
              {span?.payload.text && (
                <p className="text-muted-foreground mt-2 text-xs whitespace-pre-wrap">
                  {span.payload.text}
                </p>
              )}
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <p className="text-muted-foreground bg-muted/40 border-b px-3 py-1 text-[11px] uppercase">
              DMLex output
            </p>
            <div className="min-h-0 flex-1 overflow-auto">
              {output.notes.length > 0 && (
                <ul className="border-b px-3 py-2 text-xs">
                  {output.notes.map((note, i) => (
                    <li key={i} className="text-muted-foreground">
                      · {String(note)}
                    </li>
                  ))}
                </ul>
              )}
              {Object.keys(output.dmlex).length === 0 ? (
                <p className="text-muted-foreground p-3 text-sm">No entry was produced.</p>
              ) : (
                <ConvertedOutput output={output} view={view} />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
