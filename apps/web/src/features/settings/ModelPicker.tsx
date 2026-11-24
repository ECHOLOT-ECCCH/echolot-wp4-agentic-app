import { Link } from '@tanstack/react-router'
import { AlertTriangleIcon, CheckIcon, ChevronDownIcon, CpuIcon, SearchIcon } from 'lucide-react'
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
import { useModelChoice, useSetModelChoice, useVendorModels, useVendors } from '@/lib/queries'
import type { ModelChoice, Vendor } from '@/lib/types'

const SERVER = '__server__'

/** The agent's model for the signed-in user: the server's, or a vendor's with the user's key. */
export function ModelPicker({ compact = false }: { compact?: boolean }) {
  const [open, setOpen] = useState(false)
  const choice = useModelChoice()

  const current = choice.data
  const label = !current
    ? 'Model'
    : current.source === 'own'
      ? current.model_id
      : current.server_model
        ? `Server default · ${current.model_id}`
        : 'Choose a model'
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            size={compact ? 'xs' : 'sm'}
            className="max-w-full min-w-0"
            aria-label="Choose the model"
            title={current?.problem ?? `The agent uses ${label}`}
          />
        }
      >
        {current && !current.ready ? <AlertTriangleIcon className="text-amber-600" /> : <CpuIcon />}
        <span className="truncate">{label}</span>
        <ChevronDownIcon />
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Model for the agent</DialogTitle>
          <DialogDescription>
            Use the server's model, or one from a vendor you have a key for. The key and the cost
            are yours; your data goes to that vendor.
          </DialogDescription>
        </DialogHeader>
        {open && current && <ChooseModel current={current} onChosen={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  )
}

function ChooseModel({ current, onChosen }: { current: ModelChoice; onChosen: () => void }) {
  const vendors = useVendors()
  const usable = (vendors.data ?? []).filter((v) => v.has_key)
  const [selected, setSelected] = useState<string>(
    current.source === 'own'
      ? current.provider
      : current.server_model
        ? SERVER
        : (usable[0]?.name ?? SERVER),
  )
  const setChoice = useSetModelChoice()

  function choose(provider: string | null, modelId?: string) {
    setChoice.mutate(
      { provider, model_id: modelId },
      {
        onSuccess: (next) => {
          if (next.ready) toast.success(`The agent now uses ${next.model_id}`)
          else toast.warning(next.problem ?? 'The model is not ready')
          onChosen()
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  const vendor = usable.find((v) => v.name === selected) ?? null
  return (
    <div className="flex flex-col gap-3">
      {current.problem && (
        <p className="flex items-start gap-2 rounded-md bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
          {current.problem}
        </p>
      )}
      <div className="flex flex-wrap gap-1">
        {current.server_model && (
          <Button
            size="sm"
            variant={selected === SERVER ? 'default' : 'outline'}
            onClick={() => setSelected(SERVER)}
          >
            Server default
          </Button>
        )}
        {usable.map((v) => (
          <Button
            key={v.name}
            size="sm"
            variant={selected === v.name ? 'default' : 'outline'}
            onClick={() => setSelected(v.name)}
          >
            {v.label}
          </Button>
        ))}
      </div>
      {selected === SERVER && current.server_model && (
        <div className="flex items-center justify-between gap-2 rounded-md border p-3 text-sm">
          <div>
            <div className="font-medium">{current.server_model.model_id}</div>
            <div className="text-muted-foreground text-xs">
              {current.server_model.provider}, provided by this server
            </div>
          </div>
          <Button
            size="sm"
            disabled={setChoice.isPending || current.source === 'server'}
            onClick={() => choose(null)}
          >
            {current.source === 'server' ? 'In use' : 'Use'}
          </Button>
        </div>
      )}
      {vendor && (
        <VendorModels
          vendor={vendor}
          currentId={
            current.source === 'own' && current.provider === vendor.name ? current.model_id : null
          }
          busy={setChoice.isPending}
          onChoose={(id) => choose(vendor.name, id)}
        />
      )}
      {!vendor && selected !== SERVER && (
        <p className="text-muted-foreground text-sm">No model is available yet.</p>
      )}
      <p className="text-muted-foreground text-xs">
        {usable.length === 0 ? 'Add a key ' : 'Add keys for other vendors '}
        under{' '}
        <Link to="/settings" className="underline">
          Settings → Model keys
        </Link>
        .
      </p>
    </div>
  )
}

function VendorModels({
  vendor,
  currentId,
  busy,
  onChoose,
}: {
  vendor: Vendor
  currentId: string | null
  busy: boolean
  onChoose: (modelId: string) => void
}) {
  const models = useVendorModels(vendor.name)
  const [filter, setFilter] = useState('')
  const [custom, setCustom] = useState('')

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase()
    const list = models.data?.models ?? []
    return (q ? list.filter((m) => `${m.id} ${m.label}`.toLowerCase().includes(q)) : list).slice(
      0,
      200,
    )
  }, [models.data, filter])

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <SearchIcon className="text-muted-foreground absolute top-2 left-2 size-4" />
        <Input
          className="pl-8"
          placeholder={`Filter ${vendor.label} models`}
          aria-label="Filter models"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      {models.data?.error && (
        <p className="text-muted-foreground text-xs">
          The full list is not available ({models.data.error}); these are suggestions.
        </p>
      )}
      <div className="max-h-72 overflow-y-auto rounded-md border">
        {models.isPending && <p className="text-muted-foreground p-3 text-sm">Loading models…</p>}
        {models.isError && <p className="text-destructive p-3 text-sm">{models.error.message}</p>}
        {models.data && filtered.length === 0 && (
          <p className="text-muted-foreground p-3 text-sm">No model matches.</p>
        )}
        <ul aria-label="Models">
          {filtered.map((model) => (
            <li key={model.id} className="border-b last:border-b-0">
              <button
                type="button"
                disabled={busy}
                onClick={() => onChoose(model.id)}
                className={`hover:bg-accent flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                  model.id === currentId ? 'bg-accent' : ''
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{model.label}</span>
                    {model.suggested && <Badge variant="secondary">suggested</Badge>}
                  </div>
                  {model.label !== model.id && (
                    <div className="text-muted-foreground truncate text-xs">{model.id}</div>
                  )}
                </div>
                {model.id === currentId && <CheckIcon className="size-4 shrink-0" />}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (custom.trim()) onChoose(custom.trim())
        }}
      >
        <Input
          placeholder="Or type a model id"
          aria-label="Model id"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
        />
        <Button type="submit" variant="outline" disabled={busy || !custom.trim()}>
          Use
        </Button>
      </form>
      <p className="text-muted-foreground text-xs">
        The agent needs a model that can call tools; larger models follow the workflow best.
      </p>
    </div>
  )
}
