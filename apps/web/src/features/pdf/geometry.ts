import type { PageViewport } from 'pdfjs-dist'

export type Box = [number, number, number, number]

export interface ViewportRect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * pdfplumber box (points, origin top-left) -> CSS pixels on the rendered page (PLAN 7.10).
 * With a PDF.js viewport the two corners go through `convertToViewportPoint`, which honours the
 * page's view box offset; PDF.js 6 no longer has `convertToViewportRectangle`. Without a viewport
 * (or for rotated pages, whose pdfplumber coordinates are already in reading orientation) the
 * box is scaled directly.
 */
export function boxToViewport(
  box: Box,
  pageHeight: number,
  scale: number,
  viewport?: PageViewport | null,
): ViewportRect {
  const [x0, top, x1, bottom] = box
  if (viewport && viewport.rotation % 360 === 0 && pageHeight > 0) {
    const [ax, ay] = viewport.convertToViewportPoint(x0, pageHeight - bottom) as [number, number]
    const [bx, by] = viewport.convertToViewportPoint(x1, pageHeight - top) as [number, number]
    return {
      left: Math.min(ax, bx),
      top: Math.min(ay, by),
      width: Math.abs(bx - ax),
      height: Math.abs(by - ay),
    }
  }
  return {
    left: x0 * scale,
    top: top * scale,
    width: (x1 - x0) * scale,
    height: (bottom - top) * scale,
  }
}
