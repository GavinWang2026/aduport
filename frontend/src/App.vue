<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import FormatSelect from './components/FormatSelect.vue'
import FileUploader from './components/FileUploader.vue'
import { message } from './toast'
import {
  convertBatchZip,
  convertDocument,
  downloadServerJob,
  fetchFormats,
  fetchHealth,
  fetchLicense,
  fetchMetricsPreview,
  fetchUsage,
  getApiKey,
  listServerJobs,
  setApiKey,
  type LicensePayload,
  type ServerJob,
  type UsagePayload,
} from './api'
import type { ConvertJob, ConvertOptions, HistoryItem, SourceFormat, SourceFormatOption, TargetFormat } from './types'
import { DEFAULT_CONVERT_OPTIONS, EXPORT_SHALLOW_FORMATS, SOURCE_FORMAT_ORDER, detectFormat, formatLabel } from './types'
import { t, type Locale as UILocale } from './i18n'

const sourceFormats: SourceFormat[] = [...SOURCE_FORMAT_ORDER]
const targetFormats: TargetFormat[] = [
  'docx', 'pdf', 'md', 'html', 'txt', 'epub', 'pptx', 'mobi',
  'azw3', 'kf8', 'latex', 'csv', 'odt', 'ods', 'odp', 'json', 'rag', 'xlsx', 'rtf',
]

const locale = ref<UILocale>((localStorage.getItem('mdbridge-locale') as UILocale) || 'zh')
const theme = ref<'dark' | 'light'>((localStorage.getItem('mdbridge-theme') as 'dark' | 'light') || 'dark')
const activeTab = ref<'convert' | 'tasks' | 'ops'>('convert')
const sourceFormat = ref<SourceFormatOption>('auto')
const targetFormat = ref<TargetFormat>('md')
const showAdvanced = ref(false)
const convertOpts = ref<ConvertOptions>({ ...DEFAULT_CONVERT_OPTIONS })
const jobs = ref<ConvertJob[]>([])
const converting = ref(false)
const zipping = ref(false)
const apiOnline = ref(false)
const previewText = ref('')
const previewKind = ref<'md' | 'html'>('md')
const previewTitle = ref('')
const previewMode = ref<'md' | 'render'>('md')
const previewFullscreen = ref(false)
const history = ref<HistoryItem[]>([])
const serverJobs = ref<ServerJob[]>([])
const usageInfo = ref<UsagePayload | null>(null)
const licenseInfo = ref<LicensePayload | null>(null)
const metricsPreview = ref('')
const opsLoading = ref(false)
const apiKeyDraft = ref(getApiKey())
const dragIndex = ref<number | null>(null)
const autoRefresh = ref(true)
const jobFilter = ref<'all' | 'active' | 'done' | 'error'>('all')
const uploader = ref<{ openDirectory: () => void } | null>(null)
/** Target formats that cannot be used as source (from GET /api/formats). */
const exportOnlyFormats = ref<Set<string>>(new Set())
/** Shallow export targets (ODS/ODP/KF) — honesty tags from API or local fallback. */
const exportShallowFormats = ref<Set<string>>(new Set(EXPORT_SHALLOW_FORMATS))
let pollTimer: ReturnType<typeof setInterval> | null = null

const HISTORY_KEY = 'mdbridge-history'
const tt = (key: Parameters<typeof t>[1]) => t(locale.value, key)

const needsPdfOpts = computed(() => targetFormat.value === 'pdf')
const needsDocxOpts = computed(() => targetFormat.value === 'docx')
const needsAdvanced = computed(() => needsPdfOpts.value || needsDocxOpts.value)

function activeConvertOptions(): ConvertOptions | undefined {
  if (!needsAdvanced.value) return undefined
  const o = convertOpts.value
  const out: ConvertOptions = {}
  if (needsPdfOpts.value) {
    if (o.pdf_font_family) out.pdf_font_family = o.pdf_font_family
    if (o.pdf_custom_css) out.pdf_custom_css = o.pdf_custom_css
    if (o.pdf_page_size) out.pdf_page_size = o.pdf_page_size
    if (o.pdf_orientation) out.pdf_orientation = o.pdf_orientation
    if (o.pdf_header) out.pdf_header = o.pdf_header
    if (o.pdf_footer) out.pdf_footer = o.pdf_footer
    out.pdf_page_numbers = !!o.pdf_page_numbers
    out.pdf_toc = !!o.pdf_toc
  }
  if (needsDocxOpts.value && o.docx_theme) out.docx_theme = o.docx_theme
  return out
}

