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
import { ModelPicker } from '@/features/project/ModelPicker'
import { api, csrfToken } from '@/lib/api'
import { keys, useConversations, useCreateConversation } from '@/lib/queries'
import type { Project } from '@/lib/types'
import { isUiAction, usePreview } from '@/lib/uiActions'

export function ChatPane({ project }: { project: Project }) {
  const conversations = useConversations(project.id)
  const create = useCreateConversation(project.id)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const canChat = project.my_role !== 'viewer'

  const activeId = selectedId ?? conversations.data?.[0]?.id ?? null

  useEffect(() => {
    if (conversations.data && conversations.data.length === 0 && canChat && !create.isPending) {
      create.mutate(undefined, { onSuccess: (c) => setSelectedId(c.id) })
    }
    // only when the list first loads empty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations.data?.length, canChat])

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
          disabled={!canChat || create.isPending}
          onClick={() =>
            create.mutate(undefined, {
              onSuccess: (c) => setSelectedId(c.id),
              onError: (error) => toast.error(error.message),
            })
          }
        >
          <PlusIcon />
        </Button>
        <ModelPicker project={project} />
      </div>
      <div className="min-h-0 flex-1">
        {activeId ? (
          <ConversationThread key={activeId} project={project} conversationId={activeId} />
        ) : (
          <p className="text-muted-foreground p-4 text-sm">
            {canChat
              ? 'Starting a conversation…'
              : 'Viewers can read conversations once they exist.'}
          </p>
        )}
      </div>
    </div>
  )
}

function ConversationThread({
  project,
  conversationId,
}: {
  project: Project
  conversationId: string
}) {
  const history = useQuery({
    queryKey: keys.messages(project.id, conversationId),
    queryFn: () =>
      api<UIMessage[]>(`/api/projects/${project.id}/conversations/${conversationId}/messages`),
    staleTime: Infinity,
  })
  if (history.isPending)
    return <p className="text-muted-foreground p-4 text-sm">Loading conversation…</p>
  if (history.isError)
    return <p className="text-destructive p-4 text-sm">{history.error.message}</p>
  return (
    <LiveThread project={project} conversationId={conversationId} initialMessages={history.data} />
  )
}

function LiveThread({
  project,
  conversationId,
  initialMessages,
}: {
  project: Project
  conversationId: string
  initialMessages: UIMessage[]
}) {
  const apply = usePreview((s) => s.apply)
  const noModel = !project.default_model
  const transport = useMemo(
    () =>
      new AssistantChatTransport({
        api: `/api/projects/${project.id}/conversations/${conversationId}/chat`,
        credentials: 'same-origin',
        headers: () => ({ 'X-CSRF-Token': csrfToken() }),
      }),
    [project.id, conversationId],
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
      ToolFallback: makeGenericToolCard(project.id),
      Welcome: makeWelcome(project, noModel),
    }),
    [project, noModel],
  )
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="relative h-full">
        <Thread components={components} />
      </div>
    </AssistantRuntimeProvider>
  )
}

function makeWelcome(project: Project, noModel: boolean) {
  const Welcome = () => <WelcomeBody project={project} noModel={noModel} />
  Welcome.displayName = 'Welcome'
  return Welcome
}

function WelcomeBody({ project, noModel }: { project: Project; noModel: boolean }) {
  return (
    <div className="mx-auto flex max-w-md flex-col gap-3 px-4 py-8 text-center">
      <h2 className="text-lg font-semibold">Let's convert {project.name}</h2>
      <p className="text-muted-foreground text-sm">
        I am your conversion engineer. Tell me what this dictionary looks like, or ask me to have a
        look at the files you uploaded. I will show what I find in the middle pane and ask before
        changing anything.
      </p>
      {noModel && (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          Choose a model first (button above) so I know which model and key to use.
        </p>
      )}
    </div>
  )
}
