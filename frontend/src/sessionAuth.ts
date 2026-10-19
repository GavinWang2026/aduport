const API_KEY_STORAGE = 'aduport-api-key'
const COOKIE_SESSION_FLAG = 'aduport-cookie-session'

/** Default job-token TTL (ms). Short-lived; not written to sessionStorage (P2-1). */
const JOB_TOKEN_TTL_MS = 2 * 60 * 60 * 1000
const JOB_TOKEN_MAX = 200

/** Legacy keys — cleared on load so XSS cannot recover prior credentials from storage. */
const LEGACY_JOB_TOKEN_STORAGE = 'aduport-job-tokens'

/** In-memory API key for the session-create handshake only (never durable). */
let _ephemeralApiKey = ''

type JobTokenEntry = { token: string; exp: number }

/** Tab-lifetime job tokens only (memory). */
const _jobTokens = new Map<string, JobTokenEntry>()

try {
  sessionStorage.removeItem(LEGACY_JOB_TOKEN_STORAGE)
  // One-shot: drain any leftover plaintext key into ephemeral, then wipe storage.
  const leftover =
    (sessionStorage.getItem(API_KEY_STORAGE) || '').trim() ||
    (localStorage.getItem(API_KEY_STORAGE) || '').trim()
  if (leftover && !_ephemeralApiKey) _ephemeralApiKey = leftover
  sessionStorage.removeItem(API_KEY_STORAGE)
  localStorage.removeItem(API_KEY_STORAGE)
} catch {
  /* ignore */
}

/** True after a successful HttpOnly cookie session bind. */
export function hasCookieSession(): boolean {
  try {
    return sessionStorage.getItem(COOKIE_SESSION_FLAG) === '1'
  } catch {
    return false
  }
}

export function markCookieSession(active: boolean): void {
  try {
    if (active) sessionStorage.setItem(COOKIE_SESSION_FLAG, '1')
    else sessionStorage.removeItem(COOKIE_SESSION_FLAG)
  } catch {
    /* ignore */
  }
}

/**
 * Resolve API key for request headers.
 * Prefer empty when cookie session is active (HttpOnly cookie carries auth).
 * Key material is memory-only — never re-read from sessionStorage after bind.
 */
export function getApiKey(): string {
  if (hasCookieSession() && !_ephemeralApiKey) {
    return ''
  }
  return _ephemeralApiKey
}

/** Store key in memory only for handshake; never write durable storage (v13 §1 P2-1). */
export function setApiKey(key: string): void {
  const v = (key || '').trim()
  _ephemeralApiKey = v
  try {
    sessionStorage.removeItem(API_KEY_STORAGE)
    localStorage.removeItem(API_KEY_STORAGE)
    if (!v) markCookieSession(false)
  } catch {
    /* ignore */
  }
}

/** Drop all key material after HttpOnly cookie session is established. */
export function clearDurableApiKey(): void {
  _ephemeralApiKey = ''
  try {
    sessionStorage.removeItem(API_KEY_STORAGE)
    localStorage.removeItem(API_KEY_STORAGE)
  } catch {
    /* ignore */
  }
}

function pruneJobTokens(now = Date.now()): void {
  for (const [id, entry] of _jobTokens) {
    if (entry.exp <= now) _jobTokens.delete(id)
  }
  if (_jobTokens.size <= JOB_TOKEN_MAX) return
  const ordered = [..._jobTokens.entries()].sort((a, b) => a[1].exp - b[1].exp)
  for (const [id] of ordered.slice(0, _jobTokens.size - JOB_TOKEN_MAX)) {
    _jobTokens.delete(id)
  }
}

/**
 * Remember a job access token in memory only (short TTL).
 * Not persisted to sessionStorage — multi-tenant XSS cannot exfiltrate via storage APIs.
 */
export function rememberJobToken(jobId: string, token: string | undefined | null): void {
  const id = (jobId || '').trim()
  const tok = (token || '').trim()
  if (!id || !tok) return
  pruneJobTokens()
  _jobTokens.set(id, { token: tok, exp: Date.now() + JOB_TOKEN_TTL_MS })
}

export function getJobToken(jobId: string): string {
  const id = (jobId || '').trim()
  if (!id) return ''
  pruneJobTokens()
  const entry = _jobTokens.get(id)
  if (!entry) return ''
  if (entry.exp <= Date.now()) {
    _jobTokens.delete(id)
    return ''
  }
  return entry.token
}

export function jobAuthHeaders(jobId: string): Record<string, string> {
  const token = getJobToken(jobId)
  return token ? { 'X-Job-Token': token } : {}
}

export function apiKeyHeaders(): Record<string, string> {
  // Cookie session: never re-attach plaintext key (report P2-1 / v13 §1).
  if (hasCookieSession()) return {}
  const key = getApiKey()
  return key ? { 'X-API-Key': key } : {}
}