function applyTheme(next: 'dark' | 'light') {
  theme.value = next
  document.documentElement.setAttribute('data-theme', next)
  localStorage.setItem('mdbridge-theme', next)
}

onMounted(async () => {
  applyTheme(theme.value)
  try {
    history.value = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    history.value = []
  }
  apiOnline.value = await fetchHealth()
  if (!apiOnline.value) message.warning(tt('apiWarn'))
  const fmts = await fetchFormats()
  if (fmts?.export_only) {
    exportOnlyFormats.value = new Set(
      fmts.export_only.map((x) => x.toLowerCase()),
    )
  }
  const shallow = fmts?.fidelity_tiers?.export_shallow
  if (shallow?.length) {
    exportShallowFormats.value = new Set(shallow.map((x) => x.toLowerCase()))
  }
  await refreshServerJobs()
  startPolling()
  // keep online badge fresh (PDF/docling can make API look offline)
  window.setInterval(() => {
    void fetchHealth().then((ok) => {
      apiOnline.value = ok
    })
  }, 5000)
})

onUnmounted(() => stopPolling())

watch([activeTab, autoRefresh], () => {
  stopPolling()
  startPolling()
})

watch(activeTab, (tab) => {
  if (tab === 'ops') void refreshOps()
})

async function refreshOps() {
  opsLoading.value = true
  try {
    const [u, l, m] = await Promise.all([fetchUsage(), fetchLicense(), fetchMetricsPreview()])
    usageInfo.value = u
    licenseInfo.value = l
    metricsPreview.value = m || ''
  } finally {
    opsLoading.value = false
  }
}

function saveApiKey() {
  setApiKey(apiKeyDraft.value)
  apiKeyDraft.value = getApiKey()
  message.success(tt('apiKeySaved'))
  void checkApi()
  if (activeTab.value === 'ops') void refreshOps()
}

async function checkApi() {
  apiOnline.value = await fetchHealth()
}

function startPolling() {
  if (!autoRefresh.value || activeTab.value !== 'tasks') return
  pollTimer = setInterval(() => {
    void refreshServerJobs()
  }, 2500)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const doneCount = computed(() => jobs.value.filter((j) => j.status === 'done').length)
const queueCount = computed(() =>
  jobs.value.filter((j) => j.status === 'pending' || j.status === 'converting').length,
)
const historyOkCount = computed(() => history.value.filter((h) => h.ok).length)
const successRate = computed(() => {
  if (!history.value.length) return '—'
  return `${((historyOkCount.value / history.value.length) * 100).toFixed(1)}%`
})

const filteredServerJobs = computed(() => {
  const list = serverJobs.value
  if (jobFilter.value === 'all') return list
  if (jobFilter.value === 'active') {
    return list.filter((j) => j.status === 'queued' || j.status === 'running')
  }
  if (jobFilter.value === 'done') return list.filter((j) => j.status === 'done')
  return list.filter((j) => j.status === 'error')
})

const sourceOptions = computed(() => [
  { value: 'auto', label: formatLabel('auto', locale.value) },
  ...sourceFormats.map((f) => ({ value: f, label: formatLabel(f, locale.value) })),
])

const targetOptions = computed(() =>
  targetFormats.map((f) => {
    const only = exportOnlyFormats.value.has(f)
    const shallow = exportShallowFormats.value.has(f)
    let label = formatLabel(f, locale.value)
    if (only) label = `${label} · ${tt('exportOnlyTag')}`
    else if (f === 'csv') label = `${label} · ${tt('csvTableOnlyShort')}`
    else if (shallow) label = `${label} · ${tt('exportShallowTag')}`
    return {
      value: f,
      label,
      hint: only
        ? tt('exportOnlyHint')
        : f === 'csv'
          ? tt('csvTableOnly')
          : shallow
            ? tt('exportShallowHint')
            : undefined,
    }
  }),
)

function extractPreviewBody(html: string): string {
  const m = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)
  return m ? m[1] : html
}

const renderedPreview = computed(() => {
  if (!previewText.value) return ''
  if (previewKind.value === 'html') {
    return extractPreviewBody(previewText.value)
  }
  return simpleMarkdown(previewText.value)
})

