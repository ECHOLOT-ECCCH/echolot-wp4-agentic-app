import type { ToolCallMessagePartComponent } from '@assistant-ui/react'
import { PaperclipIcon } from 'lucide-react'
import { useState } from 'react'

import { ToolFallback } from '@/components/assistant-ui/elements/tool-fallback.aui'
import {
  type BodyProps,
  DiffRunsBody,
  MappingWriteBody,
  ProfileBody,
  RunCodeArgs,
  RunCodeBody,
  RunConversionBody,
} from '@/features/chat/toolBodies'
import { TOOL_LABELS } from '@/features/chat/toolLabels'
import { fileDownloadUrl } from '@/lib/api'
import type { ToolResultPayload } from '@/lib/types'
import { usePreview } from '@/lib/uiActions'

const TOOL_BODIES: Record<string, (props: BodyProps) => React.ReactNode> = {
  xml_profile: ProfileBody,
  mapping_write: MappingWriteBody,
  run_conversion: RunConversionBody,
  diff_runs: DiffRunsBody,
  run_code: RunCodeBody,
}

/** Tools whose arguments deserve a readable view (the approval card shows them). */
const TOOL_ARGS: Record<string, (props: { argsText?: string }) => React.ReactNode> = {
  run_code: RunCodeArgs,
}

function isToolResult(value: unknown): value is ToolResultPayload {
  return typeof value === 'object' && value !== null && 'summary' in value
}

/**
 * Renders every tool call the same way: a collapsible card with the summary the tool reported,
 * its structured data, and links to any artifacts. Approval prompts come from the fallback.
 */
export function makeGenericToolCard(projectId: string): ToolCallMessagePartComponent {
  const GenericToolCard: ToolCallMessagePartComponent = (part) => {
    const {
      toolName,
      result,
      status,
      argsText,
      addResult,
      resume,
      interrupt,
      approval,
      respondToApproval,
    } = part
    const requiresAction = status?.type === 'requires-action'
    const cancelled = status?.type === 'incomplete' && status.reason === 'cancelled'
    const [open, setOpen] = useState(requiresAction)
    const payload = isToolResult(result) ? result : null
    const isError = Boolean(payload?.data && 'error' in (payload.data ?? {}))

    return (
      <ToolFallback.Root open={open || requiresAction} onOpenChange={setOpen}>
        <ToolFallback.Trigger toolName={TOOL_LABELS[toolName] ?? toolName} status={status} />
        <ToolFallback.Content>
          <ToolFallback.Error status={status} />
          {TOOL_ARGS[toolName] ? (
            TOOL_ARGS[toolName]({ argsText })
          ) : (
            <ToolFallback.Args argsText={argsText} />
          )}
          {requiresAction && (
            <ToolFallback.Approval
              addResult={addResult}
              resume={resume}
              interrupt={interrupt}
              approval={approval}
              respondToApproval={respondToApproval}
              status={status}
            />
          )}
          {!cancelled && payload && (
            <ToolResultBody
              projectId={projectId}
              toolName={toolName}
              payload={payload}
              isError={isError}
            />
          )}
          {!cancelled && !payload && result !== undefined && (
            <ToolFallback.Result result={result} />
          )}
        </ToolFallback.Content>
      </ToolFallback.Root>
    )
  }
  GenericToolCard.displayName = 'GenericToolCard'
  return GenericToolCard
}

function ToolResultBody({
  projectId,
  toolName,
  payload,
  isError,
}: {
  projectId: string
  toolName: string
  payload: ToolResultPayload
  isError: boolean
}) {
  const [showData, setShowData] = useState(false)
  const openFile = usePreview((s) => s.openFile)
  const data = payload.data ?? {}
  const hasData =
    Object.keys(data).length > 0 && !(Object.keys(data).length === 1 && 'error' in data)
  const Body = isError ? undefined : TOOL_BODIES[toolName]
  return (
    <div className="flex flex-col gap-2 border-t px-3 py-2 text-sm">
      <p className={isError ? 'text-destructive' : ''}>{payload.summary}</p>
      {Body && <Body data={data} projectId={projectId} />}
      {payload.artifacts && payload.artifacts.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {payload.artifacts.map((artifact) => (
            <li key={artifact.file_id}>
              <button
                type="button"
                onClick={() => openFile(artifact.file_id)}
                className="bg-muted hover:bg-accent inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs"
              >
                <PaperclipIcon className="size-3" /> {artifact.name}
              </button>{' '}
              <a
                className="text-muted-foreground text-xs underline"
                href={fileDownloadUrl(projectId, artifact.file_id)}
              >
                download
              </a>
            </li>
          ))}
        </ul>
      )}
      {hasData && (
        <div>
          <button
            type="button"
            className="text-muted-foreground text-xs underline"
            onClick={() => setShowData((v) => !v)}
          >
            {showData ? 'Hide details' : 'Show details'}
          </button>
          {showData && (
            <pre className="bg-muted mt-1 max-h-72 overflow-auto rounded-md p-2 text-[11px] leading-snug whitespace-pre-wrap">
              {JSON.stringify(data, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
