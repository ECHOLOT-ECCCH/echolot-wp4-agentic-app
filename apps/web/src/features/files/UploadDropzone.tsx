import { UploadIcon } from 'lucide-react'
import { useCallback, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Progress } from '@/components/ui/progress'
import { uploadFile } from '@/lib/api'
import { formatBytes } from '@/lib/format'

interface UploadState {
  id: string
  name: string
  size: number
  fraction: number
  error?: string
}

export function UploadDropzone({
  projectId,
  onUploaded,
  disabled,
}: {
  projectId: string
  onUploaded: () => void
  disabled?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploads, setUploads] = useState<UploadState[]>([])

  const start = useCallback(
    (files: FileList | File[]) => {
      for (const file of Array.from(files)) {
        const id = `${file.name}-${Date.now()}-${Math.random()}`
        setUploads((u) => [...u, { id, name: file.name, size: file.size, fraction: 0 }])
        const handle = uploadFile(projectId, file, (fraction) =>
          setUploads((u) => u.map((x) => (x.id === id ? { ...x, fraction } : x))),
        )
        handle.promise
          .then(() => {
            setUploads((u) => u.filter((x) => x.id !== id))
            onUploaded()
            toast.success(`Uploaded ${file.name}`)
          })
          .catch((error: Error) => {
            setUploads((u) => u.map((x) => (x.id === id ? { ...x, error: error.message } : x)))
            toast.error(`${file.name}: ${error.message}`)
          })
      }
    },
    [projectId, onUploaded],
  )

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!disabled && e.dataTransfer.files.length) start(e.dataTransfer.files)
        }}
        className={`text-muted-foreground flex w-full items-center justify-center gap-2 rounded-md border border-dashed px-3 py-3 text-xs transition-colors ${
          dragging ? 'border-primary bg-accent text-foreground' : 'hover:bg-accent/50'
        } disabled:opacity-50`}
      >
        <UploadIcon className="size-3.5" />
        {disabled ? 'Viewers cannot upload' : 'Drop XML or PDF files here, or click'}
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".xml,.tei,.json,.nvh,.txt,.pdf,application/xml,text/xml,application/pdf"
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) start(e.target.files)
          e.target.value = ''
        }}
      />
      {uploads.map((u) => (
        <div key={u.id} className="flex flex-col gap-1 rounded-md border p-2 text-xs">
          <div className="flex justify-between gap-2">
            <span className="truncate">{u.name}</span>
            <span className="text-muted-foreground shrink-0">{formatBytes(u.size)}</span>
          </div>
          {u.error ? (
            <span className="text-destructive">{u.error}</span>
          ) : (
            <Progress value={Math.round(u.fraction * 100)} />
          )}
        </div>
      ))}
    </div>
  )
}
