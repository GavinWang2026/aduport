<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  disabled?: boolean
  text?: string
  hint?: string
}>()

const emit = defineEmits<{
  filesSelected: [files: File[]]
  directorySelected: [files: File[]]
}>()

const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)

function openFiles() {
  fileInput.value?.click()
}

function openDirectory() {
  dirInput.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) emit('filesSelected', files)
  input.value = ''
}

function onDirectoryChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) emit('directorySelected', files)
  input.value = ''
}

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  dragging.value = true
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  dragging.value = true
}

function onDragLeave(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragging.value = false
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length) emit('filesSelected', files)
}

defineExpose({ openDirectory })
</script>

<template>
  <div class="md-uploader">
    <div
      class="md-drop"
      :class="{ 'is-dragging': dragging, 'is-disabled': disabled }"
      role="button"
      tabindex="0"
      @click="openFiles"
      @keydown.enter.prevent="openFiles"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <div class="md-drop__icon" aria-hidden="true">
        <span class="ring" />
        <svg viewBox="0 0 24 24">
          <path d="M12 16V4m0 0 4 4m-4-4-4 4" />
          <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
        </svg>
      </div>
      <p class="md-drop__title" v-html="text" />
      <p class="md-drop__hint">{{ hint }}</p>
      <div class="md-chips">
        <span class="md-chip">PDF</span>
        <span class="md-chip">DOCX</span>
        <span class="md-chip">MD</span>
        <span class="md-chip">HTML</span>
        <span class="md-chip">ODT</span>
        <span class="md-chip">RTF</span>
        <span class="md-chip">XLSX</span>
        <span class="md-chip">CSV</span>
      </div>
    </div>
    <input ref="fileInput" class="md-hidden" type="file" multiple data-testid="file-input" @change="onFileChange" />
    <input
      ref="dirInput"
      class="md-hidden"
      type="file"
      webkitdirectory
      directory
      multiple
      @change="onDirectoryChange"
    />
  </div>
</template>
