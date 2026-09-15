import { FileCodeIcon, FileIcon, FileTextIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { formatBytes, isPdf } from '@/lib/format'
import { useDeleteFile } from '@/lib/queries'
import type { FileKind, FileRecord } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

const KIND_LABELS: Record<FileKind, string> = {
  source: 'Source files',
  derived: 'Derived files',
  output: 'Outputs',
  artifact: 'Artifacts',
}

export function FileList({
  projectId,
  files,
  canEdit,
}: {
  projectId: string
  files: FileRecord[]
  canEdit: boolean
}) {
  const target = usePreview((s) => s.target)
  const openFile = usePreview((s) => s.openFile)
  const remove = useDeleteFile(projectId)
  const selectedId = target.kind === 'file' ? target.fileId : null

  const groups = (Object.keys(KIND_LABELS) as FileKind[])
    .map((kind) => ({ kind, files: files.filter((f) => f.kind === kind) }))
    .filter((g) => g.files.length > 0)

  if (groups.length === 0) {
    return <p className="text-muted-foreground px-1 text-xs">No files yet.</p>
  }

  return (
    <div className="flex flex-col gap-3">
      {groups.map((group) => (
        <div key={group.kind}>
          <h3 className="text-muted-foreground mb-1 px-1 text-[11px] font-medium tracking-wide uppercase">
            {KIND_LABELS[group.kind]}
          </h3>
          <ul className="flex flex-col">
            {group.files.map((file) => {
              const Icon = isPdf(file)
                ? FileTextIcon
                : file.mime.includes('xml')
                  ? FileCodeIcon
                  : FileIcon
              const selected = file.id === selectedId
              return (
                <li key={file.id} className="group flex items-center">
                  <button
                    type="button"
                    onClick={() => openFile(file.id)}
                    className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                      selected ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
                    }`}
                    title={`${file.name} (${formatBytes(file.size)})`}
                  >
                    <Icon className="text-muted-foreground size-4 shrink-0" />
                    <span className="truncate">{file.name}</span>
                    <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                      {formatBytes(file.size)}
                    </span>
                  </button>
                  {canEdit && (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                      aria-label={`Delete ${file.name}`}
                      onClick={() => {
                        if (confirm(`Delete ${file.name}?`)) {
                          remove.mutate(file.id, {
                            onError: (error) => toast.error(error.message),
                          })
                        }
                      }}
                    >
                      <Trash2Icon />
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </div>
  )
}
