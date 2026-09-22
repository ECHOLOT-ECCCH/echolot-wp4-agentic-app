import { ArrowLeftIcon, MessageSquareQuoteIcon, PencilIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConvertedOutput, type OutputView } from '@/features/entries/EntryView'
import { CodeEditor } from '@/features/preview/CodeEditor'
import { ApiError } from '@/lib/api'
import { useChatContext } from '@/lib/chatContext'
import {
  useAnnotation,
  useConvertedEntryById,
  useOverrideEntry,
  usePages,
  useProject,
} from '@/lib/queries'
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
  const project = useProject(projectId)
  const override = useOverrideEntry(projectId, runId)
  const addEntry = useChatContext((s) => s.addEntry)
  const [view, setView] = useState<OutputView>('rendered')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const canEdit = project.data?.my_role !== 'viewer'

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
        {output.override && (
          <span className="rounded bg-teal-100 px-1 text-[10px] text-teal-900 dark:bg-teal-900/40 dark:text-teal-200">
            corrected
          </span>
        )}
        {span?.payload.structurer && (
          <span
            className="rounded bg-amber-100 px-1 text-[10px] text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
            title={span.payload.structurer.reason ?? undefined}
          >
            {span.payload.structurer.segmentation.replaceAll('_', ' ')}
          </span>
        )}
        <Button
          variant="outline"
          size="xs"
          title="Attach this entry (span text, output, issues) to your next message"
          onClick={() => {
            addEntry({
              document_id: documentId ?? undefined,
              headword: output.headword,
              origin: `structured PDF entry from run ${runId.slice(0, 8)}`,
              source_kind: 'pdf',
              page: span?.page_no,
              source_text: span?.payload.text ?? '',
              output_xml: output.xml,
              issues: output.issues.map((i) =>
                i.path && i.path !== 'entry' ? `${i.path}: ${i.message}` : i.message,
              ),
            })
            toast.success(`Entry ${output.headword ?? ''} added to the chat`.replace('  ', ' '))
          }}
        >
          <MessageSquareQuoteIcon /> Add to chat
        </Button>
        {canEdit && !editing && (
          <Button
            variant="outline"
            size="xs"
            onClick={() => {
              setDraft(JSON.stringify(output.override ?? output.dmlex, null, 2))
              setEditing(true)
            }}
          >
            <PencilIcon /> Correct
          </Button>
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
            <div className="flex min-h-0 flex-1 flex-col overflow-auto">
              {output.notes.length > 0 && (
                <ul className="shrink-0 border-b px-3 py-2 text-xs">
                  {output.notes.map((note, i) => (
                    <li key={i} className="text-muted-foreground">
                      · {String(note)}
                    </li>
                  ))}
                </ul>
              )}
              {editing ? (
                <CorrectionEditor
                  draft={draft}
                  onChange={setDraft}
                  hasOverride={Boolean(output.override)}
                  saving={override.isPending}
                  onCancel={() => setEditing(false)}
                  onRemove={() =>
                    override.mutate(
                      { id: output.id, override: null },
                      {
                        onSuccess: () => {
                          setEditing(false)
                          toast.success('Correction removed')
                        },
                        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message),
                      },
                    )
                  }
                  onSave={(value) =>
                    override.mutate(
                      { id: output.id, override: value },
                      {
                        onSuccess: (saved) => {
                          setEditing(false)
                          toast.success(
                            saved.issue_count
                              ? `Saved with ${saved.issue_count} issue(s)`
                              : 'Correction saved and valid',
                          )
                        },
                        onError: (e) => toast.error(e instanceof ApiError ? e.detail : e.message),
                      },
                    )
                  }
                />
              ) : Object.keys(output.override ?? output.dmlex).length === 0 ? (
                <p className="text-muted-foreground p-3 text-sm">No entry was produced.</p>
              ) : (
                <ConvertedOutput
                  output={output.override ? { ...output, dmlex: output.override } : output}
                  view={view}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function CorrectionEditor({
  draft,
  onChange,
  hasOverride,
  saving,
  onCancel,
  onRemove,
  onSave,
}: {
  draft: string
  onChange: (value: string) => void
  hasOverride: boolean
  saving: boolean
  onCancel: () => void
  onRemove: () => void
  onSave: (value: Record<string, unknown>) => void
}) {
  let parsed: Record<string, unknown> | null = null
  try {
    const value: unknown = JSON.parse(draft)
    parsed =
      typeof value === 'object' && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null
  } catch {
    parsed = null
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5 text-xs">
        <span className="text-muted-foreground">
          {parsed ? 'Edit the DMLex JSON of this entry' : 'Invalid JSON'}
        </span>
        <span className="ml-auto" />
        {hasOverride && (
          <Button variant="ghost" size="xs" onClick={onRemove} disabled={saving}>
            Remove correction
          </Button>
        )}
        <Button variant="ghost" size="xs" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="xs" disabled={!parsed || saving} onClick={() => parsed && onSave(parsed)}>
          Save correction
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <CodeEditor value={draft} onChange={onChange} language="json" ariaLabel="Entry JSON" />
      </div>
    </div>
  )
}