function setLocale(next: UILocale) {
  locale.value = next
  localStorage.setItem('mdbridge-locale', next)
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

function swapFormats() {
  const src = sourceFormat.value
  const tgt = targetFormat.value
  if (src !== 'auto' && (targetFormats as string[]).includes(src)) {
    targetFormat.value = src as TargetFormat
  }
  // Do not promote export-only targets (azw3/kf8/latex) into source.
  if ((sourceFormats as string[]).includes(tgt) && !exportOnlyFormats.value.has(tgt)) {
    sourceFormat.value = tgt as SourceFormat
  }
}

function pushHistory(item: HistoryItem) {
  history.value = [item, ...history.value].slice(0, 30)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
}

function enqueueFiles(files: File[], fromDir = false) {
  for (const file of files) {
    const detected = detectFormat(file.name)
    const src: SourceFormatOption =
      sourceFormat.value === 'auto' ? 'auto' : (detected ?? sourceFormat.value)
    if (sourceFormat.value !== 'auto' && detected) {
      sourceFormat.value = detected
    }
    const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || undefined
    jobs.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file,
      sourceFormat: src,
      targetFormat: targetFormat.value,
      status: 'pending',
      relativePath: rel || undefined,
    })
  }
  if (fromDir && files.length) {
    message.success(`${tt('dirAdded')}（${files.length}）`)
  }
}

function onFilesSelected(files: File[]) {
  enqueueFiles(files, false)
}

function onDirectorySelected(files: File[]) {
  enqueueFiles(files, true)
}

function onDragStart(index: number) {
  dragIndex.value = index
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
}

function onDrop(index: number) {
  if (dragIndex.value === null || dragIndex.value === index || converting.value) return
  const list = [...jobs.value]
  const [item] = list.splice(dragIndex.value, 1)
  list.splice(index, 0, item)
  jobs.value = list
  dragIndex.value = null
}

async function convertAll() {
  if (!jobs.value.length) {
    message.info(tt('needFiles'))
    return
  }
  converting.value = true
  const pending = jobs.value.filter((j) => j.status !== 'done')
  const concurrency = 3
  let cursor = 0

  async function worker() {
    while (cursor < pending.length) {
      const idx = cursor++
      const job = pending[idx]
      job.status = 'converting'
      job.targetFormat = targetFormat.value
      if (sourceFormat.value === 'auto') job.sourceFormat = 'auto'
      job.progress = 0
      try {
        const result = await convertDocument(
          job.file,
          job.sourceFormat,
          job.targetFormat,
          (p, status) => {
            // server often stays at 0.1 during long PDF/docling work
            job.progress = Math.max(p, status === 'running' ? 0.15 : p)
          },
          activeConvertOptions(),
        )
        job.result = result
        job.status = 'done'
        if (result.preview) {
          previewText.value = result.preview
          previewTitle.value = result.filename
          previewKind.value = job.targetFormat === 'html' ? 'html' : 'md'
          if (job.targetFormat === 'html') previewMode.value = 'render'
        }
        pushHistory({
          id: job.id,
          name: job.relativePath || job.file.name,
          sourceFormat: job.sourceFormat,
          targetFormat: job.targetFormat,
          at: new Date().toLocaleString(),
          ok: true,
        })
      } catch (err: unknown) {
        job.status = 'error'
        job.error = err instanceof Error ? err.message : tt('failed')
        pushHistory({
          id: job.id,
          name: job.relativePath || job.file.name,
          sourceFormat: job.sourceFormat,
          targetFormat: job.targetFormat,
          at: new Date().toLocaleString(),
          ok: false,
          error: job.error,
        })
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, pending.length) }, () => worker()))
  converting.value = false
  await refreshServerJobs()

  const failed = jobs.value.filter((j) => j.status === 'error').length
  const ok = jobs.value.filter((j) => j.status === 'done').length
  if (failed && !ok) message.error(`${tt('allFailed')}（${failed}）`)
  else if (failed) message.warning(`${tt('partialDone')} ${ok} / ${failed}`)
  else message.success(`${tt('batchDone')}（${ok}）`)
}

async function downloadZipBatch() {
  const pending = jobs.value.filter((j) => j.status === 'pending' || j.status === 'error')
  const files = (pending.length ? pending : jobs.value).map((j) => j.file)
  if (!files.length) {
    message.info(tt('needFiles'))
    return
  }
  zipping.value = true
  try {
    const { filename, blob } = await convertBatchZip(
      files,
      sourceFormat.value,
      targetFormat.value,
      activeConvertOptions(),
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
    message.success(tt('zipDone'))
    await refreshServerJobs()
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : tt('failed'))
  } finally {
    zipping.value = false
  }
}

