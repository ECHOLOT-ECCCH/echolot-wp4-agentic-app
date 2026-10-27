import { PlayIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatCount } from '@/lib/format'
import { useFiles, useScripts } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

function parseArgs(argsText?: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(argsText ?? '{}')
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {} // still streaming
  }
}

/** What the user approves for submit_run: the purpose, the records, the script and its services. */
export function SubmitRunArgs({
  argsText,
  workspaceId,
}: {
  argsText?: string
  workspaceId: string
}) {
  const args = parseArgs(argsText)
  const files = useFiles(workspaceId)
  const scripts = useScripts(workspaceId)
  const file = files.data?.find((f) => f.id === args.file_id)
  const version = typeof args.script_version === 'number' ? args.script_version : undefined
  const script = version ? scripts.data?.find((s) => s.version === version) : scripts.data?.[0]
  const total = file?.meta.record_count
  const limit = typeof args.limit === 'number' ? args.limit : undefined
  const records =
    limit !== undefined
      ? `the first ${formatCount(Math.min(limit, total ?? limit), 'record')}`
      : total !== undefined
        ? `all ${formatCount(total, 'record')}`
        : 'all records'
  return (
    <div className="flex flex-col gap-1 px-3 py-2 text-sm" data-testid="submit-run-args">
      {typeof args.purpose === 'string' && <p className="font-medium">{args.purpose}</p>}
      <p className="text-muted-foreground text-xs">
        Runs script {script ? `v${script.version}` : '(latest)'} on {records} of{' '}
        {file?.name ?? 'the file'}.
      </p>
      {script && (
        <p className="text-muted-foreground flex flex-wrap items-center gap-1 text-xs">
          Calls:{' '}
          {script.services.length === 0
            ? 'no services'
            : script.services.map((name) => (
                <Badge key={name} variant="outline">
                  {name}
                </Badge>
              ))}
        </p>
      )}
    </div>
  )
}

/** The result of the run tools: status line plus a button to open the run. */
export function RunBody({ data }: { data: Record<string, unknown> }) {
  const open = usePreview((s) => s.open)
  const runId = typeof data.run_id === 'string' ? data.run_id : null
  if (!runId) return null
  return (
    <Button
      size="xs"
      variant="outline"
      className="self-start"
      onClick={() => open({ kind: 'run', runId })}
    >
      <PlayIcon /> Open run
    </Button>
  )
}
