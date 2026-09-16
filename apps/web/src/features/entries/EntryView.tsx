import { useState } from 'react'

import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DmlexRendering } from '@/features/entries/DmlexRendering'
import { IssueList } from '@/features/entries/IssueList'
import { TextPreview } from '@/features/preview/TextPreview'
import { useConvertedEntry, useEntry } from '@/lib/queries'
import type { ConvertedEntryDetail } from '@/lib/types'

export type OutputView = 'rendered' | 'xml' | 'json'

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
  const converted = useConvertedEntry(projectId, entryId, runId)
  const [view, setView] = useState<OutputView>('rendered')
  if (entry.isPending) return <p className="text-muted-foreground p-3 text-sm">Loading entry…</p>
  if (entry.isError) return <p className="text-destructive p-3 text-sm">{entry.error.message}</p>
  const outputs = converted.data ?? []
  const first = outputs[0]
  const hasOutput = first !== undefined
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-xs">
        <span className="font-medium">{entry.data.headword ?? '(no headword)'}</span>
        <span className="text-muted-foreground">entry {entry.data.ordinal + 1}</span>
        {first && (
          <span className="text-muted-foreground">
            · mapping v{first.mapping_version}
            {outputs.length > 1 ? ` · ${outputs.length} output entries` : ''}
          </span>
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
            <div className="min-h-0 flex-1 overflow-auto">
              {converted.isPending ? (
                <p className="text-muted-foreground p-3 text-sm">Loading…</p>
              ) : !hasOutput ? (
                <p className="text-muted-foreground p-3 text-sm">
                  Not converted yet. Ask the agent to run a sample conversion that includes this
                  entry.
                </p>
              ) : (
                outputs.map((output) => (
                  <ConvertedOutput key={output.id} output={output} view={view} />
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export function ConvertedOutput({
  output,
  view,
}: {
  output: ConvertedEntryDetail
  view: OutputView
}) {
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
