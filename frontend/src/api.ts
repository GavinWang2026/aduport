import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { ConvertOptions, ConvertResult, SourceFormatOption, TargetFormat } from './types'

const API_KEY_STORAGE = 'mdbridge-api-key'
const JOB_TOKEN_STORAGE = 'mdbridge-job-tokens'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 300000,
})

/** FE-01: resolve API key from localStorage, then VITE_API_KEY. */
export function getApiKey(): string {
  try {
    const fromStore = (localStorage.getItem(API_KEY_STORAGE) || '').trim()
    if (fromStore) return fromStore
  } catch {
    /* ignore */
  }
  const fromEnv = String(import.meta.env.VITE_API_KEY || '').trim()
  return fromEnv
}

export function setApiKey(key: string): void {
  const v = (key || '').trim()
  try {
    if (v) localStorage.setItem(API_KEY_STORAGE, v)
    else localStorage.removeItem(API_KEY_STORAGE)
  } catch {
    /* ignore */
  }
}

function readJobTokenMap(): Record<string, string> {
  try {
    const raw = sessionStorage.getItem(JOB_TOKEN_STORAGE)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, string>
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeJobTokenMap(map: Record<string, string>): void {
  try {
    sessionStorage.setItem(JOB_TOKEN_STORAGE, JSON.stringify(map))
  } catch {
    /* ignore */
  }
}

/** FE-02: persist per-job access_token for later status/result calls. */
export function rememberJobToken(jobId: string, token: string | undefined | null): void {
  const id = (jobId || '').trim()
  const tok = (token || '').trim()
  if (!id || !tok) return
  const map = readJobTokenMap()
  map[id] = tok
  // Cap map size to avoid unbounded sessionStorage growth
  const keys = Object.keys(map)
  if (keys.length > 200) {
    for (const k of keys.slice(0, keys.length - 200)) delete map[k]
  }
  writeJobTokenMap(map)
}

export function getJobToken(jobId: string): string {
  return readJobTokenMap()[(jobId || '').trim()] || ''
}

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const key = getApiKey()
  if (key) {
    config.headers = config.headers || {}
    config.headers['X-API-Key'] = key
  }
  return config
})

function jobAuthHeaders(jobId: string): Record<string, string> {
  const token = getJobToken(jobId)
  return token ? { 'X-Job-Token': token } : {}
}

function appendConvertOptions(form: FormData, opts?: ConvertOptions) {
  if (!opts) return
  const entries: [keyof ConvertOptions, string | boolean | undefined][] = [
    ['pdf_font_family', opts.pdf_font_family],
    ['pdf_custom_css', opts.pdf_custom_css],
    ['pdf_page_size', opts.pdf_page_size],
    ['pdf_orientation', opts.pdf_orientation],
    ['pdf_header', opts.pdf_header],
    ['pdf_footer', opts.pdf_footer],
    ['docx_theme', opts.docx_theme],
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

  const data = err.response.data
  if (data instanceof Blob) {
    try {
      const text = await data.text()
      const json = JSON.parse(text) as { message?: string; code?: string }
      if (json.message) {
        return json.code ? `[${json.code}] ${json.message}` : json.message
      }
      return text.slice(0, 200) || `请求失败 (${err.response.status})`
    } catch {
      return `请求失败 (${err.response.status})`
    }
  }

  if (typeof data === 'object' && data && 'message' in data) {
    const obj = data as { message?: string; code?: string }
    return obj.code ? `[${obj.code}] ${obj.message}` : String(obj.message)
  }

  return err.message || `请求失败 (${err.response.status})`
}

export interface JobStatusPayload {
  id: string
  status: 'queued' | 'running' | 'done' | 'error'
  progress: number
  error?: string | null
  result_filename?: string | null
  access_token?: string
}

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
        const response = await api.get(`/api/jobs/${jobId}/result`, {
          responseType: 'blob',
          headers: jobAuthHeaders(jobId),
        })
        const disposition = response.headers['content-disposition'] as string | undefined
        const filename = parseFilename(
          disposition,
          st.result_filename || `converted.${targetFormat}`,
        )
        const blob = response.data as Blob
        let preview: string | undefined
        if (targetFormat === 'md' || targetFormat === 'html') {
          preview = await blob.text()
        }
        return { filename, blob, preview }
      }
    }
    throw new Error('转换超时，请稍后在历史中重试或改用同步转换')
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

export interface FormatsPayload {
  formats: string[]
  source: string[]
  target: string[]
  export_only: string[]
  notes?: Record<string, string>
  fidelity_tiers?: {
    full?: string[]
    text_level?: string[]
    export_shallow?: string[]
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
  error?: string | null
  result_filename?: string | null
  created_at: number
  bytes_in?: number
  bytes_out?: number
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

export interface LicensePayload {
  required: boolean
  license: Record<string, unknown> | null
}

export async function fetchUsage(): Promise<UsagePayload | null> {
  try {
    const { data } = await api.get<UsagePayload>('/api/usage')
    return data
  } catch {
    return null
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
