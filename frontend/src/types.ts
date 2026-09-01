export type SourceFormat =
  | 'docx'
  | 'pdf'
  | 'md'
  | 'txt'
  | 'html'
  | 'pptx'
  | 'epub'
  | 'mobi'
  | 'odt'
  | 'rtf'
  | 'doc'
  | 'json'
  | 'xlsx'
  | 'csv'

/** Explicit format or server-side auto detection (`source_format=auto`). */
export type SourceFormatOption = SourceFormat | 'auto'

export type TargetFormat =
  | 'docx'
  | 'pdf'
  | 'md'
  | 'html'
  | 'txt'
  | 'epub'
  | 'pptx'
  | 'mobi'
  | 'azw3'
  | 'kf8'
  | 'latex'
  | 'csv'
  | 'odt'
  | 'json'
  | 'xlsx'
export type DocFormat = SourceFormat | TargetFormat | 'auto'

export interface ConvertResult {
  filename: string
  blob: Blob
  preview?: string
}

export interface ConvertJob {
  id: string
  file: File
  sourceFormat: SourceFormatOption
  targetFormat: TargetFormat
  status: 'pending' | 'converting' | 'done' | 'error'
  progress?: number
  result?: ConvertResult
  error?: string
  /** Relative path when picked from a directory */
  relativePath?: string
}

export interface HistoryItem {
  id: string
  name: string
  sourceFormat: string
  targetFormat: string
  at: string
  ok: boolean
  error?: string
}

export function detectFormat(filename: string): SourceFormat | null {
  const ext = filename.split('.').pop()?.toLowerCase()
  const map: Record<string, SourceFormat> = {
    docx: 'docx',
    pdf: 'pdf',
    md: 'md',
    txt: 'txt',
    html: 'html',
    htm: 'html',
    pptx: 'pptx',
    epub: 'epub',
    mobi: 'mobi',
    azw: 'mobi',
    azw3: 'mobi',
    odt: 'odt',
    rtf: 'rtf',
    doc: 'doc',
    json: 'json',
    xlsx: 'xlsx',
    csv: 'csv',
  }
  return ext && map[ext] ? map[ext] : null
}

export function formatLabel(fmt: DocFormat, locale: 'zh' | 'en' = 'zh'): string {
  const zh: Record<DocFormat, string> = {
    auto: '自动识别',
    docx: 'Word (.docx)',
    pdf: 'PDF',
    md: 'Markdown (.md)',
    txt: '纯文本 (.txt)',
    html: 'HTML (.html)',
    pptx: 'PowerPoint (.pptx)',
    epub: 'EPUB',
    mobi: 'MOBI / Kindle',
    azw3: 'AZW3 / KF8',
    kf8: 'KF8 (Kindle)',
    latex: 'LaTeX (.tex)',
    csv: 'CSV 表格',
    odt: 'OpenDocument (.odt)',
    rtf: 'RTF',
    doc: 'Word 97-2003 (.doc)',
    json: 'JSON',
    xlsx: 'Excel (.xlsx)',
  }
  const en: Record<DocFormat, string> = {
    auto: 'Auto-detect',
    docx: 'Word (.docx)',
    pdf: 'PDF',
    md: 'Markdown (.md)',
    txt: 'Plain text (.txt)',
    html: 'HTML (.html)',
    pptx: 'PowerPoint (.pptx)',
    epub: 'EPUB',
    mobi: 'MOBI / Kindle',
    azw3: 'AZW3 / KF8',
    kf8: 'KF8 (Kindle)',
    latex: 'LaTeX (.tex)',
    csv: 'CSV table',
    odt: 'OpenDocument (.odt)',
    rtf: 'RTF',
    doc: 'Word 97-2003 (.doc)',
    json: 'JSON',
    xlsx: 'Excel (.xlsx)',
  }
  return (locale === 'en' ? en : zh)[fmt]
}
