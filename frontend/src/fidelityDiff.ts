/**
 * Approximate text-level retention (Jaccard on tokens + length blend).
 *
 * IMPORTANT (v15): This is NOT layout/AST/rendering fidelity. Scores are only
 * meaningful for md/txt/html-like round-trips in the UI preview. Do not treat
 * retentionPct as proof of "high-fidelity" PDF/DOCX/EPUB conversion.
 */

export type FidelityTextCompare = {
  available: boolean
  reason?: string
  /** Always true when available — UI should surface this disclaimer. */
  approximateOnly: true
  sourceChars: number
  resultChars: number
  /** 0–100 approximate textual retention (NOT layout fidelity). */
  retentionPct: number
  /** Up to a few sample line-level notes. */
  samples: string[]
}

const TEXT_EXTS = new Set(['md', 'markdown', 'txt', 'html', 'htm', 'csv', 'json', 'tex', 'latex', 'rtf'])

export function isTextComparableSource(name: string, sourceFormat?: string): boolean {
  const fmt = (sourceFormat || '').toLowerCase()
  if (fmt && TEXT_EXTS.has(fmt)) return true
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return TEXT_EXTS.has(ext)
}

function normalize(text: string): string {
  return text
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function tokenize(text: string): string[] {
  return normalize(text)
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t.length > 0)
}

function jaccard(a: string[], b: string[]): number {
  if (!a.length && !b.length) return 1
  const sa = new Set(a)
  const sb = new Set(b)
  let inter = 0
  for (const t of sa) if (sb.has(t)) inter += 1
  const union = sa.size + sb.size - inter
  return union === 0 ? 1 : inter / union
}

function lineSamples(source: string, result: string, limit = 4): string[] {
  const sl = normalize(source).split('\n').filter(Boolean)
  const rl = new Set(normalize(result).split('\n').filter(Boolean).map((l) => l.trim()))
  const out: string[] = []
  for (const line of sl) {
    const t = line.trim()
    if (t.length < 4) continue
    if (!rl.has(t) && ![...rl].some((r) => r.includes(t) || t.includes(r))) {
      out.push(t.length > 120 ? `${t.slice(0, 117)}…` : t)
      if (out.length >= limit) break
    }
  }
  return out
}

export function compareTextFidelity(
  sourceText: string,
  resultText: string,
): FidelityTextCompare {
  const src = normalize(sourceText)
  const res = normalize(resultText)
  if (!src && !res) {
    return {
      available: false,
      reason: 'empty',
      approximateOnly: true,
      sourceChars: 0,
      resultChars: 0,
      retentionPct: 100,
      samples: [],
    }
  }
  const tokJ = jaccard(tokenize(src), tokenize(res))
  const lenRatio =
    Math.min(src.length, res.length) / Math.max(src.length || 1, res.length || 1)
  const retentionPct = Math.round((0.65 * tokJ + 0.35 * lenRatio) * 100)
  const samples = lineSamples(src, res)
  return {
    available: true,
    approximateOnly: true,
    sourceChars: src.length,
    resultChars: res.length,
    retentionPct,
    samples,
  }
}

export async function readFileAsText(file: File, maxBytes = 512_000): Promise<string | null> {
  const slice = file.size > maxBytes ? file.slice(0, maxBytes) : file
  try {
    return await slice.text()
  } catch {
    return null
  }
}
