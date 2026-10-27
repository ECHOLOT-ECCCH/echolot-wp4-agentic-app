import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { AssistantChatTransport, useChatRuntime } from '@assistant-ui/react-ai-sdk'
import { useQuery } from '@tanstack/react-query'
import { lastAssistantMessageIsCompleteWithApprovalResponses, type UIMessage } from 'ai'
import { PlusIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { Thread } from '@/components/assistant-ui/elements/thread.aui'
import { Button } from '@/components/ui/button'
import { makeGenericToolCard } from '@/features/chat/GenericToolCard'
import { ApprovalAwareToolGroup } from '@/features/chat/ToolGroup'
import { api } from '@/lib/api'
import { keys, useConversations, useCreateConversation } from '@/lib/queries'
import type { Workspace } from '@/lib/types'
import { isUiAction, usePreview } from '@/lib/uiActions'

export function ChatPane({ workspace }: { workspace: Workspace }) {
  const conversations = useConversations(workspace.id)
  const create = useCreateConversation(workspace.id)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const activeId = selectedId ?? conversations.data?.[0]?.id ?? null

  useEffect(() => {
    if (conversations.data && conversations.data.length === 0 && !create.isPending) {
      create.mutate(undefined, { onSuccess: (c) => setSelectedId(c.id) })
    }
    // only when the list first loads empty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations.data?.length])

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-2">
        <select
          className="bg-background h-7 min-w-0 flex-1 truncate rounded-md border px-1 text-xs"
          value={activeId ?? ''}
          onChange={(e) => setSelectedId(e.target.value)}
          aria-label="Conversation"
        >
          {conversations.data?.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="New conversation"
          disabled={create.isPending}
          onClick={() =>
            create.mutate(undefined, {
              onSuccess: (c) => setSelectedId(c.id),
              onError: (error) => toast.error(error.message),
            })
          }
        >
          <PlusIcon />
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        {activeId ? (
          <ConversationThread key={activeId} workspace={workspace} conversationId={activeId} />
        ) : (
          <p className="text-muted-foreground p-4 text-sm">Starting a conversation…</p>
        )}
      </div>
    </div>
  )
}

function ConversationThread({
  workspace,
  conversationId,
}: {
  workspace: Workspace
  conversationId: string
}) {
  const history = useQuery({
    queryKey: keys.messages(workspace.id, conversationId),
    queryFn: () =>
      api<UIMessage[]>(`/api/workspaces/${workspace.id}/conversations/${conversationId}/messages`),
    staleTime: Infinity,
  })
  if (history.isPending)
    return <p className="text-muted-foreground p-4 text-sm">Loading conversation…</p>
  if (history.isError)
    return <p className="text-destructive p-4 text-sm">{history.error.message}</p>
  return (
    <LiveThread
      workspace={workspace}
      conversationId={conversationId}
      initialMessages={history.data}
    />
  )
}

function LiveThread({
  workspace,
  conversationId,
  initialMessages,
}: {
  workspace: Workspace
  conversationId: string
  initialMessages: UIMessage[]
}) {
  const apply = usePreview((s) => s.apply)
  const transport = useMemo(
    () =>
      new AssistantChatTransport({
        api: `/api/workspaces/${workspace.id}/conversations/${conversationId}/chat`,
        credentials: 'same-origin',
        // what is open in the middle pane goes along, so "this row" means something to the model
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId, body }) => ({
          body: {
            ...(body as object | undefined),
            id,
            messages,
            trigger,
            messageId,
            viewing: usePreview.getState().target,
          },
        }),
      }),
    [workspace.id, conversationId],
  )
  const runtime = useChatRuntime({
    transport,
    messages: initialMessages,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onData: (part) => {
      if (part.type === 'data-ui-action' && isUiAction(part.data)) apply(part.data)
    },
    onError: (error) => toast.error(error.message),
  })
  const components = useMemo(
    () => ({
      ToolFallback: makeGenericToolCard(workspace.id),
      ToolGroup: ApprovalAwareToolGroup,
      Welcome: makeWelcome(workspace),
    }),
    [workspace],
  )
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="relative h-full">
        <Thread components={components} />
      </div>
    </AssistantRuntimeProvider>
  )
}

function makeWelcome(workspace: Workspace) {
  const Welcome = () => <WelcomeBody workspace={workspace} />
  Welcome.displayName = 'Welcome'
  return Welcome
}

function WelcomeBody({ workspace }: { workspace: Workspace }) {
  return (
    <div className="mx-auto flex max-w-md flex-col gap-3 px-4 py-8 text-center">
      <h2 className="text-lg font-semibold">Let's work on {workspace.name}</h2>
      <p className="text-muted-foreground text-sm">
        Upload a dataset and tell me what should happen to each record: link it to Wikidata, add
        information from a service, clean a column. I will look at the data first, agree the details
        with you, then write and run the script. Runs always wait for your approval.
      </p>
    </div>
  )
}
