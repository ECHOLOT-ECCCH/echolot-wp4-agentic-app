import { ChevronLeftIcon, ChevronRightIcon, ZoomInIcon, ZoomOutIcon } from 'lucide-react'
import type { PageViewport } from 'pdfjs-dist'
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
// oxlint-disable-next-line import/default -- Vite's ?url import yields the asset URL as default export
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { Document, Page, pdfjs } from 'react-pdf'

import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

import { Button } from '@/components/ui/button'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2]

export interface PageRenderContext {
  pageNumber: number
  scale: number
  viewport: PageViewport | null
  pageHeight: number
}

export interface PdfViewerProps {
  url: string
  /** 1-based page to scroll to (from a ui-action). */
  page?: number
  bbox?: [number, number, number, number]
  revision?: number
  /** Draw the amber highlight for `bbox` (off when an overlay already marks the selection). */
  highlightBbox?: boolean
  /** Extra content drawn over each rendered page (overlays); gets the page's viewport. */
  renderOverlay?: (context: PageRenderContext) => ReactNode
  /** Called when the visible page changes (for review panels that follow the reader). */
  onCurrentPageChange?: (page: number) => void
}

/**
 * PDF viewer with lazily mounted pages: only pages near the viewport render, the rest keep an
 * estimated height. Bounding boxes use the pdfplumber convention (points, origin top-left).
 */
