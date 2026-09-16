import { AlertTriangleIcon, Loader2Icon } from 'lucide-react'

import { EntryNavigator } from '@/features/entries/EntryNavigator'
import { EntryView } from '@/features/entries/EntryView'
import { useDocument } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

/** Entry browser for a parsed document: navigator on the left, the selected entry on the right. */
export function DocumentPanel({
  projectId,
  documentId,
  entryId,
  runId,
}: {
  projectId: string
  documentId: string
  entryId: string | null
  runId: string | null
}) {
  const document = useDocument(projectId, documentId)
  const openEntry = usePreview((s) => s.openEntry)

  if (document.isPending)
    return <p className="text-muted-foreground p-3 text-sm">Loading document…</p>
  if (document.isError)
    return <p className="text-destructive p-3 text-sm">{document.error.message}</p>
  const doc = document.data

  if (doc.status === 'queued' || doc.status === 'parsing') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <Loader2Icon className="text-muted-foreground size-6 animate-spin" />
        <p className="font-medium">Analysing {doc.file_name}</p>
        <p className="text-muted-foreground text-sm">
          Finding entries and building the index. Large files take a minute.
        </p>
      </div>
    )
  }
  if (doc.status === 'error') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <AlertTriangleIcon className="text-destructive size-6" />
        <p className="font-medium">Could not analyse {doc.file_name}</p>
        <p className="text-muted-foreground max-w-md text-sm">{doc.error}</p>
      </div>
    )
  }
  return (
    <div className="flex h-full">
      <div className="w-72 shrink-0">
        <EntryNavigator
          projectId={projectId}
          documentId={documentId}
          selectedEntryId={entryId}
          onSelect={(entry) => openEntry(entry.id, documentId, runId ?? undefined)}
        />
      </div>
      <div className="min-w-0 flex-1">
        {entryId ? (
          <EntryView projectId={projectId} entryId={entryId} runId={runId} />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 p-8 text-center">
            <p className="font-medium">{doc.entry_count.toLocaleString()} entries</p>
            <p className="text-muted-foreground text-sm">
              Entry element {doc.entry_xpath}. Pick an entry on the left or search for a headword.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
