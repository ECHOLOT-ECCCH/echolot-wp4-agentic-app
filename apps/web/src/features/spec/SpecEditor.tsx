import { SaveIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { CodeEditor } from '@/features/preview/CodeEditor'
import { ApiError } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { useSaveSpec, useSpec } from '@/lib/queries'

const TEMPLATE = `# Conversion specification

Describe the source dictionary, what each element means, and every decision taken during the
conversion. The agent keeps this file up to date; edit it whenever something is decided.
`

export function SpecEditor({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const spec = useSpec(projectId)
  const save = useSaveSpec(projectId)
  const [text, setText] = useState<string | null>(null)
  const loadedVersion = useRef<number | null>(null)

  useEffect(() => {
    if (spec.isPending) return
    const version = spec.data?.version ?? 0
    if (loadedVersion.current === version) return
    loadedVersion.current = version
    setText(spec.data?.markdown ?? TEMPLATE)
  }, [spec.data, spec.isPending])

  if (spec.isPending || text === null)
    return <p className="text-muted-foreground p-3 text-sm">Loading specification…</p>
  if (spec.isError) return <p className="text-destructive p-3 text-sm">{spec.error.message}</p>

  const saved = spec.data?.markdown ?? TEMPLATE
  const dirty = text !== saved
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">Conversion specification</span>
        <span className="text-muted-foreground">
          {spec.data
            ? `v${spec.data.version} · ${formatDate(spec.data.created_at)}`
            : 'nothing saved yet'}
        </span>
        <span className="text-muted-foreground ml-auto">{dirty ? 'unsaved changes' : ''}</span>
        <Button
          size="xs"
          disabled={!canEdit || !dirty || save.isPending}
          onClick={() =>
            save.mutate(text, {
              onSuccess: (record) => {
                loadedVersion.current = record.version
                toast.success(`Specification saved as version ${record.version}`)
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
          onChange={setText}
          language="markdown"
          readOnly={!canEdit}
          ariaLabel="Specification markdown"
        />
      </div>
    </div>
  )
}
