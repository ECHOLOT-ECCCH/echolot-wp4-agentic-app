import { FileTextIcon, GitBranchIcon, SlidersHorizontalIcon } from 'lucide-react'

import { useMappings, useSpec, useStructureProfile } from '@/lib/queries'
import type { SourceType } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

/** Left-pane entries for what drives the conversion: for an XML project the mapping (latest
 *  version) and the specification; for a PDF project the structure profile and the
 *  specification (a PDF has nothing to map). */
export function ConversionLinks({
  projectId,
  sourceType,
}: {
  projectId: string
  sourceType: SourceType
}) {
  const isPdf = sourceType === 'pdf'
  const mappings = useMappings(projectId, !isPdf)
  const profile = useStructureProfile(projectId, isPdf)
  const spec = useSpec(projectId)
  const target = usePreview((s) => s.target)
  const openMapping = usePreview((s) => s.openMapping)
  const openProfile = usePreview((s) => s.openProfile)
  const openSpec = usePreview((s) => s.openSpec)
  const latest = mappings.data?.[0]
  const row = (active: boolean) =>
    `flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
      active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
    }`
  const profileSummary = profile.data
    ? profile.data.stored
      ? `${profile.data.source_language} → ${profile.data.target_languages.join(', ') || '?'}`
      : 'not set yet'
    : ''
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        {isPdf ? 'Structuring' : 'Conversion'}
      </h2>
      <ul className="flex flex-col">
        <li>
          {isPdf ? (
            <button
              type="button"
              className={row(target.kind === 'profile')}
              onClick={openProfile}
              title="What the model is told about the dictionary before structuring each entry"
            >
              <SlidersHorizontalIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="truncate">Profile</span>
              <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                {profileSummary}
              </span>
            </button>
          ) : (
            <button
              type="button"
              className={row(target.kind === 'mapping')}
              onClick={() => openMapping(latest?.version)}
            >
              <GitBranchIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="truncate">Mapping</span>
              <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                {latest
                  ? `v${latest.version}${mappings.data && mappings.data.length > 1 ? ` · ${mappings.data.length} versions` : ''}`
                  : 'none yet'}
              </span>
            </button>
          )}
        </li>
        <li>
          <button type="button" className={row(target.kind === 'spec')} onClick={openSpec}>
            <FileTextIcon className="text-muted-foreground size-4 shrink-0" />
            <span className="truncate">Specification</span>
            <span className="text-muted-foreground ml-auto shrink-0 text-xs">
              {spec.data ? `v${spec.data.version}` : 'none yet'}
            </span>
          </button>
        </li>
      </ul>
    </section>
  )
}
