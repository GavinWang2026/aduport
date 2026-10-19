/**
 * Block-level Markdown structure diff.
 * This is a semantic comparison of headings, quotes, fences, lists, and tables.
 * It is not a token Jaccard score and it is not layout fidelity.
 */

export type AstBlockKind =
  | 'heading'
  | 'paragraph'
  | 'blockquote'
  | 'code'
  | 'list'
  | 'table'
  | 'hr'
  | 'footnote'

export type AstBlock = {
  kind: AstBlockKind
  text: string
  level?: number
  lang?: string
}

export type AstChange = {
  op: 'insert' | 'delete' | 'replace'
  path: string
  before: string
  after: string
}

export type AstDiff = {
  available: boolean
  /** False: this compares block structure, not an approximate token score. */
  approximateOnly: false
  equal: boolean
  changes: AstChange[]
}

function signature(block: AstBlock): string {
  const level = block.level ?? ''
  const lang = block.lang ?? ''
  return `${block.kind}|${level}|${lang}|${block.text}`
}

export function parseMarkdownBlocks(source: string): AstBlock[] {
  const lines = source.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n')
  const blocks: AstBlock[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i += 1
      continue
    }
    const fence = /^(```|~~~)(.*)$/.exec(line.trim())
    if (fence) {
      const marker = fence[1]
      const lang = (fence[2] || '').trim()
      const body: string[] = []
      i += 1
      while (i < lines.length && !lines[i].trim().startsWith(marker)) {
        body.push(lines[i])
        i += 1
      }
      if (i < lines.length) i += 1
      blocks.push({ kind: 'code', lang, text: body.join('\n').replace(/\n$/, '') })
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2].trim() })
      i += 1
      continue
    }
    if (/^>/.test(line)) {
      const quote: string[] = []
      while (i < lines.length && /^>/.test(lines[i])) {
        quote.push(lines[i].replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push({ kind: 'blockquote', text: quote.join('\n').trim() })
      continue
    }
    if (/^\[(\^[^\]]+)\]:/.test(line.trim())) {
      blocks.push({ kind: 'footnote', text: line.trim() })
      i += 1
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push({ kind: 'hr', text: '' })
      i += 1
      continue
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, '').trim())
        i += 1
      }
      blocks.push({ kind: 'list', text: items.join('\n') })
      continue
    }
    if (/^\|.+\|$/.test(line.trim())) {
      const rows: string[] = []
      while (i < lines.length && /^\|.+\|$/.test(lines[i].trim())) {
        rows.push(lines[i].trim())
        i += 1
      }
      blocks.push({ kind: 'table', text: rows.join('\n') })
      continue
    }
    const para: string[] = [line.trim()]
    i += 1
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|>|```|~~~|\|)/.test(lines[i])) {
      para.push(lines[i].trim())
      i += 1
    }
    blocks.push({ kind: 'paragraph', text: para.join(' ') })
  }
  return blocks
}

export function diffMarkdown(left: string, right: string): AstDiff {
  const a = parseMarkdownBlocks(left).map(signature)
  const b = parseMarkdownBlocks(right).map(signature)
  const changes: AstChange[] = []
  // Myers-free alignment: walk the shorter common prefix/suffix, then one replace span.
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1
    endB -= 1
  }
  const before = a.slice(start, endA)
  const after = b.slice(start, endB)
  if (before.length || after.length) {
    let op: AstChange['op'] = 'replace'
    if (!before.length) op = 'insert'
    else if (!after.length) op = 'delete'
    changes.push({
      op,
      path: `blocks[${start}:${endA}]`,
      before: before.join('\n'),
      after: after.join('\n'),
    })
  }
  return {
    available: true,
    approximateOnly: false,
    equal: changes.length === 0,
    changes,
  }
}

export type ReviewNote = {
  kind: 'comment' | 'insert' | 'delete'
  author: string
  anchor: string
  text: string
}

export type ReviewDiff = {
  added: ReviewNote[]
  removed: ReviewNote[]
  unchanged: number
}

function collectReview(source: string): ReviewNote[] {
  const notes: ReviewNote[] = []
  const comment = /<!--\s*comment:(\S+)(?:\s+"([^"]*)")?\s+([\s\S]*?)\s*-->/g
  const inserted = /\+\+(.+?)\+\+\s*<!--\s*ins:([^>]+?)\s*-->/g
  const deleted = /~~(.+?)~~\s*<!--\s*del:([^>]+?)\s*-->/g
  for (const match of source.matchAll(comment)) {
    notes.push({ kind: 'comment', author: match[1], anchor: match[2] || '', text: match[3] })
  }
  for (const match of source.matchAll(inserted)) {
    notes.push({ kind: 'insert', author: match[2].trim(), anchor: '', text: match[1] })
  }
  for (const match of source.matchAll(deleted)) {
    notes.push({ kind: 'delete', author: match[2].trim(), anchor: '', text: match[1] })
  }
  return notes
}

function reviewKey(note: ReviewNote): string {
  return `${note.kind}|${note.author}|${note.anchor}|${note.text}`
}

export function reviewMarkdown(left: string, right: string): ReviewDiff | null {
  const before = collectReview(left)
  const after = collectReview(right)
  if (!before.length && !after.length) return null
  const leftCount = new Map<string, number>()
  const rightCount = new Map<string, number>()
  const byKey = new Map<string, ReviewNote>()
  for (const note of before) {
    const key = reviewKey(note)
    leftCount.set(key, (leftCount.get(key) || 0) + 1)
    byKey.set(key, note)
  }
  for (const note of after) {
    const key = reviewKey(note)
    rightCount.set(key, (rightCount.get(key) || 0) + 1)
    byKey.set(key, note)
  }
  const added: ReviewNote[] = []
  const removed: ReviewNote[] = []
  let unchanged = 0
  const keys = new Set([...leftCount.keys(), ...rightCount.keys()])
  for (const key of keys) {
    const l = leftCount.get(key) || 0
    const r = rightCount.get(key) || 0
    unchanged += Math.min(l, r)
    const note = byKey.get(key)
    if (!note) continue
    for (let i = 0; i < r - l; i += 1) added.push(note)
    for (let i = 0; i < l - r; i += 1) removed.push(note)
  }
  return { added, removed, unchanged }
}
