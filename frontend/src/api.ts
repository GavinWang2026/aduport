import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { ConvertOptions, ConvertResult, JobStatusPayload, SourceFormatOption, TargetFormat } from './types'
import {
  apiKeyHeaders,
  clearDurableApiKey,
  getApiKey,
  jobAuthHeaders,
  markCookieSession,
  rememberJobToken,
} from './sessionAuth'

export { getApiKey, getJobToken, rememberJobToken, setApiKey } from './sessionAuth'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 300000,
  withCredentials: true,
})

/** Bind API key into HttpOnly cookie; drop durable plaintext key on success. */
export async function createAuthSession(apiKey?: string): Promise<void> {
  const key = (apiKey || getApiKey()).trim()
  if (!key) return
  await api.post('/api/auth/session', null, {
    headers: { 'X-API-Key': key },
  })
  markCookieSession(true)
  clearDurableApiKey()
}

function parseExportWarnings(headers: Record<string, unknown>): string[] {
  const count = Number(headers['x-aduport-export-warnings'] || 0)
  const sample = String(headers['x-aduport-export-warning-sample'] || '').trim()
  if (!count) return []
  if (sample) return [sample]
  return [`${count} 条导出降级提示`]
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // Explicit: never inject X-API-Key once HttpOnly cookie session is bound.
  const headers = apiKeyHeaders()
  if (headers['X-API-Key']) {
    config.headers = config.headers || {}
    config.headers['X-API-Key'] = headers['X-API-Key']
  } else if (config.headers) {
    delete config.headers['X-API-Key']
    delete config.headers['x-api-key']
  }
  return config
})

function appendConvertOptions(form: FormData, opts?: ConvertOptions) {
  if (!opts) return
  const entries: [keyof ConvertOptions, string | boolean | number | undefined][] = [
    ['pdf_font_family', opts.pdf_font_family],
    ['pdf_custom_css', opts.pdf_custom_css],
    ['pdf_page_size', opts.pdf_page_size],
    ['pdf_orientation', opts.pdf_orientation],
    ['pdf_header', opts.pdf_header],
    ['pdf_footer', opts.pdf_footer],
    ['docx_theme', opts.docx_theme],
    ['rag_max_chars', opts.rag_max_chars],
    ['rag_chunk_overlap', opts.rag_chunk_overlap],
    ['embed_backend', opts.embed_backend],
    ['pdf_engine', opts.pdf_engine],
    ['ocr_engine', opts.ocr_engine],
  ]
  for (const [k, v] of entries) {
    if (v !== undefined && v !== null && String(v).trim() !== '') {
      form.append(k, String(v))
    }
  }
  if (opts.pdf_page_numbers !== undefined) {
    form.append('pdf_page_numbers', opts.pdf_page_numbers ? 'true' : 'false')
  }
  if (opts.pdf_toc !== undefined) {
    form.append('pdf_toc', opts.pdf_toc ? 'true' : 'false')
  }
  if (opts.embed !== undefined) {
    form.append('embed', opts.embed ? 'true' : 'false')
  }
  if (opts.vector_persist !== undefined) {
    form.append('vector_persist', opts.vector_persist ? 'true' : 'false')
  }
  if (opts.force_ocr !== undefined) {
    form.append('force_ocr', opts.force_ocr ? 'true' : 'false')
  }
  if (opts.cache_bypass !== undefined) {
    form.append('cache_bypass', opts.cache_bypass ? 'true' : 'false')
  }
  if (opts.skip_pdf_sidecar !== undefined) {
    form.append('skip_pdf_sidecar', opts.skip_pdf_sidecar ? 'true' : 'false')
  }
}

function parseFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback

  const star = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      /* fall through */
    }
  }

  const plain = disposition.match(/filename\s*=\s*"([^"]+)"/i)
    || disposition.match(/filename\s*=\s*([^;]+)/i)
  if (plain) {
    return plain[1].trim()
  }
  return fallback
}

