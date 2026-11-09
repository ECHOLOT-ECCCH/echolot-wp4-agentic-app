import { Link } from '@tanstack/react-router'
import { FolderOpenIcon, LayersIcon, PlusIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { UserMenu } from '@/features/auth/UserMenu'
import { formatDate } from '@/lib/format'
import { useCreateWorkspace, useWorkspaces } from '@/lib/queries'

export function WorkspacesPage() {
  const workspaces = useWorkspaces()
  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">ECHOLOT Workbench</h1>
          <p className="text-muted-foreground text-sm">
            Reconcile, enrich and transform datasets record by record with an agent
          </p>
        </div>
        <div className="flex items-center gap-2">
          <NewWorkspaceDialog />
          <UserMenu />
        </div>
      </header>
      {workspaces.isPending && <p className="text-muted-foreground text-sm">Loading workspaces…</p>}
      {workspaces.isError && <p className="text-destructive text-sm">{workspaces.error.message}</p>}
      {workspaces.data && workspaces.data.length === 0 && (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <FolderOpenIcon className="text-muted-foreground mx-auto mb-3 size-8" />
          <p className="font-medium">No workspaces yet</p>
          <p className="text-muted-foreground mt-1 text-sm">
            Create a workspace, upload a dataset and tell the agent what to do with each record.
          </p>
        </div>
      )}
      {workspaces.data && workspaces.data.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2">
          {workspaces.data.map((workspace) => (
            <li key={workspace.id}>
              <Link
                to="/w/$workspaceId"
                params={{ workspaceId: workspace.id }}
                className="hover:bg-accent block rounded-lg border p-4 transition-colors"
              >
                <div className="flex items-center gap-2 font-medium">
                  <LayersIcon className="text-muted-foreground size-4" />
                  {workspace.name}
                </div>
                {workspace.description && (
                  <p className="text-muted-foreground mt-2 line-clamp-2 text-sm">
                    {workspace.description}
                  </p>
                )}
                <p className="text-muted-foreground mt-3 text-xs">
                  Updated {formatDate(workspace.updated_at)}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function NewWorkspaceDialog() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useCreateWorkspace()

  function submit(event: React.FormEvent) {
    event.preventDefault()
    create.mutate(
      { name: name.trim(), description: description.trim() || undefined },
      {
        onSuccess: () => {
          setOpen(false)
          setName('')
          setDescription('')
          toast.success('Workspace created')
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <PlusIcon /> New workspace
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>New workspace</DialogTitle>
            <DialogDescription>
              One workspace per dataset and task. You can upload several files.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="workspace-name">Name</Label>
            <Input
              id="workspace-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="ehkultura people to Wikidata"
              required
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="workspace-description">Description (optional)</Label>
            <Textarea
              id="workspace-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Where the data comes from and what you want out of it."
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
