import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { usePreview } from '@/lib/uiActions'

/** A crash while rendering the middle pane must not take the workspace down: the pane shows
 *  the error and a way out (close the preview); the files and the chat stay usable. Opening
 *  another target (a new store revision) also leaves the error behind. */
export function PreviewErrorBoundary({ children }: { children: ReactNode }) {
  const revision = usePreview((s) => s.revision)
  return <Boundary resetKey={revision}>{children}</Boundary>
}

interface BoundaryProps {
  resetKey: number
  children: ReactNode
}

interface BoundaryState {
  error: Error | null
  resetKey: number
}

class Boundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, resetKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  // a new preview target (a new store revision) leaves the error behind
  static getDerivedStateFromProps(props: BoundaryProps, state: BoundaryState) {
    if (props.resetKey !== state.resetKey) return { error: null, resetKey: props.resetKey }
    return null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('preview crashed', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div
        className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center"
        role="alert"
      >
        <p className="font-medium">This view could not be shown</p>
        <p className="text-muted-foreground max-w-md text-sm break-words">
          {this.state.error.message}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            usePreview.getState().clear()
            this.setState({ error: null })
          }}
        >
          Close preview
        </Button>
      </div>
    )
  }
}
