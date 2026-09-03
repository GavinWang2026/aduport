export type SourceFormat =
  | 'docx'
  | 'doc'
  | 'docm'
  | 'odt'
  | 'rtf'
  | 'pdf'
  | 'md'
  | 'txt'
  | 'html'
  | 'pptx'
  | 'ppt'
  | 'pps'
  | 'pot'
  | 'pptm'
  | 'ppsx'
  | 'ppsm'
  | 'epub'
  | 'mobi'
  | 'azw3'
  | 'kf8'
  | 'latex'
  | 'json'
  | 'xlsx'
  | 'xls'
  | 'xlsb'
  | 'xlsm'
  | 'ods'
  | 'odp'
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
  | 'ods'
  | 'odp'
  | 'json'
  | 'rag'
  | 'xlsx'
  | 'rtf'
export type DocFormat = SourceFormat | TargetFormat | 'auto'

export interface ConvertResult {
  filename: string
  blob: Blob
  preview?: string
}

/** Advanced export options mirrored from API Form fields. */
export interface ConvertOptions {
  pdf_font_family?: string
  pdf_custom_css?: string
  pdf_page_size?: string
  pdf_orientation?: string
  pdf_header?: string
  pdf_footer?: string
  pdf_page_numbers?: boolean
  pdf_toc?: boolean
  docx_theme?: string
}

export const DEFAULT_CONVERT_OPTIONS: ConvertOptions = {
  pdf_page_size: 'A4',
  pdf_orientation: 'portrait',
  pdf_page_numbers: true,
  pdf_toc: false,
  docx_theme: 'default',
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

/** Prefer listing high-traffic formats first in the UI. */
export const SOURCE_FORMAT_ORDER: SourceFormat[] = [
  'docx',
  'doc',
  'docm',
  'pdf',
  'md',
  'txt',
  'html',
  'pptx',
  'ppt',
  'pps',
  'pot',
  'pptm',
  'ppsx',
  'ppsm',
  'xlsx',
  'xls',
  'xlsb',
  'xlsm',
  'ods',
  'odp',
  'csv',
  'odt',
  'rtf',
  'epub',
  'mobi',
  'azw3',
  'kf8',
  'latex',
  'json',
]

export function detectFormat(filename: string): SourceFormat | null {
  const ext = filename.split('.').pop()?.toLowerCase()
  const map: Record<string, SourceFormat> = {
    docx: 'docx',
    doc: 'doc',
    docm: 'docm',
    pdf: 'pdf',
    md: 'md',
    markdown: 'md',
    txt: 'txt',
    html: 'html',
    htm: 'html',
    pptx: 'pptx',
    ppt: 'ppt',
    pps: 'pps',
    pot: 'pot',
    pptm: 'pptm',
    ppsx: 'ppsx',
    ppsm: 'ppsm',
    epub: 'epub',
    mobi: 'mobi',
    azw: 'mobi',
    azw3: 'azw3',
    kf8: 'kf8',
    odt: 'odt',
    rtf: 'rtf',
    json: 'json',
    xlsx: 'xlsx',
    xls: 'xls',
    xlsb: 'xlsb',
    xlsm: 'xlsm',
    ods: 'ods',
    odp: 'odp',
    csv: 'csv',
    tex: 'latex',
    latex: 'latex',
  }
  return ext && map[ext] ? map[ext] : null
}

export function formatLabel(fmt: DocFormat, locale: 'zh' | 'en' = 'zh'): string {
  const zh: Record<DocFormat, string> = {
    auto: '自动识别',
    docx: 'Word (.docx)',
    doc: 'Word 97-2003（文本级）',
    docm: 'Word 宏 (.docm)',
    pdf: 'PDF（数字文本层；扫描需 OCR）',
    md: 'Markdown (.md)',
    txt: '纯文本 (.txt)',
    html: 'HTML (.html)',
    pptx: 'PowerPoint (.pptx)',
    ppt: 'PowerPoint 97-2003（文本级）',
    pps: 'PowerPoint 放映（文本级）',
    pot: 'PowerPoint 模板（文本级）',
    pptm: 'PowerPoint 宏 (.pptm)',
    ppsx: 'PowerPoint 放映 (.ppsx)',
    ppsm: 'PowerPoint 宏放映 (.ppsm)',
    epub: 'EPUB',
    mobi: 'MOBI（文本级）',
    azw3: 'AZW3（文本级）',
    kf8: 'KF8（文本级）',
    latex: 'LaTeX (.tex)',
    csv: 'CSV 表格',
    odt: 'OpenDocument 文本 (.odt)',
    rtf: 'RTF',
    json: 'JSON',
    rag: 'RAG Elements（JSON）',
    xlsx: 'Excel (.xlsx)',
    xls: 'Excel 97-2003（文本级）',
    xlsb: 'Excel 二进制（xlsb/anydoc）',
    xlsm: 'Excel 宏 (.xlsm)',
    ods: 'OpenDocument 表格（浅导出）',
    odp: 'OpenDocument 演示（浅导出）',
  }
  const en: Record<DocFormat, string> = {
    auto: 'Auto-detect',
    docx: 'Word (.docx)',
    doc: 'Word 97-2003 (text-level)',
    docm: 'Word macro (.docm)',
    pdf: 'PDF (digital text; scan needs OCR)',
    md: 'Markdown (.md)',
    txt: 'Plain text (.txt)',
    html: 'HTML (.html)',
    pptx: 'PowerPoint (.pptx)',
    ppt: 'PowerPoint 97-2003 (text-level)',
    pps: 'PowerPoint show (text-level)',
    pot: 'PowerPoint template (text-level)',
    pptm: 'PowerPoint macro (.pptm)',
    ppsx: 'PowerPoint show (.ppsx)',
    ppsm: 'PowerPoint macro show (.ppsm)',
    epub: 'EPUB',
    mobi: 'MOBI (text-level)',
    azw3: 'AZW3 (text-level)',
    kf8: 'KF8 (text-level)',
    latex: 'LaTeX (.tex)',
    csv: 'CSV table',
    odt: 'OpenDocument Text (.odt)',
    rtf: 'RTF',
    json: 'JSON',
    rag: 'RAG Elements (JSON)',
    xlsx: 'Excel (.xlsx)',
    xls: 'Excel 97-2003 (text-level)',
    xlsb: 'Excel binary (xlsb/anydoc)',
    xlsm: 'Excel macro (.xlsm)',
    ods: 'OpenDocument Spreadsheet (shallow export)',
    odp: 'OpenDocument Presentation (shallow export)',
  }
  return (locale === 'en' ? en : zh)[fmt] ?? fmt
}

/** Breadth honesty: formats that are import-degraded / text-level (not full layout fidelity). */
export const TEXT_LEVEL_SOURCE_FORMATS: ReadonlySet<string> = new Set([
  'doc',
  'ppt',
  'pps',
  'pot',
  'xls',
  'xlsb',
  'mobi',
  'azw3',
  'kf8',
])

/** Target formats that are intentionally shallow (table/slide text-level). */
export const EXPORT_SHALLOW_FORMATS: ReadonlySet<string> = new Set([
  'ods',
  'odp',
  'azw3',
  'kf8',
])
