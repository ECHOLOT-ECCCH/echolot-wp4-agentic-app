import { useQuery } from '@tanstack/react-query'
import { DownloadIcon, FileSearchIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Pager, RecordsTable } from '@/features/preview/RecordsTable'
import { TextPreview } from '@/features/preview/TextPreview'
import { RunView } from '@/features/runs/RunView'
import { ScriptView } from '@/features/scripts/ScriptView'
import { TaskEditor } from '@/features/task/TaskEditor'
import { ApiError, api, fileContentUrl, fileDownloadUrl } from '@/lib/api'
import { formatBytes, formatCount } from '@/lib/format'
import { PAGE_SIZE, pageStart } from '@/lib/paging'
import { useFiles, useRecords } from '@/lib/queries'
import { isRecordsFile, type FileRecord } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

const PREVIEW_LIMIT = 5 * 1024 * 1024

export function PreviewHost({ workspaceId }: { workspaceId: string }) {
  const target = usePreview((s) => s.target)
  const files = useFiles(workspaceId)

  if (target.kind === 'none') {
    return (
      <Empty
        title="Nothing selected"
        body="Pick a file on the left, or ask the agent to show you something. Records, task notes, scripts and runs open here."
      />
    )
  }
  if (target.kind === 'task') {
    return <TaskEditor workspaceId={workspaceId} />
  }
  if (target.kind === 'script') {
    return <ScriptView workspaceId={workspaceId} version={target.version} />
  }
  if (target.kind === 'run') {
    return (
      <RunView key={target.runId} workspaceId={workspaceId} runId={target.runId} row={target.row} />
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
        <span className="text-muted-foreground">
          {isRecordsFile(file)
            ? `${formatCount(file.meta.record_count ?? 0, 'record')} · ${formatBytes(file.size)}`
            : formatBytes(file.size)}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          nativeButton={false}
          render={
            <a href={fileDownloadUrl(workspaceId, file.id)} aria-label={`Download ${file.name}`} />
          }
        >
          <DownloadIcon />
          <span className="sr-only">Download</span>
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        {isRecordsFile(file) ? (
          <FileTable key={file.id} workspaceId={workspaceId} file={file} row={target.row} />
        ) : file.meta.error ? (
          <Empty title="Not readable as records" body={file.meta.error} />
        ) : file.size > PREVIEW_LIMIT ? (
          <Empty
            title="Too large for a whole-file preview"
            body="Files above 5 MB can only be browsed as records (CSV, TSV, JSON, JSONL)."
          />
        ) : (
          <TextFile workspaceId={workspaceId} file={file} />
        )}
      </div>
    </div>
  )
}

function FileTable({
  workspaceId,
  file,
  row,
}: {
  workspaceId: string
  file: FileRecord
  row?: number
}) {
  const openFile = usePreview((s) => s.openFile)
  const [paging, setPaging] = useState({ row, offset: row === undefined ? 0 : pageStart(row) })
  // a row opened by the agent (or a click) brings its page into view
  if (paging.row !== row) {
    setPaging({ row, offset: row === undefined ? paging.offset : pageStart(row) })
  }
  const offset = paging.offset
  const setOffset = (next: number) => setPaging((p) => ({ ...p, offset: next }))
  const page = useRecords(workspaceId, file.id, offset, PAGE_SIZE)
  const columns = page.data?.columns ?? file.meta.columns ?? []
  const records = page.data?.records

  useEffect(() => {
    if (row === undefined || !records) return
    document.querySelector(`[data-row="${row}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [row, records])

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-8 shrink-0 items-center gap-2 border-b px-3">
        <Pager offset={offset} total={page.data?.total ?? 0} onOffset={setOffset} />
        {page.isFetching && <span className="text-muted-foreground text-xs">loading…</span>}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {page.isError ? (
          <Empty title="Cannot show the records" body={page.error.message} />
        ) : (
          <RecordsTable
            columns={columns}
            rows={records ?? []}
            selectedRow={row}
            onSelect={(r) => openFile(file.id, r)}
          />
        )}
      </div>
    </div>
  )
}

function TextFile({ workspaceId, file }: { workspaceId: string; file: FileRecord }) {
  const content = useQuery({
    queryKey: ['file-content', workspaceId, file.id],
    queryFn: () => api<string>(fileContentUrl(workspaceId, file.id), { asText: true }),
    staleTime: 60_000,
  })
  if (content.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading…</p>
  if (content.isError) {
    const message = content.error instanceof ApiError ? content.error.detail : content.error.message
    return <Empty title="Cannot show this file" body={message} />
  }
  return <TextPreview file={file} content={content.data} />
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <FileSearchIcon className="text-muted-foreground size-8" />
      <p className="font-medium">{title}</p>
      <p className="text-muted-foreground max-w-sm text-sm">{body}</p>
    </div>
  )
}
