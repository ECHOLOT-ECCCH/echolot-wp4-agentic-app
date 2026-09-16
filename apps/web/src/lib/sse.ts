import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { keys } from '@/lib/queries'
import { isUiAction, usePreview } from '@/lib/uiActions'

export interface ProjectEvent {
  project_id: string
  type: string
  data: Record<string, unknown>
  ts: string
}

const EVENT_TYPES = [
  'job.progress',
  'job.finished',
  'run.finished',
  'document.ready',
  'document.error',
  'annotations.changed',
  'ui_action',
] as const

/** One EventSource per open project; refreshes queries and applies ui-actions. */
export function useProjectEvents(projectId: string, onEvent?: (event: ProjectEvent) => void) {
  const queryClient = useQueryClient()
  const apply = usePreview((s) => s.apply)

  useEffect(() => {
    const source = new EventSource(`/api/projects/${projectId}/events`)
    const handler = (message: MessageEvent<string>) => {
      let event: ProjectEvent
      try {
        event = JSON.parse(message.data) as ProjectEvent
      } catch {
        return
      }
      switch (event.type) {
        case 'document.ready':
        case 'document.error':
          void queryClient.invalidateQueries({ queryKey: keys.documents(projectId) })
          break
        case 'job.finished':
        case 'run.finished':
          void queryClient.invalidateQueries({ queryKey: keys.files(projectId) })
          void queryClient.invalidateQueries({ queryKey: keys.documents(projectId) })
          void queryClient.invalidateQueries({ queryKey: keys.runs(projectId) })
          void queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'entries'] })
          break
        case 'job.progress':
          if (event.data.kind === 'documents.parse') {
            void queryClient.invalidateQueries({ queryKey: keys.documents(projectId) })
          } else if (event.data.kind === 'conversions.run') {
            void queryClient.invalidateQueries({ queryKey: keys.runs(projectId) })
          }
          break
        case 'steps.changed':
          void queryClient.invalidateQueries({ queryKey: keys.steps(projectId) })
          break
        case 'annotations.changed':
          void queryClient.invalidateQueries({ queryKey: keys.documents(projectId) })
          if (typeof event.data.document_id === 'string') {
            void queryClient.invalidateQueries({
              queryKey: keys.annotations(projectId, event.data.document_id),
            })
            void queryClient.invalidateQueries({
              queryKey: keys.document(projectId, event.data.document_id),
            })
          }
          break
        case 'ui_action':
          if (isUiAction(event.data)) apply(event.data)
          break
        default:
          break
      }
      onEvent?.(event)
    }
    for (const type of EVENT_TYPES) source.addEventListener(type, handler as EventListener)
    return () => source.close()
  }, [projectId, queryClient, apply, onEvent])
}
