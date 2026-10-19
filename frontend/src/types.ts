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
  | 'msg'
  | 'ipynb'
  | 'zip'
  | 'png'
  | 'jpg'
  | 'rss'
  | 'atom'

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
  /** Degraded export hints from X-AduPort-Export-Warning-* headers */
  exportWarnings?: string[]
}

export interface JobStatusPayload {
  id: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: number
  stage?: string
  error?: string | null
  code?: string | null
  result_filename?: string | null
  access_token?: string
  export_warnings?: string[]
  filename?: string
  source_format?: string
  target_format?: string
  created_at?: number
  resumable?: boolean
  /** True when POST /jobs/{id}/reprocess can reuse the stored input. */
  reprocessable?: boolean
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
  rag_max_chars?: number
  rag_chunk_overlap?: number
  embed?: boolean
  embed_backend?: string
  vector_persist?: boolean
  force_ocr?: boolean
  pdf_engine?: string
  ocr_engine?: string
  cache_bypass?: boolean
  skip_pdf_sidecar?: boolean
}

export const DEFAULT_CONVERT_OPTIONS: ConvertOptions = {
  pdf_page_size: 'A4',
  pdf_orientation: 'portrait',
  pdf_page_numbers: true,
  pdf_toc: false,
  docx_theme: 'default',
  rag_max_chars: 1200,
  rag_chunk_overlap: 0,
  embed: false,
  vector_persist: false,
  force_ocr: false,
  cache_bypass: false,
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
  'msg',
  'ipynb',
  'zip',
  'png',
  'jpg',
  'rss',
  'atom',
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
    msg: 'msg',
    ipynb: 'ipynb',
    zip: 'zip',
    png: 'png',
    jpg: 'jpg',
    jpeg: 'jpg',
    rss: 'rss',
    atom: 'atom',
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
    rag: 'RAG 导出模式（aduport.rag_elements，不是文件格式）',
    xlsx: 'Excel (.xlsx)',
    xls: 'Excel 97-2003（文本级）',
    xlsb: 'Excel 二进制（xlsb/anydoc）',
    xlsm: 'Excel 宏 (.xlsm)',
    ods: 'OpenDocument 表格（浅导出）',
    odp: 'OpenDocument 演示（浅导出）',
    msg: 'Outlook 邮件 (.msg)',
    ipynb: 'Jupyter Notebook (.ipynb)',
    zip: 'ZIP 归档（多文件）',
    png: 'PNG 图像（未启用 OCR 时仅嵌入图片）',
    jpg: 'JPEG 图像（未启用 OCR 时仅嵌入图片）',
    rss: 'RSS 订阅 (.rss)',
    atom: 'Atom 订阅 (.atom)',
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
    rag: 'RAG export mode (aduport.rag_elements, not a file format)',
    xlsx: 'Excel (.xlsx)',
    xls: 'Excel 97-2003 (text-level)',
    xlsb: 'Excel binary (xlsb/anydoc)',
    xlsm: 'Excel macro (.xlsm)',
    ods: 'OpenDocument Spreadsheet (shallow export)',
    odp: 'OpenDocument Presentation (shallow export)',
    msg: 'Outlook mail (.msg)',
    ipynb: 'Jupyter Notebook (.ipynb)',
    zip: 'ZIP archive (multi-file)',
    png: 'PNG image (embedded only unless OCR is enabled)',
    jpg: 'JPEG image (embedded only unless OCR is enabled)',
    rss: 'RSS feed (.rss)',
    atom: 'Atom feed (.atom)',
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
  'msg',
  'ipynb',
  'zip',
  'png',
  'jpg',
  'rss',
  'atom',
])

/** Target formats that are intentionally shallow (backend export_shallow). */
export const EXPORT_SHALLOW_FORMATS: ReadonlySet<string> = new Set([
  'azw3',
  'kf8',
])

/** Targets that keep headings/tables as text markers, not layout (backend text_markers). */
export const TEXT_MARKERS_TARGET_FORMATS: ReadonlySet<string> = new Set([
  'csv',
  'txt',
])
