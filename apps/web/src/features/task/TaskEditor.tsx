import { SaveIcon } from 'lucide-react'
import { useState } from 'react'
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

/** The workspace's task notes: the agreed description of the per-record function. The agent
 *  writes them too; a newer version replaces the text unless the user has unsaved edits. */
export function TaskEditor({ workspaceId }: { workspaceId: string }) {
  const task = useTask(workspaceId)
  const save = useSaveTask(workspaceId)
  // the text being edited and the stored version it started from
  const [draft, setDraft] = useState<{ text: string; base: string } | null>(null)

  if (task.isError) return <p className="text-destructive p-3 text-sm">{task.error.message}</p>
  if (task.isPending)
    return <p className="text-muted-foreground p-3 text-sm">Loading task notes…</p>

  const stored = task.data.markdown || TEMPLATE
  const editing = draft !== null && draft.text !== draft.base
  const text = editing ? draft.text : stored
  const newerStored = editing && draft.base !== stored

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">Task notes</span>
        <span className="text-muted-foreground">
          {task.data.markdown ? `saved ${formatDate(task.data.updated_at)}` : 'nothing saved yet'}
        </span>
        <span className="text-muted-foreground ml-auto">
          {newerStored ? 'changed meanwhile; saving replaces it' : editing ? 'unsaved changes' : ''}
        </span>
        {newerStored && (
          <Button size="xs" variant="outline" onClick={() => setDraft(null)}>
            Discard mine
          </Button>
        )}
        <Button
          size="xs"
          disabled={!editing || save.isPending}
          onClick={() =>
            save.mutate(text, {
              onSuccess: () => {
                setDraft(null)
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
        <CodeEditor
          value={text}
          onChange={(next) => setDraft((d) => ({ text: next, base: d?.base ?? stored }))}
          language="markdown"
          ariaLabel="Task notes"
        />
      </div>
    </div>
  )
}