async function extractErrorMessage(err: unknown): Promise<string> {
  if (!(err instanceof AxiosError) || !err.response) {
    return err instanceof Error ? err.message : '转换失败'
  }

  const pick = (json: unknown): string | null => {
    if (typeof json !== 'object' || !json) return null
    const obj = json as Record<string, unknown>
    const message = typeof obj.message === 'string' ? obj.message : null
    const code = typeof obj.code === 'string' ? obj.code : null
    if (!message) return null
    return code ? `[${code}] ${message}` : message
  }

  const data = err.response.data
  if (data instanceof Blob) {
    try {
      const text = await data.text()
      const fromJson = pick(JSON.parse(text))
      if (fromJson) return fromJson
      return text.slice(0, 200) || `请求失败 (${err.response.status})`
    } catch {
      return `请求失败 (${err.response.status})`
    }
  }

  const fromObj = pick(data)
  if (fromObj) return fromObj

  return err.message || `请求失败 (${err.response.status})`
}

export type { JobStatusPayload } from './types'
import { subscribeJobEvents, waitForJobViaSSE } from './jobEvents'
export { subscribeJobEvents, waitForJobViaSSE }

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

/** Async job path: enqueue → poll → download result. */
export async function convertDocumentAsync(
  file: File,
  sourceFormat: SourceFormatOption,
  targetFormat: TargetFormat,
  onProgress?: (p: number, status: string) => void,
  options?: ConvertOptions,
): Promise<ConvertResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('source_format', sourceFormat)
  form.append('target_format', targetFormat)
  appendConvertOptions(form, options)

  try {
    const { data: created } = await api.post<JobStatusPayload>('/api/jobs', form)
    const jobId = created.id
    rememberJobToken(jobId, created.access_token)
    onProgress?.(created.progress ?? 0, created.status)

    let finalStatus: JobStatusPayload
    try {
      finalStatus = await waitForJobViaSSE(jobId, onProgress)
    } catch {
      finalStatus = await pollJobUntilDone(jobId, onProgress)
    }

    const response = await api.get(`/api/jobs/${jobId}/result`, {
      responseType: 'blob',
      headers: jobAuthHeaders(jobId),
    })
    const disposition = response.headers['content-disposition'] as string | undefined
    const filename = parseFilename(
      disposition,
      finalStatus.result_filename || `converted.${targetFormat}`,
    )
    const blob = response.data as Blob
    const exportWarnings =
      (finalStatus.export_warnings && finalStatus.export_warnings.length
        ? finalStatus.export_warnings
        : parseExportWarnings(response.headers as Record<string, unknown>))
    let preview: string | undefined
    if (targetFormat === 'md' || targetFormat === 'html' || targetFormat === 'rag' || targetFormat === 'json') {
      preview = await blob.text()
    }
    return { filename, blob, preview, exportWarnings: exportWarnings.length ? exportWarnings : undefined }
  } catch (err) {
    throw new Error(await extractErrorMessage(err))
  }
}

async function pollJobUntilDone(
  jobId: string,
  onProgress?: (p: number, status: string) => void,
): Promise<JobStatusPayload> {
  for (let i = 0; i < 600; i++) {
    await sleep(i < 10 ? 200 : 500)
    const { data: st } = await api.get<JobStatusPayload>(`/api/jobs/${jobId}`, {
      headers: jobAuthHeaders(jobId),
    })
    onProgress?.(st.progress ?? 0, st.status)
    if (st.status === 'error') {
      throw new Error(st.error || '转换失败')
    }
    if (st.status === 'done') {
      return st
    }
  }
  throw new Error('转换超时，请稍后在历史中重试或改用同步转换')
}

export async function convertDocumentSync(
  file: File,
  sourceFormat: SourceFormatOption,
  targetFormat: TargetFormat,
  options?: ConvertOptions,
): Promise<ConvertResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('source_format', sourceFormat)
  form.append('target_format', targetFormat)
  appendConvertOptions(form, options)
  try {
    const response = await api.post('/api/convert', form, { responseType: 'blob' })
    const disposition = response.headers['content-disposition'] as string | undefined
    const filename = parseFilename(disposition, `converted.${targetFormat}`)
    const blob = response.data as Blob
    const exportWarnings = parseExportWarnings(response.headers as Record<string, unknown>)
    let preview: string | undefined
    if (targetFormat === 'md' || targetFormat === 'html' || targetFormat === 'rag' || targetFormat === 'json') {
      preview = await blob.text()
    }
    return { filename, blob, preview, exportWarnings: exportWarnings.length ? exportWarnings : undefined }
  } catch (err) {
    throw new Error(await extractErrorMessage(err))
  }
}

