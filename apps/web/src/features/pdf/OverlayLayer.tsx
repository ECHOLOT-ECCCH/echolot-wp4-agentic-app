import type { PageViewport } from 'pdfjs-dist'

import { type Box, boxToViewport } from '@/features/pdf/geometry'
import type { AnnotationRecord, AnnotationStatus } from '@/lib/types'

const COLORS: Record<AnnotationStatus, { stroke: string; fill: string }> = {
  proposed: { stroke: '#2563eb', fill: 'rgba(37, 99, 235, 0.08)' },
  accepted: { stroke: '#16a34a', fill: 'rgba(22, 163, 74, 0.10)' },
  rejected: { stroke: '#dc2626', fill: 'rgba(220, 38, 38, 0.06)' },
  edited: { stroke: '#7c3aed', fill: 'rgba(124, 58, 237, 0.10)' },
}

export function OverlayLayer({
  annotations,
  pageHeight,
  scale,
  viewport,
  selectedId,
  showRejected,
  onSelect,
}: {
  annotations: AnnotationRecord[]
  pageHeight: number
  scale: number
  viewport?: PageViewport | null
  selectedId: string | null
  showRejected: boolean
  onSelect: (id: string) => void
}) {
  return (
    <svg
      className="absolute inset-0 h-full w-full"
      data-testid="overlay-layer"
      style={{ pointerEvents: 'none' }}
    >
      {annotations.map((annotation) => {
        if (annotation.status === 'rejected' && !showRejected) return null
        const boxes: Box[] = annotation.payload.bboxes?.length
          ? annotation.payload.bboxes
          : [annotation.bbox]
        const color =
          annotation.source === 'user'
            ? { stroke: '#0d9488', fill: 'rgba(13, 148, 136, 0.10)' }
            : COLORS[annotation.status]
        const selected = annotation.id === selectedId
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
                  style={{ pointerEvents: 'all', cursor: 'pointer' }}
                  onClick={(event) => {
                    event.stopPropagation()
                    onSelect(annotation.id)
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
                x={boxToViewport(boxes[0]!, pageHeight, scale, viewport).left}
                y={Math.max(10, boxToViewport(boxes[0]!, pageHeight, scale, viewport).top - 3)}
                fontSize={11}
                fill={color.stroke}
                style={{ pointerEvents: 'none', fontFamily: 'ui-sans-serif, system-ui' }}
              >
                {annotation.payload.headword_guess}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
