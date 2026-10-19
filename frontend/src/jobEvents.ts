import type { JobStatusPayload } from './types'
import { apiKeyHeaders, jobAuthHeaders } from './sessionAuth'

export type JobEventHandlers = {
  onProgress?: (payload: JobStatusPayload) => void
  onError?: (err: Error) => void
}

export type JobEventSubscription = {
  close: () => void
}

function apiBase(): string {
  return String(import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')
}

function jobEventHeaders(jobId: string): Record<string, string> {
  return {
    Accept: 'text/event-stream',
    ...apiKeyHeaders(),
    ...jobAuthHeaders(jobId),
  }
}

function parseSseBlock(block: string, handlers: JobEventHandlers): void {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return
  const raw = dataLines.join('\n')
  try {
    const payload = JSON.parse(raw) as JobStatusPayload & { code?: string }
    if (event === 'error') {
      handlers.onError?.(new Error(payload.code || 'job_event_error'))
      return
    }
    handlers.onProgress?.(payload)
  } catch {
    handlers.onError?.(new Error('invalid_sse_payload'))
  }
}

/** Fetch-based SSE client (supports X-API-Key / X-Job-Token). */
export function subscribeJobEvents(
  jobId: string,
  handlers: JobEventHandlers,
): JobEventSubscription {
  const ac = new AbortController()
  const url = `${apiBase()}/api/jobs/${encodeURIComponent(jobId)}/events`

  void (async () => {
    try {
      const res = await fetch(url, {
        method: 'GET',
        headers: jobEventHeaders(jobId),
        signal: ac.signal,
        credentials: 'include',
      })
      if (!res.ok || !res.body) {
        handlers.onError?.(new Error(`SSE ${res.status}`))
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let sawTerminal = false
      const wrapped: JobEventHandlers = {
        onProgress: (payload) => {
          if (payload.status === 'done' || payload.status === 'error') {
            sawTerminal = true
          }
          handlers.onProgress?.(payload)
        },
        onError: handlers.onError,
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep = buffer.indexOf('\n\n')
        while (sep >= 0) {
          const block = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          if (block.trim()) parseSseBlock(block, wrapped)
          sep = buffer.indexOf('\n\n')
        }
      }
      if (buffer.trim()) parseSseBlock(buffer, wrapped)
      // Only treat as error when stream ended without a terminal job status (P3-6).
      if (!sawTerminal) {
        handlers.onError?.(new Error('sse_stream_ended'))
      }
    } catch (err) {
      if (ac.signal.aborted) return
      handlers.onError?.(err instanceof Error ? err : new Error(String(err)))
    }
  })()

  return { close: () => ac.abort() }
}

/** Wait until job reaches done/error via SSE (rejects on stream error). */
export function waitForJobViaSSE(
  jobId: string,
  onProgress?: (progress: number, status: string) => void,
): Promise<JobStatusPayload> {
  return new Promise((resolve, reject) => {
    const sub = subscribeJobEvents(jobId, {
      onProgress: (st) => {
        onProgress?.(st.progress ?? 0, st.status)
        if (st.status === 'done') {
          sub.close()
          resolve(st)
        } else if (st.status === 'error') {
          sub.close()
          reject(new Error(st.error || '转换失败'))
        }
      },
      onError: (err) => {
        sub.close()
        reject(err)
      },
    })
  })
}
