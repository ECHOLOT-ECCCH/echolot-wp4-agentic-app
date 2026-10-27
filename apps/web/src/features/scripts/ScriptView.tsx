import { PlayIcon, SaveIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { CodeEditor } from '@/features/preview/CodeEditor'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { useFiles, useSaveScript, useScript, useScripts, useStartRun } from '@/lib/queries'
import { isRecordsFile } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

export const SAMPLE_SIZE = 20

export const SCRIPT_TEMPLATE = `"""What this script does to each record (the agent usually writes it)."""


def process(record, ctx):
    # record: one input record, column -> value
    # ctx.service("<name>").post(path, json=...) calls a registered service
    return {"name_upper": record.get("name", "").upper()}
`

/** One version of the workspace's script, editable: saving stores a new version. */
export function ScriptView({ workspaceId, version }: { workspaceId: string; version?: number }) {
  const scripts = useScripts(workspaceId)
  const open = usePreview((s) => s.open)
  const latest = scripts.data?.[0]?.version ?? null
  const shown = version ?? latest
  const script = useScript(workspaceId, shown)
  const save = useSaveScript(workspaceId)
  const files = useFiles(workspaceId)
  const start = useStartRun(workspaceId)
  const inputs = (files.data ?? []).filter((f) => f.kind === 'input' && isRecordsFile(f))
  const [chosenInput, setChosenInput] = useState<string | null>(null)
  const inputId = chosenInput ?? inputs[0]?.id ?? null
  // the edited text, per version it started from
  const [draft, setDraft] = useState<{ version: number | null; text: string } | null>(null)
  const [note, setNote] = useState('')

  if (scripts.isPending || (shown !== null && script.isPending)) {
    return <p className="text-muted-foreground p-3 text-sm">Loading the script…</p>
  }
  if (scripts.isError)
    return <p className="text-destructive p-3 text-sm">{scripts.error.message}</p>
  if (script.isError) return <p className="text-destructive p-3 text-sm">{script.error.message}</p>

  const stored = script.data?.code ?? SCRIPT_TEMPLATE
  const text = draft && draft.version === shown ? draft.text : stored
  const dirty = text !== stored

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">Script</span>
        {scripts.data && scripts.data.length > 0 ? (
          <select
            aria-label="Script version"
            className="bg-background h-6 rounded-md border px-1"
            value={shown ?? ''}
            onChange={(e) => open({ kind: 'script', version: Number(e.target.value) })}
          >
            {scripts.data.map((s) => (
              <option key={s.version} value={s.version}>
                v{s.version} · {s.author}
                {s.version === latest ? ' · latest' : ''}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-muted-foreground">no version yet</span>
        )}
        {script.data && (
          <span className="text-muted-foreground truncate" title={script.data.note ?? ''}>
            {formatDate(script.data.created_at)}
            {script.data.note ? ` · ${script.data.note}` : ''}
          </span>
        )}
        {script.data?.services.map((name) => (
          <Badge key={name} variant="outline">
            {name}
          </Badge>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        <CodeEditor
          value={text}
          onChange={(next) => setDraft({ version: shown, text: next })}
          language="python"
          ariaLabel="Script code"
        />
      </div>
      <div className="flex h-10 shrink-0 items-center gap-2 border-t px-3 text-xs">
        <input
          aria-label="Version note"
          placeholder="What changed (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="bg-background h-7 min-w-0 flex-1 rounded-md border px-2"
        />
        <span className="text-muted-foreground">{dirty ? 'unsaved changes' : ''}</span>
        <select
          aria-label="Input file"
          className="bg-background h-7 max-w-40 rounded-md border px-1"
          value={inputId ?? ''}
          onChange={(e) => setChosenInput(e.target.value)}
        >
          {inputs.length === 0 && <option value="">no records file</option>}
          {inputs.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
        {[SAMPLE_SIZE, undefined].map((limit) => (
          <Button
            key={limit ?? 'all'}
            size="xs"
            variant="outline"
            title={dirty ? 'Save the script first' : undefined}
            disabled={dirty || !script.data || !inputId || start.isPending}
            onClick={() =>
              inputId &&
              script.data &&
              start.mutate(
                {
                  file_id: inputId,
                  script_version: script.data.version,
                  limit,
                  purpose: limit ? `Sample of ${limit} records` : 'All records',
                },
                {
                  onSuccess: (run) => open({ kind: 'run', runId: run.id }),
                  onError: (error) =>
                    toast.error(error instanceof ApiError ? error.detail : error.message),
                },
              )
            }
          >
            <PlayIcon /> {limit ? `Run ${limit}` : 'Run all'}
          </Button>
        ))}
        <Button
          size="xs"
          disabled={!dirty || save.isPending}
          onClick={() =>
            save.mutate(
              { code: text, note: note.trim() || undefined },
              {
                onSuccess: (saved) => {
                  setDraft(null)
                  setNote('')
                  open({ kind: 'script', version: saved.version })
                  toast.success(`Saved as version ${saved.version}`)
                },
                onError: (error) =>
                  toast.error(error instanceof ApiError ? error.detail : error.message),
              },
            )
          }
        >
          <SaveIcon /> Save as new version
        </Button>
      </div>
    </div>
  )
}
