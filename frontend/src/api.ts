import axios, { AxiosError } from 'axios'
import type { ConvertResult, SourceFormat, TargetFormat } from './types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 300000,
})

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
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

/** Async job path: enqueue → poll → download result. */
export async function convertDocumentAsync(
  file: File,
  sourceFormat: SourceFormat,
  targetFormat: TargetFormat,
  onProgress?: (p: number, status: string) => void,
): Promise<ConvertResult> {
  const form = new FormData()
  form.append('file', file)
  form.append('source_format', sourceFormat)
  form.append('target_format', targetFormat)

  try {
    const { data: created } = await api.post<JobStatusPayload>('/api/jobs', form)
    const jobId = created.id
    onProgress?.(created.progress ?? 0, created.status)

    for (let i = 0; i < 600; i++) {
      await sleep(i < 10 ? 200 : 500)
      const { data: st } = await api.get<JobStatusPayload>(`/api/jobs/${jobId}`)
      onProgress?.(st.progress ?? 0, st.status)
      if (st.status === 'error') {
        throw new Error(st.error || '转换失败')
      }
      if (st.status === 'done') {
        const response = await api.get(`/api/jobs/${jobId}/result`, { responseType: 'blob' })
        const disposition = response.headers['content-disposition'] as string | undefined
        const filename = parseFilename(
          disposition,
          st.result_filename || `converted.${targetFormat}`,
        )
        const blob = response.data as Blob
        let preview: string | undefined
        if (targetFormat === 'md') {
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

/** Prefer async jobs for better progress; falls back message on hard failures. */
export async function convertDocument(
  file: File,
  sourceFormat: SourceFormat,
  targetFormat: TargetFormat,
  onProgress?: (p: number, status: string) => void,
): Promise<ConvertResult> {
  // Files under 2MB still use async queue (uniform UX / memory budget)
  return convertDocumentAsync(file, sourceFormat, targetFormat, onProgress)
}

export async function fetchHealth(): Promise<boolean> {
  try {
    const { data } = await api.get('/api/health')
    return data?.status === 'ok'
  } catch {
    return false
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
}

export async function listServerJobs(): Promise<ServerJob[]> {
  const { data } = await api.get<{ jobs: ServerJob[] }>('/api/jobs')
  return data.jobs || []
}

export async function downloadServerJob(jobId: string, fallbackName: string): Promise<void> {
  const response = await api.get(`/api/jobs/${jobId}/result`, { responseType: 'blob' })
  const disposition = response.headers['content-disposition'] as string | undefined
  const filename = parseFilename(disposition, fallbackName)
  const url = URL.createObjectURL(response.data as Blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
