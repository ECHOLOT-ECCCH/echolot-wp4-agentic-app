export const PAGE_SIZE = 50

/** The offset of the page that holds ``row``. */
export function pageStart(row: number): number {
  return Math.floor(row / PAGE_SIZE) * PAGE_SIZE
}
