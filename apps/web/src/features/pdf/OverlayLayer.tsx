import type { PageViewport } from 'pdfjs-dist'
import { useRef, useState } from 'react'

import { type Box, boxToViewport } from '@/features/pdf/geometry'
import type { AnnotationRecord, AnnotationStatus } from '@/lib/types'

const COLORS: Record<AnnotationStatus, { stroke: string; fill: string }> = {
  proposed: { stroke: '#2563eb', fill: 'rgba(37, 99, 235, 0.08)' },
  accepted: { stroke: '#16a34a', fill: 'rgba(22, 163, 74, 0.10)' },
  rejected: { stroke: '#dc2626', fill: 'rgba(220, 38, 38, 0.06)' },
  edited: { stroke: '#7c3aed', fill: 'rgba(124, 58, 237, 0.10)' },
}
const USER_COLOR = { stroke: '#0d9488', fill: 'rgba(13, 148, 136, 0.10)' }
const HANDLE = 8
const MIN_DRAW = 6

export type EditTool = 'select' | 'draw' | 'split' | 'context'

type Handle = 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w'
type Drag =
  | { kind: 'draw'; start: [number, number]; current: [number, number] }
  | { kind: 'move'; id: string; origin: Box; start: [number, number]; current: [number, number] }
  | {
      kind: 'resize'
      id: string
      origin: Box
      handle: Handle
      start: [number, number]
      current: [number, number]
    }

export interface OverlayEdits {
  onCreate: (bbox: Box) => void
  onChangeBox: (id: string, bbox: Box) => void
  onSplit: (id: string, y: number) => void
  /** A region dragged with the "select for chat" tool. */
  onRegion?: (bbox: Box) => void
}

