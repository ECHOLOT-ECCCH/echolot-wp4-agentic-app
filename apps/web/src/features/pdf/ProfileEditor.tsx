import { SaveIcon } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { ApiError } from '@/lib/api'
import { useSaveStructureProfile, useStructureProfile } from '@/lib/queries'
import { type Draft, fromDraft, toDraft } from '@/features/pdf/profileDraft'
import type { StructureProfile } from '@/lib/types'

/** The structure profile of a PDF project: what the agent tells the model about the dictionary
 *  before structuring every entry. Editable here as well as through the agent. */
export function ProfileEditor({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const profile = useStructureProfile(projectId)
  if (profile.isPending)
    return <p className="text-muted-foreground p-3 text-sm">Loading profile…</p>
  if (profile.isError)
    return (
      <p className="text-destructive p-3 text-sm">
        {profile.error instanceof ApiError ? profile.error.detail : profile.error.message}
      </p>
    )
  // a fresh form whenever the stored profile changes (a save, the agent, another tab)
  return (
    <ProfileForm
      key={JSON.stringify(profile.data)}
      projectId={projectId}
      initial={profile.data}
      canEdit={canEdit}
    />
  )
}

function ProfileForm({
  projectId,
  initial,
  canEdit,
}: {
  projectId: string
  initial: StructureProfile
  canEdit: boolean
}) {
  const save = useSaveStructureProfile(projectId)
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial))
  const dirty = JSON.stringify(fromDraft(draft)) !== JSON.stringify(fromDraft(toDraft(initial)))
  const field = (key: keyof Draft) => ({
    value: draft[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setDraft((d) => ({ ...d, [key]: e.target.value })),
    readOnly: !canEdit,
  })
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">Structure profile</span>
        <span className="text-muted-foreground">
          {initial.stored ? 'stored with the project' : 'not set yet'}
        </span>
        <span className="text-muted-foreground ml-auto">{dirty ? 'unsaved changes' : ''}</span>
        <Button
          size="xs"
          disabled={!canEdit || !dirty || save.isPending}
          onClick={() =>
            save.mutate(fromDraft(draft), {
              onSuccess: () => toast.success('Profile saved'),
              onError: (error) =>
                toast.error(error instanceof ApiError ? error.detail : error.message),
            })
          }
        >
          <SaveIcon /> Save
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4 text-sm">
        <p className="text-muted-foreground text-xs">
          What the agent tells the model about this dictionary before structuring each entry. It
          goes into every structuring prompt, so a change means the next sample or full run calls
          the model again for every entry; earlier results are not reused.
        </p>
        <div className="flex flex-col gap-1">
          <label htmlFor="profile-source" className="text-xs font-medium">
            Source language
          </label>
          <Input
            id="profile-source"
            className="max-w-xs"
            placeholder="e.g. en"
            {...field('source_language')}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="profile-targets" className="text-xs font-medium">
            Target languages (comma-separated)
          </label>
          <Input
            id="profile-targets"
            className="max-w-xs"
            placeholder="e.g. sl, de"
            {...field('target_languages')}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="profile-contents" className="text-xs font-medium">
            What an entry contains and how it is printed
          </label>
          <Textarea
            id="profile-contents"
            className="min-h-40"
            placeholder="Headword in bold, part of speech in italics, numbered senses, translations after a dash, examples in italics with their translations, domain labels in small caps…"
            {...field('contents')}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="profile-prefix" className="text-xs font-medium">
            Entry id prefix
          </label>
          <Input
            id="profile-prefix"
            className="w-32"
            placeholder="e"
            {...field('entry_id_prefix')}
          />
        </div>
      </div>
    </div>
  )
}
