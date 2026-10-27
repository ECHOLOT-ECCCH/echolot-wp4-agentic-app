import {
  keepPreviousData,
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { api } from '@/lib/api'
import { isTerminal } from '@/lib/types'
import type {
  Conversation,
  FileRecord,
  RecordsPage,
  RunRecord,
  RunRowsPage,
  ScriptRecord,
  ScriptSummary,
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
  scripts: (workspaceId: string) => ['workspaces', workspaceId, 'scripts'] as const,
  script: (workspaceId: string, version: number | 'latest') =>
    ['workspaces', workspaceId, 'scripts', version] as const,
  runs: (workspaceId: string) => ['workspaces', workspaceId, 'runs'] as const,
  run: (workspaceId: string, runId: string) => ['workspaces', workspaceId, 'runs', runId] as const,
  runRows: (workspaceId: string, runId: string, params: { params: string; runStatus: string }) =>
    ['workspaces', workspaceId, 'runs', runId, 'rows', params] as const,
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

export function useScripts(workspaceId: string) {
  return useQuery({
    queryKey: keys.scripts(workspaceId),
    queryFn: () => api<ScriptSummary[]>(`${base(workspaceId)}/scripts`),
  })
}

export function useScript(workspaceId: string, version: number | null) {
  return useQuery({
    queryKey: keys.script(workspaceId, version ?? 0),
    queryFn: () => api<ScriptRecord>(`${base(workspaceId)}/scripts/${version}`),
    enabled: version !== null,
    staleTime: Infinity, // a version never changes
  })
}

export function useSaveScript(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { code: string; note?: string }) =>
      api<ScriptRecord>(`${base(workspaceId)}/scripts`, { method: 'POST', json: body }),
    onSuccess: (script) => {
      queryClient.setQueryData(keys.script(workspaceId, script.version), script)
      void queryClient.invalidateQueries({ queryKey: keys.scripts(workspaceId), exact: true })
    },
  })
}

const RUN_REFRESH_MS = 2000

export function useRuns(workspaceId: string) {
  return useQuery({
    queryKey: keys.runs(workspaceId),
    queryFn: () => api<RunRecord[]>(`${base(workspaceId)}/runs`),
    // follow unfinished runs; nothing to poll once all are finished
    refetchInterval: (query) =>
      query.state.data?.some((r) => !isTerminal(r.status)) ? RUN_REFRESH_MS : false,
  })
}

export function useRun(workspaceId: string, runId: string) {
  return useQuery({
    queryKey: keys.run(workspaceId, runId),
    queryFn: () => api<RunRecord>(`${base(workspaceId)}/runs/${runId}`),
    refetchInterval: (query) =>
      query.state.data && isTerminal(query.state.data.status) ? false : RUN_REFRESH_MS,
  })
}

export interface RowsFilter {
  status?: 'ok' | 'error'
  field?: string
  equals?: string
  decided?: boolean
}

export function useRunRows(
  workspaceId: string,
  run: RunRecord | undefined,
  offset: number,
  limit: number,
  filter: RowsFilter = {},
) {
  const runId = run?.id ?? ''
  const runStatus = run?.status ?? 'queued'
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (filter.status) query.set('status', filter.status)
  if (filter.field) query.set('field', filter.field)
  if (filter.equals !== undefined) query.set('equals', filter.equals)
  if (filter.decided !== undefined) query.set('decided', String(filter.decided))
  const params = query.toString()
  return useQuery({
    // the run's status is in the key: the rows appear when the run is done
    queryKey: keys.runRows(workspaceId, runId, { params, runStatus }),
    queryFn: () => api<RunRowsPage>(`${base(workspaceId)}/runs/${runId}/rows?${params}`),
    enabled: Boolean(run?.result_file_id),
    placeholderData: keepPreviousData,
  })
}

/** Set (a QID, or null for "none of these") or clear (undefined) a row's decision. */
export function useDecide(workspaceId: string, runId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ row, qid }: { row: number; qid: string | null | undefined }) =>
      qid === undefined
        ? api<void>(`${base(workspaceId)}/runs/${runId}/rows/${row}/decision`, {
            method: 'DELETE',
          })
        : api<unknown>(`${base(workspaceId)}/runs/${runId}/rows/${row}/decision`, {
            method: 'PUT',
            json: { qid },
          }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: [...keys.run(workspaceId, runId), 'rows'] }),
  })
}

export interface RunStart {
  file_id: string
  script_version?: number
  limit?: number
  purpose?: string
}

function useRunMutation<T>(workspaceId: string, request: (arg: T) => Promise<RunRecord>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: request,
    onSuccess: (run) => {
      queryClient.setQueryData(keys.run(workspaceId, run.id), run)
      void queryClient.invalidateQueries({ queryKey: keys.runs(workspaceId), exact: true })
      void queryClient.invalidateQueries({ queryKey: keys.files(workspaceId), exact: true })
    },
  })
}

export function useStartRun(workspaceId: string) {
  return useRunMutation(workspaceId, (body: RunStart) =>
    api<RunRecord>(`${base(workspaceId)}/runs`, { method: 'POST', json: body }),
  )
}

export function useCancelRun(workspaceId: string) {
  return useRunMutation(workspaceId, (runId: string) =>
    api<RunRecord>(`${base(workspaceId)}/runs/${runId}/cancel`, { method: 'POST' }),
  )
}

export function useRetryFailed(workspaceId: string) {
  return useRunMutation(workspaceId, (runId: string) =>
    api<RunRecord>(`${base(workspaceId)}/runs/${runId}/retry-failed`, { method: 'POST' }),
  )
}

export function runExportUrl(workspaceId: string, runId: string, format: 'csv' | 'jsonl') {
  return `${base(workspaceId)}/runs/${runId}/export?format=${format}`
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
  } else if (action.type === 'open_script') {
    void queryClient.invalidateQueries({ queryKey: keys.scripts(workspaceId), exact: true })
  } else if (action.type === 'open_run') {
    void queryClient.invalidateQueries({ queryKey: keys.runs(workspaceId) })
  }
}

/** Refetch what an agent turn can change: files, task notes, script versions, runs. */
export function refreshAfterTurn(queryClient: QueryClient, workspaceId: string) {
  void queryClient.invalidateQueries({ queryKey: keys.files(workspaceId), exact: true })
  void queryClient.invalidateQueries({ queryKey: keys.task(workspaceId) })
  void queryClient.invalidateQueries({ queryKey: keys.scripts(workspaceId), exact: true })
  void queryClient.invalidateQueries({ queryKey: keys.runs(workspaceId), exact: true })
}
