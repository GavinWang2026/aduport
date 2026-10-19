<script setup lang="ts">
import { computed, ref } from 'vue'
import { postToolForm, postToolJson } from '../api'
import type { MessageKey } from '../i18n'

const props = defineProps<{
  tt: (key: MessageKey) => string
}>()

type ToolId =
  | 'merge'
  | 'split'
  | 'rotate'
  | 'extract'
  | 'redact'
  | 'diff'
  | 'sign'
  | 'verify'
  | 'formExtract'
  | 'formFill'
  | 'watch'
  | 'vector'
  | 'ragFeedback'

const TOOLS: { id: ToolId; label: MessageKey; path: string }[] = [
  { id: 'merge', label: 'toolPdfMerge', path: '/api/pdf/merge' },
  { id: 'split', label: 'toolPdfSplit', path: '/api/pdf/split' },
  { id: 'rotate', label: 'toolPdfRotate', path: '/api/pdf/rotate' },
  { id: 'extract', label: 'toolPdfExtract', path: '/api/pdf/extract' },
  { id: 'formExtract', label: 'toolFormExtract', path: '/api/pdf/form/extract' },
  { id: 'formFill', label: 'toolFormFill', path: '/api/pdf/form/fill' },
  { id: 'redact', label: 'toolRedact', path: '/api/redact' },
  { id: 'diff', label: 'toolDiff', path: '/api/diff' },
  { id: 'sign', label: 'toolSign', path: '/api/sign' },
  { id: 'verify', label: 'toolVerify', path: '/api/verify' },
  { id: 'watch', label: 'toolWatch', path: '/api/watch/scan' },
  { id: 'vector', label: 'toolVector', path: '/api/vectors/search' },
  { id: 'ragFeedback', label: 'toolRagFeedback', path: '/api/rag/layout_feedback' },
]

const tool = ref<ToolId>('merge')
const files = ref<File[]>([])
const rightFile = ref<File | null>(null)
const pages = ref('1')
const degrees = ref('90')
const watermark = ref('')
const pattern = ref('')
const signKey = ref('')
const mac = ref('')
const valuesJson = ref('{}')
const inputDir = ref('')
const outputDir = ref('')
const targetFormat = ref('md')
const sourceFormat = ref('')
const query = ref('')
const topK = ref('5')
const payloadJson = ref('{"elements":[]}')
const feedbackJson = ref('[]')
const busy = ref(false)
const errorText = ref('')
const resultText = ref('')

const current = computed(() => TOOLS.find((item) => item.id === tool.value) || TOOLS[0])
const needsPages = computed(() => tool.value === 'extract' || tool.value === 'rotate')
const needsRight = computed(() => tool.value === 'diff')
const multi = computed(() => tool.value === 'merge')
const fileless = computed(() => tool.value === 'watch' || tool.value === 'vector' || tool.value === 'ragFeedback')

function onFiles(event: Event) {
  const input = event.target as HTMLInputElement
  files.value = Array.from(input.files || [])
}

