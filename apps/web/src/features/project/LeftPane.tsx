import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon, ChevronDownIcon, ChevronUpIcon } from 'lucide-react'
import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { DocumentList } from '@/features/entries/DocumentList'
import { FileList } from '@/features/files/FileList'
import { UploadDropzone } from '@/features/files/UploadDropzone'
import { ConversionLinks } from '@/features/mapping/ConversionLinks'
import { RunList } from '@/features/runs/RunList'
import { StepList } from '@/features/steps/StepList'
import { keys, useDocuments, useFiles, useMembers, useRuns, useSteps } from '@/lib/queries'
import type { Project } from '@/lib/types'

const MORE_KEY = 'elx.leftpane.more.'

function storedMore(projectId: string): boolean | null {
  try {
    const value = localStorage.getItem(MORE_KEY + projectId)
    return value === null ? null : value === '1'
  } catch {
    return null
  }
}

/**
 * The project sidebar. An XML project shows the essentials (its document and the conversion
 * links) and keeps files, runs, steps and members behind "Show more"; a fresh project with
 * nothing uploaded yet opens with the upload area. The choice is remembered per project.
 */
export function LeftPane({ project }: { project: Project }) {
  const files = useFiles(project.id)
  const members = useMembers(project.id)
  const documents = useDocuments(project.id)
  const runs = useRuns(project.id)
  const steps = useSteps(project.id)
  const queryClient = useQueryClient()
  const canEdit = project.my_role !== 'viewer'
  const refresh = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: keys.files(project.id) }),
    [queryClient, project.id],
  )
  const collapsible = project.source_type === 'xml'
  // the persisted choice wins; then what happened in this visit (an upload keeps the files in
  // view); a fresh project with nothing uploaded yet opens with the upload area
  const [chosen, setChosen] = useState<boolean | null>(() => storedMore(project.id))
  const [uploaded, setUploaded] = useState(false)
  const noDocumentsYet = documents.data?.length === 0
  const showMore = !collapsible || (chosen ?? (uploaded || noDocumentsYet))
  const toggle = () => {
    const next = !showMore
    setChosen(next)
    try {
      localStorage.setItem(MORE_KEY + project.id, next ? '1' : '0')
    } catch {
      /* private mode: the choice lasts for the session */
    }
  }
  const onUploaded = () => {
    setUploaded(true)
    refresh()
  }
  const hidden = [
    files.data ? `${files.data.length} file${files.data.length === 1 ? '' : 's'}` : 'files',
    runs.data ? `${runs.data.length} run${runs.data.length === 1 ? '' : 's'}` : 'runs',
    steps.data ? `${steps.data.length} step${steps.data.length === 1 ? '' : 's'}` : 'steps',
    'members',
  ].join(' · ')

  const filesSection = (
    <section className="flex flex-col gap-2">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        Files
      </h2>
      <UploadDropzone projectId={project.id} onUploaded={onUploaded} disabled={!canEdit} />
      {files.isPending && <p className="text-muted-foreground text-xs">Loading files…</p>}
      {files.data && <FileList projectId={project.id} files={files.data} canEdit={canEdit} />}
    </section>
  )
  const membersSection = (
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

        {!collapsible && filesSection}

        <DocumentList projectId={project.id} />

        <ConversionLinks projectId={project.id} sourceType={project.source_type} />

        {collapsible && (
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="xs"
              className="-ml-1"
              onClick={toggle}
              aria-expanded={showMore}
              aria-controls="left-pane-more"
            >
              {showMore ? <ChevronUpIcon /> : <ChevronDownIcon />}
              {showMore ? 'Show less' : 'Show more'}
            </Button>
            {!showMore && <span className="text-muted-foreground truncate text-xs">{hidden}</span>}
          </div>
        )}

        {showMore && (
          <div id="left-pane-more" className="flex flex-col gap-4">
            {collapsible && filesSection}
            <StepList projectId={project.id} canEdit={canEdit} />
            <RunList projectId={project.id} />
            {membersSection}
          </div>
        )}
      </div>
    </ScrollArea>
  )
}
