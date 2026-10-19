import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

describe('frontend smoke (T27)', () => {
  it('package.json has build script', () => {
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
    assert.equal(typeof pkg.scripts.build, 'string')
    assert.ok(pkg.dependencies.vue)
  })

  it('App.vue exists and exposes convert UI markers', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    assert.ok(vue.includes('AduPort') || vue.includes('convert') || vue.includes('Convert'))
  })

  it('ops tab wires usage/license/metrics APIs (expansion T37)', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    assert.ok(vue.includes("activeTab === 'ops'") || vue.includes('tabOps'))
    assert.ok(api.includes('/api/usage'))
    assert.ok(api.includes('/api/license'))
    assert.ok(api.includes('/api/metrics'))
  })

  it('api client exposes convert + jobs paths (E2E surface)', () => {
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    assert.ok(api.includes('/api/convert'))
    assert.ok(api.includes('/api/jobs'))
    assert.ok(api.includes('/api/jobs/') && api.includes('result'))
  })

  it('FE-01/02: API Key + Job Token wiring (Phase A)', () => {
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const auth = readFileSync(join(root, 'src/sessionAuth.ts'), 'utf8')
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const i18n = readFileSync(join(root, 'src/i18n.ts'), 'utf8')
    assert.ok(api.includes('getApiKey') && api.includes('setApiKey'))
    assert.ok(auth.includes('X-API-Key'))
    assert.ok(auth.includes('rememberJobToken') && auth.includes('getJobToken'))
    assert.ok(auth.includes('X-Job-Token'))
    // P2-1: job tokens + API key are memory-only (no durable sessionStorage write)
    assert.ok(auth.includes('_jobTokens') || auth.includes('Map<string'))
    assert.ok(auth.includes('LEGACY_JOB_TOKEN_STORAGE') && auth.includes('removeItem'))
    assert.ok(!auth.includes("sessionStorage.setItem(API_KEY_STORAGE"))
    assert.ok(!auth.includes("sessionStorage.setItem(JOB_TOKEN_STORAGE"))
    assert.ok(auth.includes('hasCookieSession') && auth.includes('clearDurableApiKey'))
    assert.ok(api.includes('createAuthSession') && api.includes('clearDurableApiKey'))
    assert.ok(api.includes('access_token'))
    assert.ok(vue.includes('saveApiKey') && vue.includes('apiKeyDraft'))
    assert.ok(i18n.includes('apiKeyLabel') && i18n.includes('apiKeySave'))
  })

  it('B5: azw3/kf8 labeled text-level', () => {
    const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
    assert.ok(types.includes('文本级') && types.includes('text-level'))
  })

  it('B6: ods/odp listed as target formats', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
    assert.ok(vue.includes("'ods'") && vue.includes("'odp'"))
    assert.ok(types.includes("| 'ods'") && types.includes("| 'odp'"))
  })

  it('Phase C: Playwright scaffold + API key testids', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
    const ops = readFileSync(join(root, 'src/components/OpsPanel.vue'), 'utf8')
    assert.ok(ops.includes('data-testid="api-key-input"'))
    assert.ok(vue.includes('data-testid="tab-ops"'))
    assert.ok(vue.includes('data-testid="btn-convert"'))
    assert.ok(vue.includes('test-id="source-format"') || vue.includes('test-id="target-format"'))
    assert.ok(pkg.scripts['test:e2e'])
    assert.ok(pkg.devDependencies['@playwright/test'])
  })

  it('breadth: OLE/ebook/PDF labeled text-level or OCR hint', () => {
    const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
    assert.ok(types.includes('文本级') || types.includes('text-level'))
    assert.ok(types.includes('TEXT_LEVEL_SOURCE_FORMATS'))
    assert.ok(types.includes("doc") && types.includes('文本级'))
    assert.ok(types.includes('扫描需 OCR') || types.includes('scan needs OCR'))
  })

  it('P1-4: sanitize.ts blocks script tags and javascript: href', () => {
    const sanitize = readFileSync(join(root, 'src/sanitize.ts'), 'utf8')
    assert.ok(sanitize.includes('DOMPurify'))
    assert.ok(sanitize.includes('ALLOWED_URI_REGEXP'))
    assert.ok(sanitize.includes('sanitizePreviewHtml'))
  })

  it('preview uses sandboxed iframe (no parent v-html)', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    assert.ok(vue.includes('previewSrcdoc') && vue.includes('sandbox=""'))
    assert.ok(!vue.includes('v-html="renderedPreview"'))
  })

  it('P3-8: FileUploader validates size before emit', () => {
    const uploader = readFileSync(join(root, 'src/components/FileUploader.vue'), 'utf8')
    assert.ok(uploader.includes('filterFiles'))
    assert.ok(uploader.includes('maxBytes'))
    assert.ok(uploader.includes("emit('reject'"))
  })

  it('breadth+depth: ODS/ODP shallow export tags + fidelity_tiers wiring', () => {
    const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const i18n = readFileSync(join(root, 'src/i18n.ts'), 'utf8')
    assert.ok(types.includes('EXPORT_SHALLOW_FORMATS'))
    assert.ok(types.includes('浅导出') || types.includes('shallow export'))
    assert.ok(vue.includes('exportShallowFormats') && vue.includes('exportShallowTag'))
    assert.ok(api.includes('fidelity_tiers') && api.includes('export_shallow'))
    assert.ok(api.includes('text_markers') && api.includes('claim_policy'))
    assert.ok(i18n.includes('exportShallowTag'))
    assert.ok(i18n.includes('textMarkersTag'))
    assert.ok(vue.includes('textMarkersTargetFormats') && vue.includes('textMarkersTag'))
  })

  it('HttpOnly session endpoint wired', () => {
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    assert.ok(api.includes('createAuthSession') && api.includes('/api/auth/session'))
  })

  it('export warnings parsed from response headers', () => {
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    assert.ok(api.includes('parseExportWarnings'))
    assert.ok(api.includes('exportWarnings'))
  })

  it('SSE job events + EPUB preview modules wired', () => {
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const events = readFileSync(join(root, 'src/jobEvents.ts'), 'utf8')
    const epub = readFileSync(join(root, 'src/epubPreview.ts'), 'utf8')
    const reader = readFileSync(join(root, 'src/components/DocumentReader.vue'), 'utf8')
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    assert.ok(api.includes('waitForJobViaSSE') || events.includes('waitForJobViaSSE'))
    assert.ok(events.includes('/api/jobs/') && events.includes('/events'))
    assert.ok(events.includes('subscribeJobEvents') && events.includes('text/event-stream'))
    assert.ok(epub.includes('loadEpubChapters') && epub.includes('jszip'))
    assert.ok(reader.includes('loadEpubChapters') && reader.includes('srcdoc'))
    assert.ok(vue.includes('subscribeJobEvents') && vue.includes('syncJobEventSubscriptions'))
  })

  it('fidelity diff panel + job resume wired', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const i18n = readFileSync(join(root, 'src/i18n.ts'), 'utf8')
    const diff = readFileSync(join(root, 'src/fidelityDiff.ts'), 'utf8')
    assert.ok(vue.includes('fidelityNotes') && vue.includes('tab-fidelity-diff'))
    assert.ok(vue.includes('fidelityText') && vue.includes('fidelity-text-compare'))
    assert.ok(diff.includes('compareTextFidelity') && diff.includes('retentionPct'))
    const board = readFileSync(join(root, 'src/components/TaskBoard.vue'), 'utf8')
    assert.ok(vue.includes('onResumeServerJob') && board.includes('btn-resume-job'))
    assert.ok(board.includes('data-testid="task-board"') && board.includes('job-stage'))
    assert.ok(vue.includes('fidelity-review') && vue.includes('RagBrowser'))
    const ops = readFileSync(join(root, 'src/components/OpsPanel.vue'), 'utf8')
    assert.ok(ops.includes('data-testid="tenant-admin"'))
    assert.ok(api.includes('/resume') && api.includes('resumeServerJob'))
    assert.ok(i18n.includes('fidelityDiff') && i18n.includes('resumeJob') && i18n.includes('fidelityRetention'))
  })

  it('RAG chunk options wired for target=rag', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
    const i18n = readFileSync(join(root, 'src/i18n.ts'), 'utf8')
    assert.ok(vue.includes('needsRagOpts') && vue.includes('rag-max-chars'))
    assert.ok(api.includes('rag_max_chars') && api.includes('rag_chunk_overlap'))
    assert.ok(types.includes('rag_max_chars'))
    assert.ok(i18n.includes('ragMaxChars') && i18n.includes('ragOptsHint'))
  })

  it('embed + force OCR closed-loop UI wired', () => {
    const vue = readFileSync(join(root, 'src/App.vue'), 'utf8')
    const api = readFileSync(join(root, 'src/api.ts'), 'utf8')
    const i18n = readFileSync(join(root, 'src/i18n.ts'), 'utf8')
    assert.ok(vue.includes('needsOcrOpts') && vue.includes('force-ocr'))
    assert.ok(vue.includes('rag-embed') && api.includes('force_ocr') && api.includes('embed'))
    assert.ok(i18n.includes('forceOcr') && i18n.includes('ragEmbed'))
  })
})