function onRight(event: Event) {
  const input = event.target as HTMLInputElement
  rightFile.value = input.files?.[0] || null
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

async function run() {
  errorText.value = ''
  resultText.value = ''
  if (tool.value === 'watch' || tool.value === 'vector' || tool.value === 'ragFeedback') {
    await runJson()
    return
  }
  if (!files.value.length) {
    errorText.value = props.tt('toolsNeedFile')
    return
  }
  if (tool.value === 'merge' && files.value.length < 2) {
    errorText.value = props.tt('toolsNeedTwo')
    return
  }
  if (needsRight.value && !rightFile.value) {
    errorText.value = props.tt('toolsNeedRight')
    return
  }
  const form = new FormData()
  if (tool.value === 'merge') {
    for (const file of files.value) form.append('files', file)
  } else if (tool.value === 'diff') {
    form.append('left', files.value[0])
    form.append('right', rightFile.value as File)
  } else {
    form.append('file', files.value[0])
  }
  if (needsPages.value && pages.value.trim()) form.append('pages', pages.value.trim())
  if (tool.value === 'rotate') form.append('degrees', degrees.value || '90')
  if (tool.value === 'redact') {
    if (watermark.value.trim()) form.append('watermark', watermark.value.trim())
    if (pattern.value.trim()) form.append('pattern', pattern.value.trim())
  }
  if (tool.value === 'sign' || tool.value === 'verify') form.append('key', signKey.value)
  if (tool.value === 'verify') form.append('mac', mac.value.trim())
  if (tool.value === 'formFill') form.append('values', valuesJson.value.trim() || '{}')

  busy.value = true
  try {
    const result = await postToolForm(current.value.path, form)
    if (result.kind === 'file' && result.blob) {
      const name = result.filename || 'download.bin'
      saveBlob(result.blob, name)
      resultText.value = name
      return
    }
    resultText.value = JSON.stringify(result.json ?? {}, null, 2)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    busy.value = false
  }
}

async function runJson() {
  if (tool.value === 'watch' && (!inputDir.value.trim() || !outputDir.value.trim() || !targetFormat.value.trim())) {
    errorText.value = props.tt('toolsNeedDirs')
    return
  }
  if (tool.value === 'vector' && !query.value.trim()) {
    errorText.value = props.tt('toolsNeedQuery')
    return
  }
  let body: unknown
  if (tool.value === 'watch') {
    const form = new FormData()
    form.append('input_dir', inputDir.value.trim())
    form.append('output_dir', outputDir.value.trim())
    form.append('target_format', targetFormat.value.trim())
    form.append('source_format', sourceFormat.value.trim())
    busy.value = true
    try {
      const result = await postToolForm('/api/watch/scan', form)
      resultText.value = JSON.stringify(result.json ?? {}, null, 2)
    } catch (err) {
      errorText.value = err instanceof Error ? err.message : '请求失败'
    } finally {
      busy.value = false
    }
    return
  }
  if (tool.value === 'vector') {
    const count = Number(topK.value || '5')
    body = { query: query.value.trim(), top_k: Number.isFinite(count) ? count : 5 }
  } else {
    try {
      body = {
        payload: JSON.parse(payloadJson.value || '{}'),
        feedback: JSON.parse(feedbackJson.value || '[]'),
      }
    } catch {
      errorText.value = props.tt('toolsNeedJson')
      return
    }
  }
  busy.value = true
  try {
    const result = await postToolJson(current.value.path, body)
    resultText.value = JSON.stringify(result ?? {}, null, 2)
  } catch (err) {
    errorText.value = err instanceof Error ? err.message : '请求失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="md-panel" data-testid="document-tools">
    <div class="md-p-head">
      <div class="md-p-title"><span class="k" />{{ tt('toolsTitle') }}</div>
    </div>
    <p class="md-p-sub" style="margin:0 0 14px">{{ tt('toolsHint') }}</p>
    <div class="md-adv" style="margin-bottom:14px">
      <div class="md-field" style="min-width:220px">
        <span>{{ tt('toolsOp') }}</span>
        <select v-model="tool" data-testid="tool-op">
          <option v-for="item in TOOLS" :key="item.id" :value="item.id">{{ tt(item.label) }}</option>
        </select>
      </div>
      <div v-if="!fileless" class="md-field" style="flex:1;min-width:220px">
        <span>{{ multi ? tt('toolsFiles') : (needsRight ? tt('toolsLeft') : tt('toolsFile')) }}</span>
        <input data-testid="tool-file" type="file" :multiple="multi" @change="onFiles">
      </div>
      <div v-if="needsRight" class="md-field" style="flex:1;min-width:220px">
        <span>{{ tt('toolsRight') }}</span>
        <input data-testid="tool-right" type="file" @change="onRight">
      </div>
      <div v-if="needsPages" class="md-field">
        <span>{{ tt('toolsPages') }}</span>
        <input v-model="pages" class="md-input" data-testid="tool-pages" placeholder="1,3-4">
      </div>
      <div v-if="tool === 'rotate'" class="md-field">
        <span>{{ tt('toolsDegrees') }}</span>
        <input v-model="degrees" class="md-input" data-testid="tool-degrees">
      </div>
      <div v-if="tool === 'redact'" class="md-field">
        <span>{{ tt('toolsWatermark') }}</span>
        <input v-model="watermark" class="md-input" data-testid="tool-watermark">
      </div>
      <div v-if="tool === 'redact'" class="md-field">
        <span>{{ tt('toolsPattern') }}</span>
        <input v-model="pattern" class="md-input" data-testid="tool-pattern" placeholder="email">
      </div>
      <div v-if="tool === 'sign' || tool === 'verify'" class="md-field">
        <span>{{ tt('toolsKey') }}</span>
        <input v-model="signKey" class="md-input" type="password" data-testid="tool-key" autocomplete="off">
      </div>
      <div v-if="tool === 'verify'" class="md-field">
        <span>{{ tt('toolsMac') }}</span>
        <input v-model="mac" class="md-input" data-testid="tool-mac">
      </div>
      <div v-if="tool === 'formFill'" class="md-field" style="flex:1;min-width:240px">
        <span>{{ tt('toolsValues') }}</span>
        <input v-model="valuesJson" class="md-input" data-testid="tool-values">
      </div>
      <div v-if="tool === 'watch'" class="md-field" style="min-width:200px">
        <span>{{ tt('toolsInputDir') }}</span>
        <input v-model="inputDir" class="md-input" data-testid="tool-input-dir">
      </div>
      <div v-if="tool === 'watch'" class="md-field" style="min-width:200px">
        <span>{{ tt('toolsOutputDir') }}</span>
        <input v-model="outputDir" class="md-input" data-testid="tool-output-dir">
      </div>
      <div v-if="tool === 'watch'" class="md-field">
        <span>{{ tt('toolsTarget') }}</span>
        <input v-model="targetFormat" class="md-input" data-testid="tool-target">
      </div>
      <div v-if="tool === 'watch'" class="md-field">
        <span>{{ tt('toolsSource') }}</span>
        <input v-model="sourceFormat" class="md-input" data-testid="tool-source">
      </div>
      <div v-if="tool === 'vector'" class="md-field" style="flex:1;min-width:220px">
        <span>{{ tt('toolsQuery') }}</span>
        <input v-model="query" class="md-input" data-testid="tool-query">
      </div>
      <div v-if="tool === 'vector'" class="md-field">
        <span>{{ tt('toolsTopK') }}</span>
        <input v-model="topK" class="md-input" data-testid="tool-topk">
      </div>
      <div v-if="tool === 'ragFeedback'" class="md-field" style="flex:1;min-width:240px">
        <span>{{ tt('toolsPayload') }}</span>
        <input v-model="payloadJson" class="md-input" data-testid="tool-payload">
      </div>
      <div v-if="tool === 'ragFeedback'" class="md-field" style="flex:1;min-width:240px">
        <span>{{ tt('toolsFeedback') }}</span>
        <input v-model="feedbackJson" class="md-input" data-testid="tool-feedback">
      </div>
      <button type="button" class="md-btn md-btn--p" data-testid="tool-run" style="align-self:flex-end" :disabled="busy" @click="run">
        {{ busy ? tt('toolsBusy') : tt('toolsRun') }}
      </button>
    </div>
    <p v-if="errorText" class="md-h-err" data-testid="tool-error">{{ errorText }}</p>
    <pre v-if="resultText" class="md-metrics-pre" data-testid="tool-result">{{ resultText }}</pre>
  </section>
</template>
