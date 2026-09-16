import { describe, expect, it } from 'vitest'

import { boxToViewport } from '@/features/pdf/geometry'

describe('boxToViewport', () => {
  it('scales pdfplumber boxes without a viewport', () => {
    expect(boxToViewport([10, 20, 110, 70], 842, 2)).toEqual({
      left: 20,
      top: 40,
      width: 200,
      height: 100,
    })
  })

  it('uses the viewport for unrotated pages (flipping y through PDF space)', () => {
    const viewport = {
      rotation: 0,
      // a scale-2 viewport of an 842pt page: x doubles, y flips and doubles
      convertToViewportPoint: (x: number, y: number) => [x * 2, (842 - y) * 2],
    }
    expect(boxToViewport([10, 20, 110, 70], 842, 2, viewport as never)).toEqual({
      left: 20,
      top: 40,
      width: 200,
      height: 100,
    })
  })

  it('falls back to plain scaling for rotated pages', () => {
    const viewport = { rotation: 90, convertToViewportPoint: () => [0, 0] }
    expect(boxToViewport([0, 0, 50, 50], 842, 1, viewport as never)).toEqual({
      left: 0,
      top: 0,
      width: 50,
      height: 50,
    })
  })
})
