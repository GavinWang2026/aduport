/** Preview HTML sanitizer (report P1-4 / architecture deep defense). */

import DOMPurify from 'dompurify'

const PREVIEW_HOOK = {
  ALLOWED_TAGS: [
    'a', 'abbr', 'b', 'blockquote', 'br', 'caption', 'code', 'col', 'colgroup',
    'dd', 'del', 'details', 'div', 'dl', 'dt', 'em', 'figcaption', 'figure',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'kbd', 'li',
    'mark', 'ol', 'p', 'pre', 'q', 's', 'samp', 'section', 'small', 'span',
    'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th',
    'thead', 'tr', 'u', 'ul', 'var',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'alt', 'src', 'class', 'id', 'colspan', 'rowspan',
    'scope', 'start', 'type', 'width', 'height', 'align',
  ],
  ALLOW_DATA_ATTR: false,
  // Explicit schemes only — reject protocol-relative //evil.com (report P3-7).
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|blob):|#|\/(?!\/)|[a-z0-9][\w.~-]*(?:[\/?#]|$))/i,
}

export function sanitizePreviewHtml(html: string): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, PREVIEW_HOOK)
}

/** EPUB chapter body — same tag allowlist; permits blob: image URLs. */
export function sanitizeEpubHtml(html: string): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, {
    ...PREVIEW_HOOK,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|blob):|#|\/(?!\/)|[a-z0-9][\w.~-]*(?:[\/?#]|$))/i,
  })
}