export function PdfViewer({
  url,
  page,
  bbox,
  revision,
  highlightBbox = true,
  renderOverlay,
  onCurrentPageChange,
}: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0)
  const [zoom, setZoom] = useState(1)
  const [pageSize, setPageSize] = useState<{ width: number; height: number } | null>(null)
  const [viewports, setViewports] = useState<Map<number, PageViewport>>(() => new Map())
  const [visible, setVisible] = useState<Set<number>>(() => new Set([1, 2]))
  const [current, setCurrent] = useState(1)
  const containerRef = useRef<HTMLDivElement>(null)
  const pageRefs = useRef(new Map<number, HTMLDivElement>())

  const file = useMemo(() => ({ url, withCredentials: true }), [url])

  const observe = useCallback((element: HTMLDivElement | null, index: number) => {
    if (element) pageRefs.current.set(index, element)
    else pageRefs.current.delete(index)
  }, [])

  useEffect(() => {
    const root = containerRef.current
    if (!root || numPages === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        setVisible((prev) => {
          const next = new Set(prev)
          let changed = false
          for (const entry of entries) {
            const index = Number((entry.target as HTMLElement).dataset.page)
            if (entry.isIntersecting) {
              for (const i of [index - 1, index, index + 1]) {
                if (i >= 1 && i <= numPages && !next.has(i)) {
                  next.add(i)
                  changed = true
                }
              }
            }
          }
          return changed ? next : prev
        })
      },
      { root, rootMargin: '600px 0px', threshold: 0 },
    )
    for (const element of pageRefs.current.values()) observer.observe(element)
    return () => observer.disconnect()
  }, [numPages])

  // The current page is the first one whose bottom edge is below the top third of the viewport.
  const updateCurrent = useCallback(() => {
    const root = containerRef.current
    if (!root) return
    const rootTop = root.getBoundingClientRect().top
    const threshold = rootTop + root.clientHeight / 3
    for (const [index, element] of [...pageRefs.current.entries()].sort((a, b) => a[0] - b[0])) {
      if (element.getBoundingClientRect().bottom >= threshold) {
        setCurrent((previous) => {
          if (previous !== index) onCurrentPageChange?.(index)
          return index
        })
        return
      }
    }
  }, [onCurrentPageChange])

  useEffect(() => {
    const root = containerRef.current
    if (!root) return
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(updateCurrent)
    }
    root.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      cancelAnimationFrame(frame)
      root.removeEventListener('scroll', onScroll)
    }
  }, [updateCurrent])

  const scrollTo = useCallback((target: number, offsetPoints?: number) => {
    const element = pageRefs.current.get(target)
    const root = containerRef.current
    if (!element || !root) return
    if (offsetPoints === undefined) {
      element.scrollIntoView({ block: 'start' })
      return
    }
    // offsets relative to the scroll container (offsetTop would use another positioned parent)
    const pageTop =
      element.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    root.scrollTo({ top: Math.max(0, pageTop + offsetPoints - root.clientHeight / 4) })
  }, [])

  // A ui-action may re-open the same page; the revision makes the request distinct.
  const navigation = page && numPages ? `${page}:${bbox ? bbox[1] : ''}:${revision ?? 0}` : null
  useEffect(() => {
    if (!navigation) return
    const target = Number(navigation.split(':')[0])
    const frame = requestAnimationFrame(() => {
      setVisible((prev) => (prev.has(target) ? prev : new Set([...prev, target, target + 1])))
      requestAnimationFrame(() => scrollTo(target, bbox ? bbox[1] * zoom : undefined))
    })
    return () => cancelAnimationFrame(frame)
    // bbox and zoom are folded into `navigation` on purpose: re-run only on a new request
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [navigation, scrollTo])

  const scale = zoom
  const estimatedHeight = pageSize ? pageSize.height * scale : 1100 * scale
  useEffect(() => {
    setViewports(new Map())
  }, [scale])

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b px-2 text-xs">
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Previous page"
          onClick={() => scrollTo(Math.max(1, current - 1))}
        >
          <ChevronLeftIcon />
        </Button>
        <span className="tabular-nums">
          {current} / {numPages || '…'}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label="Next page"
          onClick={() => scrollTo(Math.min(numPages, current + 1))}
        >
          <ChevronRightIcon />
        </Button>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Zoom out"
            onClick={() =>
              setZoom((z) => [...ZOOMS].reverse().find((step) => step < z - 0.001) ?? z)
            }
          >
            <ZoomOutIcon />
          </Button>
          <span className="w-10 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Zoom in"
            onClick={() => setZoom((z) => ZOOMS.find((step) => step > z + 0.001) ?? z)}
          >
            <ZoomInIcon />
          </Button>
        </div>
      </div>
      <div ref={containerRef} className="bg-muted/40 min-h-0 flex-1 overflow-auto p-4">
        <Document
          file={file}
          onLoadSuccess={(doc) => {
            setNumPages(doc.numPages)
            void doc.getPage(1).then((first) => {
              const viewport = first.getViewport({ scale: 1 })
              setPageSize({ width: viewport.width, height: viewport.height })
              // start at fit-to-width so wide pages are not cropped in narrow panes
              const available = (containerRef.current?.clientWidth ?? 0) - 32
              if (available > 100 && viewport.width > available) {
                setZoom(Math.max(0.3, Math.floor((available / viewport.width) * 100) / 100))
              }
            })
          }}
          loading={<p className="text-muted-foreground text-sm">Loading PDF…</p>}
          error={<p className="text-destructive text-sm">This PDF could not be displayed.</p>}
          className="flex flex-col items-center gap-4"
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((index) => (
            <div
              key={index}
              ref={(el) => observe(el, index)}
              data-page={index}
              className="relative bg-white shadow-sm"
              style={{
                minHeight: estimatedHeight,
                width: pageSize ? pageSize.width * scale : undefined,
              }}
            >
              {visible.has(index) ? (
                <Page
                  pageNumber={index}
                  scale={scale}
                  renderAnnotationLayer={false}
                  renderTextLayer
                  onLoadSuccess={(loaded) => {
                    const viewport = loaded.getViewport({ scale })
                    setViewports((prev) => {
                      const next = new Map(prev)
                      next.set(index, viewport)
                      return next
                    })
                  }}
                >
                  {renderOverlay?.({
                    pageNumber: index,
                    scale,
                    viewport: viewports.get(index) ?? null,
                    pageHeight: pageSize?.height ?? 0,
                  })}
                  {highlightBbox && bbox && page === index && pageSize && (
                    <div
                      className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/20"
                      style={{
                        left: bbox[0] * scale,
                        top: bbox[1] * scale,
                        width: (bbox[2] - bbox[0]) * scale,
                        height: (bbox[3] - bbox[1]) * scale,
                      }}
                    />
                  )}
                </Page>
              ) : (
                <div className="text-muted-foreground flex h-full items-center justify-center text-xs">
                  {index}
                </div>
              )}
            </div>
          ))}
        </Document>
      </div>
    </div>
  )
}
