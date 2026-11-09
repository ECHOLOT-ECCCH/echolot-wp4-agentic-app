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
  ModelChoice,
  ModelListing,
  ProbeResult,
  User,
  UserWithPassword,
  Vendor,
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
  me: ['me'] as const,
  users: ['admin', 'users'] as const,
  vendors: ['vendors'] as const,
  vendorModels: (provider: string) => ['vendors', provider, 'models'] as const,
  modelChoice: ['me', 'model'] as const,
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

/** A run that finished stored its result file; the file list should show it. */
function refreshFilesWhenFinished(
  queryClient: QueryClient,
  workspaceId: string,
  before: RunRecord[],
  after: RunRecord[],
) {
  const wasRunning = new Set(before.filter((r) => !isTerminal(r.status)).map((r) => r.id))
  if (after.some((r) => wasRunning.has(r.id) && isTerminal(r.status))) {
    void queryClient.invalidateQueries({ queryKey: keys.files(workspaceId), exact: true })
  }
}

export function useRuns(workspaceId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.runs(workspaceId),
    queryFn: async () => {
      const runs = await api<RunRecord[]>(`${base(workspaceId)}/runs`)
      const before = queryClient.getQueryData<RunRecord[]>(keys.runs(workspaceId)) ?? []
      refreshFilesWhenFinished(queryClient, workspaceId, before, runs)
      return runs
    },
    // follow unfinished runs; nothing to poll once all are finished
    refetchInterval: (query) =>
      query.state.data?.some((r) => !isTerminal(r.status)) ? RUN_REFRESH_MS : false,
  })
}

export function useRun(workspaceId: string, runId: string) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.run(workspaceId, runId),
    queryFn: async () => {
      const run = await api<RunRecord>(`${base(workspaceId)}/runs/${runId}`)
      const before = queryClient.getQueryData<RunRecord>(keys.run(workspaceId, runId))
      refreshFilesWhenFinished(queryClient, workspaceId, before ? [before] : [], [run])
      return run
    },
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

// --- accounts (docs/PLAN.md 7.11) --------------------------------------------------------------

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => api<User>('/api/me'),
    retry: false,
    staleTime: 5 * 60_000,
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api<User>('/api/auth/login', { method: 'POST', json: body }),
    onSuccess: (user) => {
      // whatever was cached belonged to the session before
      queryClient.clear()
      queryClient.setQueryData(keys.me, user)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api<void>('/api/auth/logout', { method: 'POST' }),
    onSettled: () => queryClient.clear(),
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api<void>('/api/me/password', { method: 'POST', json: body }),
  })
}

export function useUsers(enabled: boolean) {
  return useQuery({
    queryKey: keys.users,
    queryFn: () => api<User[]>('/api/admin/users'),
    enabled,
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; name: string; is_admin: boolean }) =>
      api<UserWithPassword>('/api/admin/users', { method: 'POST', json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.users }),
  })
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; is_admin?: boolean; is_active?: boolean }) =>
      api<User>(`/api/admin/users/${id}`, { method: 'PATCH', json: body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.users }),
  })
}

export function useResetPassword() {
  return useMutation({
    mutationFn: (userId: string) =>
      api<UserWithPassword>(`/api/admin/users/${userId}/reset-password`, { method: 'POST' }),
  })
}

// --- model vendors, keys and the model choice (docs/PLAN.md 7.11) ------------------------------

export function useVendors() {
  return useQuery({ queryKey: keys.vendors, queryFn: () => api<Vendor[]>('/api/vendors') })
}

function afterKeyChange(queryClient: QueryClient, provider: string) {
  void queryClient.invalidateQueries({ queryKey: keys.vendors })
  void queryClient.invalidateQueries({ queryKey: keys.modelChoice })
  queryClient.removeQueries({ queryKey: keys.vendorModels(provider) })
}

export function useSaveKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      provider,
      ...body
    }: {
      provider: string
      api_key?: string
      base_url?: string
    }) => api<Vendor>(`/api/me/keys/${provider}`, { method: 'PUT', json: body }),
    onSuccess: (_vendor, { provider }) => afterKeyChange(queryClient, provider),
  })
}

export function useDeleteKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (provider: string) => api<void>(`/api/me/keys/${provider}`, { method: 'DELETE' }),
    onSuccess: (_void, provider) => afterKeyChange(queryClient, provider),
  })
}

export function useTestKey() {
  return useMutation({
    mutationFn: ({ provider, model_id }: { provider: string; model_id?: string }) =>
      api<ProbeResult>(`/api/me/keys/${provider}/test`, { method: 'POST', json: { model_id } }),
  })
}

export function useVendorModels(provider: string | null) {
  return useQuery({
    queryKey: keys.vendorModels(provider ?? ''),
    queryFn: () => api<ModelListing>(`/api/vendors/${provider}/models`),
    enabled: Boolean(provider),
    staleTime: 10 * 60_000,
  })
}

export function useModelChoice() {
  return useQuery({ queryKey: keys.modelChoice, queryFn: () => api<ModelChoice>('/api/me/model') })
}

export function useSetModelChoice() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { provider: string | null; model_id?: string }) =>
      api<ModelChoice>('/api/me/model', { method: 'PUT', json: body }),
    onSuccess: (choice) => queryClient.setQueryData(keys.modelChoice, choice),
  })
}
