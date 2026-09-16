import { ArrowDownIcon, ArrowUpIcon, CodeIcon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { useEditSteps, useSteps } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

/** The custom steps of the conversion pipeline, in the order they run: switch them on or off,
 *  move them, delete them, open their code. */
export function StepList({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const steps = useSteps(projectId)
  const edits = useEditSteps(projectId)
  const openFile = usePreview((s) => s.openFile)
  const fail = (error: unknown) =>
    toast.error(error instanceof ApiError ? error.detail : String(error))
  const list = steps.data ?? []
  if (list.length === 0) return null
  const move = (index: number, delta: number) => {
    const ids = list.map((s) => s.id)
    const target = index + delta
    if (target < 0 || target >= ids.length) return
    ;[ids[index], ids[target]] = [ids[target]!, ids[index]!]
    edits.reorder.mutate(ids, { onError: fail })
  }
  return (
    <section className="flex flex-col gap-1" aria-label="Conversion steps">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        Steps
      </h2>
      <ul className="flex flex-col">
        {list.map((step, index) => (
          <li key={step.id} className="group flex items-center gap-1 rounded-md px-1 py-1 text-sm">
            <input
              type="checkbox"
              aria-label={`${step.name} enabled`}
              checked={step.enabled}
              disabled={!canEdit || edits.update.isPending}
              onChange={(e) =>
                edits.update.mutate({ id: step.id, enabled: e.target.checked }, { onError: fail })
              }
            />
            <button
              type="button"
              className={`flex min-w-0 flex-1 items-center gap-2 text-left ${
                step.enabled ? '' : 'text-muted-foreground line-through'
              }`}
              title={step.config.purpose ?? step.name}
              onClick={() => step.code_artifact_id && openFile(step.code_artifact_id)}
            >
              <CodeIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="truncate">
                {index + 1}. {step.name}
              </span>
            </button>
            {canEdit && (
              <span className="flex shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Move ${step.name} up`}
                  disabled={index === 0 || edits.reorder.isPending}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUpIcon />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Move ${step.name} down`}
                  disabled={index === list.length - 1 || edits.reorder.isPending}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDownIcon />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Delete ${step.name}`}
                  onClick={() => {
                    if (confirm(`Delete step "${step.name}"? Its code stays as an artifact.`)) {
                      edits.remove.mutate(step.id, { onError: fail })
                    }
                  }}
                >
                  <Trash2Icon />
                </Button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
