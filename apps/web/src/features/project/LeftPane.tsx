import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { FileList } from '@/features/files/FileList'
import { UploadDropzone } from '@/features/files/UploadDropzone'
import { keys, useFiles, useMembers } from '@/lib/queries'
import type { Project } from '@/lib/types'

export function LeftPane({ project }: { project: Project }) {
  const files = useFiles(project.id)
  const members = useMembers(project.id)
  const queryClient = useQueryClient()
  const canEdit = project.my_role !== 'viewer'
  const refresh = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: keys.files(project.id) }),
    [queryClient, project.id],
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
            <ArrowLeftIcon /> Projects
          </Button>
          <div className="flex items-start justify-between gap-2">
            <h1 className="leading-tight font-semibold">{project.name}</h1>
            <Badge variant="outline">{project.source_type.toUpperCase()}</Badge>
          </div>
          {project.description && (
            <p className="text-muted-foreground mt-1 text-xs">{project.description}</p>
          )}
        </div>

        <section className="flex flex-col gap-2">
          <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            Files
          </h2>
          <UploadDropzone projectId={project.id} onUploaded={refresh} disabled={!canEdit} />
          {files.isPending && <p className="text-muted-foreground text-xs">Loading files…</p>}
          {files.data && <FileList projectId={project.id} files={files.data} canEdit={canEdit} />}
        </section>

        <section className="flex flex-col gap-1">
          <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            Members
          </h2>
          <ul className="text-xs">
            {members.data?.map((m) => (
              <li key={m.user_id} className="flex justify-between gap-2 py-0.5">
                <span className="truncate">{m.name || m.email}</span>
                <span className="text-muted-foreground">{m.role}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </ScrollArea>
  )
}
