/** Display-only internalSkeleton; never normalize or accept an identifier. */
import data from '../../unicode_data/confusables.json' with { type: 'json' }

const mappings: Record<string, string> = data.mappings
const ranges: number[][] = data.defaultIgnorableRanges

function ignorable(cp: number): boolean {
  let lo = 0, hi = ranges.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (ranges[mid][0] <= cp) lo = mid + 1
    else hi = mid
  }
  return lo > 0 && cp <= ranges[lo - 1][1]
}

export function internalSkeleton(value: string): string {
  return Array.from(value.normalize('NFD'), char => {
    const cp = char.codePointAt(0)!
    return ignorable(cp) ? '' : (mappings[String(cp)] ?? char)
  }).join('').normalize('NFD')
}

export function visuallyConfusable(left: string, right: string): boolean {
  return left !== right && internalSkeleton(left) === internalSkeleton(right)
}

export function codePoints(value: string): string {
  return Array.from(value, char => `U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}`).join(' ')
}
