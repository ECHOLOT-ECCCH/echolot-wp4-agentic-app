import { AlertTriangleIcon, BookOpenIcon, Loader2Icon } from 'lucide-react'

import { useDocuments } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

export function DocumentList({ projectId }: { projectId: string }) {
  const documents = useDocuments(projectId)
  const target = usePreview((s) => s.target)
  const openDocument = usePreview((s) => s.openDocument)
  const activeId =
    target.kind === 'document'
      ? target.documentId
      : target.kind === 'entry'
        ? target.documentId
        : null

  if (!documents.data || documents.data.length === 0) return null
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        Documents
      </h2>
      <ul className="flex flex-col">
        {documents.data.map((doc) => {
          const busy = doc.status === 'queued' || doc.status === 'parsing'
          const Icon =
            doc.status === 'error' ? AlertTriangleIcon : busy ? Loader2Icon : BookOpenIcon
          return (
            <li key={doc.id}>
              <button
                type="button"
                onClick={() => openDocument(doc.id)}
                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                  doc.id === activeId ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
                }`}
              >
                <Icon
                  className={`size-4 shrink-0 ${busy ? 'animate-spin' : ''} ${
                    doc.status === 'error' ? 'text-destructive' : 'text-muted-foreground'
                  }`}
                />
                <span className="truncate">{doc.file_name}</span>
                <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                  {doc.status === 'ready'
                    ? doc.type === 'pdf'
                      ? `${(doc.page_count ?? 0).toLocaleString()} pages${
                          doc.entry_count ? ` · ${doc.entry_count.toLocaleString()} entries` : ''
                        }`
                      : `${doc.entry_count.toLocaleString()} entries`
                    : doc.status === 'error'
                      ? 'failed'
                      : 'analysing…'}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
