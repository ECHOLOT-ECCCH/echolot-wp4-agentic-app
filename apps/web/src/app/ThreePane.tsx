import { PanelLeftIcon, PanelRightIcon } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'

const STORAGE_KEY = 'elexifier.panes.v1'

type Layout = Record<string, number>

const DEFAULT_LAYOUT: Layout = { left: 20, middle: 45, right: 35 }

function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULT_LAYOUT, ...(JSON.parse(raw) as Layout) }
  } catch {
    /* ignore */
  }
  return DEFAULT_LAYOUT
}

export interface ThreePaneProps {
  header: ReactNode
  left: ReactNode
  middle: ReactNode
  right: ReactNode
}

export function ThreePane({ header, left, middle, right }: ThreePaneProps) {
  const [layout] = useState(loadLayout)
  const [leftOpen, setLeftOpen] = useState(true)
  const [rightOpen, setRightOpen] = useState(true)
  const saveTimer = useRef<number | null>(null)

  const persist = useCallback((next: Layout) => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...loadLayout(), ...next }))
      } catch {
        /* ignore */
      }
    }, 300)
  }, [])

  useEffect(
    () => () => {
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
    },
    [],
  )

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-2">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={leftOpen ? 'Hide project pane' : 'Show project pane'}
          onClick={() => setLeftOpen((v) => !v)}
        >
          <PanelLeftIcon />
        </Button>
        <div className="min-w-0 flex-1">{header}</div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={rightOpen ? 'Hide chat pane' : 'Show chat pane'}
          onClick={() => setRightOpen((v) => !v)}
        >
          <PanelRightIcon />
        </Button>
      </div>
      <ResizablePanelGroup
        orientation="horizontal"
        className="min-h-0 flex-1"
        defaultLayout={layout}
        onLayoutChange={persist}
      >
        {leftOpen && (
          <>
            <ResizablePanel
              id="left"
              defaultSize={`${layout.left ?? 20}`}
              minSize="12"
              className="min-w-0"
            >
              {left}
            </ResizablePanel>
            <ResizableHandle withHandle />
          </>
        )}
        <ResizablePanel
          id="middle"
          defaultSize={`${layout.middle ?? 45}`}
          minSize="25"
          className="min-w-0"
        >
          {middle}
        </ResizablePanel>
        {rightOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel
              id="right"
              defaultSize={`${layout.right ?? 35}`}
              minSize="20"
              className="min-w-0"
            >
              {right}
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
    </div>
  )
}
