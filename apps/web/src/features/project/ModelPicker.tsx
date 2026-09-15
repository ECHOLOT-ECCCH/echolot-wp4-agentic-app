import { ChevronDownIcon, CpuIcon, EyeIcon, SearchIcon, WrenchIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { formatPrice } from '@/lib/format'
import { useCredentials, useModels, useUpdateProject } from '@/lib/queries'
import type { ModelInfo, Project } from '@/lib/types'

export function ModelPicker({ project }: { project: Project }) {
  const [open, setOpen] = useState(false)
  const credentials = useCredentials()
  const [credentialId, setCredentialId] = useState<string | null>(null)
  const activeCredentialId =
    credentialId ??
    credentials.data?.find((c) => c.id === project.default_model?.credential_id)?.id ??
    credentials.data?.[0]?.id ??
    null
  const models = useModels(open ? activeCredentialId : null)
  const [filter, setFilter] = useState('')
  const update = useUpdateProject(project.id)
  const canEdit = project.my_role !== 'viewer'

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = models.data ?? []
    if (!q) return list.slice(0, 200)
    return list
      .filter((m) => m.id.toLowerCase().includes(q) || m.label.toLowerCase().includes(q))
      .slice(0, 200)
  }, [models.data, filter])

  function choose(model: ModelInfo) {
    if (!activeCredentialId) return
    const credential = credentials.data?.find((c) => c.id === activeCredentialId)
    update.mutate(
      {
        default_model: {
          provider: credential?.provider ?? model.provider,
          model_id: model.id,
          credential_id: activeCredentialId,
        },
      },
      {
        onSuccess: () => {
          setOpen(false)
          toast.success(`Model set to ${model.label}`)
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  const current = project.default_model

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" disabled={!canEdit} />}>
        <CpuIcon />
        <span className="max-w-48 truncate">{current ? current.model_id : 'Choose a model'}</span>
        <ChevronDownIcon />
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Model for this project</DialogTitle>
          <DialogDescription>
            The agent uses this model with your own key. PDF projects need a model that can see
            images.
          </DialogDescription>
        </DialogHeader>
        {credentials.data?.length === 0 ? (
          <p className="text-sm">
            You have no credentials yet.{' '}
            <a className="underline" href="/settings/credentials">
              Add one in Settings
            </a>
            .
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-1">
              {credentials.data?.map((c) => (
                <Button
                  key={c.id}
                  size="sm"
                  variant={c.id === activeCredentialId ? 'default' : 'outline'}
                  onClick={() => {
                    setCredentialId(c.id)
                    setFilter('')
                  }}
                >
                  {c.label}
                </Button>
              ))}
            </div>
            <div className="relative">
              <SearchIcon className="text-muted-foreground absolute top-2 left-2 size-4" />
              <Input
                className="pl-8"
                placeholder="Filter models"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                autoFocus
              />
            </div>
            <div className="max-h-80 overflow-y-auto rounded-md border">
              {models.isPending && activeCredentialId && (
                <p className="text-muted-foreground p-3 text-sm">Loading models…</p>
              )}
              {models.isError && (
                <p className="text-destructive p-3 text-sm">{models.error.message}</p>
              )}
              {filtered.map((model) => {
                const selected =
                  current?.model_id === model.id && current?.credential_id === activeCredentialId
                return (
                  <button
                    type="button"
                    key={model.id}
                    onClick={() => choose(model)}
                    className={`hover:bg-accent flex w-full items-center gap-2 border-b px-3 py-2 text-left text-sm last:border-b-0 ${
                      selected ? 'bg-accent' : ''
                    }`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium">{model.label}</span>
                        {model.recommended && <Badge variant="secondary">recommended</Badge>}
                      </div>
                      <div className="text-muted-foreground truncate text-xs">{model.id}</div>
                    </div>
                    <div className="text-muted-foreground flex shrink-0 items-center gap-2 text-xs">
                      {model.supports_vision && (
                        <EyeIcon className="size-3.5" aria-label="vision" />
                      )}
                      {model.supports_tools && (
                        <WrenchIcon className="size-3.5" aria-label="tools" />
                      )}
                      {model.price_in_per_m !== null && (
                        <span>
                          {formatPrice(model.price_in_per_m)} / {formatPrice(model.price_out_per_m)}
                        </span>
                      )}
                    </div>
                  </button>
                )
              })}
              {models.data && filtered.length === 0 && (
                <p className="text-muted-foreground p-3 text-sm">No models match.</p>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
