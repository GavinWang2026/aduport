<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import FileUploader from './components/FileUploader.vue'
import {
  convertDocument,
  downloadServerJob,
  fetchHealth,
  listServerJobs,
  type ServerJob,
} from './api'
import type { ConvertJob, HistoryItem, SourceFormat, TargetFormat } from './types'
import { detectFormat, formatLabel } from './types'
import { t, type Locale as UILocale } from './i18n'

const sourceFormats: SourceFormat[] = ['docx', 'pdf', 'md', 'txt', 'html', 'pptx', 'epub', 'mobi']
const targetFormats: TargetFormat[] = [
  'docx',
  'pdf',
  'md',
  'html',
  'txt',
  'epub',
  'pptx',
  'mobi',
  'azw3',
  'kf8',
]

const locale = ref<UILocale>((localStorage.getItem('mdbridge-locale') as UILocale) || 'zh')
const sourceFormat = ref<SourceFormat>('docx')
const targetFormat = ref<TargetFormat>('md')
const jobs = ref<ConvertJob[]>([])
const converting = ref(false)
const apiOnline = ref(false)
const previewText = ref('')
const previewTitle = ref('')
const history = ref<HistoryItem[]>([])
const serverJobs = ref<ServerJob[]>([])
const dragIndex = ref<number | null>(null)

const HISTORY_KEY = 'mdbridge-history'

const tt = (key: Parameters<typeof t>[1]) => t(locale.value, key)

onMounted(async () => {
  try {
    history.value = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    history.value = []
  }
  apiOnline.value = await fetchHealth()
  if (!apiOnline.value) {
    message.warning(tt('apiWarn'))
  }
  await refreshServerJobs()
})

const doneCount = computed(() => jobs.value.filter((j) => j.status === 'done').length)

function setLocale(next: UILocale) {
  locale.value = next
  localStorage.setItem('mdbridge-locale', next)
}

function swapFormats() {
  const src = sourceFormat.value
  const tgt = targetFormat.value
  if ((sourceFormats as string[]).includes(tgt)) {
    sourceFormat.value = tgt as SourceFormat
  }
  if ((targetFormats as string[]).includes(src)) {
    targetFormat.value = src as TargetFormat
  }
}

function pushHistory(item: HistoryItem) {
  history.value = [item, ...history.value].slice(0, 30)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
}