export async function convertDocument(
  file: File,
  sourceFormat: SourceFormatOption,
  targetFormat: TargetFormat,
  onProgress?: (p: number, status: string) => void,
  options?: ConvertOptions,
): Promise<ConvertResult> {
  return convertDocumentAsync(file, sourceFormat, targetFormat, onProgress, options)
}

/** Multi-file ZIP via POST /api/convert/batch (source_format=auto per file when requested). */
export async function convertBatchZip(
  files: File[],
  sourceFormat: SourceFormatOption,
  targetFormat: TargetFormat,
  options?: ConvertOptions,
): Promise<{ filename: string; blob: Blob }> {
  const form = new FormData()
  for (const f of files) {
    form.append('files', f, f.name)
  }
  form.append('source_format', sourceFormat)
  form.append('target_format', targetFormat)
  appendConvertOptions(form, options)
  try {
    const response = await api.post('/api/convert/batch', form, { responseType: 'blob' })
    const disposition = response.headers['content-disposition'] as string | undefined
    const filename = parseFilename(disposition, `batch-${targetFormat}.zip`)
    return { filename, blob: response.data as Blob }
  } catch (err) {
    throw new Error(await extractErrorMessage(err))
  }
}

export async function fetchHealth(): Promise<boolean> {
  try {
    const { data } = await api.get('/api/health')
    return data?.status === 'ok'
  } catch {
    return false
  }
}

export interface CapabilityHint {
  enabled?: boolean
  ready?: boolean
  install_hint?: string | null
}

export interface HealthDetailPayload {
  ocr_ready?: boolean
  ocr_enabled?: boolean
  anydoc_available?: boolean
  capabilities?: Record<string, CapabilityHint>
  pdf_export?: {
    weasyprint_available?: boolean
    install_hint?: string | null
  }
  rate_limit?: {
    backend?: string
    install_hint?: string | null
  }
}

export async function fetchHealthDetail(): Promise<HealthDetailPayload | null> {
  try {
    const { data } = await api.get<HealthDetailPayload>('/api/health/detail')
    return data
  } catch {
    return null
  }
}

export interface FormatsPayload {
  formats: string[]
  source: string[]
  target: string[]
  export_only: string[]
  export_modes?: string[]
  structure_limits?: Record<string, string[]>
  notes?: Record<string, string>
  fidelity_tiers?: {
    full?: string[]
    structured_partial?: string[]
    text_markers?: string[]
    text_level?: string[]
    export_shallow?: string[]
  }
  claim_policy?: {
    layout_targets?: string[]
    text_markers_only_targets?: string[]
    image_carry_only_sources?: string[]
    content_success_excludes_sources?: string[]
    note?: string
  }
}

export async function fetchFormats(): Promise<FormatsPayload | null> {
  try {
    const { data } = await api.get<FormatsPayload>('/api/formats')
    return data
  } catch {
    return null
  }
}

export interface ServerJob {
  id: string
  filename: string
  source_format: string
  target_format: string
  status: string
  progress: number
  stage?: string
  error?: string | null
  code?: string | null
  result_filename?: string | null
  created_at: number
  bytes_in?: number
  bytes_out?: number
  resumable?: boolean
  reprocessable?: boolean
}

export async function listServerJobs(): Promise<ServerJob[]> {
  const { data } = await api.get<{ jobs: ServerJob[] }>('/api/jobs')
  return data.jobs || []
}

export async function getServerJob(jobId: string): Promise<ServerJob> {
  const { data } = await api.get<ServerJob>(`/api/jobs/${jobId}`, {
    headers: jobAuthHeaders(jobId),
  })
  return data
}

