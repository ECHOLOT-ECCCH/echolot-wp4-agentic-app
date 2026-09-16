import type { PageLayoutRecord } from '@/lib/queries'

export type Box = [number, number, number, number]

/** Words of the layout inside a region, in reading order, joined as text. */
export function textInRegion(layout: PageLayoutRecord, box: Box): { text: string; words: number } {
  const [x0, top, x1, bottom] = box
  const picked: string[] = []
  for (const line of layout.lines) {
    if (!(line.x1 > x0 && line.x0 < x1 && line.bottom > top && line.top < bottom)) continue
    const words = line.words.filter(
      (w) => w.x1 > x0 && w.x0 < x1 && w.bottom > top && w.top < bottom,
    )
    if (words.length) picked.push(words.map((w) => w.text).join(' '))
  }
  const text = picked.join('\n')
  return { text, words: text.split(/\s+/).filter(Boolean).length }
}

/** The page element and the selection's box in PDF points, or null when the selection is not
 *  inside one rendered page. */
export function selectionOnPage(
  selection: Selection | null,
  scale: number,
): { pageNumber: number; box: Box; text: string } | null {
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null
  const range = selection.getRangeAt(0)
  const node =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement
  const pageElement = node?.closest<HTMLElement>('[data-page]')
  if (!pageElement) return null
  const pageNumber = Number(pageElement.dataset.page)
  const pageRect = pageElement.getBoundingClientRect()
  const rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0)
  if (rects.length === 0) return null
  const left = Math.min(...rects.map((r) => r.left)) - pageRect.left
  const top = Math.min(...rects.map((r) => r.top)) - pageRect.top
  const right = Math.max(...rects.map((r) => r.right)) - pageRect.left
  const bottom = Math.max(...rects.map((r) => r.bottom)) - pageRect.top
  const text = selection.toString().replace(/\s+/g, ' ').trim()
  if (!text) return null
  return {
    pageNumber,
    box: [left / scale, top / scale, right / scale, bottom / scale],
    text,
  }
}