function onFilesSelected(files: File[]) {
  for (const file of files) {
    const detected = detectFormat(file.name)
    const src = detected ?? sourceFormat.value
    if (detected) {
      sourceFormat.value = detected
    }
    jobs.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      file,
      sourceFormat: src,
      targetFormat: targetFormat.value,
      status: 'pending',
    })
  }
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
      job.progress = 0
      try {
        const result = await convertDocument(
          job.file,
          job.sourceFormat,
          job.targetFormat,
          (p) => {
            job.progress = p
          },
        )
        job.result = result
        job.status = 'done'
        if (result.preview) {
          previewText.value = result.preview
          previewTitle.value = result.filename
        }
        pushHistory({
          id: job.id,
          name: job.file.name,
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
          name: job.file.name,
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
  if (failed && !ok) {
    message.error(`${tt('allFailed')}（${failed}）`)
  } else if (failed) {
    message.warning(`${tt('partialDone')} ${ok} / ${failed}`)
  } else {
    message.success(`${tt('batchDone')}（${ok}）`)
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

function label(fmt: SourceFormat | TargetFormat) {
  return formatLabel(fmt, locale.value)
}
</script>

<template>
  <a-layout class="layout">
    <a-layout-header class="header">
      <div class="brand">
        <span class="logo">md-bridge</span>
        <span class="subtitle">{{ tt('subtitle') }}</span>
      </div>
      <a-space>
        <a-radio-group
          size="small"
          :value="locale"
          button-style="solid"
          @update:value="(v: string) => setLocale(v as UILocale)"
        >
          <a-radio-button value="zh">中文</a-radio-button>
          <a-radio-button value="en">EN</a-radio-button>
        </a-radio-group>
        <a-tag :color="apiOnline ? 'success' : 'error'">
          {{ apiOnline ? tt('apiOnline') : tt('apiOffline') }}
        </a-tag>
      </a-space>
    </a-layout-header>

    <a-layout-content class="content">
      <a-row :gutter="[24, 24]">
        <a-col :xs="24" :lg="14">
          <a-card :title="tt('uploadCard')" :bordered="false">
            <a-space direction="vertical" size="large" style="width: 100%">
              <a-row :gutter="16" align="middle">
                <a-col :span="10">
                  <label class="field-label">{{ tt('sourceFormat') }}</label>
                  <a-select v-model:value="sourceFormat" style="width: 100%">
                    <a-select-option v-for="f in sourceFormats" :key="f" :value="f">
                      {{ label(f) }}
                    </a-select-option>
                  </a-select>
                </a-col>
                <a-col :span="4" style="text-align: center; padding-top: 22px">
                  <a-button type="link" @click="swapFormats">{{ tt('swap') }}</a-button>
                </a-col>
                <a-col :span="10">
                  <label class="field-label">{{ tt('targetFormat') }}</label>
                  <a-select v-model:value="targetFormat" style="width: 100%">
                    <a-select-option v-for="f in targetFormats" :key="f" :value="f">
                      {{ label(f) }}
                    </a-select-option>
                  </a-select>
                </a-col>
              </a-row>

              <FileUploader :disabled="converting" @files-selected="onFilesSelected" />

              <a-space wrap>
                <a-button type="primary" :loading="converting" @click="convertAll">
                  {{ tt('start') }}
                </a-button>
                <a-button :disabled="converting || !doneCount" @click="downloadAll">
                  {{ tt('downloadAll') }} ({{ doneCount }})
                </a-button>
                <a-button :disabled="converting || !jobs.length" @click="clearJobs">
                  {{ tt('clearList') }}
                </a-button>
              </a-space>
            </a-space>
          </a-card>

          <a-card :title="tt('queue')" :bordered="false" style="margin-top: 24px">
            <template #extra>
              <span class="preview-name">{{ tt('dragHint') }}</span>
            </template>
            <a-empty v-if="!jobs.length" :description="tt('queueEmpty')" />
            <a-list v-else :data-source="jobs" item-layout="horizontal">
              <template #renderItem="{ item, index }">
                <a-list-item
                  class="queue-item"
                  draggable="true"
                  @dragstart="onDragStart(index)"
                  @dragover="onDragOver"
                  @drop="onDrop(index)"
                >
                  <template #actions>
                    <a-tag v-if="item.status === 'pending'" color="default">{{ tt('waiting') }}</a-tag>
                    <a-tag v-else-if="item.status === 'converting'" color="processing">
                      {{ tt('converting') }}{{ item.progress != null ? ` ${Math.round(item.progress * 100)}%` : '' }}
                    </a-tag>
                    <a-tag v-else-if="item.status === 'done'" color="success">{{ tt('done') }}</a-tag>
                    <a-tag v-else color="error">{{ item.error || tt('failed') }}</a-tag>
                    <a-button
                      v-if="item.status === 'done'"
                      type="link"
                      size="small"
                      @click="downloadJob(item)"
                    >
                      {{ tt('download') }}
                    </a-button>
                    <a-button type="link" size="small" danger @click="removeJob(item.id)">
                      {{ tt('remove') }}
                    </a-button>
                  </template>
                  <a-list-item-meta
                    :title="item.file.name"
                    :description="`${label(item.sourceFormat)} → ${label(item.targetFormat)}`"
                  >
                    <template #avatar>
                      <span class="drag-handle" title="drag">⠿</span>
                    </template>
                  </a-list-item-meta>
                </a-list-item>
              </template>
            </a-list>
          </a-card>
        </a-col>

        <a-col :xs="24" :lg="10">
          <a-card :title="tt('preview')" :bordered="false">
            <template #extra>
              <span v-if="previewTitle" class="preview-name">{{ previewTitle }}</span>
            </template>
            <a-empty v-if="!previewText" :description="tt('previewEmpty')" />
            <pre v-else class="preview-box">{{ previewText }}</pre>
          </a-card>

          <a-card :title="tt('serverJobs')" :bordered="false" style="margin-top: 24px">
            <template #extra>
              <a-button type="link" size="small" @click="refreshServerJobs">{{ tt('refreshJobs') }}</a-button>
            </template>
            <a-empty v-if="!serverJobs.length" :description="tt('serverJobsEmpty')" />
            <a-list v-else size="small" :data-source="serverJobs">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-list-item-meta
                    :title="item.filename"
                    :description="`${item.source_format} → ${item.target_format} · ${item.status}`"
                  />
                  <template #actions>
                    <a-button
                      v-if="item.status === 'done'"
                      type="link"
                      size="small"
                      @click="onDownloadServerJob(item)"
                    >
                      {{ tt('download') }}
                    </a-button>
                    <a-tag v-else :color="item.status === 'error' ? 'error' : 'processing'">
                      {{ item.status }}
                    </a-tag>
                  </template>
                </a-list-item>
              </template>
            </a-list>
          </a-card>

          <a-card :title="tt('history')" :bordered="false" style="margin-top: 24px">
            <template #extra>
              <a-button type="link" size="small" :disabled="!history.length" @click="clearHistory">
                {{ tt('clear') }}
              </a-button>
            </template>
            <a-empty v-if="!history.length" description="—" />
            <a-list v-else size="small" :data-source="history" :split="true">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-list-item-meta
                    :title="item.name"
                    :description="`${item.sourceFormat} → ${item.targetFormat} · ${item.at}`"
                  />
                  <template #actions>
                    <a-tag :color="item.ok ? 'success' : 'error'">
                      {{ item.ok ? tt('success') : tt('failed') }}
                    </a-tag>
                  </template>
                </a-list-item>
              </template>
            </a-list>
          </a-card>
        </a-col>
      </a-row>
    </a-layout-content>

    <a-layout-footer class="footer">
      {{ tt('footer') }}
    </a-layout-footer>
  </a-layout>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  background: #f0f2f5;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #001529;
  padding: 0 32px;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.logo {
  color: #fff;
  font-size: 20px;
  font-weight: 600;
}

.subtitle {
  color: rgba(255, 255, 255, 0.65);
  font-size: 14px;
}

.content {
  padding: 24px 32px;
  max-width: 1400px;
  margin: 0 auto;
  width: 100%;
}

.field-label {
  display: block;
  margin-bottom: 6px;
  color: rgba(0, 0, 0, 0.65);
  font-size: 13px;
}

.preview-box {
  max-height: 360px;
  overflow: auto;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
  padding: 16px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
  margin: 0;
}

.preview-name {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}

.footer {
  text-align: center;
  color: rgba(0, 0, 0, 0.45);
}

.drag-handle {
  cursor: grab;
  color: #94a3b8;
  font-size: 18px;
  user-select: none;
  line-height: 1;
}

.queue-item {
  cursor: grab;
}

.queue-item:active {
  cursor: grabbing;
}
</style>