function downloadJob(job: ConvertJob) {
  if (!job.result) return
  const url = URL.createObjectURL(job.result.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = job.result.filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

async function downloadAll() {
  const done = jobs.value.filter((j) => j.status === 'done' && j.result)
  if (!done.length) {
    message.info(tt('noDownload'))
    return
  }
  for (const job of done) {
    downloadJob(job)
    await new Promise((r) => setTimeout(r, 250))
  }
}

function clearJobs() {
  jobs.value = []
  previewText.value = ''
  previewTitle.value = ''
}

function clearHistory() {
  history.value = []
  localStorage.removeItem(HISTORY_KEY)
}

function removeJob(id: string) {
  jobs.value = jobs.value.filter((j) => j.id !== id)
}

async function refreshServerJobs() {
  try {
    serverJobs.value = await listServerJobs()
  } catch {
    serverJobs.value = []
  }
}

async function onDownloadServerJob(job: ServerJob) {
  try {
    await downloadServerJob(job.id, job.result_filename || `converted.${job.target_format}`)
  } catch (err: unknown) {
    message.error(err instanceof Error ? err.message : tt('failed'))
  }
}

function formatTime(ts: number) {
  try {
    return new Date(ts * 1000).toLocaleString()
  } catch {
    return String(ts)
  }
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function extOf(name: string) {
  return (name.split('.').pop() || '').toLowerCase()
}

function extClass(name: string) {
  const e = extOf(name)
  if (['pdf', 'docx', 'doc', 'md', 'html', 'xlsx', 'csv'].includes(e)) return `is-${e}`
  return ''
}

function statusClass(status: ConvertJob['status']) {
  if (status === 'pending') return 'wait'
  if (status === 'converting') return 'run'
  if (status === 'done') return 'ready'
  return 'bad'
}

function statusText(job: ConvertJob) {
  if (job.status === 'pending') return tt('waiting')
  if (job.status === 'converting') {
    const pct = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : ''
    const bigPdf =
      /\.pdf$/i.test(job.file?.name || '') && (job.file?.size || 0) > 1_500_000
    const hint = bigPdf && (job.progress ?? 0) < 0.95 ? ` · ${tt('ocrSlowHint')}` : ''
    return `${tt('converting')}${pct}${hint}`
  }
  if (job.status === 'done') return tt('done')
  return job.error || tt('failed')
}

function histBadgeClass(name: string) {
  const e = extOf(name)
  if (e === 'pdf') return 'md-hb-pdf'
  if (e === 'docx' || e === 'doc') return 'md-hb-doc'
  if (e === 'md' || e === 'txt') return 'md-hb-md'
  if (e === 'html') return 'md-hb-html'
  return 'md-hb-def'
}

function histBadgeText(name: string) {
  const e = extOf(name)
  if (!e) return 'FILE'
  return e.slice(0, 4).toUpperCase()
}

async function copyPreview() {
  if (!previewText.value) return
  try {
    await navigator.clipboard.writeText(previewText.value)
    message.success(tt('copied'))
  } catch {
    message.error(tt('failed'))
  }
}

function downloadPreview() {
  if (!previewText.value) return
  const blob = new Blob([previewText.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = previewTitle.value || 'preview.md'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

function simpleMarkdown(src: string): string {
  const esc = src
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return esc
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>')
    .replace(/^/, '<p>')
    .replace(/$/, '</p>')
}
</script>

<template>
  <div class="md-app">
    <div class="md-stage" aria-hidden="true" />

    <header class="md-header">
      <div class="md-brand">
        <div class="md-logo" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 5h9a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H6V5Z" opacity=".9" />
            <path d="M6 5h9a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H6" opacity=".35" />
            <path d="M9.5 9.5v5M12 9.5l2.5 2.5-2.5 2.5" />
          </svg>
        </div>
        <div style="display:flex;align-items:baseline;gap:2px">
          <span class="md-brand-name">md<em>-bridge</em></span>
          <span class="md-brand-tag">{{ tt('localTag') }}</span>
        </div>
      </div>

      <div class="md-hdr-right">
        <div class="md-seg" role="group">
          <button type="button" :class="{ active: locale === 'zh' }" @click="setLocale('zh')">中文</button>
          <button type="button" :class="{ active: locale === 'en' }" @click="setLocale('en')">EN</button>
        </div>
        <div class="md-online" :class="{ 'is-on': apiOnline }">
          <span class="md-dot" />
          {{ apiOnline ? tt('apiOnline') : tt('apiOffline') }}
          <span class="md-tip">{{ tt('apiTip') }}</span>
        </div>
        <button
          type="button"
          class="md-icon-rd"
          :title="theme === 'dark' ? tt('themeLight') : tt('themeDark')"
          @click="toggleTheme"
        >
          <svg v-if="theme === 'dark'" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
          </svg>
        </button>
      </div>
    </header>

    <div class="md-pagehead">
      <div>
        <h1>{{ tt('pageTitle') }}<span class="hl">.</span></h1>
        <div class="sub">{{ tt('pageSub') }}</div>
      </div>
      <div class="md-stat-row">
        <div class="md-stat"><b class="blue">{{ historyOkCount }}</b><span>{{ tt('statConverted') }}</span></div>
        <div class="md-stat"><b class="green">{{ successRate }}</b><span>{{ tt('statRate') }}</span></div>
        <div class="md-stat"><b class="gray">{{ queueCount }}</b><span>{{ tt('statQueue') }}</span></div>
      </div>
    </div>

    <nav class="md-tabs" aria-label="tabs">
      <button type="button" data-testid="tab-convert" :class="{ on: activeTab === 'convert' }" @click="activeTab = 'convert'">{{ tt('tabConvert') }}</button>
      <button type="button" data-testid="tab-tasks" :class="{ on: activeTab === 'tasks' }" @click="activeTab = 'tasks'">{{ tt('tabTasks') }}</button>
      <button type="button" data-testid="tab-ops" :class="{ on: activeTab === 'ops' }" @click="activeTab = 'ops'">{{ tt('tabOps') }}</button>
    </nav>

    <main class="md-main">
      <template v-if="activeTab === 'convert'">
        <div class="md-grid">
          <!-- left: upload -->
          <section class="md-panel">
            <div class="md-p-head">
              <div class="md-p-title"><span class="k" />{{ tt('uploadCard') }}</div>
              <span class="md-p-sub">{{ tt('dragDrop') }}</span>
            </div>
            <div class="md-workspace">
              <div class="md-fmt-grid">
                <FormatSelect
                  v-model="sourceFormat"
                  :label="tt('sourceFormat')"
                  :options="sourceOptions"
                  test-id="source-format"
                />
                <button type="button" class="md-swap" :title="tt('swap')" @click="swapFormats">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M7 4v14m0 0-3.5-3.5M7 18l3.5-3.5" />
                    <path d="M17 20V6m0 0-3.5 3.5M17 6l3.5 3.5" />
                  </svg>
                </button>
                <FormatSelect
                  v-model="targetFormat"
                  :label="tt('targetFormat')"
                  :options="targetOptions"
                  test-id="target-format"
                />
              </div>

              <div v-if="needsAdvanced" class="md-adv">
                <button type="button" class="md-adv__toggle" @click="showAdvanced = !showAdvanced">
                  {{ showAdvanced ? tt('advHide') : tt('advShow') }}
                </button>
                <div v-if="showAdvanced" class="md-adv__grid">
                  <template v-if="needsPdfOpts">
                    <label class="md-field">
                      <span>{{ tt('pdfPageSize') }}</span>
                      <select v-model="convertOpts.pdf_page_size">
                        <option value="A4">A4</option>
                        <option value="Letter">Letter</option>
                        <option value="A3">A3</option>
                        <option value="Legal">Legal</option>
                      </select>
                    </label>
                    <label class="md-field">
                      <span>{{ tt('pdfOrientation') }}</span>
                      <select v-model="convertOpts.pdf_orientation">
                        <option value="portrait">{{ tt('portrait') }}</option>
                        <option value="landscape">{{ tt('landscape') }}</option>
                      </select>
                    </label>
                    <label class="md-field">
                      <span>{{ tt('pdfFont') }}</span>
                      <input v-model="convertOpts.pdf_font_family" type="text" :placeholder="tt('pdfFontPh')" />
                    </label>
                    <label class="md-field md-field--wide">
                      <span>{{ tt('pdfHeader') }}</span>
                      <input v-model="convertOpts.pdf_header" type="text" />
                    </label>
                    <label class="md-field md-field--wide">
                      <span>{{ tt('pdfFooter') }}</span>
                      <input v-model="convertOpts.pdf_footer" type="text" />
                    </label>
                    <label class="md-field md-field--wide">
                      <span>{{ tt('pdfCss') }}</span>
                      <textarea v-model="convertOpts.pdf_custom_css" rows="2" :placeholder="tt('pdfCssPh')" />
                    </label>
                    <label class="md-check">
                      <input v-model="convertOpts.pdf_page_numbers" type="checkbox" />
                      {{ tt('pdfPageNumbers') }}
                    </label>
                    <label class="md-check">
                      <input v-model="convertOpts.pdf_toc" type="checkbox" />
                      {{ tt('pdfToc') }}
                    </label>
                  </template>
                  <label v-if="needsDocxOpts" class="md-field">
                    <span>{{ tt('docxTheme') }}</span>
                    <select v-model="convertOpts.docx_theme">
                      <option value="default">default</option>
                      <option value="academic">academic</option>
                      <option value="minimal">minimal</option>
                    </select>
                  </label>
                </div>
              </div>

              <FileUploader
                ref="uploader"
                :disabled="converting || zipping"
                :text="tt('uploadText')"
                :hint="tt('uploadHint')"
                @files-selected="onFilesSelected"
                @directory-selected="onDirectorySelected"
              />

              <div v-if="jobs.length" class="md-files">
                <div v-for="item in jobs" :key="item.id" class="md-frow">
                  <span class="md-ext" :class="extClass(item.file.name)">{{ extOf(item.file.name).slice(0, 4) || 'FILE' }}</span>
                  <span class="md-fname" :title="item.relativePath || item.file.name">{{ item.relativePath || item.file.name }}</span>
                  <span class="md-fsize">{{ formatBytes(item.file.size) }}</span>
                  <span class="md-st" :class="statusClass(item.status)">
                    <span v-if="item.status === 'converting'" class="dot2" />
                    {{ statusText(item) }}
                  </span>
                  <div v-if="item.status === 'converting'" class="md-bar" :class="{ 'is-indet': !item.progress }">
                    <i :style="item.progress ? { width: `${Math.round(item.progress * 100)}%` } : undefined" />
                  </div>
                  <button v-if="item.status === 'done'" type="button" class="md-link" data-testid="btn-download-job" @click="downloadJob(item)">{{ tt('download') }}</button>
                  <button type="button" class="md-x" :title="tt('remove')" @click="removeJob(item.id)">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
                  </button>
                </div>
              </div>

              <div class="md-ops">
                <button type="button" class="md-btn md-btn--g" :disabled="converting || zipping" @click="uploader?.openDirectory()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16M8 16V8m8 8V4M12 12V6" /></svg>
                  {{ tt('pickDir') }}
                </button>
                <button
                  type="button"
                  class="md-btn md-btn--p"
                  data-testid="btn-convert"
                  :disabled="converting"
                  @click="convertAll"
                >                  <span v-if="converting" class="md-spin" />
                  <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" /></svg>
                  {{ tt('start') }}
                </button>
                <button type="button" class="md-btn md-btn--g" :disabled="!jobs.length || zipping" @click="downloadZipBatch">
                  <span v-if="zipping" class="md-spin" />
                  <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8v13H3V8" /><path d="M1 3h22v5H1z" /><path d="M10 12h4" /></svg>
                  {{ tt('downloadZip') }}
                </button>
                <button type="button" class="md-btn md-btn--g" data-testid="btn-download-all" :disabled="converting || !doneCount" @click="downloadAll">
                  {{ tt('downloadAll') }} ({{ doneCount }})
                </button>
                <button type="button" class="md-btn md-btn--g" :disabled="converting || !jobs.length" @click="clearJobs">
                  {{ tt('clear') }}
                </button>
              </div>
            </div>
          </section>

          <!-- right: preview -->
          <section
            class="md-panel md-preview"
            :class="{ 'is-fullscreen': previewFullscreen }"
            :style="previewFullscreen ? { position: 'fixed', inset: '16px', zIndex: 80, maxHeight: 'none' } : undefined"
          >
            <div class="md-p-head">
              <div class="md-p-title"><span class="k" />{{ tt('preview') }}</div>
              <div style="display:flex;gap:3px;align-items:center">
                <span v-if="previewTitle" class="md-p-sub" style="margin-right:6px">{{ previewTitle }}</span>
                <button type="button" class="md-icon-rd" :title="tt('copy')" :disabled="!previewText" @click="copyPreview">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
                </button>
                <button type="button" class="md-icon-rd" :title="tt('download')" :disabled="!previewText" @click="downloadPreview">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v3a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-3" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></svg>
                </button>
                <button type="button" class="md-icon-rd" :title="tt('fullscreen')" @click="previewFullscreen = !previewFullscreen">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3" /></svg>
                </button>
              </div>
            </div>
            <div class="md-pv-tabs">
              <button type="button" :class="{ on: previewMode === 'md' }" @click="previewMode = 'md'">{{ tt('previewMd') }}</button>
              <button type="button" :class="{ on: previewMode === 'render' }" @click="previewMode = 'render'">{{ tt('previewRender') }}</button>
            </div>
            <div class="md-pv-body">
              <div v-if="!previewText" class="md-pv-empty">
                <div class="frame">
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 3h16v18H4z" /><path d="M8 9h3M8 13h3M13 9h3M8 17h8" /></svg>
                </div>
                <p>{{ tt('previewEmpty') }}</p>
              </div>
              <pre v-else-if="previewMode === 'md'" class="md-pv-doc">{{ previewText }}</pre>
              <div v-else class="md-pv-render" v-html="renderedPreview" />
            </div>
          </section>
        </div>

        <!-- queue ‖ history -->
        <div class="md-bottom-row">
          <section class="md-panel md-queue">
            <div class="md-q-head">
              <div class="md-q-title"><span class="k" />{{ tt('queue') }}</div>
              <span class="md-q-hint">{{ tt('dragHint') }}</span>
            </div>
            <div class="md-q-inner">
              <div v-if="!jobs.length" class="md-q-empty">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h6" /></svg>
                {{ tt('queueEmpty') }}
              </div>
              <div
                v-for="(item, index) in jobs"
                :key="`q-${item.id}`"
                class="md-qitem"
                draggable="true"
                @dragstart="onDragStart(index)"
                @dragover="onDragOver"
                @drop="onDrop(index)"
              >
                <span class="grip" aria-hidden="true">
                  <svg width="7" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="12" cy="19" r="2" /></svg>
                </span>
                <span class="idx">{{ String(index + 1).padStart(2, '0') }}</span>
                <span class="s" :class="`is-${item.status}`" />
                <span class="n" :title="item.relativePath || item.file.name">{{ item.relativePath || item.file.name }}</span>
                <button v-if="item.status === 'done'" type="button" class="md-link" data-testid="btn-download-job" @click="downloadJob(item)">{{ tt('download') }}</button>
              </div>
            </div>
          </section>

          <section class="md-panel md-history">
            <div class="md-p-head">
              <div class="md-p-title"><span class="k" />{{ tt('history') }}</div>
              <button type="button" class="md-btn md-btn--g" style="padding:5px 12px;font-size:11px" :disabled="!history.length" @click="clearHistory">
                {{ tt('clear') }}
              </button>
            </div>
            <div v-if="!history.length" class="md-empty-pad">—</div>
            <div v-else class="md-hlist">
              <div v-for="item in history" :key="item.id" class="md-hitem" :class="item.ok ? 'ok' : 'bad'" :title="item.error || item.name">
                <div class="md-h-badge" :class="histBadgeClass(item.name)">{{ histBadgeText(item.name) }}</div>
                <div class="md-h-body">
                  <div class="md-h-name" :title="item.name">{{ item.name }}</div>
                  <div class="md-h-meta">
                    <span class="a">{{ item.sourceFormat }} → {{ item.targetFormat }}</span>
                    <span>· {{ item.at }}</span>
                  </div>
                  <div v-if="!item.ok && item.error" class="md-h-err" :title="item.error">{{ item.error }}</div>
                </div>
                <span class="md-st" :class="item.ok ? 'ok' : 'bad'">{{ item.ok ? tt('success') : tt('failed') }}</span>
              </div>
            </div>
          </section>
        </div>
      </template>

      <section v-else-if="activeTab === 'ops'" class="md-panel">
        <div class="md-p-head">
          <div class="md-p-title"><span class="k" />{{ tt('opsTitle') }}</div>
          <button type="button" class="md-btn md-btn--g" :disabled="opsLoading" @click="refreshOps">
            {{ opsLoading ? tt('opsLoading') : tt('refreshJobs') }}
          </button>
        </div>
        <p class="md-p-sub" style="margin:0 0 14px">{{ tt('opsHint') }}</p>
        <div class="md-adv" style="margin-bottom:14px">
          <div class="md-field" style="flex:1;min-width:240px">
            <span>{{ tt('apiKeyLabel') }}</span>
            <input
              v-model="apiKeyDraft"
              type="password"
              class="md-input"
              data-testid="api-key-input"
              autocomplete="off"
              :placeholder="tt('apiKeyPh')"
              @keyup.enter="saveApiKey"
            >
          </div>
          <button type="button" class="md-btn md-btn--p" data-testid="api-key-save" style="align-self:flex-end" @click="saveApiKey">
            {{ tt('apiKeySave') }}
          </button>
        </div>
        <p class="md-p-sub" style="margin:0 0 14px;font-size:12px;opacity:.8">{{ tt('apiKeyHint') }}</p>
        <div class="md-stat-row" style="margin-bottom:16px">
          <div class="md-stat">
            <b class="blue">{{ usageInfo?.conversions_today ?? '—' }}</b>
            <span>{{ tt('opsToday') }}</span>
          </div>
          <div class="md-stat">
            <b class="green">{{ usageInfo?.conversions_total ?? '—' }}</b>
            <span>{{ tt('opsTotal') }}</span>
          </div>
          <div class="md-stat">
            <b class="gray">{{ usageInfo?.conversions_failed ?? '—' }}</b>
            <span>{{ tt('opsFailed') }}</span>
          </div>
        </div>
        <div class="md-adv" style="margin-bottom:14px">
          <div class="md-field">
            <span>{{ tt('opsTenant') }}</span>
            <code class="mono">{{ usageInfo?.tenant_id || '—' }}</code>
          </div>
          <div class="md-field">
            <span>{{ tt('opsLicense') }}</span>
            <code class="mono">
              {{
                licenseInfo
                  ? (licenseInfo.required ? tt('opsLicenseRequired') : tt('opsLicenseOpen'))
                    + (licenseInfo.license ? ` · ${JSON.stringify(licenseInfo.license).slice(0, 120)}` : '')
                  : '—'
              }}
            </code>
          </div>
        </div>
        <div class="md-p-title" style="margin-bottom:8px">{{ tt('opsMetrics') }}</div>
        <pre class="md-metrics-pre">{{ metricsPreview || tt('opsMetricsEmpty') }}</pre>
      </section>

      <section v-else class="md-panel">
        <div class="md-p-head">
          <div class="md-p-title"><span class="k" />{{ tt('serverJobs') }}</div>
        </div>
        <div class="md-tasks-toolbar">
          <label class="md-check">
            <input v-model="autoRefresh" type="checkbox" />
            {{ tt('autoRefresh') }}
          </label>
          <div class="md-seg">
            <button type="button" :class="{ active: jobFilter === 'all' }" @click="jobFilter = 'all'">{{ tt('filterAll') }}</button>
            <button type="button" :class="{ active: jobFilter === 'active' }" @click="jobFilter = 'active'">{{ tt('filterActive') }}</button>
            <button type="button" :class="{ active: jobFilter === 'done' }" @click="jobFilter = 'done'">{{ tt('filterDone') }}</button>
            <button type="button" :class="{ active: jobFilter === 'error' }" @click="jobFilter = 'error'">{{ tt('filterError') }}</button>
          </div>
          <button type="button" class="md-btn md-btn--g" @click="refreshServerJobs">{{ tt('refreshJobs') }}</button>
        </div>
        <div v-if="!filteredServerJobs.length" class="md-empty-pad">{{ tt('serverJobsEmpty') }}</div>
        <div v-else class="md-table-wrap">
          <table class="md-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>File</th>
                <th>Route</th>
                <th>Status</th>
                <th>{{ tt('progress') }}</th>
                <th>{{ tt('createdAt') }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in filteredServerJobs" :key="row.id">
                <td class="mono">{{ row.id.slice(0, 8) }}</td>
                <td class="mono">{{ row.filename }}</td>
                <td>{{ row.source_format }} → {{ row.target_format }}</td>
                <td>
                  <span
                    class="md-st"
                    :class="{
                      wait: row.status === 'queued',
                      run: row.status === 'running',
                      ready: row.status === 'done',
                      bad: row.status === 'error',
                    }"
                  >{{ row.status }}</span>
                </td>
                <td>
                  <div class="md-bar" style="flex:none;width:90px">
                    <i :style="{ width: `${Math.round((row.progress || 0) * 100)}%` }" />
                  </div>
                </td>
                <td>{{ formatTime(row.created_at) }}</td>
                <td>
                  <button v-if="row.status === 'done'" type="button" class="md-link" @click="onDownloadServerJob(row)">
                    {{ tt('download') }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <footer class="md-foot">
      <div class="md-foot-l"><b>md-bridge</b> · MIT License · {{ tt('footerPrivacy') }}</div>
      <div class="md-trust">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2l8 3v6c0 5-3.5 8.5-8 11-4.5-2.5-8-6-8-11V5l8-3Z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
        {{ tt('footerPrivacy') }}
      </div>
    </footer>
  </div>
</template>