export function OverlayLayer({
  annotations,
  pageHeight,
  scale,
  viewport,
  selectedId,
  showRejected,
  onSelect,
  tool = 'select',
  editable = false,
  edits,
}: {
  annotations: AnnotationRecord[]
  pageHeight: number
  scale: number
  viewport?: PageViewport | null
  selectedId: string | null
  showRejected: boolean
  onSelect: (id: string) => void
  tool?: EditTool
  editable?: boolean
  edits?: OverlayEdits
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<Drag | null>(null)

  // the editing gestures assume an unrotated page: px = points * scale
  const toPoints = (event: React.PointerEvent): [number, number] => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return [0, 0]
    return [(event.clientX - rect.left) / scale, (event.clientY - rect.top) / scale]
  }
  const normalize = (a: [number, number], b: [number, number]): Box => [
    Math.max(0, Math.min(a[0], b[0])),
    Math.max(0, Math.min(a[1], b[1])),
    Math.max(a[0], b[0]),
    Math.max(a[1], b[1]),
  ]
  const applyDrag = (d: Drag): Box | null => {
    if (d.kind === 'draw') return normalize(d.start, d.current)
    const dx = d.current[0] - d.start[0]
    const dy = d.current[1] - d.start[1]
    const [x0, top, x1, bottom] = d.origin
    if (d.kind === 'move') return [x0 + dx, top + dy, x1 + dx, bottom + dy]
    const h = d.handle
    const nx0 = h.includes('w') ? x0 + dx : x0
    const nx1 = h.includes('e') ? x1 + dx : x1
    const ntop = h.includes('n') ? top + dy : top
    const nbottom = h.includes('s') ? bottom + dy : bottom
    return normalize([nx0, ntop], [nx1, nbottom])
  }

  const onPointerDown = (event: React.PointerEvent) => {
    if (!editable || event.button !== 0) return
    const point = toPoints(event)
    if (tool === 'draw' || tool === 'context') {
      event.preventDefault()
      svgRef.current?.setPointerCapture(event.pointerId)
      setDrag({ kind: 'draw', start: point, current: point })
    }
  }
  const startMove = (event: React.PointerEvent, annotation: AnnotationRecord) => {
    if (!editable || tool !== 'select' || event.button !== 0) return
    event.stopPropagation()
    event.preventDefault()
    onSelect(annotation.id)
    svgRef.current?.setPointerCapture(event.pointerId)
    const point = toPoints(event)
    setDrag({
      kind: 'move',
      id: annotation.id,
      origin: annotation.bbox,
      start: point,
      current: point,
    })
  }
  const startResize = (event: React.PointerEvent, annotation: AnnotationRecord, handle: Handle) => {
    event.stopPropagation()
    event.preventDefault()
    svgRef.current?.setPointerCapture(event.pointerId)
    const point = toPoints(event)
    setDrag({
      kind: 'resize',
      id: annotation.id,
      origin: annotation.bbox,
      handle,
      start: point,
      current: point,
    })
  }
  const onPointerMove = (event: React.PointerEvent) => {
    if (!drag) return
    const point = toPoints(event)
    setDrag({ ...drag, current: point })
  }
  const onPointerUp = () => {
    if (!drag) return
    const box = applyDrag(drag)
    setDrag(null)
    if (!box || !edits) return
    const moved =
      Math.abs(drag.current[0] - drag.start[0]) + Math.abs(drag.current[1] - drag.start[1])
    if (drag.kind === 'draw') {
      if (box[2] - box[0] >= MIN_DRAW && box[3] - box[1] >= MIN_DRAW) {
        if (tool === 'context') edits.onRegion?.(box)
        else edits.onCreate(box)
      }
      return
    }
    if (moved < 1) return // a click on the box: selection only
    edits.onChangeBox(drag.id, box.map((v) => Math.round(v * 100) / 100) as Box)
  }

  const preview = drag ? applyDrag(drag) : null

  return (
    <svg
      ref={svgRef}
      className="absolute inset-0 h-full w-full"
      data-testid="overlay-layer"
      style={{
        pointerEvents: editable && (tool === 'draw' || tool === 'context') ? 'all' : 'none',
        cursor: tool === 'draw' || tool === 'context' ? 'crosshair' : undefined,
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={() => setDrag(null)}
    >
      {annotations.map((annotation) => {
        if (annotation.status === 'rejected' && !showRejected) return null
        const dragging = drag && drag.kind !== 'draw' && drag.id === annotation.id
        const boxes: Box[] =
          dragging && preview
            ? [preview]
            : annotation.payload.bboxes?.length
              ? annotation.payload.bboxes
              : [annotation.bbox]
        const color = annotation.source === 'user' ? USER_COLOR : COLORS[annotation.status]
        const selected = annotation.id === selectedId
        const union = boxToViewport(
          dragging && preview ? preview : annotation.bbox,
          pageHeight,
          scale,
          viewport,
        )
        return (
          <g key={annotation.id} data-status={annotation.status} data-annotation={annotation.id}>
            {boxes.map((box, i) => {
              const r = boxToViewport(box, pageHeight, scale, viewport)
              return (
                <rect
                  key={i}
                  x={r.left}
                  y={r.top}
                  width={r.width}
                  height={r.height}
                  fill={color.fill}
                  stroke={color.stroke}
                  strokeWidth={selected ? 3 : 1.25}
                  strokeDasharray={annotation.status === 'rejected' ? '4 3' : undefined}
                  style={{
                    pointerEvents: tool === 'draw' || tool === 'context' ? 'none' : 'all',
                    cursor:
                      editable && tool === 'select'
                        ? 'move'
                        : tool === 'split'
                          ? 'row-resize'
                          : 'pointer',
                  }}
                  onPointerDown={(event) => {
                    if (tool === 'split' && editable && edits) {
                      event.stopPropagation()
                      onSelect(annotation.id)
                      edits.onSplit(annotation.id, toPoints(event)[1])
                      return
                    }
                    startMove(event, annotation)
                  }}
                  onClick={(event) => {
                    event.stopPropagation()
                    if (!editable || tool !== 'select') onSelect(annotation.id)
                  }}
                >
                  <title>
                    {annotation.payload.headword_guess ?? annotation.label} · {annotation.status}
                    {annotation.payload.confidence !== undefined
                      ? ` · ${Math.round(annotation.payload.confidence * 100)}%`
                      : ''}
                  </title>
                </rect>
              )
            })}
            {selected && annotation.payload.headword_guess && (
              <text
                x={union.left}
                y={Math.max(10, union.top - 3)}
                fontSize={11}
                fill={color.stroke}
                style={{ pointerEvents: 'none', fontFamily: 'ui-sans-serif, system-ui' }}
              >
                {annotation.payload.headword_guess}
              </text>
            )}
            {selected && editable && tool === 'select' && (
              <g data-testid="resize-handles">
                {(
                  [
                    ['nw', union.left, union.top],
                    ['ne', union.left + union.width, union.top],
                    ['sw', union.left, union.top + union.height],
                    ['se', union.left + union.width, union.top + union.height],
                    ['n', union.left + union.width / 2, union.top],
                    ['s', union.left + union.width / 2, union.top + union.height],
                    ['w', union.left, union.top + union.height / 2],
                    ['e', union.left + union.width, union.top + union.height / 2],
                  ] as [Handle, number, number][]
                ).map(([handle, cx, cy]) => (
                  <rect
                    key={handle}
                    x={cx - HANDLE / 2}
                    y={cy - HANDLE / 2}
                    width={HANDLE}
                    height={HANDLE}
                    fill="white"
                    stroke={color.stroke}
                    strokeWidth={1.5}
                    style={{ pointerEvents: 'all', cursor: `${handle}-resize` }}
                    onPointerDown={(event) => startResize(event, annotation, handle)}
                  />
                ))}
              </g>
            )}
          </g>
        )
      })}
      {drag?.kind === 'draw' && preview && (
        <rect
          {...(() => {
            const r = boxToViewport(preview, pageHeight, scale, viewport)
            return { x: r.left, y: r.top, width: r.width, height: r.height }
          })()}
          fill="rgba(13, 148, 136, 0.12)"
          stroke="#0d9488"
          strokeDasharray="5 3"
          strokeWidth={1.5}
          style={{ pointerEvents: 'none' }}
        />
      )}
    </svg>
  )
}
