import { TextPreview } from '@/features/preview/TextPreview'
import { useEntry } from '@/lib/queries'

export function EntryView({ projectId, entryId }: { projectId: string; entryId: string }) {
  const entry = useEntry(projectId, entryId)
  if (entry.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading entry…</p>
  if (entry.isError) return <p className="text-destructive p-3 text-sm">{entry.error.message}</p>
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">{entry.data.headword ?? '(no headword)'}</span>
        <span className="text-muted-foreground">entry {entry.data.ordinal + 1}</span>
        <span className="text-muted-foreground ml-auto">source XML</span>
      </div>
      <div className="min-h-0 flex-1">
        <TextPreview
          file={{ name: 'entry.xml', mime: 'application/xml' }}
          content={entry.data.source_xml}
        />
      </div>
    </div>
  )
}
