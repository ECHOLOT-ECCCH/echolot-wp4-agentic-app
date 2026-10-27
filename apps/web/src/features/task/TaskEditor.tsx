import { SaveIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { CodeEditor } from '@/features/preview/CodeEditor'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { useSaveTask, useTask } from '@/lib/queries'

const TEMPLATE = `# Task

What should happen to each record: which columns go in, which service is used, what comes
out, and what to do when there is no result. The agent keeps these notes up to date; edit
them whenever something is decided.
`

/** The workspace's task notes: the agreed description of the per-record function. */
export function TaskEditor({ workspaceId }: { workspaceId: string }) {
  const task = useTask(workspaceId)
  const save = useSaveTask(workspaceId)
  const [text, setText] = useState<string | null>(null)
  const loadedAt = useRef<string | null>(null)

  useEffect(() => {
    if (!task.data) return
    if (loadedAt.current === task.data.updated_at) return
    loadedAt.current = task.data.updated_at
    setText(task.data.markdown || TEMPLATE)
  }, [task.data])

  if (task.isError) return <p className="text-destructive p-3 text-sm">{task.error.message}</p>
  if (task.isPending || text === null)
    return <p className="text-muted-foreground p-3 text-sm">Loading task notes…</p>

  const saved = task.data.markdown || TEMPLATE
  const dirty = text !== saved
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">Task notes</span>
        <span className="text-muted-foreground">
          {task.data.markdown ? `saved ${formatDate(task.data.updated_at)}` : 'nothing saved yet'}
        </span>
        <span className="text-muted-foreground ml-auto">{dirty ? 'unsaved changes' : ''}</span>
        <Button
          size="xs"
          disabled={!dirty || save.isPending}
          onClick={() =>
            save.mutate(text, {
              onSuccess: (stored) => {
                loadedAt.current = stored.updated_at
                toast.success('Task notes saved')
              },
              onError: (error) =>
                toast.error(error instanceof ApiError ? error.detail : error.message),
            })
          }
        >
          <SaveIcon /> Save
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <CodeEditor value={text} onChange={setText} language="markdown" ariaLabel="Task notes" />
      </div>
    </div>
  )
}