export async function resumeServerJob(jobId: string): Promise<ServerJob & { access_token?: string }> {
  const { data } = await api.post<ServerJob & { access_token?: string }>(
    `/api/jobs/${jobId}/resume`,
    null,
    { headers: jobAuthHeaders(jobId) },
  )
  if (data?.access_token) {
    rememberJobToken(jobId, data.access_token)
  }
  return data
}

export async function reprocessServerJob(jobId: string): Promise<ServerJob & { access_token?: string }> {
  const { data } = await api.post<ServerJob & { access_token?: string }>(
    `/api/jobs/${jobId}/reprocess`,
    null,
    { headers: jobAuthHeaders(jobId) },
  )
  if (data?.access_token) {
    rememberJobToken(jobId, data.access_token)
  }
  return data
}

export async function downloadServerJob(jobId: string, fallbackName: string): Promise<void> {
  const response = await api.get(`/api/jobs/${jobId}/result`, {
    responseType: 'blob',
    headers: jobAuthHeaders(jobId),
  })
  const disposition = response.headers['content-disposition'] as string | undefined
  const filename = parseFilename(disposition, fallbackName)
  const url = URL.createObjectURL(response.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

export interface UsagePayload {
  tenant_id: string
  conversions_today: number
  conversions_total: number
  conversions_failed: number
}

export interface TenantPublic {
  tenant_id: string
  rate_limit: number | null
  daily_quota: number | null
}

export interface LicensePayload {
  required: boolean
  license: Record<string, unknown> | null
}

export interface FidelityProbeRow {
  target: string
  ok: boolean
  equal: boolean
  changes?: number
  error?: string
  footnote_wrapped?: boolean
  untitled?: boolean
}

export async function postToolJson(path: string, body: unknown): Promise<unknown> {
  try {
    const { data } = await api.post(path, body)
    return data
  } catch (err) {
    throw new Error(await extractErrorMessage(err))
  }
}

export async function probeFidelity(text: string): Promise<FidelityProbeRow[]> {
  const form = new FormData()
  form.append('text', text)
  const { data } = await api.post<{ rows: FidelityProbeRow[] }>('/api/fidelity/probe', form)
  return Array.isArray(data?.rows) ? data.rows : []
}

export async function fetchUsage(): Promise<UsagePayload | null> {
  try {
    const { data } = await api.get<UsagePayload>('/api/usage')
    return data
  } catch {
    return null
  }
}

export async function fetchTenants(): Promise<TenantPublic[]> {
  try {
    const { data } = await api.get<{ tenants?: TenantPublic[] }>('/api/tenants')
    return Array.isArray(data?.tenants) ? data.tenants : []
  } catch {
    return []
  }
}

export async function fetchLicense(): Promise<LicensePayload | null> {
  try {
    const { data } = await api.get<LicensePayload>('/api/license')
    return data
  } catch {
    return null
  }
}

export interface ToolCallResult {
  kind: 'json' | 'file'
  json?: unknown
  blob?: Blob
  filename?: string
}

/** Multipart call for the document-ops endpoints (PDF pages, redact, diff, sign, forms). */
export async function postToolForm(path: string, form: FormData): Promise<ToolCallResult> {
  try {
    const response = await api.post(path, form, { responseType: 'blob' })
    const ctype = String(response.headers['content-type'] || '')
    const blob = response.data as Blob
    if (ctype.includes('json') || ctype.startsWith('text/')) {
      const text = await blob.text()
      try {
        return { kind: 'json', json: JSON.parse(text) }
      } catch {
        return { kind: 'json', json: { text } }
      }
    }
    return {
      kind: 'file',
      blob,
      filename: parseFilename(response.headers['content-disposition'] as string | undefined, 'download.bin'),
    }
  } catch (err) {
    throw new Error(await extractErrorMessage(err))
  }
}

/** Prometheus text exposition (first N chars for UI preview). */
export async function fetchMetricsPreview(maxChars = 2400): Promise<string | null> {
  try {
    const { data } = await api.get<string>('/api/metrics', {
      responseType: 'text' as 'json',
      transformResponse: [(d) => d],
    })
    const text = typeof data === 'string' ? data : String(data ?? '')
    return text.slice(0, maxChars)
  } catch {
    return null
  }
}
