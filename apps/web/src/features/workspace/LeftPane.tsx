import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon, NotebookPenIcon } from 'lucide-react'
import { useCallback } from 'react'

import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { FileList } from '@/features/files/FileList'
import { UploadDropzone } from '@/features/files/UploadDropzone'
import { keys, useFiles } from '@/lib/queries'
import type { Workspace } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
      {children}
    </h2>
  )
}

/** The workspace sidebar: files with the upload area and the task notes. */
export function LeftPane({ workspace }: { workspace: Workspace }) {
  const files = useFiles(workspace.id)
  const queryClient = useQueryClient()
  const target = usePreview((s) => s.target)
  const open = usePreview((s) => s.open)
  const refresh = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: keys.files(workspace.id) }),
    [queryClient, workspace.id],
  )

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-4 p-3">
        <div>
          <Button
            variant="ghost"
            size="xs"
            nativeButton={false}
            render={<Link to="/" />}
            className="-ml-1 mb-1"
          >
            <ArrowLeftIcon /> Workspaces
          </Button>
          <h1 className="leading-tight font-semibold">{workspace.name}</h1>
          {workspace.description && (
            <p className="text-muted-foreground mt-1 text-xs">{workspace.description}</p>
          )}
        </div>

        <section className="flex flex-col gap-2">
          <SectionTitle>Files</SectionTitle>
          <UploadDropzone workspaceId={workspace.id} onUploaded={refresh} />
          {files.isPending && <p className="text-muted-foreground text-xs">Loading files…</p>}
          {files.data && <FileList workspaceId={workspace.id} files={files.data} />}
        </section>

        <section className="flex flex-col gap-1">
          <SectionTitle>Task</SectionTitle>
          <button
            type="button"
            onClick={() => open({ kind: 'task' })}
            className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
              target.kind === 'task' ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
            }`}
          >
            <NotebookPenIcon className="text-muted-foreground size-4" />
            Task notes
          </button>
        </section>
      </div>
    </ScrollArea>
  )
}
