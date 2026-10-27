import {
  keepPreviousData,
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { api } from '@/lib/api'
import type {
  Conversation,
  FileRecord,
  RecordsPage,
  TaskNotes,
  UiAction,
  Workspace,
} from '@/lib/types'

export const keys = {
  workspaces: ['workspaces'] as const,
  workspace: (id: string) => ['workspaces', id] as const,
  files: (workspaceId: string) => ['workspaces', workspaceId, 'files'] as const,
  records: (workspaceId: string, fileId: string, offset: number, limit: number) =>
    ['workspaces', workspaceId, 'files', fileId, 'records', { offset, limit }] as const,
  task: (workspaceId: string) => ['workspaces', workspaceId, 'task'] as const,
  conversations: (workspaceId: string) => ['workspaces', workspaceId, 'conversations'] as const,
  messages: (workspaceId: string, conversationId: string) =>
    ['workspaces', workspaceId, 'conversations', conversationId, 'messages'] as const,
}

const base = (workspaceId: string) => `/api/workspaces/${workspaceId}`

export function useWorkspaces() {
  return useQuery({ queryKey: keys.workspaces, queryFn: () => api<Workspace[]>('/api/workspaces') })
}

export function useWorkspace(workspaceId: string) {
  return useQuery({
    queryKey: keys.workspace(workspaceId),
    queryFn: () => api<Workspace>(base(workspaceId)),
  })
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) =>
      api<Workspace>('/api/workspaces', { method: 'POST', json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.workspaces }),
  })
}

export function useDeleteWorkspace() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (workspaceId: string) => api<void>(base(workspaceId), { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.workspaces }),
  })
}

export function useFiles(workspaceId: string) {
  return useQuery({
    queryKey: keys.files(workspaceId),
    queryFn: () => api<FileRecord[]>(`${base(workspaceId)}/files`),
  })
}

export function useDeleteFile(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fileId: string) =>
      api<void>(`${base(workspaceId)}/files/${fileId}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.files(workspaceId) }),
  })
}

export function useRecords(workspaceId: string, fileId: string, offset: number, limit: number) {
  return useQuery({
    queryKey: keys.records(workspaceId, fileId, offset, limit),
    queryFn: () =>
      api<RecordsPage>(
        `${base(workspaceId)}/files/${fileId}/records?offset=${offset}&limit=${limit}`,
      ),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}

export function useTask(workspaceId: string) {
  return useQuery({
    queryKey: keys.task(workspaceId),
    queryFn: () => api<TaskNotes>(`${base(workspaceId)}/task`),
  })
}

export function useSaveTask(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (markdown: string) =>
      api<TaskNotes>(`${base(workspaceId)}/task`, { method: 'PUT', json: { markdown } }),
    onSuccess: (task) => queryClient.setQueryData(keys.task(workspaceId), task),
  })
}

export function useConversations(workspaceId: string) {
  return useQuery({
    queryKey: keys.conversations(workspaceId),
    queryFn: () => api<Conversation[]>(`${base(workspaceId)}/conversations`),
  })
}

export function useCreateConversation(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      api<Conversation>(`${base(workspaceId)}/conversations`, { method: 'POST', json: {} }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.conversations(workspaceId) }),
  })
}

/** Refetch what a tool's ui-action points at: the tool may just have written it. */
export function refreshAfterAction(
  queryClient: QueryClient,
  workspaceId: string,
  action: UiAction,
) {
  if (action.type === 'open_task') {
    void queryClient.invalidateQueries({ queryKey: keys.task(workspaceId) })
  } else if (action.type === 'open_file') {
    void queryClient.invalidateQueries({ queryKey: keys.files(workspaceId) })
  }
}

/** Refetch what an agent turn can change: the files and the task notes. */
export function refreshAfterTurn(queryClient: QueryClient, workspaceId: string) {
  void queryClient.invalidateQueries({ queryKey: keys.files(workspaceId), exact: true })
  void queryClient.invalidateQueries({ queryKey: keys.task(workspaceId) })
}
