import { Link } from '@tanstack/react-router'
import { FileTextIcon, FolderOpenIcon, PlusIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
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
import { useCreateProject, useProjects } from '@/lib/queries'
import type { SourceType } from '@/lib/types'

export function ProjectsPage() {
  const projects = useProjects()
  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-6 p-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Elexifier</h1>
          <p className="text-muted-foreground text-sm">Your dictionary conversion projects</p>
        </div>
        <div className="flex items-center gap-2">
          <NewProjectDialog />
          <UserMenu />
        </div>
      </header>
      {projects.isPending && <p className="text-muted-foreground text-sm">Loading projects…</p>}
      {projects.isError && <p className="text-destructive text-sm">{projects.error.message}</p>}
      {projects.data && projects.data.length === 0 && (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <FolderOpenIcon className="text-muted-foreground mx-auto mb-3 size-8" />
          <p className="font-medium">No projects yet</p>
          <p className="text-muted-foreground mt-1 text-sm">
            Create a project, upload a dictionary and start the conversation.
          </p>
        </div>
      )}
      {projects.data && projects.data.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2">
          {projects.data.map((project) => (
            <li key={project.id}>
              <Link
                to="/projects/$projectId"
                params={{ projectId: project.id }}
                className="hover:bg-accent block rounded-lg border p-4 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 font-medium">
                    <FileTextIcon className="text-muted-foreground size-4" />
                    {project.name}
                  </div>
                  <Badge variant="outline">{project.source_type.toUpperCase()}</Badge>
                </div>
                {project.description && (
                  <p className="text-muted-foreground mt-2 line-clamp-2 text-sm">
                    {project.description}
                  </p>
                )}
                <p className="text-muted-foreground mt-3 text-xs">
                  Updated {formatDate(project.updated_at)} · {project.my_role}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function NewProjectDialog() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [sourceType, setSourceType] = useState<SourceType>('xml')
  const create = useCreateProject()

  function submit(event: React.FormEvent) {
    event.preventDefault()
    create.mutate(
      { name: name.trim(), source_type: sourceType, description: description.trim() || undefined },
      {
        onSuccess: () => {
          setOpen(false)
          setName('')
          setDescription('')
          toast.success('Project created')
        },
        onError: (error) => toast.error(error.message),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button />}>
        <PlusIcon /> New project
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              One project per dictionary. You can upload several files.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="project-name">Name</Label>
            <Input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Dictionary of Standard Slovene, 2nd edition"
              required
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Source</Label>
            <div className="grid grid-cols-2 gap-2">
              {(['xml', 'pdf'] as const).map((value) => (
                <button
                  type="button"
                  key={value}
                  onClick={() => setSourceType(value)}
                  className={`rounded-md border p-3 text-left text-sm transition-colors ${
                    sourceType === value ? 'border-primary bg-accent' : 'hover:bg-accent/50'
                  }`}
                >
                  <div className="font-medium">{value === 'xml' ? 'Legacy XML' : 'PDF'}</div>
                  <div className="text-muted-foreground text-xs">
                    {value === 'xml'
                      ? 'An export from a dictionary writing system or a TEI file'
                      : 'A typeset dictionary as PDF pages'}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="project-description">Description (optional)</Label>
            <Textarea
              id="project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this dictionary is, where it comes from, anything the agent should know."
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
