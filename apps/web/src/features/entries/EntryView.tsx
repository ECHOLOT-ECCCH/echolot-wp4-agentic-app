import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DmlexRendering } from '@/features/entries/DmlexRendering'
import { IssueList } from '@/features/entries/IssueList'
import { TextPreview } from '@/features/preview/TextPreview'
import { ApiError } from '@/lib/api'
import { useConvertedEntry, useEntry, useEntryPreview, useMappings } from '@/lib/queries'
import type { EntryIssue } from '@/lib/types'

export type OutputView = 'rendered' | 'xml' | 'json'

/** What the output pane renders, whether it came from a run or was converted just now. */
export interface OutputLike {
  issues: EntryIssue[]
  dmlex: Record<string, unknown>
  xml: string
}

/**
 * Source XML next to its DMLex output. Without a run the output is converted on the spot with
 * the latest mapping (custom steps applied) and validated on its own, so every entry is always
 * up to date; opened from a run, the run's stored output shows, with a switch to the live view.
 */
export function EntryView({
  projectId,
  entryId,
  runId,
}: {
  projectId: string
  entryId: string
  runId: string | null
}) {
  const entry = useEntry(projectId, entryId)
  const mappings = useMappings(projectId)
  const latest = mappings.data?.[0]?.version ?? null
  const [showRun, setShowRun] = useState(true)
  const runMode = runId !== null && showRun
  const converted = useConvertedEntry(projectId, runMode ? entryId : null, runId)
  const preview = useEntryPreview(projectId, runMode ? null : entryId, latest)
  const [view, setView] = useState<OutputView>('rendered')
  if (entry.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading entry…</p>
  if (entry.isError) return <p className="text-destructive p-3 text-sm">{entry.error.message}</p>

  const runOutputs = converted.data ?? []
  const live = preview.data
  const outputs: (OutputLike & { key: string })[] = runMode
    ? runOutputs.map((o) => ({ key: o.id, issues: o.issues, dmlex: o.dmlex, xml: o.xml }))
    : (live?.outputs ?? []).map((o) => ({
        key: `live-${o.position}`,
        issues: o.issues,
        dmlex: o.dmlex,
        xml: o.xml,
      }))
  const hasOutput = outputs.length > 0
  const loading = runMode ? converted.isPending : preview.isPending && latest !== null
  const previewError =
    !runMode && preview.isError
      ? preview.error instanceof ApiError
        ? preview.error.detail
        : preview.error.message
      : null

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">{entry.data.headword ?? '(no headword)'}</span>
        <span className="text-muted-foreground">entry {entry.data.ordinal + 1}</span>
        {runMode && runOutputs[0] && (
          <span className="text-muted-foreground" data-testid="output-origin">
            · From run · mapping v{runOutputs[0].mapping_version}
            {runOutputs.length > 1 ? ` · ${runOutputs.length} output entries` : ''}
          </span>
        )}
        {!runMode && live && (
          <span className="text-muted-foreground" data-testid="output-origin">
            · Live · mapping v{live.mapping_version}
            {live.steps.length > 0
              ? ` · ${live.steps.length} step${live.steps.length === 1 ? '' : 's'}`
              : ''}
            {live.outputs.length > 1 ? ` · ${live.outputs.length} output entries` : ''}
          </span>
        )}
        {runId !== null && latest !== null && (
          <Button variant="ghost" size="xs" onClick={() => setShowRun((v) => !v)}>
            {runMode ? `Show live (v${latest})` : 'Show run output'}
          </Button>
        )}
        {hasOutput && (
          <Tabs
            value={view}
            onValueChange={(value) => setView(value as OutputView)}
            className="ml-auto"
          >
            <TabsList className="h-7">
              <TabsTrigger value="rendered" className="text-xs">
                Rendered
              </TabsTrigger>
              <TabsTrigger value="xml" className="text-xs">
                DMLex XML
              </TabsTrigger>
              <TabsTrigger value="json" className="text-xs">
                JSON
              </TabsTrigger>
            </TabsList>
          </Tabs>
        )}
      </div>
      <div className="@container min-h-0 flex-1">
        <div className="grid h-full grid-cols-1 grid-rows-2 @3xl:grid-cols-2 @3xl:grid-rows-1">
          <div className="min-h-0 border-b @3xl:border-r @3xl:border-b-0">
            <p className="text-muted-foreground bg-muted/40 border-b px-3 py-1 text-[11px] uppercase">
              Source XML
            </p>
            <div className="h-[calc(100%-1.5rem)]">
              <TextPreview
                file={{ name: 'entry.xml', mime: 'application/xml' }}
                content={entry.data.source_xml}
              />
            </div>
          </div>
          <div className="flex min-h-0 flex-col">
            <p className="text-muted-foreground bg-muted/40 border-b px-3 py-1 text-[11px] uppercase">
              DMLex output
            </p>
            {!runMode && live && (
              <LiveNotes
                steps={live.steps}
                warnings={[...live.warnings, ...live.plan_warnings]}
                resourceIssues={live.resource_issues}
              />
            )}
            <div className="min-h-0 flex-1 overflow-auto">
              {loading ? (
                <p className="text-muted-foreground p-3 text-sm">Converting…</p>
              ) : previewError ? (
                <p className="text-destructive p-3 text-sm">{previewError}</p>
              ) : !runMode && mappings.data && latest === null ? (
                <p className="text-muted-foreground p-3 text-sm">
                  No mapping yet. Ask the agent to write one; every entry then converts here as you
                  open it.
                </p>
              ) : !hasOutput && !runMode && live?.skipped ? (
                <p className="text-muted-foreground p-3 text-sm">
                  The mapping produces no entry for this source entry: its element matches none of
                  the entry selectors.
                </p>
              ) : !hasOutput ? (
                <p className="text-muted-foreground p-3 text-sm">
                  {runMode ? 'This run did not include this entry.' : 'Nothing to show yet.'}
                </p>
              ) : (
                outputs.map((output) => (
                  <ConvertedOutput key={output.key} output={output} view={view} />
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function LiveNotes({
  steps,
  warnings,
  resourceIssues,
}: {
  steps: string[]
  warnings: string[]
  resourceIssues: EntryIssue[]
}) {
  return (
    <div className="text-muted-foreground border-b px-3 py-1 text-[11px]" data-testid="live-notes">
      <span title="Duplicate ids, homograph numbers and other cross-entry checks need a sample or a full run">
        Converted just now{steps.length > 0 ? ` (steps: ${steps.join(', ')})` : ''}; checked alone.
      </span>
      {warnings.map((warning, i) => (
        <span key={i} className="ml-2 text-amber-700 dark:text-amber-400">
          {warning}
        </span>
      ))}
      {resourceIssues.length > 0 && (
        <span className="ml-2">
          <IssueList issues={resourceIssues} compact />
        </span>
      )}
    </div>
  )
}

export function ConvertedOutput({ output, view }: { output: OutputLike; view: OutputView }) {
  return (
    <div className="flex flex-col border-b last:border-b-0">
      {output.issues.length > 0 && (
        <div className="border-b bg-amber-50/60 px-3 py-2 dark:bg-amber-950/20">
          <IssueList issues={output.issues} />
        </div>
      )}
      {view === 'rendered' && <DmlexRendering entry={output.dmlex} />}
      {view === 'xml' && (
        <div className="h-[28rem]">
          <TextPreview file={{ name: 'entry.xml', mime: 'application/xml' }} content={output.xml} />
        </div>
      )}
      {view === 'json' && (
        <div className="h-[28rem]">
          <TextPreview
            file={{ name: 'entry.json', mime: 'application/json' }}
            content={JSON.stringify(output.dmlex, null, 2)}
          />
        </div>
      )}
    </div>
  )
}
