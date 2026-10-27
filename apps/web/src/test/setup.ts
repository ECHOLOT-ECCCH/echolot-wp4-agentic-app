import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'

// findBy*/waitFor default to 1 s, which a busy machine running twelve test files in parallel
// can miss on a re-render after a fetch; the tests wait for real data, not for timers
configure({ asyncUtilTimeout: 5000 })

// jsdom lacks matchMedia (used by sonner) and IntersectionObserver.
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList
}
if (!('IntersectionObserver' in window)) {
  class FakeIntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return []
    }
  }
  Object.defineProperty(window, 'IntersectionObserver', { value: FakeIntersectionObserver })
}
