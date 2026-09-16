import { FileTextIcon, GitBranchIcon } from 'lucide-react'

import { useMappings, useSpec } from '@/lib/queries'
import { usePreview } from '@/lib/uiActions'

/** Left-pane entries for the mapping (latest version) and the conversion specification. */
export function ConversionLinks({ projectId }: { projectId: string }) {
  const mappings = useMappings(projectId)
  const spec = useSpec(projectId)
  const target = usePreview((s) => s.target)
  const openMapping = usePreview((s) => s.openMapping)
  const openSpec = usePreview((s) => s.openSpec)
  const latest = mappings.data?.[0]
  const row = (active: boolean) =>
    `flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
      active ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/60'
    }`
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        Conversion
      </h2>
      <ul className="flex flex-col">
        <li>
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
