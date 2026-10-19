<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { loadEpubChapters, revokeEpubBlobUrls, type EpubChapter } from '../epubPreview'

const props = defineProps<{
  blob: Blob | null
  kind: 'pdf' | 'epub' | null
  title?: string
}>()

const objectUrl = ref('')
const epubChapters = ref<EpubChapter[]>([])
const epubIndex = ref(0)
const epubLoading = ref(false)
const epubError = ref('')

const currentEpubHtml = computed(() => epubChapters.value[epubIndex.value]?.html || '')
const currentEpubTitle = computed(() => epubChapters.value[epubIndex.value]?.title || '')

function revokePdf() {
  if (objectUrl.value) {
    URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
  }
}

function resetEpub() {
  revokeEpubBlobUrls(epubChapters.value)
  epubChapters.value = []
  epubIndex.value = 0
  epubError.value = ''
}

async function loadEpub(blob: Blob) {
  epubLoading.value = true
  epubError.value = ''
  resetEpub()
  try {
    epubChapters.value = await loadEpubChapters(blob)
  } catch {
    epubError.value = 'EPUB 预览解析失败，请下载后用阅读器打开。'
  } finally {
    epubLoading.value = false
  }
}

watch(
  () => [props.blob, props.kind] as const,
  ([blob, kind]) => {
    revokePdf()
    resetEpub()
    if (!blob || !kind) return
    if (kind === 'pdf') {
      objectUrl.value = URL.createObjectURL(blob)
      return
    }
    if (kind === 'epub') void loadEpub(blob)
  },
  { immediate: true },
)

onUnmounted(() => {
  revokePdf()
  resetEpub()
})

function prevChapter() {
  if (epubIndex.value > 0) epubIndex.value -= 1
}

function nextChapter() {
  if (epubIndex.value < epubChapters.value.length - 1) epubIndex.value += 1
}

function downloadEpub() {
  if (!props.blob) return
  const url = URL.createObjectURL(props.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = props.title || 'converted.epub'
  a.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
}
</script>

<template>
  <div class="md-doc-reader">
    <iframe
      v-if="kind === 'pdf' && objectUrl"
      class="md-doc-reader__frame"
      sandbox=""
      :src="objectUrl"
      :title="title || 'PDF preview'"
    />
    <div v-else-if="kind === 'epub' && blob" class="md-doc-reader__epub-wrap">
      <div v-if="epubLoading" class="md-doc-reader__status">正在加载 EPUB…</div>
      <div v-else-if="epubError" class="md-doc-reader__epub-fallback">
        <p>{{ epubError }}</p>
        <button type="button" class="md-btn md-btn--p" @click="downloadEpub">下载 EPUB</button>
      </div>
      <template v-else-if="epubChapters.length">
        <div class="md-doc-reader__epub-bar">
          <button type="button" class="md-btn md-btn--g" :disabled="epubIndex <= 0" @click="prevChapter">
            ‹
          </button>
          <span class="md-doc-reader__epub-meta">
            {{ currentEpubTitle }} · {{ epubIndex + 1 }}/{{ epubChapters.length }}
          </span>
          <button
            type="button"
            class="md-btn md-btn--g"
            :disabled="epubIndex >= epubChapters.length - 1"
            @click="nextChapter"
          >
            ›
          </button>
          <button type="button" class="md-btn md-btn--g" @click="downloadEpub">下载</button>
        </div>
        <iframe
          class="md-doc-reader__frame"
          sandbox=""
          :srcdoc="currentEpubHtml"
          :title="currentEpubTitle || title || 'EPUB preview'"
        />
      </template>
    </div>
    <p v-else class="md-doc-reader__fallback">暂无阅读器预览。</p>
  </div>
</template>

<style scoped>
.md-doc-reader {
  height: 100%;
  min-height: 280px;
  display: flex;
  flex-direction: column;
}
.md-doc-reader__frame {
  flex: 1;
  width: 100%;
  min-height: 240px;
  border: 0;
  background: #fff;
  border-radius: 6px;
}
.md-doc-reader__epub-wrap {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 280px;
}
.md-doc-reader__epub-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.md-doc-reader__epub-meta {
  flex: 1;
  font-size: 12px;
  opacity: 0.8;
  min-width: 120px;
}
.md-doc-reader__epub-fallback,
.md-doc-reader__status {
  margin: auto;
  text-align: center;
  max-width: 360px;
  font-size: 13px;
  line-height: 1.5;
  opacity: 0.85;
}
.md-doc-reader__fallback {
  margin: auto;
  opacity: 0.65;
  font-size: 13px;
}
</style>
