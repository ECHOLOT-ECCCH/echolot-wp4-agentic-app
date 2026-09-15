import { useQuery } from '@tanstack/react-query'
import { DownloadIcon, FileSearchIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { PdfViewer } from '@/features/preview/PdfViewer'
import { TextPreview } from '@/features/preview/TextPreview'
import { ApiError, api, fileContentUrl, fileDownloadUrl } from '@/lib/api'
import { formatBytes, isPdf } from '@/lib/format'
import { useFiles } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

const PREVIEW_LIMIT = 5 * 1024 * 1024

export function PreviewHost({ projectId }: { projectId: string }) {
  const target = usePreview((s) => s.target)
  const revision = usePreview((s) => s.revision)
  const files = useFiles(projectId)

  if (target.kind === 'none') {
    return (
      <Empty
        title="Nothing selected"
        body="Pick a file on the left, or ask the agent to show you something. Entries, mappings and reports will open here."
      />
    )
  }
  if (target.kind !== 'file') {
    return (
      <Empty
        title="Coming in a later milestone"
        body={`Preview of ${target.kind} is not available yet.`}
      />
    )
  }
  const file = files.data?.find((f) => f.id === target.fileId)
  if (!file) {
    return files.isPending ? null : (
      <Empty title="File not found" body="It may have been deleted." />
    )
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="truncate font-medium">{file.name}</span>
        <span className="text-muted-foreground">{formatBytes(file.size)}</span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          nativeButton={false}
          render={
            <a href={fileDownloadUrl(projectId, file.id)} aria-label={`Download ${file.name}`} />
          }
        >
          <DownloadIcon />
          <span className="sr-only">Download</span>
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        {isPdf(file) ? (
          <PdfViewer
            url={fileDownloadUrl(projectId, file.id, true)}
            page={target.page}
            bbox={target.bbox}
            revision={revision}
          />
        ) : file.size > PREVIEW_LIMIT ? (
          <Empty
            title="Too large for a whole-file preview"
            body="Files above 5 MB are browsed entry by entry once the document has been analysed (milestone 1)."
          />
        ) : (
          <TextFile projectId={projectId} file={file} />
        )}
      </div>
    </div>
  )
}

function TextFile({
  projectId,
  file,
}: {
  projectId: string
  file: { id: string; name: string; mime: string }
}) {
  const content = useQuery({
    queryKey: ['file-content', projectId, file.id],
    queryFn: () => api<string>(fileContentUrl(projectId, file.id), { headers: { Accept: '*/*' } }),
    staleTime: 60_000,
  })
  if (content.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading…</p>
  if (content.isError) {
    const message = content.error instanceof ApiError ? content.error.detail : content.error.message
    return <Empty title="Cannot show this file" body={message} />
  }
  return <TextPreview file={file} content={content.data} />
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <FileSearchIcon className="text-muted-foreground size-8" />
      <p className="font-medium">{title}</p>
      <p className="text-muted-foreground max-w-sm text-sm">{body}</p>
    </div>
  )
}
