<script setup lang="ts">
import { ref } from 'vue'
import type { UploadProps } from 'ant-design-vue'

const props = defineProps<{
  disabled?: boolean
}>()

const emit = defineEmits<{
  filesSelected: [files: File[]]
}>()

const fileList = ref<UploadProps['fileList']>([])

const beforeUpload: UploadProps['beforeUpload'] = (file) => {
  emit('filesSelected', [file as unknown as File])
  return false
}

const handleDrop = (e: DragEvent) => {
  if (props.disabled) return
  const files = Array.from(e.dataTransfer?.files ?? [])
  if (files.length) {
    emit('filesSelected', files)
  }
}

const handleChange: UploadProps['onChange'] = (info) => {
  fileList.value = info.fileList
}
</script>

<template>
  <a-upload-dragger
    v-model:file-list="fileList"
    name="file"
    :multiple="true"
    :disabled="disabled"
    :before-upload="beforeUpload"
    :show-upload-list="true"
    @drop="handleDrop"
    @change="handleChange"
  >
    <p class="ant-upload-drag-icon">📄</p>
    <p class="ant-upload-text">点击或拖拽文件到此区域上传</p>
    <p class="ant-upload-hint">PDF / DOCX / MD / TXT / HTML / PPTX / EPUB / MOBI</p>
  </a-upload-dragger>
</template>

<style scoped>
.ant-upload-drag-icon {
  font-size: 48px;
  color: #1677ff;
}
</style>
