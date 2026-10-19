import JSZip from 'jszip'
import { sanitizeEpubHtml } from './sanitize'

export interface EpubChapter {
  title: string
  html: string
}

const CONTAINER_PATH = 'META-INF/container.xml'

function dirname(path: string): string {
  const i = path.lastIndexOf('/')
  return i >= 0 ? path.slice(0, i + 1) : ''
}

function resolvePath(baseDir: string, href: string): string {
  if (href.startsWith('/')) return href.replace(/^\/+/, '')
  const parts = (baseDir + href).split('/')
  const out: string[] = []
  for (const p of parts) {
    if (!p || p === '.') continue
    if (p === '..') out.pop()
    else out.push(p)
  }
  return out.join('/')
}

function extractBody(html: string): string {
  const m = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)
  return m ? m[1] : html
}

function chapterTitle(html: string, fallback: string): string {
  const t = html.match(/<title[^>]*>([^<]*)<\/title>/i)
  if (t?.[1]?.trim()) return t[1].trim()
  const h = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)
  if (h?.[1]) return h[1].replace(/<[^>]+>/g, '').trim() || fallback
  return fallback
}

async function readText(zip: JSZip, path: string): Promise<string | null> {
  const file = zip.file(path) || zip.file(path.replace(/^\//, ''))
  if (!file) return null
  return file.async('string')
}

function parseOpfPath(containerXml: string): string | null {
  const m =
    containerXml.match(/full-path="([^"]+)"/i) ||
    containerXml.match(/full-path='([^']+)'/i)
  return m?.[1]?.trim() || null
}

function parseManifest(opf: string): Map<string, { href: string; media: string }> {
  const map = new Map<string, { href: string; media: string }>()
  for (const m of opf.matchAll(/<item\b[^>]*>/gi)) {
    const tag = m[0]
    const id = tag.match(/\bid="([^"]+)"/i)?.[1]
    const href = tag.match(/\bhref="([^"]+)"/i)?.[1]
    const media = tag.match(/media-type="([^"]+)"/i)?.[1] || ''
    if (id && href) map.set(id, { href, media })
  }
  return map
}

function parseSpine(opf: string): string[] {
  const ids: string[] = []
  for (const m of opf.matchAll(/<itemref\b[^>]*>/gi)) {
    const id = m[0].match(/\bidref="([^"]+)"/i)?.[1]
    if (id) ids.push(id)
  }
  return ids
}

async function inlineResources(
  zip: JSZip,
  opfDir: string,
  html: string,
  blobUrls: string[],
): Promise<string> {
  let out = html
  const imgRe = /\bsrc="([^"]+)"/gi
  let match: RegExpExecArray | null
  while ((match = imgRe.exec(html)) !== null) {
    const rel = match[1]
    if (!rel || rel.startsWith('data:') || rel.startsWith('http')) continue
    const path = resolvePath(opfDir, rel)
    const entry = zip.file(path)
    if (!entry) continue
    try {
      const buf = await entry.async('uint8array')
      const ext = path.split('.').pop()?.toLowerCase() || 'png'
      const mime =
        ext === 'jpg' || ext === 'jpeg'
          ? 'image/jpeg'
          : ext === 'svg'
            ? 'image/svg+xml'
            : ext === 'gif'
              ? 'image/gif'
              : 'image/png'
      const url = URL.createObjectURL(new Blob([buf], { type: mime }))
      blobUrls.push(url)
      out = out.split(`src="${rel}"`).join(`src="${url}"`)
    } catch {
      /* skip broken image */
    }
  }
  return out
}

function wrapChapterDocument(bodyHtml: string): string {
  const safe = sanitizeEpubHtml(bodyHtml)
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
body{font-family:Georgia,"Times New Roman",serif;line-height:1.65;color:#1a1a1a;background:#fff;
padding:1.25rem 1.5rem;max-width:42rem;margin:0 auto}
img{max-width:100%;height:auto}
h1,h2,h3{margin-top:1.2em}
p{margin:0.75em 0}
</style></head><body>${safe}</body></html>`
}

const MAX_EPUB_COMPRESSED_BYTES = 40 * 1024 * 1024
const MAX_EPUB_UNCOMPRESSED_BYTES = 120 * 1024 * 1024
const MAX_EPUB_ENTRIES = 4000

function assertZipBudget(zip: JSZip): void {
  let entries = 0
  let uncompressed = 0
  zip.forEach((_path, file) => {
    if (file.dir) return
    entries += 1
    if (entries > MAX_EPUB_ENTRIES) {
      throw new Error('epub_too_many_entries')
    }
    // JSZip stores uncompressedSize on the low-level data when available.
    const data = (file as unknown as { _data?: { uncompressedSize?: number } })._data
    const size = data?.uncompressedSize
    if (typeof size === 'number' && size >= 0) {
      uncompressed += size
      if (uncompressed > MAX_EPUB_UNCOMPRESSED_BYTES) {
        throw new Error('epub_uncompressed_too_large')
      }
    }
  })
}

/** Extract spine XHTML/HTML chapters from an EPUB blob for iframe preview. */
export async function loadEpubChapters(blob: Blob): Promise<EpubChapter[]> {
  if (blob.size > MAX_EPUB_COMPRESSED_BYTES) {
    throw new Error('epub_too_large')
  }
  const zip = await JSZip.loadAsync(await blob.arrayBuffer(), { checkCRC32: true })
  assertZipBudget(zip)
  const container = await readText(zip, CONTAINER_PATH)
  if (!container) throw new Error('invalid_epub_container')

  const opfPath = parseOpfPath(container)
  if (!opfPath) throw new Error('invalid_epub_opf_path')

  const opf = await readText(zip, opfPath)
  if (!opf) throw new Error('invalid_epub_opf')

  const opfDir = dirname(opfPath)
  const manifest = parseManifest(opf)
  const spine = parseSpine(opf)
  const blobUrls: string[] = []
  const chapters: EpubChapter[] = []

  try {
    let n = 0
    for (const id of spine.slice(0, 80)) {
      const item = manifest.get(id)
      if (!item) continue
      const media = item.media.toLowerCase()
      if (!media.includes('html') && !media.includes('xml')) continue
      const path = resolvePath(opfDir, item.href)
      const raw = await readText(zip, path)
      if (!raw?.trim()) continue
      n += 1
      let body = extractBody(raw)
      body = await inlineResources(zip, dirname(path), body, blobUrls)
      chapters.push({
        title: chapterTitle(raw, `Chapter ${n}`),
        html: wrapChapterDocument(body),
      })
    }
  } catch (err) {
    for (const u of blobUrls) URL.revokeObjectURL(u)
    throw err
  }

  if (!chapters.length) throw new Error('epub_empty_spine')
  ;(chapters as EpubChapter[] & { _blobs?: string[] })._blobs = blobUrls
  return chapters
}

export function revokeEpubBlobUrls(chapters: EpubChapter[] | null): void {
  const blobs = (chapters as (EpubChapter[] & { _blobs?: string[] }) | null)?._blobs
  if (!blobs) return
  for (const u of blobs) URL.revokeObjectURL(u)
}
