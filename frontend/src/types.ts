export type SourceFormat = 'docx' | 'pdf' | 'md' | 'txt' | 'html' | 'pptx' | 'epub' | 'mobi'
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
export type DocFormat = SourceFormat | TargetFormat

export interface ConvertResult {
  filename: string
  blob: Blob
  preview?: string
}

export interface ConvertJob {
  id: string
  file: File
  sourceFormat: SourceFormat
  targetFormat: TargetFormat
  status: 'pending' | 'converting' | 'done' | 'error'
  progress?: number
  result?: ConvertResult
  error?: string
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
  }
  return ext && map[ext] ? map[ext] : null
}

export function formatLabel(fmt: DocFormat, locale: 'zh' | 'en' = 'zh'): string {
  const zh: Record<DocFormat, string> = {
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
  }
  const en: Record<DocFormat, string> = {
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
  }
  return (locale === 'en' ? en : zh)[fmt]
}
